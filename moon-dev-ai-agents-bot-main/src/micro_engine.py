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

from src.token_scanner import TokenScanner, TokenCandidate
from src.category_agents import get_category_params, TokenCategory
from src.micro_sniper import MicroSniper, TradeSignal
from src.paper_trader import PaperTrader
from src.rug_pull_detector import RugPullDetector
from src.event_bus import EventBus, Events, DispatchMode
from datetime import datetime, timezone
from termcolor import cprint
from src.agent_orchestrator import AgentOrchestrator
from src.telegram_reporter import get_telegram_reporter
from src.lightweight_sentiment import get_lightweight_sentiment
from src.async_scheduler import AsyncScheduler, JobStatus
from src.event_bus import _fire_and_forget

# Strategy Bridge — connects backtest strategies to live engine
try:
    from src.strategy_bridge import get_strategy_bridge
    STRATEGY_BRIDGE_AVAILABLE = True
except ImportError:
    STRATEGY_BRIDGE_AVAILABLE = False

from src.trading_confluence import get_trading_confluence
from src.pine_backtest_pipeline import get_pine_backtest_pipeline

# OHLCV Collector — builds candle history for strategy analysis
try:
    from src.ohlcv_collector import get_ohlcv_collector
    OHLCV_COLLECTOR_AVAILABLE = True
except ImportError:
    OHLCV_COLLECTOR_AVAILABLE = False

# Wallet Intelligence (smart money tracking)
try:
    from src.wallet_tracker import WalletTracker
    from src.wallet_scorer import WalletScorer
    from src.smart_money_detector import SmartMoneyDetector
    WALLET_INTEL_AVAILABLE = True
except ImportError:
    WALLET_INTEL_AVAILABLE = False

# Portfolio Risk Manager — circuit breaker, max loss/gain, min balance
try:
    from src.portfolio_risk_manager import get_portfolio_risk_manager
    PORTFOLIO_RISK_AVAILABLE = True
except ImportError:
    PORTFOLIO_RISK_AVAILABLE = False

# PredictionEngine v2 — multi-factor scoring
try:
    from src.prediction_engine_v2 import get_prediction_engine
    PREDICTION_V2_AVAILABLE = True
except ImportError:
    PREDICTION_V2_AVAILABLE = False

# Feature Engineer — microstructure features
try:
    from src.feature_engineer import get_feature_engineer
    FEATURE_ENGINEER_AVAILABLE = True
except ImportError:
    FEATURE_ENGINEER_AVAILABLE = False

# LLM Exit Decider — AI-driven exits
try:
    from src.llm_exit_decider import get_llm_exit_decider
    LLM_EXIT_AVAILABLE = True
except ImportError:
    LLM_EXIT_AVAILABLE = False

# AI Override Engine — override decisions during risk events
try:
    from src.ai_override_engine import get_ai_override_engine
    AI_OVERRIDE_AVAILABLE = True
except ImportError:
    AI_OVERRIDE_AVAILABLE = False

# Full SentimentAgent — BERTweet ML + Twitter scraping
try:
    from src.full_sentiment_agent import get_full_sentiment_agent
    FULL_SENTIMENT_AVAILABLE = True
except ImportError:
    FULL_SENTIMENT_AVAILABLE = False

# Chart Analysis Agent — chart pattern recognition
try:
    from src.chart_analysis_agent import get_chart_analysis_agent
    CHART_ANALYSIS_AVAILABLE = True
except ImportError:
    CHART_ANALYSIS_AVAILABLE = False

# CoinGecko Agent — market data & token discovery
try:
    from src.coingecko_agent import get_coingecko_agent
    COINGECKO_AVAILABLE = True
except ImportError:
    COINGECKO_AVAILABLE = False

# ICT Analysis Agent — Smart Money concepts
try:
    from src.ict_analysis_agent import get_ict_analysis_agent
    ICT_ANALYSIS_AVAILABLE = True
except ImportError:
    ICT_ANALYSIS_AVAILABLE = False

# Storage Tier Manager — data lifecycle, cleanup, compression
try:
    from src.storage_tier_manager import get_storage_tier_manager
    STORAGE_TIER_AVAILABLE = True
except ImportError:
    STORAGE_TIER_AVAILABLE = False

# Backup Manager — data backup to separate DB
try:
    from src.backup_manager import get_backup_manager
    BACKUP_MANAGER_AVAILABLE = True
except ImportError:
    BACKUP_MANAGER_AVAILABLE = False

# Order Book Collector — whale detection via order book
try:
    from src.orderbook_collector import get_orderbook_collector
    ORDERBOOK_COLLECTOR_AVAILABLE = True
except ImportError:
    ORDERBOOK_COLLECTOR_AVAILABLE = False

