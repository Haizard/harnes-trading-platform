"""
🚌 Moon Dev's Event Bus — DSH-style typed event dispatch
Based on DeepSeek Harness's Cordis event system.

Four dispatch modes matching DSH's architecture:
  - emit:      Fire-and-forget notification (synchronous, no return)
  - waterfall: Cooperative middleware (priority-ordered, can reject/modify)
  - fanout:    Parallel broadcast to all listeners
  - serial:    Awaited in-order dispatch (each listener completes before next)

Every trading event flows through this bus. Modules register as listeners.
The bus replaces direct function calls between agents.

Usage:
    bus = EventBus()

    # Session Log listens to everything (emit mode)
    bus.on('order/submitted', session_log_handler, mode='emit')

    # Risk Guard intercepts orders (waterfall mode)
    bus.on('order/submit', risk_guard_handler, mode='waterfall', priority=10)

    # Benchmark Tracker records PnL (emit mode)
    bus.on('pnl/snapshot', benchmark_handler, mode='emit')

    # Dispatch an event
    await bus.emit('order/submitted', {'token': 'FART', 'side': 'buy', 'amount_usd': 25.0})

    # Waterfall: risk guard can reject
    result = await bus.waterfall('order/submit', {'token': 'FART', 'amount_usd': 25.0})
    if not result['approved']:
        print(f"Rejected: {result['reason']}")
"""

import asyncio
import uuid
import time
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Awaitable, Dict, List, Optional, Set, Tuple, Union
)
from enum import Enum
from termcolor import cprint


# ── Dispatch Modes ─────────────────────────────────────────────

class DispatchMode(str, Enum):
    """The four DSH dispatch modes."""
    EMIT = "emit"           # Fire-and-forget, synchronous
    WATERFALL = "waterfall"  # Cooperative middleware, priority-ordered
    FANOUT = "fanout"       # Parallel broadcast
    SERIAL = "serial"       # Awaited in-order


# ── Listener Registration ─────────────────────────────────────

@dataclass
class Listener:
    """A registered event listener."""
    id: str
    event_name: str
    fn: Callable
    mode: DispatchMode
    priority: int = 100
    enabled: bool = True
    tag: Optional[str] = None  # For bulk operations (e.g., disable all "risk" listeners)

    def __repr__(self):
        status = "✅" if self.enabled else "❌"
        return f"{status} [{self.mode.value}] {self.event_name} (p={self.priority}) {self.tag or ''}"


# ── Waterfall Result ──────────────────────────────────────────

@dataclass
class WaterfallResult:
    """Result of a waterfall dispatch."""
    approved: bool = True
    payload: Dict[str, Any] = field(default_factory=dict)
    reason: Optional[str] = None
    rejected_by: Optional[str] = None  # Which listener rejected
    modified: bool = False
    modifications: List[str] = field(default_factory=list)
    timing_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            'approved': self.approved,
            'payload': self.payload,
            'reason': self.reason,
            'rejected_by': self.rejected_by,
            'modified': self.modified,
            'modifications': self.modifications,
            'timing_ms': round(self.timing_ms, 2),
        }


# ── Abort Controller ──────────────────────────────────────────

class AbortController:
    """
    DSH-style cancellation signal.
    Listeners can check `controller.aborted` to stop early.
    """
    def __init__(self):
        self._aborted = False
        self._reason = ""
        self._id = str(uuid.uuid4())[:8]

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def reason(self) -> str:
        return self._reason

    def abort(self, reason: str = "aborted"):
        self._aborted = True
        self._reason = reason

    def __repr__(self):
        return f"AbortController({self._id}, aborted={self._aborted})"


# ── Event Bus ─────────────────────────────────────────────────

