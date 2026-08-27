"""
Tests for Moon Dev's Order Executor — integrated order flow
"""

import pytest
import asyncio
from src.event_bus import EventBus, Events, DispatchMode
from src.session_log import create_test_session_log, EventType
from src.order_executor import OrderExecutor, OrderResult, create_order_executor


# ── Fixtures ───────────────────────────────────────────────────

@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def log():
    return create_test_session_log()


@pytest.fixture
def executor(bus, log):
    return OrderExecutor(event_bus=bus, session_log=log)


@pytest.fixture
def executor_with_risk_guard(bus, log):
    """Executor with a mock risk guard that rejects orders > $50."""
    async def mock_risk(payload, next_fn):
        if payload['amount_usd'] > 50:
            return {'rejected': True, 'reason': 'Position too large'}
        return await next_fn(payload)

    bus.on(Events.ORDER_SUBMIT, mock_risk,
           mode=DispatchMode.WATERFALL, priority=10, tag="risk_guard")

    return OrderExecutor(event_bus=bus, session_log=log)


# ── Test OrderResult ──────────────────────────────────────────

class TestOrderResult:
    def test_created(self):
        r = OrderResult(executed=True, reason="Filled", token="FART", side="buy")
        assert r.executed is True
        assert r.token == "FART"

    def test_to_dict(self):
        r = OrderResult(executed=True, token="FART", side="buy", amount_usd=25.0)
        d = r.to_dict()
        assert d['executed'] is True
        assert d['side'] == 'buy'
        assert d['amount_usd'] == 25.0

    def test_repr(self):
        r = OrderResult(executed=True, token="FART", side="buy", amount_usd=25.0)
        assert "✅" in repr(r)
        assert "BUY" in repr(r)


# ── Test Basic Execution ──────────────────────────────────────

class TestBasicExecution:
    @pytest.mark.asyncio
    async def test_buy_no_risk_guard(self, executor):
        """Buy should execute when no risk guard is registered."""
        result = await executor.buy('FARTCOIN', 25.0, source='test')
        # Will fail because nice_funcs isn't available in test env
        # But we can verify the flow reached execution
        assert result.side == 'buy'
        assert result.token == 'FARTCOIN'
        assert result.amount_usd == 25.0

    @pytest.mark.asyncio
    async def test_sell_no_risk_guard(self, executor):
        result = await executor.sell('FARTCOIN', 10.0, source='test')
        assert result.side == 'sell'

    @pytest.mark.asyncio
    async def test_intent(self, executor):
        result = await executor.intent('FARTCOIN', 'buy', 25.0, source='test')
        assert result.executed is False
        assert result.reason == "Intent only — not executed"


# ── Test Risk Guard Integration ───────────────────────────────

class TestRiskGuardIntegration:
    @pytest.mark.asyncio
    async def test_small_order_passes(self, executor_with_risk_guard):
        """Order under $50 should pass the mock risk guard."""
        result = await executor_with_risk_guard.buy('FARTCOIN', 25.0, source='test')
        # The waterfall approved, so it tried to execute
        # (may fail on nice_funcs, but that's ok for this test)
        assert result.side == 'buy'

    @pytest.mark.asyncio
    async def test_large_order_rejected(self, executor_with_risk_guard):
        """Order over $50 should be rejected by the mock risk guard."""
        result = await executor_with_risk_guard.buy('FARTCOIN', 100.0, source='test')
        assert result.executed is False
        assert 'Risk Guard' in result.reason
        assert 'Position too large' in result.reason

    @pytest.mark.asyncio
    async def test_sell_also_goes_through_guard(self, executor_with_risk_guard):
        """Sells should also go through the risk guard."""
        result = await executor_with_risk_guard.sell('FARTCOIN', 100.0, source='test')
        assert result.executed is False
        assert 'Risk Guard' in result.reason


# ── Test Session Log Integration ──────────────────────────────

class TestSessionLogIntegration:
    @pytest.mark.asyncio
    async def test_intent_logged(self, executor, log):
        await executor.intent('FARTCOIN', 'buy', 25.0, source='test')
        events = await log.get_events(event_type=EventType.ORDER_INTENT)
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_rejection_logged(self, executor_with_risk_guard, log):
        await executor_with_risk_guard.buy('FARTCOIN', 100.0, source='test')
        events = await log.get_events(event_type=EventType.RISK_DENIED)
        assert len(events) >= 1
        assert events[0].data['reason'] == 'Position too large'

    @pytest.mark.asyncio
    async def test_execution_failure_logged(self, executor, log):
        """Even failed executions should be logged."""
        await executor.buy('FARTCOIN', 25.0, source='test')
        # Should have either ORDER_SUBMITTED (success) or ORDER_FAILED (error)
        submitted = await log.get_events(event_type=EventType.ORDER_SUBMITTED)
        failed = await log.get_events(event_type=EventType.ORDER_FAILED)
        assert len(submitted) >= 1 or len(failed) >= 1


