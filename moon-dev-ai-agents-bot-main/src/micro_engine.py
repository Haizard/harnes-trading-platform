"""
Moon Dev Micro-Cap Trading Engine (Paper + Live + AI Orchestrator)
DSH Pattern: Scanner -> Safety -> AI Decision -> Trade -> Learn
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from src.token_scanner import TokenScanner, TokenCandidate
from src.micro_sniper import MicroSniper, TradeSignal
from src.paper_trader import PaperTrader
from src.rug_pull_detector import RugPullDetector
from src.event_bus import EventBus, Events, DispatchMode
from src.agent_orchestrator import AgentOrchestrator

DEFAULT_CAPITAL = 25.0
SCAN_INTERVAL = 30
EXIT_CHECK_INTERVAL = 10
MIN_SCORE = 40


class MicroEngine:
    """DSH-compliant micro-cap trading engine with AI orchestrator."""

    def __init__(self, capital=DEFAULT_CAPITAL, mode="paper", rpc_url=None):
        self.capital = capital
        self.mode = mode
        self.scanner = TokenScanner(callback=self._on_candidate)
        self.sniper = MicroSniper(capital=capital, mode=mode, rpc_url=rpc_url)
        self.mode = self.sniper.mode  # Sync mode (may fallback from live to paper)
        self.paper = PaperTrader(capital=capital)
        self.rug_detector = RugPullDetector()
        self.orchestrator = AgentOrchestrator(capital=capital, mode=self.mode)
        self.event_bus = self.orchestrator.event_bus
        self._running = False
        self._scan_count = 0
        self._signals_generated = 0
        self._trades_executed = 0
        self._safe_trades = 0
        self._rug_blocked = 0
        self._ai_skipped = 0
        self.data_dir = Path("src/data/micro_engine")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._events = []

    def _on_candidate(self, candidate):
        self._signals_generated += 1
        self._events.append({
            "type": "token/candidate",
            "data": candidate.to_dict(),
            "timestamp": datetime.utcnow().isoformat(),
        })
        if candidate.score < MIN_SCORE:
            return

        # Step 1: Rug-pull safety check
        print("[RUG] Checking safety for " + candidate.symbol + "...")
        report = self.rug_detector.check(candidate.address)
        if not report.is_safe:
            self._rug_blocked += 1
            print("[RUG] BLOCKED " + candidate.symbol + " - Risk: " + str(int(report.risk_score)) + "/100")
            for reason in report.reasons:
                print("[RUG]   " + reason)
            self._events.append({
                "type": "rug/blocked",
                "data": {"token": candidate.address, "symbol": candidate.symbol,
                          "risk_score": report.risk_score, "reasons": report.reasons},
                "timestamp": datetime.utcnow().isoformat(),
            })
            return

        print("[RUG] PASSED " + candidate.symbol + " - Risk: " + str(int(report.risk_score)) + "/100")

        # Step 2: AI Orchestrator decision (consensus + feedback loop)
        decision = self.orchestrator.analyze_candidate(candidate.to_dict())
        ai_source = decision.get("source", "algorithmic")
        ai_action = decision.get("action", "SKIP")
        ai_confidence = decision.get("confidence", 0)

        print("[AI] " + candidate.symbol + " -> " + ai_action + " (" + ai_source + " conf=" + str(round(ai_confidence, 2)) + ")")

        if ai_action != "BUY":
            self._ai_skipped += 1
            print("[AI] SKIP " + candidate.symbol + ": " + decision.get("reason", ""))
            return

        # Step 3: Evaluate position sizing
        signal = self.sniper.evaluate_signal(
            candidate.address, candidate.symbol,
            candidate.score, candidate.liquidity_usd,
        )
        if not signal:
            return

        # Step 4: Execute trade
        if self.mode == "live":
            self._execute_live_trade(signal, candidate, report)
        else:
            self._execute_paper_trade(signal, candidate, report)

    def _execute_live_trade(self, signal, candidate, safety_report):
        self._safe_trades += 1
        print("")
        print("=" * 60)
        print("LIVE TRADE: " + signal.symbol)
        print("   Score: " + str(int(signal.score)) + "/100")
        print("   Amount: $" + "{:.2f}".format(signal.amount_usd))
        print("   Safety: Risk " + str(int(safety_report.risk_score)) + "/100")
        print("   Mode: LIVE")
        print("=" * 60)
        self._events.append({
            "type": "live/intent",
            "data": {"token": signal.token_address, "symbol": signal.symbol,
                      "side": signal.side, "amount_usd": signal.amount_usd,
                      "score": signal.score},
            "timestamp": datetime.utcnow().isoformat(),
        })
        pos = self.sniper.execute_buy(signal)
        if pos:
            self._trades_executed += 1
            self._events.append({
                "type": "live/executed",
                "data": pos.to_dict(),
                "timestamp": datetime.utcnow().isoformat(),
            })

    def _execute_paper_trade(self, signal, candidate, safety_report):
        self._safe_trades += 1
        print("")
        print("=" * 60)
        print("PAPER TRADE: " + signal.symbol)
        print("   Score: " + str(int(signal.score)) + "/100")
        print("   Amount: $" + "{:.2f}".format(signal.amount_usd))
        print("   Safety: Risk " + str(int(safety_report.risk_score)) + "/100")
        print("=" * 60)
        self._events.append({
            "type": "paper/intent",
            "data": {"token": signal.token_address, "symbol": signal.symbol,
                      "side": signal.side, "amount_usd": signal.amount_usd,
                      "score": signal.score},
            "timestamp": datetime.utcnow().isoformat(),
        })
        trade = self.paper.buy(
            token_address=signal.token_address, symbol=signal.symbol,
            amount_usd=signal.amount_usd, score=signal.score,
            signals=candidate.signals,
        )
        if trade:
            self._trades_executed += 1
            self._events.append({
                "type": "paper/executed",
                "data": trade.to_dict(),
                "timestamp": datetime.utcnow().isoformat(),
            })

    def _check_exits(self):
        if self.mode == "live":
            closed = self.sniper.check_exits()
            for pos in closed:
                self.orchestrator.record_trade_outcome(pos.symbol, pos.pnl_usd, pos.pnl_pct, 0)
                self._events.append({"type": "live/exit", "data": pos.to_dict(),
                    "timestamp": datetime.utcnow().isoformat()})
        else:
            closed = self.paper.check_exits()
            for trade in closed:
                self.orchestrator.record_trade_outcome(trade.symbol, trade.pnl_usd, trade.pnl_pct, 0)
                self._events.append({"type": "paper/exit", "data": trade.to_dict(),
                    "timestamp": datetime.utcnow().isoformat()})

    def _log_events(self):
        log_path = self.data_dir / "engine_events.jsonl"
        with open(log_path, "a") as f:
            for event in self._events:
                f.write(json.dumps(event, default=str) + chr(10))
        self._events = []

    async def run(self):
        self._running = True
        mode_label = "LIVE" if self.mode == "live" else "PAPER"
        print("")
        print("=" * 60)
        print("MICRO ENGINE (" + mode_label + " + AI ORCHESTRATOR)")
        print("   Capital: $" + "{:.2f}".format(self.capital))
        print("   Mode: " + mode_label)
        print("   Rug-pull protection: ENABLED")
        print("   AI Orchestrator: " + ("ENABLED" if self.orchestrator.get_stats().get("bedrock_configured") else "FALLBACK (algo only)"))
        print("   Scan interval: " + str(SCAN_INTERVAL) + "s")
        print("=" * 60)
        print("")
        last_exit_check = time.time()

        while self._running:
            try:
                candidates = self.scanner.scan_once()
                self._scan_count += 1
                if candidates:
                    print("[ENGINE] Scan #" + str(self._scan_count) + ": Found " + str(len(candidates)) + " candidates")
                if time.time() - last_exit_check >= EXIT_CHECK_INTERVAL:
              
                    self._check_exits()
                    last_exit_check = time.time()
                if self._events:
                    self._log_events()
                if self._scan_count % 5 == 0:
                    self._print_stats()
                await asyncio.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                print("")
                print("[ENGINE] Stopping...")
                self._running = False
            except Exception as e:
                print("[ENGINE] Error: " + str(e))
                await asyncio.sleep(5)

        self._log_events()
        self._print_stats()
        if self.mode == "paper":
            self.paper.print_summary()
        print("[ENGINE] Stopped")

    def _print_stats(self):
        scanner_stats = self.scanner.get_scan_stats()
        sniper_stats = self.sniper.get_stats()
        orch_stats = self.orchestrator.get_stats()
        print("")
        print("--- Engine Stats (" + self.mode.upper() + ") ---")
        print("  Scans: " + str(self._scan_count))
        print("  Unique tokens: " + str(scanner_stats.get("unique_tokens_seen", 0)))
        print("  Signals: " + str(self._signals_generated))
        print("  Safe trades: " + str(self._safe_trades))
        print("  Rug blocked: " + str(self._rug_blocked))
        print("  AI skipped: " + str(self._ai_skipped))
        print("  Executed: " + str(self._trades_executed))
        print("  AI approved: " + str(orch_stats.get("ai_approved", 0)))
        print("  AI rejected: " + str(orch_stats.get("ai_rejected", 0)))
        print("  Bedrock: " + ("ON" if orch_stats.get("bedrock_configured") else "OFF"))
        print("  Capital: $" + "{:.2f}".format(sniper_stats.get("total_capital", self.capital)))
        print("  Open positions: " + str(sniper_stats.get("open_positions", 0)))
        print("  Total PnL: $" + str(sniper_stats.get("total_pnl", 0)))
        print("  Win rate: " + str(sniper_stats.get("win_rate", 0)) + "%")
        print("--- End Stats ---")


def main():
    import sys
    import os
    capital = DEFAULT_CAPITAL
    mode = "paper"
    rpc_url = None
    args = sys.argv[1:]
    for arg in args:
        if arg == "--live":
            mode = "live"
        elif arg.startswith("--rpc="):
            rpc_url = arg.split("=", 1)[1]
        elif not arg.startswith("--"):
            try:
                capital = float(arg)
            except ValueError:
                pass
    if mode == "live" and not os.getenv("SOLANA_PRIVATE_KEY"):
        print("[ENGINE] ERROR: --live requires SOLANA_PRIVATE_KEY env variable")
        print("[ENGINE] Falling back to paper mode")
        mode = "paper"
    engine = MicroEngine(capital=capital, mode=mode, rpc_url=rpc_url)
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
