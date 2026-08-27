"""
Tests for Moon Dev's Session Log & Audit Trail
"""

import pytest
import asyncio
import os
import tempfile
import json
from datetime import datetime, timedelta
from src.session_log import (
    SessionLog, LogEvent, EventType, CSVStorage, InMemoryStorage,
    create_session_log, create_test_session_log, EVENT_DESCRIPTIONS,
)


# ── Fixtures ───────────────────────────────────────────────────

@pytest.fixture
def mem_log():
    """Create an in-memory session log for testing."""
    return create_test_session_log(session_id="test-001")


@pytest.fixture
def csv_log(tmp_path):
    """Create a CSV-backed session log for testing."""
    return create_session_log(log_dir=str(tmp_path), session_id="test-csv")


# ── Test LogEvent ──────────────────────────────────────────────

class TestLogEvent:
    def test_event_creation(self):
        event = LogEvent(
            event_type="order/submitted",
            data={"token": "FART", "amount": 25.0},
        )
        assert event.event_type == "order/submitted"
        assert event.data["token"] == "FART"
        assert event.id  # Auto-generated
        assert event.timestamp  # Auto-generated

    def test_event_to_dict(self):
        event = LogEvent(
            event_type="signal/generated",
            data={"score": 5},
            session_id="s1",
            signal_id="sig-1",
        )
        d = event.to_dict()
        assert d['event_type'] == 'signal/generated'
        assert d['session_id'] == 's1'
        assert d['signal_id'] == 'sig-1'
        assert 'description' in d

    def test_event_from_dict(self):
        d = {
            'id': 'abc123',
            'event_type': 'order/filled',
            'data': {'price': 0.0042},
            'timestamp': '2026-01-01T00:00:00',
            'session_id': 's1',
            'signal_id': 'sig-1',
        }
        event = LogEvent.from_dict(d)
        assert event.id == 'abc123'
        assert event.event_type == 'order/filled'
        assert event.data['price'] == 0.0042


# ── Test Event Types ──────────────────────────────────────────

class TestEventTypes:
    def test_all_event_types_have_descriptions(self):
        for et in EventType:
            assert et.value in EVENT_DESCRIPTIONS

    def test_event_types_are_strings(self):
        assert EventType.ORDER_SUBMITTED.value == "order/submitted"
        assert EventType.RISK_DENIED.value == "risk/denied"


# ── Test InMemoryStorage ──────────────────────────────────────

class TestInMemoryStorage:
    @pytest.mark.asyncio
    async def test_append_and_query(self):
        storage = InMemoryStorage()
        event = LogEvent(event_type="test/event", data={"key": "value"})
        await storage.append(event)

        results = await storage.query()
        assert len(results) == 1
        assert results[0].event_type == "test/event"

    @pytest.mark.asyncio
    async def test_query_with_limit(self):
        storage = InMemoryStorage()
        for i in range(10):
            await storage.append(LogEvent(event_type="test/event", data={"i": i}))

        results = await storage.query(limit=5)
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_query_by_event_type(self):
        storage = InMemoryStorage()
        await storage.append(LogEvent(event_type="order/submitted", data={}))
        await storage.append(LogEvent(event_type="signal/generated", data={}))
        await storage.append(LogEvent(event_type="order/submitted", data={}))

        results = await storage.query(filters={'event_type': 'order/submitted'})
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_query_by_token(self):
        storage = InMemoryStorage()
        await storage.append(LogEvent(event_type="order/submitted", data={"token": "FART"}))
        await storage.append(LogEvent(event_type="order/submitted", data={"token": "BONK"}))

        results = await storage.query(filters={'token': 'FART'})
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_query_by_session_id(self):
        storage = InMemoryStorage()
        await storage.append(LogEvent(event_type="test", data={}, session_id="s1"))
        await storage.append(LogEvent(event_type="test", data={}, session_id="s2"))

        results = await storage.query(filters={'session_id': 's1'})
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_query_empty(self):
        storage = InMemoryStorage()
        results = await storage.query()
        assert results == []


# ── Test CSVStorage ───────────────────────────────────────────

