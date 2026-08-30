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

    Stores to PostgreSQL when available, falls back to JSONL.
    """

    def __init__(self, history_dir: str = None):
        self.history_dir = history_dir or os.path.join(
            os.path.dirname(__file__), 'data'
        )
        os.makedirs(self.history_dir, exist_ok=True)
        self.signals_path = os.path.join(self.history_dir, 'signal_history.jsonl')
        self.outcomes_path = os.path.join(self.history_dir, 'outcome_history.jsonl')
        # Check DB availability
        self._db_available = False
        try:
            from src.db_storage import get_pool
            self._db_available = get_pool() is not None
        except Exception:
            pass

    async def record_signal(self, symbol: str, signal: str, confidence: float,
                           factors: Dict[str, float], regime: str = "unknown",
                           signal_id: str = None) -> str:
        """Record a prediction signal. Returns signal_id for linking to outcome."""
        import uuid
        sid = signal_id or str(uuid.uuid4())[:8]
        timestamp = datetime.utcnow().isoformat()

        record = SignalRecord(
            symbol=symbol,
            signal=signal,
            confidence=confidence,
            factors=factors,
            regime=regime,
            signal_id=sid,
        )

        # Write to DB if available
        if self._db_available:
            try:
                from src.db_storage import save_feedback_signal
                save_feedback_signal(
                    signal_id=sid, symbol=symbol, signal=signal,
                    confidence=confidence, factors=factors, regime=regime,
                    timestamp=timestamp,
                )
            except Exception:
                pass

        # Also write to JSONL as fallback
        self._append_jsonl(self.signals_path, record.__dict__)
        return sid

    async def record_outcome(self, symbol: str, pnl_usd: float,
                            pnl_pct: float = 0.0, holding_minutes: float = 0.0,
                            signal_id: str = None):
        """Record the outcome of a trade."""
        timestamp = datetime.utcnow().isoformat()
        record = OutcomeRecord(
            symbol=symbol,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            holding_minutes=holding_minutes,
            signal_id=signal_id or "",
        )

        # Write to DB if available
        if self._db_available:
            try:
                from src.db_storage import save_feedback_outcome
                save_feedback_outcome(
                    signal_id=signal_id or "", symbol=symbol,
                    pnl_usd=pnl_usd, pnl_pct=pnl_pct,
                    holding_minutes=holding_minutes, timestamp=timestamp,
                )
            except Exception:
                pass

        self._append_jsonl(self.outcomes_path, record.__dict__)

    async def get_accuracy_report(self, days: int = 30) -> dict:
        """Analyze prediction accuracy and factor effectiveness."""
        # Prefer DB query
        if self._db_available:
            try:
                return await self._report_from_db(days)
            except Exception:
                pass

        # Fallback to JSONL
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        signals = self._read_jsonl(self.signals_path, cutoff)
        outcomes = self._read_jsonl(self.outcomes_path, cutoff)
        return self._build_report(signals, outcomes, days)

    async def _report_from_db(self, days: int) -> dict:
        """Build accuracy report from PostgreSQL."""
        from src.db_storage import get_feedback_signals, get_feedback_outcomes

        signals = get_feedback_signals(days=days)
        outcomes = get_feedback_outcomes(days=days)

        # Convert DB rows to dict format
        signals_dicts = []
        for s in signals:
            factors = s.get('factors', {})
            if isinstance(factors, str):
                factors = json.loads(factors)
            signals_dicts.append({
                'signal_id': s.get('signal_id', ''),
                'symbol': s.get('symbol', ''),
                'signal': s.get('signal', ''),
                'confidence': s.get('confidence', 0),
                'factors': factors,
                'regime': s.get('regime', 'unknown'),
                'timestamp': str(s.get('timestamp', '')),
            })

        outcomes_dicts = []
        for o in outcomes:
            outcomes_dicts.append({
                'signal_id': o.get('signal_id', ''),
                'symbol': o.get('symbol', ''),
                'pnl_usd': o.get('pnl_usd', 0),
                'pnl_pct': o.get('pnl_pct', 0),
                'holding_minutes': o.get('holding_minutes', 0),
                'timestamp': str(o.get('timestamp', '')),
            })

        return self._build_report(signals_dicts, outcomes_dicts, days)

    def _build_report(self, signals: list, outcomes: list, days: int) -> dict:
        """Build accuracy report from signal/outcome data."""
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
            del data['confidences']

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
        """Recommend weight adjustments based on historical accuracy."""
        if self._db_available:
            try:
                return await self._weights_from_db(days)
            except Exception:
                pass

        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        signals = self._read_jsonl(self.signals_path, cutoff)
        outcomes = self._read_jsonl(self.outcomes_path, cutoff)
        return self._compute_weights(signals, outcomes)

    async def _weights_from_db(self, days: int) -> Dict[str, float]:
        """Compute recommended weights from DB data."""
        from src.db_storage import get_feedback_signals, get_feedback_outcomes
        signals = get_feedback_signals(days=days)
        outcomes = get_feedback_outcomes(days=days)

        signals_dicts = []
        for s in signals:
            factors = s.get('factors', {})
            if isinstance(factors, str):
                factors = json.loads(factors)
            signals_dicts.append({
                'signal_id': s.get('signal_id', ''),
                'factors': factors,
            })
        outcomes_dicts = [{
            'signal_id': o.get('signal_id', ''),
            'pnl_usd': o.get('pnl_usd', 0),
        } for o in outcomes]

        return self._compute_weights(signals_dicts, outcomes_dicts)

    def _compute_weights(self, signals: list, outcomes: list) -> Dict[str, float]:
        """Compute recommended weight adjustments."""
        outcome_map = {}
        for o in outcomes:
            if o.get('signal_id'):
                outcome_map[o['signal_id']] = o

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

        recommendations = {}
        all_factors = set(list(winning_factors.keys()) + list(losing_factors.keys()))

        for factor in all_factors:
            win_scores = winning_factors.get(factor, [])
            lose_scores = losing_factors.get(factor, [])

            win_avg = sum(win_scores) / len(win_scores) if win_scores else 0
            lose_avg = sum(lose_scores) / len(lose_scores) if lose_scores else 0

            if win_avg > 0 and lose_avg > 0:
                ratio = win_avg / lose_avg
                recommendations[factor] = max(0.5, min(2.0, ratio))
            elif win_avg > 0:
                recommendations[factor] = 1.2
            elif lose_avg > 0:
                recommendations[factor] = 0.8
            else:
                recommendations[factor] = 1.0

        return recommendations

    # ── Internal ──────────────────────────────────────────────

    def _analyze_factors(self, signals: list, outcome_map: dict) -> dict:
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
        """Append a JSON line to a file (fallback storage)."""
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
