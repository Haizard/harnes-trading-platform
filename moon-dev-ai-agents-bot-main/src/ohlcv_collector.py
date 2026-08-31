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

    def add_tick(self, price: float, timestamp: float, volume: float = 0,
                 buys: int = 0, sells: int = 0, pair_address: str = ""):
        """Add a new price tick. Automatically buckets into candles."""
        if price <= 0:
            return

        candle_start = int(timestamp // self._candle_interval) * self._candle_interval

        if self._current_candle is None:
            # First tick — create new candle
            self._current_candle = Candle(
                timestamp=candle_start, price=price,
                volume=volume, buys=buys, sells=sells, pair_address=pair_address,
            )
        elif candle_start > self._current_candle.timestamp:
            # New candle period — close current and start new
            self.candles.append(self._current_candle)
            if len(self.candles) > self.max_candles:
                self.candles = self.candles[-self.max_candles:]
            self._current_candle = Candle(
                timestamp=candle_start, price=price,
                volume=volume, buys=buys, sells=sells, pair_address=pair_address,
            )
        else:
            # Same candle period — update
            self._current_candle.update(price, volume, buys, sells)

        self._last_update = time.time()

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


# ── OHLCV Collector ──────────────────────────────────────
class OHLCVCollector:
    """
    Continuous OHLCV collector for Solana tokens.
    
    Polls DexScreener pair data and builds candle history over time.
    Stores data locally so StrategyBridge can read it.
    
    Usage:
        collector = OHLCVCollector()
        
        # Register a token to track
        collector.track_token("token_address", pair_address="pair_address")
        
        # Poll (call from background thread)
        collector.poll_once()
        
        # Get OHLCV for strategy analysis
        df = collector.get_ohlcv("token_address")
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
        """Register a token to start tracking."""
        if token_address not in self.stores:
            self.stores[token_address] = TokenCandleStore(token_address)
            # Load any existing data
            self._load_token(token_address)

    def poll_once(self) -> int:
        """
        Poll DexScreener for all tracked tokens and update candle stores.
        Returns number of tokens updated.
        """
        self._poll_count += 1
        updated = 0

        for token_address, store in self.stores.items():
            try:
                # Get pair data from DexScreener
                pair_data = self._fetch_pair(token_address)
                if not pair_data:
                    self._tokens_without_data += 1
                    continue

                price = float(pair_data.get("priceUsd", 0) or 0)
                if price <= 0:
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
                    volume=vol_1h / 12,  # Distribute 1h volume across 5-min candles
                    buys=buys_1h // 12,
                    sells=sells_1h // 12,
                    pair_address=pair_addr,
                )
                updated += 1
                self._tokens_with_data += 1

                # Save to DB if available
                if self._db_available and store._current_candle:
                    self._save_candle_to_db(token_address, store._current_candle)

            except Exception as e:
                cprint(f"[OHLCV] Poll error for {token_address[:8]}...: {e}", "yellow")

        # Cleanup old candles every 100 polls (~50 minutes)
        if self._poll_count % 100 == 0:
            if self._db_available:
                try:
                    from src.db_storage import cleanup_old_candles
                    cleanup_old_candles(hours=24)
                except Exception:
                    pass
            self._save_all()  # Save local files too

        return updated

    def get_ohlcv(self, token_address: str, min_candles: int = 10) -> Optional[pd.DataFrame]:
        """Get OHLCV DataFrame for a token. Returns None if insufficient data."""
        # Try PostgreSQL first (persistent across restarts)
        if self._db_available:
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

        # Fall back to in-memory store
        store = self.stores.get(token_address)
        if not store:
            return None

        df = store.get_dataframe()
        if df is not None and len(df) >= min_candles:
            return df
        return None

    def get_stats(self) -> dict:
        """Get collector statistics."""
        tokens_with_data = sum(1 for s in self.stores.values() if s.get_tick_count() >= 3)
        return {
            "tracked_tokens": len(self.stores),
            "tokens_with_data": tokens_with_data,
            "total_polls": self._poll_count,
            "candles_per_token": {
                addr[:8]: s.get_tick_count()
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

    def _save_token(self, token_address: str, store: TokenCandleStore):
        """Save a single token's candle data to disk."""
        try:
            filepath = self.data_dir / f"{token_address}.json"
            all_candles = list(store.candles)
            if store._current_candle:
                all_candles.append(store._current_candle)

            data = {
                "token_address": token_address,
                "candles": [c.to_dict() for c in all_candles],
                "saved_at": time.time(),
            }
            with open(filepath, "w") as f:
                json.dump(data, f)
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

            store = TokenCandleStore(token_address)
            for candle_data in data.get("candles", []):
                c = Candle(
                    timestamp=candle_data["t"],
                    price=candle_data["c"],  # Use close as price
                    volume=candle_data.get("v", 0),
                    buys=candle_data.get("buys", 0),
                    sells=candle_data.get("sells", 0),
                )
                c.open = candle_data.get("o", candle_data["c"])
                c.high = candle_data.get("h", candle_data["c"])
                c.low = candle_data.get("l", candle_data["c"])
                store.candles.append(c)

            if store.candles:
                store._last_update = time.time()
                self.stores[token_address] = store
                cprint(f"[OHLCV] Loaded {len(store.candles)} candles for {token_address[:8]}...", "cyan")

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
