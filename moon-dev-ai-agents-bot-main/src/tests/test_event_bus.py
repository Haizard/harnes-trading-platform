"""
Tests for Moon Dev's DSH-style Event Bus
"""

import pytest
import asyncio
from src.event_bus import (
    EventBus, Events, DispatchMode, Listener, WaterfallResult,
    AbortController, create_integrated_bus,
)


# ── Fixtures ───────────────────────────────────────────────────

@pytest.fixture
def bus():
    """Create a fresh EventBus for each test."""
    return EventBus()


# ── Test Registration ─────────────────────────────────────────

class TestRegistration:
    def test_register_listener(self, bus):
        async def handler(payload):
            pass
        listener = bus.on('test/event', handler, mode=DispatchMode.EMIT)
        assert isinstance(listener, Listener)
        assert listener.event_name == 'test/event'

    def test_register_multiple_listeners(self, bus):
        bus.on('test/event', lambda p: None, mode=DispatchMode.EMIT)
        bus.on('test/event', lambda p: None, mode=DispatchMode.EMIT)
        assert len(bus.listeners('test/event')) == 2

    def test_listeners_sorted_by_priority(self, bus):
        bus.on('test/event', lambda p: None, mode=DispatchMode.EMIT, priority=100)
        bus.on('test/event', lambda p: None, mode=DispatchMode.EMIT, priority=10)
        bus.on('test/event', lambda p: None, mode=DispatchMode.EMIT, priority=50)
        listeners = bus.listeners('test/event')
        priorities = [l.priority for l in listeners]
        assert priorities == [10, 50, 100]

    def test_remove_listener(self, bus):
        listener = bus.on('test/event', lambda p: None, mode=DispatchMode.EMIT)
        assert len(bus.listeners('test/event')) == 1
        bus.off(listener)
        assert len(bus.listeners('test/event')) == 0

    def test_disable_enable_listener(self, bus):
        listener = bus.on('test/event', lambda p: None, mode=DispatchMode.EMIT)
        assert listener.enabled is True
        bus.disable(listener)
        assert listener.enabled is False
        bus.enable(listener)
        assert listener.enabled is True

    def test_clear_all(self, bus):
        bus.on('a', lambda p: None, mode=DispatchMode.EMIT)
        bus.on('b', lambda p: None, mode=DispatchMode.EMIT)
        bus.clear()
        assert len(bus.listeners()) == 0

    def test_clear_event(self, bus):
        bus.on('a', lambda p: None, mode=DispatchMode.EMIT)
        bus.on('b', lambda p: None, mode=DispatchMode.EMIT)
        bus.clear('a')
        assert len(bus.listeners('a')) == 0
        assert len(bus.listeners('b')) == 1


# ── Test Tag Operations ──────────────────────────────────────

class TestTagOperations:
    def test_disable_tag(self, bus):
        bus.on('a', lambda p: None, mode=DispatchMode.EMIT, tag="risk")
        bus.on('b', lambda p: None, mode=DispatchMode.EMIT, tag="risk")
        bus.on('c', lambda p: None, mode=DispatchMode.EMIT, tag="log")
        bus.disable_tag("risk")
        assert all(not l.enabled for l in bus.listeners('a'))
        assert all(l.enabled for l in bus.listeners('c'))

    def test_enable_tag(self, bus):
        listener = bus.on('a', lambda p: None, mode=DispatchMode.EMIT, tag="risk")
        bus.disable(listener)
        bus.enable_tag("risk")
        assert listener.enabled is True

    def test_remove_tag(self, bus):
        bus.on('a', lambda p: None, mode=DispatchMode.EMIT, tag="temp")
        bus.on('b', lambda p: None, mode=DispatchMode.EMIT, tag="keep")
        bus.off_tag("temp")
        assert len(bus.listeners('a')) == 0
        assert len(bus.listeners('b')) == 1


# ── Test Emit Mode ───────────────────────────────────────────