# Data Compressor — compress/aggregate old data
try:
    from src.data_compressor import get_data_compressor
    DATA_COMPRESSOR_AVAILABLE = True
except ImportError:
    DATA_COMPRESSOR_AVAILABLE = False

# Storage Alerts — DB usage monitoring
try:
    from src.storage_alerts import get_storage_alerts
    STORAGE_ALERTS_AVAILABLE = True
except ImportError:
    STORAGE_ALERTS_AVAILABLE = False

DEFAULT_CAPITAL = 100.0
SCAN_INTERVAL = 30
EXIT_CHECK_INTERVAL = 10
MIN_SCORE = 40


class MicroEngine:
    """DSH-compliant micro-cap trading engine with AI orchestrator."""

    def __init__(self, capital=DEFAULT_CAPITAL, mode="paper", rpc_url=None):
        self.capital = capital
        self.mode = mode
        self.sniper = MicroSniper(capital=capital, mode=mode, rpc_url=rpc_url)
        self.mode = self.sniper.mode  # Sync mode (may fallback from live to paper)
        self.paper = PaperTrader(capital=capital)
        self.orchestrator = AgentOrchestrator(capital=capital, mode=self.mode)
        self.event_bus = self.orchestrator.event_bus
        self.rug_detector = RugPullDetector(event_bus=self.event_bus)
        # Scanner gets event_bus for DSH category agents
        self.scanner = TokenScanner(callback=self._on_candidate, event_bus=self.event_bus)
        self.telegram = get_telegram_reporter()
        self.telegram.set_paper_trader(self.paper)
        self.sentiment = get_lightweight_sentiment(event_bus=self.event_bus)
        self._register_telegram_listeners()
        self._running = False
        self._scan_count = 0
        self._signals_generated = 0
        self._trades_executed = 0
        self._safe_trades = 0
        self._rug_blocked = 0
        self._ai_skipped = 0
        self._strategy_boosts = 0

        # Trading Confluence Engine - 7-in-1 validation gate
        try:
            from src.trading_confluence import get_trading_confluence
            self.confluence = get_trading_confluence(event_bus=self.event_bus)
            print("[ENGINE] Trading Confluence ENABLED - 5 validation gates active")
        except Exception as e:
            self.confluence = None
            print("[ENGINE] Trading Confluence unavailable: " + str(e))

        # Pine Backtest Pipeline - strategy validation
        try:
            self.pine_backtest = get_pine_backtest_pipeline(event_bus=self.event_bus)
            print("[ENGINE] Pine Backtest Pipeline connected")
        except Exception as e:
            self.pine_backtest = None
            print("[ENGINE] Pine Backtest Pipeline unavailable: " + str(e))

                # Strategy Bridge — runs backtest strategies on candidates
        self.strategy_bridge = None
        if STRATEGY_BRIDGE_AVAILABLE:
            try:
                self.strategy_bridge = get_strategy_bridge(event_bus=self.event_bus)
                print("[ENGINE] Strategy Bridge connected — backtest strategies active")
            except Exception as e:
                print("[ENGINE] Strategy Bridge unavailable: " + str(e))

        # OHLCV Collector — builds candle history for strategy analysis
        self.ohlcv_collector = None
        if OHLCV_COLLECTOR_AVAILABLE:
            try:
                self.ohlcv_collector = get_ohlcv_collector()
                print("[ENGINE] OHLCV Collector connected — building candle history")
            except Exception as e:
                print("[ENGINE] OHLCV Collector unavailable: " + str(e))

        self.data_dir = Path("src/data/micro_engine")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._events = []

        # DB availability — start with JSON fallback if DB unavailable
        self._db_available = False
        try:
            from src.db_storage import get_pool
            self._db_available = get_pool() is not None
        except Exception:
            pass

        if not self._db_available:
            print("[ENGINE] WARNING: Database unavailable — using JSON fallback", flush=True)
            print("[ENGINE] Trades and events will be saved to local JSON files", flush=True)
            print("[ENGINE] Set LUCERIS_DATABASE_URL to enable PostgreSQL storage", flush=True)

        # Restore engine counters from DB
        self._restore_counters_from_db()

        # DSH AsyncScheduler — non-blocking background jobs
        self.scheduler = AsyncScheduler()

        # Wallet Intelligence (smart money tracking)
        self.wallet_tracker = None
        self.wallet_scorer = None
        self.smart_money_detector = None
        if WALLET_INTEL_AVAILABLE:
            try:
                self.wallet_tracker = WalletTracker(event_bus=self.event_bus)
                self.wallet_scorer = WalletScorer(event_bus=self.event_bus)
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

        # Portfolio Risk Manager — circuit breaker, max loss/gain, min balance
        self.portfolio_risk = None
        if PORTFOLIO_RISK_AVAILABLE:
            try:
                self.portfolio_risk = get_portfolio_risk_manager(initial_capital=capital, mode=self.mode, event_bus=self.event_bus)
                print("[ENGINE] Portfolio Risk Manager connected — circuit breaker active")
            except Exception as e:
                print("[ENGINE] Portfolio Risk Manager unavailable: " + str(e))

        # PredictionEngine v2 — multi-factor scoring
        self.prediction_engine = None
        if PREDICTION_V2_AVAILABLE:
            try:
                self.prediction_engine = get_prediction_engine()
                print("[ENGINE] PredictionEngine v2 connected — multi-factor signals active")
            except Exception as e:
                print("[ENGINE] PredictionEngine v2 unavailable: " + str(e))

        # Feature Engineer — microstructure features
        self.feature_engineer = None
        if FEATURE_ENGINEER_AVAILABLE:
            try:
                self.feature_engineer = get_feature_engineer()
                print("[ENGINE] Feature Engineer connected — microstructure analysis active")
            except Exception as e:
                print("[ENGINE] Feature Engineer unavailable: " + str(e))

        # LLM Exit Decider — AI-driven exits
        self.exit_decider = None
        if LLM_EXIT_AVAILABLE:
            try:
                self.exit_decider = get_llm_exit_decider(event_bus=self.event_bus)
                print("[ENGINE] LLM Exit Decider connected — AI exits active")
            except Exception as e:
                print("[ENGINE] LLM Exit Decider unavailable: " + str(e))

        # AI Override Engine — override decisions during risk events
        self.override_engine = None
        if AI_OVERRIDE_AVAILABLE:
            try:
                self.override_engine = get_ai_override_engine(event_bus=self.event_bus)
                print("[ENGINE] AI Override Engine connected — risk overrides active")
            except Exception as e:
                print("[ENGINE] AI Override Engine unavailable: " + str(e))

        # Full SentimentAgent — BERTweet ML + Twitter scraping
        self.full_sentiment = None
        if FULL_SENTIMENT_AVAILABLE:
            try:
                self.full_sentiment = get_full_sentiment_agent(event_bus=self.event_bus)
                print("[ENGINE] Full SentimentAgent connected — BERTweet ML active")
            except Exception as e:
                print("[ENGINE] Full SentimentAgent unavailable: " + str(e))

        # Chart Analysis Agent — chart pattern recognition
        self.chart_agent = None
        if CHART_ANALYSIS_AVAILABLE:
            try:
                self.chart_agent = get_chart_analysis_agent()
                print("[ENGINE] Chart Analysis Agent connected — pattern recognition active")
            except Exception as e:
                print("[ENGINE] Chart Analysis Agent unavailable: " + str(e))

        # CoinGecko Agent — market data & token discovery
        self.coingecko = None
        if COINGECKO_AVAILABLE:
            try:
                self.coingecko = get_coingecko_agent()
                print("[ENGINE] CoinGecko Agent connected — market data active")
            except Exception as e:
                print("[ENGINE] CoinGecko Agent unavailable: " + str(e))

        # ICT Analysis Agent — Smart Money concepts
        self.ict_agent = None
        if ICT_ANALYSIS_AVAILABLE:
            try:
                self.ict_agent = get_ict_analysis_agent()
                print("[ENGINE] ICT Analysis Agent connected — Smart Money active")
            except Exception as e:
                print("[ENGINE] ICT Analysis Agent unavailable: " + str(e))

        # Storage Tier Manager — data lifecycle, cleanup, compression
        self.storage_tier = None
        if STORAGE_TIER_AVAILABLE:
            try:
                self.storage_tier = get_storage_tier_manager(event_bus=self.event_bus)
                print("[ENGINE] Storage Tier Manager connected — data lifecycle active")
            except Exception as e:
                print("[ENGINE] Storage Tier Manager unavailable: " + str(e))

        # Backup Manager — data backup to separate DB
        self.backup_manager = None
        if BACKUP_MANAGER_AVAILABLE:
            try:
                self.backup_manager = get_backup_manager(event_bus=self.event_bus)
                print("[ENGINE] Backup Manager connected — data backup active")
            except Exception as e:
                print("[ENGINE] Backup Manager unavailable: " + str(e))

        # Order Book Collector — whale detection via order book
        self.orderbook_collector = None
        if ORDERBOOK_COLLECTOR_AVAILABLE:
            try:
                self.orderbook_collector = get_orderbook_collector(event_bus=self.event_bus)
                print("[ENGINE] Order Book Collector connected — whale detection active")
            except Exception as e:
                print("[ENGINE] Order Book Collector unavailable: " + str(e))

        # Data Compressor — compress/aggregate old data
        self.data_compressor = None
        if DATA_COMPRESSOR_AVAILABLE:
            try:
                self.data_compressor = get_data_compressor(event_bus=self.event_bus)
                print("[ENGINE] Data Compressor connected — compression active")
            except Exception as e:
                print("[ENGINE] Data Compressor unavailable: " + str(e))

        # Storage Alerts — DB usage monitoring
        self.storage_alerts = None
        if STORAGE_ALERTS_AVAILABLE:
            try:
                self.storage_alerts = get_storage_alerts(event_bus=self.event_bus)
                print("[ENGINE] Storage Alerts connected — DB monitoring active")
            except Exception as e:
                print("[ENGINE] Storage Alerts unavailable: " + str(e))

    def _restore_counters_from_db(self):
        """Restore engine counters from DB after deploy."""
        if not self._db_available:
            return
        try:
            from src.db_storage import get_trades
            trades = get_trades(limit=10000)
            self._trades_executed = len([t for t in trades if t.get("status") != "open"])
            self._safe_trades = self._trades_executed
            print("[ENGINE] Restored counters: " + str(self._trades_executed) + " past trades")
        except Exception:
            pass

    def _persist_counters(self):
        """Save engine counters to DB."""
        if not self._db_available:
            return
        try:
            from src.db_storage import save_engine_state
            save_engine_state("engine_trades_executed", str(self._trades_executed))
            save_engine_state("engine_signals_generated", str(self._signals_generated))
            save_engine_state("engine_rug_blocked", str(self._rug_blocked))
            save_engine_state("engine_ai_skipped", str(self._ai_skipped))
        except Exception:
            pass

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
                _fire_and_forget(self.event_bus.emit(event_name, payload))
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
            # Sync capital to Telegram reporter
            self.telegram.set_capital(stats["initial_capital"], stats["current_capital"])
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

    def _poll_coingecko_trending(self):
        """DSH BackgroundJob: poll CoinGecko for trending tokens."""
        if not self.coingecko:
            return
        try:
            trending = self.coingecko.get_trending()
            if trending:
                print("[COINGECKO] Trending: " + ", ".join(t["symbol"] for t in trending[:5]))
                # Log trending tokens to DB
                self._log_event_to_db("coingecko/trending", {
                    "tokens": [t["symbol"] for t in trending[:10]],
                    "count": len(trending),
                })
        except Exception as e:
            print("[COINGECKO] Trending poll error: " + str(e))

    def _poll_wallets(self):
        """DSH BackgroundJob: poll tracked wallets for smart money swaps."""
        if not self.wallet_tracker:
            return
        try:
            new_events = self.wallet_tracker.poll_wallets()
            new_events = [e for e in (new_events or []) if (getattr(e, "amount_sol", 0) or 0) > 0]
            if new_events:
                print("[WALLET] Detected " + str(len(new_events)) + " new swap events from tracked wallets")
                for evt in new_events:
                    # SwapEvent is a dataclass — use attribute access
                    print("[WALLET]   " + evt.wallet[:8] + "... " +
                          evt.direction.upper() + " " +
                          evt.token_address[:8] + "... " +
                          str(round(evt.amount_sol, 4)) + " SOL")
                    # Save wallet event to DB
                    if self._db_available:
                        try:
                            from src.db_storage import save_wallet_event
                            save_wallet_event(
                                event_type="wallet/swap",
                                wallet=evt.wallet,
                                token_address=evt.token_address,
                                direction=evt.direction,
                                amount_sol=evt.amount_sol,
                                data=evt.to_dict(),
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

        # Track order book for high-scoring candidates
        if self.orderbook_collector and candidate.score >= 60:
            try:
                self.orderbook_collector.track_token(candidate.address)
            except Exception:
                pass

        # Track OHLCV for candle-history building. This is intentionally
        # decoupled from the risk gate below so data keeps accumulating for
        # research/backtesting even while the circuit breaker is active.
        if self.ohlcv_collector:
            try:
                self.ohlcv_collector.track_token(candidate.address, candidate.pair_address)
            except Exception as e:
                print("[OHLCV] Register error for " + candidate.symbol + ": " + str(e))

        if candidate.score < MIN_SCORE:
            return

        # Step 0: Portfolio Risk Manager — circuit breaker check
        if self.portfolio_risk:
            try:
                # Update capital from paper trader
                stats = self.paper.get_stats()
                self.portfolio_risk.update_capital(stats["current_capital"])
                
                # Check risk limits
                risk_event = self.portfolio_risk.check_risk()
                if risk_event and not self.portfolio_risk.is_trading_allowed():
                    # Circuit breaker is latched - block without re-consulting the
                    # AI override or re-activating the breaker on every candidate.
                    print("[RISK] Circuit breaker active - no new trades")
                    return
            except Exception as e:
                print("[RISK] Risk check error: " + str(e))

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

        # Step 1.5: Sentiment check (full ML or lightweight)
        sentiment_data = None
        if self.full_sentiment:
            try:
                sentiment_data = self.full_sentiment.get_token_sentiment(candidate.symbol)
                if sentiment_data and sentiment_data.get("tweet_count", 0) > 0:
                    print("[SENTIMENT-ML] " + candidate.symbol + ": " + sentiment_data.get("label", "unknown") +
                          " (score=" + str(sentiment_data.get("score", 0)) +
                          ", tweets=" + str(sentiment_data.get("tweet_count", 0)) +
                          ", source=" + sentiment_data.get("source", "unknown") + ")")
            except Exception as e:
                print("[SENTIMENT-ML] Error: " + str(e))
        elif self.sentiment:
            try:
                sentiment_data = self.sentiment.get_token_sentiment(candidate.symbol)
                if sentiment_data and sentiment_data.get("tweet_count", 0) > 0:
                    print("[SENTIMENT] " + candidate.symbol + ": " + sentiment_data.get("label", "unknown") +
                          " (score=" + str(sentiment_data.get("score", 0)) + ")")
            except Exception:
                pass

        # Step 1.7: Strategy Bridge — run backtest strategies on live data
        strategy_result = None
        prediction_signal = None
        if self.strategy_bridge:
            try:
                strategy_result = self.strategy_bridge.analyze(
                    token_address=candidate.address,
                    symbol=candidate.symbol,
                    pair_address=candidate.pair_address,
                    candidate_metrics=candidate.to_dict(),
                )
                
                # Step 1.8: PredictionEngine v2 — multi-factor scoring
                if self.prediction_engine and strategy_result:
                    try:
                        indicators = strategy_result.indicators
                        prediction_signal = self.prediction_engine.get_prediction(
                            candidate.address,
                            indicators=indicators,
                            candidate_metrics=candidate.to_dict(),
                        )
                        if prediction_signal.get("signal") in ("BUY", "SELL", "STRONG_BUY", "STRONG_SELL"):
                            print("[PREDICTION] " + candidate.symbol + " -> " +
                                  prediction_signal["signal"] +
                                  " (score=" + str(prediction_signal["score"]) +
                                  ", conf=" + str(round(prediction_signal["confidence"], 2)) + ")")
                        
                        # Hard gate: skip LLM if confident SELL signal
                        if prediction_signal.get("signal") in ("SELL", "STRONG_SELL") and prediction_signal.get("confidence", 0) >= 0.75:
                            print("[PREDICTION] SELL gate triggered for " + candidate.symbol + " — skipping")
                            self._log_event_to_db("prediction/sell_gate", {
                                "token": candidate.address, "symbol": candidate.symbol,
                                "signal": prediction_signal["signal"],
                                "confidence": prediction_signal["confidence"],
                            })
                            return
                    except Exception as pe:
                        print("[PREDICTION] Error: " + str(pe))
                
                # Step 1.9: Chart Analysis — pattern recognition
                chart_signal = None
                if self.chart_agent:
                    try:
                        chart_signal = self.chart_agent.analyze(
                            candidate.symbol,
                            indicators=strategy_result.indicators,
                            candidate_metrics=candidate.to_dict(),
                        )
                        if chart_signal.get("action") != "HOLD":
                            print("[CHART] " + candidate.symbol + " -> " +
                                  chart_signal["action"] +
                                  " (" + chart_signal.get("pattern", "") +
                                  ", conf=" + str(round(chart_signal["confidence"], 2)) + ")")
                    except Exception as ce:
                        print("[CHART] Error: " + str(ce))
                
                # Step 1.10: ICT Analysis — Smart Money concepts
                ict_signal = None
                if self.ict_agent:
                    try:
                        ict_signal = self.ict_agent.analyze(
                            indicators=strategy_result.indicators,
                            candidate_metrics=candidate.to_dict(),
                        )
                        if ict_signal.get("signal") != "HOLD":
                            print("[ICT] " + candidate.symbol + " -> " +
                                  ict_signal["signal"] +
                                  " (" + ict_signal.get("reason", "")[:50] + ")")
                    except Exception as ie:
                        print("[ICT] Error: " + str(ie))
                if strategy_result.combined_direction != "NEUTRAL":
                    print("[STRATEGY] " + candidate.symbol + " -> " +
                          strategy_result.combined_direction +
                          " (strength=" + str(round(strategy_result.combined_strength, 2)) +
                          ", conf=" + str(round(strategy_result.combined_confidence, 2)) +
                          ", signals=" + str(len(strategy_result.signals)) + ")")
                    # Log strategy event
                    self._log_event_to_db("strategy/signal", {
                        "token": candidate.address, "symbol": candidate.symbol,
                        "direction": strategy_result.combined_direction,
                        "strength": strategy_result.combined_strength,
                        "confidence": strategy_result.combined_confidence,
                        "signals": [s.strategy_name + ":" + s.direction for s in strategy_result.signals],
                    })
                    self._strategy_boosts += 1
            except Exception as e:
                print("[STRATEGY] Error analyzing " + candidate.symbol + ": " + str(e))

        # Step 1.6: Trading Confluence - 5 validation gates
        if self.confluence:
            try:
                # Gather smart money data if available
                sm_data = None
                if self.smart_money_detector:
                    try:
                        sm_signals = self.smart_money_detector.get_recent_signals(hours=1)
                        token_sigs = [s for s in sm_signals if s.get("token_address") == candidate.address]
                        if token_sigs:
                            sm_data = token_sigs[-1]
                    except Exception:
                        pass
                confluence_result = self.confluence.check(candidate, smart_money_data=sm_data)
                if confluence_result.should_trade:
                    print("[CONFLUENCE] " + candidate.symbol + " -> " + confluence_result.final_signal +
                          " (level=" + confluence_result.confluence_level +
                          ", score=" + str(round(confluence_result.final_score, 1)) + ")")
                    # Boost score if strong confluence
                    if confluence_result.confluence_level == "strong":
                        candidate.score = min(100, candidate.score + 10)
                    elif confluence_result.confluence_level == "moderate":
                        candidate.score = min(100, candidate.score + 5)
                else:
                    print("[CONFLUENCE] BLOCKED " + candidate.symbol + " - " + confluence_result.confluence_level +
                          " (score=" + str(round(confluence_result.final_score, 1)) +
                          ", reasons=" + str(len(confluence_result.rejection_reasons)) + ")")
                    for reason in confluence_result.rejection_reasons[:3]:
                        print("[CONFLUENCE]   " + reason[:100])
                    # HARD BLOCK: if confluence is "none", skip entirely
                    if confluence_result.confluence_level == "none":
                        print("[CONFLUENCE] HARD BLOCK - no confluence, skipping " + candidate.symbol)
                        return
                # Inject confluence data into candidate dict
                candidate_dict = candidate.to_dict()
                candidate_dict["confluence"] = confluence_result.to_dict()
            except Exception as e:
                print("[CONFLUENCE] Error: " + str(e))
        else:
            candidate_dict = candidate.to_dict()

        # Step 1.7: Pine Backtest - strategy validation
        if self.pine_backtest:
            try:
                tv_sym = self.confluence._resolve_tv_symbol(candidate.symbol) if self.confluence else None
                if tv_sym:
                    pine_result = self.pine_backtest.get_strategy_signals(tv_sym)
                    if pine_result.get("signal") in ("STRONG_BUY", "BUY"):
                        print("[PINE] " + candidate.symbol + " -> " + pine_result["signal"] +
                              " (strategies=" + str(pine_result.get("buy_strategies", 0)) + "/4 agree)")
                        candidate_dict["pine_signal"] = pine_result
                        candidate.score = min(100, candidate.score + 5)
                    elif pine_result.get("signal") in ("STRONG_SELL", "SELL"):
                        print("[PINE] " + candidate.symbol + " -> " + pine_result["signal"] + " (warning)")
                        candidate_dict["pine_signal"] = pine_result
            except Exception as e:
                print("[PINE] Error: " + str(e))

        # Step 2: AI Orchestrator decision (consensus + feedback loop)
        # Inject all signals into candidate data for the orchestrator.
        # NOTE: do NOT reassign candidate_dict = candidate.to_dict() here —
        # that wiped the confluence and pine_signal data injected above.
        # Only build a fresh dict if the confluence block errored before
        # creating one.
        if 'candidate_dict' not in locals():
            candidate_dict = candidate.to_dict()
        if strategy_result:
            candidate_dict["strategy_signals"] = strategy_result.to_dict()
        if prediction_signal:
            candidate_dict["prediction_signal"] = prediction_signal
        if sentiment_data:
            candidate_dict["sentiment"] = sentiment_data
        if chart_signal:
            candidate_dict["chart_analysis"] = chart_signal
        if ict_signal:
            candidate_dict["ict_analysis"] = ict_signal
        decision = self.orchestrator.analyze_candidate(candidate_dict)
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
            stop_loss_pct=cat_params.stop_loss_pct,
            take_profit_pct=cat_params.take_profit_pct,
            max_hold_hours=cat_params.max_hold_hours,
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

        trade = self.paper.buy(
            token_address=signal.token_address, symbol=signal.symbol,
            amount_usd=signal.amount_usd, score=signal.score,
            signals=candidate.signals, category=str(candidate.category),
            stop_loss_pct=cat_params.stop_loss_pct if cat_params else 10.0,
            take_profit_pct=cat_params.take_profit_pct if cat_params else 30.0,
            max_hold_hours=cat_params.max_hold_hours if cat_params else 12.0,
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
        # Update portfolio risk manager with current capital
        if self.portfolio_risk:
            try:
                stats = self.paper.get_stats()
                self.portfolio_risk.update_capital(stats["current_capital"])
                risk_event = self.portfolio_risk.check_risk()
                if risk_event:
                    self._log_event_to_db("risk/event", risk_event.to_dict())
                    # If critical, try AI override or activate circuit breaker
                    if risk_event.severity == "critical":
                        # Latch: only consult the AI override / fire the breaker
                        # on the FIRST breach, not every exit-check cycle.
                        if not self.portfolio_risk.circuit_breaker_active:
                            if self.override_engine:
                                override = self.override_engine.should_override(
                                    risk_event.event_type,
                                    self.portfolio_risk.get_portfolio_stats(),
                                    self.paper.get_positions_for_risk(),
                                )
                                if override.get("decision") == "OVERRIDE":
                                    self.portfolio_risk.set_override(True)
                                else:
                                    self.portfolio_risk.activate_circuit_breaker(risk_event.message)
                            else:
                                self.portfolio_risk.activate_circuit_breaker(risk_event.message)
                            # Auto-reset mechanism: if the breaker just tripped on
                            # max loss and auto-reset is enabled, restore capital
                            # to the initial amount, count the reset, and resume.
                            self.portfolio_risk.maybe_auto_reset(
                                getattr(self.paper, "reset_capital", None)
                            )
            except Exception as e:
                print("[RISK] Exit risk check error: " + str(e))

        # LLM Exit Decisions — ask AI whether to exit each position
        if self.exit_decider and self.exit_decider._available:
            try:
                positions = self.paper.get_positions_for_risk()
                for addr, pos_data in positions.items():
                    # Only ask LLM for positions held > 1 hour
                    if pos_data.get("hours_held", 0) < 1:
                        continue
                    # Get indicators if strategy bridge available
                    indicators = None
                    strategy_signals = None
                    if self.strategy_bridge:
                        try:
                            cached = self.strategy_bridge.fetcher.get_ohlcv(addr)
                            if cached is not None and len(cached) >= 5:
                                from src.strategy_bridge import IndicatorEngine
                                indicators = IndicatorEngine.calculate(cached)
                        except Exception:
                            pass
                    
                    decision = self.exit_decider.should_exit(
                        pos_data,
                        indicators=indicators,
                        strategy_signals=strategy_signals,
                    )
                    if decision.get("action") == "EXIT" and decision.get("confidence", 0) >= 0.7:
                        print("[EXIT-LLM] AI recommends EXIT for " + pos_data["symbol"] +
                              ": " + decision.get("reason", "") +
                              " (conf=" + str(round(decision["confidence"], 2)) + ")")
                        # Execute paper exit
                        self.paper.sell(addr, "llm_exit")
            except Exception as e:
                print("[EXIT-LLM] Error: " + str(e))

        # Standard exits (SL/TP/stale)
        if self.mode == "live":
            closed = self.sniper.check_exits()
            for pos in closed:
                self.orchestrator.record_trade_outcome(pos.symbol, pos.pnl_usd, pos.pnl_pct, 0,
                                                       strategy_name=getattr(pos, 'strategy_name', None))
                if self.portfolio_risk:
                    self.portfolio_risk.record_trade_pnl(pos.pnl_usd)
                # Alpha decay: live trade outcomes feed the decay detector (#2)
                self._record_decay_outcome(getattr(pos, 'strategy_name', None), pos.pnl_pct)
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
                self.orchestrator.record_trade_outcome(trade.symbol, trade.pnl_usd, trade.pnl_pct, 0,
                                                       strategy_name=getattr(trade, 'strategy_name', None))
                if self.portfolio_risk:
                    self.portfolio_risk.record_trade_pnl(trade.pnl_usd)
                # Alpha decay: live trade outcomes feed the decay detector (#2)
                self._record_decay_outcome(getattr(trade, 'strategy_name', None), trade.pnl_pct)
                self._emit_event(Events.POSITION_CLOSED, {
                    "symbol": trade.symbol, "amount_usd": trade.amount_usd,
                    "pnl_usd": trade.pnl_usd, "pnl_pct": trade.pnl_pct,
                    "reason": trade.status, "mode": "paper",
                    "token": trade.token_address,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    def _record_decay_outcome(self, strategy_name, pnl_pct):
        """Feed live trade outcomes into the alpha decay detector (#2).

        This is what makes decay tracking real: previously only the
        deploy-time 0.0 baseline was ever recorded, so decay status could
        never change. Best-effort — never breaks the exit path.
        """
        if not strategy_name or pnl_pct is None:
            return
        try:
            detector = self.strategy_bridge._get_decay_detector() if self.strategy_bridge else None
            if detector:
                detector.record_trade(strategy_name, pnl_pct=float(pnl_pct))
        except Exception:
            pass

    async def run(self):
        """Main engine loop."""
        self._running = True
        print("[ENGINE] Starting micro-cap trading engine...")
        print("[ENGINE] Mode: " + self.mode)
        print("[ENGINE] Capital: $" + str(self.capital))

        # Start web dashboard in background
        try:
            import threading
            import uvicorn
            from src.web_dashboard import app as dashboard_app
            def run_dashboard():
                uvicorn.run(dashboard_app, host="0.0.0.0", port=8080, log_level="error")
            dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
            dashboard_thread.start()
            print("[ENGINE] Web Dashboard started on port 8080")
        except Exception as e:
            print("[ENGINE] Dashboard unavailable: " + str(e))

        # Send startup notification to Telegram
        try:
            self.telegram.send_startup_message(self.capital, self.mode)
        except Exception as e:
            print("[TG] Startup notify failed: " + str(e))

        # Start the scanner
        self.scanner.start()

        # Restore open paper trades from DB (survives deploys)
        if self.mode == "paper":
            self.paper.restore_open_trades()

        # Register category agents as DSH background jobs
        from src.category_agents import get_all_agents
        self.category_agents = get_all_agents(event_bus=self.event_bus, scheduler=self.scheduler)
        for agent in self.category_agents:
            self.scheduler.register(
                name="agent_" + agent.name,
                fn=agent.discover,
                interval_seconds=120,  # Each agent discovers every 2 min
            )
            print("[AGENT] Registered " + agent.name + " as background job", flush=True)

        # Register OHLCV collector as background job (polls every 30s)
        if self.ohlcv_collector:
            self.scheduler.register(
                name="ohlcv_poll",
                fn=self.ohlcv_collector.poll_once,
                interval_seconds=30,  # Poll every 30s to build candle history
            )
            print("[ENGINE] OHLCV Collector registered as background job (30s interval)", flush=True)

        # Register CoinGecko trending discovery (polls every 5 min)
        if self.coingecko:
            self.scheduler.register(
                name="coingecko_trending",
                fn=self._poll_coingecko_trending,
                interval_seconds=300,  # Every 5 minutes
            )
            print("[ENGINE] CoinGecko trending discovery registered (5min interval)", flush=True)

        # Register Order Book Collector (polls every 30s)
        if self.orderbook_collector:
            # Track tokens that we're trading
            self.scheduler.register(
                name="orderbook_poll",
                fn=self.orderbook_collector.poll_once,
                interval_seconds=30,
            )
            print("[ENGINE] Order Book Collector registered (30s interval)", flush=True)

        # Register Storage Tier Manager (daily cleanup at midnight)
        if self.storage_tier:
            self.scheduler.register(
                name="storage_cleanup",
                fn=self.storage_tier.run_cleanup,
                interval_seconds=86400,  # Daily
            )
            print("[ENGINE] Storage Tier Manager registered (daily cleanup)", flush=True)

        # Register Backup Manager (daily backup at 1am)
        if self.backup_manager:
            self.scheduler.register(
                name="storage_backup",
                fn=self.backup_manager.run_backup,
                interval_seconds=86400,  # Daily
            )
            print("[ENGINE] Backup Manager registered (daily backup)", flush=True)

        # Register Data Compressor (daily compression at 2am)
        if self.data_compressor:
            self.scheduler.register(
                name="storage_compress",
                fn=self.data_compressor.run_compression,
                interval_seconds=86400,  # Daily
            )
            print("[ENGINE] Data Compressor registered (daily compression)", flush=True)

        # Register Storage Alerts (hourly check)
        if self.storage_alerts:
            self.scheduler.register(
                name="storage_alerts",
                fn=self.storage_alerts.check_storage,
                interval_seconds=3600,  # Hourly
            )
            print("[ENGINE] Storage Alerts registered (hourly check)", flush=True)

        # Start async scheduler for background jobs
        await self.scheduler.start()

        cycle = 0
        while self._running:
            try:
                cycle += 1
                # Check exits periodically
                if cycle % (EXIT_CHECK_INTERVAL // SCAN_INTERVAL + 1) == 0:
                    self._check_exits()

                # Save portfolio and counters to DB periodically
                if cycle % 10 == 0:
                    self._save_portfolio_to_db()
                    self._persist_counters()

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
        self._persist_counters()
        self.scanner.stop()
        print("[ENGINE] Stopped")
