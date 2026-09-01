"""
Moon Dev Feature Engineer — Microstructure Features
Adapted from System 1 (MongoDB) to System 2 (PostgreSQL/OHLCV).

Calculates autonomous features from OHLCV data:
- Volume spike detection
- Momentum analysis
- Buy/sell pressure estimation
- Volatility metrics
- Microstructure features
"""

import numpy as np
from typing import Dict, Optional
from termcolor import cprint


class FeatureEngineer:
    """
    Calculate microstructure and autonomous features from OHLCV data.
    
    These features feed into the PredictionEngine v2 for multi-factor scoring.
    """

    def __init__(self):
        cprint("[FEATURE] Feature Engineer initialized", "white", "on_blue")

    def calculate_features(self, indicators: Dict, candidate_metrics: Dict = None) -> Dict:
        """
        Calculate all autonomous features from indicators and metrics.
        
        Args:
            indicators: Dict from IndicatorEngine.calculate() (RSI, MACD, etc.)
            candidate_metrics: Dict from TokenCandidate.to_dict()
        
        Returns:
            Dict with autonomous and microstructure features
        """
        candidate_metrics = candidate_metrics or {}
        
        features = {
            "autonomous": self._calculate_autonomous(indicators, candidate_metrics),
            "microstructure": self._calculate_microstructure(indicators, candidate_metrics),
            "indicators": indicators,
        }
        
        return features

    def _calculate_autonomous(self, indicators: Dict, metrics: Dict) -> Dict:
        """Calculate self-computed autonomous features."""
        
        # Volume spike
        volume_ratio = float(indicators.get("volume_ratio", 1.0))
        
        # Momentum
        momentum_5 = float(indicators.get("momentum_5", 0.0))
        momentum_10 = float(indicators.get("momentum_10", 0.0))
        
        # Buy/sell pressure from transaction counts
        buys_1h = float(metrics.get("txns_1h_buys", 0))
        sells_1h = float(metrics.get("txns_1h_sells", 0))
        total_txns = buys_1h + sells_1h
        buy_pressure = buys_1h / total_txns if total_txns > 0 else 0.5
        sell_pressure = sells_1h / total_txns if total_txns > 0 else 0.5
        
        # Volatility from ATR
        atr_pct = float(indicators.get("atr_pct", 0.0))
        
        return {
            "volume_spike": round(volume_ratio, 3),
            "momentum_5m_pct": round(momentum_5, 4),
            "momentum_10m_pct": round(momentum_10, 4),
            "buy_pressure": round(buy_pressure, 3),
            "sell_pressure": round(sell_pressure, 3),
            "volatility_20": round(atr_pct * 100, 2),
        }

    def _calculate_microstructure(self, indicators: Dict, metrics: Dict) -> Dict:
        """Calculate microstructure features."""
        
        # Price changes
        pc_1h = float(metrics.get("price_change_1h", 0.0))
        pc_24h = float(metrics.get("price_change_24h", 0.0))
        
        # Volume imbalance (proxy for order book imbalance)
        buys_1h = float(metrics.get("txns_1h_buys", 0))
        sells_1h = float(metrics.get("txns_1h_sells", 0))
        total = buys_1h + sells_1h
        volume_imbalance = (buys_1h - sells_1h) / total if total > 0 else 0.0
        
        # Spread estimation (from price impact)
        bb_width = float(indicators.get("bb_width", 0.0))
        
        return {
            "price_change_1h": round(pc_1h, 2),
            "price_change_24h": round(pc_24h, 2),
            "volume_imbalance": round(volume_imbalance, 3),
            "bb_width": round(bb_width, 4),
        }


# ── Singleton ──────────────────────────────────────────────
_feature_instance = None

def get_feature_engineer() -> FeatureEngineer:
    """Get or create the singleton FeatureEngineer instance."""
    global _feature_instance
    if _feature_instance is None:
        _feature_instance = FeatureEngineer()
    return _feature_instance
