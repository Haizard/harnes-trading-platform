"""
🗜️ Moon Dev's LLM Context Compactor
DSH Pattern: Compaction — reduce context size before sending to LLM.

Instead of dumping 1000 candles into the prompt, summarize them.
LLMs perform BETTER with focused, compact context.

Usage:
    compactor = ContextCompactor()
    compact = compactor.compact_ohlcv(df, max_candles=20)
    prompt = compactor.build_compact_prompt(token, features, compact)
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Any
from dataclasses import dataclass


@dataclass
class CompactSummary:
    """A compressed summary of market data."""
    symbol: str
    timeframe: str
    candle_count: int
    # Price summary
    current_price: float
    high_24h: float
    low_24h: float
    change_24h_pct: float
    # Volume summary
    avg_volume: float
    volume_trend: str  # 'increasing', 'decreasing', 'stable'
    # Trend summary
    trend: str  # 'bullish', 'bearish', 'ranging'
    trend_strength: float  # 0.0 to 1.0
    # Key levels
    support: float
    resistance: float
    # Indicators (compact)
    rsi: float
    macd_signal: str  # 'bullish', 'bearish', 'neutral'

    def to_prompt_text(self) -> str:
        """Convert to compact text for LLM prompt."""
        return (
            f"📊 {self.symbol} ({self.timeframe}):\n"
            f"  Price: ${self.current_price:.6f} | 24h: {self.change_24h_pct:+.2f}%\n"
            f"  Range: ${self.low_24h:.6f} - ${self.high_24h:.6f}\n"
            f"  Volume: avg={self.avg_volume:.0f} trend={self.volume_trend}\n"
            f"  Trend: {self.trend} (strength={self.trend_strength:.0%})\n"
            f"  Support: ${self.support:.6f} | Resistance: ${self.resistance:.6f}\n"
            f"  RSI: {self.rsi:.1f} | MACD: {self.macd_signal}\n"
            f"  Candles analyzed: {self.candle_count}"
        )


class ContextCompactor:
    """
    Reduces market data to compact summaries for LLM prompts.

    Instead of:
      "Here are 1000 OHLCV candles: [50KB of data]"

    Sends:
      "Price: $0.0042 (+3.2%) | Trend: bullish | RSI: 35 | Volume: increasing"
    """

    def __init__(self, max_candles: int = 50, summary_candles: int = 20):
        self.max_candles = max_candles
        self.summary_candles = summary_candles

    def compact_ohlcv(self, df: pd.DataFrame, symbol: str = "UNKNOWN") -> Optional[CompactSummary]:
        """
        Compress OHLCV data into a compact summary.

        Takes a DataFrame with columns: open, high, low, close, volume
        Returns a CompactSummary with key metrics.
        """
        if df is None or df.empty or len(df) < 2:
            return None

        try:
            # Ensure numeric columns
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # Take last N candles
            recent = df.tail(self.summary_candles)

            # Price metrics
            current_price = float(recent['close'].iloc[-1])
            high_24h = float(recent['high'].max())
            low_24h = float(recent['low'].min())
            first_price = float(recent['close'].iloc[0])
            change_24h_pct = ((current_price - first_price) / first_price * 100) if first_price > 0 else 0

            # Volume metrics
            avg_volume = float(recent['volume'].mean())
            vol_first_half = recent['volume'].iloc[:len(recent)//2].mean()
            vol_second_half = recent['volume'].iloc[len(recent)//2:].mean()
            if vol_first_half > 0:
                vol_ratio = vol_second_half / vol_first_half
                if vol_ratio > 1.2:
                    volume_trend = 'increasing'
                elif vol_ratio < 0.8:
                    volume_trend = 'decreasing'
                else:
                    volume_trend = 'stable'
            else:
                volume_trend = 'stable'

            # Trend detection (simple: compare first half avg to second half avg)
            mid = len(recent) // 2
            first_half_avg = recent['close'].iloc[:mid].mean()
            second_half_avg = recent['close'].iloc[mid:].mean()
            if first_half_avg > 0:
                trend_pct = (second_half_avg - first_half_avg) / first_half_avg
                if trend_pct > 0.02:
                    trend = 'bullish'
                    trend_strength = min(abs(trend_pct) * 5, 1.0)
                elif trend_pct < -0.02:
                    trend = 'bearish'
                    trend_strength = min(abs(trend_pct) * 5, 1.0)
                else:
                    trend = 'ranging'
                    trend_strength = 1.0 - abs(trend_pct) * 10
            else:
                trend = 'ranging'
                trend_strength = 0.5

            # Support/Resistance (simple: recent lows/highs)
            support = float(recent['low'].quantile(0.1))
            resistance = float(recent['high'].quantile(0.9))

            # RSI (simple calculation)
            rsi = self._calc_rsi(recent['close'], period=min(14, len(recent)-1))

            # MACD signal (simple)
            if len(recent) >= 12:
                ema12 = recent['close'].ewm(span=12).mean()
                ema26 = recent['close'].ewm(span=min(26, len(recent))).mean()
                macd_line = ema12 - ema26
                signal_line = macd_line.ewm(span=9).mean()
                if macd_line.iloc[-1] > signal_line.iloc[-1]:
                    macd_signal = 'bullish'
                elif macd_line.iloc[-1] < signal_line.iloc[-1]:
                    macd_signal = 'bearish'
                else:
                    macd_signal = 'neutral'
            else:
                macd_signal = 'neutral'

            return CompactSummary(
                symbol=symbol,
                timeframe='mixed',
                candle_count=len(df),
                current_price=current_price,
                high_24h=high_24h,
                low_24h=low_24h,
                change_24h_pct=round(change_24h_pct, 2),
                avg_volume=avg_volume,
                volume_trend=volume_trend,
                trend=trend,
                trend_strength=round(trend_strength, 3),
                support=support,
                resistance=resistance,
                rsi=round(rsi, 1),
                macd_signal=macd_signal,
            )

        except Exception:
            return None

    def build_compact_prompt(self, token: str, features: dict = None,
                            compact: CompactSummary = None) -> str:
        """
        Build a compact LLM prompt with summarized data.

        Instead of dumping raw OHLCV, provides a focused summary.
        """
        parts = [f"Trading analysis for {token[:12]}..."]

        if compact:
            parts.append(compact.to_prompt_text())

        if features:
            # Add key features in compact form
            pred = features.get('prediction_signal', {})
            if pred:
                parts.append(
                    f"  Signal: {pred.get('signal', 'N/A')} "
                    f"(conf={pred.get('confidence', 0):.0%}, "
                    f"score={pred.get('score', 0):+d})"
                )

            auto = features.get('autonomous', {})
            if auto:
                parts.append(
                    f"  Autonomous: vol_spike={auto.get('volume_spike', 1):.1f}x "
                    f"momentum={auto.get('momentum_5m_pct', 0):+.2f}% "
                    f"buy_pressure={auto.get('buy_pressure', 0.5):.0%}"
                )

        parts.append("\nMake a BUY, SELL, or NOTHING decision. Be concise.")
        return '\n'.join(parts)

    def _calc_rsi(self, prices: pd.Series, period: int = 14) -> float:
        """Calculate RSI."""
        if len(prices) < period + 1:
            return 50.0

        deltas = prices.diff()
        gain = deltas.where(deltas > 0, 0).rolling(window=period).mean()
        loss = (-deltas.where(deltas < 0, 0)).rolling(window=period).mean()

        if loss.iloc[-1] == 0:
            return 100.0

        rs = gain.iloc[-1] / loss.iloc[-1]
        rsi = 100 - (100 / (1 + rs))
        return float(rsi) if not np.isnan(rsi) else 50.0


# ── CLI Demo ──────────────────────────────────────────────────

def main():
    """Demo the context compactor."""
    compactor = ContextCompactor()

    # Create sample data
    import numpy as np
    np.random.seed(42)
    n = 100
    prices = 0.004 + np.random.randn(n).cumsum() * 0.0001
    df = pd.DataFrame({
        'open': prices + np.random.randn(n) * 0.00001,
        'high': prices + abs(np.random.randn(n) * 0.00002),
        'low': prices - abs(np.random.randn(n) * 0.00002),
        'close': prices,
        'volume': np.random.randint(1000, 10000, n),
    })

    print("\n🗜️ Moon Dev Context Compactor — Demo\n")
    print(f"Original: {len(df)} candles")
    summary = compactor.compact_ohlcv(df, symbol="FARTCOIN")
    if summary:
        print(f"\n{summary.to_prompt_text()}")


if __name__ == "__main__":
    main()
