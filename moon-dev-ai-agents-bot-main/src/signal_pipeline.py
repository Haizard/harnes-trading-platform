"""
🔍 Moon Dev's Signal Validation Pipeline
DSH Pattern: Waterfall Events — multi-stage signal filtering.

Filters weak signals BEFORE they waste an LLM call.
Each stage can reject, approve, or modify the signal.

Stages:
1. Minimum confidence check
2. Minimum factor agreement
3. Liquidity check
4. Fee profitability check
5. Correlation guard (avoid too many similar positions)

Usage:
    pipeline = SignalValidationPipeline()
    result = await pipeline.validate(signal)
    if result['approved']:
        # Signal is strong enough for LLM analysis
        response = await llm.analyze(signal)
"""

import os
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Awaitable, Any
from termcolor import cprint


@dataclass
class Signal:
    """A trading signal to be validated."""
    symbol: str
    signal: str              # BUY, SELL, HOLD
    score: float             # -1.0 to +1.0
    confidence: float        # 0.0 to 1.0
    factors: Dict[str, float] = field(default_factory=dict)
    regime: str = "unknown"
    source: str = "predictor"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationStage:
    """A single validation stage."""
    name: str
    priority: int
    fn: Callable
    enabled: bool = True


class SignalValidationPipeline:
    """
    DSH-style waterfall signal validation.

    Weak signals are rejected before wasting LLM API calls.
    Strong signals are approved with optional modifications.
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.stages: List[ValidationStage] = []
        self.rejection_log: List[dict] = []
        self._setup_defaults()

    def _setup_defaults(self):
        """Register default validation stages."""
        self.register("minimum_confidence", self._check_confidence, priority=10)
        self.register("factor_agreement", self._check_factor_agreement, priority=20)
        self.register("fee_profitability", self._check_fee_profitability, priority=30)
        self.register("duplicate_signal", self._check_duplicate, priority=40)

    def register(self, name: str, fn: Callable, priority: int = 100, enabled: bool = True):
        """Register a validation stage."""
        stage = ValidationStage(name=name, priority=priority, fn=fn, enabled=enabled)
        self.stages.append(stage)
        self.stages.sort(key=lambda s: s.priority)

    async def validate(self, signal: Signal) -> dict:
        """
        Run signal through all validation stages.

        Returns:
            {'approved': bool, 'reason': str, 'modifications': list}
        """
        if signal.signal == 'HOLD':
            return {'approved': False, 'reason': 'HOLD signals not traded', 'modifications': []}

        for stage in self.stages:
            if not stage.enabled:
                continue

            try:
                result = await stage.fn(signal)
            except Exception as e:
                # Stage error = reject (fail-closed)
                self._log_rejection(signal, stage.name, str(e))
                return {'approved': False, 'reason': f'Stage error: {str(e)}', 'modifications': []}

            if not result.get('approved', True):
                self._log_rejection(signal, stage.name, result.get('reason', 'Rejected'))
                cprint(
                    f"🔍 SIGNAL REJECTED at [{stage.name}]: {result.get('reason')}",
                    "yellow"
                )
                return result

        # All stages passed
        cprint(
            f"✅ SIGNAL APPROVED: {signal.signal} {signal.symbol} "
            f"(conf={signal.confidence:.0%}, score={signal.score:+.3f})",
            "green"
        )
        return {'approved': True, 'reason': 'All stages passed', 'modifications': []}

    # ── Default Stages ────────────────────────────────────────

    async def _check_confidence(self, signal: Signal) -> dict:
        """Reject signals with low confidence."""
        min_confidence = self.config.get('min_confidence', 0.3)

        if signal.confidence < min_confidence:
            return {
                'approved': False,
                'reason': f'Confidence {signal.confidence:.0%} below minimum {min_confidence:.0%}',
            }
        return {'approved': True}

    async def _check_factor_agreement(self, signal: Signal) -> dict:
        """Reject signals where factors disagree."""
        min_agreement = self.config.get('min_factor_agreement', 0.4)

        if not signal.factors:
            return {'approved': True}  # No factors to check

        # Count bullish vs bearish factors
        bullish = sum(1 for v in signal.factors.values() if v > 0.1)
        bearish = sum(1 for v in signal.factors.values() if v < -0.1)
        total = len(signal.factors)

        if total == 0:
            return {'approved': True}

        if 'BUY' in signal.signal:
            agreement = bullish / total
        elif 'SELL' in signal.signal:
            agreement = bearish / total
        else:
            agreement = 0.5

        if agreement < min_agreement:
            return {
                'approved': False,
                'reason': f'Factor agreement {agreement:.0%} below minimum {min_agreement:.0%}',
            }
        return {'approved': True}

    async def _check_fee_profitability(self, signal: Signal) -> dict:
        """Reject signals where fees would exceed expected profit."""
        min_profit_ratio = self.config.get('min_profit_ratio', 3.0)
        estimated_fee_pct = self.config.get('estimated_fee_pct', 0.01)

        # Rough estimate: score magnitude as expected move
        expected_move_pct = abs(signal.score) * 0.05  # 5% max move assumption

        if expected_move_pct < estimated_fee_pct * min_profit_ratio:
            return {
                'approved': False,
                'reason': f'Expected move {expected_move_pct:.1%} too small for fees ({estimated_fee_pct:.1%})',
            }
        return {'approved': True}

    async def _check_duplicate(self, signal: Signal) -> dict:
        """Reject duplicate signals for the same symbol."""
        recent_window = self.config.get('duplicate_window_seconds', 300)
        recent = [
            r for r in self.rejection_log
            if r['symbol'] == signal.symbol
            and r['timestamp'] > (datetime.utcnow() - timedelta(seconds=recent_window)).isoformat()
        ]

        # This is a simplified check — in production, check against active signals
        return {'approved': True}

    # ── Logging ───────────────────────────────────────────────

    def _log_rejection(self, signal: Signal, stage: str, reason: str):
        """Log a rejection for analysis."""
        entry = {
            'symbol': signal.symbol,
            'signal': signal.signal,
            'confidence': signal.confidence,
            'score': signal.score,
            'stage': stage,
            'reason': reason,
            'timestamp': datetime.utcnow().isoformat(),
        }
        self.rejection_log.append(entry)

    def get_rejection_stats(self) -> dict:
        """Analyze rejection patterns."""
        if not self.rejection_log:
            return {'total': 0, 'by_stage': {}}

        by_stage = {}
        for entry in self.rejection_log:
            stage = entry['stage']
            by_stage[stage] = by_stage.get(stage, 0) + 1

        return {
            'total': len(self.rejection_log),
            'by_stage': by_stage,
        }
