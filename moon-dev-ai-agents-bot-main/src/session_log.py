"""
📋 Moon Dev's Session Log & Audit Trail
DSH Pattern: Append-only SessionEventMap — every event is durable and queryable.

Every decision, model call, and trade is permanently recorded.
No more print() statements — everything goes through the log.

Usage:
    log = SessionLog(storage=pg_storage)
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

    # Wallet Intelligence
    WALLET_SWAP_DETECTED = "wallet/swap_detected"
    WALLET_SCORED = "wallet/scored"
    SMART_MONEY_CONSENSUS = "wallet/smart_money"
    SMART_MONEY_ALERT = "wallet/smart_money_alert"

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
    EventType.WALLET_SWAP_DETECTED: "Tracked wallet executed a swap",
    EventType.WALLET_SCORED: "Wallet scored by WalletScorer",
    EventType.SMART_MONEY_CONSENSUS: "Multiple wallets buying same token",
    EventType.SMART_MONEY_ALERT: "High-confidence smart money consensus",
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

class PGStorage:
    """PostgreSQL storage backend — durable, queryable, production-ready."""

    def __init__(self):
        self._pool = None

    def _get_pool(self):
        if self._pool is not None:
            return self._pool
        try:
            from src.db_storage import get_pool
            self._pool = get_pool()
            if self._pool:
                self._init_session_table()
            return self._pool
        except Exception:
            return None

    def _init_session_table(self):
        pool = self._get_pool()
        if not pool:
            return
        try:
            with pool.connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS session_events (
                        id TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        description TEXT,
                        data JSONB,
                        timestamp TIMESTAMPTZ NOT NULL,
                        session_id TEXT,
                        signal_id TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_session_events_type ON session_events(event_type)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_session_events_session ON session_events(session_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_session_events_signal ON session_events(signal_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_session_events_time ON session_events(timestamp)")
                conn.commit()
        except Exception as e:
            print(f"[DB] session_events table init error: {e}")

    async def append(self, event: LogEvent):
        pool = self._get_pool()
        if not pool:
            return
        try:
            with pool.connection() as conn:
                conn.execute("""
                    INSERT INTO session_events (id, event_type, description, data, timestamp, session_id, signal_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (
                    event.id,
                    event.event_type,
                    EVENT_DESCRIPTIONS.get(event.event_type, 'Unknown'),
                    json.dumps(event.data, default=str),
                    event.timestamp,
                    event.session_id,
                    event.signal_id or '',
                ))
                conn.commit()
        except Exception as e:
            print(f"[DB] session_events append error: {e}")

    async def query(self, filters: dict = None, limit: int = 100) -> List[LogEvent]:
        pool = self._get_pool()
        if not pool:
            return []
        try:
            with pool.connection() as conn:
                conditions = ["1=1"]
                params = []

                if filters:
                    if 'event_type' in filters:
                        conditions.append("event_type = %s")
                        params.append(filters['event_type'])
                    if 'session_id' in filters:
                        conditions.append("session_id = %s")
                        params.append(filters['session_id'])
                    if 'signal_id' in filters:
                        conditions.append("signal_id = %s")
                        params.append(filters['signal_id'])
                    if 'since' in filters:
                        conditions.append("timestamp >= %s")
                        params.append(filters['since'])

                where = " AND ".join(conditions)
                query = f"SELECT * FROM session_events WHERE {where} ORDER BY timestamp DESC LIMIT %s"
                params.append(limit)

                rows = conn.execute(query, params).fetchall()
                events = []
                for row in rows:
                    data = row['data']
                    if isinstance(data, str):
                        data = json.loads(data)
                    events.append(LogEvent(
                        id=row['id'],
                        event_type=row['event_type'],
                        data=data,
                        timestamp=str(row['timestamp']),
                        session_id=row.get('session_id', ''),
                        signal_id=row.get('signal_id', '') or None,
                    ))
                return events
        except Exception as e:
            print(f"[DB] session_events query error: {e}")
            return []