class TestCSVStorage:
    @pytest.mark.asyncio
    async def test_append_creates_file(self, tmp_path):
        storage = CSVStorage(log_dir=str(tmp_path))
        event = LogEvent(event_type="test/event", data={"key": "value"})
        await storage.append(event)

        assert os.path.exists(storage.log_path)

    @pytest.mark.asyncio
    async def test_append_and_query(self, tmp_path):
        storage = CSVStorage(log_dir=str(tmp_path))
        await storage.append(LogEvent(event_type="order/submitted", data={"token": "FART"}))
        await storage.append(LogEvent(event_type="signal/generated", data={"token": "BONK"}))

        results = await storage.query()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_query_by_event_type(self, tmp_path):
        storage = CSVStorage(log_dir=str(tmp_path))
        await storage.append(LogEvent(event_type="order/submitted", data={}))
        await storage.append(LogEvent(event_type="signal/generated", data={}))
        await storage.append(LogEvent(event_type="order/submitted", data={}))

        results = await storage.query(filters={'event_type': 'order/submitted'})
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_persistence_across_queries(self, tmp_path):
        storage = CSVStorage(log_dir=str(tmp_path))
        await storage.append(LogEvent(event_type="test", data={"x": 1}))

        # Query twice — should get same results
        r1 = await storage.query()
        r2 = await storage.query()
        assert len(r1) == len(r2) == 1

    @pytest.mark.asyncio
    async def test_query_empty_file(self, tmp_path):
        storage = CSVStorage(log_dir=str(tmp_path))
        results = await storage.query()
        assert results == []


# ── Test SessionLog Core ──────────────────────────────────────

class TestSessionLog:
    @pytest.mark.asyncio
    async def test_log_event(self, mem_log):
        event = await mem_log.log('order/submitted', {'token': 'FART', 'amount': 25.0})
        assert event.event_type == 'order/submitted'
        assert event.session_id == 'test-001'

    @pytest.mark.asyncio
    async def test_log_with_signal_id(self, mem_log):
        event = await mem_log.log('signal/generated', {'score': 5}, signal_id='sig-001')
        assert event.signal_id == 'sig-001'

    @pytest.mark.asyncio
    async def test_log_invalid_event_type(self, mem_log):
        with pytest.raises(ValueError, match="Unknown event type"):
            await mem_log.log('invalid/type', {})

    @pytest.mark.asyncio
    async def test_log_validates_all_types(self, mem_log):
        for et in EventType:
            event = await mem_log.log(et.value, {'test': True})
            assert event.event_type == et.value


# ── Test Custom Validators ────────────────────────────────────

class TestValidators:
    @pytest.mark.asyncio
    async def test_custom_validator_passes(self, mem_log):
        async def validate_order(data):
            if data.get('amount_usd', 0) <= 0:
                return "Amount must be positive"
            return None

        mem_log.register_validator('order/submitted', validate_order)

        event = await mem_log.log('order/submitted', {'amount_usd': 25.0})
        assert event.event_type == 'order/submitted'

    @pytest.mark.asyncio
    async def test_custom_validator_rejects(self, mem_log):
        async def validate_order(data):
            if data.get('amount_usd', 0) <= 0:
                return "Amount must be positive"
            return None

        mem_log.register_validator('order/submitted', validate_order)

        with pytest.raises(ValueError, match="Amount must be positive"):
            await mem_log.log('order/submitted', {'amount_usd': -5.0})


# ── Test Query Methods ────────────────────────────────────────

class TestQueryMethods:
    @pytest.mark.asyncio
    async def test_get_events_by_type(self, mem_log):
        await mem_log.log('order/submitted', {'token': 'A'})
        await mem_log.log('signal/generated', {'token': 'B'})
        await mem_log.log('order/submitted', {'token': 'C'})

        events = await mem_log.get_events(event_type='order/submitted')
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_get_events_by_token(self, mem_log):
        await mem_log.log('order/submitted', {'token': 'FART'})
        await mem_log.log('signal/generated', {'token': 'BONK'})

        events = await mem_log.get_events(token='FART')
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_get_events_with_limit(self, mem_log):
        for i in range(10):
            await mem_log.log('order/submitted', {'i': i})

        events = await mem_log.get_events(limit=3)
        assert len(events) == 3


