"""
Moon Dev PredictionEngine v2 — Multi-Factor Signal Engine
Adapted from System 1 (MongoDB) to System 2 (PostgreSQL/OHLCV).

Scores BUY/SELL/HOLD from:
  - RSI (Technical)
  - Volume Spike (Autonomous)
  - 5-min Momentum (Autonomous)
  - Buy/Sell Pressure (Autonomous)
  - Volatility Guard (Autonomous)
  - Order Book Imbalance (from DexScreener data)
"""

import time
from datetime import datetime, timezone
from typing import Dict, Optional
from termcolor import cprint


# ── Signal Thresholds ─────────────────────────────────────────────
RSI_OVERSOLD         = 35       # RSI below this → potential buy zone
RSI_OVERBOUGHT       = 65       # RSI above this → potential sell zone
VOLUME_SPIKE_MIN     = 1.5      # 1.5x normal volume = confirmed spike
MOMENTUM_BULL_PCT    = 0.15     # +0.15% over 5m = bullish momentum
MOMENTUM_BEAR_PCT    = -0.15    # -0.15% over 5m = bearish momentum
BUY_PRESSURE_BULL    = 0.60     # >60% buyers = strong demand
BUY_PRESSURE_BEAR    = 0.40     # <40% buyers = strong sell pressure
VOLATILITY_HIGH      = 50.0     # USD — high vol = don't chase


class PredictionEngineV2:
    """
    Multi-factor prediction engine using OHLCV data.
    
    Scores tokens from -5 to +5:
      >= +2 = BUY
      <= -2 = SELL
      else  = HOLD
    
    Each factor contributes +1 (bullish), -1 (bearish), or 0 (neutral).
    """

    def __init__(self):
        self._predictions: Dict[str, dict] = {}
        cprint("[PREDICTION] PredictionEngine v2 initialized (OHLCV-based)", "white", "on_blue")

    def get_prediction(self, token_address: str, indicators: Dict = None,
                       candidate_metrics: Dict = None) -> dict:
        """
        Generate a multi-factor prediction from indicators and metrics.
        
        Args:
            token_address: Solana token mint address
            indicators: Dict from IndicatorEngine.calculate() (RSI, MACD, etc.)
            candidate_metrics: Dict from TokenCandidate.to_dict() (volume, txns, etc.)
        
        Returns:
            dict with signal, score, confidence, reasons, factors
        """
        indicators = indicators or {}
        candidate_metrics = candidate_metrics or {}

        # ── Extract Factors ───────────────────────────────────────

        # Technical: RSI
        rsi = float(indicators.get("rsi", 50))

        # Autonomous: self-computed metrics from OHLCV
        vol_spike = float(indicators.get("volume_ratio", 1.0))
        mom_pct = float(indicators.get("momentum_5", 0.0))

        # Buy/sell pressure from candidate metrics
        buys_1h = float(candidate_metrics.get("txns_1h_buys", 0))
        sells_1h = float(candidate_metrics.get("txns_1h_sells", 0))
        total_txns = buys_1h + sells_1h
        buy_pressure = buys_1h / total_txns if total_txns > 0 else 0.5

        # Volatility from ATR
        volatility = float(indicators.get("atr_pct", 0.0)) * 100  # Convert to USD-like scale

        # Order book imbalance from candidate metrics
        pc_1h = float(candidate_metrics.get("price_change_1h", 0.0))
        pc_24h = float(candidate_metrics.get("price_change_24h", 0.0))
        imbalance = (pc_1h / 100) if pc_1h != 0 else 0.0

        # ── Multi-Factor Scoring ──────────────────────────────────
        score = 0
        reasons = []

        # 1. RSI
        if rsi < RSI_OVERSOLD:
            score += 1
            reasons.append(f"RSI={rsi:.1f} oversold")
        elif rsi > RSI_OVERBOUGHT:
            score -= 1
            reasons.append(f"RSI={rsi:.1f} overbought")

        # 2. Volume spike (confirm move)
        if vol_spike >= VOLUME_SPIKE_MIN:
            spike_label = f"vol spike {vol_spike:.1f}x"
            if mom_pct >= 0:
                score += 1
                reasons.append(f"{spike_label} with up move")
            else:
                score -= 1
                reasons.append(f"{spike_label} with down move")

        # 3. 5-min momentum
        if mom_pct >= MOMENTUM_BULL_PCT:
            score += 1
            reasons.append(f"momentum +{mom_pct:.2f}%")
        elif mom_pct <= MOMENTUM_BEAR_PCT:
            score -= 1
            reasons.append(f"momentum {mom_pct:.2f}%")

        # 4. Buy/sell pressure
        if buy_pressure >= BUY_PRESSURE_BULL:
            score += 1
            reasons.append(f"buy pressure {buy_pressure:.0%}")
        elif buy_pressure <= BUY_PRESSURE_BEAR:
            score -= 1
            reasons.append(f"sell pressure {1-buy_pressure:.0%}")

        # 5. Order book imbalance confirmation
        if imbalance > 0.15:
            score += 1
            reasons.append(f"book imbalance +{imbalance:.2f}")
        elif imbalance < -0.15:
            score -= 1
            reasons.append(f"book imbalance {imbalance:.2f}")

        # 6. Volatility guard — reduce confidence during high vol
        vol_penalty = volatility > VOLATILITY_HIGH
        if vol_penalty:
            reasons.append(f"high volatility ({volatility:.1f}) → caution")

        # ── Signal Decision ───────────────────────────────────────
        if score >= 2 and not vol_penalty:
            signal = "BUY"
        elif score >= 2 and vol_penalty:
            signal = "WEAK_BUY"
        elif score <= -2 and not vol_penalty:
            signal = "SELL"
        elif score <= -2 and vol_penalty:
            signal = "WEAK_SELL"
        else:
            signal = "HOLD"

        # Confidence: 0.5 base + 0.1 per confirming factor, capped at 0.95
        raw_factors = abs(score)
        confidence = min(0.5 + raw_factors * 0.1, 0.95)
        if vol_penalty:
            confidence *= 0.75

        result = {
            "symbol": token_address[:8],
            "signal": signal,
            "score": score,
            "confidence": round(confidence, 3),
            "reasons": reasons,
            "factors": {
                "rsi": rsi,
                "volume_spike": vol_spike,
                "momentum_pct": mom_pct,
                "buy_pressure": buy_pressure,
                "imbalance": imbalance,
                "volatility": volatility,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Cache
        self._predictions[token_address] = result

        return result

    def get_cached_prediction(self, token_address: str) -> Optional[dict]:
        """Get cached prediction for a token."""
        return self._predictions.get(token_address)


# ── Singleton ──────────────────────────────────────────────
_prediction_instance = None

def get_prediction_engine() -> PredictionEngineV2:
    """Get or create the singleton PredictionEngineV2 instance."""
    global _prediction_instance
    if _prediction_instance is None:
        _prediction_instance = PredictionEngineV2()
    return _prediction_instance
