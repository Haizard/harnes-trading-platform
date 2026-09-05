"""
PostgreSQL Storage Layer for Moon Dev Trading Platform
Persistent storage replacing JSON files. Connects to Luceris PostgreSQL.

Environment variables:
  LUCERIS_DATABASE_URL=postgres://app:password@cli.luceris.cloud:5432/main?sslmode=require

Tables:
  trades            - All trade entries and exits
  portfolio         - Current portfolio state
  sentiment         - Cached sentiment data
  engine_events     - Event log for audit trail
  session_events    - DSH SessionLog events (append-only audit trail)
  feedback_signals  - Signals recorded by TradeFeedbackLoop
  feedback_outcomes - Trade outcomes recorded by TradeFeedbackLoop
  executions        - Execution quality tracking
  wallet_events     - Wallet swap events from WalletTracker
  scanner_results   - Token scanner candidate results
"""

import os
import json
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict
from pathlib import Path
from contextlib import contextmanager

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    PSYCOPG_AVAILABLE = True
except ImportError:
    PSYCOPG_AVAILABLE = False


_pool = None


_pool_failed = False  # Track if pool creation failed to avoid retrying

def get_pool():
    """Get or create the connection pool."""
    global _pool, _pool_failed
    if _pool is not None:
        return _pool

    # Don't retry if pool creation already failed
    if _pool_failed:
        return None

    if not PSYCOPG_AVAILABLE:
        return None
    db_url = os.environ.get("LUCERIS_DATABASE_URL", "")
    if not db_url:
        return None

    try:
        _pool = ConnectionPool(
            conninfo=db_url,
            min_size=2,
            max_size=10,
            kwargs={"row_factory": dict_row, "connect_timeout": 10},
        )
        print("[DB] Connected to PostgreSQL")
    except Exception as e:
        print(f"[DB] Connection failed: {e} — using JSON fallback")
        _pool_failed = True
        return None

    # Init tables separately — don't let table errors kill the pool
    try:
        _init_tables()
    except Exception as e:
        print(f"[DB] Table init error: {e}")

    return _pool


