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
from contextlib import contextmanager

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    PSYCOPG_AVAILABLE = True
except ImportError:
    PSYCOPG_AVAILABLE = False


_pool = None


def get_pool():
    """Get or create the connection pool."""
    global _pool
    if _pool is not None:
        return _pool

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
        _init_tables()
        return _pool
    except Exception as e:
        print(f"[DB] Connection failed: {e} — using JSON fallback")
        _pool = None
        return None


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
        # Indexes
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
        conn.commit()
        print("[DB] Tables initialized")


# ── Trade Operations ─────────────────────────────────────────

def save_trade(trade_dict: dict, mode: str = "paper") -> Optional[int]:
    """Save a trade to PostgreSQL. Returns the trade ID."""
    pool = get_pool()
    if not pool:
        return None
    try:
        with pool.connection() as conn:
            row = conn.execute("""
                INSERT INTO trades (token_address, symbol, side, amount_usd,
                    entry_price, exit_price, token_amount, slippage_pct,
                    price_impact_pct, entry_time, exit_time, pnl_usd, pnl_pct,
                    status, score, mode, signals, ai_confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    """Log an engine event."""
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
                       buys: int = 0, sells: int = 0, source: str = "dexscreener"):
    """Save or update an OHLCV candle. Uses UPSERT to avoid duplicates."""
    pool = get_pool()
    if not pool:
        return
    try:
        with pool.connection() as conn:
            conn.execute("""
                INSERT INTO ohlcv_candles (token_address, candle_time, open, high, low, close,
                    volume, buys, sells, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (token_address, candle_time) DO UPDATE SET
                    high = GREATEST(ohlcv_candles.high, EXCLUDED.high),
                    low = LEAST(ohlcv_candles.low, EXCLUDED.low),
                    close = EXCLUDED.close,
                    volume = ohlcv_candles.volume + EXCLUDED.volume,
                    buys = ohlcv_candles.buys + EXCLUDED.buys,
                    sells = ohlcv_candles.sells + EXCLUDED.sells
            """, (token_address, candle_time, open_p, high, low, close,
                  volume, buys, sells, source))
            conn.commit()
    except Exception as e:
        print(f"[DB] save_ohlcv_candle error: {e}")


def save_ohlcv_candles_bulk(token_address: str, candles: list):
    """Save multiple OHLCV candles in a single batch. candles = list of dicts."""
    pool = get_pool()
    if not pool or not candles:
        return
    try:
        with pool.connection() as conn:
            for c in candles:
                conn.execute("""
                    INSERT INTO ohlcv_candles (token_address, candle_time, open, high, low, close,
                        volume, buys, sells, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (token_address, candle_time) DO UPDATE SET
                        high = GREATEST(ohlcv_candles.high, EXCLUDED.high),
                        low = LEAST(ohlcv_candles.low, EXCLUDED.low),
                        close = EXCLUDED.close,
                        volume = ohlcv_candles.volume + EXCLUDED.volume,
                        buys = ohlcv_candles.buys + EXCLUDED.buys,
                        sells = ohlcv_candles.sells + EXCLUDED.sells
                """, (
                    token_address, c["time"], c["open"], c["high"], c["low"], c["close"],
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