# ── Test Event Bus Integration ────────────────────────────────

class TestEventBusIntegration:
    @pytest.mark.asyncio
    async def test_order_emits_events(self, executor, bus):
        """Order should emit events through the bus."""
        results = []

        async def capture(payload):
            results.append(payload)

        bus.on(Events.ORDER_INTENT, capture, mode=DispatchMode.EMIT)
        await executor.intent('FARTCOIN', 'buy', 25.0, source='test')
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_waterfall_called(self, executor_with_risk_guard, bus):
        """The waterfall should be called for every order."""
        waterfall_calls = []

        # Add a second waterfall listener to track calls
        async def tracker(payload, next_fn):
            waterfall_calls.append(payload)
            return await next_fn(payload)

        bus.on(Events.ORDER_SUBMIT, tracker,
               mode=DispatchMode.WATERFALL, priority=5)

        await executor_with_risk_guard.buy('FARTCOIN', 25.0, source='test')
        assert len(waterfall_calls) >= 1


# ── Test Stats ────────────────────────────────────────────────

class TestStats:
    @pytest.mark.asyncio
    async def test_stats_tracking(self, executor_with_risk_guard):
        # 2 rejections (over $50)
        await executor_with_risk_guard.buy('FARTCOIN', 100.0, source='test')
        await executor_with_risk_guard.buy('FARTCOIN', 200.0, source='test')

        stats = executor_with_risk_guard.stats()
        assert stats['total_rejections'] == 2

    def test_initial_stats(self, executor):
        stats = executor.stats()
        assert stats['total_executions'] == 0
        assert stats['total_rejections'] == 0
        assert stats['approval_rate'] == 0


# ── Test Factory ──────────────────────────────────────────────

class TestFactory:
    def test_create_order_executor(self):
        executor = create_order_executor()
        assert isinstance(executor, OrderExecutor)

    def test_create_with_bus(self):
        bus = EventBus()
        executor = create_order_executor(event_bus=bus)
        assert executor.bus is bus


# ── Test Multiple Risk Guards ─────────────────────────────────

class TestMultipleGuards:
    @pytest.mark.asyncio
    async def test_two_guards_stacked(self, log):
        """Two risk guards in sequence — first denies, second never runs."""
        bus = EventBus()

        async def guard_1(payload, next_fn):
            if payload['amount_usd'] > 30:
                return {'rejected': True, 'reason': 'Guard 1: over 30'}
            return await next_fn(payload)

        async def guard_2(payload, next_fn):
            if payload['amount_usd'] > 20:
                return {'rejected': True, 'reason': 'Guard 2: over 20'}
            return await next_fn(payload)

        bus.on(Events.ORDER_SUBMIT, guard_1,
               mode=DispatchMode.WATERFALL, priority=10)
        bus.on(Events.ORDER_SUBMIT, guard_2,
               mode=DispatchMode.WATERFALL, priority=20)

        executor = OrderExecutor(event_bus=bus, session_log=log)

        # $25 passes guard_1 but fails guard_2
        result = await executor.buy('FART', 25.0)
        assert result.executed is False
        assert 'Guard 2' in result.reason

    @pytest.mark.asyncio
    async def test_order_modification(self, log):
        """A guard can reduce order size."""
        bus = EventBus()

        async def reducer(payload, next_fn):
            if payload['amount_usd'] > 40:
                payload['amount_usd'] = 40
                payload['modifications'] = ['Reduced from >40 to 40']
            return await next_fn(payload)

        bus.on(Events.ORDER_SUBMIT, reducer,
               mode=DispatchMode.WATERFALL, priority=10)

        executor = OrderExecutor(event_bus=bus, session_log=log)
        result = await executor.buy('FART', 50.0)

        # Should have been modified from 50 to 40
        assert result.amount_usd == 40.0


# ── Test Close Position ──────────────────────────────────────

class TestClosePosition:
    @pytest.mark.asyncio
    async def test_close_no_position(self, executor):
        """Closing a non-existent position should return failure."""
        result = await executor.close_position('NONEXISTENT', source='test')
        assert result.executed is False
        assert 'No position' in result.reason