class EventBus:
    """
    DSH-style event bus with 4 dispatch modes.

    Architecture:
    - Listeners register on named events with a mode and priority
    - emit:      Invokes all listeners synchronously (fire-and-forget)
    - waterfall: Runs listeners in priority order; each can reject/modify/pass
    - fanout:    Runs all listeners in parallel
    - serial:    Runs listeners sequentially, awaiting each

    Integration:
    - Session Log subscribes to all events (emit mode)
    - Risk Guard intercepts order events (waterfall mode)
    - Benchmark Tracker records PnL snapshots (emit mode)
    """

    def __init__(self):
        self._listeners: Dict[str, List[Listener]] = {}
        self._history: List[dict] = []  # Recent dispatch history
        self._max_history = 500
        self._dispatch_count = 0

    # ── Registration ───────────────────────────────────────────

    def on(self, event_name: str, fn: Callable, mode: DispatchMode = DispatchMode.EMIT,
           priority: int = 100, tag: Optional[str] = None) -> Listener:
        """
        Register a listener on an event.

        Args:
            event_name: Event to listen for (e.g., 'order/submitted')
            fn: Async callback. Signature depends on mode:
                - emit:      fn(payload)
                - waterfall: fn(payload, next) -> payload or WaterfallResult
                - fanout:    fn(payload)
                - serial:    fn(payload) -> payload
            mode: Dispatch mode
            priority: Lower = runs first (waterfall/serial)
            tag: Optional tag for bulk enable/disable

        Returns:
            The Listener object (for later removal)
        """
        listener = Listener(
            id=str(uuid.uuid4())[:8],
            event_name=event_name,
            fn=fn,
            mode=mode,
            priority=priority,
            tag=tag,
        )

        if event_name not in self._listeners:
            self._listeners[event_name] = []

        self._listeners[event_name].append(listener)
        # Sort by priority for waterfall/serial modes
        self._listeners[event_name].sort(key=lambda l: l.priority)

        return listener

    def off(self, listener: Listener):
        """Remove a listener."""
        if listener.event_name in self._listeners:
            self._listeners[listener.event_name] = [
                l for l in self._listeners[listener.event_name]
                if l.id != listener.id
            ]

    def off_tag(self, tag: str):
        """Remove all listeners with a given tag."""
        for event_name in self._listeners:
            self._listeners[event_name] = [
                l for l in self._listeners[event_name]
                if l.tag != tag
            ]

    def disable(self, listener: Listener):
        """Disable a listener without removing it."""
        listener.enabled = False

    def enable(self, listener: Listener):
        """Re-enable a disabled listener."""
        listener.enabled = True

    def disable_tag(self, tag: str):
        """Disable all listeners with a given tag."""
        for listeners in self._listeners.values():
            for l in listeners:
                if l.tag == tag:
                    l.enabled = False

    def enable_tag(self, tag: str):
        """Enable all listeners with a given tag."""
        for listeners in self._listeners.values():
            for l in listeners:
                if l.tag == tag:
                    l.enabled = True

    def clear(self, event_name: Optional[str] = None):
        """Remove all listeners, or all listeners for a specific event."""
        if event_name:
            self._listeners.pop(event_name, None)
        else:
            self._listeners.clear()

    # ── Dispatch: emit ────────────────────────────────────────

    async def emit(self, event_name: str, payload: dict = None) -> int:
        """
        Fire-and-forget notification.
        Invokes all enabled listeners synchronously.
        Returns the number of listeners invoked.

        DSH pattern: notification mode — "invokes every listener and
        contains both synchronous throws and returned-promise rejections."
        """
        payload = payload or {}
        listeners = self._get_enabled(event_name, DispatchMode.EMIT)
        invoked = 0

        for listener in listeners:
            try:
                if asyncio.iscoroutinefunction(listener.fn):
                    await listener.fn(payload)
                else:
                    listener.fn(payload)
                invoked += 1
            except Exception as e:
                # Emit is fire-and-forget — log but don't crash
                self._record(event_name, 'emit', payload, error=str(e))

        self._record(event_name, 'emit', payload, invoked=invoked)
        return invoked

    # ── Dispatch: waterfall ───────────────────────────────────

    async def waterfall(self, event_name: str, payload: dict = None,
                        controller: Optional[AbortController] = None) -> WaterfallResult:
        """
        Cooperative middleware dispatch.
        Listeners run in priority order. Each must call next() to proceed.

        Listener signature: async def handler(payload, next) -> dict
          - Call next(payload) to pass to next listener
          - Return modified payload without calling next() to stop the waterfall
          - Return {'rejected': True, 'reason': '...'} to reject

        DSH pattern: "waterfall listeners must call next() to delegate"

        Returns:
            WaterfallResult with approved/rejected status
        """
        payload = payload or {}
        listeners = self._get_enabled(event_name, DispatchMode.WATERFALL)
        start = time.monotonic()

        if not listeners:
            return WaterfallResult(approved=True, payload=payload)

        # Build the chain: each listener wraps the next
        async def build_chain(index: int, current_payload: dict) -> WaterfallResult:
            if index >= len(listeners):
                return WaterfallResult(approved=True, payload=current_payload)

            listener = listeners[index]

            # Check abort
            if controller and controller.aborted:
                return WaterfallResult(
                    approved=False,
                    payload=current_payload,
                    reason=f"Aborted: {controller.reason}",
                    rejected_by="abort_controller",
                )

            # Build next() function for this listener
            async def next_fn(next_payload: dict = None) -> WaterfallResult:
                return await build_chain(index + 1, next_payload or current_payload)

            try:
                if asyncio.iscoroutinefunction(listener.fn):
                    result = await listener.fn(current_payload, next_fn)
                else:
                    result = listener.fn(current_payload, next_fn)

                # Handle result
                if result is None:
                    # Listener called next() internally
                    return await build_chain(index + 1, current_payload)

                if isinstance(result, WaterfallResult):
                    return result

                if isinstance(result, dict):
                    if result.get('rejected'):
                        return WaterfallResult(
                            approved=False,
                            payload=current_payload,
                            reason=result.get('reason', 'Rejected'),
                            rejected_by=listener.tag or listener.id,
                        )
                    if result.get('approved') is False:
                        return WaterfallResult(
                            approved=False,
                            payload=result.get('payload', current_payload),
                            reason=result.get('reason'),
                            rejected_by=listener.tag or listener.id,
                        )
                    # Modified payload returned
                    return WaterfallResult(
                        approved=True,
                        payload=result.get('payload', result),
                        modified=True,
                        modifications=[f"Modified by {listener.tag or listener.id}"],
                    )

                # Default: pass through
                return await build_chain(index + 1, current_payload)

            except Exception as e:
                # Waterfall listener error = reject (fail-closed)
                return WaterfallResult(
                    approved=False,
                    payload=current_payload,
                    reason=f"Listener error: {str(e)}",
                    rejected_by=listener.tag or listener.id,
                )

        result = await build_chain(0, payload)
        result.timing_ms = (time.monotonic() - start) * 1000

        self._record(event_name, 'waterfall', payload,
                     approved=result.approved, timing_ms=result.timing_ms)
        return result

    # ── Dispatch: fanout ──────────────────────────────────────

    async def fanout(self, event_name: str, payload: dict = None) -> List[Any]:
        """
        Parallel broadcast to all listeners.
        All listeners run concurrently. Returns all results.

        DSH pattern: parallel dispatch for independent processing.
        """
        payload = payload or {}
        listeners = self._get_enabled(event_name, DispatchMode.FANOUT)

        if not listeners:
            return []

        tasks = []
        for listener in listeners:
            if asyncio.iscoroutinefunction(listener.fn):
                tasks.append(self._safe_call(listener.fn, payload))
            else:
                tasks.append(asyncio.to_thread(listener.fn, payload))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        self._record(event_name, 'fanout', payload, invoked=len(listeners))
        return results

    # ── Dispatch: serial ──────────────────────────────────────

    async def serial(self, event_name: str, payload: dict = None) -> List[Any]:
        """
        Awaited in-order dispatch.
        Each listener runs after the previous one completes.

        DSH pattern: "Awaited in-order dispatch (Cordis serial)"
        """
        payload = payload or {}
        listeners = self._get_enabled(event_name, DispatchMode.SERIAL)
        results = []

        for listener in listeners:
            try:
                if asyncio.iscoroutinefunction(listener.fn):
                    result = await listener.fn(payload)
                else:
                    result = listener.fn(payload)
                results.append(result)
            except Exception as e:
                results.append({'error': str(e)})

        self._record(event_name, 'serial', payload, invoked=len(listeners))
        return results

    # ── Query ─────────────────────────────────────────────────

    def listeners(self, event_name: Optional[str] = None) -> List[Listener]:
        """Get all registered listeners, optionally filtered by event."""
        if event_name:
            return list(self._listeners.get(event_name, []))
        all_listeners = []
        for listeners in self._listeners.values():
            all_listeners.extend(listeners)
        return all_listeners

    def history(self, limit: int = 50) -> List[dict]:
        """Get recent dispatch history."""
        return self._history[-limit:]

    def stats(self) -> dict:
        """Get dispatch statistics."""
        by_event = {}
        by_mode = {}
        for entry in self._history:
            event = entry['event']
            mode = entry['mode']
            by_event[event] = by_event.get(event, 0) + 1
            by_mode[mode] = by_mode.get(mode, 0) + 1

        return {
            'total_dispatches': self._dispatch_count,
            'total_listeners': sum(len(v) for v in self._listeners.values()),
            'events_registered': list(self._listeners.keys()),
            'by_event': by_event,
            'by_mode': by_mode,
        }

    # ── Internal ──────────────────────────────────────────────

    def _get_enabled(self, event_name: str, mode: Optional[DispatchMode] = None) -> List[Listener]:
        """Get enabled listeners for an event, optionally filtered by mode."""
        listeners = self._listeners.get(event_name, [])
        if mode:
            return [l for l in listeners if l.enabled and l.mode == mode]
        return [l for l in listeners if l.enabled]

    def _record(self, event: str, mode: str, payload: dict, **kwargs):
        """Record a dispatch event in history."""
        self._dispatch_count += 1
        entry = {
            'event': event,
            'mode': mode,
            'payload_keys': list(payload.keys()) if payload else [],
            'timestamp': time.time(),
            **kwargs,
        }
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    async def _safe_call(self, fn, payload):
        """Call a function safely, catching exceptions."""
        try:
            return await fn(payload)
        except Exception as e:
            return {'error': str(e)}


