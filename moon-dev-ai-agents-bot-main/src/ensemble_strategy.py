"""
🎵 Moon Dev's Ensemble Strategy — Multiple Backends with Learned Weights
Combines signals from multiple strategies, weighted by historical performance.
"""

from dataclasses import dataclass, field
import asyncio
from typing import Dict, List, Optional, Callable
from enum import Enum


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class StrategySignal:
    name: str
    signal: Signal
    confidence: float
    weight: float = 1.0


@dataclass
class EnsembleResult:
    signal: Signal
    confidence: float
    weighted_score: float
    contributing_strategies: int
    details: Dict[str, float] = field(default_factory=dict)

    def to_dict(self):
        return {
            'signal': self.signal.value, 'confidence': round(self.confidence, 3),
            'weighted_score': round(self.weighted_score, 4),
            'contributing_strategies': self.contributing_strategies,
            'details': {k: round(v, 3) for k, v in self.details.items()},
        }


class EnsembleStrategy:
    """Combines multiple strategy signals using weighted voting."""

    def __init__(self):
        self._strategies: Dict[str, Callable] = {}
        self._weights: Dict[str, float] = {}
        self._history: List[Dict] = []

    def register(self, name: str, fn: Callable, weight: float = 1.0):
        self._strategies[name] = fn
        self._weights[name] = weight

    def set_weight(self, name: str, weight: float):
        if name in self._weights:
            self._weights[name] = max(0.0, min(weight, 5.0))

    async def evaluate(self, features: dict) -> EnsembleResult:
        signals = []
        for name, fn in self._strategies.items():
            try:
                sig = await fn(features) if asyncio.iscoroutinefunction(fn) else fn(features)
                if isinstance(sig, StrategySignal):
                    signals.append(sig)
                elif isinstance(sig, dict):
                    signals.append(StrategySignal(
                        name=name, signal=Signal(sig.get('signal', 'HOLD')),
                        confidence=sig.get('confidence', 0.5),
                        weight=self._weights.get(name, 1.0),
                    ))
            except Exception:
                continue

        if not signals:
            return EnsembleResult(Signal.HOLD, 0.0, 0.0, 0)

        buy_score = sum(s.confidence * s.weight for s in signals if s.signal == Signal.BUY)
        sell_score = sum(s.confidence * s.weight for s in signals if s.signal == Signal.SELL)
        total_weight = sum(s.weight for s in signals)

        net_score = (buy_score - sell_score) / max(total_weight, 0.01)
        confidence = min(abs(net_score), 1.0)
        details = {s.name: s.confidence * (1 if s.signal == Signal.BUY else -1) for s in signals}

        if net_score > 0.2:
            signal = Signal.BUY
        elif net_score < -0.2:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD

        return EnsembleResult(signal, confidence, net_score, len(signals), details)
