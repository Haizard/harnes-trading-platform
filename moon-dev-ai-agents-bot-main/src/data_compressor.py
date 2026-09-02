"""
Moon Dev Data Compressor
Compresses and aggregates old data to reduce storage usage.
Runs as part of the tiered storage cleanup.

Compression Rules:
  - 1m candles → 5m candles (aggregate 5 into 1)
  - 5m candles → 15m candles (aggregate 3 into 1)
  - 15m candles → 1h candles (aggregate 4 into 1)
  - Trades → daily summary
  - Wallet events → daily summary

DSH Pattern: EventBus → DB → Singleton
"""

import time
from datetime import datetime, timezone
from termcolor import cprint
from src.event_bus import _fire_and_forget


class DataCompressor:
    """Compresses and aggregates old data with DSH compliance."""

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._db_available = False
        self._last_compression = 0
        self._compression_interval = 86400  # Daily
        self._stats = {
            "total_compressions": 0,
            "rows_compressed": 0,
            "mb_saved": 0,
        }

        # Check DB
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if pool:
                self._db_available = True
                cprint("[COMPRESS] PostgreSQL connected — data compression active", "white", "on_green")
            else:
                cprint("[COMPRESS] No DB — compression disabled", "yellow")
        except Exception:
            cprint("[COMPRESS] DB init error", "yellow")

    async def run_compression(self):
        """Main compression routine — called daily by AsyncScheduler."""
        if not self._db_available:
            return

        now = time.time()
        if (now - self._last_compression) < self._compression_interval:
            return

        self._last_compression = now
        cprint("[COMPRESS] Starting daily compression...", "cyan")

        # Emit start event
        await self._emit_event("compression/started", {
            "time": datetime.now(timezone.utc).isoformat()
        })

        total_rows = 0
        total_saved = 0

        try:
            from src.db_storage import get_pool
            pool = get_pool()

            with pool.connection() as conn:
                # 1. Aggregate 1m → 5m candles (older than 7 days)
                rows, saved = self._compress_1m_to_5m(conn)
                total_rows += rows
                total_saved += saved

                # 2. Aggregate 5m → 15m candles (older than 30 days)
                rows, saved = self._compress_5m_to_15m(conn)
                total_rows += rows
                total_saved += saved

                # 3. Aggregate 15m → 1h candles (older than 60 days)
                rows, saved = self._compress_15m_to_1h(conn)
                total_rows += rows
                total_saved += saved

                # 4. Compress order book to summary (older than 24h)
                rows, saved = self._compress_orderbook(conn)
                total_rows += rows
                total_saved += saved

                # VACUUM skipped — PostgreSQL auto-vacuums

        except Exception as e:
            cprint(f"[COMPRESS] Error: {e}", "yellow")
            return

        # Update stats
        self._stats["total_compressions"] += 1
        self._stats["rows_compressed"] += total_rows
        self._stats["mb_saved"] += total_saved

        # Emit completion event
        await self._emit_event("compression/completed", {
            "rows_compressed": total_rows,
            "mb_saved": round(total_saved, 2),
            "total_compressions": self._stats["total_compressions"],
            "total_mb_saved": round(self._stats["mb_saved"], 2),
        })

        cprint(f"[COMPRESS] Complete: {total_rows} rows compressed, saved {total_saved:.1f} MB", "white", "on_green")

    def _compress_1m_to_5m(self, conn) -> tuple:
        """Aggregate 1m candles into 5m candles."""
        try:
            # Get 1m candles older than 7 days that haven't been aggregated
            rows = conn.execute("""
                WITH old_1m AS (
                    SELECT token_address, 
                           date_trunc('hour', candle_time) + 
                           (extract(minute from candle_time)::int / 5 * 5 || ' minutes')::interval as bucket,
                           MIN(open) as open_p,
                           MAX(high) as high_p,
                           MIN(low) as low_p,
                           MAX(close) as close_p,
                           SUM(volume) as total_vol
                    FROM ohlcv_candles 
                    WHERE timeframe = '1m' 
                    AND candle_time < NOW() - INTERVAL '7 days'
                    GROUP BY token_address, bucket
                )
                INSERT INTO ohlcv_candles (token_address, timeframe, candle_time, open, high, low, close, volume)
                SELECT token_address, '5m', bucket, open_p, high_p, low_p, close_p, total_vol
                FROM old_1m
                ON CONFLICT (token_address, candle_time, timeframe) DO NOTHING
            """)
            inserted = rows.rowcount

            # Delete the original 1m candles
            deleted = conn.execute("""
                DELETE FROM ohlcv_candles 
                WHERE timeframe = '1m' 
                AND candle_time < NOW() - INTERVAL '7 days'
            """).rowcount

            saved = deleted * 150 / (1024 * 1024)  # ~150 bytes per candle
            if deleted > 0:
                cprint(f"[COMPRESS] 1m → 5m: {deleted} 1m candles → {inserted} 5m candles", "cyan")

            return deleted, saved

        except Exception as e:
            cprint(f"[COMPRESS] 1m→5m error: {e}", "yellow")
            return 0, 0

    def _compress_5m_to_15m(self, conn) -> tuple:
        """Aggregate 5m candles into 15m candles."""
        try:
            rows = conn.execute("""
                WITH old_5m AS (
                    SELECT token_address, 
                           date_trunc('hour', candle_time) + 
                           (extract(minute from candle_time)::int / 15 * 15 || ' minutes')::interval as bucket,
                           MIN(open) as open_p,
                           MAX(high) as high_p,
                           MIN(low) as low_p,
                           MAX(close) as close_p,
                           SUM(volume) as total_vol
                    FROM ohlcv_candles 
                    WHERE timeframe = '5m' 
                    AND candle_time < NOW() - INTERVAL '30 days'
                    GROUP BY token_address, bucket
                )
                INSERT INTO ohlcv_candles (token_address, timeframe, candle_time, open, high, low, close, volume)
                SELECT token_address, '15m', bucket, open_p, high_p, low_p, close_p, total_vol
                FROM old_5m
                ON CONFLICT (token_address, candle_time, timeframe) DO NOTHING
            """)
            inserted = rows.rowcount

            deleted = conn.execute("""
                DELETE FROM ohlcv_candles 
                WHERE timeframe = '5m' 
                AND candle_time < NOW() - INTERVAL '30 days'
            """).rowcount

            saved = deleted * 150 / (1024 * 1024)
            if deleted > 0:
                cprint(f"[COMPRESS] 5m → 15m: {deleted} 5m candles → {inserted} 15m candles", "cyan")

            return deleted, saved

        except Exception as e:
            cprint(f"[COMPRESS] 5m→15m error: {e}", "yellow")
            return 0, 0

    def _compress_15m_to_1h(self, conn) -> tuple:
        """Aggregate 15m candles into 1h candles."""
        try:
            rows = conn.execute("""
                WITH old_15m AS (
                    SELECT token_address, 
                           date_trunc('hour', candle_time) as bucket,
                           MIN(open) as open_p,
                           MAX(high) as high_p,
                           MIN(low) as low_p,
                           MAX(close) as close_p,
                           SUM(volume) as total_vol
                    FROM ohlcv_candles 
                    WHERE timeframe = '15m' 
                    AND candle_time < NOW() - INTERVAL '60 days'
                    GROUP BY token_address, bucket
                )
                INSERT INTO ohlcv_candles (token_address, timeframe, candle_time, open, high, low, close, volume)
                SELECT token_address, '1h', bucket, open_p, high_p, low_p, close_p, total_vol
                FROM old_15m
                ON CONFLICT (token_address, candle_time, timeframe) DO NOTHING
            """)
            inserted = rows.rowcount

            deleted = conn.execute("""
                DELETE FROM ohlcv_candles 
                WHERE timeframe = '15m' 
                AND candle_time < NOW() - INTERVAL '60 days'
            """).rowcount

            saved = deleted * 150 / (1024 * 1024)
            if deleted > 0:
                cprint(f"[COMPRESS] 15m → 1h: {deleted} 15m candles → {inserted} 1h candles", "cyan")

            return deleted, saved

        except Exception as e:
            cprint(f"[COMPRESS] 15m→1h error: {e}", "yellow")
            return 0, 0

    def _compress_orderbook(self, conn) -> tuple:
        """Compress order book snapshots to hourly summaries."""
        try:
            # Check if tables exist first
            try:
                table_check = conn.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'orderbook_snapshots'
                    )
                """).fetchone()
                
                if not table_check or not table_check[0]:
                    return 0, 0  # Table doesn't exist yet, skip silently
            except Exception:
                return 0, 0  # Table check failed, skip silently

            # Create hourly summaries from raw snapshots
            rows = conn.execute("""
                WITH hourly AS (
                    SELECT token_address,
                           date_trunc('hour', snapshot_time) as hour,
                           AVG(spread) as avg_spread,
                           MAX(bid_depth) as max_bid_depth,
                           MAX(ask_depth) as max_ask_depth,
                           AVG(bid_depth) as avg_bid_depth,
                           AVG(ask_depth) as avg_ask_depth,
                           COUNT(*) as snapshot_count
                    FROM orderbook_snapshots 
                    WHERE snapshot_time < NOW() - INTERVAL '24 hours'
                    GROUP BY token_address, hour
                )
                INSERT INTO orderbook_summary 
                    (token_address, summary_time, avg_spread, max_bid, max_ask, 
                     avg_bid_depth, avg_ask_depth, whale_alerts)
                SELECT token_address, hour, avg_spread, max_bid_depth, max_ask_depth,
                       avg_bid_depth, avg_ask_depth, 0
                FROM hourly
                ON CONFLICT DO NOTHING
            """)
            inserted = rows.rowcount

            # Delete old raw snapshots
            deleted = conn.execute("""
                DELETE FROM orderbook_snapshots 
                WHERE snapshot_time < NOW() - INTERVAL '24 hours'
            """).rowcount

            saved = deleted * 10 / (1024 * 1024)  # ~10KB per snapshot
            if deleted > 0:
                cprint(f"[COMPRESS] Order book: {deleted} snapshots → {inserted} hourly summaries", "cyan")

            return deleted, saved

        except Exception as e:
            cprint(f"[COMPRESS] Order book error: {e}", "yellow")
            return 0, 0

    async def _emit_event(self, event_name: str, payload: dict):
        """Emit event via EventBus and log to DB."""
        if payload.get("completed"):
            cprint(f"[COMPRESS] Event: {event_name} — {payload.get('rows_compressed', 0)} rows", "cyan")

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

    def get_stats(self) -> dict:
        """Get compression statistics."""
        return self._stats.copy()


# ── Singleton ──────────────────────────────────────────────
_compressor_instance = None

def get_data_compressor(event_bus=None) -> DataCompressor:
    """Get or create the singleton DataCompressor instance."""
    global _compressor_instance
    if _compressor_instance is None:
        _compressor_instance = DataCompressor(event_bus=event_bus)
        cprint("[COMPRESS] Data Compressor initialized", "white", "on_green")
    return _compressor_instance