# ── Pre-built Event Names ─────────────────────────────────────

class Events:
    """Typed event names following DSH's SessionEventMap pattern."""
    # Signal lifecycle
    SIGNAL_GENERATED = "signal/generated"
    SIGNAL_VALIDATED = "signal/validated"
    SIGNAL_LLM_REASON = "signal/llm_reason"

    # Order lifecycle
    ORDER_INTENT = "order/intent"
    ORDER_SUBMIT = "order/submit"          # Waterfall: risk guard intercepts
    ORDER_SUBMITTED = "order/submitted"     # Emit: logged after execution
    ORDER_FILLED = "order/filled"
    ORDER_PARTIAL = "order/partial"
    ORDER_FAILED = "order/failed"

    # Position lifecycle
    POSITION_OPENED = "position/opened"
    POSITION_CLOSED = "position/closed"
    POSITION_UPDATED = "position/updated"

    # Portfolio
    PNL_SNAPSHOT = "pnl/snapshot"
    REGIME_DETECTED = "regime/detected"
    PREDICTION_SCORE = "prediction/score"

    # Risk
    RISK_APPROVED = "risk/approved"
    RISK_DENIED = "risk/denied"

    # Wallet Intelligence
    WALLET_SWAP_DETECTED = "wallet/swap_detected"       # A tracked wallet made a swap
    WALLET_SCORED = "wallet/scored"                     # A wallet was scored
    SMART_MONEY_CONSENSUS = "wallet/smart_money"        # Multiple wallets buying same token
    SMART_MONEY_ALERT = "wallet/smart_money_alert"      # High-confidence consensus signal

    # System
    AGENT_ERROR = "agent/error"
    MODEL_CALL = "model/call"
    SESSION_LOG = "session/log"             # Emit: universal logging


