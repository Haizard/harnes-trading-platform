"""
Tests for Trade Feedback Loop, Execution Quality Tracker, and Signal Validation Pipeline
"""

import pytest
import asyncio
import os
from src.feedback_loop import TradeFeedbackLoop
from src.execution_tracker import ExecutionTracker
from src.signal_pipeline import SignalValidationPipeline, Signal


# ── Trade Feedback Loop Tests ─────────────────────────────────

class TestTradeFeedbackLoop:
    @pytest.fixture
    def loop(self, tmp_path):
        return TradeFeedbackLoop(history_dir=str(tmp_path))

    @pytest.mark.asyncio
    async def test_record_signal(self, loop):
        sid = await loop.record_signal(
            'FART', signal='BUY', confidence=0.8,
            factors={'rsi': 0.7, 'volume': 0.5}
        )
        assert sid  # Should return a signal_id

    @pytest.mark.asyncio
    async def test_record_outcome(self, loop):
        sid = await loop.record_signal('FART', signal='BUY', confidence=0.8, factors={})
        await loop.record_outcome('FART', pnl_usd=3.50, signal_id=sid)

    @pytest.mark.asyncio
    async def test_accuracy_report_empty(self, loop):
        report = await loop.get_accuracy_report()
        assert report['total_signals'] == 0

    @pytest.mark.asyncio
    async def test_accuracy_report_with_data(self, loop):
        # Record signals and outcomes
        sid1 = await loop.record_signal('FART', signal='BUY', confidence=0.8, factors={'rsi': 0.7})
        await loop.record_outcome('FART', pnl_usd=5.0, signal_id=sid1)

        sid2 = await loop.record_signal('BONK', signal='SELL', confidence=0.6, factors={'rsi': -0.5})
        await loop.record_outcome('BONK', pnl_usd=-2.0, signal_id=sid2)

        report = await loop.get_accuracy_report(days=30)
        assert report['total_signals'] == 2
        assert report['total_outcomes'] == 2
        assert 'BUY' in report['by_signal']
        assert 'SELL' in report['by_signal']

    @pytest.mark.asyncio
    async def test_recommended_weights(self, loop):
        # Record winning BUY signals with high RSI factor
        for i in range(5):
            sid = await loop.record_signal(
                'FART', signal='BUY', confidence=0.8,
                factors={'rsi': 0.8, 'volume': 0.3}
            )
            await loop.record_outcome('FART', pnl_usd=5.0, signal_id=sid)

        # Record losing SELL signals with high volume factor
        for i in range(3):
            sid = await loop.record_signal(
                'BONK', signal='SELL', confidence=0.6,
                factors={'rsi': -0.3, 'volume': 0.9}
            )
            await loop.record_outcome('BONK', pnl_usd=-3.0, signal_id=sid)

        weights = await loop.get_recommended_weights()
        assert 'rsi' in weights
        assert 'volume' in weights


# ── Execution Tracker Tests ───────────────────────────────────

class TestExecutionTracker:
    @pytest.fixture
    def tracker(self, tmp_path):
        return ExecutionTracker(history_dir=str(tmp_path))

    @pytest.mark.asyncio
    async def test_record_intent(self, tracker):
        await tracker.record_intent('FART', 'buy', 25.0, expected_price=0.0042)

    @pytest.mark.asyncio
    async def test_record_fill(self, tracker):
        await tracker.record_fill(
            'FART', 'buy', 25.0,
            fill_price=0.00425, expected_price=0.0042,
            latency_ms=150
        )

    @pytest.mark.asyncio
    async def test_slippage_calculation(self, tracker):
        # Buy: fill_price > expected = positive slippage (paid more)
        await tracker.record_fill(
            'FART', 'buy', 25.0,
            fill_price=0.0043, expected_price=0.0042
        )
        report = await tracker.get_quality_report()
        assert report['slippage']['avg_bps'] > 0

    @pytest.mark.asyncio
    async def test_quality_report_empty(self, tracker):
        report = await tracker.get_quality_report()
        assert report['total_intents'] == 0

    @pytest.mark.asyncio
    async def test_quality_report_with_data(self, tracker):
        # Record some executions
        await tracker.record_fill('FART', 'buy', 25.0, fill_price=0.0043, expected_price=0.0042, latency_ms=100)
        await tracker.record_fill('BONK', 'sell', 10.0, fill_price=0.0050, expected_price=0.0051, latency_ms=200)
        await tracker.record_rejection('FART', 'buy', 25.0, reason='Risk guard: too large')

        report = await tracker.get_quality_report()
        assert report['total_intents'] == 3
        assert report['total_filled'] == 2
        assert report['total_rejected'] == 1
        assert abs(report['fill_rate'] - 2/3) < 0.01

    @pytest.mark.asyncio
    async def test_by_symbol_breakdown(self, tracker):
        await tracker.record_fill('FART', 'buy', 25.0, fill_price=0.0043, expected_price=0.0042)
        await tracker.record_fill('FART', 'sell', 10.0, fill_price=0.0050, expected_price=0.0051)

        report = await tracker.get_quality_report()
        assert 'FART' in report['by_symbol']
        assert report['by_symbol']['FART']['count'] == 2


