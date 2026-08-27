"""
🔄 Moon Dev's Trade Feedback Loop
DSH Pattern: Session Log + Self-Modification — learn from every trade.

Tracks signal → outcome relationships and auto-tunes prediction weights.
This is what separates a gambling bot from a quantitative trading system.

Usage:
    loop = TradeFeedbackLoop()
    await loop.record_signal('FART', signal='BUY', confidence=0.8, factors={'rsi': 0.7})
    await loop.record_outcome('FART', pnl_usd=3.50, holding_minutes=45)
    report = await loop.get_accuracy_report()
    weights = await loop.get_recommended_weights()
"""

import os
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from termcolor import cprint


@dataclass
class SignalRecord:
    """A recorded signal with its context."""
    symbol: str
    signal: str
    confidence: float
    factors: Dict[str, float]
    regime: str = "unknown"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    signal_id: str = ""
    outcome_recorded: bool = False


@dataclass
class OutcomeRecord:
    """The outcome of a trade."""
    symbol: str
    pnl_usd: float
    pnl_pct: float = 0.0
    holding_minutes: float = 0.0
    signal_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class TradeFeedbackLoop:
    """
    DSH-style feedback loop.

    Records every signal and its outcome, then analyzes:
    1. Which factors actually predict profitable trades
    2. Which regimes are most profitable
    3. Optimal confidence thresholds
    4. Recommended weight adjustments
    """

    def __init__(self, history_dir: str = None):
        self.history_dir = history_dir or os.path.join(
            os.path.dirname(__file__), 'data'
        )
        os.makedirs(self.history_dir, exist_ok=True)
        self.signals_path = os.path.join(self.history_dir, 'signal_history.jsonl')
        self.outcomes_path = os.path.join(self.history_dir, 'outcome_history.jsonl')

    async def record_signal(self, symbol: str, signal: str, confidence: float,
                           factors: Dict[str, float], regime: str = "unknown",
                           signal_id: str = None) -> str:
        """Record a prediction signal. Returns signal_id for linking to outcome."""
        import uuid
        sid = signal_id or str(uuid.uuid4())[:8]

        record = SignalRecord(
            symbol=symbol,
            signal=signal,
            confidence=confidence,
            factors=factors,
            regime=regime,
            signal_id=sid,
        )

        self._append_jsonl(self.signals_path, record.__dict__)
        return sid

    async def record_outcome(self, symbol: str, pnl_usd: float,
                            pnl_pct: float = 0.0, holding_minutes: float = 0.0,
                            signal_id: str = None):
        """Record the outcome of a trade."""
        record = OutcomeRecord(
            symbol=symbol,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            holding_minutes=holding_minutes,
            signal_id=signal_id or "",
        )

        self._append_jsonl(self.outcomes_path, record.__dict__)

    async def get_accuracy_report(self, days: int = 30) -> dict:
        """Analyze prediction accuracy and factor effectiveness."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

        signals = self._read_jsonl(self.signals_path, cutoff)
        outcomes = self._read_jsonl(self.outcomes_path, cutoff)

        if not signals:
            return {'total_signals': 0, 'message': 'No signals recorded'}

        # Match signals to outcomes by signal_id
        outcome_map = {}
        for o in outcomes:
            if o.get('signal_id'):
                outcome_map[o['signal_id']] = o

        # Analyze by signal type
        by_signal = {}
        for s in signals:
            sig = s.get('signal', 'UNKNOWN')
            sid = s.get('signal_id', '')
            outcome = outcome_map.get(sid)

            if sig not in by_signal:
                by_signal[sig] = {'count': 0, 'wins': 0, 'total_pnl': 0.0, 'confidences': []}

            by_signal[sig]['count'] += 1
            by_signal[sig]['confidences'].append(s.get('confidence', 0))

            if outcome:
                pnl = outcome.get('pnl_usd', 0)
                by_signal[sig]['total_pnl'] += pnl
                if pnl > 0:
                    by_signal[sig]['wins'] += 1

        # Calculate win rates
        for sig, data in by_signal.items():
            data['win_rate'] = data['wins'] / data['count'] if data['count'] > 0 else 0
            data['avg_confidence'] = (
                sum(data['confidences']) / len(data['confidences'])
                if data['confidences'] else 0
            )
            data['avg_pnl'] = data['total_pnl'] / data['count'] if data['count'] > 0 else 0
            del data['confidences']  # Clean up

        # Factor effectiveness analysis
        factor_analysis = self._analyze_factors(signals, outcome_map)

        # Regime analysis
        regime_analysis = self._analyze_regimes(signals, outcome_map)

        return {
            'period_days': days,
            'total_signals': len(signals),
            'total_outcomes': len(outcomes),
            'by_signal': by_signal,
            'by_factor': factor_analysis,
            'by_regime': regime_analysis,
        }

    async def get_recommended_weights(self, days: int = 30) -> Dict[str, float]:
        """
        Recommend weight adjustments based on historical accuracy.

        Factors that predict winning trades get higher weights.
        Factors that predict losing trades get lower weights.
        """
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        signals = self._read_jsonl(self.signals_path, cutoff)
        outcomes = self._read_jsonl(self.outcomes_path, cutoff)

        outcome_map = {}
        for o in outcomes:
            if o.get('signal_id'):
                outcome_map[o['signal_id']] = o

        # Separate winning and losing signals
        winning_factors = {}
        losing_factors = {}

        for s in signals:
            sid = s.get('signal_id', '')
            outcome = outcome_map.get(sid)
            if not outcome:
                continue

            pnl = outcome.get('pnl_usd', 0)
            factors = s.get('factors', {})

            target = winning_factors if pnl > 0 else losing_factors
            for factor, score in factors.items():
                if factor not in target:
                    target[factor] = []
                target[factor].append(score)

        # Calculate recommended weights
        recommendations = {}
        all_factors = set(list(winning_factors.keys()) + list(losing_factors.keys()))

        for factor in all_factors:
            win_scores = winning_factors.get(factor, [])
            lose_scores = losing_factors.get(factor, [])

            win_avg = sum(win_scores) / len(win_scores) if win_scores else 0
            lose_avg = sum(lose_scores) / len(lose_scores) if lose_scores else 0

            # If winning trades have higher factor scores, boost weight
            # If losing trades have higher factor scores, reduce weight
            if win_avg > 0 and lose_avg > 0:
                ratio = win_avg / lose_avg
                recommendations[factor] = max(0.5, min(2.0, ratio))
            elif win_avg > 0:
                recommendations[factor] = 1.2  # Factor appears in wins
            elif lose_avg > 0:
                recommendations[factor] = 0.8  # Factor appears in losses
            else:
                recommendations[factor] = 1.0  # No data

        return recommendations

    # ── Internal ──────────────────────────────────────────────

    def _analyze_factors(self, signals: list, outcome_map: dict) -> dict:
        """Analyze which factors predict profitable trades."""
        factor_wins = {}
        factor_losses = {}

        for s in signals:
            sid = s.get('signal_id', '')
            outcome = outcome_map.get(sid)
            if not outcome:
                continue

            pnl = outcome.get('pnl_usd', 0)
            factors = s.get('factors', {})

            for factor, score in factors.items():
                if factor not in factor_wins:
                    factor_wins[factor] = []
                    factor_losses[factor] = []

                if pnl > 0:
                    factor_wins[factor].append(score)
                else:
                    factor_losses[factor].append(score)

        result = {}
        all_factors = set(list(factor_wins.keys()) + list(factor_losses.keys()))
        for factor in all_factors:
            wins = factor_wins.get(factor, [])
            losses = factor_losses.get(factor, [])
            result[factor] = {
                'winning_avg': sum(wins) / len(wins) if wins else 0,
                'losing_avg': sum(losses) / len(losses) if losses else 0,
                'win_count': len(wins),
                'loss_count': len(losses),
            }

        return result

    def _analyze_regimes(self, signals: list, outcome_map: dict) -> dict:
        """Analyze which regimes are most profitable."""
        regime_data = {}

        for s in signals:
            regime = s.get('regime', 'unknown')
            sid = s.get('signal_id', '')
            outcome = outcome_map.get(sid)

            if regime not in regime_data:
                regime_data[regime] = {'count': 0, 'wins': 0, 'total_pnl': 0}

            regime_data[regime]['count'] += 1
            if outcome:
                pnl = outcome.get('pnl_usd', 0)
                regime_data[regime]['total_pnl'] += pnl
                if pnl > 0:
                    regime_data[regime]['wins'] += 1

        for regime, data in regime_data.items():
            data['win_rate'] = data['wins'] / data['count'] if data['count'] > 0 else 0
            data['avg_pnl'] = data['total_pnl'] / data['count'] if data['count'] > 0 else 0

        return regime_data

    def _append_jsonl(self, path: str, data: dict):
        """Append a JSON line to a file."""
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(data, default=str) + '\n')
        except Exception:
            pass

    def _read_jsonl(self, path: str, since: str = None) -> list:
        """Read JSONL file, optionally filtering by timestamp."""
        if not os.path.exists(path):
            return []

        results = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if since and entry.get('timestamp', '') < since:
                        continue
                    results.append(entry)
                except Exception:
                    continue
        return results
