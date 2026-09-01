"""
Moon Dev Storage Alerts
Monitors database usage and sends alerts via Telegram.
Checks hourly, warns at 70%, critical at 90%.

Alerts:
  - DB usage warnings (70%, 90%)
  - Monthly storage reports
  - Cleanup completion notifications
  - Backup status updates

DSH Pattern: EventBus → DB → Singleton
"""

import time
from datetime import datetime, timezone
from termcolor import cprint


class StorageAlerts:
    """Monitors storage and sends alerts with DSH compliance."""

    # Alert thresholds
    WARNING_THRESHOLD = 70  # 70% DB usage
    CRITICAL_THRESHOLD = 90  # 90% DB usage
    CHECK_INTERVAL = 3600  # Check hourly (seconds)

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._last_check = 0
        self._last_alert = {}  # {type: timestamp}
        self._alert_cooldown = 3600  # Don't repeat same alert within 1 hour

        # Listen for storage events
        if self.event_bus:
            self._setup_listeners()

    def _setup_listeners(self):
        """Subscribe to storage-related events."""
        try:
            import asyncio
            asyncio.ensure_future(self.event_bus.on("storage/tier/cleanup", self._on_cleanup))
            asyncio.ensure_future(self.event_bus.on("backup/completed", self._on_backup))
            asyncio.ensure_future(self.event_bus.on("storage/tier/stats", self._on_stats))
        except Exception:
            pass

    async def _on_cleanup(self, event):
        """Handle cleanup completion event."""
        if event.get("completed"):
            freed = event.get("freed_mb", 0)
            if freed > 100:  # Only alert if significant cleanup
                cprint(f"[ALERT] Cleanup freed {freed:.1f} MB", "cyan")

    async def _on_backup(self, event):
        """Handle backup completion event."""
        if event.get("status") == "completed":
            rows = event.get("rows_exported", 0)
            size = event.get("size_mb", 0)
            cprint(f"[ALERT] Backup complete: {rows} rows, {size:.1f} MB", "cyan")

    async def _on_stats(self, event):
        """Handle storage stats event."""
        pass

    async def check_storage(self):
        """Check DB usage and send alerts if needed."""
        now = time.time()
        if (now - self._last_check) < self.CHECK_INTERVAL:
            return

        self._last_check = now

        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return

            with pool.connection() as conn:
                # Get DB size
                row = conn.execute(
                    "SELECT pg_database_size(current_database()) as size_bytes"
                ).fetchone()

                if not row:
                    return

                size_mb = row["size_bytes"] / (1024 * 1024)
                limit_mb = 10240  # 10 GB
                usage_pct = (size_mb / limit_mb) * 100

                cprint(f"[ALERT] DB usage: {size_mb:.1f} MB ({usage_pct:.1f}%)", "cyan")

                # Check thresholds
                if usage_pct >= self.CRITICAL_THRESHOLD:
                    await self._send_alert("critical", usage_pct, size_mb, limit_mb)
                elif usage_pct >= self.WARNING_THRESHOLD:
                    await self._send_alert("warning", usage_pct, size_mb, limit_mb)

                # Take usage snapshot
                await self._save_usage_snapshot(size_mb, usage_pct)

        except Exception as e:
            cprint(f"[ALERT] Storage check error: {e}", "yellow")

    async def _send_alert(self, level: str, usage_pct: float,
                          size_mb: float, limit_mb: float):
        """Send storage alert via Telegram."""
        # Rate limit
        now = time.time()
        if level in self._last_alert and (now - self._last_alert[level]) < self._alert_cooldown:
            return
        self._last_alert[level] = now

        # Build message
        if level == "critical":
            emoji = "🔴"
            text = (
                f"{emoji} CRITICAL: Database {usage_pct:.1f}% full!\n\n"
                f"Used: {size_mb:.1f} MB / {limit_mb:.1f} MB\n"
                f"Action needed: Run cleanup or backup\n"
                f"Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
            )
        else:
            emoji = "🟡"
            text = (
                f"{emoji} WARNING: Database {usage_pct:.1f}% full\n\n"
                f"Used: {size_mb:.1f} MB / {limit_mb:.1f} MB\n"
                f"Auto-cleanup will run tonight\n"
                f"Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
            )

        # Emit event
        await self._emit_event(f"storage/{level}", {
            "usage_pct": round(usage_pct, 1),
            "size_mb": round(size_mb, 1),
            "limit_mb": limit_mb,
            "message": text,
        })

        cprint(f"[ALERT] {text}", "white", "on_red" if level == "critical" else "on_yellow")

    async def send_monthly_report(self):
        """Send monthly storage report to Telegram."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return

            with pool.connection() as conn:
                # Get DB size
                row = conn.execute(
                    "SELECT pg_database_size(current_database()) as size_bytes"
                ).fetchone()
                size_mb = row["size_bytes"] / (1024 * 1024) if row else 0

                # Get table sizes
                rows = conn.execute("""
                    SELECT relname, pg_total_relation_size(relid) as size_bytes
                    FROM pg_catalog.pg_statio_user_tables
                    ORDER BY pg_total_relation_size(relid) DESC
                    LIMIT 10
                """).fetchall()

                # Get row counts
                tables = {}
                for name in ["ohlcv_candles", "orderbook_snapshots", "trades",
                             "wallet_events", "engine_events", "scanner_results"]:
                    try:
                        r = conn.execute(f"SELECT COUNT(*) as cnt FROM {name}").fetchone()
                        tables[name] = r["cnt"] if r else 0
                    except Exception:
                        tables[name] = 0

                # Build report
                report_lines = [
                    f"📊 MONTHLY STORAGE REPORT ({datetime.now().strftime('%B %Y')})",
                    "=" * 40,
                    f"\nPrimary DB: {size_mb:.1f} GB / 10 GB ({size_mb/102.4:.1f}%)",
                    "\nTable Sizes:"
                ]

                for r in rows:
                    name = r["relname"]
                    size = r["size_bytes"] / (1024 * 1024)
                    report_lines.append(f"  {name}: {size:.1f} MB")

                report_lines.append("\nRow Counts:")
                for name, count in tables.items():
                    report_lines.append(f"  {name}: {count:,}")

                # Projection
                days_in_month = 30
                daily_growth = size_mb / max(days_in_month, 1)
                days_until_full = (10240 - size_mb) / max(daily_growth, 0.1)
                months_until_full = days_until_full / 30

                report_lines.extend([
                    f"\nGrowth Rate: {daily_growth:.1f} MB/day",
                    f"Projected Full: {months_until_full:.1f} months",
                    f"\nTime: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                ])

                report = "\n".join(report_lines)

                # Emit event
                await self._emit_event("storage/monthly_report", {
                    "total_size_mb": round(size_mb, 1),
                    "usage_pct": round(size_mb / 10240 * 100, 1),
                    "daily_growth_mb": round(daily_growth, 2),
                    "months_until_full": round(months_until_full, 1),
                    "table_counts": tables,
                    "message": report,
                })

                cprint(f"[ALERT] Monthly report generated", "cyan")
                return report

        except Exception as e:
            cprint(f"[ALERT] Monthly report error: {e}", "yellow")

    async def _save_usage_snapshot(self, size_mb: float, usage_pct: float):
        """Save usage snapshot to DB."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return

            with pool.connection() as conn:
                conn.execute(
                    """INSERT INTO storage_usage_snapshots 
                       (total_size_mb, table_sizes, tier_counts)
                       VALUES (%s, %s, %s)""",
                    (size_mb, "{}", "{}")
                )
        except Exception:
            pass

    async def _emit_event(self, event_name: str, payload: dict):
        """Emit event via EventBus and log to DB."""
        try:
            from src.db_storage import log_event
            log_event(event_name, payload)
        except Exception:
            pass

        if self.event_bus:
            try:
                import asyncio
                asyncio.ensure_future(self.event_bus.emit(event_name, payload))
            except Exception:
                pass


# ── Singleton ──────────────────────────────────────────────
_alerts_instance = None

def get_storage_alerts(event_bus=None) -> StorageAlerts:
    """Get or create the singleton StorageAlerts instance."""
    global _alerts_instance
    if _alerts_instance is None:
        _alerts_instance = StorageAlerts(event_bus=event_bus)
        cprint("[ALERT] Storage Alerts initialized", "white", "on_green")
    return _alerts_instance
