"""
🌙 Moon Dev's Strategy Bridge
Connects backtest strategy logic to the live MicroEngine pipeline.

Fetches OHLCV data for any Solana token, calculates technical indicators,
and generates BUY/SELL/NEUTRAL signals from multiple strategy logics.

This bridges the gap between:
  - System 1 (main.py): Has strategies + OHLCV analysis but doesn't run
  - System 2 (MicroEngine): Runs but has no strategy signals

DSH Pattern: Candidate → OHLCV Fetch → Indicators → Strategy Signals → Orchestrator
"""

import os
import time
import json
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from termcolor import cprint
from src.event_bus import _fire_and_forget

# TA-Lib for indicator calculations
try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False
    cprint("[STRATEGY_BRIDGE] TA-Lib not available — using pandas fallback", "yellow")

# pandas-ta as fallback
try:
    import pandas_ta as ta
    PANDAS_TA_AVAILABLE = True
except ImportError:
    PANDAS_TA_AVAILABLE = False


# ── Signal Dataclass ──────────────────────────────────────────
@dataclass
class StrategySignal:
    """A signal from a single strategy."""
    strategy_name: str
    direction: str          # "BUY", "SELL", "NEUTRAL"
    strength: float         # 0.0 - 1.0
    confidence: float       # 0.0 - 1.0
    reasons: List[str] = field(default_factory=list)
    indicators: Dict = field(default_factory=dict)


@dataclass
class BridgeResult:
    """Combined result from all strategies."""
    token_address: str
    symbol: str
    signals: List[StrategySignal] = field(default_factory=list)
    combined_direction: str = "NEUTRAL"
    combined_strength: float = 0.0
    combined_confidence: float = 0.0
    data_source: str = "none"  # "birdeye", "dexscreener", "none"
    indicators: Dict = field(default_factory=dict)
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "token_address": self.token_address,
            "symbol": self.symbol,
            "combined_direction": self.combined_direction,
            "combined_strength": round(self.combined_strength, 3),
            "combined_confidence": round(self.combined_confidence, 3),
            "data_source": self.data_source,
            "signal_count": len(self.signals),
            "signals": [
                {
                    "strategy": s.strategy_name,
                    "direction": s.direction,
                    "strength": round(s.strength, 3),
                    "confidence": round(s.confidence, 3),
                    "reasons": s.reasons,
                }
                for s in self.signals
            ],
            "indicators": self.indicators,
            "timestamp": self.timestamp,
        }


