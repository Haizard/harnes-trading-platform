"""
Tests for Context Compactor, Position Sizer, Alpha Decay Detection, and Portfolio Goals
"""

import pytest
import pandas as pd
import numpy as np
from src.context_compactor import ContextCompactor, CompactSummary
from src.position_sizer import PositionSizer, PositionSize
from src.alpha_decay import AlphaDecayDetector, DecayStatus, StrategyHealth
from src.portfolio_goals import (
    PortfolioGoalManager, PortfolioGoal, GoalType,
    TradeAlignment, create_default_goals,
)


# ── Context Compactor Tests ───────────────────────────────────

class TestContextCompactor:
    @pytest.fixture
    def compactor(self):
        return ContextCompactor()

    @pytest.fixture
    def sample_df(self):
        np.random.seed(42)
        n = 100
        prices = 0.004 + np.random.randn(n).cumsum() * 0.0001
        return pd.DataFrame({
            'open': prices + np.random.randn(n) * 0.00001,
            'high': prices + abs(np.random.randn(n) * 0.00002),
            'low': prices - abs(np.random.randn(n) * 0.00002),
            'close': prices,
            'volume': np.random.randint(1000, 10000, n),
        })

    def test_compact_ohlcv(self, compactor, sample_df):
        result = compactor.compact_ohlcv(sample_df, symbol="TEST")
        assert result is not None
        assert result.symbol == "TEST"
        assert result.candle_count == 100
        assert result.current_price > 0

    def test_compact_empty_df(self, compactor):
        result = compactor.compact_ohlcv(pd.DataFrame())
        assert result is None

    def test_compact_summary_to_prompt(self, compactor, sample_df):
        result = compactor.compact_ohlcv(sample_df)
        text = result.to_prompt_text()
        assert 'TEST' in text or 'Price' in text
        assert '$' in text

    def test_build_compact_prompt(self, compactor, sample_df):
        compact = compactor.compact_ohlcv(sample_df)
        prompt = compactor.build_compact_prompt('FART', compact=compact)
        assert 'FART' in prompt
        assert 'BUY' in prompt or 'SELL' in prompt

    def test_volume_trend(self, compactor):
        # Create data with increasing volume
        n = 50
        df = pd.DataFrame({
            'open': np.ones(n),
            'high': np.ones(n) * 1.1,
            'low': np.ones(n) * 0.9,
            'close': np.ones(n),
            'volume': np.linspace(1000, 5000, n),
        })
        result = compactor.compact_ohlcv(df)
        assert result.volume_trend == 'increasing'


# ── Position Sizer Tests ──────────────────────────────────────

class TestPositionSizer:
    @pytest.fixture
    def sizer(self):
        return PositionSizer({
            'max_position_pct': 0.25,
            'max_total_exposure_pct': 0.80,
            'min_position_usd': 1.0,
            'risk_per_trade_pct': 0.02,
            'win_rate': 0.5,
            'avg_win': 0.05,
            'avg_loss': 0.03,
        })

    def test_basic_sizing(self, sizer):
        result = sizer.calculate_size(
            signal_confidence=0.7,
            volatility=30,
            portfolio_value=1000,
        )
        assert result.amount_usd > 0
        assert result.amount_usd <= 250  # 25% max

    def test_high_confidence_larger(self, sizer):
        low = sizer.calculate_size(0.3, 30, 1000)
        high = sizer.calculate_size(0.9, 30, 1000)
        assert high.amount_usd >= low.amount_usd

    def test_high_volatility_smaller(self, sizer):
        calm = sizer.calculate_size(0.7, 20, 1000)
        volatile = sizer.calculate_size(0.7, 80, 1000)
        assert volatile.amount_usd <= calm.amount_usd

    def test_high_heat_smaller(self, sizer):
        empty = sizer.calculate_size(0.7, 30, 1000, current_exposure_pct=0.1)
        loaded = sizer.calculate_size(0.7, 30, 1000, current_exposure_pct=0.7)
        assert loaded.amount_usd <= empty.amount_usd

    def test_kelly_criterion(self, sizer):
        kelly = sizer._kelly_criterion()
        assert 0 <= kelly <= 0.25

    def test_update_stats(self, sizer):
        sizer.update_stats(win_rate=0.6, avg_win=0.08)
        assert sizer.win_rate == 0.6
        assert sizer.avg_win == 0.08

    def test_zero_portfolio(self, sizer):
        result = sizer.calculate_size(0.7, 30, 0)
        assert result.amount_usd == 0

    def test_position_size_to_dict(self, sizer):
        result = sizer.calculate_size(0.7, 30, 1000)
        d = result.to_dict()
        assert 'amount_usd' in d
        assert 'reasoning' in d


# ── Alpha Decay Detection Tests ───────────────────────────────