# ── Signal Validation Pipeline Tests ──────────────────────────

class TestSignalValidationPipeline:
    @pytest.fixture
    def pipeline(self):
        return SignalValidationPipeline({
            'min_confidence': 0.3,
            'min_factor_agreement': 0.4,
            'min_profit_ratio': 2.0,
        })

    def make_signal(self, **kwargs) -> Signal:
        defaults = {
            'symbol': 'FART',
            'signal': 'BUY',
            'score': 0.5,
            'confidence': 0.7,
            'factors': {'rsi': 0.8, 'volume': 0.5, 'momentum': 0.3},
        }
        defaults.update(kwargs)
        return Signal(**defaults)

    @pytest.mark.asyncio
    async def test_hold_rejected(self, pipeline):
        signal = self.make_signal(signal='HOLD', score=0.0, confidence=0.5)
        result = await pipeline.validate(signal)
        assert result['approved'] is False
        assert 'HOLD' in result['reason']

    @pytest.mark.asyncio
    async def test_low_confidence_rejected(self, pipeline):
        signal = self.make_signal(confidence=0.1)
        result = await pipeline.validate(signal)
        assert result['approved'] is False
        assert 'Confidence' in result['reason']

    @pytest.mark.asyncio
    async def test_high_confidence_approved(self, pipeline):
        signal = self.make_signal(confidence=0.8, score=0.5)
        result = await pipeline.validate(signal)
        assert result['approved'] is True

    @pytest.mark.asyncio
    async def test_low_factor_agreement_rejected(self, pipeline):
        # Factors mostly disagree with BUY signal
        signal = self.make_signal(
            confidence=0.8,
            factors={'rsi': -0.8, 'volume': -0.5, 'momentum': -0.3}
        )
        result = await pipeline.validate(signal)
        assert result['approved'] is False

    @pytest.mark.asyncio
    async def test_strong_signal_approved(self, pipeline):
        signal = self.make_signal(
            confidence=0.9,
            score=0.8,
            factors={'rsi': 0.9, 'volume': 0.7, 'momentum': 0.6}
        )
        result = await pipeline.validate(signal)
        assert result['approved'] is True

    @pytest.mark.asyncio
    async def test_sell_signal(self, pipeline):
        signal = self.make_signal(
            signal='SELL',
            score=-0.7,
            confidence=0.8,
            factors={'rsi': -0.9, 'volume': -0.6, 'momentum': -0.5}
        )
        result = await pipeline.validate(signal)
        assert result['approved'] is True

    @pytest.mark.asyncio
    async def test_rejection_stats(self, pipeline):
        # Reject a few signals
        await pipeline.validate(self.make_signal(confidence=0.1))
        await pipeline.validate(self.make_signal(confidence=0.2))

        stats = pipeline.get_rejection_stats()
        assert stats['total'] == 2

    @pytest.mark.asyncio
    async def test_disable_stage(self, pipeline):
        pipeline.stages[0].enabled = False  # Disable confidence check
        signal = self.make_signal(confidence=0.05)  # Very low confidence
        result = await pipeline.validate(signal)
        # Should pass confidence check (disabled) but may fail factor agreement
        assert result.get('reason', '') != 'Confidence 5% below minimum 30%'
