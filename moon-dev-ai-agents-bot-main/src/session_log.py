"""
📋 Moon Dev's Session Log & Audit Trail
DSH Pattern: Append-only SessionEventMap — every event is durable and queryable.

Every decision, model call, and trade is permanently recorded.
No more print() statements — everything goes through the log.

Usage:
    log = SessionLog(storage=csv_storage)
    await log.log('order/submitted', {'token': 'FART', 'side': 'buy', 'amount_usd': 25.0})
    trades = await log.get_trade_chain('FART', days=30)
    report = await log.get_accuracy_report(days=7)
"""

import os
import json
import csv
import uuid
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable
from enum import Enum


# ── Event Types ────────────────────────────────────────────────

class EventType(str, Enum):
    """All recognized event types — matching DSH's SessionEventMap pattern."""
    # Signal lifecycle
    SIGNAL_GENERATED = "signal/generated"
    SIGNAL_VALIDATED = "signal/validated"
    SIGNAL_LLM_REASON = "signal/llm_reason"

    # Order lifecycle
    ORDER_INTENT = "order/intent"
    ORDER_SUBMITTED = "order/submitted"
    ORDER_FILLED = "order/filled"
    ORDER_PARTIAL = "order/partial"
    ORDER_FAILED = "order/failed"

    # Position lifecycle
    POSITION_OPENED = "position/opened"
    POSITION_CLOSED = "position/closed"
    POSITION_UPDATED = "position/updated"

    # Portfolio state
    PNL_SNAPSHOT = "pnl/snapshot"
    REGIME_DETECTED = "regime/detected"
    PREDICTION_SCORE = "prediction/score"

    # Risk
    RISK_APPROVED = "risk/approved"
    RISK_DENIED = "risk/denied"

    # System
    AGENT_ERROR = "agent/error"
    MODEL_CALL = "model/call"


EVENT_DESCRIPTIONS = {
    EventType.SIGNAL_GENERATED: "Strategy produced a raw signal",
    EventType.SIGNAL_VALIDATED: "Pipeline approved/rejected a signal",
    EventType.SIGNAL_LLM_REASON: "LLM reasoning for a decision",
    EventType.ORDER_INTENT: "About to place an order",
    EventType.ORDER_SUBMITTED: "Order sent to exchange",
    EventType.ORDER_FILLED: "Order confirmed filled",
    EventType.ORDER_PARTIAL: "Partial fill",
    EventType.ORDER_FAILED: "Order failed",
    EventType.POSITION_OPENED: "New position established",
    EventType.POSITION_CLOSED: "Position exited",
    EventType.POSITION_UPDATED: "Stop-loss or take-profit adjusted",
    EventType.PNL_SNAPSHOT: "Periodic portfolio value",
    EventType.REGIME_DETECTED: "Market regime changed",
    EventType.PREDICTION_SCORE: "PredictionEngine output",
    EventType.RISK_APPROVED: "Risk guard approved order",
    EventType.RISK_DENIED: "Risk guard denied order",
    EventType.AGENT_ERROR: "Agent encountered an error",
    EventType.MODEL_CALL: "LLM API call made",
}


# ── Event Data ─────────────────────────────────────────────────

@dataclass
class LogEvent:
    """A single immutable log event."""
    event_type: str
    data: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    session_id: str = ""
    signal_id: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'event_type': self.event_type,
            'description': EVENT_DESCRIPTIONS.get(self.event_type, 'Unknown'),
            'data': self.data,
            'timestamp': self.timestamp,
            'session_id': self.session_id,
            'signal_id': self.signal_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'LogEvent':
        return cls(
            id=d.get('id', str(uuid.uuid4())[:12]),
            event_type=d['event_type'],
            data=d.get('data', {}),
            timestamp=d.get('timestamp', ''),
            session_id=d.get('session_id', ''),
            signal_id=d.get('signal_id'),
        )


# ── Storage Backends ───────────────────────────────────────────

