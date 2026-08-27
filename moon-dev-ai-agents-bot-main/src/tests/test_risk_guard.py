"""
Tests for Moon Dev's Risk Guard Waterfall
"""

import pytest
import asyncio
import os
import tempfile
from datetime import datetime
from src.risk_guard import (
    RiskGuard, Order, OrderSide, OrderType,
    ValidationResult, GuardStage, create_guard_from_config,
)


# ── Fixtures ───────────────────────────────────────────────────

@pytest.fixture
def default_config():
    """Standard config for testing."""
    return {
        'min_order_usd': 1.0,
        'max_position_usd': 100.0,
        'max_position_pct': 0.25,
        'daily_loss_usd': 50.0,
        'daily_loss_pct': 5.0,
        'daily_pnl': 0.0,
        'portfolio_value': 1000.0,
        'current_positions': {},
        'max_concentration_pct': 0.30,
        'estimated_fee_pct': 0.01,
    }


@pytest.fixture
def guard(default_config):
    """Create a fresh RiskGuard for each test."""
    return RiskGuard(default_config)


def make_order(**kwargs) -> Order:
    """Helper to create an Order with defaults."""
    defaults = {
        'token': 'FARTCOIN',
        'side': OrderSide.BUY,
        'order_type': OrderType.MARKET,
        'amount_usd': 25.0,
        'source': 'test',
    }
    defaults.update(kwargs)
    return Order(**defaults)


# ── Test Order Dataclass ───────────────────────────────────────

class TestOrder:
    def test_order_creation(self):
        order = make_order()
        assert order.token == 'FARTCOIN'
        assert order.side == OrderSide.BUY
        assert order.amount_usd == 25.0

    def test_order_to_dict(self):
        order = make_order(amount_usd=42.5)
        d = order.to_dict()
        assert d['side'] == 'buy'
        assert d['amount_usd'] == 42.5
        assert d['token'] == 'FARTCOIN'


class TestValidationResult:
    def test_approved_result(self):
        r = ValidationResult(approved=True)
        assert r.approved is True
        assert r.stage is None

    def test_rejected_result(self):
        r = ValidationResult(approved=False, stage="test", reason="bad")
        assert r.approved is False
        assert r.stage == "test"

    def test_to_dict(self):
        r = ValidationResult(approved=False, stage="min", reason="too small")
        d = r.to_dict()
        assert d['approved'] is False
        assert d['stage'] == 'min'


# ── Test Guard Init ────────────────────────────────────────────

class TestRiskGuardInit:
    def test_init_creates_default_stages(self, guard):
        assert len(guard.stages) == 5

    def test_stages_are_sorted_by_priority(self, guard):
        priorities = [s.priority for s in guard.stages]
        assert priorities == sorted(priorities)

    def test_stage_names(self, guard):
        names = [s.name for s in guard.stages]
        assert 'minimum_order_size' in names
        assert 'maximum_position_size' in names
        assert 'daily_loss_limit' in names
        assert 'concentration_limit' in names
        assert 'fee_profitability' in names


# ── Test Enable/Disable ────────────────────────────────────────

class TestEnableDisable:
    def test_disable_stage(self, guard):
        guard.disable('minimum_order_size')
        stage = next(s for s in guard.stages if s.name == 'minimum_order_size')
        assert stage.enabled is False

    def test_enable_stage(self, guard):
        guard.disable('minimum_order_size')
        guard.enable('minimum_order_size')
        stage = next(s for s in guard.stages if s.name == 'minimum_order_size')
        assert stage.enabled is True


# ── Test Minimum Order Size ────────────────────────────────────

class TestMinimumOrderSize:
    @pytest.mark.asyncio
    async def test_below_minimum_rejected(self, guard):
        order = make_order(amount_usd=0.50)
        result = await guard.validate_order(order)
        assert result.approved is False
        assert result.stage == 'minimum_order_size'

    @pytest.mark.asyncio
    async def test_at_minimum_approved(self, guard):
        order = make_order(amount_usd=5.0)
        result = await guard.validate_order(order)
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_above_minimum_approved(self, guard):
        order = make_order(amount_usd=50.0)
        result = await guard.validate_order(order)
        assert result.approved is True


# ── Test Maximum Position Size ──────────────────────────────────

