"""
🌙 Moon Dev's OHLCV Continuous Collector
Polls DexScreener pair data every scan cycle and builds up real candle history.

Problem: DexScreener has no free OHLCV endpoint, and Birdeye lacks data for brand new tokens.
Solution: Poll DexScreener's pair endpoint every 30-60s and build candle history from
          the price/volume data we already collect. Over time, this builds enough data
          for the StrategyBridge to run technical indicators.

DSH Pattern: BackgroundJob → Poll → Store → StrategyBridge reads cache
"""

import os
import time
import json
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
from collections import defaultdict
from termcolor import cprint


# ── Candle Data Point ──────────────────────────────────────
class Candle:
    """A single OHLCV candle built from DexScreener pair data."""
    __slots__ = ['timestamp', 'open', 'high', 'low', 'close', 'volume',
                 'buys', 'sells', 'pair_address', 'price_usd']

    def __init__(self, timestamp: float, price: float, volume: float = 0,
                 buys: int = 0, sells: int = 0, pair_address: str = ""):
        self.timestamp = timestamp
        self.price_usd = price
        self.open = price
        self.high = price
        self.low = price
        self.close = price
        self.volume = volume
        self.buys = buys
        self.sells = sells
        self.pair_address = pair_address

    def update(self, price: float, volume: float = 0, buys: int = 0, sells: int = 0):
        """Update candle with new tick data."""
        self.close = price
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.volume += volume
        self.buys += buys
        self.sells += sells

    def to_dict(self) -> dict:
        return {
            "t": self.timestamp,
            "o": self.open,
            "h": self.high,
            "l": self.low,
            "c": self.close,
            "v": self.volume,
            "buys": self.buys,
            "sells": self.sells,
        }