class CSVStorage:
    """Simple CSV file storage — works without database."""

    def __init__(self, log_dir: str = None):
        self.log_dir = log_dir or os.path.join(os.path.dirname(__file__), 'data')
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_path = os.path.join(self.log_dir, 'session_log.csv')

    async def append(self, event: LogEvent):
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

        events.sort(key=lambda e: e.timestamp, reverse=True)
        return events[:limit]

    def _matches_filters(self, event: LogEvent, filters: dict) -> bool:
        if not filters:
            return True
        if 'event_type' in filters and event.event_type != filters['event_type']:
            return False
        if 'token' in filters and filters['token'] not in json.dumps(event.data):
            return False
        if 'session_id' in filters and event.session_id != filters['session_id']:
            return False
        if 'since' in filters and event.timestamp < filters['since']:
            return False
        if 'signal_id' in filters and event.signal_id != filters['signal_id']:
            return False
        return True


class InMemoryStorage:
    """In-memory storage for testing — no disk I/O."""

    def __init__(self):
        self.events: List[LogEvent] = []
        self._seq = 0

    async def append(self, event: LogEvent):
        self._seq += 1
        event._seq = self._seq
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
    Defaults to PostgreSQL when available, falls back to CSV.
    """

    def __init__(self, storage=None, session_id: str = None):
        if storage is None:
            # Auto-select: PostgreSQL > CSV > InMemory
            pg = PGStorage()
            if pg._get_pool():
                self.storage = pg
            else:
                self.storage = CSVStorage()
        else:
            self.storage = storage
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self._validators: Dict[str, Callable] = {}

    def register_validator(self, event_type: str, fn: Callable):
        self._validators[event_type] = fn

    async def log(self, event_type: str, data: dict, signal_id: str = None) -> LogEvent:
        valid_types = [e.value for e in EventType]
        if event_type not in valid_types:
            raise ValueError(
                f"Unknown event type: {event_type}. "
                f"Valid types: {valid_types}"
            )

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
        filters = {}
        if event_type:
            filters['event_type'] = event_type
        if token:
            filters['token'] = token
        if since:
            filters['since'] = since
        return await self.storage.query(filters=filters, limit=limit)

    async def get_trade_chain(self, token: str, days: int = 30) -> List[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        events = await self.storage.query(
            filters={'token': token, 'since': cutoff},
            limit=1000
        )

        events.sort(key=lambda e: getattr(e, '_seq', 0))

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

        all_pnl = [t['pnl_usd'] for t in trades]
        overall = calc_stats(all_pnl)

        by_strategy = {}
        for t in trades:
            strat = t['strategy']
            by_strategy.setdefault(strat, []).append(t['pnl_usd'])
        by_strategy = {k: calc_stats(v) for k, v in by_strategy.items()}

        by_regime = {}
        for t in trades:
            regime = t['regime']
            by_regime.setdefault(regime, []).append(t['pnl_usd'])
        by_regime = {k: calc_stats(v) for k, v in by_regime.items()}

        by_token = {}
        for t in trades:
            token = t['token'][:12] + '...' if len(t['token']) > 12 else t['token']
            by_token.setdefault(token, []).append(t['pnl_usd'])
        by_token = {k: calc_stats(v) for k, v in by_token.items()}

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
            'trades': trades[-10:],
        }

    async def get_recent_activity(self, limit: int = 20) -> List[dict]:
        events = await self.storage.query(limit=limit)
        return [e.to_dict() for e in events]

    async def get_session_summary(self) -> dict:
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
    """Create a SessionLog — prefers PostgreSQL, falls back to CSV."""
    return SessionLog(session_id=session_id)


def create_test_session_log(session_id: str = None) -> SessionLog:
    """Create a SessionLog with in-memory storage for testing."""
    storage = InMemoryStorage()
    return SessionLog(storage=storage, session_id=session_id)