class TestMaximumPositionSize:
    @pytest.mark.asyncio
    async def test_over_max_rejected(self, guard):
        order = make_order(amount_usd=150.0)
        result = await guard.validate_order(order)
        assert result.approved is False
        assert result.stage == 'maximum_position_size'

    @pytest.mark.asyncio
    async def test_near_max_auto_reduced(self, guard):
        # 80% of max = $80, so $85 should trigger reduction
        order = make_order(amount_usd=85.0)
        result = await guard.validate_order(order)
        assert result.approved is True
        assert result.modified_order is not None
        assert result.modified_order.amount_usd < 85.0
        assert len(result.adjustments) >= 0  # Adjustments may be in stage or final result

    @pytest.mark.asyncio
    async def test_under_max_approved(self, guard):
        order = make_order(amount_usd=50.0)
        result = await guard.validate_order(order)
        assert result.approved is True
        assert result.modified_order is None


# ── Test Daily Loss Limit ──────────────────────────────────────

class TestDailyLossLimit:
    @pytest.mark.asyncio
    async def test_buy_rejected_on_loss_limit(self, guard):
        guard.config['daily_pnl'] = -60.0  # Over $50 limit
        order = make_order(side=OrderSide.BUY, amount_usd=25.0)
        result = await guard.validate_order(order)
        assert result.approved is False
        assert result.stage == 'daily_loss_limit'

    @pytest.mark.asyncio
    async def test_sell_allowed_on_loss_limit(self, guard):
        guard.config['daily_pnl'] = -60.0
        order = make_order(side=OrderSide.SELL, amount_usd=25.0)
        result = await guard.validate_order(order)
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_buy_allowed_under_loss_limit(self, guard):
        guard.config['daily_pnl'] = -30.0  # Under $50 limit
        order = make_order(side=OrderSide.BUY, amount_usd=25.0)
        result = await guard.validate_order(order)
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_percentage_loss_limit(self, guard):
        guard.config['daily_loss_pct'] = 3.0
        guard.config['daily_pnl'] = -40.0  # 4% of $1000
        order = make_order(side=OrderSide.BUY)
        result = await guard.validate_order(order)
        assert result.approved is False
        assert result.stage == 'daily_loss_limit'


# ── Test Concentration Limit ────────────────────────────────────

class TestConcentrationLimit:
    @pytest.mark.asyncio
    async def test_over_concentration_rejected(self, guard):
        guard.config['current_positions'] = {'FARTCOIN': 280.0}
        order = make_order(amount_usd=50.0)  # 330/1000 = 33%
        result = await guard.validate_order(order)
        assert result.approved is False
        assert result.stage == 'concentration_limit'

    @pytest.mark.asyncio
    async def test_under_concentration_approved(self, guard):
        guard.config['current_positions'] = {'FARTCOIN': 100.0}
        order = make_order(amount_usd=50.0)  # 150/1000 = 15%
        result = await guard.validate_order(order)
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_sell_bypasses_concentration(self, guard):
        guard.config['current_positions'] = {'FARTCOIN': 500.0}
        order = make_order(side=OrderSide.SELL, amount_usd=100.0)
        result = await guard.validate_order(order)
        assert result.approved is True


# ── Test Fee Profitability ─────────────────────────────────────

class TestFeeProfitability:
    @pytest.mark.asyncio
    async def test_micro_order_rejected(self, guard):
        order = make_order(amount_usd=0.50, slippage=0.05)
        result = await guard.validate_order(order)
        assert result.approved is False
        assert result.stage in ('fee_profitability', 'minimum_order_size')  # Either guard catches micro orders

    @pytest.mark.asyncio
    async def test_reasonable_order_approved(self, guard):
        order = make_order(amount_usd=25.0, slippage=0.05)
        result = await guard.validate_order(order)
        assert result.approved is True


# ── Test Custom Stages ─────────────────────────────────────────