def _init_tables():
    """Create tables if they don't exist."""
    pool = get_pool()
    if not pool:
        return
    with pool.connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id SERIAL PRIMARY KEY,
                token_address TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL DEFAULT 'buy',
                amount_usd REAL NOT NULL,
                entry_price REAL DEFAULT 0,
                exit_price REAL DEFAULT 0,
                token_amount REAL DEFAULT 0,
                slippage_pct REAL DEFAULT 0,
                price_impact_pct REAL DEFAULT 0,
                entry_time TIMESTAMPTZ,
                exit_time TIMESTAMPTZ,
                pnl_usd REAL DEFAULT 0,
                pnl_pct REAL DEFAULT 0,
                status TEXT DEFAULT 'open',
                score REAL DEFAULT 0,
                mode TEXT DEFAULT 'paper',
                signals JSONB DEFAULT '[]',
                ai_confidence REAL DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio (
                id SERIAL PRIMARY KEY,
                initial_capital REAL NOT NULL,
                current_capital REAL NOT NULL,
                total_pnl REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sentiment (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                score REAL DEFAULT 0,
                label TEXT DEFAULT 'neutral',
                tweet_count INTEGER DEFAULT 0,
                positive_pct REAL DEFAULT 0,
                negative_pct REAL DEFAULT 0,
                cached_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(symbol)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS engine_events (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                data JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # DSH SessionLog table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                description TEXT,
                data JSONB,
                timestamp TIMESTAMPTZ NOT NULL,
                session_id TEXT,
                signal_id TEXT
            )
        """)
        # Feedback Loop tables
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback_signals (
                id SERIAL PRIMARY KEY,
                signal_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal TEXT NOT NULL,
                confidence REAL DEFAULT 0,
                factors JSONB DEFAULT '{}',
                regime TEXT DEFAULT 'unknown',
                timestamp TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback_outcomes (
                id SERIAL PRIMARY KEY,
                signal_id TEXT,
                symbol TEXT NOT NULL,
                pnl_usd REAL DEFAULT 0,
                pnl_pct REAL DEFAULT 0,
                holding_minutes REAL DEFAULT 0,
                timestamp TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Execution quality tracking
        conn.execute("""
            CREATE TABLE IF NOT EXISTS executions (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                amount_usd REAL DEFAULT 0,
                expected_price REAL DEFAULT 0,
                fill_price REAL DEFAULT 0,
                slippage_bps REAL DEFAULT 0,
                latency_ms REAL DEFAULT 0,
                filled BOOLEAN DEFAULT FALSE,
                reason TEXT DEFAULT '',
                source TEXT DEFAULT '',
                timestamp TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Wallet events
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wallet_events (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                wallet TEXT NOT NULL,
                token_address TEXT DEFAULT '',
                direction TEXT DEFAULT '',
                amount_sol REAL DEFAULT 0,
                data JSONB DEFAULT '{}',
                timestamp TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Scanner results
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scanner_results (
                id SERIAL PRIMARY KEY,
                token_address TEXT NOT NULL,
                symbol TEXT NOT NULL,
                score REAL DEFAULT 0,
                liquidity_usd REAL DEFAULT 0,
                volume_24h REAL DEFAULT 0,
                price_usd REAL DEFAULT 0,
                data JSONB DEFAULT '{}',
                timestamp TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Smart money consensus signals
        conn.execute("""
            CREATE TABLE IF NOT EXISTS smart_money_signals (
                id SERIAL PRIMARY KEY,
                token_address TEXT NOT NULL,
                token_symbol TEXT DEFAULT '',
                wallets_buying INTEGER DEFAULT 0,
                wallets_selling INTEGER DEFAULT 0,
                aggregate_buy_sol REAL DEFAULT 0,
                aggregate_sell_sol REAL DEFAULT 0,
                avg_wallet_score REAL DEFAULT 0,
                weighted_quality REAL DEFAULT 0,
                confidence REAL DEFAULT 0,
                time_window_seconds INTEGER DEFAULT 0,
                data JSONB DEFAULT '{}',
                timestamp TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Scanner seen tokens (persists across deploys)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scanner_seen_tokens (
                token_address TEXT PRIMARY KEY,
                first_seen TIMESTAMPTZ DEFAULT NOW(),
                last_seen TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # ── RBI pipeline tables (DB-first persistence for Research-Backtest-Implement) ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rbi_runs (
                id TEXT PRIMARY KEY,
                idea TEXT NOT NULL,
                auto_mode BOOLEAN DEFAULT FALSE,
                status TEXT DEFAULT 'queued',
                strategy_name TEXT,
                result TEXT,
                error TEXT,
                phases JSONB DEFAULT '{}',
                created_at TIMESTAMPTZ,
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rbi_strategies (
                id SERIAL PRIMARY KEY,
                strategy_name TEXT NOT NULL UNIQUE,
                idea TEXT,
                signal_id TEXT,
                decision TEXT,
                reasoning TEXT,
                walk_forward JSONB,
                decay_status TEXT,
                backtest_stats JSONB DEFAULT '{}',
                code_path TEXT,
                deployed BOOLEAN DEFAULT FALSE,
                elapsed_seconds REAL DEFAULT 0,
                session_id TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rbi_session_events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                data JSONB DEFAULT '{}',
                timestamp TIMESTAMPTZ NOT NULL,
                session_id TEXT,
                signal_id TEXT
            )
        """)
        # ── Alpha decay trade history (persistent — fixes in-memory-only decay) ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alpha_decay_trades (
                id SERIAL PRIMARY KEY,
                strategy_name TEXT NOT NULL,
                pnl_pct REAL NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("ALTER TABLE rbi_strategies ADD COLUMN IF NOT EXISTS live_active BOOLEAN DEFAULT FALSE")
        conn.execute("ALTER TABLE rbi_strategies ADD COLUMN IF NOT EXISTS code_hash TEXT")
        conn.execute("ALTER TABLE rbi_strategies ADD COLUMN IF NOT EXISTS status TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alpha_decay_trades_name ON alpha_decay_trades(strategy_name)")
        # Trades ↔ strategy linkage (live PnL attribution back to RBI pipeline)
        conn.execute("""
            ALTER TABLE trades ADD COLUMN IF NOT EXISTS strategy_name TEXT
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rbi_runs_status ON rbi_runs(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rbi_runs_created ON rbi_runs(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rbi_strategies_name ON rbi_strategies(strategy_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rbi_session_events_type ON rbi_session_events(event_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rbi_session_events_session ON rbi_session_events(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rbi_session_events_time ON rbi_session_events(timestamp)")
        # Wallet poll state (persists last poll times)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wallet_poll_state (
                wallet_address TEXT PRIMARY KEY,
                last_poll_time REAL DEFAULT 0,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Engine state (counters, capital, etc.)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS engine_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # OHLCV candle storage for strategy analysis
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv_candles (
                id SERIAL PRIMARY KEY,
                token_address TEXT NOT NULL,
                candle_time TIMESTAMPTZ NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL DEFAULT 0,
                buys INTEGER DEFAULT 0,
                sells INTEGER DEFAULT 0,
                source TEXT DEFAULT 'dexscreener',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(token_address, candle_time)
            )
        """)
    # Migration: Add timeframe column in a separate transaction
    try:
        with pool.connection() as conn2:
            conn2.execute("ALTER TABLE ohlcv_candles ADD COLUMN IF NOT EXISTS timeframe TEXT NOT NULL DEFAULT '1m'")
            conn2.execute("ALTER TABLE ohlcv_candles DROP CONSTRAINT IF EXISTS ohlcv_candles_token_address_candle_time_key")
            conn2.execute("ALTER TABLE ohlcv_candles ADD CONSTRAINT ohlcv_candles_unique UNIQUE (token_address, candle_time, timeframe)")
            print("[DB] Timeframe migration complete")
    except Exception as e:
        print(f"[DB] Timeframe migration: {e}")

    # Indexes (separate transaction so index errors don't kill the pool)
    try:
        with pool.connection() as conn:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON engine_events(event_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_events_type ON session_events(event_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_events_session ON session_events(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_events_signal ON session_events(signal_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_events_time ON session_events(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_signals_symbol ON feedback_signals(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_signals_time ON feedback_signals(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_outcomes_signal ON feedback_outcomes(signal_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_outcomes_time ON feedback_outcomes(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_executions_symbol ON executions(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_executions_time ON executions(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wallet_events_wallet ON wallet_events(wallet)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wallet_events_time ON wallet_events(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scanner_results_token ON scanner_results(token_address)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scanner_results_time ON scanner_results(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_token ON ohlcv_candles(token_address)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_time ON ohlcv_candles(candle_time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_token_time ON ohlcv_candles(token_address, candle_time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_timeframe ON ohlcv_candles(timeframe)")
            conn.commit()
            print("[DB] Tables initialized")
    except Exception as e:
        print(f"[DB] Index init error: {e}")


# ── Trade Operations ─────────────────────────────────────────

def _save_trade_jsonl(trade_dict: dict, action: str = "entry"):
    """Save trade to JSONL as fallback when DB is unavailable."""
    try:
        jsonl_path = Path("src/data/paper_trading/paper_trades.jsonl")
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        entry = dict(trade_dict)
        entry["action"] = action
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


def save_trade(trade_dict: dict, mode: str = "paper") -> Optional[int]:
    """Save a trade to PostgreSQL. Returns the trade ID."""
    # Always save to JSONL
    _save_trade_jsonl(trade_dict, trade_dict.get("action", "entry"))

    pool = get_pool()
    if not pool:
        return None
    try:
        with pool.connection() as conn:
            row = conn.execute("""
                INSERT INTO trades (token_address, symbol, side, amount_usd,
                    entry_price, exit_price, token_amount, slippage_pct,
                    price_impact_pct, entry_time, exit_time, pnl_usd, pnl_pct,
                    status, score, mode, signals, ai_confidence, strategy_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                trade_dict.get("token_address", ""),
                trade_dict.get("symbol", ""),
                trade_dict.get("side", "buy"),
                trade_dict.get("amount_usd", 0),
                trade_dict.get("entry_price", 0),
                trade_dict.get("exit_price", 0),
                trade_dict.get("token_amount", 0),
                trade_dict.get("slippage_pct", 0),
                trade_dict.get("price_impact_pct", 0),
                trade_dict.get("entry_time") or datetime.now(timezone.utc).isoformat(),
                trade_dict.get("exit_time") or None,
                trade_dict.get("pnl_usd", 0),
                trade_dict.get("pnl_pct", 0),
                trade_dict.get("status", "open"),
                trade_dict.get("score", 0),
                mode,
                json.dumps(trade_dict.get("signals", []), default=str),
                trade_dict.get("ai_confidence", 0),
                trade_dict.get("strategy_name"),
            )).fetchone()
            conn.commit()
            return row["id"] if row else None
    except Exception as e:
        print(f"[DB] save_trade error: {e}")
        return None


def update_trade_exit(token_address: str, exit_price: float, pnl_usd: float,
                      pnl_pct: float, status: str, exit_time: str = None):
    """Update a trade with exit data."""
    pool = get_pool()
    if not pool:
        return
    try:
        with pool.connection() as conn:
            conn.execute("""
                UPDATE trades SET exit_price = %s, pnl_usd = %s, pnl_pct = %s,
                    status = %s, exit_time = %s
                WHERE token_address = %s AND status = 'open'
            """, (exit_price, pnl_usd, pnl_pct, status,
                  exit_time or datetime.now(timezone.utc).isoformat(),
                  token_address))
            conn.commit()
    except Exception as e:
        print(f"[DB] update_trade_exit error: {e}")


def get_trades(symbol: str = None, status: str = None, limit: int = 50) -> List[dict]:
    """Query trades from PostgreSQL."""
    pool = get_pool()
    if not pool:
        return []
    try:
        with pool.connection() as conn:
            query = "SELECT * FROM trades WHERE 1=1"
            params = []
            if symbol:
                query += " AND symbol = %s"
                params.append(symbol)
            if status:
                query += " AND status = %s"
                params.append(status)
            query += " ORDER BY created_at DESC LIMIT %s"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] get_trades error: {e}")
        return []


def get_trade_stats() -> dict:
    """Get aggregate trade statistics."""
    pool = get_pool()
    if not pool:
        return {}
    try:
        with pool.connection() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total_trades,
                    COUNT(*) FILTER (WHERE pnl_usd > 0) as wins,
                    COUNT(*) FILTER (WHERE pnl_usd <= 0 AND status != 'open') as losses,
                    COALESCE(SUM(pnl_usd), 0) as total_pnl,
                    COALESCE(AVG(pnl_usd), 0) as avg_pnl,
                    COALESCE(MAX(pnl_usd), 0) as best_trade,
                    COALESCE(MIN(pnl_usd), 0) as worst_trade
                FROM trades WHERE status != 'open'
            """).fetchone()
            return dict(row) if row else {}
    except Exception as e:
        print(f"[DB] get_trade_stats error: {e}")
        return {}


def get_trades_by_strategies(strategy_names: List[str], limit: int = 500) -> List[dict]:
    """Query trades attributed to specific RBI strategies (for chart marker tagging #2)."""
    if not strategy_names:
        return []
    pool = get_pool()
    if not pool:
        return []
    try:
        with pool.connection() as conn:
            placeholders = ", ".join(["%s"] * len(strategy_names))
            rows = conn.execute(
                f"SELECT id, token_address, symbol, strategy_name, entry_time, exit_time, "
                f"pnl_usd, pnl_pct, status, created_at FROM trades "
                f"WHERE strategy_name IN ({placeholders}) "
                f"ORDER BY created_at DESC LIMIT %s",
                strategy_names + [limit]
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] get_trades_by_strategies error: {e}")
        return []


# ── Portfolio Operations ─────────────────────────────────────

def save_portfolio(initial_capital: float, current_capital: float, total_pnl: float,
                   total_trades: int, wins: int, losses: int):
    """Save or update portfolio state."""
    pool = get_pool()
    if not pool:
        return
    try:
        with pool.connection() as conn:
            existing = conn.execute("SELECT id FROM portfolio ORDER BY id DESC LIMIT 1").fetchone()
            if existing:
                conn.execute("""
                    UPDATE portfolio SET initial_capital = %s, current_capital = %s,
                        total_pnl = %s, total_trades = %s, wins = %s, losses = %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (initial_capital, current_capital, total_pnl, total_trades,
                      wins, losses, existing["id"]))
            else:
                conn.execute("""
                    INSERT INTO portfolio (initial_capital, current_capital, total_pnl,
                        total_trades, wins, losses)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (initial_capital, current_capital, total_pnl, total_trades,
                      wins, losses))
            conn.commit()
    except Exception as e:
        print(f"[DB] save_portfolio error: {e}")


def get_portfolio() -> Optional[dict]:
    """Get current portfolio state."""
    pool = get_pool()
    if not pool:
        return None
    try:
        with pool.connection() as conn:
            row = conn.execute("SELECT * FROM portfolio ORDER BY id DESC LIMIT 1").fetchone()
            return dict(row) if row else None
    except Exception as e:
        print(f"[DB] get_portfolio error: {e}")
        return None


# ── Sentiment Operations ─────────────────────────────────────

def save_sentiment(symbol: str, score: float, label: str, tweet_count: int,
                   positive_pct: float, negative_pct: float):
    """Save or update sentiment cache."""
    pool = get_pool()
    if not pool:
        return
    try:
        with pool.connection() as conn:
            conn.execute("""
                INSERT INTO sentiment (symbol, score, label, tweet_count, positive_pct, negative_pct)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol) DO UPDATE SET
                    score = EXCLUDED.score, label = EXCLUDED.label,
                    tweet_count = EXCLUDED.tweet_count, positive_pct = EXCLUDED.positive_pct,
                    negative_pct = EXCLUDED.negative_pct, cached_at = NOW()
            """, (symbol, score, label, tweet_count, positive_pct, negative_pct))
            conn.commit()
    except Exception as e:
        print(f"[DB] save_sentiment error: {e}")


def get_sentiment(symbol: str) -> Optional[dict]:
    """Get cached sentiment for a symbol."""
    pool = get_pool()
    if not pool:
        return None
    try:
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM sentiment WHERE symbol = %s", (symbol,)
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        print(f"[DB] get_sentiment error: {e}")
        return None


# ── Event Log ────────────────────────────────────────────────

def log_event(event_type: str, data: dict):
    """Log an engine event to DB and JSONL."""
    # Always write to JSONL (survives DB failures)
    try:
        jsonl_path = Path("src/data/micro_engine/engine_events.jsonl")
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass

    # Also write to DB
    pool = get_pool()
    if not pool:
        return
    try:
        with pool.connection() as conn:
            conn.execute(
                "INSERT INTO engine_events (event_type, data) VALUES (%s, %s)",
                (event_type, json.dumps(data, default=str))
            )
            conn.commit()
    except Exception as e:
        print(f"[DB] log_event error: {e}")


def get_events(event_type: str = None, limit: int = 100) -> List[dict]:
    """Query engine events."""
    pool = get_pool()
    if not pool:
        return []
    try:
        with pool.connection() as conn:
            if event_type:
                rows = conn.execute(
                    "SELECT * FROM engine_events WHERE event_type = %s ORDER BY created_at DESC LIMIT %s",
                    (event_type, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM engine_events ORDER BY created_at DESC LIMIT %s",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] get_events error: {e}")
        return []


# ── RBI Pipeline Operations ──────────────────────────────────
# DB-first persistence for the RBI (Research-Backtest-Implement) pipeline.
# All functions are best-effort: they never raise — callers keep their
# JSONL/CSV fallbacks for when the DB is unavailable.

def save_rbi_run(run: dict) -> bool:
    """Upsert an RBI pipeline run (from rbi_web RunManager) into rbi_runs."""
    pool = get_pool()
    if not pool:
        return False
    try:
        with pool.connection() as conn:
            conn.execute("""
                INSERT INTO rbi_runs (id, idea, auto_mode, status, strategy_name,
                    result, error, phases, created_at, started_at, finished_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    strategy_name = EXCLUDED.strategy_name,
                    result = EXCLUDED.result,
                    error = EXCLUDED.error,
                    phases = EXCLUDED.phases,
                    started_at = EXCLUDED.started_at,
                    finished_at = EXCLUDED.finished_at,
                    updated_at = NOW()
            """, (
                run.get("id"),
                run.get("idea", ""),
                bool(run.get("auto_mode", False)),
                run.get("status", "queued"),
                run.get("strategy_name"),
                run.get("result"),
                run.get("error"),
                json.dumps(run.get("phases", {}), default=str),
                run.get("created_at"),
                run.get("started_at"),
                run.get("finished_at"),
            ))
            conn.commit()
        return True
    except Exception as e:
        print(f"[DB] save_rbi_run error: {e}")
        return False


def get_rbi_runs(limit: int = 50) -> List[dict]:
    """Query recent RBI runs, newest first."""
    pool = get_pool()
    if not pool:
        return []
    try:
        with pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM rbi_runs ORDER BY created_at DESC NULLS LAST LIMIT %s",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] get_rbi_runs error: {e}")
        return []


def save_rbi_strategy(record: dict) -> bool:
    """Upsert a strategy lifecycle record (from StrategyMemory) into rbi_strategies.

    Latest pipeline result per strategy_name wins (ON CONFLICT DO UPDATE),
    so re-runs keep the table current with the strategy's newest state.
    """
    pool = get_pool()
    if not pool:
        return False
    name = record.get("strategy_name")
    if not name:
        return False
    try:
        with pool.connection() as conn:
            conn.execute("""
                INSERT INTO rbi_strategies (strategy_name, idea, signal_id, decision,
                    reasoning, walk_forward, decay_status, backtest_stats, code_path,
                    deployed, elapsed_seconds, session_id, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (strategy_name) DO UPDATE SET
                    idea = EXCLUDED.idea,
                    signal_id = EXCLUDED.signal_id,
                    decision = EXCLUDED.decision,
                    reasoning = EXCLUDED.reasoning,
                    walk_forward = EXCLUDED.walk_forward,
                    decay_status = EXCLUDED.decay_status,
                    backtest_stats = EXCLUDED.backtest_stats,
                    code_path = EXCLUDED.code_path,
                    deployed = EXCLUDED.deployed,
                    elapsed_seconds = EXCLUDED.elapsed_seconds,
                    session_id = EXCLUDED.session_id,
                    updated_at = NOW()
            """, (
                name,
                record.get("idea"),
                record.get("signal_id"),
                record.get("decision") or record.get("result"),
                record.get("reasoning"),
                json.dumps(record.get("walk_forward"), default=str)
                    if record.get("walk_forward") else None,
                record.get("decay_status"),
                json.dumps(record.get("backtest_stats", record.get("stats", {})), default=str),
                record.get("code_path"),
                bool(record.get("deployed", False)),
                record.get("elapsed_seconds"),
                record.get("session_id"),
            ))
            conn.commit()
        return True
    except Exception as e:
        print(f"[DB] save_rbi_strategy error: {e}")
        return False


def get_rbi_strategies(limit: int = 100) -> List[dict]:
    """Query RBI strategy lifecycle records, newest first."""
    pool = get_pool()
    if not pool:
        return []
    try:
        with pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM rbi_strategies ORDER BY updated_at DESC LIMIT %s",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] get_rbi_strategies error: {e}")
        return []


def get_rbi_strategy(name: str) -> Optional[dict]:
    """Get a single RBI strategy record by name."""
    pool = get_pool()
    if not pool:
        return None
    try:
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM rbi_strategies WHERE strategy_name = %s",
                (name,)
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        print(f"[DB] get_rbi_strategy error: {e}")
        return None


def log_rbi_event(event_type: str, data: dict, session_id: str = None,
                  signal_id: str = None, event_id: str = None) -> bool:
    """Insert an RBI pipeline session event into rbi_session_events."""
    pool = get_pool()
    if not pool:
        return False
    eid = event_id or f"{session_id or 'anon'}_{int(time.time() * 1000)}"
    try:
        with pool.connection() as conn:
            conn.execute("""
                INSERT INTO rbi_session_events (id, event_type, data, timestamp, session_id, signal_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (
                eid,
                event_type,
                json.dumps(data, default=str),
                datetime.now(timezone.utc).isoformat(),
                session_id,
                signal_id or "",
            ))
            conn.commit()
        return True
    except Exception as e:
        print(f"[DB] log_rbi_event error: {e}")
        return False


def get_rbi_events(limit: int = 100, event_type: str = None, session_id: str = None) -> List[dict]:
    """Query RBI pipeline session events, newest first."""
    pool = get_pool()
    if not pool:
        return []
    try:
        with pool.connection() as conn:
            conditions = ["1=1"]
            params: list = []
            if event_type:
                conditions.append("event_type = %s")
                params.append(event_type)
            if session_id:
                conditions.append("session_id = %s")
                params.append(session_id)
            params.append(limit)
            rows = conn.execute(
                f"SELECT * FROM rbi_session_events WHERE {' AND '.join(conditions)} "
                f"ORDER BY timestamp DESC LIMIT %s",
                params
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] get_rbi_events error: {e}")
        return []


def update_rbi_strategy_status(strategy_name: str, status: str = None,
                               live_active: bool = None, code_hash: str = None) -> bool:
    """Update runtime status fields on an rbi_strategies record
    (awaiting_approval / approved / rejected / deployed / failed)."""
    pool = get_pool()
    if not pool:
        return False
    sets, params = [], []
    if status is not None:
        sets.append("status = %s"); params.append(status)
    if live_active is not None:
        sets.append("live_active = %s"); params.append(live_active)
    if code_hash is not None:
        sets.append("code_hash = %s"); params.append(code_hash)
    if not sets:
        return False
    sets.append("updated_at = NOW()")
    params.append(strategy_name)
    try:
        with pool.connection() as conn:
            conn.execute(
                f"UPDATE rbi_strategies SET {', '.join(sets)} WHERE strategy_name = %s",
                params)
            conn.commit()
        return True
    except Exception as e:
        print(f"[DB] update_rbi_strategy_status error: {e}")
        return False


def get_rbi_strategy_by_hash(code_hash: str) -> Optional[dict]:
    """Find a previously validated strategy with the same generated-code hash
    (dedupe: reuse prior validation instead of re-running the pipeline)."""
    pool = get_pool()
    if not pool:
        return None
    try:
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM rbi_strategies WHERE code_hash = %s "
                "ORDER BY updated_at DESC LIMIT 1", (code_hash,)).fetchone()
            return dict(row) if row else None
    except Exception as e:
        print(f"[DB] get_rbi_strategy_by_hash error: {e}")
        return None


# ── Alpha Decay Persistence ──────────────────────────────────

def record_decay_trade(strategy_name: str, pnl_pct: float, timestamp: str = None) -> bool:
    """Persist a strategy trade outcome for alpha-decay analysis (DB + JSONL fallback)."""
    # JSONL always (survives DB outages)
    try:
        jsonl_path = Path("src/data/rbi/alpha_decay_trades.jsonl")
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"strategy_name": strategy_name, "pnl_pct": pnl_pct,
                 "timestamp": timestamp or datetime.now(timezone.utc).isoformat()}
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass

    pool = get_pool()
    if not pool:
        return False
    try:
        with pool.connection() as conn:
            conn.execute(
                "INSERT INTO alpha_decay_trades (strategy_name, pnl_pct, timestamp) "
                "VALUES (%s, %s, %s)",
                (strategy_name, pnl_pct,
                 timestamp or datetime.now(timezone.utc).isoformat()))
            conn.commit()
        return True
    except Exception as e:
        print(f"[DB] record_decay_trade error: {e}")
        return False


def get_decay_trades(strategy_name: str = None, limit: int = 500) -> List[dict]:
    """Load decay trade history — DB first, JSONL fallback."""
    pool = get_pool()
    if pool:
        try:
            with pool.connection() as conn:
                if strategy_name:
                    rows = conn.execute(
                        "SELECT strategy_name, pnl_pct, timestamp FROM alpha_decay_trades "
                        "WHERE strategy_name = %s ORDER BY timestamp ASC LIMIT %s",
                        (strategy_name, limit)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT strategy_name, pnl_pct, timestamp FROM alpha_decay_trades "
                        "ORDER BY timestamp ASC LIMIT %s", (limit,)).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            print(f"[DB] get_decay_trades error: {e}")
    # JSONL fallback
    try:
        jsonl_path = Path("src/data/rbi/alpha_decay_trades.jsonl")
        if not jsonl_path.exists():
            return []
        out = []
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if strategy_name and entry.get("strategy_name") != strategy_name:
                continue
            out.append(entry)
        return out[-limit:]
    except Exception:
        return []


# ── Feedback Signal Operations ───────────────────────────────

def save_feedback_signal(signal_id: str, symbol: str, signal: str, confidence: float,
                         factors: dict, regime: str = "unknown", timestamp: str = None):
    """Save a feedback signal to PostgreSQL."""
    pool = get_pool()
    if not pool:
        return None
    try:
        with pool.connection() as conn:
            row = conn.execute("""
                INSERT INTO feedback_signals (signal_id, symbol, signal, confidence, factors, regime, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                signal_id, symbol, signal, confidence,
                json.dumps(factors, default=str), regime,
                timestamp or datetime.now(timezone.utc).isoformat(),
            )).fetchone()
            conn.commit()
            return row["id"] if row else None
    except Exception as e:
        print(f"[DB] save_feedback_signal error: {e}")
        return None


def save_feedback_outcome(signal_id: str, symbol: str, pnl_usd: float,
                          pnl_pct: float = 0.0, holding_minutes: float = 0.0,
                          timestamp: str = None):
    """Save a feedback outcome to PostgreSQL."""
    pool = get_pool()
    if not pool:
        return None
    try:
        with pool.connection() as conn:
            row = conn.execute("""
                INSERT INTO feedback_outcomes (signal_id, symbol, pnl_usd, pnl_pct, holding_minutes, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                signal_id, symbol, pnl_usd, pnl_pct, holding_minutes,
                timestamp or datetime.now(timezone.utc).isoformat(),
            )).fetchone()
            conn.commit()
            return row["id"] if row else None
    except Exception as e:
        print(f"[DB] save_feedback_outcome error: {e}")
        return None


def get_feedback_signals(days: int = 30, limit: int = 1000) -> List[dict]:
    """Query feedback signals from PostgreSQL."""
    pool = get_pool()
    if not pool:
        return []
    try:
        with pool.connection() as conn:
            rows = conn.execute("""
                SELECT * FROM feedback_signals
                WHERE timestamp >= NOW() - INTERVAL '%s days'
                ORDER BY timestamp DESC LIMIT %s
            """, (days, limit)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] get_feedback_signals error: {e}")
        return []


def get_feedback_outcomes(days: int = 30, limit: int = 1000) -> List[dict]:
    """Query feedback outcomes from PostgreSQL."""
    pool = get_pool()
    if not pool:
        return []
    try:
        with pool.connection() as conn:
            rows = conn.execute("""
                SELECT * FROM feedback_outcomes
                WHERE timestamp >= NOW() - INTERVAL '%s days'
                ORDER BY timestamp DESC LIMIT %s
            """, (days, limit)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] get_feedback_outcomes error: {e}")
        return []


# ── Execution Operations ─────────────────────────────────────

def save_execution(symbol: str, side: str, amount_usd: float,
                   expected_price: float = 0.0, fill_price: float = 0.0,
                   slippage_bps: float = 0.0, latency_ms: float = 0.0,
                   filled: bool = False, reason: str = "", source: str = "",
                   timestamp: str = None):
    """Save an execution record to PostgreSQL."""
    pool = get_pool()
    if not pool:
        return None
    try:
        with pool.connection() as conn:
            row = conn.execute("""
                INSERT INTO executions (symbol, side, amount_usd, expected_price,
                    fill_price, slippage_bps, latency_ms, filled, reason, source, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                symbol, side, amount_usd, expected_price,
                fill_price, slippage_bps, latency_ms, filled, reason, source,
                timestamp or datetime.now(timezone.utc).isoformat(),
            )).fetchone()
            conn.commit()
            return row["id"] if row else None
    except Exception as e:
        print(f"[DB] save_execution error: {e}")
        return None


def get_executions(days: int = 30, limit: int = 1000) -> List[dict]:
    """Query execution records from PostgreSQL."""
    pool = get_pool()
    if not pool:
        return []
    try:
        with pool.connection() as conn:
            rows = conn.execute("""
                SELECT * FROM executions
                WHERE timestamp >= NOW() - INTERVAL '%s days'
                ORDER BY timestamp DESC LIMIT %s
            """, (days, limit)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] get_executions error: {e}")
        return []


# ── Wallet Event Operations ──────────────────────────────────

def save_wallet_event(event_type: str, wallet: str, token_address: str = "",
                      direction: str = "", amount_sol: float = 0.0,
                      data: dict = None, timestamp: str = None):
    """Save a wallet event to PostgreSQL."""
    pool = get_pool()
    if not pool:
        return None
    try:
        with pool.connection() as conn:
            row = conn.execute("""
                INSERT INTO wallet_events (event_type, wallet, token_address,
                    direction, amount_sol, data, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                event_type, wallet, token_address, direction, amount_sol,
                json.dumps(data or {}, default=str),
                timestamp or datetime.now(timezone.utc).isoformat(),
            )).fetchone()
            conn.commit()
            return row["id"] if row else None
    except Exception as e:
        print(f"[DB] save_wallet_event error: {e}")
        return None


def get_wallet_events(wallet: str = None, hours: int = 24, limit: int = 1000) -> List[dict]:
    """Query wallet events from PostgreSQL."""
    pool = get_pool()
    if not pool:
        return []
    try:
        with pool.connection() as conn:
            conditions = ["timestamp >= NOW() - INTERVAL '%s hours'"]
            params = [hours]
            if wallet:
                conditions.append("wallet = %s")
                params.append(wallet)
            where = " AND ".join(conditions)
            params.append(limit)
            rows = conn.execute(f"""
                SELECT * FROM wallet_events
                WHERE {where}
                ORDER BY timestamp DESC LIMIT %s
            """, params).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] get_wallet_events error: {e}")
        return []


# ── Scanner Result Operations ────────────────────────────────

def save_scanner_result(token_address: str, symbol: str, score: float,
                        liquidity_usd: float = 0.0, volume_24h: float = 0.0,
                        price_usd: float = 0.0, data: dict = None,
                        timestamp: str = None):
    """Save a scanner result to PostgreSQL."""
    pool = get_pool()
    if not pool:
        return None
    try:
        with pool.connection() as conn:
            row = conn.execute("""
                INSERT INTO scanner_results (token_address, symbol, score,
                    liquidity_usd, volume_24h, price_usd, data, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                token_address, symbol, score, liquidity_usd, volume_24h,
                price_usd, json.dumps(data or {}, default=str),
                timestamp or datetime.now(timezone.utc).isoformat(),
            )).fetchone()
            conn.commit()
            return row["id"] if row else None
    except Exception as e:
        print(f"[DB] save_scanner_result error: {e}")
        return None


def get_scanner_results(hours: int = 24, min_score: float = 0, limit: int = 1000) -> List[dict]:
    """Query scanner results from PostgreSQL."""
    pool = get_pool()
    if not pool:
        return []
    try:
        with pool.connection() as conn:
            rows = conn.execute("""
                SELECT * FROM scanner_results
                WHERE timestamp >= NOW() - INTERVAL '%s hours' AND score >= %s
                ORDER BY score DESC LIMIT %s
            """, (hours, min_score, limit)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] get_scanner_results error: {e}")
        return []


# ── Smart Money Signal Operations ────────────────────────────

def save_smart_money_signal(token_address: str, token_symbol: str = "",
                            wallets_buying: int = 0, wallets_selling: int = 0,
                            aggregate_buy_sol: float = 0.0, aggregate_sell_sol: float = 0.0,
                            avg_wallet_score: float = 0.0, weighted_quality: float = 0.0,
                            confidence: float = 0.0, time_window_seconds: int = 0,
                            data: dict = None, timestamp: str = None):
    """Save a smart money consensus signal to PostgreSQL."""
    pool = get_pool()
    if not pool:
        return None
    try:
        with pool.connection() as conn:
            row = conn.execute("""
                INSERT INTO smart_money_signals (
                    token_address, token_symbol, wallets_buying, wallets_selling,
                    aggregate_buy_sol, aggregate_sell_sol, avg_wallet_score,
                    weighted_quality, confidence, time_window_seconds, data, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                token_address, token_symbol, wallets_buying, wallets_selling,
                aggregate_buy_sol, aggregate_sell_sol, avg_wallet_score,
                weighted_quality, confidence, time_window_seconds,
                json.dumps(data or {}, default=str),
                timestamp or datetime.now(timezone.utc).isoformat(),
            )).fetchone()
            conn.commit()
            return row["id"] if row else None
    except Exception as e:
        print(f"[DB] save_smart_money_signal error: {e}")
        return None


# ── Scanner Seen Tokens ──────────────────────────────────────

def save_scanner_seen_token(token_address: str):
    """Record a token as seen by the scanner."""
    pool = get_pool()
    if not pool:
        return
    try:
        with pool.connection() as conn:
            conn.execute("""
                INSERT INTO scanner_seen_tokens (token_address, first_seen, last_seen)
                VALUES (%s, NOW(), NOW())
                ON CONFLICT (token_address) DO UPDATE SET last_seen = NOW()
            """, (token_address,))
            conn.commit()
    except Exception:
        pass


def load_scanner_seen_tokens() -> set:
    """Load all recently seen token addresses."""
    pool = get_pool()
    if not pool:
        return set()
    try:
        with pool.connection() as conn:
            rows = conn.execute(
                "SELECT token_address FROM scanner_seen_tokens WHERE last_seen >= NOW() - INTERVAL '2 hours'"
            ).fetchall()
            return {r["token_address"] for r in rows}
    except Exception:
        return set()


# ── Wallet Poll State ────────────────────────────────────────

def save_wallet_poll_state(wallet_address: str, last_poll_time: float):
    """Save last poll time for a wallet."""
    pool = get_pool()
    if not pool:
        return
    try:
        with pool.connection() as conn:
            conn.execute("""
                INSERT INTO wallet_poll_state (wallet_address, last_poll_time, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (wallet_address) DO UPDATE SET last_poll_time = %s, updated_at = NOW()
            """, (wallet_address, last_poll_time, last_poll_time))
            conn.commit()
    except Exception:
        pass


def load_wallet_poll_state() -> dict:
    """Load last poll times for all wallets."""
    pool = get_pool()
    if not pool:
        return {}
    try:
        with pool.connection() as conn:
            rows = conn.execute("SELECT wallet_address, last_poll_time FROM wallet_poll_state").fetchall()
            return {r["wallet_address"]: r["last_poll_time"] for r in rows}
    except Exception:
        return {}


# ── Engine State ─────────────────────────────────────────────

def save_engine_state(key: str, value: str):
    """Save a key-value pair to engine state."""
    pool = get_pool()
    if not pool:
        return
    try:
        with pool.connection() as conn:
            conn.execute("""
                INSERT INTO engine_state (key, value, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (key) DO UPDATE SET value = %s, updated_at = NOW()
            """, (key, value, value))
            conn.commit()
    except Exception:
        pass


def load_engine_state(key: str, default: str = None) -> str:
    """Load a value from engine state."""
    pool = get_pool()
    if not pool:
        return default
    try:
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT value FROM engine_state WHERE key = %s", (key,)
            ).fetchone()
            return row["value"] if row else default
    except Exception:
        return default


# ── OHLCV Candle Operations ─────────────────────────────────

def save_ohlcv_candle(token_address: str, candle_time: str, open_p: float,
                       high: float, low: float, close: float, volume: float = 0,
                       buys: int = 0, sells: int = 0, source: str = "dexscreener",
                       timeframe: str = "1m"):
    """Save or update an OHLCV candle. Uses UPSERT to avoid duplicates."""
    pool = get_pool()
    if not pool:
        return
    try:
        with pool.connection() as conn:
            conn.execute("""
                INSERT INTO ohlcv_candles (token_address, candle_time, timeframe, open, high, low, close,
                    volume, buys, sells, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (token_address, candle_time, timeframe) DO UPDATE SET
                    high = GREATEST(ohlcv_candles.high, EXCLUDED.high),
                    low = LEAST(ohlcv_candles.low, EXCLUDED.low),
                    close = EXCLUDED.close,
                    volume = ohlcv_candles.volume + EXCLUDED.volume,
                    buys = ohlcv_candles.buys + EXCLUDED.buys,
                    sells = ohlcv_candles.sells + EXCLUDED.sells
            """, (token_address, candle_time, timeframe, open_p, high, low, close,
                  volume, buys, sells, source))
            conn.commit()
    except Exception as e:
        print(f"[DB] save_ohlcv_candle error: {e}")


def save_ohlcv_candles_bulk(token_address: str, candles: list, timeframe: str = "1m"):
    """Save multiple OHLCV candles in a single batch. candles = list of dicts."""
    pool = get_pool()
    if not pool or not candles:
        return
    try:
        with pool.connection() as conn:
            for c in candles:
                conn.execute("""
                    INSERT INTO ohlcv_candles (token_address, candle_time, timeframe, open, high, low, close,
                        volume, buys, sells, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (token_address, candle_time, timeframe) DO UPDATE SET
                        high = GREATEST(ohlcv_candles.high, EXCLUDED.high),
                        low = LEAST(ohlcv_candles.low, EXCLUDED.low),
                        close = EXCLUDED.close,
                        volume = ohlcv_candles.volume + EXCLUDED.volume,
                        buys = ohlcv_candles.buys + EXCLUDED.buys,
                        sells = ohlcv_candles.sells + EXCLUDED.sells
                """, (
                    token_address, c["time"], timeframe, c["open"], c["high"], c["low"], c["close"],
                    c.get("volume", 0), c.get("buys", 0), c.get("sells", 0),
                    c.get("source", "dexscreener"),
                ))
            conn.commit()
    except Exception as e:
        print(f"[DB] save_ohlcv_candles_bulk error: {e}")


def get_ohlcv_candles(token_address: str, hours: int = 24, limit: int = 200) -> list:
    """Get OHLCV candles for a token from PostgreSQL."""
    pool = get_pool()
    if not pool:
        return []
    try:
        with pool.connection() as conn:
            rows = conn.execute("""
                SELECT candle_time as "datetime", open as "Open", high as "High",
                    low as "Low", close as "Close", volume as "Volume",
                    buys, sells, source
                FROM ohlcv_candles
                WHERE token_address = %s
                    AND candle_time >= NOW() - INTERVAL '%s hours'
                ORDER BY candle_time ASC
                LIMIT %s
            """, (token_address, hours, limit)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] get_ohlcv_candles error: {e}")
        return []


def cleanup_old_candles(hours: int = 24):
    """Delete OHLCV candles older than specified hours."""
    pool = get_pool()
    if not pool:
        return
    try:
        with pool.connection() as conn:
            result = conn.execute("""
                DELETE FROM ohlcv_candles
                WHERE candle_time < NOW() - INTERVAL '%s hours'
            """, (hours,))
            deleted = result.rowcount
            conn.commit()
            if deleted > 0:
                print(f"[DB] Cleaned up {deleted} old OHLCV candles")
    except Exception as e:
        print(f"[DB] cleanup_old_candles error: {e}")


def get_tracked_token_count() -> int:
    """Get number of unique tokens with OHLCV data."""
    pool = get_pool()
    if not pool:
        return 0
    try:
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT token_address) as count FROM ohlcv_candles"
            ).fetchone()
            return row["count"] if row else 0
    except Exception:
        return 0