class TestAlphaDecayDetector:
    @pytest.fixture
    def detector(self):
        return AlphaDecayDetector({'min_trades': 5})

    def test_healthy_strategy(self, detector):
        # Record 20 winning trades
        for i in range(20):
            detector.record_trade('momentum', 0.02)
        health = detector.check_strategy('momentum')
        assert health.status == DecayStatus.HEALTHY

    def test_decaying_strategy(self, detector):
        # Record declining performance: good start, then losses
        for i in range(8):
            detector.record_trade('momentum', 0.03)
        for i in range(12):
            detector.record_trade('momentum', -0.01)
        health = detector.check_strategy('momentum')
        assert health.status in (DecayStatus.DECLINING, DecayStatus.DECAYED, DecayStatus.DEAD)

    def test_dead_strategy(self, detector):
        # Record mostly losses
        for i in range(15):
            detector.record_trade('bad_strat', -0.03)
        for i in range(5):
            detector.record_trade('bad_strat', 0.01)
        health = detector.check_strategy('bad_strat')
        assert health.status in (DecayStatus.DEAD, DecayStatus.DECAYED)

    def test_auto_disable(self, detector):
        detector.auto_disable('momentum')
        assert detector.is_disabled('momentum')

    def test_re_enable(self, detector):
        detector.auto_disable('momentum')
        detector.re_enable('momentum')
        assert not detector.is_disabled('momentum')

    def test_get_disabled(self, detector):
        detector.auto_disable('a')
        detector.auto_disable('b')
        disabled = detector.get_disabled_strategies()
        assert 'a' in disabled
        assert 'b' in disabled

    def test_insufficient_data(self, detector):
        detector.record_trade('new', 0.01)
        health = detector.check_strategy('new')
        assert health.total_trades == 1
        assert 'need' in health.recommendation.lower()

    def test_check_all(self, detector):
        for i in range(10):
            detector.record_trade('strat_a', 0.02)
            detector.record_trade('strat_b', -0.02)
        all_health = detector.check_all_strategies()
        assert 'strat_a' in all_health
        assert 'strat_b' in all_health

    def test_health_to_dict(self, detector):
        for i in range(10):
            detector.record_trade('test', 0.02)
        health = detector.check_strategy('test')
        d = health.to_dict()
        assert 'status' in d
        assert 'win_rate' in d


# ── Portfolio Goals Tests ─────────────────────────────────────

class TestPortfolioGoals:
    @pytest.fixture
    def manager(self):
        return create_default_goals()

    def test_default_goals_created(self, manager):
        goals = manager.get_all_goals()
        assert len(goals) >= 5

    def test_set_custom_goal(self, manager):
        manager.set_goal('custom', GoalType.RETURN_TARGET, 0.20)
        goal = manager.get_goal('custom')
        assert goal is not None
        assert goal.target_value == 0.20

    def test_update_progress(self, manager):
        manager.update_progress('monthly_return', 0.05)
        goal = manager.get_goal('monthly_return')
        assert goal.current_value == 0.05

    def test_achieved_goals(self, manager):
        manager.update_progress('cash_reserve', 0.25)
        achieved = manager.get_achieved_goals()
        assert 'cash_reserve' in achieved

    def test_trade_alignment_good(self, manager):
        trade = {'token': 'FART', 'side': 'buy', 'amount_usd': 25, 'confidence': 0.8}
        state = {'value': 1000, 'cash_pct': 50, 'positions': 2, 'drawdown_pct': 0.02}
        alignment = manager.check_alignment(trade, state)
        assert alignment.overall_score > 0.3

    def test_trade_alignment_bad_cash(self, manager):
        # Trade that would drain cash below reserve
        trade = {'token': 'FART', 'side': 'buy', 'amount_usd': 900, 'confidence': 0.8}
        state = {'value': 1000, 'cash_pct': 0.30, 'positions': 2, 'drawdown_pct': 0.02}
        alignment = manager.check_alignment(trade, state)
        assert len(alignment.recommendations) > 0

    def test_goal_progress(self, manager):
        goal = manager.get_goal('monthly_return')
        assert goal.progress >= 0

    def test_status_summary(self, manager):
        summary = manager.get_status_summary()
        assert 'total_goals' in summary
        assert 'achieved' in summary

    def test_goal_dict(self, manager):
        goal = manager.get_goal('monthly_return')
        d = goal.to_dict()
        assert 'type' in d
        assert 'target' in d
        assert 'achieved' in d

    def test_disabled_goal_ignored(self, manager):
        manager.set_goal('test', GoalType.RETURN_TARGET, 0.10, weight=1.0)
        manager._goals['test'].enabled = False
        trade = {'token': 'FART', 'side': 'buy', 'amount_usd': 25, 'confidence': 0.8}
        state = {'value': 1000, 'cash_pct': 50, 'positions': 2, 'drawdown_pct': 0.02}
        alignment = manager.check_alignment(trade, state)
        # Disabled goal should not affect score
        assert 'test' not in alignment.goal_scores or alignment.goal_scores.get('test', 0) == 0
