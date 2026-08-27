"""
🌙 Moon Dev's Prediction Engine v2
Multi-factor signal engine using autonomous Binance-derived metrics
Built with love by Moon Dev 🚀
"""

import asyncio
from termcolor import cprint
from src.data.storage.mongo_db import MongoStorage


# ── Signal Thresholds ─────────────────────────────────────────────
RSI_OVERSOLD         = 35       # RSI below this → potential buy zone
RSI_OVERBOUGHT       = 65       # RSI above this → potential sell zone
VOLUME_SPIKE_MIN     = 1.5      # 1.5x normal volume = confirmed spike
MOMENTUM_BULL_PCT    = 0.15     # +0.15% over 5m = bullish momentum
MOMENTUM_BEAR_PCT    = -0.15    # -0.15% over 5m = bearish momentum
BUY_PRESSURE_BULL    = 0.60     # >60% buyers = strong demand
BUY_PRESSURE_BEAR    = 0.40     # <40% buyers = strong sell pressure
VOLATILITY_HIGH      = 50.0     # USD — high vol = don't chase


class PredictionEngine:
    """
    Multi-factor prediction engine.
    Scores BUY/SELL/HOLD from:
      - RSI (Technical)
      - Volume Spike (Autonomous)
      - 5-min Momentum (Autonomous)
      - Buy/Sell Pressure (Autonomous)
      - Volatility Guard (Autonomous)
      - Order Book Imbalance (Microstructure)
    """

    def __init__(self):
        self.storage = MongoStorage()
        cprint("[PREDICTION] Moon Dev's Prediction Engine v2 initialized", "white", "on_blue")

    async def get_prediction(self, symbol: str) -> dict:
        """
        Fetch latest features and generate a multi-factor trade signal.
        Returns signal dict with score, confidence, and reason breakdown.
        """
        try:
            await self.storage.connect()

            # 1. Fetch latest engineered features — sort by _id (insertion order)
            feature_doc = await self.storage.db["features_dataset"].find_one(
                {"symbol": symbol.upper()},
                sort=[("_id", -1)]
            )

            if not feature_doc:
                return {"signal": "NEUTRAL", "reason": "No features found", "score": 0}

            features    = feature_doc["data"]
            indicators  = features.get("indicators", {})
            micro       = features.get("microstructure", {})
            autonomous  = features.get("autonomous", {})

            # ── Extract Signals ───────────────────────────────────────

            # Technical: RSI
            rsi = 50.0
            for k in indicators:
                if k.startswith("RSI"):
                    rsi = float(indicators[k] or 50)
                    break

            # Microstructure: Order book imbalance
            imbalance = float(micro.get("volume_imbalance", 0) or 0)

            # Autonomous: self-computed Binance metrics
            vol_spike    = float(autonomous.get("volume_spike", 1.0) or 1.0)
            mom_pct      = float(autonomous.get("momentum_5m_pct", 0.0) or 0.0)
            buy_pressure = float(autonomous.get("buy_pressure", 0.5) or 0.5)
            volatility   = float(autonomous.get("volatility_20", 0.0) or 0.0)

            # ── Multi-Factor Scoring ──────────────────────────────────
            # Each factor contributes +1 (bullish), -1 (bearish), or 0 (neutral)
            score     = 0
            reasons   = []

            # 1. RSI
            if rsi < RSI_OVERSOLD:
                score += 1; reasons.append(f"RSI={rsi:.1f} oversold")
            elif rsi > RSI_OVERBOUGHT:
                score -= 1; reasons.append(f"RSI={rsi:.1f} overbought")

            # 2. Volume spike (confirm move)
            if vol_spike >= VOLUME_SPIKE_MIN:
                spike_label = f"vol spike {vol_spike:.1f}x"
                if mom_pct >= 0:
                    score += 1; reasons.append(f"{spike_label} with up move")
                else:
                    score -= 1; reasons.append(f"{spike_label} with down move")

            # 3. 5-min momentum
            if mom_pct >= MOMENTUM_BULL_PCT:
                score += 1; reasons.append(f"momentum +{mom_pct:.2f}%")
            elif mom_pct <= MOMENTUM_BEAR_PCT:
                score -= 1; reasons.append(f"momentum {mom_pct:.2f}%")

            # 4. Buy/sell pressure
            if buy_pressure >= BUY_PRESSURE_BULL:
                score += 1; reasons.append(f"buy pressure {buy_pressure:.0%}")
            elif buy_pressure <= BUY_PRESSURE_BEAR:
                score -= 1; reasons.append(f"sell pressure {1-buy_pressure:.0%}")

            # 5. Order book imbalance confirmation
            if imbalance > 0.15:
                score += 1; reasons.append(f"book imbalance +{imbalance:.2f}")
            elif imbalance < -0.15:
                score -= 1; reasons.append(f"book imbalance {imbalance:.2f}")

            # 6. Volatility guard — reduce confidence during high vol
            vol_penalty = volatility > VOLATILITY_HIGH
            if vol_penalty:
                reasons.append(f"high volatility ({volatility:.1f}) → caution")

            # ── Signal Decision ───────────────────────────────────────
            # Score ≥ +2 = BUY, ≤ -2 = SELL, else HOLD
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
            raw_factors  = abs(score)
            confidence   = min(0.5 + raw_factors * 0.1, 0.95)
            if vol_penalty:
                confidence *= 0.75  # Reduce confidence in high vol

            cprint(
                f"[PREDICTION] {symbol} | score={score:+d} | signal={signal} | "
                f"conf={confidence:.0%} | RSI={rsi:.1f} | vol={vol_spike:.1f}x | "
                f"mom={mom_pct:+.2f}% | buy={buy_pressure:.0%}",
                "white", "on_green" if "BUY" in signal else ("on_red" if "SELL" in signal else "on_blue")
            )

            return {
                "symbol":     symbol.upper(),
                "signal":     signal,
                "score":      score,
                "confidence": round(confidence, 3),
                "reasons":    reasons,
                "factors": {
                    "rsi":          rsi,
                    "volume_spike": vol_spike,
                    "momentum_pct": mom_pct,
                    "buy_pressure": buy_pressure,
                    "imbalance":    imbalance,
                    "volatility":   volatility,
                },
                "timestamp": feature_doc["timestamp"]
            }

        except Exception as e:
            cprint(f"[ERROR] Prediction failed: {str(e)}", "white", "on_red")
            return {"signal": "ERROR", "reason": str(e), "score": 0}


if __name__ == "__main__":
    engine = PredictionEngine()
    import json
    result = asyncio.run(engine.get_prediction("BTCUSDT"))
    print(json.dumps({k: str(v) for k, v in result.items()}, indent=2))