class TestEmitMode:
    @pytest.mark.asyncio
    async def test_emit_calls_listeners(self, bus):
        results = []
        async def handler(payload):
            results.append(payload['x'])
        bus.on('test', handler, mode=DispatchMode.EMIT)
        count = await bus.emit('test', {'x': 42})
        assert count == 1
        assert results == [42]

    @pytest.mark.asyncio
    async def test_emit_multiple_listeners(self, bus):
        results = []
        async def h1(payload):
            results.append('h1')
        async def h2(payload):
            results.append('h2')
        bus.on('test', h1, mode=DispatchMode.EMIT)
        bus.on('test', h2, mode=DispatchMode.EMIT)
        count = await bus.emit('test', {})
        assert count == 2
        assert 'h1' in results
        assert 'h2' in results

    @pytest.mark.asyncio
    async def test_emit_no_listeners(self, bus):
        count = await bus.emit('nonexistent', {})
        assert count == 0

    @pytest.mark.asyncio
    async def test_emit_disabled_listener_skipped(self, bus):
        results = []
        async def handler(payload):
            results.append(1)
        listener = bus.on('test', handler, mode=DispatchMode.EMIT)
        bus.disable(listener)
        count = await bus.emit('test', {})
        assert count == 0
        assert results == []

    @pytest.mark.asyncio
    async def test_emit_sync_function(self, bus):
        results = []
        def sync_handler(payload):
            results.append(payload['v'])
        bus.on('test', sync_handler, mode=DispatchMode.EMIT)
        count = await bus.emit('test', {'v': 99})
        assert count == 1
        assert results == [99]

    @pytest.mark.asyncio
    async def test_emit_error_doesnt_crash(self, bus):
        async def bad_handler(payload):
            raise RuntimeError("oops")
        async def good_handler(payload):
            pass
        bus.on('test', bad_handler, mode=DispatchMode.EMIT)
        bus.on('test', good_handler, mode=DispatchMode.EMIT)
        count = await bus.emit('test', {})
        assert count == 1  # good_handler still ran


# ── Test Waterfall Mode ──────────────────────────────────────

class TestWaterfallMode:
    @pytest.mark.asyncio
    async def test_waterfall_approved(self, bus):
        async def allow(payload, next_fn):
            return await next_fn(payload)

        bus.on('order', allow, mode=DispatchMode.WATERFALL, priority=10)
        result = await bus.waterfall('order', {'amount': 25})
        assert result.approved is True
        assert result.payload == {'amount': 25}

    @pytest.mark.asyncio
    async def test_waterfall_rejected(self, bus):
        async def deny(payload, next_fn):
            return {'rejected': True, 'reason': 'Too large'}

        bus.on('order', deny, mode=DispatchMode.WATERFALL, priority=10)
        result = await bus.waterfall('order', {'amount': 200})
        assert result.approved is False
        assert result.reason == 'Too large'

    @pytest.mark.asyncio
    async def test_waterfall_priority_order(self, bus):
        order = []

        async def first(payload, next_fn):
            order.append('first')
            return await next_fn(payload)

        async def second(payload, next_fn):
            order.append('second')
            return await next_fn(payload)

        bus.on('test', second, mode=DispatchMode.WATERFALL, priority=100)
        bus.on('test', first, mode=DispatchMode.WATERFALL, priority=10)

        await bus.waterfall('test', {})
        assert order == ['first', 'second']

    @pytest.mark.asyncio
    async def test_waterfall_early_reject_stops_chain(self, bus):
        order = []

        async def deny(payload, next_fn):
            order.append('deny')
            return {'rejected': True, 'reason': 'blocked'}

        async def should_not_run(payload, next_fn):
            order.append('should_not_run')
            return await next_fn(payload)

        bus.on('test', deny, mode=DispatchMode.WATERFALL, priority=10)
        bus.on('test', should_not_run, mode=DispatchMode.WATERFALL, priority=100)

        result = await bus.waterfall('test', {})
        assert result.approved is False
        assert 'should_not_run' not in order

    @pytest.mark.asyncio
    async def test_waterfall_modify_payload(self, bus):
        async def reducer(payload, next_fn):
            payload['amount'] = payload['amount'] * 0.75
            return await next_fn(payload)

        bus.on('test', reducer, mode=DispatchMode.WATERFALL, priority=10)
        result = await bus.waterfall('test', {'amount': 100})
        assert result.approved is True
        assert result.payload['amount'] == 75.0

    @pytest.mark.asyncio
    async def test_waterfall_no_listeners(self, bus):
        result = await bus.waterfall('nonexistent', {'x': 1})
        assert result.approved is True
        assert result.payload == {'x': 1}

    @pytest.mark.asyncio
    async def test_waterfall_abort_controller(self, bus):
        async def handler(payload, next_fn):
            return await next_fn(payload)

        bus.on('test', handler, mode=DispatchMode.WATERFALL)
        controller = AbortController()
        controller.abort("manual cancel")

        result = await bus.waterfall('test', {}, controller=controller)
        assert result.approved is False
        assert 'Aborted' in result.reason

    @pytest.mark.asyncio
    async def test_waterfall_timing(self, bus):
        async def slow(payload, next_fn):
            await asyncio.sleep(0.01)
            return await next_fn(payload)

        bus.on('test', slow, mode=DispatchMode.WATERFALL)
        result = await bus.waterfall('test', {})
        assert result.timing_ms > 5  # At least 5ms

    @pytest.mark.asyncio
    async def test_waterfall_listener_error_rejects(self, bus):
        async def crash(payload, next_fn):
            raise RuntimeError("boom")

        bus.on('test', crash, mode=DispatchMode.WATERFALL)
        result = await bus.waterfall('test', {})
        assert result.approved is False
        assert 'Listener error' in result.reason


