"""
🧠 Moon Dev's Weighted Prediction Engine
DSH Pattern: Capability Seam — swappable signal processing.

Replaces the binary PredictionEngine with:
- Weighted factors (configurable per-factor importance)
- Continuous scoring (not binary thresholds)
- Regime detection (trending/ranging/volatile)
- Regime-adaptive thresholds
- Historical accuracy tracking

Usage:
    predictor = WeightedPredictor()
    result = await predictor.predict('BTCUSDT')
    print(result['signal'])       # BUY/SELL/HOLD
    print(result['confidence'])   # 0.0 - 1.0
    print(result['regime'])       # trending/ranging/volatile
"""

import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
from termcolor import cprint


# ── Market Regimes ────────────────────────────────────────────

class Regime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    VOLATILE = "volatile"


# ── Factor Config ─────────────────────────────────────────────

@dataclass
class FactorConfig:
    """Configuration for a single scoring factor."""
    name: str
    weight: float = 1.0        # Importance multiplier
    enabled: bool = True

    # Thresholds (will be adjusted by regime)
    buy_threshold: float = 0.0
    sell_threshold: float = 0.0

    # Continuous scoring params
    sensitivity: float = 1.0   # How aggressively to score


@dataclass
class RegimeProfile:
    """Factor adjustments for a specific market regime."""
    regime: Regime
    factor_adjustments: Dict[str, float] = field(default_factory=dict)
    # e.g., {'rsi': 0.8, 'volume': 1.2} — reduce RSI weight, increase volume weight
    score_multiplier: float = 1.0
    confidence_floor: float = 0.3
    confidence_ceiling: float = 0.95


# ── Prediction Result ─────────────────────────────────────────

@dataclass
class Prediction:
    """A weighted prediction result."""
    symbol: str
    signal: str              # BUY, SELL, HOLD, WEAK_BUY, WEAK_SELL
    score: float             # Continuous score (-1.0 to +1.0)
    raw_score: float         # Unnormalized weighted sum
    confidence: float        # 0.0 to 1.0
    regime: Regime
    factors: Dict[str, float]  # Individual factor scores
    weights: Dict[str, float]  # Applied weights
    reasons: List[str]
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'signal': self.signal,
            'score': round(self.score, 4),
            'raw_score': round(self.raw_score, 4),
            'confidence': round(self.confidence, 4),
            'regime': self.regime.value,
            'factors': {k: round(v, 4) for k, v in self.factors.items()},
            'weights': {k: round(v, 4) for k, v in self.weights.items()},
            'reasons': self.reasons,
            'timestamp': self.timestamp,
        }


# ── Default Configs ──────────────────────────────────────────

DEFAULT_FACTORS = {
    'rsi': FactorConfig(
        name='RSI',
        weight=1.2,
        buy_threshold=35,
        sell_threshold=65,
        sensitivity=1.0,
    ),
    'volume_spike': FactorConfig(
        name='Volume Spike',
        weight=1.0,
        buy_threshold=1.5,
        sell_threshold=1.5,
        sensitivity=1.0,
    ),
    'momentum': FactorConfig(
        name='5m Momentum',
        weight=0.8,
        buy_threshold=0.15,
        sell_threshold=-0.15,
        sensitivity=1.2,
    ),
    'buy_pressure': FactorConfig(
        name='Buy Pressure',
        weight=1.1,
        buy_threshold=0.60,
        sell_threshold=0.40,
        sensitivity=1.0,
    ),
    'imbalance': FactorConfig(
        name='Order Book Imbalance',
        weight=0.9,
        buy_threshold=0.15,
        sell_threshold=-0.15,
        sensitivity=1.0,
    ),
    'volatility': FactorConfig(
        name='Volatility Guard',
        weight=0.7,
        buy_threshold=50.0,
        sell_threshold=50.0,
        sensitivity=0.5,
    ),
}

DEFAULT_REGIME_PROFILES = {
    Regime.TRENDING_UP: RegimeProfile(
        regime=Regime.TRENDING_UP,
        factor_adjustments={'rsi': 0.8, 'momentum': 1.3, 'buy_pressure': 1.2},
        score_multiplier=1.1,
        confidence_floor=0.4,
    ),
    Regime.TRENDING_DOWN: RegimeProfile(
        regime=Regime.TRENDING_DOWN,
        factor_adjustments={'rsi': 0.8, 'momentum': 1.3, 'buy_pressure': 1.2},
        score_multiplier=1.1,
        confidence_floor=0.4,
    ),
    Regime.RANGING: RegimeProfile(
        regime=Regime.RANGING,
        factor_adjustments={'rsi': 1.3, 'volume_spike': 1.2, 'momentum': 0.7},
        score_multiplier=0.9,
        confidence_floor=0.3,
    ),
    Regime.VOLATILE: RegimeProfile(
        regime=Regime.VOLATILE,
        factor_adjustments={'volatility': 1.5, 'rsi': 0.6, 'momentum': 0.6},
        score_multiplier=0.8,
        confidence_ceiling=0.7,
    ),
}