# ── Token Candle Store ──────────────────────────────────────
class TokenCandleStore:
    """Stores candle history for a single token."""

    def __init__(self, token_address: str, max_candles: int = 200):
        self.token_address = token_address
        self.max_candles = max_candles
        self.candles: list = []
        self._current_candle: Optional[Candle] = None
        self._candle_interval = 300  # 5-minute candles
        self._last_update = 0

        # Adaptive polling state
        self._volatility_score: float = 0.0   # 0-1, higher = more volatile
        self._activity_score: float = 0.0     # 0-1, higher = more active
        self._recent_prices: list = []        # last 10 prices for volatility calc
        self._poll_interval: float = 30.0     # seconds between polls (adaptive)
        self._last_polled: float = 0.0        # timestamp of last poll
        self._consecutive_no_data: int = 0    # how many polls returned nothing

    def _update_volatility(self):
        """Calculate volatility score from recent price changes (0-1)."""
        if len(self._recent_prices) < 3:
            self._volatility_score = 0.5  # default medium
            return

        # Calculate average absolute % change between consecutive prices
        changes = []
        for i in range(1, len(self._recent_prices)):
            prev = self._recent_prices[i - 1]
            curr = self._recent_prices[i]
            if prev > 0:
                pct = abs(curr - prev) / prev
                changes.append(pct)

        if not changes:
            self._volatility_score = 0.0
            return

        avg_change = sum(changes) / len(changes)
        # Normalize: 0% change = 0, 5%+ change = 1.0
        self._volatility_score = min(1.0, avg_change / 0.05)

    def _adjust_poll_interval(self):
        """Adjust poll interval based on volatility and activity.
        
        High volatility + high activity → poll every 15s (fast)
        Medium → poll every 30s (normal)
        Low volatility + low activity → poll every 90s (slow)
        """
        # Combined score: volatility matters more
        combined = (self._volatility_score * 0.7) + (self._activity_score * 0.3)

        if combined > 0.7:
            # Very active/volatile — poll fast
            self._poll_interval = 15.0
        elif combined > 0.4:
            # Moderate — normal polling
            self._poll_interval = 30.0
        elif combined > 0.15:
            # Low activity — slow polling
            self._poll_interval = 60.0
        else:
            # Dead/stale — very slow polling
            self._poll_interval = 90.0

    def should_poll_now(self) -> bool:
        """Check if enough time has passed since last poll based on adaptive interval."""
        now = time.time()
        return (now - self._last_polled) >= self._poll_interval

    def mark_polled(self):
        """Mark that this token was just polled."""
        self._last_polled = time.time()

    def is_dead(self, max_no_data_polls: int = 20) -> bool:
        """Check if token is dead (no data for many consecutive polls)."""
        return self._consecutive_no_data >= max_no_data_polls

    def get_poll_info(self) -> dict:
        """Get current polling stats for this token."""
        return {
            "volatility": round(self._volatility_score, 2),
            "activity": round(self._activity_score, 2),
            "interval": round(self._poll_interval, 1),
            "no_data_count": self._consecutive_no_data,
        }

    def add_tick(self, price: float, timestamp: float, volume: float = 0,
                 buys: int = 0, sells: int = 0, pair_address: str = ""):
        """Add a new price tick. Automatically buckets into candles."""
        if price <= 0:
            return

        candle_start = int(timestamp // self._candle_interval) * self._candle_interval

        if self._current_candle is None:
            self._current_candle = Candle(
                timestamp=candle_start, price=price,
                volume=volume, buys=buys, sells=sells, pair_address=pair_address,
            )
        elif candle_start > self._current_candle.timestamp:
            self.candles.append(self._current_candle)
            if len(self.candles) > self.max_candles:
                self.candles = self.candles[-self.max_candles:]
            self._current_candle = Candle(
                timestamp=candle_start, price=price,
                volume=volume, buys=buys, sells=sells, pair_address=pair_address,
            )
        else:
            self._current_candle.update(price, volume, buys, sells)

        # Track recent prices for volatility
        self._recent_prices.append(price)
        if len(self._recent_prices) > 10:
            self._recent_prices = self._recent_prices[-10:]

        # Update activity score
        if volume > 0:
            self._activity_score = min(1.0, self._activity_score + 0.1)
        else:
            self._activity_score = max(0.0, self._activity_score - 0.02)

        self._update_volatility()
        self._adjust_poll_interval()
        self._last_update = time.time()
        self._consecutive_no_data = 0

    def get_dataframe(self) -> Optional[pd.DataFrame]:
        """Get OHLCV DataFrame from accumulated candles."""
        all_candles = list(self.candles)
        if self._current_candle:
            all_candles.append(self._current_candle)

        if len(all_candles) < 3:
            return None

        rows = []
        for c in all_candles:
            rows.append({
                "datetime": datetime.fromtimestamp(c.timestamp, tz=timezone.utc),
                "Open": c.open,
                "High": c.high,
                "Low": c.low,
                "Close": c.close,
                "Volume": max(c.volume, 1),
            })

        df = pd.DataFrame(rows)
        df.set_index("datetime", inplace=True)
        df.sort_index(inplace=True)
        return df

    def get_tick_count(self) -> int:
        return len(self.candles) + (1 if self._current_candle else 0)

    def is_stale(self, max_age_seconds: int = 600) -> bool:
        """Check if we haven't received data recently."""
        return (time.time() - self._last_update) > max_age_seconds if self._last_update > 0 else True


# ── Timeframe Config ──────────────────────────────────────
TIMEFRAMES = {
    "1m": 60,      # 1 minute
    "5m": 300,     # 5 minutes
    "15m": 900,    # 15 minutes
    "1h": 3600,    # 1 hour
}


# ── Multi-Timeframe Store ──────────────────────────────────
class MultiTimeframeStore:
    """Stores candle history for a single token across multiple timeframes."""

    def __init__(self, token_address: str, max_candles: int = 200):
        self.token_address = token_address
        self.stores: Dict[str, TokenCandleStore] = {}

        # Create a store for each timeframe
        for tf_name, tf_seconds in TIMEFRAMES.items():
            store = TokenCandleStore(token_address, max_candles)
            store._candle_interval = tf_seconds
            self.stores[tf_name] = store

        # Adaptive polling state (on the primary 1m store)
        self._volatility_score: float = 0.0
        self._activity_score: float = 0.0
        self._recent_prices: list = []
        self._poll_interval: float = 30.0
        self._last_polled: float = 0.0
        self._consecutive_no_data: int = 0
        self._last_update = 0

    def add_tick(self, price: float, timestamp: float, volume: float = 0,
                 buys: int = 0, sells: int = 0, pair_address: str = ""):
        """Add a tick to ALL timeframe stores."""
        if price <= 0:
            return

        # Add to each timeframe store
        for tf_name, store in self.stores.items():
            store.add_tick(price, timestamp, volume, buys, sells, pair_address)

        # Track volatility/activity on this store
        self._recent_prices.append(price)
        if len(self._recent_prices) > 10:
            self._recent_prices = self._recent_prices[-10:]

        if volume > 0:
            self._activity_score = min(1.0, self._activity_score + 0.1)
        else:
            self._activity_score = max(0.0, self._activity_score - 0.02)

        self._update_volatility()
        self._adjust_poll_interval()
        self._last_update = time.time()
        self._consecutive_no_data = 0

    def _update_volatility(self):
        if len(self._recent_prices) < 3:
            self._volatility_score = 0.5
            return
        changes = []
        for i in range(1, len(self._recent_prices)):
            prev = self._recent_prices[i - 1]
            curr = self._recent_prices[i]
            if prev > 0:
                changes.append(abs(curr - prev) / prev)
        if changes:
            self._volatility_score = min(1.0, (sum(changes) / len(changes)) / 0.05)
        else:
            self._volatility_score = 0.0

    def _adjust_poll_interval(self):
        combined = (self._volatility_score * 0.7) + (self._activity_score * 0.3)
        if combined > 0.7:
            self._poll_interval = 15.0
        elif combined > 0.4:
            self._poll_interval = 30.0
        elif combined > 0.15:
            self._poll_interval = 60.0
        else:
            self._poll_interval = 90.0

    def should_poll_now(self) -> bool:
        return (time.time() - self._last_polled) >= self._poll_interval

    def mark_polled(self):
        self._last_polled = time.time()

    def is_dead(self, max_no_data_polls: int = 20) -> bool:
        return self._consecutive_no_data >= max_no_data_polls

    def get_dataframe(self, timeframe: str = "1m") -> Optional[pd.DataFrame]:
        """Get OHLCV DataFrame for a specific timeframe."""
        store = self.stores.get(timeframe)
        if not store:
            return None
        return store.get_dataframe()

    def get_poll_info(self) -> dict:
        return {
            "volatility": round(self._volatility_score, 2),
            "activity": round(self._activity_score, 2),
            "interval": round(self._poll_interval, 1),
            "no_data_count": self._consecutive_no_data,
        }

    def get_total_candles(self) -> int:
        return sum(s.get_tick_count() for s in self.stores.values())

    def is_stale(self, max_age_seconds: int = 600) -> bool:
        return (time.time() - self._last_update) > max_age_seconds if self._last_update > 0 else True


# ── OHLCV Collector ──────────────────────────────────────
class OHLCVCollector:
    """
    Continuous OHLCV collector for Solana tokens with multi-timeframe support.
    
    Polls DexScreener pair data and builds candle history across
    1m, 5m, 15m, and 1h timeframes.
    
    Usage:
        collector = OHLCVCollector()
        collector.track_token("token_address")
        collector.poll_once()
        
        # Get candles for specific timeframe
        df_1m = collector.get_ohlcv("token_address", timeframe="1m")
        df_1h = collector.get_ohlcv("token_address", timeframe="1h")
    """

    DEXSCREENER_PAIR = "https://api.dexscreener.com/latest/dex/pairs/solana/{pair_address}"
    DEXSCREENER_TOKEN = "https://api.dexscreener.com/tokens/v1/solana/{token_address}"

    def __init__(self, data_dir: str = "src/data/ohlcv"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.stores: Dict[str, TokenCandleStore] = {}
        self._poll_count = 0
        self._tokens_with_data = 0
        self._tokens_without_data = 0
        self._running = False
        self._db_available = False

        # Check DB availability
        try:
            from src.db_storage import get_pool
            self._db_available = get_pool() is not None
            if self._db_available:
                cprint("[OHLCV] PostgreSQL connected — candles stored in DB", "white", "on_green")
            else:
                cprint("[OHLCV] No DB — using local JSON files", "yellow")
        except Exception:
            self._db_available = False

        # Load existing data from disk or DB
        if self._db_available:
            self._load_from_db()
        else:
            self._load_all()

    def track_token(self, token_address: str, pair_address: str = ""):
        """Register a token to start tracking across all timeframes."""
        if token_address not in self.stores:
            self.stores[token_address] = MultiTimeframeStore(token_address)
            self._load_token(token_address)

    def poll_once(self) -> int:
        """
        Poll DexScreener for tracked tokens using adaptive intervals.
        High-volatility tokens poll every 15s, stale tokens every 90s.
        Dead tokens (no data for 20+ polls) are auto-removed.
        Returns number of tokens updated.
        """
        self._poll_count += 1
        updated = 0
        skipped = 0
        dead_tokens = []

        for token_address, store in self.stores.items():
            try:
                # Adaptive polling — skip if not time yet
                if not store.should_poll_now():
                    skipped += 1
                    continue

                store.mark_polled()

                # Check if token is dead
                if store.is_dead():
                    dead_tokens.append(token_address)
                    continue

                # Get pair data from DexScreener
                pair_data = self._fetch_pair(token_address)
                if not pair_data:
                    store._consecutive_no_data += 1
                    self._tokens_without_data += 1
                    continue

                price = float(pair_data.get("priceUsd", 0) or 0)
                if price <= 0:
                    store._consecutive_no_data += 1
                    continue

                # Extract volume and transaction data
                vol = pair_data.get("volume", {})
                vol_1h = float(vol.get("h1", 0) or 0)

                txns = pair_data.get("txns", {})
                txns_1h = txns.get("h1", {})
                buys_1h = int(txns_1h.get("buys", 0) or 0)
                sells_1h = int(txns_1h.get("sells", 0) or 0)

                pair_addr = pair_data.get("pairAddress", "")

                # Add tick to in-memory store
                now = time.time()
                store.add_tick(
                    price=price, timestamp=now,
                    volume=vol_1h / 12,
                    buys=buys_1h // 12,
                    sells=sells_1h // 12,
                    pair_address=pair_addr,
                )
                updated += 1
                self._tokens_with_data += 1

                # Save 1m candle to DB if available
                if self._db_available and store.stores["1m"]._current_candle:
                    self._save_candle_to_db(token_address, store.stores["1m"]._current_candle)

            except Exception as e:
                cprint(f"[OHLCV] Poll error for {token_address[:8]}...: {e}", "yellow")

        # Remove dead tokens
        for addr in dead_tokens:
            del self.stores[addr]
            cprint(f"[OHLCV] Removed dead token {addr[:8]}... (no data for 20+ polls)", "yellow")

        # Log adaptive stats every 20 polls
        if self._poll_count % 20 == 0:
            active = len(self.stores) - len(dead_tokens)
            if active > 0:
                intervals = [s._poll_interval for s in self.stores.values()]
                avg_interval = sum(intervals) / len(intervals) if intervals else 30
                cprint(f"[OHLCV] Adaptive: {updated} updated, {skipped} skipped, {active} active tokens, avg interval: {avg_interval:.0f}s", "cyan")

        # Cleanup old candles every 100 polls (~50 minutes)
        if self._poll_count % 100 == 0:
            if self._db_available:
                try:
                    from src.db_storage import cleanup_old_candles
                    cleanup_old_candles(hours=24)
                except Exception:
                    pass
            self._save_all()

        return updated

    def get_ohlcv(self, token_address: str, min_candles: int = 10,
                   timeframe: str = "1m") -> Optional[pd.DataFrame]:
        """Get OHLCV DataFrame for a token at a specific timeframe.
        
        Args:
            token_address: Token mint address
            min_candles: Minimum candles required (returns None if less)
            timeframe: "1m", "5m", "15m", or "1h"
        """
        store = self.stores.get(token_address)
        if not store:
            return None

        # Get from multi-timeframe store
        df = store.get_dataframe(timeframe)
        if df is not None and len(df) >= min_candles:
            return df

        # Fallback: try DB for 1m only
        if timeframe == "1m" and self._db_available:
            try:
                from src.db_storage import get_ohlcv_candles
                rows = get_ohlcv_candles(token_address, hours=24, limit=200)
                if rows and len(rows) >= min_candles:
                    df = pd.DataFrame(rows)
                    if "datetime" in df.columns:
                        df.set_index("datetime", inplace=True)
                    df.sort_index(inplace=True)
                    return df
            except Exception:
                pass

        return None

    def get_available_timeframes(self, token_address: str) -> dict:
        """Get available candle counts per timeframe for a token."""
        store = self.stores.get(token_address)
        if not store:
            return {}
        return {tf: s.get_tick_count() for tf, s in store.stores.items()}

    def get_stats(self) -> dict:
        """Get collector statistics with adaptive polling info."""
        tokens_with_data = sum(1 for s in self.stores.values() if s.get_total_candles() >= 3)
        intervals = [s._poll_interval for s in self.stores.values()]
        volatilities = [s._volatility_score for s in self.stores.values()]
        return {
            "tracked_tokens": len(self.stores),
            "tokens_with_data": tokens_with_data,
            "total_polls": self._poll_count,
            "avg_poll_interval": round(sum(intervals) / len(intervals), 1) if intervals else 30,
            "avg_volatility": round(sum(volatilities) / len(volatilities), 2) if volatilities else 0,
            "fast_tokens": sum(1 for i in intervals if i <= 15),
            "slow_tokens": sum(1 for i in intervals if i >= 60),
            "candles_per_token": {
                addr[:8]: s.get_total_candles()
                for addr, s in self.stores.items()
            },
            "poll_info_per_token": {
                addr[:8]: s.get_poll_info()
                for addr, s in self.stores.items()
            },
        }

    def _fetch_pair(self, token_address: str) -> Optional[dict]:
        """Fetch pair data from DexScreener."""
        try:
            url = self.DEXSCREENER_TOKEN.format(token_address=token_address)
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return None

            data = resp.json()
            pairs = data if isinstance(data, list) else data.get("pairs", [])

            # Find best Solana pair
            for p in pairs:
                if p.get("chainId") == "solana":
                    return p
            return pairs[0] if pairs else None

        except Exception:
            return None

    def _save_candle_to_db(self, token_address: str, candle: Candle):
        """Save a single candle to PostgreSQL."""
        try:
            from src.db_storage import save_ohlcv_candle
            from datetime import datetime, timezone
            candle_time = datetime.fromtimestamp(candle.timestamp, tz=timezone.utc).isoformat()
            save_ohlcv_candle(
                token_address=token_address,
                candle_time=candle_time,
                open_p=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                buys=candle.buys,
                sells=candle.sells,
                source="dexscreener",
            )
        except Exception as e:
            cprint(f"[OHLCV] DB save error: {e}", "yellow")

    def _load_from_db(self):
        """Load candle data from PostgreSQL for all tracked tokens."""
        try:
            from src.db_storage import get_ohlcv_candles, get_tracked_token_count
            # Get all unique tokens from DB
            token_count = get_tracked_token_count()
            if token_count > 0:
                cprint(f"[OHLCV] Loading {token_count} tokens from PostgreSQL", "cyan")
        except Exception as e:
            cprint(f"[OHLCV] DB load error: {e}", "yellow")

    def _save_all(self):
        """Save all token candle data to disk."""
        for token_address, store in self.stores.items():
            self._save_token(token_address, store)

    def _save_token(self, token_address: str, store: MultiTimeframeStore):
        """Save a single token's candle data to disk (all timeframes)."""
        try:
            filepath = self.data_dir / f"{token_address}.json"
            data = {
                "token_address": token_address,
                "timeframes": {},
                "saved_at": time.time(),
            }
            for tf_name, tf_store in store.stores.items():
                all_candles = list(tf_store.candles)
                if tf_store._current_candle:
                    all_candles.append(tf_store._current_candle)
                data["timeframes"][tf_name] = [c.to_dict() for c in all_candles]

            with open(filepath, "w") as f:
                json.dump(data, f, default=str)
        except Exception as e:
            cprint(f"[OHLCV] Save error for {token_address[:8]}...: {e}", "yellow")

    def _load_all(self):
        """Load all saved candle data from disk."""
        for filepath in self.data_dir.glob("*.json"):
            try:
                token_address = filepath.stem
                self._load_token(token_address)
            except Exception:
                pass

    def _load_token(self, token_address: str):
        """Load a single token's candle data from disk."""
        filepath = self.data_dir / f"{token_address}.json"
        if not filepath.exists():
            return

        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            store = MultiTimeframeStore(token_address)
            total = 0

            # New multi-timeframe format
            timeframes_data = data.get("timeframes", {})
            if timeframes_data:
                for tf_name, candles_data in timeframes_data.items():
                    tf_store = store.stores.get(tf_name)
                    if not tf_store:
                        continue
                    for candle_data in candles_data:
                        c = Candle(
                            timestamp=candle_data["t"],
                            price=candle_data["c"],
                            volume=candle_data.get("v", 0),
                            buys=candle_data.get("buys", 0),
                            sells=candle_data.get("sells", 0),
                        )
                        c.open = candle_data.get("o", candle_data["c"])
                        c.high = candle_data.get("h", candle_data["c"])
                        c.low = candle_data.get("l", candle_data["c"])
                        tf_store.candles.append(c)
                    total += len(tf_store.candles)
            else:
                # Legacy single-timeframe format
                tf_store = store.stores["5m"]
                for candle_data in data.get("candles", []):
                    c = Candle(
                        timestamp=candle_data["t"],
                        price=candle_data["c"],
                        volume=candle_data.get("v", 0),
                        buys=candle_data.get("buys", 0),
                        sells=candle_data.get("sells", 0),
                    )
                    c.open = candle_data.get("o", candle_data["c"])
                    c.high = candle_data.get("h", candle_data["c"])
                    c.low = candle_data.get("l", candle_data["c"])
                    tf_store.candles.append(c)
                total = len(tf_store.candles)

            if total > 0:
                store._last_update = time.time()
                self.stores[token_address] = store
                cprint(f"[OHLCV] Loaded {total} candles for {token_address[:8]}...", "cyan")

        except Exception as e:
            cprint(f"[OHLCV] Load error for {token_address[:8]}...: {e}", "yellow")


# ── Singleton ──────────────────────────────────────────────
_collector_instance = None

def get_ohlcv_collector() -> OHLCVCollector:
    """Get or create the singleton OHLCVCollector instance."""
    global _collector_instance
    if _collector_instance is None:
        _collector_instance = OHLCVCollector()
        cprint("[OHLCV] Continuous OHLCV Collector initialized", "white", "on_green")
    return _collector_instance