class CSVStorage:
    """Simple CSV file storage — works without MongoDB."""

    def __init__(self, log_dir: str = None):
        self.log_dir = log_dir or os.path.join(os.path.dirname(__file__), 'data')
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_path = os.path.join(self.log_dir, 'session_log.csv')

    async def append(self, event: LogEvent):
        """Append event to CSV."""
        row = {
            'id': event.id,
            'event_type': event.event_type,
            'data': json.dumps(event.data, default=str),
            'timestamp': event.timestamp,
            'session_id': event.session_id,
            'signal_id': event.signal_id or '',
        }

        file_exists = os.path.exists(self.log_path)
        with open(self.log_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

    async def query(self, filters: dict = None, limit: int = 100) -> List[LogEvent]:
        """Query events with optional filters."""
        if not os.path.exists(self.log_path):
            return []

        events = []
        with open(self.log_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                event = LogEvent(
                    id=row['id'],
                    event_type=row['event_type'],
                    data=json.loads(row['data']),
                    timestamp=row['timestamp'],
                    session_id=row.get('session_id', ''),
                    signal_id=row.get('signal_id') or None,
                )

                if self._matches_filters(event, filters):
                    events.append(event)

        # Sort by timestamp descending (newest first)
        events.sort(key=lambda e: e.timestamp, reverse=True)
        return events[:limit]

    def _matches_filters(self, event: LogEvent, filters: dict) -> bool:
        """Check if an event matches the given filters."""
        if not filters:
            return True

        if 'event_type' in filters:
            if event.event_type != filters['event_type']:
                return False

        if 'token' in filters:
            if filters['token'] not in json.dumps(event.data):
                return False

        if 'session_id' in filters:
            if event.session_id != filters['session_id']:
                return False

        if 'since' in filters:
            if event.timestamp < filters['since']:
                return False

        if 'signal_id' in filters:
            if event.signal_id != filters['signal_id']:
                return False

        return True


class InMemoryStorage:
    """In-memory storage for testing — no disk I/O."""

    def __init__(self):
        self.events: List[LogEvent] = []
        self._seq = 0  # Monotonic sequence for stable ordering

    async def append(self, event: LogEvent):
        self._seq += 1
        event._seq = self._seq  # Attach sequence number
        self.events.append(event)

    async def query(self, filters: dict = None, limit: int = 100) -> List[LogEvent]:
        results = [e for e in self.events if self._matches(e, filters)]
        results.sort(key=lambda e: getattr(e, '_seq', 0), reverse=True)
        return results[:limit]

    def _matches(self, event: LogEvent, filters: dict) -> bool:
        if not filters:
            return True
        if 'event_type' in filters and event.event_type != filters['event_type']:
            return False
        if 'token' in filters and filters['token'] not in json.dumps(event.data):
            return False
        if 'session_id' in filters and event.session_id != filters['session_id']:
            return False
        if 'signal_id' in filters and event.signal_id != filters['signal_id']:
            return False
        return True


# ── Session Log ────────────────────────────────────────────────

class SessionLog:
    """
    DSH-style append-only session log.

    Every trading event is permanently recorded and queryable.
    Supports CSV or in-memory storage (MongoDB optional).
    """

    def __init__(self, storage=None, session_id: str = None):
        self.storage = storage or InMemoryStorage()
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self._validators: Dict[str, Callable] = {}

    def register_validator(self, event_type: str, fn: Callable):
        """Register a validation function for an event type."""
        self._validators[event_type] = fn

    async def log(self, event_type: str, data: dict, signal_id: str = None) -> LogEvent:
        """
        Append an event to the log.

        Args:
            event_type: One of the EventType values
            data: Event-specific payload
            signal_id: Optional correlation ID to chain related events

        Returns:
            The created LogEvent
        """
        # Validate event type
        valid_types = [e.value for e in EventType]
        if event_type not in valid_types:
            raise ValueError(
                f"Unknown event type: {event_type}. "
                f"Valid types: {valid_types}"
            )

        # Run custom validator if registered
        if event_type in self._validators:
            error = await self._validators[event_type](data)
            if error:
                raise ValueError(f"Validation failed for {event_type}: {error}")

        event = LogEvent(
            event_type=event_type,
            data=data,
            session_id=self.session_id,
            signal_id=signal_id,
        )

        await self.storage.append(event)
        return event

    async def get_events(self, event_type: str = None, token: str = None,
                         limit: int = 100, since: str = None) -> List[LogEvent]:
        """Query events with optional filters."""
        filters = {}
        if event_type:
            filters['event_type'] = event_type
        if token:
            filters['token'] = token
        if since:
            filters['since'] = since

        return await self.storage.query(filters=filters, limit=limit)

    async def get_trade_chain(self, token: str, days: int = 30) -> List[dict]:
        """
        Reconstruct the full decision chain for a token.

        Returns a list of trade narratives, each containing:
        - entry_signals: What signals triggered the trade
        - validation: Was the signal validated
        - risk_approval/denial: Risk guard decisions
        - order: Order details
        - exit: Position close details
        - pnl_usd: Profit/loss
        - holding_time: How long the position was held
        """
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        events = await self.storage.query(
            filters={'token': token, 'since': cutoff},
            limit=1000
        )

        # Sort chronologically (oldest first) — use sequence as tiebreaker
        events.sort(key=lambda e: getattr(e, '_seq', 0))

        # Rebuild trade narratives
        trades = []
        current = {}

        for event in events:
            etype = event.event_type
            data = event.data

            if etype == EventType.SIGNAL_GENERATED:
                current = {
                    'token': token,
                    'entry_signals': [data],
                    'timestamp': event.timestamp,
                    'signal_id': event.signal_id,
                }
            elif etype == EventType.SIGNAL_VALIDATED:
                current['validation'] = data
            elif etype == EventType.SIGNAL_LLM_REASON:
                current['llm_reasoning'] = data
            elif etype == EventType.RISK_APPROVED:
                current['risk_approval'] = data
            elif etype == EventType.RISK_DENIED:
                current['risk_denial'] = data
            elif etype == EventType.ORDER_SUBMITTED:
                current['order'] = data
            elif etype == EventType.ORDER_FILLED:
                current['fill'] = data
            elif etype == EventType.ORDER_FAILED:
                current['failure'] = data
            elif etype == EventType.POSITION_OPENED:
                current['position_open'] = data
            elif etype == EventType.POSITION_CLOSED:
                current['exit'] = data
                current['pnl_usd'] = data.get('pnl_usd', 0)
                current['holding_time'] = data.get('holding_minutes', 0)
                trades.append(current)
                current = {}
            elif etype == EventType.REGIME_DETECTED:
                current['regime'] = data.get('regime', 'unknown')

        return trades

    async def get_accuracy_report(self, days: int = 30) -> dict:
        """
        What actually makes money?

        Returns breakdown by strategy, regime, and overall performance.
        """
        # Get all position/close events
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        close_events = await self.storage.query(
            filters={'event_type': EventType.POSITION_CLOSED, 'since': cutoff},
            limit=1000
        )

        if not close_events:
            return {
                'period_days': days,
                'total_trades': 0,
                'message': 'No closed trades found in this period',
            }

        # Get all signal events for strategy breakdown
        signal_events = await self.storage.query(
            filters={'event_type': EventType.SIGNAL_GENERATED, 'since': cutoff},
            limit=1000
        )

        # Build trade records
        trades = []
        for event in close_events:
            data = event.data
            trades.append({
                'token': data.get('token', 'unknown'),
                'pnl_usd': data.get('pnl_usd', 0),
                'pnl_pct': data.get('pnl_pct', 0),
                'holding_minutes': data.get('holding_minutes', 0),
                'strategy': data.get('strategy', 'unknown'),
                'regime': data.get('regime', 'unknown'),
                'side': data.get('side', 'unknown'),
                'timestamp': event.timestamp,
            })

        # Calculate stats
        def calc_stats(values):
            if not values:
                return {'count': 0}
            wins = [v for v in values if v > 0]
            losses = [v for v in values if v <= 0]
            return {
                'count': len(values),
                'win_rate': len(wins) / len(values) if values else 0,
                'avg_pnl': sum(values) / len(values),
                'total_pnl': sum(values),
                'best_trade': max(values) if values else 0,
                'worst_trade': min(values) if values else 0,
                'profit_factor': (
                    sum(wins) / abs(sum(losses))
                    if losses and sum(losses) != 0
                    else float('inf') if wins else 0
                ),
            }

        # Overall stats
        all_pnl = [t['pnl_usd'] for t in trades]
        overall = calc_stats(all_pnl)

        # By strategy
        by_strategy = {}
        for t in trades:
            strat = t['strategy']
            by_strategy.setdefault(strat, []).append(t['pnl_usd'])
        by_strategy = {k: calc_stats(v) for k, v in by_strategy.items()}

        # By regime
        by_regime = {}
        for t in trades:
            regime = t['regime']
            by_regime.setdefault(regime, []).append(t['pnl_usd'])
        by_regime = {k: calc_stats(v) for k, v in by_regime.items()}

        # By token
        by_token = {}
        for t in trades:
            token = t['token'][:12] + '...' if len(t['token']) > 12 else t['token']
            by_token.setdefault(token, []).append(t['pnl_usd'])
        by_token = {k: calc_stats(v) for k, v in by_token.items()}

        # Holding time stats
        holding_times = [t['holding_minutes'] for t in trades if t['holding_minutes'] > 0]
        avg_holding = sum(holding_times) / len(holding_times) if holding_times else 0

        return {
            'period_days': days,
            'total_trades': len(trades),
            'overall': overall,
            'by_strategy': by_strategy,
            'by_regime': by_regime,
            'by_token': by_token,
            'avg_holding_minutes': round(avg_holding, 1),
            'trades': trades[-10:],  # Last 10 trades for reference
        }

    async def get_recent_activity(self, limit: int = 20) -> List[dict]:
        """Get the most recent log entries for monitoring."""
        events = await self.storage.query(limit=limit)
        return [e.to_dict() for e in events]

    async def get_session_summary(self) -> dict:
        """Get a summary of the current session's activity."""
        events = await self.storage.query(
            filters={'session_id': self.session_id},
            limit=10000
        )

        by_type = {}
        for event in events:
            by_type[event.event_type] = by_type.get(event.event_type, 0) + 1

        return {
            'session_id': self.session_id,
            'total_events': len(events),
            'events_by_type': by_type,
            'first_event': events[-1].timestamp if events else None,
            'last_event': events[0].timestamp if events else None,
        }


# ── Integration Helpers ────────────────────────────────────────

def create_session_log(log_dir: str = None, session_id: str = None) -> SessionLog:
    """Create a SessionLog with CSV storage (no MongoDB required)."""
    storage = CSVStorage(log_dir=log_dir)
    return SessionLog(storage=storage, session_id=session_id)


def create_test_session_log(session_id: str = None) -> SessionLog:
    """Create a SessionLog with in-memory storage for testing."""
    storage = InMemoryStorage()
    return SessionLog(storage=storage, session_id=session_id)


# ── CLI Interface ──────────────────────────────────────────────

async def main():
    """Demo the session log."""
    log = create_test_session_log()

    print("\n📋 Moon Dev Session Log — Demo\n")

    # Simulate a trading cycle
    signal = await log.log(EventType.SIGNAL_GENERATED, {
        'token': 'FARTCOIN',
        'score': 4,
        'signal': 'BUY',
        'strategy': 'momentum',
        'reasons': ['RSI oversold', 'volume spike'],
    }, signal_id='sig-001')

    await log.log(EventType.SIGNAL_VALIDATED, {
        'token': 'FARTCOIN',
        'approved': True,
        'stage': 'liquidity_check',
    }, signal_id='sig-001')

    await log.log(EventType.RISK_APPROVED, {
        'token': 'FARTCOIN',
        'order': {'side': 'buy', 'amount_usd': 25.0},
        'guard': 'position_size',
    }, signal_id='sig-001')

    await log.log(EventType.ORDER_SUBMITTED, {
        'token': 'FARTCOIN',
        'side': 'buy',
        'amount_usd': 25.0,
        'exchange': 'jupiter',
    }, signal_id='sig-001')

    await log.log(EventType.ORDER_FILLED, {
        'token': 'FARTCOIN',
        'side': 'buy',
        'amount_usd': 25.0,
        'fill_price': 0.0042,
        'slippage': 0.003,
    }, signal_id='sig-001')

    await log.log(EventType.POSITION_OPENED, {
        'token': 'FARTCOIN',
        'side': 'buy',
        'size_usd': 25.0,
        'entry_price': 0.0042,
    }, signal_id='sig-001')

    # Later: position closed
    await log.log(EventType.POSITION_CLOSED, {
        'token': 'FARTCOIN',
        'side': 'sell',
        'pnl_usd': 3.50,
        'pnl_pct': 14.0,
        'holding_minutes': 45,
        'exit_price': 0.00479,
        'strategy': 'momentum',
        'regime': 'trending',
    }, signal_id='sig-001')

    # Print results
    print("--- Recent Activity ---")
    activity = await log.get_recent_activity(limit=5)
    for entry in activity:
        print(f"  [{entry['event_type']}] {entry['data']}")

    print("\n--- Session Summary ---")
    summary = await log.get_session_summary()
    print(f"  Session: {summary['session_id']}")
    print(f"  Total events: {summary['total_events']}")
    print(f"  Events by type: {summary['events_by_type']}")

    print("\n--- Accuracy Report ---")
    report = await log.get_accuracy_report(days=30)
    print(f"  Total trades: {report['total_trades']}")
    if report['total_trades'] > 0:
        print(f"  Overall win rate: {report['overall']['win_rate']:.0%}")
        print(f"  Total PnL: ${report['overall']['total_pnl']:.2f}")
        print(f"  Avg holding: {report['avg_holding_minutes']:.0f} min")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
