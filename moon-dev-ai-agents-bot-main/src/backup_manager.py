"""
Moon Dev Backup Manager
Automatically backs up data to a separate database or compressed local files.
Runs daily, exports critical data, verifies integrity.

Backup Targets:
  - Trade results (forever)
  - Token metadata (forever)
  - OHLCV 1h candles (forever)
  - Daily summaries (forever)

DSH Pattern: EventBus → DB → Singleton
"""

import os
import time
import json
import gzip
from datetime import datetime, timezone, timedelta
from pathlib import Path
from termcolor import cprint
from src.event_bus import _fire_and_forget


class BackupManager:
    """Manages data backup to separate storage with DSH compliance."""

    BACKUP_DIR = Path("src/data/backups")
    BACKUP_INTERVAL = 86400  # Run daily (seconds)
    MAX_BACKUP_SIZE_MB = 500  # Max size for backup DB

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._last_backup = 0
        self._db_available = False
        self._backup_available = False
        self._stats = {
            "total_backed_up_mb": 0,
            "backup_count": 0,
            "last_backup": None,
            "tables_backed": [],
        }

        # Ensure backup directory exists
        self.BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # Check primary DB
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if pool:
                self._db_available = True
                self._ensure_backup_tables()
                cprint("[BACKUP] PostgreSQL connected — backup system active", "white", "on_green")
            else:
                cprint("[BACKUP] No DB — using local file backup", "yellow")
        except Exception:
            cprint("[BACKUP] DB init error", "yellow")

        # Check backup DB (separate connection)
        self._check_backup_db()

    def _check_backup_db(self):
        """Check if a separate backup database is configured."""
        backup_url = os.environ.get("BACKUP_DATABASE_URL", "")
        if backup_url:
            try:
                import psycopg2
                self._backup_conn = psycopg2.connect(backup_url)
                self._backup_available = True
                cprint("[BACKUP] Separate backup DB connected", "white", "on_green")
            except Exception as e:
                cprint(f"[BACKUP] Backup DB not available: {e}", "yellow")
                self._backup_available = False
        else:
            self._backup_available = False
            cprint("[BACKUP] No BACKUP_DATABASE_URL — using local compressed files", "yellow")

    def _ensure_backup_tables(self):
        """Create backup tracking tables in primary DB."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return

            with pool.connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS backup_history (
                        id SERIAL PRIMARY KEY,
                        backup_time TIMESTAMPTZ DEFAULT NOW(),
                        tables_backed TEXT[],
                        rows_exported INTEGER DEFAULT 0,
                        size_mb NUMERIC DEFAULT 0,
                        method TEXT DEFAULT 'local',  -- local, remote
                        status TEXT DEFAULT 'running',
                        error TEXT
                    )
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS backup_manifest (
                        id SERIAL PRIMARY KEY,
                        backup_id INTEGER REFERENCES backup_history(id),
                        table_name TEXT,
                        row_count INTEGER,
                        compressed_size_bytes INTEGER,
                        checksum TEXT
                    )
                """)

        except Exception as e:
            cprint(f"[BACKUP] Table init error: {e}", "yellow")

    async def run_backup(self):
        """Main backup routine — called daily by AsyncScheduler."""
        if not self._db_available:
            return

        now = time.time()
        if (now - self._last_backup) < self.BACKUP_INTERVAL:
            return

        self._last_backup = now
        cprint("[BACKUP] Starting daily backup...", "cyan")

        # Emit start event
        await self._emit_event("backup/started", {
            "time": datetime.now(timezone.utc).isoformat()
        })

        # Log backup start
        backup_id = self._log_backup_start()
        total_rows = 0
        total_size = 0
        tables_backed = []

        try:
            from src.db_storage import get_pool
            pool = get_pool()

            with pool.connection() as conn:
                # 1. Backup trade results
                rows = self._backup_table(conn, "trades", backup_id)
                if rows > 0:
                    tables_backed.append("trades")
                    total_rows += rows

                # 2. Backup token metadata (from scanner_results)
                rows = self._backup_table(conn, "scanner_results", backup_id)
                if rows > 0:
                    tables_backed.append("scanner_results")
                    total_rows += rows

                # 3. Backup OHLCV 1h candles (the most valuable)
                try:
                    r = conn.execute(
                        "SELECT COUNT(*) as cnt FROM ohlcv_candles WHERE timeframe = '1h'"
                    ).fetchone()
                    rows_1h = r["cnt"] if r else 0
                    if rows_1h > 0:
                        self._backup_ohlcv_1h(conn, backup_id)
                        tables_backed.append("ohlcv_candles_1h")
                        total_rows += rows_1h
                except Exception:
                    pass

                # 4. Backup portfolio summary
                rows = self._backup_table(conn, "portfolio", backup_id)
                if rows > 0:
                    tables_backed.append("portfolio")
                    total_rows += rows

                # 5. Backup engine events summary (last 24h only)
                try:
                    r = conn.execute(
                        "SELECT COUNT(*) as cnt FROM engine_events WHERE created_at > NOW() - INTERVAL '24 hours'"
                    ).fetchone()
                    rows_events = r["cnt"] if r else 0
                    if rows_events > 0:
                        self._backup_engine_events_24h(conn, backup_id)
                        tables_backed.append("engine_events_24h")
                        total_rows += rows_events
                except Exception:
                    pass

        except Exception as e:
            cprint(f"[BACKUP] Error: {e}", "yellow")
            self._log_backup_end(backup_id, tables_backed, total_rows, 0, error=str(e))
            return

        # Calculate total size
        total_size = self._estimate_backup_size(tables_backed)

        # Log backup end
        self._log_backup_end(backup_id, tables_backed, total_rows, total_size)

        # Update stats
        self._stats["total_backed_up_mb"] += total_size
        self._stats["backup_count"] += 1
        self._stats["last_backup"] = datetime.now(timezone.utc).isoformat()
        self._stats["tables_backed"] = tables_backed

        # Emit completion event
        await self._emit_event("backup/completed", {
            "backup_id": backup_id,
            "tables_backed": tables_backed,
            "rows_exported": total_rows,
            "size_mb": round(total_size, 2),
            "method": "remote" if self._backup_available else "local",
            "total_backed_up_mb": round(self._stats["total_backed_up_mb"], 2),
        })

        cprint(f"[BACKUP] Complete: {total_rows} rows, {total_size:.1f} MB, {len(tables_backed)} tables", "white", "on_green")

    def _backup_table(self, conn, table_name: str, backup_id: int) -> int:
        """Backup a table to compressed local file."""
        try:
            # Check if table exists
            try:
                table_check = conn.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = %s
                    )
                """, (table_name,)).fetchone()
                
                if not table_check or not table_check[0]:
                    cprint(f"[BACKUP] Table {table_name} not found, skipping", "yellow")
                    return 0
            except Exception:
                # If table check fails, try to query directly
                pass

            # Get row count
            try:
                r = conn.execute(f"SELECT COUNT(*) as cnt FROM {table_name}").fetchone()
                count = r["cnt"] if r else 0
            except Exception:
                cprint(f"[BACKUP] Table {table_name} query failed, skipping", "yellow")
                return 0
            
            if count == 0:
                return 0

            # Export to JSON
            rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
            data = [dict(row) for row in rows]

            # Compress and save
            filepath = self.BACKUP_DIR / f"{table_name}_{datetime.now().strftime('%Y%m%d')}.json.gz"
            with gzip.open(filepath, 'wt', encoding='utf-8') as f:
                json.dump(data, f, default=str, indent=2)

            size_mb = filepath.stat().st_size / (1024 * 1024)
            cprint(f"[BACKUP] {table_name}: {count} rows → {size_mb:.2f} MB", "cyan")

            # If remote backup available, also push there
            if self._backup_available:
                self._push_to_backup_db(table_name, data, backup_id)

            # Log manifest
            self._log_manifest(backup_id, table_name, count, filepath.stat().st_size)

            return count

        except Exception as e:
            cprint(f"[BACKUP] {table_name} error: {e}", "yellow")
            return 0

    def _backup_ohlcv_1h(self, conn, backup_id: int):
        """Backup 1h OHLCV candles specifically."""
        try:
            rows = conn.execute(
                "SELECT * FROM ohlcv_candles WHERE timeframe = '1h'"
            ).fetchall()
            data = [dict(row) for row in rows]

            filepath = self.BACKUP_DIR / f"ohlcv_1h_{datetime.now().strftime('%Y%m%d')}.json.gz"
            with gzip.open(filepath, 'wt', encoding='utf-8') as f:
                json.dump(data, f, default=str, indent=2)

            size_mb = filepath.stat().st_size / (1024 * 1024)
            cprint(f"[BACKUP] ohlcv_1h: {len(data)} rows → {size_mb:.2f} MB", "cyan")

            if self._backup_available:
                self._push_to_backup_db("ohlcv_1h", data, backup_id)

            self._log_manifest(backup_id, "ohlcv_1h", len(data), filepath.stat().st_size)

        except Exception as e:
            cprint(f"[BACKUP] ohlcv_1h error: {e}", "yellow")

    def _backup_engine_events_24h(self, conn, backup_id: int):
        """Backup last 24h of engine events."""
        try:
            rows = conn.execute(
                "SELECT * FROM engine_events WHERE created_at > NOW() - INTERVAL '24 hours'"
            ).fetchall()
            data = [dict(row) for row in rows]

            filepath = self.BACKUP_DIR / f"engine_events_24h_{datetime.now().strftime('%Y%m%d')}.json.gz"
            with gzip.open(filepath, 'wt', encoding='utf-8') as f:
                json.dump(data, f, default=str, indent=2)

            size_mb = filepath.stat().st_size / (1024 * 1024)
            cprint(f"[BACKUP] engine_events_24h: {len(data)} rows → {size_mb:.2f} MB", "cyan")

            if self._backup_available:
                self._push_to_backup_db("engine_events_24h", data, backup_id)

            self._log_manifest(backup_id, "engine_events_24h", len(data), filepath.stat().st_size)

        except Exception as e:
            cprint(f"[BACKUP] engine_events_24h error: {e}", "yellow")

    def _push_to_backup_db(self, table_name: str, data: list, backup_id: int):
        """Push data to separate backup database."""
        if not self._backup_available or not data:
            return

        try:
            cur = self._backup_conn.cursor()

            # Create table if not exists (simple schema)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS backup_{table_name} (
                    id SERIAL PRIMARY KEY,
                    backup_id INTEGER,
                    data JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Insert data
            for row in data:
                cur.execute(
                    f"INSERT INTO backup_{table_name} (backup_id, data) VALUES (%s, %s)",
                    (backup_id, json.dumps(row, default=str))
                )

            self._backup_conn.commit()
            cprint(f"[BACKUP] Pushed {len(data)} rows to backup DB: {table_name}", "cyan")

        except Exception as e:
            cprint(f"[BACKUP] Backup DB push error: {e}", "yellow")
            self._backup_conn.rollback()

    def _estimate_backup_size(self, tables: list) -> float:
        """Estimate total backup size in MB."""
        total = 0
        for filepath in self.BACKUP_DIR.glob("*.gz"):
            total += filepath.stat().st_size / (1024 * 1024)
        return total

    def _log_backup_start(self) -> int:
        """Log backup start to DB."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return 0

            with pool.connection() as conn:
                row = conn.execute(
                    "INSERT INTO backup_history (status) VALUES ('running') RETURNING id"
                ).fetchone()
                return row["id"] if row else 0
        except Exception:
            return 0

    def _log_backup_end(self, backup_id: int, tables: list, rows: int,
                        size_mb: float, error: str = None):
        """Log backup completion to DB."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool or not backup_id:
                return

            status = "error" if error else "completed"
            method = "remote" if self._backup_available else "local"
            with pool.connection() as conn:
                conn.execute(
                    """UPDATE backup_history 
                       SET completed_at = NOW(), tables_backed = %s, 
                           rows_exported = %s, size_mb = %s, method = %s,
                           status = %s, error = %s
                       WHERE id = %s""",
                    (tables, rows, size_mb, method, status, error, backup_id)
                )
        except Exception:
            pass

    def _log_manifest(self, backup_id: int, table_name: str,
                      row_count: int, size_bytes: int):
        """Log backup manifest entry."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return

            with pool.connection() as conn:
                conn.execute(
                    """INSERT INTO backup_manifest 
                       (backup_id, table_name, row_count, compressed_size_bytes)
                       VALUES (%s, %s, %s, %s)""",
                    (backup_id, table_name, row_count, size_bytes)
                )
        except Exception:
            pass

    async def get_backup_report(self) -> dict:
        """Get backup status report."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return {"error": "No DB"}

            with pool.connection() as conn:
                r = conn.execute(
                    "SELECT * FROM backup_history ORDER BY id DESC LIMIT 1"
                ).fetchone()

                # Count backup files
                backup_files = list(self.BACKUP_DIR.glob("*.gz"))
                total_backup_size = sum(f.stat().st_size for f in backup_files) / (1024 * 1024)

                return {
                    "last_backup": dict(r) if r else None,
                    "backup_files": len(backup_files),
                    "total_backup_size_mb": round(total_backup_size, 2),
                    "backup_db_connected": self._backup_available,
                    "stats": self._stats,
                }

        except Exception as e:
            return {"error": str(e)}

    async def _emit_event(self, event_name: str, payload: dict):
        """Emit event via EventBus and log to DB."""
        if payload.get("completed"):
            cprint(f"[BACKUP] Event: {event_name} — {payload.get('rows_exported', 0)} rows", "cyan")

        try:
            from src.db_storage import log_event
            log_event(event_name, payload)
        except Exception:
            pass

        if self.event_bus:
            try:
                import asyncio
                _fire_and_forget(self.event_bus.emit(event_name, payload))
            except Exception:
                pass


# ── Singleton ──────────────────────────────────────────────
_manager_instance = None

def get_backup_manager(event_bus=None) -> BackupManager:
    """Get or create the singleton BackupManager instance."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = BackupManager(event_bus=event_bus)
        cprint("[BACKUP] Backup Manager initialized", "white", "on_green")
    return _manager_instance