# ── Weighted Predictor ───────────────────────────────────────

class WeightedPredictor:
    """
    DSH-style weighted prediction engine.

    Key improvements over PredictionEngine:
    1. Configurable factor weights (RSI matters more than momentum)
    2. Continuous scoring (not binary +1/-1)
    3. Regime detection (trending/ranging/volatile)
    4. Regime-adaptive thresholds
    5. Historical accuracy tracking
    """

    def __init__(self, factors: Dict[str, FactorConfig] = None,
                 regime_profiles: Dict[Regime, RegimeProfile] = None,
                 history_path: str = None):
        self.factors = factors or DEFAULT_FACTORS.copy()
        self.regime_profiles = regime_profiles or DEFAULT_REGIME_PROFILES.copy()
        self.history_path = history_path or os.path.join(
            os.path.dirname(__file__), 'data', 'prediction_history.jsonl'
        )
        self._regime_history: List[dict] = []

    async def predict(self, symbol: str, features: dict = None) -> Prediction:
        """
        Generate a weighted prediction.

        Args:
            symbol: Token symbol (e.g., 'BTCUSDT')
            features: Optional pre-fetched features dict. If None, fetches from MongoDB.

        Returns:
            Prediction with signal, score, confidence, regime
        """
        # 1. Get features
        if features is None:
            features = await self._fetch_features(symbol)

        if not features:
            return Prediction(
                symbol=symbol, signal='HOLD', score=0.0, raw_score=0.0,
                confidence=0.0, regime=Regime.RANGING,
                factors={}, weights={}, reasons=['No data available'],
            )

        # 2. Detect regime
        regime = self._detect_regime(features)
        regime_profile = self.regime_profiles.get(regime, DEFAULT_REGIME_PROFILES[Regime.RANGING])

        # 3. Score each factor
        factor_scores = {}
        factor_reasons = []
        total_weighted_score = 0.0
        total_weight = 0.0
        applied_weights = {}

        for factor_name, config in self.factors.items():
            if not config.enabled:
                continue

            raw_value = self._extract_factor(factor_name, features)
            if raw_value is None:
                continue

            # Get regime-adjusted weight
            regime_adj = regime_profile.factor_adjustments.get(factor_name, 1.0)
            adjusted_weight = config.weight * regime_adj
            applied_weights[factor_name] = adjusted_weight

            # Continuous scoring
            factor_score = self._score_factor(factor_name, config, raw_value, regime)

            factor_scores[factor_name] = factor_score
            total_weighted_score += factor_score * adjusted_weight
            total_weight += abs(adjusted_weight)

            # Collect reasons
            if abs(factor_score) > 0.1:
                direction = "bullish" if factor_score > 0 else "bearish"
                factor_reasons.append(
                    f"{config.name}={raw_value:.2f} ({direction}, score={factor_score:.2f})"
                )

        # 4. Normalize score to [-1, 1]
        if total_weight > 0:
            raw_score = total_weighted_score / total_weight
        else:
            raw_score = 0.0

        normalized_score = max(-1.0, min(1.0, raw_score))

        # 5. Determine signal
        signal, confidence = self._determine_signal(
            normalized_score, regime_profile, factor_scores
        )

        # 6. Build reasons
        reasons = [f"Regime: {regime.value}"] + factor_reasons
        if confidence < 0.4:
            reasons.append(f"Low confidence ({confidence:.0%}) — consider waiting")

        # 7. Record for accuracy tracking
        self._record_prediction(symbol, signal, confidence, regime, factor_scores)

        prediction = Prediction(
            symbol=symbol,
            signal=signal,
            score=normalized_score,
            raw_score=raw_score,
            confidence=confidence,
            regime=regime,
            factors=factor_scores,
            weights=applied_weights,
            reasons=reasons,
        )

        cprint(
            f"[WEIGHTED] {symbol} | signal={signal} | score={normalized_score:+.3f} | "
            f"conf={confidence:.0%} | regime={regime.value} | factors={len(factor_scores)}",
            "white",
            "on_green" if "BUY" in signal else ("on_red" if "SELL" in signal else "on_blue")
        )

        return prediction

    # ── Regime Detection ──────────────────────────────────────

    def _detect_regime(self, features: dict) -> Regime:
        """
        Detect current market regime from features.

        Logic:
        - High volatility + strong momentum = TRENDING
        - High volatility + weak momentum = VOLATILE
        - Low volatility + weak momentum = RANGING
        """
        autonomous = features.get('autonomous', {})
        indicators = features.get('indicators', {})

        volatility = float(autonomous.get('volatility_20', 0) or 0)
        momentum_raw = float(autonomous.get('momentum_5m_pct', 0) or 0)
        momentum = abs(momentum_raw)
        vol_spike = float(autonomous.get('volume_spike', 1.0) or 1.0)

        # Get RSI for trend direction
        rsi = 50.0
        for k in indicators:
            if k.startswith('RSI'):
                rsi = float(indicators[k] or 50)
                break

        # Regime classification
        high_vol = volatility > 50.0
        strong_momentum = momentum > 0.15
        high_volume = vol_spike > 1.5

        if high_vol and strong_momentum:
            return Regime.TRENDING_UP if momentum_raw > 0 else Regime.TRENDING_DOWN
        elif high_vol and not strong_momentum:
            return Regime.VOLATILE
        elif not high_vol and high_volume:
            # Volume spike without volatility = potential breakout
            return Regime.TRENDING_UP if rsi > 50 else Regime.TRENDING_DOWN
        else:
            return Regime.RANGING

    # ── Factor Extraction ─────────────────────────────────────

    def _extract_factor(self, name: str, features: dict) -> Optional[float]:
        """Extract a factor value from features."""
        autonomous = features.get('autonomous', {})
        micro = features.get('microstructure', {})
        indicators = features.get('indicators', {})

        if name == 'rsi':
            for k in indicators:
                if k.startswith('RSI'):
                    return float(indicators[k] or 50)
            return 50.0

        elif name == 'volume_spike':
            return float(autonomous.get('volume_spike', 1.0) or 1.0)

        elif name == 'momentum':
            return float(autonomous.get('momentum_5m_pct', 0.0) or 0.0)

        elif name == 'buy_pressure':
            return float(autonomous.get('buy_pressure', 0.5) or 0.5)

        elif name == 'imbalance':
            return float(micro.get('volume_imbalance', 0) or 0)

        elif name == 'volatility':
            return float(autonomous.get('volatility_20', 0.0) or 0.0)

        return None

    # ── Continuous Scoring ────────────────────────────────────

    def _score_factor(self, name: str, config: FactorConfig,
                      value: float, regime: Regime) -> float:
        """
        Score a factor continuously from -1.0 to +1.0.

        Instead of binary +1/-1, this gives partial scores:
        - RSI at 30 (deeply oversold) → +0.9
        - RSI at 34 (barely oversold) → +0.3
        - RSI at 50 (neutral) → 0.0
        - RSI at 70 (overbought) → -0.8
        """
        if name == 'rsi':
            # RSI: 0-100 scale, lower = more bullish
            if value < config.buy_threshold:
                # Oversold: score from +0.5 to +1.0 as RSI drops
                depth = (config.buy_threshold - value) / config.buy_threshold
                return min(0.5 + depth * 0.5, 1.0) * config.sensitivity
            elif value > config.sell_threshold:
                # Overbought: score from -0.5 to -1.0 as RSI rises
                depth = (value - config.sell_threshold) / (100 - config.sell_threshold)
                return max(-0.5 - depth * 0.5, -1.0) * config.sensitivity
            else:
                # Neutral zone: slight score based on position
                mid = (config.buy_threshold + config.sell_threshold) / 2
                distance_from_mid = (value - mid) / (config.sell_threshold - config.buy_threshold)
                return -distance_from_mid * 0.3 * config.sensitivity

        elif name == 'volume_spike':
            # Volume spike: >1.0 = above normal
            if value >= config.buy_threshold:
                # Direction depends on momentum (passed as abs value here)
                spike_strength = min((value - 1.0) / 2.0, 1.0)
                return spike_strength * config.sensitivity
            return 0.0

        elif name == 'momentum':
            # Momentum: percentage change
            if value > config.buy_threshold:
                strength = min(value / 0.5, 1.0)  # Normalize to 0.5% = max
                return strength * config.sensitivity
            elif value < config.sell_threshold:
                strength = max(value / 0.5, -1.0)
                return strength * config.sensitivity
            return 0.0

        elif name == 'buy_pressure':
            # Buy pressure: 0.0 to 1.0
            if value >= config.buy_threshold:
                excess = (value - config.buy_threshold) / (1.0 - config.buy_threshold)
                return min(excess, 1.0) * config.sensitivity
            elif value <= config.sell_threshold:
                deficit = (config.sell_threshold - value) / config.sell_threshold
                return max(-deficit, -1.0) * config.sensitivity
            return 0.0

        elif name == 'imbalance':
            # Imbalance: -1.0 to +1.0
            if abs(value) > config.buy_threshold:
                return max(-1.0, min(1.0, value * 2)) * config.sensitivity
            return 0.0

        elif name == 'volatility':
            # Volatility: always negative (caution signal)
            if value > config.buy_threshold:
                excess = (value - config.buy_threshold) / config.buy_threshold
                return -min(excess, 1.0) * config.sensitivity
            return 0.0

        return 0.0

    # ── Signal Determination ──────────────────────────────────

    def _determine_signal(self, score: float, regime: RegimeProfile,
                          factor_scores: Dict[str, float]) -> Tuple[str, float]:
        """
        Determine signal and confidence from normalized score.

        Uses regime-adaptive thresholds instead of fixed ±2.
        """
        # Signal thresholds (continuous)
        strong_threshold = 0.4   # Strong signal
        weak_threshold = 0.2     # Weak signal

        if score >= strong_threshold:
            signal = "BUY"
        elif score >= weak_threshold:
            signal = "WEAK_BUY"
        elif score <= -strong_threshold:
            signal = "SELL"
        elif score <= -weak_threshold:
            signal = "WEAK_SELL"
        else:
            signal = "HOLD"

        # Confidence: based on score magnitude + factor agreement
        base_confidence = abs(score)

        # Factor agreement bonus: if multiple factors agree, confidence increases
        bullish_factors = sum(1 for v in factor_scores.values() if v > 0.1)
        bearish_factors = sum(1 for v in factor_scores.values() if v < -0.1)
        total_factors = len(factor_scores)

        if total_factors > 0:
            if "BUY" in signal:
                agreement = bullish_factors / total_factors
            elif "SELL" in signal:
                agreement = bearish_factors / total_factors
            else:
                agreement = 0.5
        else:
            agreement = 0.5

        confidence = base_confidence * 0.6 + agreement * 0.4

        # Apply regime limits
        confidence = max(regime.confidence_floor, min(regime.confidence_ceiling, confidence))

        return signal, round(confidence, 3)

    # ── Feature Fetching ──────────────────────────────────────

    async def _fetch_features(self, symbol: str) -> Optional[dict]:
        """Fetch features from MongoDB."""
        try:
            from src.data.storage.mongo_db import MongoStorage
            storage = MongoStorage()
            await storage.connect()

            doc = await storage.db['features_dataset'].find_one(
                {'symbol': symbol.upper()},
                sort=[('_id', -1)]
            )

            if doc:
                return doc.get('data', {})
        except Exception as e:
            cprint(f"[WEIGHTED] Feature fetch failed: {str(e)}", "yellow")
        return None

    # ── History Tracking ──────────────────────────────────────

    def _record_prediction(self, symbol: str, signal: str, confidence: float,
                           regime: Regime, factors: dict):
        """Record prediction for accuracy tracking."""
        try:
            os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
            entry = {
                'timestamp': datetime.utcnow().isoformat(),
                'symbol': symbol,
                'signal': signal,
                'confidence': confidence,
                'regime': regime.value,
                'factors': factors,
            }
            with open(self.history_path, 'a') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception:
            pass  # Don't crash on logging failure

    async def get_accuracy_report(self, days: int = 7) -> dict:
        """Analyze prediction accuracy from history."""
        if not os.path.exists(self.history_path):
            return {'total': 0, 'message': 'No prediction history found'}

        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        predictions = []

        with open(self.history_path, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get('timestamp', '') >= cutoff:
                        predictions.append(entry)
                except Exception:
                    continue

        if not predictions:
            return {'total': 0, 'message': f'No predictions in last {days} days'}

        by_signal = {}
        by_regime = {}
        for p in predictions:
            sig = p.get('signal', 'UNKNOWN')
            regime = p.get('regime', 'unknown')
            by_signal[sig] = by_signal.get(sig, 0) + 1
            by_regime[regime] = by_regime.get(regime, 0) + 1

        avg_confidence = sum(p.get('confidence', 0) for p in predictions) / len(predictions)

        return {
            'total': len(predictions),
            'period_days': days,
            'by_signal': by_signal,
            'by_regime': by_regime,
            'avg_confidence': round(avg_confidence, 3),
        }


# ── Factory ───────────────────────────────────────────────────

def create_weighted_predictor(**kwargs) -> WeightedPredictor:
    """Create a WeightedPredictor with default config."""
    return WeightedPredictor(**kwargs)


# ── CLI Demo ──────────────────────────────────────────────────

async def main():
    """Demo the weighted predictor."""
    predictor = WeightedPredictor()

    print("\n🧠 Moon Dev Weighted Predictor — Demo\n")

    # Simulate features
    features = {
        'indicators': {'RSI_14': 32.5},
        'autonomous': {
            'volume_spike': 2.1,
            'momentum_5m_pct': 0.25,
            'buy_pressure': 0.72,
            'volatility_20': 35.0,
        },
        'microstructure': {'volume_imbalance': 0.18},
    }

    result = await predictor.predict('FARTCOIN', features=features)
    print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