# ── Test Fanout Mode ─────────────────────────────────────────

class TestFanoutMode:
    @pytest.mark.asyncio
    async def test_fanout_calls_all(self, bus):
        results = []

        async def h1(payload):
            results.append('h1')

        async def h2(payload):
            results.append('h2')

        bus.on('test', h1, mode=DispatchMode.FANOUT)
        bus.on('test', h2, mode=DispatchMode.FANOUT)

        await bus.fanout('test', {})
        assert 'h1' in results
        assert 'h2' in results

    @pytest.mark.asyncio
    async def test_fanout_returns_results(self, bus):
        async def h1(payload):
            return 'result_1'

        async def h2(payload):
            return 'result_2'

        bus.on('test', h1, mode=DispatchMode.FANOUT)
        bus.on('test', h2, mode=DispatchMode.FANOUT)

        results = await bus.fanout('test', {})
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_fanout_empty(self, bus):
        results = await bus.fanout('nonexistent', {})
        assert results == []


# ── Test Serial Mode ─────────────────────────────────────────

class TestSerialMode:
    @pytest.mark.asyncio
    async def test_serial_in_order(self, bus):
        order = []

        async def h1(payload):
            order.append('h1')

        async def h2(payload):
            order.append('h2')

        bus.on('test', h1, mode=DispatchMode.SERIAL, priority=10)
        bus.on('test', h2, mode=DispatchMode.SERIAL, priority=20)

        await bus.serial('test', {})
        assert order == ['h1', 'h2']

    @pytest.mark.asyncio
    async def test_serial_awaits_each(self, bus):
        order = []

        async def h1(payload):
            await asyncio.sleep(0.01)
            order.append('h1_done')

        async def h2(payload):
            order.append('h2_done')

        bus.on('test', h1, mode=DispatchMode.SERIAL, priority=10)
        bus.on('test', h2, mode=DispatchMode.SERIAL, priority=20)

        await bus.serial('test', {})
        assert order == ['h1_done', 'h2_done']


# ── Test AbortController ─────────────────────────────────────

class TestAbortController:
    def test_create(self):
        ctrl = AbortController()
        assert ctrl.aborted is False
        assert ctrl.reason == ""

    def test_abort(self):
        ctrl = AbortController()
        ctrl.abort("test reason")
        assert ctrl.aborted is True
        assert ctrl.reason == "test reason"