# ── Test Trade Chain ──────────────────────────────────────────

class TestTradeChain:
    @pytest.mark.asyncio
    async def test_reconstruct_trade_chain(self, mem_log):
        """Test full trade lifecycle reconstruction."""
        # Signal
        await mem_log.log(EventType.SIGNAL_GENERATED, {
            'token': 'FART', 'score': 4, 'strategy': 'momentum',
        }, signal_id='sig-001')

        # Validation
        await mem_log.log(EventType.SIGNAL_VALIDATED, {
            'token': 'FART', 'approved': True,
        }, signal_id='sig-001')

        # Risk approval
        await mem_log.log(EventType.RISK_APPROVED, {
            'token': 'FART', 'guard': 'position_size',
        }, signal_id='sig-001')

        # Order
        await mem_log.log(EventType.ORDER_SUBMITTED, {
            'token': 'FART', 'side': 'buy', 'amount_usd': 25.0,
        }, signal_id='sig-001')

        # Fill
        await mem_log.log(EventType.ORDER_FILLED, {
            'token': 'FART', 'fill_price': 0.0042,
        }, signal_id='sig-001')

        # Position opened
        await mem_log.log(EventType.POSITION_OPENED, {
            'token': 'FART', 'entry_price': 0.0042,
        }, signal_id='sig-001')

        # Position closed
        await mem_log.log(EventType.POSITION_CLOSED, {
            'token': 'FART', 'pnl_usd': 3.50, 'holding_minutes': 45,
            'strategy': 'momentum', 'regime': 'trending',
        }, signal_id='sig-001')

        trades = await mem_log.get_trade_chain('FART', days=1)
        assert len(trades) == 1
        assert trades[0]['pnl_usd'] == 3.50
        assert trades[0]['holding_time'] == 45
        assert trades[0]['entry_signals'][0]['strategy'] == 'momentum'

    @pytest.mark.asyncio
    async def test_multiple_trades(self, mem_log):
        """Test multiple trades for the same token."""
        for i in range(3):
            sid = f'sig-{i:03d}'
            await mem_log.log(EventType.SIGNAL_GENERATED, {'token': 'FART'}, signal_id=sid)
            await mem_log.log(EventType.POSITION_CLOSED, {
                'token': 'FART', 'pnl_usd': (i + 1) * 1.0,
            }, signal_id=sid)

        trades = await mem_log.get_trade_chain('FART', days=1)
        assert len(trades) == 3
        assert trades[0]['pnl_usd'] == 1.0
        assert trades[2]['pnl_usd'] == 3.0

    @pytest.mark.asyncio
    async def test_empty_trade_chain(self, mem_log):
        trades = await mem_log.get_trade_chain('NONEXISTENT', days=1)
        assert trades == []


# ── Test Accuracy Report ──────────────────────────────────────

class TestAccuracyReport:
    @pytest.mark.asyncio
    async def test_empty_report(self, mem_log):
        report = await mem_log.get_accuracy_report(days=30)
        assert report['total_trades'] == 0

    @pytest.mark.asyncio
    async def test_report_with_trades(self, mem_log):
        # Add some closed trades
        for i, pnl in enumerate([5.0, -2.0, 3.0, -1.0, 8.0]):
            await mem_log.log(EventType.POSITION_CLOSED, {
                'token': f'TOKEN{i}',
                'pnl_usd': pnl,
                'pnl_pct': pnl * 2,
                'holding_minutes': 30 + i * 10,
                'strategy': 'momentum' if i % 2 == 0 else 'mean_reversion',
                'regime': 'trending' if i < 3 else 'ranging',
                'side': 'sell',
            })

        report = await mem_log.get_accuracy_report(days=30)
        assert report['total_trades'] == 5
        assert report['overall']['total_pnl'] == 13.0
        assert report['overall']['win_rate'] == 0.6  # 3/5 wins

    @pytest.mark.asyncio
    async def test_report_by_strategy(self, mem_log):
        await mem_log.log(EventType.POSITION_CLOSED, {
            'token': 'A', 'pnl_usd': 5.0, 'strategy': 'momentum',
        })
        await mem_log.log(EventType.POSITION_CLOSED, {
            'token': 'B', 'pnl_usd': -2.0, 'strategy': 'momentum',
        })
        await mem_log.log(EventType.POSITION_CLOSED, {
            'token': 'C', 'pnl_usd': 3.0, 'strategy': 'mean_reversion',
        })

        report = await mem_log.get_accuracy_report(days=30)
        assert 'momentum' in report['by_strategy']
        assert 'mean_reversion' in report['by_strategy']
        assert report['by_strategy']['momentum']['count'] == 2

    @pytest.mark.asyncio
    async def test_report_profit_factor(self, mem_log):
        # 2 wins (+10), 1 loss (-5) = profit factor = 10/5 = 2.0
        await mem_log.log(EventType.POSITION_CLOSED, {'token': 'A', 'pnl_usd': 5.0})
        await mem_log.log(EventType.POSITION_CLOSED, {'token': 'B', 'pnl_usd': 5.0})
        await mem_log.log(EventType.POSITION_CLOSED, {'token': 'C', 'pnl_usd': -5.0})

        report = await mem_log.get_accuracy_report(days=30)
        assert report['overall']['profit_factor'] == 2.0