# ── Integration: Connect Existing Modules ─────────────────────

def create_integrated_bus(session_log=None, risk_guard=None,
                          benchmark_tracker=None) -> EventBus:
    """
    Create an EventBus pre-wired to existing modules.

    This is the DSH "capability seam" — modules don't import each other.
    They register on the bus and the bus connects them.
    """
    bus = EventBus()

    # ── Session Log: listens to everything (emit mode) ────────
    if session_log:
        async def log_handler(payload):
            event_type = payload.get('event_type', 'unknown')
            await session_log.log(event_type, payload)

        # Log all major events
        for event_name in [
            Events.SIGNAL_GENERATED, Events.SIGNAL_VALIDATED,
            Events.ORDER_SUBMIT, Events.ORDER_SUBMITTED, Events.ORDER_FILLED,
            Events.ORDER_FAILED, Events.POSITION_OPENED, Events.POSITION_CLOSED,
            Events.RISK_APPROVED, Events.RISK_DENIED,
            Events.PNL_SNAPSHOT, Events.REGIME_DETECTED,
            Events.AGENT_ERROR, Events.MODEL_CALL,
        ]:
            bus.on(event_name, log_handler, mode=DispatchMode.EMIT, tag="session_log")

    # ── Risk Guard: intercepts order submissions (waterfall) ──
    if risk_guard:
        async def risk_guard_handler(payload, next_fn):
            from src.risk_guard import Order, OrderSide, OrderType

            order = Order(
                token=payload.get('token', ''),
                side=OrderSide(payload.get('side', 'buy')),
                order_type=OrderType(payload.get('order_type', 'market')),
                amount_usd=payload.get('amount_usd', 0),
                slippage=payload.get('slippage', 0.05),
                source=payload.get('source', 'event_bus'),
            )

            result = await risk_guard.validate_order(order)

            if not result.approved:
                return {
                    'rejected': True,
                    'reason': result.reason,
                    'guard': result.stage,
                }

            # Pass modified order forward
            if result.modified_order:
                payload['amount_usd'] = result.modified_order.amount_usd
                payload['modifications'] = result.adjustments

            return await next_fn(payload)

        bus.on(Events.ORDER_SUBMIT, risk_guard_handler,
               mode=DispatchMode.WATERFALL, priority=10, tag="risk_guard")

    # ── Benchmark Tracker: records PnL snapshots (emit) ───────
    if benchmark_tracker:
        async def benchmark_handler(payload):
            # Record PnL snapshot for benchmark comparison
            if hasattr(benchmark_tracker, '_record_snapshot'):
                await benchmark_tracker._record_snapshot(
                    portfolio_value=payload.get('portfolio_value', 0),
                    token=payload.get('token', ''),
                )

        bus.on(Events.PNL_SNAPSHOT, benchmark_handler,
               mode=DispatchMode.EMIT, tag="benchmark")

    return bus