# ── Test History & Stats ─────────────────────────────────────

class TestHistoryAndStats:
    @pytest.mark.asyncio
    async def test_history_recorded(self, bus):
        bus.on('test', lambda p: None, mode=DispatchMode.EMIT)
        await bus.emit('test', {'x': 1})

        history = bus.history()
        assert len(history) == 1
        assert history[0]['event'] == 'test'
        assert history[0]['mode'] == 'emit'

    @pytest.mark.asyncio
    async def test_stats(self, bus):
        bus.on('a', lambda p: None, mode=DispatchMode.EMIT)
        bus.on('b', lambda p: None, mode=DispatchMode.WATERFALL)
        await bus.emit('a', {})

        stats = bus.stats()
        assert stats['total_dispatches'] == 1
        assert stats['total_listeners'] == 2
        assert 'a' in stats['events_registered']
        assert stats['by_mode']['emit'] == 1


# ── Test Event Names ─────────────────────────────────────────

class TestEventNames:
    def test_all_events_defined(self):
        assert Events.ORDER_SUBMIT == "order/submit"
        assert Events.RISK_APPROVED == "risk/approved"
        assert Events.PNL_SNAPSHOT == "pnl/snapshot"


# ── Test Integration ──────────────────────────────────────────

class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_order_flow(self, bus):
        """Simulate a complete order flow through the bus."""
        flow = []

        async def risk_check(payload, next_fn):
            flow.append('risk_check')
            if payload['amount_usd'] > 100:
                return {'rejected': True, 'reason': 'Over limit'}
            return await next_fn(payload)

        async def log_order(payload):
            flow.append('log_order')

        async def execute(payload):
            flow.append('execute')
            return {'filled': True}

        bus.on('order/submit', risk_check, mode=DispatchMode.WATERFALL, priority=10)
        bus.on('order/submitted', log_order, mode=DispatchMode.EMIT)

        # Waterfall: risk check → execute
        result = await bus.waterfall('order/submit', {
            'token': 'FART', 'amount_usd': 25.0,
        })

        # Emit: log
        await bus.emit('order/submitted', {
            'token': 'FART', 'amount_usd': 25.0,
        })

        assert result.approved is True
        assert 'risk_check' in flow
        assert 'log_order' in flow

    @pytest.mark.asyncio
    async def test_risk_rejects_order(self, bus):
        """Test that risk guard can reject orders via waterfall."""
        async def deny_large(payload, next_fn):
            if payload['amount_usd'] > 100:
                return {'rejected': True, 'reason': 'Position too large'}
            return await next_fn(payload)

        bus.on('order/submit', deny_large, mode=DispatchMode.WATERFALL, priority=10)

        result = await bus.waterfall('order/submit', {
            'token': 'FART', 'amount_usd': 200.0,
        })

        assert result.approved is False
        assert result.reason == 'Position too large'

    @pytest.mark.asyncio
    async def test_create_integrated_bus(self):
        """Test the factory function creates a bus with default listeners."""
        bus = create_integrated_bus()
        assert isinstance(bus, EventBus)
        # Should have no listeners (no modules passed)
        assert len(bus.listeners()) == 0


# ── Test Mixed Modes ─────────────────────────────────────────

class TestMixedModes:
    @pytest.mark.asyncio
    async def test_same_event_different_modes(self, bus):
        """Same event can have listeners in different modes."""
        emit_results = []
        waterfall_results = []

        async def emit_handler(payload):
            emit_results.append('emit')

        async def waterfall_handler(payload, next_fn):
            waterfall_results.append('waterfall')
            return await next_fn(payload)

        bus.on('order', emit_handler, mode=DispatchMode.EMIT)
        bus.on('order', waterfall_handler, mode=DispatchMode.WATERFALL)

        # Emit only calls EMIT listeners
        await bus.emit('order', {})
        assert emit_results == ['emit']
        assert waterfall_results == []

        # Waterfall only calls WATERFALL listeners
        await bus.waterfall('order', {})
        assert waterfall_results == ['waterfall']
