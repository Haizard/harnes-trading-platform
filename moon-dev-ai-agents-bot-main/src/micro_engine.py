"""
Moon Dev Micro-Cap Trading Engine (Paper Trading + Rug-Pull Detection)
DSH Pattern: Event-driven engine that coordinates scanner -> safety -> paper trade -> exit check.
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

DEFAULT_CAPITAL = 25.0
SCAN_INTERVAL = 30
EXIT_CHECK_INTERVAL = 10
MIN_SCORE = 40


class MicroEngine:
    """DSH-compliant micro-cap trading engine with paper trading and rug-pull detection."""

    def __init__(self, capital=DEFAULT_CAPITAL):
        self.capital = capital
        self.scanner = TokenScanner(callback=self._on_candidate)
        self.sniper = MicroSniper(capital=capital)
        self.paper = PaperTrader(capital=capital)
        self.rug_detector = RugPullDetector()
        self._running = False
        self._scan_count = 0
        self._signals_generated = 0
        self._trades_executed = 0
        self._safe_trades = 0
        self._rug_blocked = 0
        self.data_dir = Path("src/data/micro_engine")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._events = []

    def _on_candidate(self, candidate):
        """Called when scanner finds a candidate."""
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
        for reason in report.reasons:
            print("[RUG]   " + reason)

        # Step 2: Evaluate trade signal
        signal = self.sniper.evaluate_signal(
            candidate.address, candidate.symbol,
            candidate.score, candidate.liquidity_usd,
        )
        if signal:
            self._execute_paper_trade(signal, candidate, report)

    def _execute_paper_trade(self, signal, candidate, safety_report):
        """Execute a paper trade after passing safety checks."""
        self._safe_trades += 1
        print("")
        print("=" * 60)
        print("PAPER TRADE: " + signal.symbol)
        print("   Score: " + str(int(signal.score)) + "/100")
        print("   Amount: $" + "{:.2f}".format(signal.amount_usd))
        print("   Safety: Risk " + str(int(safety_report.risk_score)) + "/100")
        print("   Reason: " + signal.reason)
        print("=" * 60)

        self._events.append({
            "type": "paper/intent",
            "data": {"token": signal.token_address, "symbol": signal.symbol,
                      "side": signal.side, "amount_usd": signal.amount_usd,
                      "score": signal.score, "safety_score": safety_report.risk_score},
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
        """Check all paper positions for exits."""
        closed = self.paper.check_exits()
        for trade in closed:
            self._events.append({
                "type": "paper/exit",
                "data": trade.to_dict(),
                "timestamp": datetime.utcnow().isoformat(),
            })

    def _log_events(self):
        """Persist events to JSONL file."""
        log_path = self.data_dir / "engine_events.jsonl"
        with open(log_path, "a") as f:
            for event in self._events:
                f.write(json.dumps(event, default=str) + chr(10))
        self._events = []

    async def run(self):
        """Main engine loop."""
        self._running = True
        print("")
        print("=" * 60)
        print("MICRO ENGINE STARTED (PAPER TRADING MODE)")
        print("   Capital: $" + "{:.2f}".format(self.capital))
        print("   Scan interval: " + str(SCAN_INTERVAL) + "s")
        print("   Exit check: " + str(EXIT_CHECK_INTERVAL) + "s")
        print("   Rug-pull protection: ENABLED")
        print("   Mode: PAPER (no real money at risk)")
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
        self.paper.print_summary()
        print("[ENGINE] Stopped")

    def _print_stats(self):
        scanner_stats = self.scanner.get_scan_stats()
        paper_stats = self.paper.get_stats()
        print("")
        print("--- Engine Stats ---")
        print("  Scans: " + str(self._scan_count))
        print("  Unique tokens: " + str(scanner_stats.get("unique_tokens_seen", 0)))
        print("  Signals: " + str(self._signals_generated))
        print("  Safe trades: " + str(self._safe_trades))
        print("  Rug blocked: " + str(self._rug_blocked))
        print("  Paper trades: " + str(self._trades_executed))
        print("  Capital: $" + "{:.2f}".format(paper_stats.get("current_capital", self.capital)))
        print("  Open positions: " + str(paper_stats.get("open_positions", 0)))
        print("  Total PnL: $" + str(paper_stats.get("total_pnl", 0)))
        print("  Win rate: " + str(paper_stats.get("win_rate", 0)) + "%")
        print("  Profit factor: " + str(paper_stats.get("profit_factor", 0)))
        print("--- End Stats ---")


def main():
    import sys
    capital = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CAPITAL
    engine = MicroEngine(capital=capital)
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
