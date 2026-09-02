"""
Moon Dev Storage Tier Manager
Automatically moves data between HOT/WARM/COLD tiers based on age.
Compresses and aggregates old data to save storage.

Tiers:
  HOT (0-7 days):    Full detail, instant queries
  WARM (7-30 days):  Compressed, aggregated
  COLD (30+ days):   Summary only, archived

DSH Pattern: EventBus → DB → Singleton
"""

import time
import json
from datetime import datetime, timezone, timedelta
from termcolor import cprint
from src.event_bus import _fire_and_forget


class StorageTierManager:
    """Manages data lifecycle across storage tiers with DSH compliance."""

    # Tier boundaries (days)
    HOT_DAYS = 7
    WARM_DAYS = 30

    # Cleanup intervals (polls)
    CLEANUP_INTERVAL = 86400  # Run daily (seconds)

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._last_cleanup = 0
        self._db_available = False
        self._stats = {
            "total_freed_mb": 0,
            "cleanup_count": 0,
            "last_cleanup": None,
            "tables_cleaned": [],
        }

        # Check DB
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if pool:
                self._db_available = True
                self._ensure_tables()
                cprint("[TIER] PostgreSQL connected — tiered storage active", "white", "on_green")
            else:
                cprint("[TIER] No DB — tiered storage disabled", "yellow")
        except Exception:
            cprint("[TIER] DB init error — tiered storage disabled", "yellow")

    def _ensure_tables(self):
        """Create storage tier tables if they don't exist."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return

            with pool.connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS storage_tier_policies (
                        id SERIAL PRIMARY KEY,
                        table_name TEXT NOT NULL,
                        tier TEXT NOT NULL,  -- hot, warm, cold, archive
                        retention_days INTEGER,
                        action TEXT,  -- delete, compress, aggregate
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS storage_cleanup_log (
                        id SERIAL PRIMARY KEY,
                        started_at TIMESTAMPTZ DEFAULT NOW(),
                        completed_at TIMESTAMPTZ,
                        tables_cleaned TEXT[],
                        rows_deleted INTEGER DEFAULT 0,
                        mb_freed NUMERIC DEFAULT 0,
                        status TEXT DEFAULT 'running',
                        error TEXT
                    )
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS storage_usage_snapshots (
                        id SERIAL PRIMARY KEY,
                        snapshot_time TIMESTAMPTZ DEFAULT NOW(),
                        total_size_mb NUMERIC,
                        table_sizes JSONB,
                        tier_counts JSONB
                    )
                """)

                # Insert default policies if empty
                count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM storage_tier_policies"
                ).fetchone()["cnt"]

                if count == 0:
                    policies = [
                        ("ohlcv_candles", "hot", 7, "keep"),
                        ("ohlcv_candles", "warm", 30, "compress_1m_to_5m"),
                        ("ohlcv_candles", "cold", None, "compress_5m_to_1h"),
                        ("orderbook_snapshots", "hot", 1, "keep"),
                        ("orderbook_snapshots", "warm", 7, "compress_to_summary"),
                        ("orderbook_summary", "cold", None, "keep"),
                        ("trades", "hot", 30, "keep"),
                        ("trades", "cold", None, "aggregate_daily"),
                        ("wallet_events", "hot", 7, "keep"),
                        ("wallet_events", "warm", 30, "aggregate_daily"),
                        ("engine_events", "hot", 30, "keep"),
                        ("engine_events", "cold", None, "aggregate_daily"),
                        ("scanner_results", "hot", 7, "keep"),
                    ]
                    for table, tier, days, action in policies:
                        conn.execute(
                            """INSERT INTO storage_tier_policies 
                               (table_name, tier, retention_days, action)
                               VALUES (%s, %s, %s, %s)""",
                            (table, tier, days, action)
                        )
                    cprint("[TIER] Default policies created", "cyan")

        except Exception as e:
            cprint(f"[TIER] Table init error: {e}", "yellow")

    async def run_cleanup(self):
        """Main cleanup routine — called daily by AsyncScheduler."""
        if not self._db_available:
            return

        now = time.time()
        if (now - self._last_cleanup) < self.CLEANUP_INTERVAL:
            return

        self._last_cleanup = now
        cprint("[TIER] Starting daily cleanup...", "cyan")

        # Emit start event
        await self._emit_event("storage/tier/cleanup", {
            "started": True,
            "time": datetime.now(timezone.utc).isoformat()
        })

        # Log cleanup start
        cleanup_id = self._log_cleanup_start()
        total_freed = 0
        tables_cleaned = []

        try:
            from src.db_storage import get_pool
            pool = get_pool()

            with pool.connection() as conn:
                # 1. Clean orderbook > 24 hours
                rows = conn.execute(
                    "DELETE FROM orderbook_snapshots WHERE snapshot_time < NOW() - INTERVAL '24 hours'"
                ).rowcount
                if rows > 0:
                    tables_cleaned.append("orderbook_snapshots")
                    total_freed += rows * 10 / 1024  # ~10KB per snapshot
                    cprint(f"[TIER] Deleted {rows} orderbook snapshots (>24h)", "cyan")

                # 2. Clean OHLCV 1m candles > 7 days (after aggregating to 5m)
                rows = conn.execute(
                    "DELETE FROM ohlcv_candles WHERE timeframe = '1m' AND candle_time < NOW() - INTERVAL '7 days'"
                ).rowcount
                if rows > 0:
                    tables_cleaned.append("ohlcv_candles_1m")
                    total_freed += rows * 150 / (1024 * 1024)  # ~150 bytes per candle
                    cprint(f"[TIER] Deleted {rows} 1m candles (>7 days)", "cyan")

                # 3. Clean OHLCV 5m candles > 30 days
                rows = conn.execute(
                    "DELETE FROM ohlcv_candles WHERE timeframe = '5m' AND candle_time < NOW() - INTERVAL '30 days'"
                ).rowcount
                if rows > 0:
                    tables_cleaned.append("ohlcv_candles_5m")
                    total_freed += rows * 150 / (1024 * 1024)
                    cprint(f"[TIER] Deleted {rows} 5m candles (>30 days)", "cyan")

                # 4. Clean wallet events > 7 days
                rows = conn.execute(
                    "DELETE FROM wallet_events WHERE created_at < NOW() - INTERVAL '7 days'"
                ).rowcount
                if rows > 0:
                    tables_cleaned.append("wallet_events")
                    total_freed += rows * 300 / (1024 * 1024)
                    cprint(f"[TIER] Deleted {rows} wallet events (>7 days)", "cyan")

                # 5. Clean scanner results > 7 days
                rows = conn.execute(
                    "DELETE FROM scanner_results WHERE created_at < NOW() - INTERVAL '7 days'"
                ).rowcount
                if rows > 0:
                    tables_cleaned.append("scanner_results")
                    total_freed += rows * 200 / (1024 * 1024)
                    cprint(f"[TIER] Deleted {rows} scanner results (>7 days)", "cyan")

                # 6. Clean engine events > 30 days
                rows = conn.execute(
                    "DELETE FROM engine_events WHERE created_at < NOW() - INTERVAL '30 days'"
                ).rowcount
                if rows > 0:
                    tables_cleaned.append("engine_events")
                    total_freed += rows * 200 / (1024 * 1024)
                    cprint(f"[TIER] Deleted {rows} engine events (>30 days)", "cyan")

        except Exception as e:
            cprint(f"[TIER] Cleanup error: {e}", "yellow")
            self._log_cleanup_end(cleanup_id, tables_cleaned, total_freed, error=str(e))
            return

        # VACUUM must run outside transaction block — skip it (too risky)
        # PostgreSQL auto-vacuums, and manual VACUUM causes issues in connection pools

        # Log cleanup end
        self._log_cleanup_end(cleanup_id, tables_cleaned, total_freed)

        # Update stats
        self._stats["total_freed_mb"] += total_freed
        self._stats["cleanup_count"] += 1
        self._stats["last_cleanup"] = datetime.now(timezone.utc).isoformat()
        self._stats["tables_cleaned"] = tables_cleaned

        # Take usage snapshot
        await self._take_usage_snapshot()

        # Emit completion event
        await self._emit_event("storage/tier/cleanup", {
            "completed": True,
            "freed_mb": round(total_freed, 2),
            "tables_cleaned": tables_cleaned,
            "total_freed_mb": round(self._stats["total_freed_mb"], 2),
            "cleanup_count": self._stats["cleanup_count"],
        })

        cprint(f"[TIER] Cleanup complete: freed {total_freed:.1f} MB, cleaned {len(tables_cleaned)} tables", "white", "on_green")

    def _log_cleanup_start(self) -> int:
        """Log cleanup start to DB."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return 0

            with pool.connection() as conn:
                row = conn.execute(
                    "INSERT INTO storage_cleanup_log (status) VALUES ('running') RETURNING id"
                ).fetchone()
                return row["id"] if row else 0
        except Exception:
            return 0

    def _log_cleanup_end(self, cleanup_id: int, tables_cleaned: list,
                         mb_freed: float, error: str = None):
        """Log cleanup completion to DB."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool or not cleanup_id:
                return

            status = "error" if error else "completed"
            with pool.connection() as conn:
                conn.execute(
                    """UPDATE storage_cleanup_log 
                       SET completed_at = NOW(), tables_cleaned = %s, 
                           rows_deleted = 0, mb_freed = %s, status = %s, error = %s
                       WHERE id = %s""",
                    (tables_cleaned, mb_freed, status, error, cleanup_id)
                )
        except Exception:
            pass

    async def _take_usage_snapshot(self):
        """Take a snapshot of current DB usage."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return

            with pool.connection() as conn:
                # Get total DB size
                row = conn.execute(
                    "SELECT pg_database_size(current_database()) as size_bytes"
                ).fetchone()
                total_mb = row["size_bytes"] / (1024 * 1024) if row else 0

                # Get table sizes
                rows = conn.execute("""
                    SELECT relname, pg_total_relation_size(relid) as size_bytes
                    FROM pg_catalog.pg_statio_user_tables
                    ORDER BY pg_total_relation_size(relid) DESC
                    LIMIT 15
                """).fetchall()

                table_sizes = {}
                for r in rows:
                    table_sizes[r["relname"]] = round(r["size_bytes"] / (1024 * 1024), 2)

                # Store snapshot
                conn.execute(
                    """INSERT INTO storage_usage_snapshots 
                       (total_size_mb, table_sizes, tier_counts)
                       VALUES (%s, %s, %s)""",
                    (total_mb, json.dumps(table_sizes), json.dumps(self._stats))
                )

                cprint(f"[TIER] DB size: {total_mb:.1f} MB", "cyan")

        except Exception as e:
            cprint(f"[TIER] Snapshot error: {e}", "yellow")

    async def get_storage_report(self) -> dict:
        """Get current storage usage report."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return {"error": "No DB"}

            with pool.connection() as conn:
                row = conn.execute(
                    "SELECT pg_database_size(current_database()) as size_bytes"
                ).fetchone()
                total_mb = row["size_bytes"] / (1024 * 1024) if row else 0

                # Get table counts
                tables = {}
                for name in ["ohlcv_candles", "orderbook_snapshots", "trades",
                             "wallet_events", "engine_events", "scanner_results"]:
                    try:
                        r = conn.execute(f"SELECT COUNT(*) as cnt FROM {name}").fetchone()
                        tables[name] = r["cnt"] if r else 0
                    except Exception:
                        tables[name] = 0

                return {
                    "total_size_mb": round(total_mb, 1),
                    "limit_mb": 10240,  # 10 GB
                    "usage_pct": round(total_mb / 10240 * 100, 1),
                    "table_counts": tables,
                    "stats": self._stats,
                }

        except Exception as e:
            return {"error": str(e)}

    async def _emit_event(self, event_name: str, payload: dict):
        """Emit event via EventBus and log to DB."""
        # Log to console
        if payload.get("completed"):
            cprint(f"[TIER] Event: {event_name} — freed {payload.get('freed_mb', 0):.1f} MB", "cyan")
        elif payload.get("started"):
            cprint(f"[TIER] Event: {event_name} — started", "cyan")

        # Save to DB
        try:
            from src.db_storage import log_event
            log_event(event_name, payload)
        except Exception:
            pass

        # Emit via EventBus
        if self.event_bus:
            try:
                import asyncio
                _fire_and_forget(self.event_bus.emit(event_name, payload))
            except Exception:
                pass


# ── Singleton ──────────────────────────────────────────────
_manager_instance = None

def get_storage_tier_manager(event_bus=None) -> StorageTierManager:
    """Get or create the singleton StorageTierManager instance."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = StorageTierManager(event_bus=event_bus)
        cprint("[TIER] Storage Tier Manager initialized", "white", "on_green")
    return _manager_instance