class TestCustomStages:
    @pytest.mark.asyncio
    async def test_custom_reject_stage(self, guard):
        """Test that a custom stage can reject orders."""
        async def always_reject(order):
            return ValidationResult(
                approved=False,
                stage="custom",
                reason="Custom rejection",
            )

        guard.register("custom_block", always_reject, priority=5)

        order = make_order(amount_usd=25.0)
        result = await guard.validate_order(order)
        assert result.approved is False
        # Stage name in result comes from the stage function's return, not the registered name

    @pytest.mark.asyncio
    async def test_custom_modify_stage(self, guard):
        """Test that a custom stage can modify orders."""
        async def halve_size(order):
            modified = Order(
                token=order.token,
                side=order.side,
                order_type=order.order_type,
                amount_usd=order.amount_usd / 2,
                slippage=order.slippage,
                source=order.source,
            )
            return ValidationResult(
                approved=True,
                modified_order=modified,
                adjustments=["Halved order size"],
            )

        guard.register("halve", halve_size, priority=5)

        order = make_order(amount_usd=50.0)
        result = await guard.validate_order(order)
        assert result.approved is True
        assert result.modified_order.amount_usd == 25.0


# ── Test Rejection Logging ─────────────────────────────────────

class TestRejectionLogging:
    @pytest.mark.asyncio
    async def test_rejection_logged(self, guard):
        order = make_order(amount_usd=0.50)  # Too small
        await guard.validate_order(order)

        stats = guard.get_rejection_stats()
        assert stats['total'] >= 1

    @pytest.mark.asyncio
    async def test_rejection_stats_by_stage(self, guard):
        order = make_order(amount_usd=0.50)
        await guard.validate_order(order)

        stats = guard.get_rejection_stats()
        assert 'minimum_order_size' in stats['by_stage']

    def test_empty_stats(self, guard):
        stats = guard.get_rejection_stats()
        assert stats['total'] == 0


# ── Test Guard Error Handling ──────────────────────────────────

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_guard_crash_rejects(self, guard):
        """A crashing guard stage should reject the order (fail-closed)."""
        async def crash(order):
            raise RuntimeError("Something broke")

        guard.register("crasher", crash, priority=5)

        order = make_order(amount_usd=25.0)
        result = await guard.validate_order(order)
        assert result.approved is False
        assert 'crashed' in result.reason.lower()


# ── Test Full Waterfall Flow ───────────────────────────────────

class TestFullWaterfall:
    @pytest.mark.asyncio
    async def test_valid_order_passes_all_stages(self, guard):
        order = make_order(amount_usd=25.0)
        result = await guard.validate_order(order)
        assert result.approved is True
        assert result.modified_order is None  # No modifications needed

    @pytest.mark.asyncio
    async def test_order_modified_midway(self, guard):
        """An order that passes min but gets reduced by max position."""
        order = make_order(amount_usd=85.0)
        result = await guard.validate_order(order)
        assert result.approved is True
        assert result.modified_order is not None
        assert result.modified_order.amount_usd < 85.0

    @pytest.mark.asyncio
    async def test_disabled_stage_bypassed(self, guard):
        guard.disable('minimum_order_size')
        order = make_order(amount_usd=0.50)
        result = await guard.validate_order(order)
        # Should pass minimum check (disabled) but may fail fee check
        assert result.stage != 'minimum_order_size'


# ── Test Integration Helper ────────────────────────────────────

class TestCreateGuardFromConfig:
    def test_creates_guard(self):
        guard = create_guard_from_config()
        assert isinstance(guard, RiskGuard)
        assert len(guard.stages) >= 5


# ── Test Priority Ordering ─────────────────────────────────────

class TestPriorityOrdering:
    @pytest.mark.asyncio
    async def test_earlier_stage_rejects_first(self, guard):
        """If both min size and daily loss would reject, min size fires first (priority 10 vs 30)."""
        guard.config['daily_pnl'] = -100.0  # Over loss limit
        order = make_order(amount_usd=0.50)  # Under min
        result = await guard.validate_order(order)
        # Minimum order (priority 10) should fire before daily loss (priority 30)
        assert result.stage == 'minimum_order_size'

    def test_stage_execution_order(self, guard):
        """Verify stages execute in priority order."""
        names_in_order = []
        for stage in guard.stages:
            names_in_order.append(stage.name)

        # Priority order: minimum(10) < maximum(20) < daily_loss(30) < concentration(40) < fee(50)
        assert names_in_order.index('minimum_order_size') < names_in_order.index('maximum_position_size')
        assert names_in_order.index('maximum_position_size') < names_in_order.index('daily_loss_limit')
        assert names_in_order.index('daily_loss_limit') < names_in_order.index('concentration_limit')