# ── Test Session Summary ──────────────────────────────────────

class TestSessionSummary:
    @pytest.mark.asyncio
    async def test_summary(self, mem_log):
        await mem_log.log('order/submitted', {'token': 'A'})
        await mem_log.log('order/submitted', {'token': 'B'})
        await mem_log.log('signal/generated', {'token': 'C'})

        summary = await mem_log.get_session_summary()
        assert summary['session_id'] == 'test-001'
        assert summary['total_events'] == 3
        assert summary['events_by_type']['order/submitted'] == 2
        assert summary['events_by_type']['signal/generated'] == 1

    @pytest.mark.asyncio
    async def test_summary_empty(self, mem_log):
        summary = await mem_log.get_session_summary()
        assert summary['total_events'] == 0


# ── Test Recent Activity ──────────────────────────────────────

class TestRecentActivity:
    @pytest.mark.asyncio
    async def test_recent_activity(self, mem_log):
        for i in range(5):
            await mem_log.log('order/submitted', {'i': i})

        activity = await mem_log.get_recent_activity(limit=3)
        assert len(activity) == 3
        # Should contain the most recent events (may not be in exact order due to timestamp resolution)
        values = [a['data']['i'] for a in activity]
        assert 4 in values  # Most recent should be included

    @pytest.mark.asyncio
    async def test_recent_activity_format(self, mem_log):
        await mem_log.log('order/submitted', {'token': 'FART'})
        activity = await mem_log.get_recent_activity(limit=1)
        assert 'event_type' in activity[0]
        assert 'data' in activity[0]
        assert 'timestamp' in activity[0]


# ── Test Factory Functions ────────────────────────────────────

class TestFactories:
    def test_create_test_session_log(self):
        log = create_test_session_log()
        assert isinstance(log.storage, InMemoryStorage)
        assert log.session_id

    def test_create_session_log_csv(self, tmp_path):
        log = create_session_log(log_dir=str(tmp_path))
        assert isinstance(log.storage, CSVStorage)


# ── Test Integration with Risk Guard ──────────────────────────

class TestRiskGuardIntegration:
    @pytest.mark.asyncio
    async def test_risk_events_logged(self, mem_log):
        """Test that risk guard decisions are properly logged."""
        await mem_log.log(EventType.RISK_APPROVED, {
            'order': {'token': 'FART', 'amount_usd': 25.0},
            'guard': 'position_size',
        })

        await mem_log.log(EventType.RISK_DENIED, {
            'order': {'token': 'BONK', 'amount_usd': 500.0},
            'guard': 'daily_loss',
            'reason': 'Daily loss limit exceeded',
        })

        approved = await mem_log.get_events(event_type=EventType.RISK_APPROVED)
        denied = await mem_log.get_events(event_type=EventType.RISK_DENIED)

        assert len(approved) == 1
        assert len(denied) == 1
        assert denied[0].data['reason'] == 'Daily loss limit exceeded'
