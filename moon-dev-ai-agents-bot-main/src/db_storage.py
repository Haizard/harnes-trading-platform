"""
PostgreSQL Storage Layer for Moon Dev Trading Platform
Persistent storage replacing JSON files. Connects to Luceris PostgreSQL.

Environment variables:
  LUCERIS_DATABASE_URL=postgres://app:password@cli.luceris.cloud:5432/main?sslmode=require

Tables:
  trades        - All trade entries and exits
  portfolio     - Current portfolio state
  sentiment     - Cached sentiment data
  engine_events - Event log for audit trail
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON engine_events(event_type)")
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
                trade_dict.get("entry_time"),
                trade_dict.get("exit_time"),
                trade_dict.get("pnl_usd", 0),
                trade_dict.get("pnl_pct", 0),
                trade_dict.get("status", "open"),
                trade_dict.get("score", 0),
                mode,
                json.dumps(trade_dict.get("signals", [])),
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