# ── OHLCV Data Fetcher ──────────────────────────────────────
class OHLCVFetcher:
    """
    Fetches OHLCV candle data for Solana tokens.
    
    Priority:
      1. Birdeye API (requires BIRDEYE_API_KEY) — best quality
      2. DexScreener pair metrics (free, no key) — fallback with limited candles
    """

    DEXSCREENER_PAIR = "https://api.dexscreener.com/latest/dex/pairs/solana/{pair_address}"
    DEXSCREENER_TOKEN = "https://api.dexscreener.com/tokens/v1/solana/{token_address}"
    JUPITER_PRICE = "https://api.jup.ag/price/v2?ids={token_address}"

    def __init__(self):
        self._birdeye_key = os.environ.get("BIRDEYE_API_KEY", "")
        self._cache: Dict[str, pd.DataFrame] = {}
        self._cache_ttl = 120  # 2 minutes
        self._cache_timestamps: Dict[str, float] = {}

    def fetch_ohlcv(self, token_address: str, pair_address: str = "",
                    timeframe: str = "15m", limit: int = 100) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candles for a token.
        
        Returns DataFrame with columns: Open, High, Low, Close, Volume
        Index is DatetimeIndex.
        
        Priority:
          1. Local OHLCV cache (built up over time by OHLCVCollector)
          2. Birdeye API (requires BIRDEYE_API_KEY)
          3. DexScreener synthetic fallback (limited quality)
        """
        # Check in-memory cache
        cache_key = f"{token_address}_{timeframe}"
        if cache_key in self._cache:
            age = time.time() - self._cache_timestamps.get(cache_key, 0)
            if age < self._cache_ttl:
                return self._cache[cache_key]

        # Try local OHLCV collector cache first (built up over time)
        try:
            from src.ohlcv_collector import get_ohlcv_collector
            collector = get_ohlcv_collector()
            df = collector.get_ohlcv(token_address, min_candles=10)
            if df is not None and len(df) >= 10:
                self._cache[cache_key] = df
                self._cache_timestamps[cache_key] = time.time()
                cprint(f"[STRATEGY_BRIDGE] Local cache hit: {len(df)} candles for {token_address[:8]}...", "green")
                return df
        except Exception:
            pass

        # Try Birdeye (best quality OHLCV)
        df = self._fetch_birdeye(token_address, timeframe, limit)
        if df is not None and len(df) >= 10:
            self._cache[cache_key] = df
            self._cache_timestamps[cache_key] = time.time()
            return df

        # Fallback: Build from DexScreener metrics + Jupiter price
        df = self._build_from_dexscreener(token_address, pair_address)
        if df is not None and len(df) >= 5:
            self._cache[cache_key] = df
            self._cache_timestamps[cache_key] = time.time()
            return df

        return None

    def _fetch_birdeye(self, token_address: str, timeframe: str,
                       limit: int) -> Optional[pd.DataFrame]:
        """Fetch OHLCV from Birdeye public API."""
        if not self._birdeye_key:
            return None

        try:
            # Calculate time range
            now = int(time.time())
            # Map timeframe to seconds
            tf_map = {"1m": 60, "5m": 300, "15m": 900, "1H": 3600, "4H": 14400, "1D": 86400}
            tf_seconds = tf_map.get(timeframe, 900)
            time_from = now - (limit * tf_seconds)
            time_to = now

            url = (
                f"https://public-api.birdeye.so/defi/ohlcv"
                f"?address={token_address}&type={timeframe}"
                f"&time_from={time_from}&time_to={time_to}"
            )
            headers = {"X-API-KEY": self._birdeye_key}
            resp = requests.get(url, headers=headers, timeout=10)

            if resp.status_code != 200:
                return None

            items = resp.json().get("data", {}).get("items", [])
            if not items:
                return None

            rows = []
            for item in items:
                rows.append({
                    "datetime": datetime.fromtimestamp(item["unixTime"], tz=timezone.utc),
                    "Open": item["o"],
                    "High": item["h"],
                    "Low": item["l"],
                    "Close": item["c"],
                    "Volume": item["v"],
                })

            df = pd.DataFrame(rows)
            df.set_index("datetime", inplace=True)
            df.sort_index(inplace=True)
            cprint(f"[STRATEGY_BRIDGE] Birdeye OHLCV: {len(df)} candles for {token_address[:8]}...", "green")
            return df

        except Exception as e:
            cprint(f"[STRATEGY_BRIDGE] Birdeye error: {e}", "yellow")
            return None

    def _build_from_dexscreener(self, token_address: str,
                                pair_address: str) -> Optional[pd.DataFrame]:
        """
        Build a synthetic OHLCV DataFrame from DexScreener pair metrics.
        Uses current price + historical price changes to approximate candles.
        """
        try:
            # Fetch pair data from DexScreener
            if pair_address:
                url = self.DEXSCREENER_PAIR.format(pair_address=pair_address)
            else:
                url = self.DEXSCREENER_TOKEN.format(token_address=token_address)

            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return None

            data = resp.json()
            pairs = data if isinstance(data, list) else data.get("pairs", [])

            if not pairs:
                return None

            # Find the best Solana pair
            pair = None
            for p in pairs:
                if p.get("chainId") == "solana":
                    pair = p
                    break
            if not pair:
                pair = pairs[0] if pairs else None
            if not pair:
                return None

            # Extract metrics
            price_usd = float(pair.get("priceUsd", 0) or 0)
            if price_usd <= 0:
                return None

            vol = pair.get("volume", {})
            vol_1h = float(vol.get("h1", 0) or 0)
            vol_6h = float(vol.get("h6", 0) or 0)
            vol_24h = float(vol.get("h24", 0) or 0)

            pc = pair.get("priceChange", {})
            pc_5m = float(pc.get("m5", 0) or 0)
            pc_1h = float(pc.get("h1", 0) or 0)
            pc_6h = float(pc.get("h6", 0) or 0)
            pc_24h = float(pc.get("h24", 0) or 0)

            txns = pair.get("txns", {})
            txns_1h = txns.get("h1", {})
            buys_1h = int(txns_1h.get("buys", 0) or 0)
            sells_1h = int(txns_1h.get("sells", 0) or 0)

            # Build synthetic OHLCV from available data points
            # We create ~20 candles representing recent price action
            now = datetime.now(timezone.utc)
            rows = []

            # Work backwards from current price using price change percentages
            changes = [pc_5m, pc_1h, pc_6h, pc_24h]
            valid_changes = [c for c in changes if c != 0]

            if not valid_changes:
                # No price change data — create flat candles
                valid_changes = [0] * 20

            # Build price series from changes
            prices = [price_usd]
            for change in reversed(valid_changes):
                prev_price = price_usd / (1 + change / 100)
                prices.append(prev_price)
            prices.reverse()

            # Interpolate to get more data points
            if len(prices) < 20:
                x_old = np.linspace(0, 1, len(prices))
                x_new = np.linspace(0, 1, 20)
                prices = list(np.interp(x_new, x_old, prices))

            # Build OHLCV candles from price series
            vol_per_candle = vol_1h / max(len(prices), 1) if vol_1h > 0 else 1000

            for i, close_price in enumerate(prices):
                # Simulate OHLC from close price
                noise = abs(close_price * 0.005)  # 0.5% noise
                open_price = prices[i - 1] if i > 0 else close_price
                high_price = max(open_price, close_price) + noise
                low_price = min(open_price, close_price) - noise
                volume = vol_per_candle * (1 + np.random.uniform(-0.3, 0.3))

                candle_time = now - pd.Timedelta(minutes=15 * (len(prices) - i))
                rows.append({
                    "datetime": candle_time,
                    "Open": open_price,
                    "High": high_price,
                    "Low": low_price,
                    "Close": close_price,
                    "Volume": max(volume, 100),
                })

            df = pd.DataFrame(rows)
            df.set_index("datetime", inplace=True)
            df.sort_index(inplace=True)

            # Store extra metrics for strategy use
            df.attrs["volume_1h"] = vol_1h
            df.attrs["volume_24h"] = vol_24h
            df.attrs["buys_1h"] = buys_1h
            df.attrs["sells_1h"] = sells_1h
            df.attrs["pair_age_hours"] = float(pair.get("pairCreatedAt", 0))

            cprint(f"[STRATEGY_BRIDGE] DexScreener fallback: {len(df)} synthetic candles for {token_address[:8]}...", "cyan")
            return df

        except Exception as e:
            cprint(f"[STRATEGY_BRIDGE] DexScreener fallback error: {e}", "yellow")
            return None


# ── Indicator Calculator ──────────────────────────────────────
class IndicatorEngine:
    """Calculate technical indicators from OHLCV data."""

    @staticmethod
    def calculate(df: pd.DataFrame) -> Dict[str, float]:
        """
        Calculate key indicators from OHLCV DataFrame.
        Returns dict of indicator values.
        """
        if df is None or len(df) < 5:
            return {}

        indicators = {}
        close = df["Close"].values.astype(float)
        high = df["High"].values.astype(float)
        low = df["Low"].values.astype(float)
        volume = df["Volume"].values.astype(float)

        try:
            if TALIB_AVAILABLE:
                # RSI
                if len(close) >= 15:
                    rsi = talib.RSI(close, timeperiod=14)
                    indicators["rsi"] = float(rsi[-1]) if not np.isnan(rsi[-1]) else 50.0
                else:
                    indicators["rsi"] = 50.0

                # MACD
                if len(close) >= 26:
                    macd, signal, hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
                    indicators["macd"] = float(macd[-1]) if not np.isnan(macd[-1]) else 0.0
                    indicators["macd_signal"] = float(signal[-1]) if not np.isnan(signal[-1]) else 0.0
                    indicators["macd_hist"] = float(hist[-1]) if not np.isnan(hist[-1]) else 0.0
                else:
                    indicators["macd"] = 0.0
                    indicators["macd_signal"] = 0.0
                    indicators["macd_hist"] = 0.0

                # Bollinger Bands
                if len(close) >= 20:
                    upper, middle, lower = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2)
                    indicators["bb_upper"] = float(upper[-1]) if not np.isnan(upper[-1]) else close[-1] * 1.02
                    indicators["bb_middle"] = float(middle[-1]) if not np.isnan(middle[-1]) else close[-1]
                    indicators["bb_lower"] = float(lower[-1]) if not np.isnan(lower[-1]) else close[-1] * 0.98
                    # BB width as percentage
                    if indicators["bb_middle"] > 0:
                        indicators["bb_width"] = (indicators["bb_upper"] - indicators["bb_lower"]) / indicators["bb_middle"] * 100
                    else:
                        indicators["bb_width"] = 0.0
                    # Price position within bands
                    bb_range = indicators["bb_upper"] - indicators["bb_lower"]
                    if bb_range > 0:
                        indicators["bb_pct"] = (close[-1] - indicators["bb_lower"]) / bb_range
                    else:
                        indicators["bb_pct"] = 0.5
                else:
                    indicators["bb_width"] = 0.0
                    indicators["bb_pct"] = 0.5

                # ATR (Average True Range)
                if len(close) >= 15:
                    atr = talib.ATR(high, low, close, timeperiod=14)
                    indicators["atr"] = float(atr[-1]) if not np.isnan(atr[-1]) else 0.0
                    # ATR as percentage of price
                    if close[-1] > 0:
                        indicators["atr_pct"] = indicators["atr"] / close[-1] * 100
                    else:
                        indicators["atr_pct"] = 0.0
                else:
                    indicators["atr"] = 0.0
                    indicators["atr_pct"] = 0.0

                # Stochastic Oscillator
                if len(close) >= 14:
                    slowk, slowd = talib.STOCH(high, low, close, fastk_period=14, slowk_period=3, slowd_period=3)
                    indicators["stoch_k"] = float(slowk[-1]) if not np.isnan(slowk[-1]) else 50.0
                    indicators["stoch_d"] = float(slowd[-1]) if not np.isnan(slowd[-1]) else 50.0
                else:
                    indicators["stoch_k"] = 50.0
                    indicators["stoch_d"] = 50.0

                # Simple Moving Averages
                if len(close) >= 20:
                    sma20 = talib.SMA(close, timeperiod=20)
                    indicators["sma20"] = float(sma20[-1]) if not np.isnan(sma20[-1]) else close[-1]
                else:
                    indicators["sma20"] = close[-1]

                if len(close) >= 10:
                    sma10 = talib.SMA(close, timeperiod=10)
                    indicators["sma10"] = float(sma10[-1]) if not np.isnan(sma10[-1]) else close[-1]
                else:
                    indicators["sma10"] = close[-1]

            else:
                # Fallback: pandas-based calculations
                indicators = IndicatorEngine._calculate_pandas(df)

        except Exception as e:
            cprint(f"[STRATEGY_BRIDGE] Indicator calculation error: {e}", "yellow")
            indicators = IndicatorEngine._calculate_pandas(df)

        # Current price
        indicators["current_price"] = float(close[-1])
        indicators["prev_price"] = float(close[-2]) if len(close) >= 2 else close[-1]

        # Price momentum (recent candles)
        if len(close) >= 5:
            indicators["momentum_5"] = (close[-1] / close[-5] - 1) * 100 if close[-5] > 0 else 0
        if len(close) >= 10:
            indicators["momentum_10"] = (close[-1] / close[-10] - 1) * 100 if close[-10] > 0 else 0

        # Volume analysis
        if len(volume) >= 5:
            avg_vol_5 = np.mean(volume[-5:])
            indicators["volume_ratio"] = volume[-1] / avg_vol_5 if avg_vol_5 > 0 else 1.0
        else:
            indicators["volume_ratio"] = 1.0

        return indicators

    @staticmethod
    def _calculate_pandas(df: pd.DataFrame) -> Dict[str, float]:
        """Fallback indicator calculation using pandas only."""
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        indicators = {}

        # RSI (simplified)
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        indicators["rsi"] = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

        # SMA
        indicators["sma10"] = float(close.rolling(10).mean().iloc[-1]) if len(close) >= 10 else float(close.iloc[-1])
        indicators["sma20"] = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else float(close.iloc[-1])

        # MACD (simplified)
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        indicators["macd"] = float(macd_line.iloc[-1]) if not pd.isna(macd_line.iloc[-1]) else 0.0
        indicators["macd_signal"] = float(signal_line.iloc[-1]) if not pd.isna(signal_line.iloc[-1]) else 0.0
        indicators["macd_hist"] = indicators["macd"] - indicators["macd_signal"]

        # Bollinger Bands
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        indicators["bb_upper"] = float(sma20.iloc[-1] + 2 * std20.iloc[-1]) if len(close) >= 20 else float(close.iloc[-1] * 1.02)
        indicators["bb_lower"] = float(sma20.iloc[-1] - 2 * std20.iloc[-1]) if len(close) >= 20 else float(close.iloc[-1] * 0.98)
        indicators["bb_middle"] = float(sma20.iloc[-1]) if len(close) >= 20 else float(close.iloc[-1])

        # ATR (simplified)
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        indicators["atr"] = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0
        indicators["atr_pct"] = indicators["atr"] / float(close.iloc[-1]) * 100 if close.iloc[-1] > 0 else 0.0

        # Stochastic (simplified)
        low_14 = low.rolling(14).min()
        high_14 = high.rolling(14).max()
        denom = high_14 - low_14
        stoch_k = ((close - low_14) / denom) * 100
        stoch_d = stoch_k.rolling(3).mean()
        indicators["stoch_k"] = float(stoch_k.iloc[-1]) if not pd.isna(stoch_k.iloc[-1]) else 50.0
        indicators["stoch_d"] = float(stoch_d.iloc[-1]) if not pd.isna(stoch_d.iloc[-1]) else 50.0

        return indicators


# ── Strategy Logics ──────────────────────────────────────────
class MomentumStrategy:
    """
    Based on MomentumRejection_PKG backtest.
    Uses Stochastic crossovers + trend confirmation.
    """
    NAME = "MomentumRejection"

    @staticmethod
    def evaluate(indicators: Dict, candidate_metrics: Dict = None) -> StrategySignal:
        rsi = indicators.get("rsi", 50)
        stoch_k = indicators.get("stoch_k", 50)
        stoch_d = indicators.get("stoch_d", 50)
        macd_hist = indicators.get("macd_hist", 0)
        momentum = indicators.get("momentum_5", 0)
        volume_ratio = indicators.get("volume_ratio", 1)

        strength = 0.0
        reasons = []

        # Stochastic crossover BUY
        if stoch_k < 30 and stoch_k > stoch_d:  # Oversold + bullish cross
            strength += 0.3
            reasons.append(f"Stoch oversold cross ({stoch_k:.0f}>{stoch_d:.0f})")

        # RSI oversold
        if rsi < 35:
            strength += 0.2
            reasons.append(f"RSI oversold ({rsi:.0f})")
        elif rsi < 45:
            strength += 0.1
            reasons.append(f"RSI low ({rsi:.0f})")

        # MACD bullish
        if macd_hist > 0:
            strength += 0.2
            reasons.append("MACD bullish")
        elif macd_hist > indicators.get("macd_signal", 0) * 0.5:
            strength += 0.1
            reasons.append("MACD improving")

        # Momentum
        if momentum > 1:
            strength += 0.15
            reasons.append(f"Momentum +{momentum:.1f}%")
        elif momentum > 0:
            strength += 0.05
            reasons.append("Positive momentum")

        # Volume confirmation
        if volume_ratio > 1.5:
            strength += 0.15
            reasons.append(f"Volume spike {volume_ratio:.1f}x")

        # Determine direction
        if strength >= 0.5:
            direction = "BUY"
        elif strength >= 0.3:
            direction = "BUY"  # Weak buy
        else:
            direction = "NEUTRAL"

        confidence = min(strength, 1.0)

        return StrategySignal(
            strategy_name=MomentumStrategy.NAME,
            direction=direction,
            strength=min(strength, 1.0),
            confidence=confidence,
            reasons=reasons,
            indicators={"rsi": rsi, "stoch_k": stoch_k, "macd_hist": macd_hist},
        )


class MeanReversionStrategy:
    """
    Based on ATRMeanReversion_PKG backtest.
    Uses Bollinger Bands + ATR for mean reversion signals.
    """
    NAME = "ATRMeanReversion"

    @staticmethod
    def evaluate(indicators: Dict, candidate_metrics: Dict = None) -> StrategySignal:
        bb_pct = indicators.get("bb_pct", 0.5)
        bb_width = indicators.get("bb_width", 0)
        rsi = indicators.get("rsi", 50)
        atr_pct = indicators.get("atr_pct", 0)
        price = indicators.get("current_price", 0)
        bb_lower = indicators.get("bb_lower", 0)
        bb_upper = indicators.get("bb_upper", 0)

        strength = 0.0
        reasons = []

        # Price near lower Bollinger Band (mean reversion buy)
        if bb_pct < 0.2:
            strength += 0.35
            reasons.append(f"Price near lower BB ({bb_pct:.0%})")
        elif bb_pct < 0.35:
            strength += 0.2
            reasons.append(f"Price in lower BB zone ({bb_pct:.0%})")

        # Price near upper Bollinger Band (mean reversion sell signal)
        if bb_pct > 0.85:
            strength -= 0.35
            reasons.append(f"Price near upper BB ({bb_pct:.0%})")
        elif bb_pct > 0.7:
            strength -= 0.2
            reasons.append(f"Price in upper BB zone ({bb_pct:.0%})")

        # RSI confirmation
        if rsi < 35:
            strength += 0.25
            reasons.append(f"RSI oversold ({rsi:.0f})")
        elif rsi > 65:
            strength -= 0.25
            reasons.append(f"RSI overbought ({rsi:.0f})")

        # ATR-based volatility (high ATR = better reversion opportunity)
        if atr_pct > 3:
            strength += 0.15
            reasons.append(f"High volatility ATR {atr_pct:.1f}%")
        elif atr_pct > 1.5:
            strength += 0.1
            reasons.append(f"Moderate volatility ATR {atr_pct:.1f}%")

        # Bollinger Band squeeze (low width = breakout imminent)
        if bb_width < 2:
            strength += 0.1
            reasons.append(f"BB squeeze (width={bb_width:.1f}%)")

        # Determine direction
        if strength >= 0.4:
            direction = "BUY"
        elif strength <= -0.4:
            direction = "SELL"
        else:
            direction = "NEUTRAL"

        confidence = min(abs(strength), 1.0)

        return StrategySignal(
            strategy_name=MeanReversionStrategy.NAME,
            direction=direction,
            strength=min(abs(strength), 1.0),
            confidence=confidence,
            reasons=reasons,
            indicators={"bb_pct": bb_pct, "bb_width": bb_width, "rsi": rsi, "atr_pct": atr_pct},
        )


class BreakoutStrategy:
    """
    Based on VengeanceTrend_PKG backtest.
    Uses ATR-based trend following + breakout detection.
    """
    NAME = "VengeanceTrend"

    @staticmethod
    def evaluate(indicators: Dict, candidate_metrics: Dict = None) -> StrategySignal:
        sma10 = indicators.get("sma10", 0)
        sma20 = indicators.get("sma20", 0)
        price = indicators.get("current_price", 0)
        prev_price = indicators.get("prev_price", 0)
        macd_hist = indicators.get("macd_hist", 0)
        momentum = indicators.get("momentum_5", 0)
        atr_pct = indicators.get("atr_pct", 0)
        volume_ratio = indicators.get("volume_ratio", 1)
        bb_pct = indicators.get("bb_pct", 0.5)

        strength = 0.0
        reasons = []

        # Trend: Price above SMAs = uptrend
        if price > sma10 > sma20:
            strength += 0.25
            reasons.append("Uptrend (price > SMA10 > SMA20)")
        elif price < sma10 < sma20:
            strength -= 0.25
            reasons.append("Downtrend (price < SMA10 < SMA20)")

        # Breakout: Price crossing above SMA20
        if prev_price < sma20 and price > sma20:
            strength += 0.3
            reasons.append("Breakout above SMA20")
        elif prev_price > sma20 and price < sma20:
            strength -= 0.3
            reasons.append("Breakdown below SMA20")

        # MACD momentum confirmation
        if macd_hist > 0 and momentum > 0:
            strength += 0.2
            reasons.append("MACD + momentum bullish")
        elif macd_hist < 0 and momentum < 0:
            strength -= 0.2
            reasons.append("MACD + momentum bearish")

        # Volume breakout confirmation
        if volume_ratio > 2 and momentum > 0:
            strength += 0.15
            reasons.append(f"Volume breakout ({volume_ratio:.1f}x)")
        elif volume_ratio > 2 and momentum < 0:
            strength -= 0.15
            reasons.append(f"Volume breakdown ({volume_ratio:.1f}x)")

        # ATR-based stop distance (volatility filter)
        if atr_pct > 5:
            reasons.append(f"High volatility ATR {atr_pct:.1f}% — use tight stops")

        # Determine direction
        if strength >= 0.45:
            direction = "BUY"
        elif strength <= -0.45:
            direction = "SELL"
        else:
            direction = "NEUTRAL"

        confidence = min(abs(strength), 1.0)

        return StrategySignal(
            strategy_name=BreakoutStrategy.NAME,
            direction=direction,
            strength=min(abs(strength), 1.0),
            confidence=confidence,
            reasons=reasons,
            indicators={"sma10": sma10, "sma20": sma20, "macd_hist": macd_hist, "momentum": momentum},
        )


class RSISnapbackStrategy:
    """
    Based on LUNARSNAPBACK_PKG — RSI crossover strategy.
    Buys when RSI crosses above oversold, sells when crosses above overbought.
    """
    NAME = "RSISnapback"

    @staticmethod
    def evaluate(indicators: Dict, candidate_metrics: Dict = None) -> StrategySignal:
        rsi = indicators.get("rsi", 50)
        macd_hist = indicators.get("macd_hist", 0)
        momentum = indicators.get("momentum_5", 0)

        strength = 0.0
        reasons = []

        # RSI oversold bounce (strong buy signal)
        if rsi < 30:
            strength += 0.4
            reasons.append(f"RSI deeply oversold ({rsi:.0f}) — snapback zone")
        elif rsi < 40:
            strength += 0.25
            reasons.append(f"RSI oversold ({rsi:.0f})")
        elif rsi > 70:
            strength -= 0.4
            reasons.append(f"RSI overbought ({rsi:.0f}) — distribution zone")
        elif rsi > 60:
            strength -= 0.2
            reasons.append(f"RSI elevated ({rsi:.0f})")

        # MACD confirmation
        if rsi < 40 and macd_hist > 0:
            strength += 0.2
            reasons.append("RSI oversold + MACD turning bullish")
        elif rsi > 60 and macd_hist < 0:
            strength -= 0.2
            reasons.append("RSI overbought + MACD turning bearish")

        # Momentum confirmation
        if rsi < 40 and momentum > 0:
            strength += 0.15
            reasons.append("Bullish momentum in oversold zone")

        direction = "BUY" if strength >= 0.35 else ("SELL" if strength <= -0.35 else "NEUTRAL")
        return StrategySignal(
            strategy_name=RSISnapbackStrategy.NAME, direction=direction,
            strength=min(abs(strength), 1.0), confidence=min(abs(strength), 1.0),
            reasons=reasons, indicators={"rsi": rsi},
        )


class StochRSIStrategy:
    """
    Based on AdaptiveStochasticReversal_PKG + StoicReversal_PKG.
    Uses Stochastic RSI for overbought/oversold detection with trailing stops.
    """
    NAME = "StochReversal"

    @staticmethod
    def evaluate(indicators: Dict, candidate_metrics: Dict = None) -> StrategySignal:
        stoch_k = indicators.get("stoch_k", 50)
        stoch_d = indicators.get("stoch_d", 50)
        rsi = indicators.get("rsi", 50)
        atr_pct = indicators.get("atr_pct", 0)

        strength = 0.0
        reasons = []

        # Stochastic RSI oversold + bullish cross
        if stoch_k < 20 and stoch_k > stoch_d:
            strength += 0.4
            reasons.append(f"Stoch oversold cross ({stoch_k:.0f}>{stoch_d:.0f})")
        elif stoch_k < 30:
            strength += 0.2
            reasons.append(f"Stoch near oversold ({stoch_k:.0f})")

        # Stochastic RSI overbought + bearish cross
        if stoch_k > 80 and stoch_k < stoch_d:
            strength -= 0.4
            reasons.append(f"Stoch overbought cross ({stoch_k:.0f}<{stoch_d:.0f})")
        elif stoch_k > 70:
            strength -= 0.2
            reasons.append(f"Stoch near overbought ({stoch_k:.0f})")

        # RSI confirmation
        if stoch_k < 30 and rsi < 40:
            strength += 0.2
            reasons.append("Dual oversold (Stoch + RSI)")
        elif stoch_k > 70 and rsi > 60:
            strength -= 0.2
            reasons.append("Dual overbought (Stoch + RSI)")

        # Volatility filter
        if atr_pct > 5:
            reasons.append(f"High volatility ATR {atr_pct:.1f}% — reduce size")

        direction = "BUY" if strength >= 0.35 else ("SELL" if strength <= -0.35 else "NEUTRAL")
        return StrategySignal(
            strategy_name=StochRSIStrategy.NAME, direction=direction,
            strength=min(abs(strength), 1.0), confidence=min(abs(strength), 1.0),
            reasons=reasons, indicators={"stoch_k": stoch_k, "stoch_d": stoch_d},
        )


class SMACrossoverStrategy:
    """
    Based on CobaltOrbit_PKG + MockSMAStrategy.
    SMA 10/20 crossover with trend confirmation.
    """
    NAME = "SMACrossover"

    @staticmethod
    def evaluate(indicators: Dict, candidate_metrics: Dict = None) -> StrategySignal:
        sma10 = indicators.get("sma10", 0)
        sma20 = indicators.get("sma20", 0)
        price = indicators.get("current_price", 0)
        prev_price = indicators.get("prev_price", 0)
        macd_hist = indicators.get("macd_hist", 0)

        strength = 0.0
        reasons = []

        # Golden cross: price breaks above SMA20
        if prev_price < sma20 and price > sma20:
            strength += 0.4
            reasons.append("Golden cross — price broke above SMA20")
        elif price > sma10 > sma20:
            strength += 0.25
            reasons.append("Uptrend (price > SMA10 > SMA20)")

        # Death cross: price breaks below SMA20
        if prev_price > sma20 and price < sma20:
            strength -= 0.4
            reasons.append("Death cross — price broke below SMA20")
        elif price < sma10 < sma20:
            strength -= 0.25
            reasons.append("Downtrend (price < SMA10 < SMA20)")

        # MACD confirmation
        if strength > 0 and macd_hist > 0:
            strength += 0.15
            reasons.append("MACD confirms bullish")
        elif strength < 0 and macd_hist < 0:
            strength -= 0.15
            reasons.append("MACD confirms bearish")

        direction = "BUY" if strength >= 0.35 else ("SELL" if strength <= -0.35 else "NEUTRAL")
        return StrategySignal(
            strategy_name=SMACrossoverStrategy.NAME, direction=direction,
            strength=min(abs(strength), 1.0), confidence=min(abs(strength), 1.0),
            reasons=reasons, indicators={"sma10": sma10, "sma20": sma20},
        )


class KeltnerReversionStrategy:
    """
    Based on AtrReversion_PKG + UnknownStrategy (ATR_MeanReversion).
    Uses Keltner Channels (EMA ± ATR multiplier) for mean reversion.
    """
    NAME = "KeltnerReversion"

    @staticmethod
    def evaluate(indicators: Dict, candidate_metrics: Dict = None) -> StrategySignal:
        bb_pct = indicators.get("bb_pct", 0.5)
        atr_pct = indicators.get("atr_pct", 0)
        rsi = indicators.get("rsi", 50)

        strength = 0.0
        reasons = []

        # Price below lower band (oversold)
        if bb_pct < 0.15:
            strength += 0.35
            reasons.append(f"Price below lower band ({bb_pct:.0%}) — mean reversion buy")
        elif bb_pct < 0.3:
            strength += 0.2
            reasons.append(f"Price near lower band ({bb_pct:.0%})")

        # Price above upper band (overbought)
        if bb_pct > 0.85:
            strength -= 0.35
            reasons.append(f"Price above upper band ({bb_pct:.0%}) — mean reversion sell")
        elif bb_pct > 0.7:
            strength -= 0.2
            reasons.append(f"Price near upper band ({bb_pct:.0%})")

        # ATR extension
        if atr_pct > 4 and bb_pct < 0.2:
            strength += 0.2
            reasons.append(f"ATR extension + oversold (ATR {atr_pct:.1f}%)")
        elif atr_pct > 4 and bb_pct > 0.8:
            strength -= 0.2
            reasons.append(f"ATR extension + overbought (ATR {atr_pct:.1f}%)")

        # RSI confirmation
        if rsi < 35 and bb_pct < 0.3:
            strength += 0.15
            reasons.append("RSI confirms oversold")
        elif rsi > 65 and bb_pct > 0.7:
            strength -= 0.15
            reasons.append("RSI confirms overbought")

        direction = "BUY" if strength >= 0.35 else ("SELL" if strength <= -0.35 else "NEUTRAL")
        return StrategySignal(
            strategy_name=KeltnerReversionStrategy.NAME, direction=direction,
            strength=min(abs(strength), 1.0), confidence=min(abs(strength), 1.0),
            reasons=reasons, indicators={"bb_pct": bb_pct, "atr_pct": atr_pct},
        )


class SwingLevelStrategy:
    """
    Based on AccumulationManipulation_PKG + ValidatedBreakthrough_PKG + DynamicValidation.
    Uses swing highs/lows and SMA for breakout/reversal detection.
    """
    NAME = "SwingLevels"

    @staticmethod
    def evaluate(indicators: Dict, candidate_metrics: Dict = None) -> StrategySignal:
        sma20 = indicators.get("sma20", 0)
        price = indicators.get("current_price", 0)
        prev_price = indicators.get("prev_price", 0)
        bb_upper = indicators.get("bb_upper", 0)
        bb_lower = indicators.get("bb_lower", 0)
        volume_ratio = indicators.get("volume_ratio", 1)

        strength = 0.0
        reasons = []

        # Breakout above resistance
        if prev_price < bb_upper and price > bb_upper:
            strength += 0.35
            reasons.append("Breakout above resistance")
        elif prev_price > bb_lower and price < bb_lower:
            strength -= 0.35
            reasons.append("Breakdown below support")

        # Accumulation at support
        if price < sma20 and volume_ratio > 1.5:
            strength += 0.2
            reasons.append(f"Accumulation at support (vol {volume_ratio:.1f}x)")
        elif price > sma20 and volume_ratio > 1.5:
            strength -= 0.15
            reasons.append(f"Distribution near resistance (vol {volume_ratio:.1f}x)")

        # Trend alignment
        if price > sma20:
            strength += 0.1
            reasons.append("Above SMA20")
        elif price < sma20:
            strength -= 0.1
            reasons.append("Below SMA20")

        direction = "BUY" if strength >= 0.35 else ("SELL" if strength <= -0.35 else "NEUTRAL")
        return StrategySignal(
            strategy_name=SwingLevelStrategy.NAME, direction=direction,
            strength=min(abs(strength), 1.0), confidence=min(abs(strength), 1.0),
            reasons=reasons, indicators={"sma20": sma20, "volume_ratio": volume_ratio},
        )


class DemandZoneStrategy:
    """
    Based on StructuralDemandReversal_PKG.
    Identifies demand/supply zones using ATR and price structure.
    """
    NAME = "DemandZone"

    @staticmethod
    def evaluate(indicators: Dict, candidate_metrics: Dict = None) -> StrategySignal:
        price = indicators.get("current_price", 0)
        atr_pct = indicators.get("atr_pct", 0)
        rsi = indicators.get("rsi", 50)
        bb_pct = indicators.get("bb_pct", 0.5)
        sma20 = indicators.get("sma20", 0)
        volume_ratio = indicators.get("volume_ratio", 1)

        strength = 0.0
        reasons = []

        in_demand_zone = price < sma20 and rsi < 40 and bb_pct < 0.3
        in_supply_zone = price > sma20 and rsi > 60 and bb_pct > 0.7

        if in_demand_zone:
            strength += 0.4
            reasons.append("Demand zone: price at support + oversold")
            if volume_ratio > 1.5:
                strength += 0.15
                reasons.append(f"Volume confirmation ({volume_ratio:.1f}x)")
        elif in_supply_zone:
            strength -= 0.4
            reasons.append("Supply zone: price at resistance + overbought")
            if volume_ratio > 1.5:
                strength -= 0.15
                reasons.append(f"Volume confirms distribution ({volume_ratio:.1f}x)")

        if not in_demand_zone and not in_supply_zone:
            if rsi < 35:
                strength += 0.15
                reasons.append(f"Approaching demand (RSI {rsi:.0f})")
            elif rsi > 65:
                strength -= 0.15
                reasons.append(f"Approaching supply (RSI {rsi:.0f})")

        direction = "BUY" if strength >= 0.35 else ("SELL" if strength <= -0.35 else "NEUTRAL")
        return StrategySignal(
            strategy_name=DemandZoneStrategy.NAME, direction=direction,
            strength=min(abs(strength), 1.0), confidence=min(abs(strength), 1.0),
            reasons=reasons, indicators={"rsi": rsi, "bb_pct": bb_pct},
        )


class PressureStrategy:
    """
    Buy/sell pressure analysis from DexScreener transaction data.
    Unique strategy — analyzes order flow imbalance.
    """
    NAME = "PressureFlow"

    @staticmethod
    def evaluate(indicators: Dict, candidate_metrics: Dict = None) -> StrategySignal:
        if not candidate_metrics:
            return StrategySignal(
                strategy_name=PressureStrategy.NAME,
                direction="NEUTRAL", strength=0, confidence=0,
                reasons=["No candidate metrics available"],
            )

        buys_1h = candidate_metrics.get("txns_1h_buys", 0)
        sells_1h = candidate_metrics.get("txns_1h_sells", 0)
        vol_1h = candidate_metrics.get("volume_1h", 0)
        vol_24h = candidate_metrics.get("volume_24h", 0)
        liq = candidate_metrics.get("liquidity_usd", 0)
        pc_1h = candidate_metrics.get("price_change_1h", 0)

        total_txns = buys_1h + sells_1h
        buy_ratio = buys_1h / total_txns if total_txns > 0 else 0.5

        strength = 0.0
        reasons = []

        # Buy pressure
        if buy_ratio > 0.7:
            strength += 0.35
            reasons.append(f"Strong buy pressure ({buy_ratio:.0%})")
        elif buy_ratio > 0.55:
            strength += 0.2
            reasons.append(f"Buy dominant ({buy_ratio:.0%})")
        elif buy_ratio < 0.3:
            strength -= 0.35
            reasons.append(f"Strong sell pressure ({1-buy_ratio:.0%})")
        elif buy_ratio < 0.45:
            strength -= 0.2
            reasons.append(f"Sell dominant ({1-buy_ratio:.0%})")

        # Volume momentum
        if vol_24h > 0:
            vol_ratio_1h_24h = vol_1h / (vol_24h / 24) if vol_24h > 24 else 1
            if vol_ratio_1h_24h > 2:
                strength += 0.2
                reasons.append(f"Volume surge ({vol_ratio_1h_24h:.1f}x avg)")
            elif vol_ratio_1h_24h > 1.5:
                strength += 0.1
                reasons.append(f"Above-avg volume ({vol_ratio_1h_24h:.1f}x)")

        # Liquidity depth
        if liq > 100000:
            strength += 0.1
            reasons.append("Deep liquidity")
        elif liq < 10000:
            strength -= 0.15
            reasons.append("Thin liquidity")

        # Price momentum confirmation
        if pc_1h > 5:
            strength += 0.15
            reasons.append(f"Strong momentum +{pc_1h:.1f}%")
        elif pc_1h < -5:
            strength -= 0.15
            reasons.append(f"Weak momentum {pc_1h:.1f}%")

        # Determine direction
        if strength >= 0.4:
            direction = "BUY"
        elif strength <= -0.4:
            direction = "SELL"
        else:
            direction = "NEUTRAL"

        confidence = min(abs(strength), 1.0)

        return StrategySignal(
            strategy_name=PressureStrategy.NAME,
            direction=direction,
            strength=min(abs(strength), 1.0),
            confidence=confidence,
            reasons=reasons,
            indicators={"buy_ratio": buy_ratio, "buys_1h": buys_1h, "sells_1h": sells_1h},
        )


# ── Main Strategy Bridge ─────────────────────────────────────
class StrategyBridge:
    """
    The bridge between backtest strategies and the live MicroEngine.
    
    Flow:
      Candidate → Fetch OHLCV → Calculate Indicators → Run Strategies → Combine Signals
    
    Usage:
        bridge = StrategyBridge()
        result = bridge.analyze(candidate.address, candidate.symbol, 
                                pair_address=candidate.pair_address,
                                candidate_metrics=candidate.to_dict())
    """

    # Strategy weights for combining signals
    # Derived from 30 validated backtest strategies in backtests_package/
    STRATEGY_WEIGHTS = {
        # Original strategies (adapted from backtest packages)
        "MomentumRejection": 1.0,   # Stochastic + RSI + MACD
        "ATRMeanReversion": 1.0,    # Bollinger + ATR mean reversion
        "VengeanceTrend": 1.2,      # ATR trend following (highest weight)
        "PressureFlow": 0.8,        # Order flow analysis
        # New strategies (adapted from backtest packages)
        "RSISnapback": 1.1,         # RSI crossover (from LUNARSNAPBACK)
        "StochReversal": 1.0,       # Stochastic RSI (from AdaptiveStochasticReversal)
        "SMACrossover": 1.1,        # SMA golden/death cross (from CobaltOrbit)
        "KeltnerReversion": 0.9,    # Keltner channel reversion (from AtrReversion)
        "SwingLevels": 1.0,         # Swing breakout (from ValidatedBreakthrough)
        "DemandZone": 1.2,          # Demand/supply zones (from StructuralDemandReversal)
    }

    def __init__(self, event_bus=None):
        self.fetcher = OHLCVFetcher()
        self.indicator_engine = IndicatorEngine()
        self.event_bus = event_bus  # DSH EventBus
        self.strategies = [
            # Original strategies
            MomentumStrategy,
            MeanReversionStrategy,
            BreakoutStrategy,
            PressureStrategy,
            # New strategies from backtest packages
            RSISnapbackStrategy,
            StochRSIStrategy,
            SMACrossoverStrategy,
            KeltnerReversionStrategy,
            SwingLevelStrategy,
            DemandZoneStrategy,
        ]
        # Stats
        self._analyses = 0
        self._signals_generated = 0
        self._errors = 0

    def analyze(self, token_address: str, symbol: str,
                pair_address: str = "",
                candidate_metrics: Dict = None) -> BridgeResult:
        """
        Analyze a token candidate with all strategies.
        
        Args:
            token_address: Solana token mint address
            symbol: Token symbol (e.g., "FART")
            pair_address: DexScreener pair address (optional, improves OHLCV fetch)
            candidate_metrics: Dict from TokenCandidate.to_dict() with volume, price changes, etc.
        
        Returns:
            BridgeResult with combined signals from all strategies
        """
        self._analyses += 1
        timestamp = datetime.now(timezone.utc).isoformat()

        result = BridgeResult(
            token_address=token_address,
            symbol=symbol,
            timestamp=timestamp,
        )

        try:
            # Step 1: Fetch OHLCV data
            df = self.fetcher.fetch_ohlcv(token_address, pair_address)

            if df is not None and len(df) >= 5:
                result.data_source = "birdeye" if self.fetcher._birdeye_key else "dexscreener"

                # Step 2: Calculate indicators
                indicators = self.indicator_engine.calculate(df)
                result.indicators = indicators

                # Step 3: Run each strategy
                for strategy_cls in self.strategies:
                    try:
                        signal = strategy_cls.evaluate(indicators, candidate_metrics)
                        if signal:
                            result.signals.append(signal)
                            if signal.direction != "NEUTRAL":
                                self._signals_generated += 1
                    except Exception as e:
                        cprint(f"[STRATEGY_BRIDGE] {strategy_cls.NAME} error: {e}", "yellow")

                # Step 4: Combine signals with weights
                result = self._combine_signals(result)

            else:
                result.data_source = "none"
                cprint(f"[STRATEGY_BRIDGE] No OHLCV data for {symbol} ({token_address[:8]}...)", "yellow")

                # Still run PressureFlow with candidate metrics if available
                if candidate_metrics:
                    try:
                        signal = PressureStrategy.evaluate({}, candidate_metrics)
                        result.signals.append(signal)
                        if signal.direction != "NEUTRAL":
                            result.combined_direction = signal.direction
                            result.combined_strength = signal.strength
                            result.combined_confidence = signal.confidence
                            result.data_source = "metrics_only"
                    except Exception as e:
                        cprint(f"[STRATEGY_BRIDGE] PressureFlow error: {e}", "yellow")

        except Exception as e:
            self._errors += 1
            cprint(f"[STRATEGY_BRIDGE] Analysis error for {symbol}: {e}", "red")

        # Log result
        if result.combined_direction != "NEUTRAL":
            cprint(
                f"[STRATEGY_BRIDGE] {symbol}: {result.combined_direction} "
                f"(strength={result.combined_strength:.2f}, conf={result.combined_confidence:.2f}, "
                f"source={result.data_source}, signals={len(result.signals)})",
                "green" if result.combined_direction == "BUY" else "red",
            )
            
            # DSH: Save non-NEUTRAL signals to DB
            try:
                from src.db_storage import log_event
                log_event("strategy/signal", {
                    "token": token_address,
                    "symbol": symbol,
                    "direction": result.combined_direction,
                    "strength": result.combined_strength,
                    "confidence": result.combined_confidence,
                    "signals": [s.strategy_name + ":" + s.direction for s in result.signals],
                })
            except Exception:
                pass
            
            # DSH: Emit to EventBus
            if self.event_bus:
                try:
                    import asyncio
                    payload = {
                        "token": token_address,
                        "symbol": symbol,
                        "direction": result.combined_direction,
                        "strength": result.combined_strength,
                    }
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        _fire_and_forget(self.event_bus.emit("strategy/signal", payload))
                    else:
                        loop.run_until_complete(self.event_bus.emit("strategy/signal", payload))
                except Exception:
                    pass

        return result

    def _combine_signals(self, result: BridgeResult) -> BridgeResult:
        """
        Combine multiple strategy signals into a single decision.
        
        Uses weighted voting:
          - Each strategy votes BUY/SELL/NEUTRAL with a strength
          - Weighted by strategy weight × signal strength × confidence
          - Majority wins, but needs minimum threshold
        """
        if not result.signals:
            return result

        buy_score = 0.0
        sell_score = 0.0
        total_weight = 0.0

        for signal in result.signals:
            weight = self.STRATEGY_WEIGHTS.get(signal.strategy_name, 1.0)
            weighted_score = weight * signal.strength * signal.confidence

            if signal.direction == "BUY":
                buy_score += weighted_score
            elif signal.direction == "SELL":
                sell_score += weighted_score

            total_weight += weight

        if total_weight == 0:
            return result

        # Normalize scores
        buy_norm = buy_score / total_weight
        sell_norm = sell_score / total_weight

        # Determine combined direction
        net_score = buy_norm - sell_norm

        if net_score > 0.15:
            result.combined_direction = "BUY"
            result.combined_strength = min(buy_norm, 1.0)
        elif net_score < -0.15:
            result.combined_direction = "SELL"
            result.combined_strength = min(sell_norm, 1.0)
        else:
            result.combined_direction = "NEUTRAL"
            result.combined_strength = 0.0

        # Confidence based on signal agreement
        buy_signals = sum(1 for s in result.signals if s.direction == "BUY")
        sell_signals = sum(1 for s in result.signals if s.direction == "SELL")
        total_signals = len(result.signals)

        if total_signals > 0:
            agreement = max(buy_signals, sell_signals) / total_signals
            result.combined_confidence = agreement * (abs(net_score) * 2)
            result.combined_confidence = min(result.combined_confidence, 1.0)

        return result

    def get_stats(self) -> Dict:
        return {
            "total_analyses": self._analyses,
            "signals_generated": self._signals_generated,
            "errors": self._errors,
            "talib_available": TALIB_AVAILABLE,
            "birdeye_configured": bool(self.fetcher._birdeye_key),
        }


# ── Singleton ──────────────────────────────────────────────
_bridge_instance = None

def get_strategy_bridge(event_bus=None) -> StrategyBridge:
    """Get or create the singleton StrategyBridge instance."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = StrategyBridge(event_bus=event_bus)
        cprint("[STRATEGY_BRIDGE] Strategy Bridge initialized", "white", "on_green")
    return _bridge_instance
