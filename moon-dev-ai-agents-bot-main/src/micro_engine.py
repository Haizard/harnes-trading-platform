"""
Moon Dev Micro-Cap Trading Engine (Paper + Live + AI Orchestrator)
DSH Pattern: Scanner -> Safety -> AI Decision -> Trade -> Learn

Uses PostgreSQL for all event logging. EventBus for inter-module communication.
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from src.token_scanner import TokenScanner, TokenCandidate, get_category_params
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

DEFAULT_CAPITAL = 100.0
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

        # DB availability
        self._db_available = False
        try:
            from src.db_storage import get_pool
            self._db_available = get_pool() is not None
        except Exception:
            pass

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
        # Also log to DB
        if self._db_available:
            try:
                from src.db_storage import log_event
                log_event(event_name, payload)
            except Exception:
                pass

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.event_bus.emit(event_name, payload))
            else:
                loop.run_until_complete(self.event_bus.emit(event_name, payload))
        except RuntimeError:
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
        """Log wallet swap events to DB."""
        self._log_event_to_db("wallet/swap", payload)

    def _on_smart_money_consensus(self, payload):
        """Log smart money consensus events to DB."""
        self._log_event_to_db("wallet/smart_money", payload)

    def _log_event_to_db(self, event_type: str, data: dict):
        """Log event to PostgreSQL."""
        if self._db_available:
            try:
                from src.db_storage import log_event
                log_event(event_type, data)
            except Exception:
                pass

    def _poll_wallets(self):
        """DSH BackgroundJob: poll tracked wallets for smart money swaps."""
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
                    # Save wallet event to DB
                    if self._db_available:
                        try:
                            from src.db_storage import save_wallet_event
                            save_wallet_event(
                                event_type="wallet/swap",
                                wallet=evt.get('wallet', ''),
                                token_address=evt.get('token_address', ''),
                                direction=evt.get('direction', ''),
                                amount_sol=evt.get('amount_sol', 0),
                                data=evt,
                            )
                        except Exception:
                            pass
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
        self._log_event_to_db("token/candidate", candidate.to_dict())

        # Save scanner result to DB
        if self._db_available:
            try:
                from src.db_storage import save_scanner_result
                save_scanner_result(
                    token_address=candidate.address,
                    symbol=candidate.symbol,
                    score=candidate.score,
                    liquidity_usd=candidate.liquidity_usd,
                    volume_24h=candidate.volume_24h,
                    price_usd=candidate.price_usd,
                    data=candidate.to_dict(),
                )
            except Exception:
                pass

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
            self._log_event_to_db("rug/blocked", {
                "token": candidate.address, "symbol": candidate.symbol,
                "risk_score": report.risk_score, "reasons": report.reasons,
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

        # Step 3: Evaluate position sizing with category-aware params
        cat_params = get_category_params(candidate.category)
        signal = self.sniper.evaluate_signal(
            candidate.address, candidate.symbol,
            candidate.score, candidate.liquidity_usd,
            category=str(candidate.category),
            stop_loss_pct=cat_params["stop_loss_pct"],
            take_profit_pct=cat_params["take_profit_pct"],
            max_hold_hours=cat_params["max_hold_hours"],
        )
        if not signal:
            return

        # Step 4: Execute trade with category-aware params
        if self.mode == "live":
            self._execute_live_trade(signal, candidate, report, cat_params)
        else:
            self._execute_paper_trade(signal, candidate, report, cat_params)

    def _execute_live_trade(self, signal, candidate, safety_report, cat_params=None):
        self._safe_trades += 1
        print("")
        print("=" * 60)
        print("LIVE TRADE: " + signal.symbol)
        print("   Score: " + str(int(signal.score)) + "/100")
        print("   Amount: $" + "{:.2f}".format(signal.amount_usd))
        print("   Safety: Risk " + str(int(safety_report.risk_score)) + "/100")
        print("   Mode: LIVE")
        print("=" * 60)

        # Emit ORDER_INTENT via EventBus
        self._emit_event(Events.ORDER_INTENT, {
            "symbol": signal.symbol, "amount_usd": signal.amount_usd,
            "score": int(signal.score), "mode": "live",
            "token": signal.token_address, "side": signal.side,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        pos = self.sniper.execute_buy(signal)
        if pos:
            self._trades_executed += 1
            # Emit ORDER_SUBMITTED and POSITION_OPENED via EventBus
            self._emit_event(Events.ORDER_SUBMITTED, {
                "symbol": pos.symbol, "amount_usd": pos.amount_usd,
                "score": int(signal.score), "mode": "live",
                "token": pos.token_address, "side": pos.side,
                "entry_price": pos.entry_price, "token_amount": pos.token_amount,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self._emit_event(Events.POSITION_OPENED, {
                "symbol": pos.symbol, "amount_usd": pos.amount_usd,
                "score": int(signal.score), "mode": "live",
                "token": pos.token_address, "side": pos.side,
                "entry_price": pos.entry_price,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    def _execute_paper_trade(self, signal, candidate, safety_report, cat_params=None):
        self._safe_trades += 1
        print("")
        print("=" * 60)
        print("PAPER TRADE: " + signal.symbol)
        print("   Score: " + str(int(signal.score)) + "/100")
        print("   Amount: $" + "{:.2f}".format(signal.amount_usd))
        print("   Safety: Risk " + str(int(safety_report.risk_score)) + "/100")
        print("=" * 60)

        # Emit ORDER_INTENT via EventBus
        self._emit_event(Events.ORDER_INTENT, {
            "symbol": signal.symbol, "amount_usd": signal.amount_usd,
            "score": int(signal.score), "mode": "paper",
            "token": signal.token_address, "side": signal.side,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        params = cat_params or {}
        trade = self.paper.buy(
            token_address=signal.token_address, symbol=signal.symbol,
            amount_usd=signal.amount_usd, score=signal.score,
            signals=candidate.signals, category=str(candidate.category),
            stop_loss_pct=params.get("stop_loss_pct", 10.0),
            take_profit_pct=params.get("take_profit_pct", 30.0),
            max_hold_hours=params.get("max_hold_hours", 12.0),
        )
        if trade:
            self._trades_executed += 1
            # Emit ORDER_SUBMITTED and POSITION_OPENED via EventBus
            self._emit_event(Events.ORDER_SUBMITTED, {
                "symbol": trade.symbol, "amount_usd": trade.amount_usd,
                "score": int(signal.score), "mode": "paper",
                "token": trade.token_address, "side": trade.side,
                "entry_price": trade.entry_price,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self._emit_event(Events.POSITION_OPENED, {
                "symbol": trade.symbol, "amount_usd": trade.amount_usd,
                "score": int(signal.score), "mode": "paper",
                "token": trade.token_address, "side": trade.side,
                "entry_price": trade.entry_price,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    def _check_exits(self):
        if self.mode == "live":
            closed = self.sniper.check_exits()
            for pos in closed:
                self.orchestrator.record_trade_outcome(pos.symbol, pos.pnl_usd, pos.pnl_pct, 0)
                self._emit_event(Events.POSITION_CLOSED, {
                    "symbol": pos.symbol, "amount_usd": pos.amount_usd,
                    "pnl_usd": pos.pnl_usd, "pnl_pct": pos.pnl_pct,
                    "reason": pos.status, "mode": "live",
                    "token": pos.token_address,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        else:
            closed = self.paper.check_exits()
            for trade in closed:
                self.orchestrator.record_trade_outcome(trade.symbol, trade.pnl_usd, trade.pnl_pct, 0)
                self._emit_event(Events.POSITION_CLOSED, {
                    "symbol": trade.symbol, "amount_usd": trade.amount_usd,
                    "pnl_usd": trade.pnl_usd, "pnl_pct": trade.pnl_pct,
                    "reason": trade.status, "mode": "paper",
                    "token": trade.token_address,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    async def run(self):
        """Main engine loop."""
        self._running = True
        print("[ENGINE] Starting micro-cap trading engine...")
        print("[ENGINE] Mode: " + self.mode)
        print("[ENGINE] Capital: $" + str(self.capital))

        # Send startup notification to Telegram
        try:
            self.telegram.send_startup_message(self.capital, self.mode)
        except Exception as e:
            print("[TG] Startup notify failed: " + str(e))

        # Start the scanner
        self.scanner.start()

        # Start async scheduler for background jobs
        await self.scheduler.start()

        cycle = 0
        while self._running:
            try:
                cycle += 1
                # Check exits periodically
                if cycle % (EXIT_CHECK_INTERVAL // SCAN_INTERVAL + 1) == 0:
                    self._check_exits()

                # Save portfolio to DB periodically
                if cycle % 10 == 0:
                    self._save_portfolio_to_db()

                await asyncio.sleep(SCAN_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print("[ENGINE] Error: " + str(e))
                self._emit_event(Events.AGENT_ERROR, {
                    "error": str(e), "context": "main_loop",
                })
                await asyncio.sleep(5)

    def stop(self):
        """Stop the engine."""
        self._running = False
        self.scanner.stop()
        print("[ENGINE] Stopped")