# ── CLI Demo ──────────────────────────────────────────────────

async def main():
    """Demo the event bus."""
    bus = EventBus()

    print("\n🚌 Moon Dev Event Bus — Demo\n")

    # Register listeners
    call_log = []

    async def logger(payload):
        call_log.append(('emit', payload))
        print(f"  📋 Logger: {payload}")

    async def risk_check(payload, next_fn):
        if payload.get('amount_usd', 0) > 100:
            return {'rejected': True, 'reason': 'Amount too large'}
        print(f"  🛡️ Risk: approved ${payload.get('amount_usd', 0):.2f}")
        return await next_fn(payload)

    async def executor(payload):
        call_log.append(('exec', payload))
        print(f"  🚀 Executor: {payload}")

    bus.on('order/submitted', logger, mode=DispatchMode.EMIT, tag="log")
    bus.on('order/submit', risk_check, mode=DispatchMode.WATERFALL, priority=10, tag="risk")
    bus.on('order/submit', executor, mode=DispatchMode.WATERFALL, priority=100, tag="exec")

    # Test 1: Valid order through waterfall
    print("--- Test 1: Valid $25 order ---")
    result = await bus.waterfall('order/submit', {
        'token': 'FARTCOIN', 'side': 'buy', 'amount_usd': 25.0,
    })
    print(f"  Result: {'✅' if result.approved else '❌'} ({result.timing_ms:.1f}ms)\n")

    # Test 2: Rejected order
    print("--- Test 2: $200 order (too large) ---")
    result = await bus.waterfall('order/submit', {
        'token': 'FARTCOIN', 'side': 'buy', 'amount_usd': 200.0,
    })
    print(f"  Result: {'✅' if result.approved else '❌'} - {result.reason}\n")

    # Test 3: Emit
    print("--- Test 3: Emit event ---")
    count = await bus.emit('order/submitted', {
        'token': 'FARTCOIN', 'side': 'buy', 'amount_usd': 25.0,
    })
    print(f"  Invoked {count} listeners\n")

    # Stats
    print("--- Stats ---")
    stats = bus.stats()
    print(f"  Total dispatches: {stats['total_dispatches']}")
    print(f"  Total listeners: {stats['total_listeners']}")
    print(f"  By mode: {stats['by_mode']}")


if __name__ == "__main__":
    asyncio.run(main())
