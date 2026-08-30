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
from datetime import datetime, timezone
from src.agent_orchestrator import AgentOrchestrator
from src.telegram_reporter import get_telegram_reporter
from src.lightweight_sentiment import get_lightweight_sentiment
from src.async_scheduler import AsyncScheduler, JobStatus

# Wallet Intelligence (smart money tracking)
try:
    from src.wallet_tracker import WalletTracker
    from src.wallet_scorer import WalletScorer
    from src.smart_money_detector import SmartMoneyDetector
    WALLET_INTEL_AVAILABLE = True
except ImportError:
    WALLET_INTEL_AVAILABLE = False

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
        self.telegram = get_telegram_reporter()
        self.sentiment = get_lightweight_sentiment()
        self._register_telegram_listeners()
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

        # DSH AsyncScheduler — non-blocking background jobs
        self.scheduler = AsyncScheduler()

        # Wallet Intelligence (smart money tracking)
        self.wallet_tracker = None
        self.wallet_scorer = None
        self.smart_money_detector = None
        if WALLET_INTEL_AVAILABLE:
            try:
                self.wallet_tracker = WalletTracker(event_bus=self.event_bus)
                self.wallet_scorer = WalletScorer()
                self.smart_money_detector = SmartMoneyDetector(
                    tracker=self.wallet_tracker,
                    scorer=self.wallet_scorer,
                    event_bus=self.event_bus,
                )
                # Register wallet event listeners (DSH pattern)
                self._register_wallet_listeners()
                # Register wallet polling as DSH BackgroundJob
                self.scheduler.register(
                    name="wallet_poll",
                    fn=self._poll_wallets,
                    interval_seconds=60,
                )
                wallet_count = len(self.wallet_tracker.get_tracked_wallets())
                print("[ENGINE] Wallet Intelligence ENABLED — tracking " + str(wallet_count) + " wallets")
            except Exception as e:
                print("[ENGINE] Wallet Intelligence unavailable: " + str(e))

    def _emit_event(self, event_name: str, payload: dict):
        """Fire-and-forget event emission. Wraps async emit() with create_task().
        Safe to call from sync methods within the async run() loop.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.event_bus.emit(event_name, payload))
            else:
                loop.run_until_complete(self.event_bus.emit(event_name, payload))
        except RuntimeError:
            # No running loop — use a thread
            import concurrent.futures
            def _do_emit():
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self.event_bus.emit(event_name, payload))
                finally:
                    loop.close()
            concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(_do_emit)

    def _register_telegram_listeners(self):
        """Wire Telegram as event listeners on the bus (DSH pattern)."""
        self.event_bus.on(Events.POSITION_OPENED, self._tg_on_entry, mode=DispatchMode.EMIT, tag="telegram")
        self.event_bus.on(Events.POSITION_CLOSED, self._tg_on_exit, mode=DispatchMode.EMIT, tag="telegram")
        self.event_bus.on(Events.AGENT_ERROR, self._tg_on_error, mode=DispatchMode.EMIT, tag="telegram")

    def _register_wallet_listeners(self):
        """Wire wallet intelligence events to Telegram (DSH pattern)."""
        if self.smart_money_detector:
            self.event_bus.on(Events.SMART_MONEY_ALERT, self._tg_on_smart_money, mode=DispatchMode.EMIT, tag="telegram")
        self.event_bus.on(Events.WALLET_SWAP_DETECTED, self._on_wallet_swap, mode=DispatchMode.EMIT, tag="wallet_log")
        self.event_bus.on(Events.SMART_MONEY_CONSENSUS, self._on_smart_money_consensus, mode=DispatchMode.EMIT, tag="wallet_log")

    def _tg_on_entry(self, payload):
        """Telegram listener for trade entry events."""
        self.telegram.notify_entry(
            symbol=payload.get("symbol", ""),
            amount_usd=payload.get("amount_usd", 0),
            score=payload.get("score", 0),
            mode=payload.get("mode", "paper"),
        )

    def _tg_on_exit(self, payload):
        """Telegram listener for trade exit events."""
        self.telegram.notify_exit(
            symbol=payload.get("symbol", ""),
            amount_usd=payload.get("amount_usd", 0),
            pnl_usd=payload.get("pnl_usd", 0),
            pnl_pct=payload.get("pnl_pct", 0),
            reason=payload.get("reason", "exit"),
            mode=payload.get("mode", "paper"),
        )

    def _save_portfolio_to_db(self):
        """Save portfolio state to PostgreSQL if available."""
        try:
            from src.db_storage import save_portfolio
            stats = self.paper.get_stats()
            save_portfolio(
                initial_capital=stats["initial_capital"],
                current_capital=stats["current_capital"],
                total_pnl=stats["total_pnl"],
                total_trades=stats["total_trades"],
                wins=stats["wins"],
                losses=stats["losses"],
            )
        except Exception:
            pass

    def _tg_on_error(self, payload):
        """Telegram listener for error events."""
        self.telegram.notify_error(
            error=payload.get("error", "unknown"),
            context=payload.get("context", ""),
        )

    def _tg_on_smart_money(self, payload):
        """Telegram listener for high-confidence smart money alerts."""
        try:
            token = payload.get("token_address", "")[:8]
            wallets = payload.get("wallets_buying", 0)
            sol = payload.get("aggregate_buy_sol", 0)
            conf = payload.get("confidence", 0)
            self.telegram.notify_error(
                error="SMART MONEY ALERT",
                context=f"{wallets} wallets buying {token}... ({sol:.2f} SOL, conf={conf:.2f})",
            )
        except Exception:
            pass

    def _on_wallet_swap(self, payload):
        """Log wallet swap events to engine events."""
        self._events.append({
            "type": "wallet/swap",
            "data": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _on_smart_money_consensus(self, payload):
        """Log smart money consensus events to engine events."""
        self._events.append({
            "type": "wallet/smart_money",
            "data": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _poll_wallets(self):
        """DSH BackgroundJob: poll tracked wallets for smart money swaps.
        Called by AsyncScheduler every 60s. Handles errors gracefully.
        """
        if not self.wallet_tracker:
            return
        try:
            new_events = self.wallet_tracker.poll_wallets()
            if new_events:
                print("[WALLET] Detected " + str(len(new_events)) + " new swap events from tracked wallets")
                for evt in new_events:
                    print("[WALLET]   " + evt.get('wallet', '')[:8] + "... " +
                          evt.get('direction', '').upper() + " " +
                          evt.get('token_address', '')[:8] + "... " +
                          str(round(evt.get('amount_sol', 0), 4)) + " SOL")
            # Check for smart money consensus signals
            if self.smart_money_detector:
                signals = self.smart_money_detector.scan(hours=1)
                if signals:
                    for sig in signals:
                        print("[SMART MONEY] CONSENSUS DETECTED!")
                        print("[SMART MONEY]   Token: " + sig.token_address[:8] + "...")
                        print("[SMART MONEY]   Wallets buying: " + str(sig.wallets_buying))
                        print("[SMART MONEY]   Aggregate volume: " + str(round(sig.aggregate_buy_sol, 4)) + " SOL")
                        print("[SMART MONEY]   Confidence: " + str(round(sig.confidence, 3)))
        except Exception as e:
            print("[ENGINE] Wallet poll error: " + str(e))

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

        # Step 1.5: Quick sentiment check (non-blocking, uses cache)
        try:
            sent = self.sentiment.get_token_sentiment(candidate.symbol)
            if sent and sent.get("tweet_count", 0) > 0:
                print("[SENTIMENT] " + candidate.symbol + ": " + sent.get("label", "unknown") + 
                      " (score=" + str(sent.get("score", 0)) + ", tweets=" + str(sent.get("tweet_count", 0)) + ")")
        except Exception:
            pass

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
        self._emit_event(Events.POSITION_OPENED, {
            "symbol": signal.symbol, "amount_usd": signal.amount_usd,
            "score": int(signal.score), "mode": "live",
            "token": signal.token_address, "side": signal.side,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
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
        self._emit_event(Events.POSITION_OPENED, {
            "symbol": signal.symbol, "amount_usd": signal.amount_usd,
            "score": int(signal.score), "mode": "paper",
            "token": signal.token_address, "side": signal.side,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
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
                self._emit_event(Events.POSITION_CLOSED, {
                    "symbol": pos.symbol, "amount_usd": pos.amount_usd,
                    "pnl_usd": pos.pnl_usd, "pnl_pct": pos.pnl_pct,
                    "reason": pos.exit_reason if hasattr(pos, 'exit_reason') else 'exit',
                    "mode": "live", "token": pos.token_address,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                self._events.append({"type": "live/exit", "data": pos.to_dict(),
                    "timestamp": datetime.utcnow().isoformat()})
        else:
            closed = self.paper.check_exits()
            for trade in closed:
                self.orchestrator.record_trade_outcome(trade.symbol, trade.pnl_usd, trade.pnl_pct, 0)
                self._emit_event(Events.POSITION_CLOSED, {
                    "symbol": trade.symbol, "amount_usd": trade.amount_usd,
                    "pnl_usd": trade.pnl_usd, "pnl_pct": trade.pnl_pct,
                    "reason": trade.exit_reason if hasattr(trade, 'exit_reason') else 'exit',
                    "mode": "paper", "token": trade.token_address,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
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
        self.telegram.set_paper_trader(self.paper)
        self.telegram.set_capital(self.capital, self.capital)
        self.telegram.send_startup_message(self.capital, self.mode)
        last_exit_check = time.time()
        last_heartbeat = time.time()
        HEARTBEAT_INTERVAL = 1800  # 30 minutes

        # Start DSH AsyncScheduler (background jobs)
        await self.scheduler.start()
        print("[ENGINE] Background jobs started (wallet_poll every 60s)")

        while self._running:
            try:
                candidates = self.scanner.scan_once()
                self._scan_count += 1
                if candidates:
                    print("[ENGINE] Scan #" + str(self._scan_count) + ": Found " + str(len(candidates)) + " candidates")
                if time.time() - last_exit_check >= EXIT_CHECK_INTERVAL:
                    self._check_exits()
                    last_exit_check = time.time()

                # Update capital in telegram and DB
                self.telegram.set_capital(self.capital, self.paper.capital)
                self._save_portfolio_to_db()
                # Heartbeat every 30 min
                if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                    self.telegram.notify_heartbeat()
                    last_heartbeat = time.time()
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
                self._emit_event(Events.AGENT_ERROR, {
                    "error": str(e), "context": "engine_loop",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                await asyncio.sleep(5)

        # Stop DSH AsyncScheduler
        await self.scheduler.stop()
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
        if self.wallet_tracker:
            wt_stats = self.wallet_tracker.get_stats()
            print("  Wallets tracked: " + str(wt_stats.get("tracked_wallets", 0)))
            print("  Wallet events 24h: " + str(wt_stats.get("events_24h", 0)))
            print("  Smart money signals: " + str(self.smart_money_detector.get_stats().get("total_signals", 0) if self.smart_money_detector else 0))
        scheduler_stats = self.scheduler.get_status()
        for job_name, job_info in scheduler_stats.items():
            print("  Job " + job_name + ": " + job_info.get("status", "unknown") + " (" + str(job_info.get("runs", 0)) + " runs)")
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
