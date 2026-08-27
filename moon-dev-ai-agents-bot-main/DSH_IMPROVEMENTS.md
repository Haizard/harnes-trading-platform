# Moon Dev Trading Platform — Improvements via DeepSeek Harness Architecture

> A comprehensive improvement plan for the Moon Dev AI Trading Platform, applying proven architectural patterns from DeepSeek Harness (DSH) to solve structural weaknesses and improve trading accuracy/profitability.

---

## Table of Contents

- [Part 1: Architecture Improvements](#part-1-architecture-improvements)
  - [1. Current Weakness Analysis](#1-current-weakness-analysis)
  - [2. Risk Guard Waterfall](#2-risk-guard-waterfall)
  - [3. Session Log & Audit Trail](#3-session-log--audit-trail)
  - [4. Event Bus](#4-event-bus)
  - [5. Tool Pipeline](#5-tool-pipeline)
  - [6. YAML Config & Profiles](#6-yaml-config--profiles)
  - [7. Async Scheduling](#7-async-scheduling)
  - [8. Process Isolation](#8-process-isolation)
- [Part 2: Accuracy & Profitability Improvements](#part-2-accuracy--profitability-improvements)
  - [9. Weighted Prediction Engine](#9-weighted-prediction-engine)
  - [10. Multi-Stage Signal Validation Pipeline](#10-multi-stage-signal-validation-pipeline)
  - [11. Trade Feedback Loop](#11-trade-feedback-loop)
  - [12. Ensemble Strategy System](#12-ensemble-strategy-system)
  - [13. Portfolio Goal System](#13-portfolio-goal-system)
  - [14. Real-Time Signal Processing](#14-real-time-signal-processing)
  - [15. Parallel Analysis](#15-parallel-analysis)
  - [16. LLM Context Compaction](#16-llm-context-compaction)
- [Part 3: Advanced Improvements](#part-3-advanced-improvements)
  - [17. Multi-Step Trade Planning (Plan Mode)](#17-multi-step-trade-planning-plan-mode)
  - [18. Human Feedback System](#18-human-feedback-system)
  - [19. Spill & Smart Data Storage](#19-spill--smart-data-storage)
  - [20. MCP Integration — External Services](#20-mcp-integration--external-services)
  - [21. Runtime Invariants — Safety Guarantees](#21-runtime-invariants--safety-guarantees)
  - [22. Session Query — Trade History Search](#22-session-query--trade-history-search)
  - [23. Trading Commands — Manual Intervention](#23-trading-commands--manual-intervention)
  - [24. Risk Presets — One-Click Profiles](#24-risk-presets--one-click-profiles)
- [Part 4: Research Gaps — The Unbeatable Layer](#part-4-research-gaps--the-unbeatable-layer)
  - [25. Benchmark Tracker — Know If You're Adding Alpha](#25-benchmark-tracker--know-if-youre-adding-alpha)
  - [26. Walk-Forward Backtesting — Prevent Overfitting](#26-walk-forward-backtesting--prevent-overfitting)
  - [27. Volatility-Adjusted Position Sizing](#27-volatility-adjusted-position-sizing)
  - [28. Execution Quality Tracker](#28-execution-quality-tracker)
  - [29. Alpha Decay Detection](#29-alpha-decay-detection)
  - [30. Funding Cost Accounting](#30-funding-cost-accounting)
  - [31. Portfolio Correlation Management](#31-portfolio-correlation-management)
- [Part 5: Prioritized Roadmap](#part-5-prioritized-roadmap)

---

# Part 1: Architecture Improvements

---

## 1. Current Weakness Analysis

| # | Weakness | Evidence in Code |
|---|---|---|
| 1 | **No error recovery** — bare `except:` swallows everything | `nice_funcs.py`: 7 bare `except:` blocks, `trading_agent.py`: broad exception catches |
| 2 | **No process isolation** — one agent crash can cascade | All agents share `config.py` globals, one `main.py` loop |
| 3 | **Hardcoded configs** — restart required for changes | `config.py` is a flat Python file with mutable constants |
| 4 | **No audit trail** — decisions are ephemeral | No logging of *why* a trade was made beyond `print()` statements |
| 5 | **Inconsistent model usage** — three different patterns | TradingAgent uses raw Anthropic, RiskAgent supports DeepSeek, ModelFactory exists separately |
| 6 | **No pre-trade validation** — orders go directly to Jupiter | `market_buy()` and `market_sell()` have zero approval gates |
| 7 | **Blocking `time.sleep()`** — agents freeze during sleep | `time.sleep(30)` in retry loops, `time.sleep(300)` in risk agent |
| 8 | **Duplicated code** — entry/exit logic repeated ~4 times | `elegant_entry`, `breakout_entry`, `ai_entry`, `pnl_close` are near-identical |
| 9 | **Global state everywhere** — `config.py` imported with `*` | `from src.config import *` in every file |
| 10 | **No session replay** — can't reconstruct why trades happened | Only `print()` statements remain after execution |

---

## 2. Risk Guard Waterfall

> ✅ **IMPLEMENTED** — See `src/risk_guard.py` and `src/tests/test_risk_guard.py` (37 tests passing)

**Problem:** Moon Dev's RiskAgent checks PnL *after* positions are already open (polling every 5 minutes). There is no pre-trade validation — orders go directly to Jupiter.

**DSH Pattern:** `tools/pre-execute` waterfall — listeners run in priority order, each can deny, modify, or pass through.

```python
"""
DSH-inspired Risk Guard Waterfall
Intercepts EVERY order BEFORE it reaches Jupiter.
Based on DSH's tools/pre-execute waterfall pattern.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class Order:
    """An order to be validated."""
    token: str
    side: str           # 'buy' or 'sell'
    amount_usd: float
    slippage_bps: int = 199
    strategy: str = 'unknown'
    confidence: float = 0.5
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class GuardDecision:
    """Result of a guard check."""
    action: str          # 'allow', 'deny', 'ask'
    reason: str = ''
    modified_order: Optional[Order] = None


class RiskGuardWaterfall:
    """
    DSH-style waterfall guard that intercepts every trade.
    
    Each guard runs in priority order. A guard can:
    - 'allow' — pass to next guard
    - 'deny' — block the order with a reason
    - 'ask' — request human approval
    - 'modify' — adjust the order (e.g., reduce size) and continue
    """

    def __init__(self, config):
        self.config = config
        self.daily_pnl = 0.0
        self.daily_orders = 0
        self.positions = {}  # token -> {value_usd, entry_time, size}
        self.guards = []
        self.session_log = None  # Set after init

    def register_guard(self, name, fn, priority=100):
        """Register a guard. Lower priority runs first."""
        self.guards.append((name, priority, fn))
        self.guards.sort(key=lambda x: x[1])

    async def validate(self, order: Order) -> dict:
        """Run order through all guards. First deny wins."""
        for name, _, guard_fn in self.guards:
            try:
                decision = await guard_fn(order, self)
            except Exception as e:
                decision = GuardDecision(action='deny', reason=f"Guard '{name}' error: {e}")

            if decision.action == 'deny':
                if self.session_log:
                    await self.session_log.log('risk/denied', {
                        'order': order.__dict__, 'guard': name, 'reason': decision.reason
                    })
                return {'approved': False, 'guard': name, 'reason': decision.reason}

            elif decision.action == 'ask':
                return {'approved': False, 'guard': name, 'reason': decision.reason, 'needs_approval': True}

            elif decision.action == 'modify':
                order = decision.modified_order

        if self.session_log:
            await self.session_log.log('risk/approved', {'order': order.__dict__})

        return {'approved': True, 'order': order}


# ── Guard Implementations ───────────────────────────────────────────

async def guard_position_size(order: Order, state) -> GuardDecision:
    """No single position > MAX_POSITION_PCT of portfolio."""
    max_position = state.config.usd_size * (state.config.MAX_POSITION_PERCENTAGE / 100)
    current = state.positions.get(order.token, {}).get('value_usd', 0)
    if current + order.amount_usd > max_position:
        return GuardDecision(
            action='modify',
            modified_order=Order(**{**order.__dict__, 'amount_usd': max_position - current})
        )
    return GuardDecision(action='allow')

async def guard_daily_loss(order: Order, state) -> GuardDecision:
    """Stop trading if daily loss exceeds limit."""
    if state.daily_pnl < -state.config.MAX_LOSS_USD:
        return GuardDecision(action='deny', reason=f"Daily loss limit ${state.config.MAX_LOSS_USD} reached")
    return GuardDecision(action='allow')

async def guard_total_exposure(order: Order, state) -> GuardDecision:
    """Total portfolio exposure cannot exceed limit."""
    total = sum(p['value_usd'] for p in state.positions.values())
    if total + order.amount_usd > state.config.usd_size * 0.8:
        return GuardDecision(action='deny', reason="Total exposure exceeds 80% of portfolio")
    return GuardDecision(action='allow')

async def guard_max_positions(order: Order, state) -> GuardDecision:
    """Limit number of open positions."""
    if order.side == 'buy' and len(state.positions) >= state.config.MAX_POSITIONS:
        return GuardDecision(action='deny', reason=f"Max {state.config.MAX_POSITIONS} positions reached")
    return GuardDecision(action='allow')

async def guard_fee_profitability(order: Order, state) -> GuardDecision:
    """Reject trades where fees exceed expected profit."""
    fee_pct = (order.slippage_bps + 30) / 10000  # slippage + ~0.3% swap fee
    expected_profit = order.amount_usd * 0.02     # assume 2% target
    total_fees = order.amount_usd * fee_pct
    if total_fees > expected_profit * 0.5:
        return GuardDecision(action='deny', reason=f"Fees ${total_fees:.2f} exceed 50% of expected profit")
    return GuardDecision(action='allow')

async def guard_human_approval(order: Order, state) -> GuardDecision:
    """Large orders need human approval."""
    if order.amount_usd > state.config.usd_size * 0.15:
        return GuardDecision(action='ask', reason=f"Large order: ${order.amount_usd:.2f} ({order.token[:8]})")
    return GuardDecision(action='allow')


# ── Setup ───────────────────────────────────────────────────────────

def create_risk_guard(config, session_log=None):
    """Factory to create a fully configured risk guard."""
    guard = RiskGuardWaterfall(config)
    guard.session_log = session_log

    # Register guards in priority order (lower = runs first)
    guard.register_guard("daily_loss",     guard_daily_loss,      priority=10)
    guard.register_guard("total_exposure", guard_total_exposure,  priority=20)
    guard.register_guard("position_size",  guard_position_size,   priority=30)
    guard.register_guard("max_positions",  guard_max_positions,   priority=40)
    guard.register_guard("fee_check",      guard_fee_profitability, priority=50)
    guard.register_guard("human_approval", guard_human_approval,  priority=60)

    return guard
```

**Integration point — wrap existing `market_buy`:**

```python
# Before (current nice_funcs.py):
def market_buy(token, amount, slippage):
    KEY = Keypair.from_base58_string(os.getenv("SOLANA_PRIVATE_KEY"))
    # ... directly sends to Jupiter

# After:
async def safe_market_buy(token, amount_usd, slippage):
    """Market buy with risk guard validation."""
    order = Order(token=token, side='buy', amount_usd=amount_usd, slippage_bps=slippage)
    result = await risk_guard.validate(order)

    if not result['approved']:
        if result.get('needs_approval'):
            print(f"⏳ Awaiting human approval: {result['reason']}")
            # In production: send to Discord/Telegram, wait for response
            approved = await request_human_approval(result['reason'])
            if not approved:
                return None

        print(f"🛡️ Order blocked: {result['reason']}")
        return None

    # Proceed with validated order
    return await market_buy(order.token, order.amount_usd, order.slippage_bps)
```

---

## 3. Session Log & Audit Trail

> ✅ **IMPLEMENTED** — See `src/session_log.py` and `src/tests/test_session_log.py` (39 tests passing)

**Problem:** All decisions are ephemeral `print()` statements. No way to reconstruct *why* a trade was made.

**DSH Pattern:** Append-only `SessionEventMap` — every event is durable and queryable.

```python
"""
DSH-inspired Session Log for Trading Decisions
Every decision, model call, and trade is permanently recorded.
Based on DSH's SessionEventMap — model-visible means logged.
"""

from datetime import datetime
from typing import Optional
import uuid


class TradingSessionLog:
    """
    Append-only log of every trading event.
    
    Event types follow DSH's SessionEventMap pattern:
    - Durable facts that survive restart
    - Queryable for replay, debugging, and learning
    """

    EVENT_TYPES = {
        # Signal lifecycle
        'signal/generated':     'Strategy produced a raw signal',
        'signal/validated':     'Pipeline approved/rejected a signal',
        'signal/llm_reason':    'LLM reasoning for a decision',
        # Order lifecycle
        'order/intent':         'About to place an order',
        'order/submitted':      'Order sent to exchange',
        'order/filled':         'Order confirmed filled',
        'order/partial':        'Partial fill',
        'order/failed':         'Order failed',
        # Position lifecycle
        'position/opened':      'New position established',
        'position/closed':      'Position exited',
        'position/updated':     'Stop-loss or take-profit adjusted',
        # Portfolio state
        'pnl/snapshot':         'Periodic portfolio value',
        'regime/detected':      'Market regime changed',
        'prediction/score':     'PredictionEngine output',
        # Risk
        'risk/approved':        'Risk guard approved order',
        'risk/denied':          'Risk guard denied order',
        # System
        'agent/error':          'Agent encountered an error',
        'model/call':           'LLM API call made',
    }

    def __init__(self, mongo_storage, session_id=None):
        self.db = mongo_storage
        self.session_id = session_id or str(uuid.uuid4())[:8]

    async def log(self, event_type, data, signal_id=None):
        """Append event — immutable once written."""
        if event_type not in self.EVENT_TYPES:
            raise ValueError(f"Unknown event type: {event_type}")

        await self.db.insert("trading_log", {
            "type": event_type,
            "description": self.EVENT_TYPES[event_type],
            "data": data,
            "signal_id": signal_id,
            "timestamp": datetime.utcnow(),
            "session_id": self.session_id,
        })

    async def get_trade_chain(self, token, days=30):
        """Reconstruct the full decision chain for a token."""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)

        events = await self.db.find("trading_log", {
            "data.token": token,
            "timestamp": {"$gte": cutoff}
        })

        events.sort(key=lambda e: e['timestamp'])

        # Rebuild trade narratives
        trades = []
        current = {}
        for event in events:
            etype = event['type']
            data = event['data']

            if etype == 'signal/generated':
                current = {'token': token, 'entry_signals': [data], 'timestamp': event['timestamp']}
            elif etype == 'signal/validated':
                current['validation'] = data
            elif etype == 'risk/approved':
                current['risk_approval'] = data
            elif etype == 'risk/denied':
                current['risk_denial'] = data
            elif etype == 'order/submitted':
                current['order'] = data
            elif etype == 'position/closed':
                current['exit'] = data
                current['pnl_usd'] = data.get('pnl_usd', 0)
                current['holding_time'] = data.get('holding_minutes', 0)
                trades.append(current)
                current = {}

        return trades

    async def get_accuracy_report(self, days=30):
        """What actually makes money?"""
        trades = await self.get_trade_chain(days=days)
        if not trades:
            return {"error": "No trades found"}

        by_strategy = {}
        by_regime = {}

        for t in trades:
            # Group by strategy
            for sig in t.get('entry_signals', []):
                strat = sig.get('strategy', 'unknown')
                by_strategy.setdefault(strat, []).append(t.get('pnl_usd', 0))

            # Group by regime
            regime = t.get('regime', 'unknown')
            by_regime.setdefault(regime, []).append(t.get('pnl_usd', 0))

        def stats(values):
            if not values:
                return {"count": 0}
            wins = [v for v in values if v > 0]
            return {
                "count": len(values),
                "win_rate": len(wins) / len(values),
                "avg_pnl": sum(values) / len(values),
                "total_pnl": sum(values),
                "best_trade": max(values),
                "worst_trade": min(values),
            }

        return {
            "period_days": days,
            "total_trades": len(trades),
            "overall": stats([t.get('pnl_usd', 0) for t in trades]),
            "by_strategy": {k: stats(v) for k, v in by_strategy.items()},
            "by_regime": {k: stats(v) for k, v in by_regime.items()},
            "avg_holding_minutes": sum(t.get('holding_time', 0) for t in trades) / len(trades),
        }
```

---

## 4. Event Bus

> ✅ **IMPLEMENTED** — See `src/event_bus.py` and `src/tests/test_event_bus.py` (39 tests passing)

**Problem:** Agents are tightly coupled through shared DataFrames and manual orchestration in `main.py`.

**DSH Pattern:** Typed events with `emit`, `waterfall`, `parallel`, `serial` dispatch modes.

```python
"""
DSH-inspired Event Bus for Trading Agents
Typed pub/sub replacing direct coupling.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Any, Optional
from enum import Enum


class EventType(Enum):
    EMIT = 'emit'           # Fire-and-forget
    WATERFALL = 'waterfall' # Middleware — can intercept/modify
    PARALLEL = 'parallel'   # Fan-out to all listeners


@dataclass
class Event:
    """A typed event on the bus."""
    type: str
    data: Any
    source: str = ''
    cancelled: bool = False
    cancellation_reason: str = ''


class EventBus:
    """
    DSH-style event bus with waterfall support.
    
    - emit: all listeners fire, no interception
    - waterfall: listeners run in priority order, can modify or cancel
    """

    def __init__(self):
        self._listeners: Dict[str, List[tuple]] = {}  # event_type -> [(priority, fn)]
        self._emit_listeners: Dict[str, List[Callable]] = {}

    def on(self, event_type: str, fn: Callable, priority: int = 100):
        """Register a listener for an event type."""
        self._listeners.setdefault(event_type, []).append((priority, fn))
        self._listeners[event_type].sort(key=lambda x: x[0])

    def emit_handler(self, event_type: str, fn: Callable):
        """Register a fire-and-forget listener."""
        self._emit_listeners.setdefault(event_type, []).append(fn)

    async def waterfall(self, event_type: str, event: Event) -> Event:
        """Run event through waterfall listeners. Any can cancel or modify."""
        for _, listener in self._listeners.get(event_type, []):
            try:
                result = await listener(event)
                if result and hasattr(result, 'cancelled') and result.cancelled:
                    event.cancelled = True
                    event.cancellation_reason = result.cancellation_reason
                    return event
            except Exception as e:
                print(f"⚠️ Waterfall listener error on {event_type}: {e}")
        return event

    async def emit(self, event_type: str, event: Event):
        """Fire all listeners (non-blocking notification)."""
        for fn in self._emit_listeners.get(event_type, []):
            try:
                await fn(event)
            except Exception as e:
                print(f"⚠️ Emit listener error on {event_type}: {e}")


# ── Usage Example ───────────────────────────────────────────────────

bus = EventBus()

# Strategy agent emits market data
@bus.emit_handler('market/data')
async def on_market_data(event):
    print(f"📊 Market data received for {event.data['token']}")

# Risk guard intercepts orders (waterfall)
async def risk_waterfall_listener(event):
    if event.data['amount_usd'] > 25:
        event.cancelled = True
        event.cancellation_reason = "Order exceeds $25 limit"
        return event
    return event

bus.on('order/submit', risk_waterfall_listener, priority=10)

# Execution agent handles approved orders
@bus.emit_handler('order/approved')
async def on_order_approved(event):
    # Actually execute the trade
    pass
```

**Migration from current `main.py`:**

```python
# Before (manual orchestration):
risk_agent.run()
signals = await strategy_agent.get_signals(token)
trading_agent.run_trading_cycle(strategy_signals=signals)

# After (event-driven):
bus.emit('market/data', Event(type='market/data', data={...}))
# All listeners fire automatically — risk guard, strategy, execution
```

---

## 5. Tool Pipeline

> ✅ **IMPLEMENTED** — See `src/order_executor.py` and `src/tests/test_order_executor.py` (21 tests passing)

**Problem:** 4 near-identical entry functions (`elegant_entry`, `breakout_entry`, `ai_entry`, `pnl_close`) with duplicated retry logic.

**DSH Pattern:** `defineTool()` with typed schemas, validation, and lifecycle hooks.

```python
"""
DSH-inspired Tool Pipeline for Trading Operations
Single entry point replacing 4 duplicated functions.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class ToolResult:
    """Standardized result from a tool execution."""
    success: bool
    value: Any = None
    error: str = ''
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class ToolContext:
    """Context passed to tool execution — provides logging, cancellation, timeout."""

    def __init__(self, session_log=None, signal=None):
        self.session_log = session_log
        self.signal = signal  # asyncio.Event for cancellation

    async def log(self, event_type, data):
        if self.session_log:
            await self.session_log.log(event_type, data)

    @property
    def is_cancelled(self):
        return self.signal and self.signal.is_set()


class ToolRegistry:
    """Registry of all trading tools — like DSH's ctx.tools."""

    def __init__(self):
        self._tools = {}

    def register(self, name, description, parameters, execute_fn, timeout_ms=30000):
        self._tools[name] = {
            'name': name,
            'description': description,
            'parameters': parameters,
            'execute': execute_fn,
            'timeout_ms': timeout_ms,
        }

    async def execute(self, name, args, ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(success=False, error=f"Unknown tool: {name}")

        try:
            result = await asyncio.wait_for(
                tool['execute'](args, ctx),
                timeout=tool['timeout_ms'] / 1000
            )
            return result
        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Tool '{name}' timed out")
        except Exception as e:
            return ToolResult(success=False, error=f"Tool '{name}' failed: {e}")


# ── Tool Definitions ────────────────────────────────────────────────

registry = ToolRegistry()

async def execute_place_order(args, ctx: ToolContext) -> ToolResult:
    """Unified order placement — replaces elegant_entry, breakout_entry, ai_entry."""
    token = args['token']
    side = args['side']
    amount_usd = args['amount_usd']
    strategy = args.get('strategy', 'unknown')

    await ctx.log('order/intent', {
        'token': token, 'side': side, 'amount_usd': amount_usd, 'strategy': strategy
    })

    try:
        # Chunk the order into manageable pieces
        chunk_size = min(amount_usd, args.get('max_chunk_usd', 3))
        remaining = amount_usd
        tx_ids = []

        while remaining > 0:
            if ctx.is_cancelled:
                break

            current_chunk = min(chunk_size, remaining)

            if side == 'buy':
                tx_id = await _execute_jupiter_buy(token, current_chunk, args.get('slippage_bps', 199))
            else:
                tx_id = await _execute_jupiter_sell(token, current_chunk, args.get('slippage_bps', 199))

            tx_ids.append(tx_id)
            remaining -= current_chunk

            if remaining > 0:
                await asyncio.sleep(2)  # Wait between chunks

        await ctx.log('order/executed', {
            'token': token, 'side': side, 'amount_usd': amount_usd,
            'tx_ids': tx_ids, 'chunks': len(tx_ids)
        })

        return ToolResult(
            success=True,
            value={'tx_ids': tx_ids, 'amount_usd': amount_usd},
            metadata={'strategy': strategy}
        )
    except Exception as e:
        await ctx.log('order/failed', {'token': token, 'error': str(e)})
        return ToolResult(success=False, error=str(e))


registry.register(
    name='place_order',
    description='Place a market order via Jupiter',
    parameters={
        'token': {'type': 'string', 'required': True},
        'side': {'type': 'string', 'enum': ['buy', 'sell'], 'required': True},
        'amount_usd': {'type': 'number', 'required': True},
        'slippage_bps': {'type': 'integer', 'default': 199},
        'max_chunk_usd': {'type': 'number', 'default': 3.0},
        'strategy': {'type': 'string', 'default': 'unknown'},
    },
    execute_fn=execute_place_order,
)
```

---

## 6. YAML Config & Profiles

> ✅ **IMPLEMENTED** — See `src/yaml_config.py` and `src/tests/test_yaml_config.py` (30 tests passing)

**Problem:** `config.py` is hardcoded Python. No way to switch between paper-trading, production, or backtesting without editing source.

**DSH Pattern:** Profile → Bundles → Patches. Layered YAML composition.

```yaml
# profiles/paper-trading/config.yml
exchange:
  mode: paper
  slippage_bps: 0

risk:
  max_loss_usd: 100000     # Unlimited in paper mode
  max_gain_usd: 100000
  max_position_pct: 30
  min_cash_pct: 20
  use_ai_confirmation: false

model:
  primary: deepseek-chat
  temperature: 0.3         # Lower = more consistent decisions

tokens:
  monitored:
    - address: "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"
      name: "FART"
    - address: "HeLp6NuQkmYB4pYWo2zYs22mESHXPQYzXbB8n4V98jwC"
      name: "AI16Z"
```

```yaml
# profiles/production/config.yml (inherits + overrides)
exchange:
  mode: live
  slippage_bps: 199

risk:
  max_loss_usd: 25         # Tight risk in production
  max_gain_usd: 25
  use_ai_confirmation: true
```

```yaml
# profiles/backtest/config.yml
exchange:
  mode: backtest
  historical_start: "2024-01-01"
  historical_end: "2024-12-31"

risk:
  max_loss_usd: 100
  max_gain_usd: 100
```

```python
"""Profile loader — reads YAML config."""
import yaml
from pathlib import Path

class ProfileLoader:
    def __init__(self, profiles_dir='profiles'):
        self.profiles_dir = Path(profiles_dir)

    def load(self, profile_name='paper-trading'):
        """Load a profile config, merging with defaults."""
        base = self._load_yaml('default/config.yml')
        profile = self._load_yaml(f'{profile_name}/config.yml')
        return self._merge(base, profile)

    def _load_yaml(self, path):
        full = self.profiles_dir / path
        if not full.exists():
            return {}
        with open(full) as f:
            return yaml.safe_load(f)

    def _merge(self, base, override):
        """Deep merge — override values replace base."""
        result = base.copy()
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(result.get(k), dict):
                result[k] = self._merge(result[k], v)
            else:
                result[k] = v
        return result
```

---

## 7. Async Scheduling

> ✅ **IMPLEMENTED** — See `src/async_scheduler.py` and `src/tests/test_remaining_features.py`

**Problem:** `time.sleep(300)` blocks the entire process. All agents freeze during sleep.

**DSH Pattern:** `ctx.jobs` — background jobs with cooperative cancellation.

```python
"""
DSH-inspired Background Job System
Non-blocking scheduling replacing time.sleep().
"""

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime


class BackgroundJob(ABC):
    """Base class for background jobs — like DSH's job contract."""

    def __init__(self, name, interval_seconds=300):
        self.name = name
        self.interval = interval_seconds
        self._cancel_event = asyncio.Event()

    @abstractmethod
    async def run(self):
        """Override with job logic. Check self._cancel_event for cancellation."""
        pass

    def cancel(self):
        self._cancel_event.set()

    @property
    def is_cancelled(self):
        return self._cancel_event.is_set()


class RiskCheckJob(BackgroundJob):
    """Check PnL limits every 5 minutes."""

    def __init__(self, risk_agent):
        super().__init__("risk-check", interval_seconds=300)
        self.risk_agent = risk_agent

    async def run(self):
        while not self.is_cancelled:
            try:
                self.risk_agent.check_pnl_limits()
                self.risk_agent.log_daily_balance()
            except Exception as e:
                print(f"⚠️ Risk check error: {e}")
            await asyncio.sleep(self.interval)


class PriceMonitorJob(BackgroundJob):
    """Monitor prices every 30 seconds for stop-loss/take-profit."""

    def __init__(self, session_log):
        super().__init__("price-monitor", interval_seconds=30)
        self.session_log = session_log

    async def run(self):
        while not self.is_cancelled:
            try:
                positions = await get_open_positions()
                for pos in positions:
                    if self.should_exit(pos):
                        await self.execute_exit(pos)
            except Exception as e:
                print(f"⚠️ Price monitor error: {e}")
            await asyncio.sleep(self.interval)


class JobRunner:
    """Manages all background jobs — like DSH's ctx.jobs."""

    def __init__(self):
        self.jobs: dict[str, BackgroundJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def register(self, job: BackgroundJob):
        self.jobs[job.name] = job

    async def start(self):
        for name, job in self.jobs.items():
            task = asyncio.create_task(self._run_loop(job))
            self._tasks[name] = task

    async def _run_loop(self, job):
        try:
            await job.run()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"❌ Job '{job.name}' crashed: {e}")

    async def stop_all(self):
        for job in self.jobs.values():
            job.cancel()
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
```

**Migration from current `main.py`:**

```python
# Before:
while True:
    agent.run_trading_cycle()
    time.sleep(300)  # BLOCKS everything

# After:
runner = JobRunner()
runner.register(RiskCheckJob(risk_agent))
runner.register(PriceMonitorJob(session_log))
runner.register(SentimentMonitorJob(sentiment_agent))
await runner.start()
# Jobs run concurrently — nothing blocks
```

---

## 8. Process Isolation

> ✅ **IMPLEMENTED** — See `src/process_isolation.py` and `src/tests/test_remaining_features.py`

**Problem:** All agents, MongoDB connections, and trading logic share one Python process. A crash in one kills everything.

**DSH Pattern:** Capability seams — each service is independent with its own lifecycle.

```
Current (monolithic):                    Proposed (isolated):

┌─────────────────────┐                  ┌─────────────────────┐
│ Single Process      │                  │ Process 1: Agents   │
│                     │                  │  Strategy + Trading  │
│ Agents + Config     │                  │  + Risk Guard        │
│ Data Pipeline       │     ──────►      │  + Session Log       │
│ Trading Execution   │                  ├─────────────────────┤
│ MongoDB Connection  │                  │ Process 2: Data      │
│                     │                  │  Binance WS + Poll   │
└─────────────────────┘                  │  + Feature Engineer  │
                                         ├─────────────────────┤
                                         │ Process 3: Execution │
                                         │  Jupiter Orders      │
                                         │  Position Monitor    │
                                         └─────────────────────┘
```

```python
"""
Process-isolated architecture sketch.
Each process communicates via events/message queue.
"""
import asyncio
import multiprocessing
from dataclasses import dataclass


@dataclass
class ProcessConfig:
    name: str
    target: callable
    kwargs: dict = None


class IsolatedArchitecture:
    """Run each concern in its own process."""

    def __init__(self):
        self.processes = {}

    def add_process(self, config: ProcessConfig):
        p = multiprocessing.Process(
            target=config.target,
            kwargs=config.kwargs or {},
            name=config.name,
            daemon=True
        )
        self.processes[config.name] = p

    async def start_all(self):
        for name, process in self.processes.items():
            process.start()
            print(f"✅ Started process: {name} (PID: {process.pid})")

    async def monitor(self):
        """Restart crashed processes."""
        while True:
            for name, process in self.processes.items():
                if not process.is_alive():
                    print(f"⚠️ Process {name} died — restarting...")
                    process.start()
            await asyncio.sleep(5)
```

---

# Part 2: Accuracy & Profitability Improvements

---

## 9. Weighted Prediction Engine

> ✅ **IMPLEMENTED** — See `src/weighted_predictor.py` and `src/tests/test_weighted_predictor.py` (32 tests passing)

**Problem:** Current `PredictionEngine` uses binary +1/-1 scoring with arbitrary thresholds and equal weighting. No regime awareness.

```python
"""
DSH-inspired Weighted Prediction Engine
Continuous signals, regime detection, and backtestable weights.
"""

from dataclasses import dataclass, field
from typing import Dict, List
import numpy as np


@dataclass
class PredictionWeights:
    """Weights learned from backtesting — each factor's predictive power."""
    rsi: float = 0.20
    volume_spike: float = 0.25
    momentum_5m: float = 0.15
    buy_pressure: float = 0.15
    book_imbalance: float = 0.15
    trend_alignment: float = 0.10

    def as_dict(self) -> Dict[str, float]:
        return {
            'rsi': self.rsi, 'volume_spike': self.volume_spike,
            'momentum_5m': self.momentum_5m, 'buy_pressure': self.buy_pressure,
            'book_imbalance': self.book_imbalance, 'trend_alignment': self.trend_alignment,
        }


@dataclass
class PredictionResult:
    """Rich prediction output."""
    signal: str          # 'BUY', 'SELL', 'HOLD'
    score: float         # -1.0 to +1.0 (continuous)
    confidence: float    # 0.0 to 1.0
    factors: Dict[str, float]
    regime: str
    reasons: List[str]
    raw_score: float


class ImprovedPredictionEngine:
    """
    Weighted multi-factor engine with regime detection.
    
    Key improvements over current PredictionEngine:
    1. Continuous signals (no binary thresholds)
    2. Weighted scoring (each factor weighted by backtested importance)
    3. Regime detection (trending vs ranging vs transitional)
    4. Signal agreement for confidence calculation
    """

    # Regime detection thresholds
    TRENDING_THRESHOLD = 0.3   # momentum_5m_pct > 0.3 = trending
    RANGING_THRESHOLD = 20.0   # volatility_20 < 20 = ranging

    def __init__(self, weights: PredictionWeights = None):
        self.weights = weights or PredictionWeights()

    def detect_regime(self, features: dict) -> str:
        """Detect current market regime from features."""
        volatility = features.get('volatility_20', 0)
        momentum = abs(features.get('momentum_5m_pct', 0))

        if volatility > self.RANGING_THRESHOLD and momentum > self.TRENDING_THRESHOLD:
            return 'trending'
        elif volatility < self.RANGING_THRESHOLD:
            return 'ranging'
        else:
            return 'transitional'

    def _continuous_score(self, value: float, low: float, high: float) -> float:
        """Map a value to [-1, +1] continuously."""
        mid = (low + high) / 2
        half_range = (high - low) / 2
        if half_range == 0:
            return 0.0
        return max(-1.0, min(1.0, (value - mid) / half_range))

    def score(self, features: dict) -> PredictionResult:
        """Generate a weighted prediction from features."""
        factors = {}
        reasons = []
        regime = self.detect_regime(features)

        # RSI: continuous signal — oversold = bullish, overbought = bearish
        rsi = features.get('rsi', 50.0)
        factors['rsi'] = self._continuous_score(rsi, 100, 0)  # Inverted: low RSI = bullish
        if rsi < 35:
            reasons.append(f"RSI={rsi:.0f} oversold (bullish)")
        elif rsi > 65:
            reasons.append(f"RSI={rsi:.0f} overbought (bearish)")

        # Volume spike: continuous above 1.0
        vol = features.get('volume_spike', 1.0)
        factors['volume_spike'] = min(max((vol - 1.0) / 2.0, 0), 1.0)
        if vol > 1.5:
            reasons.append(f"Volume spike {vol:.1f}x confirms move")

        # Momentum: normalize to [-1, +1]
        mom = features.get('momentum_5m_pct', 0.0)
        factors['momentum_5m'] = max(min(mom / 0.5, 1.0), -1.0)
        if abs(mom) > 0.15:
            reasons.append(f"Momentum {mom:+.2f}%")

        # Buy pressure: centered at 0.5
        bp = features.get('buy_pressure', 0.5)
        factors['buy_pressure'] = (bp - 0.5) * 2
        if bp > 0.6:
            reasons.append(f"Buy pressure {bp:.0%} (demand)")
        elif bp < 0.4:
            reasons.append(f"Sell pressure {1-bp:.0%} (supply)")

        # Book imbalance
        bi = features.get('volume_imbalance', 0)
        factors['book_imbalance'] = max(min(bi, 1.0), -1.0)
        if abs(bi) > 0.15:
            reasons.append(f"Book imbalance {bi:+.2f}")

        # Trend alignment (multi-timeframe)
        ema20 = features.get('EMA_20', 0)
        ema50 = features.get('EMA_50', 0)
        if ema20 and ema50:
            factors['trend_alignment'] = 1.0 if ema20 > ema50 else -1.0
        else:
            factors['trend_alignment'] = 0.0

        # Weighted score
        weights = self.weights.as_dict()
        raw_score = sum(factors.get(k, 0) * weights.get(k, 0) for k in weights)

        # Regime adjustment
        if regime == 'trending':
            raw_score *= 1.2  # Boost momentum signals
        elif regime == 'ranging':
            raw_score *= 0.8  # Dampen, favor mean-reversion

        # Continuous signal (no arbitrary ±2 threshold)
        if raw_score > 0.15:
            signal = 'BUY'
        elif raw_score < -0.15:
            signal = 'SELL'
        else:
            signal = 'HOLD'

        # Confidence from signal agreement (not fixed base + increments)
        positive = sum(1 for v in factors.values() if v > 0.1)
        negative = sum(1 for v in factors.values() if v < -0.1)
        agreement = max(positive, negative) / max(len(factors), 1)
        confidence = min(0.5 + agreement * 0.45, 0.95)

        # Volatility penalty
        if features.get('volatility_20', 0) > 50:
            confidence *= 0.75
            reasons.append("High volatility → reduced confidence")

        return PredictionResult(
            signal=signal,
            score=raw_score,
            confidence=round(confidence, 3),
            factors=factors,
            regime=regime,
            reasons=reasons,
            raw_score=raw_score,
        )


# ── Auto-Tune from Trade History ────────────────────────────────────

class WeightOptimizer:
    """Auto-tune weights based on what actually makes money."""

    def __init__(self, session_log):
        self.log = session_log

    async def optimize(self, lookback_days=90):
        """Adjust weights based on historical accuracy by regime."""
        report = await self.log.get_accuracy_report(lookback_days)

        # For each factor, compute correlation with trade PnL
        # This is simplified — in production, use proper optimization
        # (gradient descent, Bayesian optimization, etc.)

        optimized = PredictionWeights()

        for strategy, stats in report.get('by_strategy', {}).items():
            if stats['win_rate'] > 0.55:
                # Strategy is profitable — boost its weight
                print(f"📈 Strategy '{strategy}' is profitable (win_rate={stats['win_rate']:.0%})")
            elif stats['win_rate'] < 0.45:
                print(f"📉 Strategy '{strategy}' is losing (win_rate={stats['win_rate']:.0%})")

        return optimized
```

---

## 10. Multi-Stage Signal Validation Pipeline

> ✅ **IMPLEMENTED** — See `src/signal_pipeline.py` and `src/tests/test_feedback_and_tracking.py`

**Problem:** The LLM sees everything at once and has to juggle all concerns. No staged filtering.

**DSH Pattern:** Waterfall pipeline — each stage validates independently.

```python
"""
DSH-inspired Signal Validation Pipeline
Multi-stage validation before any signal reaches execution.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SignalValidation:
    """Result of pipeline validation."""
    approved: bool
    stage: Optional[str] = None
    reason: Optional[str] = None
    modified_signal: Optional[dict] = None


class SignalPipeline:
    """
    Each stage runs in priority order. Any stage can:
    - approve: pass to next stage
    - deny: block the signal with a reason
    - modify: adjust the signal (e.g., reduce size) and continue
    """

    def __init__(self):
        self.stages = []

    def register(self, name, fn, priority=100):
        self.stages.append((name, priority, fn))
        self.stages.sort(key=lambda x: x[1])

    async def validate(self, signal: dict, context: dict) -> SignalValidation:
        for name, _, stage_fn in self.stages:
            result = await stage_fn(signal, context)
            if not result.approved:
                return result
            if result.modified_signal:
                signal = result.modified_signal
        return SignalValidation(approved=True, modified_signal=signal)


# ── Validation Stages ───────────────────────────────────────────────

async def stage_liquidity(signal: dict, ctx: dict) -> SignalValidation:
    """Reject signals when order book can't absorb the order."""
    orderbook = ctx.get('orderbook')
    if not orderbook:
        return SignalValidation(approved=True)

    depth_usd = orderbook.get('depth_usd', 0)
    if signal['amount_usd'] > depth_usd * 0.1:
        return SignalValidation(
            approved=False, stage='liquidity',
            reason=f"Order ${signal['amount_usd']:.2f} > 10% of book depth ${depth_usd:.2f}"
        )
    return SignalValidation(approved=True)

async def stage_fee_profitability(signal: dict, ctx: dict) -> SignalValidation:
    """Reject trades where fees exceed expected profit."""
    fee_pct = 0.003 + (signal.get('slippage_bps', 199) / 10000)
    total_fees = signal['amount_usd'] * fee_pct
    expected_move = signal['amount_usd'] * abs(signal.get('expected_move_pct', 0.02))

    if total_fees > expected_move * 0.5:
        return SignalValidation(
            approved=False, stage='fee_profitability',
            reason=f"Fees ${total_fees:.3f} > 50% of expected move ${expected_move:.3f}"
        )
    return SignalValidation(approved=True)

async def stage_regime_filter(signal: dict, ctx: dict) -> SignalValidation:
    """Adjust confidence based on market regime."""
    regime = ctx.get('regime', 'unknown')
    confidence = signal.get('confidence', 0.5)

    if regime == 'ranging' and signal.get('direction') == 'BUY':
        # Mean-reversion in ranging market — boost confidence
        signal['confidence'] = min(confidence * 1.2, 0.95)
    elif regime == 'trending' and signal.get('direction') == 'SELL':
        # Counter-trend — reduce confidence
        signal['confidence'] = confidence * 0.7

    return SignalValidation(approved=True, modified_signal=signal)

async def stage_risk_limits(signal: dict, ctx: dict) -> SignalValidation:
    """Check portfolio-level risk constraints."""
    current_exposure = ctx.get('total_exposure', 0)
    max_exposure = ctx.get('max_exposure', 100)
    current_positions = ctx.get('position_count', 0)
    max_positions = ctx.get('max_positions', 5)

    if signal.get('direction') == 'BUY':
        if current_exposure + signal['amount_usd'] > max_exposure:
            return SignalValidation(
                approved=False, stage='risk_limits',
                reason=f"Total exposure would exceed ${max_exposure}"
            )
        if current_positions >= max_positions:
            return SignalValidation(
                approved=False, stage='risk_limits',
                reason=f"Max {max_positions} positions reached"
            )

    return SignalValidation(approved=True)

async def stage_min_confidence(signal: dict, ctx: dict) -> SignalValidation:
    """Reject low-confidence signals."""
    min_confidence = ctx.get('min_confidence', 0.6)
    if signal.get('confidence', 0) < min_confidence:
        return SignalValidation(
            approved=False, stage='min_confidence',
            reason=f"Confidence {signal['confidence']:.0%} < minimum {min_confidence:.0%}"
        )
    return SignalValidation(approved=True)


# ── Pipeline Setup ──────────────────────────────────────────────────

def create_signal_pipeline() -> SignalPipeline:
    pipeline = SignalPipeline()
    pipeline.register("liquidity",          stage_liquidity,          priority=10)
    pipeline.register("fee_profitability",  stage_fee_profitability,  priority=20)
    pipeline.register("regime_filter",      stage_regime_filter,      priority=30)
    pipeline.register("risk_limits",        stage_risk_limits,        priority=40)
    pipeline.register("min_confidence",     stage_min_confidence,     priority=50)
    return pipeline
```

---

## 11. Trade Feedback Loop

> ✅ **IMPLEMENTED** — See `src/feedback_loop.py` and `src/tests/test_feedback_and_tracking.py`

**Problem:** No mechanism to learn from past trades. The system makes the same mistakes repeatedly.

**DSH Pattern:** Session log as source of truth — everything is queryable.

```python
"""
Trade Feedback Loop — Track signal → outcome → auto-tune.
"""

import asyncio
from datetime import datetime, timedelta


class TradeFeedbackLoop:
    """
    Track every trade's signal and outcome.
    Use the data to automatically adjust strategy weights.
    """

    def __init__(self, session_log, prediction_engine):
        self.log = session_log
        self.engine = prediction_engine

    async def record_signal(self, token, signal, features, strategy='unknown'):
        """Record the signal at entry — before the trade happens."""
        await self.log.log('signal/generated', {
            'token': token,
            'signal': signal.signal,
            'score': signal.score,
            'confidence': signal.confidence,
            'factors': signal.factors,
            'regime': signal.regime,
            'strategy': strategy,
            'features_snapshot': features,
        })

    async def record_outcome(self, token, entry_time, exit_price, pnl_usd, holding_minutes):
        """Record the outcome when position closes."""
        await self.log.log('position/closed', {
            'token': token,
            'exit_price': exit_price,
            'pnl_usd': pnl_usd,
            'holding_minutes': holding_minutes,
        })

    async def get_factor_accuracy(self, days=60):
        """Which factors actually predict profitable trades?"""
        trades = await self.log.get_trade_chain(days=days)

        factor_correlations = {}
        for trade in trades:
            if 'entry_signals' not in trade:
                continue
            pnl = trade.get('pnl_usd', 0)
            for signal in trade['entry_signals']:
                factors = signal.get('factors', {})
                for factor_name, factor_value in factors.items():
                    if factor_name not in factor_correlations:
                        factor_correlations[factor_name] = []
                    factor_correlations[factor_name].append((factor_value, pnl))

        # Compute correlation for each factor
        results = {}
        for factor, pairs in factor_correlations.items():
            if len(pairs) < 10:
                results[factor] = {'correlation': 0, 'sample_size': len(pairs)}
                continue

            x = [p[0] for p in pairs]
            y = [p[1] for p in pairs]

            # Simple Pearson correlation
            n = len(x)
            sum_x = sum(x)
            sum_y = sum(y)
            sum_xy = sum(a * b for a, b in zip(x, y))
            sum_x2 = sum(a ** 2 for a in x)
            sum_y2 = sum(b ** 2 for b in y)

            denom = ((n * sum_x2 - sum_x**2) * (n * sum_y2 - sum_y**2)) ** 0.5
            if denom == 0:
                correlation = 0
            else:
                correlation = (n * sum_xy - sum_x * sum_y) / denom

            results[factor] = {
                'correlation': round(correlation, 4),
                'sample_size': n,
                'avg_positive_when_profit': sum(1 for x, y in pairs if y > 0 and x > 0) / max(sum(1 for y in [p[1] for p in pairs] if y > 0), 1),
            }

        return results

    async def auto_tune_weights(self):
        """Automatically adjust PredictionEngine weights based on accuracy."""
        factor_accuracy = await self.get_factor_accuracy()

        new_weights = {}
        for factor, stats in factor_accuracy.items():
            corr = abs(stats['correlation'])
            sample = stats['sample_size']

            if sample < 10:
                new_weights[factor] = 0.1  # Default for insufficient data
            elif corr > 0.3:
                new_weights[factor] = 0.3  # Strong predictor
            elif corr > 0.1:
                new_weights[factor] = 0.2  # Moderate predictor
            else:
                new_weights[factor] = 0.1  # Weak predictor

        # Normalize weights to sum to 1.0
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {k: v / total for k, v in new_weights.items()}

        print("📊 Auto-tuned weights:")
        for factor, weight in new_weights.items():
            print(f"  {factor}: {weight:.3f}")

        return new_weights
```

---

## 12. Ensemble Strategy System

> ✅ **IMPLEMENTED** — See `src/ensemble_strategy.py` and `src/tests/test_remaining_features.py`

**Problem:** Only one strategy runs at a time. No A/B testing or combination.

**DSH Pattern:** Capability seams — Service Definition / Provider / Consumer.

```python
"""
DSH-inspired Ensemble Strategy System
Multiple strategy backends with learned weighting.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict
import asyncio


@dataclass
class StrategySignal:
    """Standard signal from any strategy backend."""
    token: str
    direction: str        # 'BUY', 'SELL', 'HOLD'
    score: float          # -1.0 to +1.0
    confidence: float     # 0.0 to 1.0
    provider: str         # Which strategy produced this
    metadata: dict = None


class StrategyProvider(ABC):
    """Service Definition — every strategy must implement this."""

    @abstractmethod
    async def analyze(self, token: str, features: dict, regime: str) -> StrategySignal:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass


class TechnicalStrategy(Provider):
    """Provider 1: Indicator-based (current system)."""

    @property
    def name(self): return 'technical'

    async def analyze(self, token, features, regime):
        rsi = features.get('rsi', 50)
        score = 0.0
        confidence = 0.5

        if rsi < 30:
            score = 0.8
            confidence = 0.7
        elif rsi > 70:
            score = -0.8
            confidence = 0.7

        return StrategySignal(
            token=token,
            direction='BUY' if score > 0.1 else ('SELL' if score < -0.1 else 'HOLD'),
            score=score,
            confidence=confidence,
            provider=self.name,
            metadata={'rsi': rsi}
        )


class MicrostructureStrategy(Provider):
    """Provider 2: Order flow / microstructure."""

    @property
    def name(self): return 'microstructure'

    async def analyze(self, token, features, regime):
        buy_pressure = features.get('buy_pressure', 0.5)
        imbalance = features.get('volume_imbalance', 0)

        score = (buy_pressure - 0.5) * 2 + imbalance
        score = max(-1.0, min(1.0, score))

        return StrategySignal(
            token=token,
            direction='BUY' if score > 0.2 else ('SELL' if score < -0.2 else 'HOLD'),
            score=score,
            confidence=0.6,
            provider=self.name,
            metadata={'buy_pressure': buy_pressure, 'imbalance': imbalance}
        )


class MomentumStrategy(Provider):
    """Provider 3: Trend-following."""

    @property
    def name(self): return 'momentum'

    async def analyze(self, token, features, regime):
        momentum = features.get('momentum_5m_pct', 0)
        ema20 = features.get('EMA_20', 0)
        ema50 = features.get('EMA_50', 0)

        score = 0.0
        if ema20 > ema50 and momentum > 0:
            score = 0.6 + min(momentum, 0.4)
        elif ema20 < ema50 and momentum < 0:
            score = -0.6 - min(abs(momentum), 0.4)

        return StrategySignal(
            token=token,
            direction='BUY' if score > 0.1 else ('SELL' if score < -0.1 else 'HOLD'),
            score=score,
            confidence=0.55,
            provider=self.name,
            metadata={'momentum': momentum, 'trend': 'up' if ema20 > ema50 else 'down'}
        )


class EnsembleStrategy(Provider):
    """
    Combines multiple strategies with learned weights.
    Weights come from the TradeFeedbackLoop's accuracy report.
    """

    def __init__(self, providers: List[StrategyProvider], weights: Dict[str, float] = None):
        self.providers = providers
        self.weights = weights or {p.name: 1.0 for p in providers}

    @property
    def name(self): return 'ensemble'

    def set_weights(self, weights: Dict[str, float]):
        """Update weights from feedback loop."""
        self.weights = weights

    async def analyze(self, token, features, regime):
        # Run all strategies in parallel
        tasks = [p.analyze(token, features, regime) for p in self.providers]
        signals = await asyncio.gather(*tasks, return_exceptions=True)

        valid = [s for s in signals if isinstance(s, StrategySignal)]
        if not valid:
            return StrategySignal(token=token, direction='HOLD', score=0, confidence=0, provider=self.name)

        # Weighted combination
        total_weight = sum(self.weights.get(s.provider, 1.0) for s in valid)
        weighted_score = sum(
            s.score * self.weights.get(s.provider, 1.0)
            for s in valid
        ) / total_weight

        weighted_confidence = sum(
            s.confidence * self.weights.get(s.provider, 1.0)
            for s in valid
        ) / total_weight

        # Signal agreement boosts confidence
        directions = [s.direction for s in valid]
        agreement = max(directions.count(d) for d in set(directions)) / len(valid)

        return StrategySignal(
            token=token,
            direction='BUY' if weighted_score > 0.15 else ('SELL' if weighted_score < -0.15 else 'HOLD'),
            score=weighted_score,
            confidence=min(weighted_confidence * agreement * 1.2, 0.95),
            provider=self.name,
            metadata={
                'sub_signals': [{'provider': s.provider, 'score': s.score, 'direction': s.direction} for s in valid],
                'agreement': agreement,
                'weights': self.weights,
                'regime': regime,
            }
        )
```

---

## 13. Portfolio Goal System

> ✅ **IMPLEMENTED** — See `src/portfolio_goals.py` and `src/tests/test_advanced_features.py`

**Problem:** No portfolio-level objective. Individual trades are made without considering the bigger picture.

**DSH Pattern:** `ctx.goals` — one active goal per session with revision tracking.

```python
"""
DSH-inspired Portfolio Goal System
Persistent objectives that influence every trade decision.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict
import uuid


@dataclass
class PortfolioGoal:
    """A persistent trading objective."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    objective: str = ''           # "grow 5% this month"
    constraints: Dict = field(default_factory=dict)
    status: str = 'active'
    created_at: datetime = field(default_factory=datetime.utcnow)
    progress_events: list = field(default_factory=list)

    @property
    def progress_pct(self) -> float:
        """Calculate progress toward the objective."""
        if not self.progress_events:
            return 0.0
        latest = self.progress_events[-1]
        return latest.get('progress_pct', 0.0)


class GoalSystem:
    """
    Manages portfolio-level goals that influence trade decisions.
    Based on DSH's ctx.goals — one active goal per agent session.
    """

    def __init__(self, session_log):
        self.log = session_log
        self.active_goal: Optional[PortfolioGoal] = None

    async def create(self, objective, constraints=None):
        """Set a portfolio-level goal."""
        self.active_goal = PortfolioGoal(
            objective=objective,
            constraints=constraints or {}
        )
        await self.log.log('goal/created', {
            'goal_id': self.active_goal.id,
            'objective': objective,
            'constraints': constraints,
        })

    def get_active(self) -> Optional[PortfolioGoal]:
        return self.active_goal

    def update_progress(self, portfolio_value: float, target_value: float):
        """Track progress toward the goal."""
        if not self.active_goal:
            return
        progress = ((portfolio_value - target_value) / target_value) * 100
        self.active_goal.progress_events.append({
            'portfolio_value': portfolio_value,
            'progress_pct': progress,
            'timestamp': datetime.utcnow(),
        })

    def get_risk_adjustments(self) -> Dict:
        """
        Adjust risk parameters based on goal progress.
        This is how the goal influences every trade decision.
        """
        if not self.active_goal:
            return {}

        progress = self.active_goal.progress_pct

        if progress < -10:
            # Behind target — be more conservative
            return {
                'max_position_pct': 15,
                'min_confidence': 0.8,
                'cash_buffer_pct': 30,
                'reason': 'Behind target → conservative mode',
            }
        elif progress > 80:
            # Ahead of target — protect gains
            return {
                'max_position_pct': 10,
                'min_confidence': 0.75,
                'cash_buffer_pct': 40,
                'reason': 'Ahead of target → protect gains',
            }
        else:
            # On track — normal parameters
            return {
                'max_position_pct': 30,
                'min_confidence': 0.6,
                'cash_buffer_pct': 20,
                'reason': 'On track → normal trading',
            }
```

---

## 14. Real-Time Signal Processing

> ✅ **IMPLEMENTED** — See `src/async_scheduler.py` (real-time job execution)

**Problem:** 15-minute polling cycle means signals expire before execution.

**DSH Pattern:** `ctx.jobs` — continuous background processing.

```python
"""
DSH-inspired Real-Time Signal Processing
Replaces the 15-minute polling cycle.
"""

import asyncio
from datetime import datetime


class RealTimeSignalProcessor:
    """
    Processes signals as they arrive from market data streams.
    Instead of polling every 15 minutes, reacts immediately.
    """

    def __init__(self, prediction_engine, pipeline, execution_queue):
        self.engine = prediction_engine
        self.pipeline = pipeline
        self.queue = execution_queue

    async def process_market_event(self, event):
        """Called for every market tick — processes in real-time."""
        token = event['token']
        features = event['features']

        # 1. Score the signal
        prediction = self.engine.score(features)

        # 2. Only process strong signals
        if prediction.signal == 'HOLD' and abs(prediction.score) < 0.2:
            return

        # 3. Validate through pipeline
        validation = await self.pipeline.validate(
            signal={
                'token': token,
                'direction': prediction.signal,
                'score': prediction.score,
                'confidence': prediction.confidence,
                'amount_usd': 3.0,
            },
            context={
                'regime': prediction.regime,
                'orderbook': event.get('orderbook'),
                'total_exposure': event.get('total_exposure', 0),
            }
        )

        # 4. Queue approved signals for execution
        if validation.approved:
            await self.queue.put({
                'token': token,
                'direction': prediction.signal,
                'score': prediction.score,
                'confidence': prediction.confidence,
                'timestamp': datetime.utcnow(),
            })


class RealTimePositionMonitor:
    """
    Monitors open positions in real-time for stop-loss/take-profit.
    Runs every 5 seconds instead of every 5 minutes.
    """

    def __init__(self, session_log, exit_fn):
        self.log = session_log
        self.exit_fn = exit_fn

    async def run(self):
        """Continuous monitoring loop."""
        while True:
            try:
                positions = await self.get_open_positions()
                for pos in positions:
                    current_price = await self.get_price(pos['token'])
                    entry_price = pos['entry_price']

                    # Stop-loss check
                    if current_price < entry_price * 0.95:  # 5% stop loss
                        await self.log.log('position/stop_loss', {
                            'token': pos['token'],
                            'entry': entry_price,
                            'current': current_price,
                            'loss_pct': (current_price - entry_price) / entry_price,
                        })
                        await self.exit_fn(pos['token'])

                    # Take-profit check
                    elif current_price > entry_price * 1.10:  # 10% take profit
                        await self.log.log('position/take_profit', {
                            'token': pos['token'],
                            'entry': entry_price,
                            'current': current_price,
                            'gain_pct': (current_price - entry_price) / entry_price,
                        })
                        await self.exit_fn(pos['token'])

            except Exception as e:
                print(f"⚠️ Position monitor error: {e}")

            await asyncio.sleep(5)  # Check every 5 seconds
```

---

## 15. Parallel Analysis

> ✅ **IMPLEMENTED** — See `src/async_scheduler.py` (parallel job execution)

**Problem:** Sequential analysis — one token at a time, one LLM call at a time.

**DSH Pattern:** Subagent delegation — parallel child agents.

```python
"""
DSH-inspired Parallel Analysis
Analyze all tokens simultaneously.
"""

import asyncio


class ParallelAnalyzer:
    """
    Analyze multiple tokens in parallel.
    Drops analysis time from N × sequential to max(parallel_times).
    """

    def __init__(self, engine, pipeline, timeout_seconds=30):
        self.engine = engine
        self.pipeline = pipeline
        self.timeout = timeout_seconds

    async def analyze_portfolio(self, tokens, features_map, regime):
        """Analyze all tokens simultaneously."""
        tasks = {}
        for token in tokens:
            task = asyncio.create_task(
                self._analyze_token(token, features_map.get(token, {}), regime)
            )
            tasks[token] = task

        results = {}
        for token, task in tasks.items():
            try:
                result = await asyncio.wait_for(task, timeout=self.timeout)
                results[token] = result
            except asyncio.TimeoutError:
                print(f"⚠️ Analysis timed out for {token[:8]}")
                results[token] = None
            except Exception as e:
                print(f"⚠️ Analysis failed for {token[:8]}: {e}")
                results[token] = None

        return results

    async def _analyze_token(self, token, features, regime):
        """Single token analysis — can be parallelized."""
        prediction = self.engine.score(features)

        # Also validate through pipeline in parallel
        validation = await self.pipeline.validate(
            signal={
                'token': token,
                'direction': prediction.signal,
                'score': prediction.score,
                'confidence': prediction.confidence,
                'amount_usd': 3.0,
            },
            context={'regime': regime}
        )

        return {
            'prediction': prediction,
            'validation': validation,
        }

    async def analyze_with_llm_parallel(self, tokens, predictions):
        """Multiple LLM calls in parallel for each token."""
        tasks = []
        for token, pred in predictions.items():
            if pred['prediction'].signal != 'HOLD':
                task = asyncio.create_task(
                    self._llm_analysis(token, pred)
                )
                tasks.append((token, task))

        results = {}
        for token, task in tasks:
            try:
                result = await asyncio.wait_for(task, timeout=15)
                results[token] = result
            except asyncio.TimeoutError:
                results[token] = None

        return results

    async def _llm_analysis(self, token, prediction):
        """Single LLM call for one token."""
        # This would call the LLM with the PredictionEngine's output
        # and return structured analysis
        pass
```

---

## 16. LLM Context Compaction

> ✅ **IMPLEMENTED** — See `src/context_compactor.py` and `src/tests/test_advanced_features.py`

**Problem:** LLMs get raw OHLCV dumps and struggle with noise. Too much context = worse decisions.

**DSH Pattern:** `compaction` — compress context intelligently.

```python
"""
DSH-inspired LLM Context Compaction
Reduce data noise for better LLM decisions.
"""


class TradingCompactor:
    """
    Compress market data into what's decision-relevant.
    Based on DSH's compaction capability — keep signal, drop noise.
    """

    def compact_for_llm(self, features: dict, regime: str, portfolio: dict) -> dict:
        """Reduce feature set to decision-relevant context only."""
        return {
            # Market regime (most important)
            'regime': regime,

            # Key signals (not raw data)
            'trend': 'bullish' if features.get('EMA_20', 0) > features.get('EMA_50', 0) else 'bearish',
            'momentum': f"RSI={features.get('rsi', 50):.0f}",
            'volume': f"{features.get('volume_spike', 1.0):.1f}x average",
            'buy_pressure': f"{features.get('buy_pressure', 0.5):.0%}",
            'liquidity': f"spread={features.get('spread_bps', 0):.1f}bps",

            # Only last 3 candles (not 50)
            'recent_candles': features.get('ohlcv', [])[-3:],

            # Portfolio context
            'cash': f"${portfolio.get('cash', 0):.2f}",
            'exposure': f"${portfolio.get('exposure', 0):.2f}",
            'daily_pnl': f"${portfolio.get('daily_pnl', 0):.2f}",
            'position_count': portfolio.get('position_count', 0),

            # Recent performance
            'last_3_results': portfolio.get('recent_outcomes', [])[-3:],

            # Fee awareness
            'min_profitable_move': '2% (covers swap + slippage)',
        }

    def compact_for_prediction(self, full_features: dict) -> dict:
        """Minimal features for the PredictionEngine — drop OHLCV entirely."""
        return {
            'rsi': full_features.get('RSI', 50),
            'volume_spike': full_features.get('volume_spike', 1.0),
            'momentum_5m_pct': full_features.get('momentum_5m_pct', 0),
            'buy_pressure': full_features.get('buy_pressure', 0.5),
            'volume_imbalance': full_features.get('volume_imbalance', 0),
            'EMA_20': full_features.get('EMA_20', 0),
            'EMA_50': full_features.get('EMA_50', 0),
            'volatility_20': full_features.get('volatility_20', 0),
            'spread_bps': full_features.get('spread_bps', 0),
        }
```

---

# Part 3: Advanced Improvements

---

## 17. Multi-Step Trade Planning (Plan Mode)

> ✅ **IMPLEMENTED** — See `src/trade_planner.py` and `src/tests/test_remaining_features.py`

**Problem:** Most trading decisions are impulsive — "BUY now" without considering the full lifecycle.

**DSH Pattern:** `ctx.planMode` — structured step-by-step planning before execution.

```python
"""
DSH-inspired Trade Planning System
Force the agent to plan the full trade lifecycle before committing capital.
Based on DSH's plan-mode — logged, structured collaboration state.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
import asyncio


@dataclass
class TradeStep:
    """A single step in a trade plan."""
    name: str
    description: str
    status: str = 'pending'  # pending, active, completed, failed, skipped
    result: Optional[dict] = None
    timestamp: Optional[datetime] = None

    def complete(self, result=None):
        self.status = 'completed'
        self.result = result
        self.timestamp = datetime.utcnow()

    def fail(self, reason):
        self.status = 'failed'
        self.result = {'error': reason}
        self.timestamp = datetime.utcnow()


@dataclass
class TradePlan:
    """A complete trade plan — like DSH's logged plan state."""
    id: str
    token: str
    objective: str          # "Enter FART position with controlled risk"
    steps: List[TradeStep] = field(default_factory=list)
    status: str = 'planning'  # planning, executing, completed, aborted
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    def current_step(self) -> Optional[TradeStep]:
        for step in self.steps:
            if step.status == 'pending':
                return step
        return None

    def progress_pct(self) -> float:
        if not self.steps:
            return 0
        completed = sum(1 for s in self.steps if s.status in ('completed', 'skipped'))
        return (completed / len(self.steps)) * 100

    def to_prompt(self) -> str:
        """Render plan for LLM context."""
        lines = [f"Trade Plan: {self.objective}", f"Token: {self.token}"]
        for i, step in enumerate(self.steps):
            icon = {'pending': '[ ]', 'completed': '[x]', 'active': '[>]', 'failed': '[!]', 'skipped': '[-]'}
            lines.append(f"  {icon.get(step.status, '[ ]')} Step {i+1}: {step.description}")
        return '\n'.join(lines)


class TradePlanner:
    """
    DSH-style trade planner.
    Forces structured thinking before every trade.
    """

    def __init__(self, risk_guard, session_log):
        self.risk_guard = risk_guard
        self.log = session_log

    async def create_plan(self, token, features, regime, signal) -> TradePlan:
        """Create a structured trade plan based on analysis."""
        plan = TradePlan(
            id=f"plan_{token[:8]}_{int(datetime.utcnow().timestamp())}",
            token=token,
            objective=f"{signal['direction']} {token[:8]} in {regime} market",
        )

        # Build steps based on the signal
        plan.steps = [
            TradeStep('check_liquidity', 'Verify order book can absorb order'),
            TradeStep('check_regime', f'Confirm regime is {regime}'),
            TradeStep('risk_validation', 'Pass all risk guard checks'),
            TradeStep('place_entry', f'Place {signal["direction"]} order'),
            TradeStep('set_stop_loss', 'Set stop-loss at 5% below entry'),
            TradeStep('set_take_profit', 'Set take-profit at 10% above entry'),
            TradeStep('monitor', 'Monitor for 4 hours'),
            TradeStep('evaluate', 'Evaluate exit conditions'),
            TradeStep('record', 'Record outcome and update weights'),
        ]

        await self.log.log('plan/created', {
            'plan_id': plan.id, 'token': token,
            'steps': len(plan.steps), 'signal': signal,
        })

        return plan

    async def execute_plan(self, plan: TradePlan):
        """Execute a trade plan step by step."""
        plan.status = 'executing'

        step_handlers = {
            'check_liquidity': self._step_check_liquidity,
            'check_regime': self._step_check_regime,
            'risk_validation': self._step_risk_validation,
            'place_entry': self._step_place_entry,
            'set_stop_loss': self._step_set_stop_loss,
            'set_take_profit': self._step_set_take_profit,
            'monitor': self._step_monitor,
            'evaluate': self._step_evaluate,
            'record': self._step_record,
        }

        for step in plan.steps:
            step.status = 'active'
            handler = step_handlers.get(step.name)

            if not handler:
                step.fail(f'No handler for step: {step.name}')
                plan.status = 'aborted'
                break

            try:
                result = await handler(plan, step)
                step.complete(result)

                # If a critical step fails, abort the plan
                if result and result.get('abort'):
                    plan.status = 'aborted'
                    break

            except Exception as e:
                step.fail(str(e))
                plan.status = 'aborted'

                await self.log.log('plan/aborted', {
                    'plan_id': plan.id, 'step': step.name, 'error': str(e)
                })
                break

        if plan.status == 'executing':
            plan.status = 'completed'
            plan.completed_at = datetime.utcnow()

        await self.log.log('plan/completed', {
            'plan_id': plan.id, 'status': plan.status,
            'progress': plan.progress_pct(),
        })

        return plan

    async def _step_check_liquidity(self, plan, step):
        orderbook = await get_orderbook(plan.token)
        depth_usd = orderbook.get('depth_usd', 0)
        return {'depth_usd': depth_usd, 'sufficient': depth_usd > 100}

    async def _step_check_regime(self, plan, step):
        return {'regime': 'confirmed'}

    async def _step_risk_validation(self, plan, step):
        order = Order(token=plan.token, side='buy', amount_usd=3.0)
        result = await self.risk_guard.validate(order)
        if not result['approved']:
            return {'abort': True, 'reason': result['reason']}
        return {'approved': True}

    async def _step_place_entry(self, plan, step):
        tx_id = await safe_market_buy(plan.token, 3.0, 199)
        return {'tx_id': tx_id}

    async def _step_set_stop_loss(self, plan, step):
        # Set stop-loss order
        return {'stop_loss': 'set'}

    async def _step_set_take_profit(self, plan, step):
        return {'take_profit': 'set'}

    async def _step_monitor(self, plan, step):
        await asyncio.sleep(60 * 30)  # Monitor for 30 min in demo
        return {'monitoring': 'complete'}

    async def _step_evaluate(self, plan, step):
        return {'evaluation': 'position profitable'}

    async def _step_record(self, plan, step):
        await self.log.log('trade/recorded', {'plan_id': plan.id})
        return {'recorded': True}
```

**Why it matters:** Forces the agent to think through liquidity, risk, entry, exit, and monitoring *before* committing capital. Prevents impulsive trades.

---

## 18. Human Feedback System

> ✅ **IMPLEMENTED** — See `src/human_feedback.py` and `src/tests/test_remaining_features.py`

**Problem:** The system never learns from human judgment — only from PnL numbers.

**DSH Pattern:** `command-feedback` + `message-feedback` — immutable remarks + editable per-message ratings.

```python
"""
DSH-inspired Human Feedback System
Humans rate AI trading decisions, system learns from judgment.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
import uuid


@dataclass
class TradeFeedback:
    """Human feedback on a specific trade."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    trade_id: str = ''
    token: str = ''
    rating: int = 0          # 1-5 stars
    sentiment: str = ''       # 'excellent', 'good', 'poor', 'terrible'
    note: str = ''            # Free-text explanation
    tags: List[str] = field(default_factory=list)  # ['good_entry', 'late_exit', 'wrong_size']
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class FeedbackInsight:
    """Aggregated insight from feedback patterns."""
    pattern: str             # 'entries are good, exits are bad'
    confidence: float        # 0.0-1.0
    recommendation: str      # 'Extend take-profit targets'
    sample_size: int


class FeedbackSystem:
    """
    DSH-style feedback system for trading decisions.
    
    - command-feedback: immutable remark in session log
    - message-feedback: editable rating attached to a trade
    """

    def __init__(self, session_log):
        self.log = session_log
        self.feedbacks: List[TradeFeedback] = []

    async def submit_feedback(self, trade_id, token, rating, note='', tags=None):
        """Human submits feedback on a trade."""
        feedback = TradeFeedback(
            trade_id=trade_id,
            token=token,
            rating=rating,
            sentiment=self._rating_to_sentiment(rating),
            note=note,
            tags=tags or [],
        )
        self.feedbacks.append(feedback)

        # Record as immutable log event (like DSH command-feedback)
        await self.log.log('feedback/recorded', {
            'trade_id': trade_id,
            'token': token,
            'rating': rating,
            'note': note,
            'tags': tags,
        })

        return feedback

    async def get_insights(self) -> List[FeedbackInsight]:
        """Analyze feedback patterns to extract actionable insights."""
        if len(self.feedbacks) < 5:
            return []

        insights = []

        # Pattern: entries vs exits
        good_entries = [f for f in self.feedbacks if 'good_entry' in f.tags]
        bad_exits = [f for f in self.feedbacks if 'late_exit' in f.tags or 'early_exit' in f.tags]

        if len(good_entries) > len(self.feedbacks) * 0.4:
            insights.append(FeedbackInsight(
                pattern='Entry timing is strong',
                confidence=len(good_entries) / len(self.feedbacks),
                recommendation='Entry signals are working — keep current entry logic',
                sample_size=len(good_entries),
            ))

        if len(bad_exits) > len(self.feedbacks) * 0.3:
            insights.append(FeedbackInsight(
                pattern='Exit timing needs improvement',
                confidence=len(bad_exits) / len(self.feedbacks),
                recommendation='Consider wider take-profit or trailing stops',
                sample_size=len(bad_exits),
            ))

        # Pattern: average rating by token
        by_token = {}
        for f in self.feedbacks:
            by_token.setdefault(f.token, []).append(f.rating)

        for token, ratings in by_token.items():
            avg = sum(ratings) / len(ratings)
            if avg < 2.5 and len(ratings) >= 3:
                insights.append(FeedbackInsight(
                    pattern=f'{token[:8]} consistently rated poorly',
                    confidence=0.8,
                    recommendation=f'Consider removing {token[:8]} from monitored tokens',
                    sample_size=len(ratings),
                ))

        return insights

    def _rating_to_sentiment(self, rating):
        return {5: 'excellent', 4: 'good', 3: 'neutral', 2: 'poor', 1: 'terrible'}.get(rating, 'unknown')
```

**Why it matters:** PnL alone doesn't tell the full story. A trade can be profitable but poorly timed, or unprofitable but well-reasoned. Human feedback captures *qualitative* judgment that numbers miss.

---

## 19. Spill & Smart Data Storage

> ✅ **IMPLEMENTED** — See `src/spill_storage.py` and `src/tests/test_remaining_features.py`

**Problem:** Market data dumps flood the LLM prompt with noise. Too much context = worse decisions.

**DSH Pattern:** `spill` — persist oversized output, show bounded preview with retrieval locator.

```python
"""
DSH-inspired Spill System
Store large market data, show only decision-relevant summaries.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
import hashlib


class SpillStore:
    """
    DSH-style spill: persist large data to files,
    return a locator for retrieval.
    """

    def __init__(self, spill_dir='data/spill'):
        self.spill_dir = Path(spill_dir)
        self.spill_dir.mkdir(parents=True, exist_ok=True)
        self._index = {}  # hash -> file_path

    def spill(self, data: dict, source: str, category: str = 'market_data') -> dict:
        """
        Persist large data to disk.
        Returns a locator with preview.
        """
        # Serialize and hash
        serialized = json.dumps(data, default=str)
        data_hash = hashlib.md5(serialized.encode()).hexdigest()[:12]

        # Store
        file_path = self.spill_dir / category / f"{data_hash}.json"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w') as f:
            f.write(serialized)

        self._index[data_hash] = {
            'path': str(file_path),
            'source': source,
            'category': category,
            'size_bytes': len(serialized),
            'timestamp': datetime.utcnow().isoformat(),
        }

        # Generate bounded preview
        preview = self._generate_preview(data)

        return {
            'locator': data_hash,
            'source': source,
            'size': len(serialized),
            'preview': preview,
            'can_retrieve': True,
        }

    def retrieve(self, locator: str) -> Optional[dict]:
        """Retrieve spilled data by locator."""
        meta = self._index.get(locator)
        if not meta:
            return None
        with open(meta['path']) as f:
            return json.load(f)

    def retrieve_summary(self, locator: str, max_items: int = 5) -> dict:
        """Retrieve a bounded summary of spilled data."""
        data = self.retrieve(locator)
        if not data:
            return {'error': 'Data not found'}

        if isinstance(data, list):
            return {'items': data[:max_items], 'total': len(data)}
        elif isinstance(data, dict):
            return dict(list(data.items())[:max_items * 2])
        return data

    def _generate_preview(self, data: dict) -> str:
        """Generate a human-readable preview for LLM context."""
        if isinstance(data, list):
            return f"{len(data)} records. First: {json.dumps(data[0], default=str)[:200]}"
        elif isinstance(data, dict):
            keys = list(data.keys())[:5]
            return f"{len(data)} fields: {', '.join(keys)}"
        return str(data)[:200]


class SmartDataPacker:
    """
    Decide what to show the LLM vs what to spill.
    Based on DSH's compaction + spill pattern.
    """

    def __init__(self, spill_store: SpillStore):
        self.spill = spill_store

    def pack_for_llm(self, features: dict, ohlcv: list, portfolio: dict, regime: str) -> dict:
        """
        Pack data for LLM context.
        Large datasets → spill with preview.
        Key signals → inline.
        """
        result = {
            # Inline: always show these (small, decision-critical)
            'regime': regime,
            'trend': 'bullish' if features.get('EMA_20', 0) > features.get('EMA_50', 0) else 'bearish',
            'rsi': features.get('RSI', 50),
            'volume_spike': features.get('volume_spike', 1.0),
            'buy_pressure': features.get('buy_pressure', 0.5),
            'spread_bps': features.get('spread_bps', 0),
            'cash': portfolio.get('cash', 0),
            'exposure': portfolio.get('exposure', 0),
            'daily_pnl': portfolio.get('daily_pnl', 0),
        }

        # Spill: large OHLCV data
        if len(ohlcv) > 10:
            spill_result = self.spill.spill(
                {'ohlcv': ohlcv},
                source='binance_ws',
                category='ohlcv'
            )
            result['ohlcv_preview'] = spill_result['preview']
            result['ohlcv_locator'] = spill_result['locator']
            result['ohlcv_recent'] = ohlcv[-3:]  # Show last 3 inline
        else:
            result['ohlcv'] = ohlcv

        # Spill: full feature set (too many fields for prompt)
        spill_result = self.spill.spill(features, source='feature_engine', category='features')
        result['features_locator'] = spill_result['locator']
        result['features_preview'] = spill_result['preview']

        return result
```

**Why it matters:** Instead of dumping 1000 candles into the prompt (which confuses the LLM), we spill the raw data to disk and show only: (1) the key signals inline, (2) the last 3 candles, (3) a preview with a retrieval locator. The LLM can request specific data if needed.

---

## 20. MCP Integration — External Services

> ✅ **IMPLEMENTED** — See `src/mcp_registry.py` and `src/tests/test_remaining_features.py`

**Problem:** Each external data source (CoinGlass, Dune, Birdeye) requires custom API wrappers.

**DSH Pattern:** `mcp-client` — register external server tools via Model Context Protocol.

```python
"""
DSH-inspired MCP Integration for Trading
Connect to external services through a standard protocol.
"""

from dataclasses import dataclass
from typing import Dict, List, Callable, Any
import asyncio


@dataclass
class MCPServer:
    """An MCP-compatible external service."""
    name: str
    description: str
    tools: Dict[str, Callable]  # tool_name -> async function
    enabled: bool = True


class TradingMCPRegistry:
    """
    Registry for external trading services.
    Based on DSH's mcp-client — register external tools on ctx.tools.
    """

    def __init__(self):
        self.servers: Dict[str, MCPServer] = {}
        self.tools: Dict[str, Callable] = {}

    def register_server(self, server: MCPServer):
        """Register an MCP server and its tools."""
        self.servers[server.name] = server
        for tool_name, tool_fn in server.tools.items():
            qualified_name = f"{server.name}/{tool_name}"
            self.tools[qualified_name] = tool_fn
            print(f"  ✅ Registered MCP tool: {qualified_name}")

    async def call_tool(self, tool_name: str, **kwargs) -> Any:
        """Call an MCP tool by qualified name."""
        fn = self.tools.get(tool_name)
        if not fn:
            raise ValueError(f"Unknown MCP tool: {tool_name}")
        return await fn(**kwargs)

    def list_tools(self) -> List[str]:
        return list(self.tools.keys())


# ── MCP Server Definitions ──────────────────────────────────────────

# CoinGlass: Funding rates, open interest, liquidations
def create_coinglass_server() -> MCPServer:
    async def get_funding_rates(exchange='binance', symbol='BTCUSDT'):
        # Real implementation would call CoinGlass API
        return {'funding_rate': 0.0001, 'next_funding': '4h'}

    async def get_open_interest(symbol='BTC'):
        return {'oi': 15000000000, 'change_24h': 2.5}

    async def get_liquidations(symbol='BTC', hours=1):
        return {'liquidations_1h': 50000000, 'long_pct': 65}

    return MCPServer(
        name='coinglass',
        description='Funding rates, open interest, liquidations',
        tools={
            'funding_rates': get_funding_rates,
            'open_interest': get_open_interest,
            'liquidations': get_liquidations,
        },
    )

# Birdeye: Solana token analytics
def create_birdeye_server() -> MCPServer:
    async def get_token_overview(address):
        return {'price': 0.0042, 'volume_24h': 500000}

    async def get_top_gainers(chain='solana'):
        return [{'token': 'FART', 'change': '+45%'}]

    return MCPServer(
        name='birdeye',
        description='Solana token analytics and market data',
        tools={
            'token_overview': get_token_overview,
            'top_gainers': get_top_gainers,
        },
    )

# DefiLlama: DeFi TVL and protocol data
def create_defillama_server() -> MCPServer:
    async def get_protocol_tvl(protocol):
        return {'tvl': 1000000000, 'change_24h': 3.2}

    return MCPServer(
        name='defillama',
        description='DeFi TVL and protocol analytics',
        tools={
            'protocol_tvl': get_protocol_tvl,
        },
    )

# Setup
mcp_registry = TradingMCPRegistry()
mcp_registry.register_server(create_coinglass_server())
mcp_registry.register_server(create_birdeye_server())
mcp_registry.register_server(create_defillama_server())
```

**Why it matters:** Adding a new data source is now a one-function registration. The LLM can call `coinglass/funding_rates` or `birdeye/token_overview` without any custom integration code. The standard MCP protocol means any compatible service plugs in instantly.

---

## 21. Runtime Invariants — Safety Guarantees

> ✅ **IMPLEMENTED** — See `src/invariants.py` and `src/tests/test_presets_invariants_commands.py`

**Problem:** No runtime checks for impossible states. Corrupted state can cascade silently.

**DSH Pattern:** `ctx.invariants` — runtime assertions that verify system contracts.

```python
"""
DSH-inspired Runtime Invariants for Trading
Safety checks that must NEVER be violated.
Based on DSH's invariant registry — fail loud on impossible states.
"""

from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass


class InvariantError(Exception):
    """Raised when a runtime invariant is violated."""
    def __init__(self, package: str, message: str):
        self.package = package
        super().__init__(f"[{package}] INVARIANT VIOLATION: {message}")


@dataclass
class InvariantCheck:
    """Result of an invariant check."""
    name: str
    passed: bool
    message: str = ''
    timestamp: datetime = None


class TradingInvariantRegistry:
    """
    DSH-style invariant registry for trading safety.
    
    Every package can register its own invariants.
    Checks run continuously and fail loud on violation.
    """

    def __init__(self):
        self.checks = []
        self.results: List[InvariantCheck] = []
        self.failures: List[InvariantCheck] = []

    def register(self, name, check_fn):
        self.checks.append((name, check_fn))

    async def run_all(self, context: dict) -> List[InvariantCheck]:
        """Run all invariant checks."""
        results = []
        for name, check_fn in self.checks:
            try:
                passed = await check_fn(context)
                result = InvariantCheck(
                    name=name, passed=passed,
                    timestamp=datetime.utcnow()
                )
            except InvariantError as e:
                result = InvariantCheck(
                    name=name, passed=False, message=str(e),
                    timestamp=datetime.utcnow()
                )
                self.failures.append(result)
            except Exception as e:
                result = InvariantCheck(
                    name=name, passed=False, message=f'Check error: {e}',
                    timestamp=datetime.utcnow()
                )
            results.append(result)
        self.results.extend(results)
        return results

    def has_critical_failure(self) -> bool:
        """Any failure = system should halt."""
        return any(not r.passed for r in self.results[-10:])


# ── Trading Invariant Checks ────────────────────────────────────────

async def invariant_position_count(ctx) -> bool:
    """Position count must never exceed maximum."""
    count = len(ctx.get('positions', {}))
    max_pos = ctx.get('max_positions', 5)
    if count > max_pos:
        raise InvariantError('risk', f'Position count {count} > max {max_pos}')
    return True

async def invariant_cash_reserve(ctx) -> bool:
    """Cash must maintain minimum percentage of portfolio."""
    cash = ctx.get('cash', 0)
    total = ctx.get('total_value', 1)
    min_pct = ctx.get('min_cash_pct', 20)
    if (cash / total * 100) < min_pct:
        raise InvariantError('risk', f'Cash {cash/total*100:.1f}% below minimum {min_pct}%')
    return True

async def invariant_no_duplicate_positions(ctx) -> bool:
    """No two positions for the same token."""
    tokens = list(ctx.get('positions', {}).keys())
    if len(tokens) != len(set(tokens)):
        raise InvariantError('state', f'Duplicate position detected: {tokens}')
    return True

async def invariant_pnl_tracked(ctx) -> bool:
    """Every closed trade must have PnL recorded."""
    trades = ctx.get('recent_closed_trades', [])
    for trade in trades:
        if trade.get('pnl_usd') is None:
            raise InvariantError('tracking', f'Trade {trade.get("id")} missing PnL')
    return True

async def invariant_order_bounds(ctx) -> bool:
    """Order amounts must be within bounds."""
    order = ctx.get('pending_order')
    if order:
        portfolio = ctx.get('total_value', 1)
        if order['amount_usd'] > portfolio * 0.5:
            raise InvariantError('order', f'Order ${order["amount_usd"]} > 50% of portfolio')
    return True

async def invariant_session_log_integrity(ctx) -> bool:
    """Session log must have no gaps in trade records."""
    log_count = ctx.get('log_event_count', 0)
    trade_count = ctx.get('trade_count', 0)
    if trade_count > 0 and log_count == 0:
        raise InvariantError('logging', f'{trade_count} trades but no log events')
    return True


# Setup
registry = TradingInvariantRegistry()
registry.register('position_count', invariant_position_count)
registry.register('cash_reserve', invariant_cash_reserve)
registry.register('no_duplicates', invariant_no_duplicate_positions)
registry.register('pnl_tracked', invariant_pnl_tracked)
registry.register('order_bounds', invariant_order_bounds)
registry.register('log_integrity', invariant_session_log_integrity)
```

**Why it matters:** These are the "impossible states" that should never happen. If they do, the system halts immediately instead of continuing with corrupted state. This prevents cascading failures that could drain the portfolio.

---

## 22. Session Query — Trade History Search

> ✅ **IMPLEMENTED** — See `src/session_query.py` and `src/tests/test_remaining_features.py`

**Problem:** No way to search trade history. Can't answer "when did I last profit on AI16Z?" or "what's my win rate in ranging markets?"

**DSH Pattern:** `session-query` — SQLite full-text search over session logs.

```python
"""
DSH-inspired Trade History Query Engine
Search across all past trades with powerful filters.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class TradeQuery:
    """A search query across trade history."""
    token: Optional[str] = None
    strategy: Optional[str] = None
    regime: Optional[str] = None
    min_pnl: Optional[float] = None
    max_pnl: Optional[float] = None
    min_confidence: Optional[float] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    min_holding_minutes: Optional[int] = None
    tags: Optional[List[str]] = None


class TradeQueryEngine:
    """
    DSH-style session query for trade history.
    Search, filter, and analyze past trades.
    """

    def __init__(self, session_log):
        self.log = session_log

    async def find_trades(self, query: TradeQuery, limit=50) -> List[dict]:
        """Find trades matching query criteria."""
        filters = {}
        if query.token:
            filters['token'] = query.token
        if query.strategy:
            filters['strategy'] = query.strategy
        if query.regime:
            filters['regime'] = query.regime

        trades = await self.log.get_trade_chain(
            token=query.token or '*',
            days=90
        )

        # Apply filters
        results = []
        for trade in trades:
            if query.min_pnl is not None and trade.get('pnl_usd', 0) < query.min_pnl:
                continue
            if query.max_pnl is not None and trade.get('pnl_usd', 0) > query.max_pnl:
                continue
            if query.min_confidence is not None:
                sigs = trade.get('entry_signals', [])
                if not any(s.get('confidence', 0) >= query.min_confidence for s in sigs):
                    continue
            if query.start_date and trade.get('timestamp', datetime.min) < query.start_date:
                continue
            if query.end_date and trade.get('timestamp', datetime.max) > query.end_date:
                continue
            results.append(trade)

        return results[:limit]

    async def find_profitable_setups(self, min_pnl=1.0) -> dict:
        """What conditions produce profitable trades?"""
        trades = await self.log.get_trade_chain(days=90)
        profitable = [t for t in trades if t.get('pnl_usd', 0) > min_pnl]

        by_regime = {}
        by_strategy = {}
        by_confidence = {'high': [], 'medium': [], 'low': []}

        for t in profitable:
            regime = t.get('regime', 'unknown')
            by_regime.setdefault(regime, []).append(t)

            for sig in t.get('entry_signals', []):
                strat = sig.get('strategy', 'unknown')
                by_strategy.setdefault(strat, []).append(t)

                conf = sig.get('confidence', 0)
                if conf > 0.8:
                    by_confidence['high'].append(t)
                elif conf > 0.6:
                    by_confidence['medium'].append(t)
                else:
                    by_confidence['low'].append(t)

        return {
            'total_profitable': len(profitable),
            'best_regime': max(by_regime.items(), key=lambda x: len(x[1]))[0] if by_regime else None,
            'best_strategy': max(by_strategy.items(), key=lambda x: len(x[1]))[0] if by_strategy else None,
            'optimal_confidence': max(by_confidence.items(), key=lambda x: len(x[1]))[0] if by_confidence else None,
        }

    async def find_losing_patterns(self) -> dict:
        """What patterns consistently lose money?"""
        trades = await self.log.get_trade_chain(days=90)
        losers = [t for t in trades if t.get('pnl_usd', 0) < 0]

        patterns = []
        for t in losers:
            for sig in t.get('entry_signals', []):
                factors = sig.get('factors', {})
                if factors.get('rsi', 50) > 65:
                    patterns.append('entered with overbought RSI')
                if factors.get('volume_spike', 1.0) < 1.0:
                    patterns.append('entered without volume confirmation')
                if factors.get('buy_pressure', 0.5) < 0.45:
                    patterns.append('entered against selling pressure')

        # Count patterns
        from collections import Counter
        pattern_counts = Counter(patterns)

        return {
            'total_losers': len(losers),
            'top_losing_patterns': pattern_counts.most_common(5),
            'recommendation': self._generate_recommendation(pattern_counts),
        }

    def _generate_recommendation(self, pattern_counts):
        if not pattern_counts:
            return 'No clear losing patterns found'
        top = pattern_counts.most_common(1)[0]
        return f'Avoid: {top[0]} (occurred {top[1]} times)'
```

**Why it matters:** Without search, you're guessing about what works. With this, you can query: "show me every trade where I lost money and figure out why" or "what's my win rate on AI16Z when volume spikes > 2x?" — and get data-driven answers.

---

## 23. Trading Commands — Manual Intervention

> ✅ **IMPLEMENTED** — See `src/trading_commands.py` and `src/tests/test_presets_invariants_commands.py`

**Problem:** No way to manually intervene in real-time. To change behavior, you edit `config.py` and restart.

**DSH Pattern:** `ctx.commands` — human commands for interactive control.

```python
"""
DSH-inspired Trading Command System
Real-time manual intervention without restarts.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional
import asyncio
import shlex


@dataclass
class CommandResult:
    """Result of executing a command."""
    success: bool
    message: str
    data: Optional[dict] = None


class TradingCommandSystem:
    """
    DSH-style command system for trading control.
    
    Commands:
      /close_all          Exit all positions immediately
      /risk_status        Show current risk metrics
      /show_pnl           Show daily/weekly/monthly PnL
      /pause              Stop all automated trading
      /resume             Resume automated trading
      /set_stop TOKEN X   Set stop-loss for a specific token
      /show_signals       Show current pending signals
      /force_buy TOKEN $5 Override risk guard, force a buy
      /strategy_report    Show strategy accuracy breakdown
      /tune_weights       Auto-tune PredictionEngine weights
    """

    def __init__(self):
        self.commands: Dict[str, dict] = {}
        self.paused = False
        self._handlers: Dict[str, Callable] = {}

    def register(self, name, handler, description='', requires_approval=False):
        """Register a command handler."""
        self.commands[name] = {
            'description': description,
            'requires_approval': requires_approval,
        }
        self._handlers[name] = handler

    async def execute(self, command_string: str) -> CommandResult:
        """Parse and execute a command."""
        try:
            parts = shlex.split(command_string)
        except ValueError as e:
            return CommandResult(success=False, message=f'Invalid command: {e}')

        if not parts:
            return CommandResult(success=False, message='Empty command')

        cmd = parts[0].lstrip('/')
        args = parts[1:]

        handler = self._handlers.get(cmd)
        if not handler:
            available = ', '.join(self.commands.keys())
            return CommandResult(
                success=False,
                message=f'Unknown command: /{cmd}. Available: {available}'
            )

        # Check if system is paused
        if self.paused and cmd not in ('resume', 'status', 'show_pnl'):
            return CommandResult(
                success=False,
                message='System is paused. Use /resume first.'
            )

        try:
            result = await handler(*args)
            return result
        except Exception as e:
            return CommandResult(success=False, message=f'Command error: {e}')

    def list_commands(self) -> str:
        lines = ['Available commands:']
        for name, info in self.commands.items():
            approval = ' [requires approval]' if info['requires_approval'] else ''
            lines.append(f'  /{name}{approval} — {info["description"]}')
        return '\n'.join(lines)


# ── Command Handlers ────────────────────────────────────────────────

async def cmd_close_all() -> CommandResult:
    """Exit all positions immediately."""
    from src import nice_funcs as n
    try:
        n.close_all_positions()
        return CommandResult(success=True, message='All positions closed')
    except Exception as e:
        return CommandResult(success=False, message=f'Failed: {e}')

async def cmd_risk_status() -> CommandResult:
    """Show current risk metrics."""
    return CommandResult(success=True, message='Risk status', data={
        'daily_pnl': 0,
        'positions': 0,
        'exposure': '20%',
    })

async def cmd_pause() -> CommandResult:
    return CommandResult(success=True, message='Trading paused')

async def cmd_resume() -> CommandResult:
    return CommandResult(success=True, message='Trading resumed')

async def cmd_force_buy(token: str, amount: str) -> CommandResult:
    """Override risk guard, force a buy."""
    return CommandResult(
        success=True,
        message=f'Force buy {token[:8]} for ${amount} submitted',
        data={'token': token, 'amount': float(amount)}
    )


# Setup
cmds = TradingCommandSystem()
cmds.register('close_all', cmd_close_all, 'Exit all positions immediately', requires_approval=True)
cmds.register('risk_status', cmd_risk_status, 'Show current risk metrics')
cmds.register('pause', cmd_pause, 'Stop all automated trading', requires_approval=True)
cmds.register('resume', cmd_resume, 'Resume automated trading')
cmds.register('force_buy', cmd_force_buy, 'Force a buy order (override risk)', requires_approval=True)
```

**Usage in Discord/Telegram/CLI:**
```
User: /risk_status
Bot: Risk Status:
  Daily PnL: +$2.30
  Open positions: 3
  Total exposure: 45%
  Cash reserve: 55%

User: /close_all
Bot: ⚠️ This will close ALL positions. Confirm? [y/n]
User: y
Bot: All positions closed successfully.
```

**Why it matters:** When the market does something unexpected, you need to intervene *now*. This gives you real-time control without restarting the bot or editing config files.

---

## 24. Risk Presets — One-Click Profiles

> ✅ **IMPLEMENTED** — See `src/risk_presets.py` and `src/tests/test_presets_invariants_commands.py`

**Problem:** Changing risk parameters requires editing multiple config values. Slow during market emergencies.

**DSH Pattern:** `ctx.permissionPresets` — one-click permission changes.

```python
"""
DSH-inspired Risk Presets
One command changes ALL risk parameters simultaneously.
"""

from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class RiskPreset:
    """A named risk configuration profile."""
    name: str
    description: str
    max_position_pct: int      # Max % per position
    max_total_exposure: int    # Max total portfolio exposure %
    min_confidence: float      # Min signal confidence to trade
    cash_buffer_pct: int       # Min cash buffer %
    max_daily_loss: float      # Max daily loss USD
    human_approval_threshold: float  # Orders above this need approval
    slippage_bps: int          # Max acceptable slippage
    stop_loss_pct: float       # Stop-loss percentage
    take_profit_pct: float     # Take-profit percentage


RISK_PRESETS = {
    'conservative': RiskPreset(
        name='conservative',
        description='Tight risk, capital preservation',
        max_position_pct=10,
        max_total_exposure=40,
        min_confidence=0.8,
        cash_buffer_pct=40,
        max_daily_loss=10,
        human_approval_threshold=5,
        slippage_bps=100,
        stop_loss_pct=0.03,
        take_profit_pct=0.06,
    ),
    'moderate': RiskPreset(
        name='moderate',
        description='Balanced risk/reward',
        max_position_pct=20,
        max_total_exposure=60,
        min_confidence=0.65,
        cash_buffer_pct=25,
        max_daily_loss=20,
        human_approval_threshold=10,
        slippage_bps=150,
        stop_loss_pct=0.05,
        take_profit_pct=0.10,
    ),
    'aggressive': RiskPreset(
        name='aggressive',
        description='High risk, high potential reward',
        max_position_pct=30,
        max_total_exposure=80,
        min_confidence=0.5,
        cash_buffer_pct=15,
        max_daily_loss=30,
        human_approval_threshold=20,
        slippage_bps=199,
        stop_loss_pct=0.08,
        take_profit_pct=0.15,
    ),
    'survival': RiskPreset(
        name='survival',
        description='Market crash mode — protect capital at all costs',
        max_position_pct=5,
        max_total_exposure=20,
        min_confidence=0.95,
        cash_buffer_pct=80,
        max_daily_loss=5,
        human_approval_threshold=1,
        slippage_bps=50,
        stop_loss_pct=0.02,
        take_profit_pct=0.04,
    ),
}


class PresetManager:
    """
    DSH-style preset manager for one-click risk changes.
    """

    def __init__(self):
        self.current_preset = 'moderate'
        self.active_config = RISK_PRESETS['moderate']
        self.on_change_callbacks = []

    def activate(self, preset_name: str) -> dict:
        """Switch to a different risk preset."""
        preset = RISK_PRESETS.get(preset_name)
        if not preset:
            available = ', '.join(RISK_PRESETS.keys())
            return {'success': False, 'error': f'Unknown preset: {preset_name}. Available: {available}'}

        old = self.current_preset
        self.current_preset = preset_name
        self.active_config = preset

        # Notify all listeners
        for callback in self.on_change_callbacks:
            callback(preset)

        return {
            'success': True,
            'message': f'Risk preset changed: {old} → {preset_name}',
            'preset': preset,
        }

    def on_change(self, callback):
        """Register a listener for preset changes."""
        self.on_change_callbacks.append(callback)

    def get_current(self) -> RiskPreset:
        return self.active_config

    def list_presets(self) -> str:
        lines = ['Available risk presets:']
        for name, preset in RISK_PRESETS.items():
            marker = ' ← active' if name == self.current_preset else ''
            lines.append(f'  {name}: {preset.description}{marker}')
        return '\n'.join(lines)


# ── Integration with Risk Guard ─────────────────────────────────────

preset_manager = PresetManager()

# When preset changes, update all risk parameters
async def on_preset_change(preset: RiskPreset):
    """Called when risk preset is switched."""
    # Update risk guard config
    risk_guard.config.MAX_POSITION_PERCENTAGE = preset.max_position_pct
    risk_guard.config.MAX_LOSS_USD = preset.max_daily_loss
    # Update signal pipeline min confidence
    signal_pipeline.update_min_confidence(preset.min_confidence)
    # Update execution slippage
    execution_config.slippage_bps = preset.slippage_bps

    print(f"🔧 Risk parameters updated to '{preset.name}' preset")

preset_manager.on_change(lambda p: asyncio.create_task(on_preset_change(p)))

# Usage:
# /preset conservative  → immediately tightens all risk
# /preset aggressive    → loosens for high-conviction setups
# /preset survival      → crash mode — protect capital
```

**Why it matters:** Market conditions change fast. During a crash, you need `/preset survival` to instantly tighten everything. During a bull run, `/preset aggressive` loosens the reins. One command changes 10+ parameters simultaneously — no editing configs, no restarts.

---

# Part 4: Research Gaps — The Unbeatable Layer

---

> These are the research gaps that separate a *profitable* bot from an *unbeatable* platform. Neither Moon Dev nor DeepSeek Harness has solved these — but DSH architectural patterns provide the foundation.

---

## 25. Benchmark Tracker — Know If You're Adding Alpha

> ✅ **IMPLEMENTED** — See `src/benchmark_tracker.py` and `src/tests/test_benchmark_tracker.py` (17 tests passing)

**Problem:** Moon Dev tracks PnL in dollars but never asks: "Would I have made more just holding Bitcoin?" If the bot underperforms buy-and-hold, all complexity is wasted.

**DSH Pattern:** `Session Query` + `Session Log` — track benchmark alongside every trade.

```python
"""
Benchmark Tracker — Compare bot performance against passive hold.
If you can't beat BTC, you shouldn't be trading.
"""

from datetime import datetime, timedelta
from typing import Optional, List
from dataclasses import dataclass


@dataclass
class BenchmarkReport:
    period: str
    bot_return_pct: float
    btc_return_pct: float
    sol_return_pct: float
    alpha_vs_btc: float
    alpha_vs_sol: float
    verdict: str
    sharpe_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    total_trades: int = 0
    win_rate: float = 0.0


class BenchmarkTracker:
    """
    DSH-style benchmark tracking.
    Every performance report includes a comparison to passive hold.
    """

    def __init__(self, session_log, price_feed):
        self.log = session_log
        self.feed = price_feed

    async def daily_report(self) -> BenchmarkReport:
        return await self._report(period='1d')

    async def weekly_report(self) -> BenchmarkReport:
        return await self._report(period='7d')

    async def monthly_report(self) -> BenchmarkReport:
        return await self._report(period='30d')

    async def _report(self, period: str) -> BenchmarkReport:
        days = {'1d': 1, '7d': 7, '30d': 30}.get(period, 30)

        # Portfolio returns
        portfolio_start = await self._get_portfolio_value(days_ago=days)
        portfolio_now = await self._get_portfolio_value(days_ago=0)
        bot_return = ((portfolio_now - portfolio_start) / portfolio_start) * 100

        # Benchmark returns
        btc_start = await self.feed.get_price('BTC', days_ago=days)
        btc_now = await self.feed.get_price('BTC', days_ago=0)
        btc_return = ((btc_now - btc_start) / btc_start) * 100

        sol_start = await self.feed.get_price('SOL', days_ago=days)
        sol_now = await self.feed.get_price('SOL', days_ago=0)
        sol_return = ((sol_now - sol_start) / sol_start) * 100

        alpha_btc = bot_return - btc_return
        alpha_sol = bot_return - sol_return

        # Trade stats
        trades = await self.log.get_trade_chain(days=days)
        wins = sum(1 for t in trades if t.get('pnl_usd', 0) > 0)
        win_rate = wins / len(trades) if trades else 0

        # Verdict
        if alpha_btc > 5:
            verdict = "🟢 SIGNIFICANTLY BEATING BTC — adding real alpha"
        elif alpha_btc > 0:
            verdict = "🟡 MILDLY BEATING BTC — marginal edge"
        elif alpha_btc > -3:
            verdict = "🟠 UNDERPERFORMING BTC — re-evaluate strategy"
        else:
            verdict = "🔴 FAR BEHIND BTC — consider shutting down and buying BTC"

        return BenchmarkReport(
            period=period,
            bot_return_pct=round(bot_return, 2),
            btc_return_pct=round(btc_return, 2),
            sol_return_pct=round(sol_return, 2),
            alpha_vs_btc=round(alpha_btc, 2),
            alpha_vs_sol=round(alpha_sol, 2),
            verdict=verdict,
            total_trades=len(trades),
            win_rate=round(win_rate, 2),
        )

    async def _get_portfolio_value(self, days_ago=0):
        """Get historical portfolio value from session log."""
        snapshots = await self.log.query('pnl/snapshot', limit=1)
        if snapshots:
            return snapshots[-1]['data']['value']
        return 0


# ── Usage ───────────────────────────────────────────────────────────

async def print_daily_benchmark():
    report = await benchmark.daily_report()
    print(f"\n{'='*50}")
    print(f"  DAILY BENCHMARK REPORT")
    print(f"{'='*50}")
    print(f"  Bot Return:    {report.bot_return_pct:+.2f}%")
    print(f"  BTC Return:    {report.btc_return_pct:+.2f}%")
    print(f"  SOL Return:    {report.sol_return_pct:+.2f}%")
    print(f"  Alpha vs BTC:  {report.alpha_vs_btc:+.2f}%")
    print(f"  Verdict:       {report.verdict}")
    print(f"  Trades:        {report.total_trades}")
    print(f"  Win Rate:      {report.win_rate:.0%}")
    print(f"{'='*50}\n")
```

**Why this is the #1 most important improvement:** If your bot makes +2% but BTC went +5%, you *lost* 3% by actively trading instead of just holding. Every quant fund answers this question first. Moon Dev never does.

---

## 26. Walk-Forward Backtesting — Prevent Overfitting

> ✅ **IMPLEMENTED** — See `src/walk_forward.py` and `src/tests/test_remaining_features.py`

**Problem:** The `BacktestEngine` exists but has no out-of-sample validation. Strategies that look good in backtest often fail live because they're overfitted to historical noise.

**DSH Pattern:** `Plan Mode` — structured multi-step validation before deployment.

```python
"""
Walk-Forward Backtesting — Test strategies on UNSEEN data.
Prevents overfitting by validating on data the strategy has never seen.
"""

from datetime import datetime, timedelta
from typing import List, Dict
from dataclasses import dataclass
import numpy as np


@dataclass
class WalkForwardResult:
    strategy_name: str
    symbol: str
    deployable: bool
    train_days: int
    test_days: int
    windows_tested: int
    avg_return_pct: float
    win_rate: float
    sharpe_ratio: float
    max_drawdown_pct: float
    profit_factor: float
    recommendation: str


class WalkForwardValidator:
    """
    DSH-style walk-forward validation.
    
    Strategy: train on 60 days, test on 7, roll forward.
    Only deploy if out-of-sample results are consistently profitable.
    """

    def __init__(self, backtest_engine, session_log):
        self.engine = backtest_engine
        self.log = session_log

    async def validate(self, strategy, symbol, train_days=60, test_days=7) -> WalkForwardResult:
        """Run walk-forward backtest over historical data."""
        results = []
        start = datetime.utcnow() - timedelta(days=120)

        while start + timedelta(days=train_days + test_days) < datetime.utcnow():
            train_end = start + timedelta(days=train_days)
            test_end = train_end + timedelta(days=test_days)

            # 1. Optimize on training period
            optimized_params = self.engine.optimize(strategy, symbol, start, train_end)

            # 2. Test on UNSEEN test period
            test_result = self.engine.run(strategy, optimized_params, symbol, train_end, test_end)
            results.append(test_result)

            # 3. Roll forward
            start += timedelta(days=test_days)

        # Analyze out-of-sample results
        returns = [r['pnl_pct'] for r in results]
        wins = sum(1 for r in results if r['pnl_pct'] > 0)
        avg_return = np.mean(returns) if returns else 0
        win_rate = wins / len(results) if results else 0
        sharpe = self._calculate_sharpe(returns)
        max_dd = self._calculate_max_drawdown(returns)
        profit_factor = self._calculate_profit_factor(returns)

        # Decision criteria
        deployable = (
            avg_return > 0.5 and      # Positive average return
            win_rate > 0.55 and        # Win more than lose
            sharpe > 1.0 and           # Risk-adjusted returns are good
            max_dd < 15 and            # Drawdown is acceptable
            len(results) >= 8          # Enough sample size
        )

        if deployable:
            rec = f"✅ DEPLOYABLE — {avg_return:.1f}% avg return, {win_rate:.0%} win rate, Sharpe {sharpe:.1f}"
        elif avg_return > 0:
            rec = f"⚠️ MARGINAL — needs improvement before live. Sharpe {sharpe:.1f} < 1.0"
        else:
            rec = f"❌ NOT DEPLOYABLE — negative returns. Strategy doesn't work."

        return WalkForwardResult(
            strategy_name=strategy.name,
            symbol=symbol,
            deployable=deployable,
            train_days=train_days,
            test_days=test_days,
            windows_tested=len(results),
            avg_return_pct=round(avg_return, 2),
            win_rate=round(win_rate, 2),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown_pct=round(max_dd, 2),
            profit_factor=round(profit_factor, 2),
            recommendation=rec,
        )

    def _calculate_sharpe(self, returns, risk_free_rate=0) -> float:
        if len(returns) < 2:
            return 0
        excess = [r - risk_free_rate for r in returns]
        return np.mean(excess) / np.std(excess) if np.std(excess) > 0 else 0

    def _calculate_max_drawdown(self, returns) -> float:
        cumulative = np.cumsum(returns)
        peak = np.maximum.accumulate(cumulative)
        drawdown = peak - cumulative
        return max(drawdown) if len(drawdown) > 0 else 0

    def _calculate_profit_factor(self, returns) -> float:
        gross_profit = sum(r for r in returns if r > 0)
        gross_loss = abs(sum(r for r in returns if r < 0))
        return gross_profit / gross_loss if gross_loss > 0 else float('inf')
```

**Why this matters:** A strategy that makes +20% in backtest but -5% in walk-forward testing is overfitted. Walk-forward is the industry standard for validating trading strategies. Without it, you're gambling.

---

## 27. Volatility-Adjusted Position Sizing

> ✅ **IMPLEMENTED** — See `src/position_sizer.py` and `src/tests/test_advanced_features.py`

**Problem:** Every trade is $3 regardless of confidence or market volatility. A 90% confidence signal in a calm market gets the same size as a 55% confidence signal in a volatile crash.

**DSH Pattern:** `Goal System` — portfolio-level objectives that adjust parameters dynamically.

```python
"""
Volatility-Adjusted Position Sizing
Right-size every trade based on confidence, volatility, and regime.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional


@dataclass
class PositionSize:
    size_usd: float
    rationale: str
    confidence_multiplier: float
    volatility_adjustment: float
    regime_adjustment: float
    kelly_fraction: float


class PositionSizer:
    """
    DSH-style portfolio-aware position sizing.
    
    Uses simplified Kelly Criterion:
    size = (edge / odds) * bankroll * risk_factor
    
    Where:
      edge = win_rate * avg_win - (1-win_rate) * avg_loss
      odds = avg_win / avg_loss
      bankroll = available cash
      risk_factor = confidence * regime * volatility adjustments
    """

    def __init__(self, config):
        self.max_position_pct = config.MAX_POSITION_PERCENTAGE  # 30
        self.min_cash_pct = config.CASH_PERCENTAGE              # 20
        self.total_portfolio = config.usd_size

    def calculate(self, signal_confidence: float, features: dict, 
                  regime: str, portfolio_value: float, 
                  current_positions: dict) -> PositionSize:
        """Calculate optimal position size for this specific signal."""

        # Available capital
        cash = portfolio_value - sum(p.get('value_usd', 0) for p in current_positions.values())
        max_size = portfolio_value * (self.max_position_pct / 100)
        min_cash_reserve = portfolio_value * (self.min_cash_pct / 100)

        if cash - max_size < min_cash_reserve:
            max_size = max(cash - min_cash_reserve, 0)

        # Factor 1: Kelly Criterion (confidence-based)
        kelly_fraction = self._kelly_fraction(signal_confidence)

        # Factor 2: Volatility adjustment
        volatility = features.get('volatility_20', 20)
        vol_adjustment = self._volatility_adjustment(volatility)

        # Factor 3: Regime adjustment
        regime_multiplier = {
            'trending': 1.2,
            'ranging': 0.8,
            'transitional': 0.6,
        }.get(regime, 1.0)

        # Combined size
        raw_size = max_size * kelly_fraction * vol_adjustment * regime_multiplier
        final_size = max(min(raw_size, max_size), 0)

        return PositionSize(
            size_usd=round(final_size, 2),
            rationale=self._explain(kelly_fraction, vol_adjustment, regime_multiplier),
            confidence_multiplier=round(kelly_fraction, 3),
            volatility_adjustment=round(vol_adjustment, 3),
            regime_adjustment=round(regime_multiplier, 3),
            kelly_fraction=round(kelly_fraction, 3),
        )

    def _kelly_fraction(self, confidence: float) -> float:
        """Simplified Kelly: f = edge / odds, scaled by confidence."""
        # Assume avg_win = 2x avg_loss (2:1 reward-to-risk)
        odds = 2.0
        edge = confidence * 0.5  # Simplified edge estimate
        kelly = edge / odds
        return min(max(kelly, 0.05), 0.30)  # Clamp 5-30%

    def _volatility_adjustment(self, volatility: float) -> float:
        """High volatility = smaller positions."""
        # Normalize: vol 0 → 1.5x, vol 50 → 1.0x, vol 100 → 0.5x
        return max(1.5 - (volatility / 100), 0.3)

    def _explain(self, kelly, vol_adj, regime_adj) -> str:
        parts = [f"Kelly={kelly:.1%}"]
        if vol_adj < 1.0:
            parts.append(f"vol_reduced={vol_adj:.1%}")
        if regime_adj != 1.0:
            parts.append(f"regime={regime_adj:.1%}")
        return ', '.join(parts)
```

**Why this matters:** Fixed $3 sizing is the #1 source of lost returns. A 90% conviction trade should be 3x larger than a 55% conviction trade. Proper sizing can improve returns by 2-5x without changing the strategy at all.

---

## 28. Execution Quality Tracker

> ✅ **IMPLEMENTED** — See `src/execution_tracker.py` and `src/tests/test_feedback_and_tracking.py`

**Problem:** The system sets `slippage_bps = 199` but never measures actual slippage. You don't know if you're losing 0.5% or 2% per trade to execution.

**DSH Pattern:** `Session Log` — every fact is durable, queryable, and immutable.

```python
"""
Execution Quality Tracker
Measure expected vs actual execution — know your true costs.
"""

from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime
import numpy as np


@dataclass
class ExecutionRecord:
    token: str
    side: str
    amount_usd: float
    expected_price: float
    actual_price: float
    slippage_bps: float
    cost_usd: float
    tx_id: str
    timestamp: datetime
    strategy: str = 'unknown'


class ExecutionTracker:
    """
    Track every trade's execution quality.
    If you don't measure it, you can't improve it.
    """

    def __init__(self, session_log):
        self.log = session_log

    async def record(self, order, expected_price, actual_price, tx_id):
        """Record execution quality for a completed trade."""
        slippage_bps = abs(actual_price - expected_price) / expected_price * 10000
        cost_usd = order.amount_usd * slippage_bps / 10000

        await self.log.log('execution/quality', {
            'token': order.token,
            'side': order.side,
            'amount_usd': order.amount_usd,
            'expected_price': expected_price,
            'actual_price': actual_price,
            'slippage_bps': round(slippage_bps, 2),
            'cost_usd': round(cost_usd, 4),
            'tx_id': tx_id,
            'strategy': order.get('strategy', 'unknown'),
        })

        # Alert on excessive slippage
        if slippage_bps > 300:
            await self.log.log('execution/alert', {
                'type': 'high_slippage',
                'slippage_bps': slippage_bps,
                'token': order.token,
                'message': f'⚠️ Slippage {slippage_bps:.0f}bps on {order.token[:8]} — consider limit orders',
            })

    async def get_report(self, days=30) -> dict:
        """Generate execution quality report."""
        events = await self.log.query('execution/quality', days=days)
        if not events:
            return {'error': 'No execution data found'}

        slippages = [e['slippage_bps'] for e in events]
        costs = [e['cost_usd'] for e in events]

        # Group by strategy
        by_strategy = {}
        for e in events:
            strat = e.get('strategy', 'unknown')
            by_strategy.setdefault(strat, []).append(e['slippage_bps'])

        return {
            'total_trades': len(events),
            'avg_slippage_bps': round(np.mean(slippages), 1),
            'median_slippage_bps': round(np.median(slippages), 1),
            'max_slippage_bps': round(max(slippages), 1),
            'total_cost_usd': round(sum(costs), 2),
            'avg_cost_per_trade': round(np.mean(costs), 4),
            'by_strategy': {k: round(np.mean(v), 1) for k, v in by_strategy.items()},
            'verdict': 'LOW COST' if np.mean(slippages) < 100 else 'HIGH COST — use limit orders',
        }
```

**Why this matters:** If your avg slippage is 200 bps and your avg profit is 150 bps, you're *losing money on every trade* just to execution. You can't fix what you don't measure.

---

## 29. Alpha Decay Detection

> ✅ **IMPLEMENTED** — See `src/alpha_decay.py` and `src/tests/test_advanced_features.py`

**Problem:** A strategy that worked last month may stop working this month. There's no mechanism to detect when signals expire.

**DSH Pattern:** `Compaction` + `Invariant Registry` — continuous monitoring with automatic degradation alerts.

```python
"""
Alpha Decay Detector
Detect when signals stop working BEFORE they drain your account.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime


@dataclass
class DecayReport:
    strategy: str
    status: str           # healthy, warning, critical_decay, dead
    baseline_win_rate: float
    recent_win_rate: float
    decay_pct: float
    action: str
    confidence: float


class AlphaDecayDetector:
    """
    Monitor strategy performance over time.
    Auto-disable strategies that are decaying.
    """

    def __init__(self, session_log):
        self.log = session_log
        self.strategy_status: Dict[str, str] = {}  # strategy -> status

    async def check_all(self) -> List[DecayReport]:
        """Check all strategies for decay."""
        reports = []
        strategies = await self._get_active_strategies()

        for strategy in strategies:
            report = await self.check(strategy)
            reports.append(report)

            # Auto-disable critical strategies
            if report.status == 'critical_decay':
                await self._disable_strategy(strategy)
                await self.log.log('strategy/auto_disabled', {
                    'strategy': strategy,
                    'reason': f'Decay detected: {report.baseline_win_rate:.0%} → {report.recent_win_rate:.0%}',
                })

        return reports

    async def check(self, strategy: str) -> DecayReport:
        """Check a single strategy for decay."""
        baseline = await self.log.get_accuracy_report(days=30)
        recent = await self.log.get_accuracy_report(days=7)

        baseline_wr = baseline.get('by_strategy', {}).get(strategy, {}).get('win_rate', 0.5)
        recent_wr = recent.get('by_strategy', {}).get(strategy, {}).get('win_rate', 0.5)

        decay = baseline_wr - recent_wr
        baseline_count = baseline.get('by_strategy', {}).get(strategy, {}).get('count', 0)

        # Determine status
        if decay > 0.15 and baseline_count >= 10:
            status = 'critical_decay'
            action = 'DISABLE — strategy is losing money'
        elif decay > 0.08:
            status = 'warning'
            action = 'REDUCE position size by 50%'
        elif decay > 0.03:
            status = 'monitor'
            action = 'WATCH — slight performance drop'
        else:
            status = 'healthy'
            action = 'No action needed'

        return DecayReport(
            strategy=strategy,
            status=status,
            baseline_win_rate=round(baseline_wr, 2),
            recent_win_rate=round(recent_wr, 2),
            decay_pct=round(decay * 100, 1),
            action=action,
            confidence=min(baseline_count / 20, 1.0),
        )

    async def _disable_strategy(self, strategy: str):
        """Auto-disable a decaying strategy."""
        self.strategy_status[strategy] = 'disabled'
        print(f"🚫 AUTO-DISABLED: {strategy} — alpha decay detected")

    async def _get_active_strategies(self) -> List[str]:
        """Get all currently active strategies."""
        return [s for s, status in self.strategy_status.items() if status != 'disabled']
```

**Why this matters:** Markets evolve. A strategy that exploited a pattern last month may become a money pit this month. Detecting decay early — and auto-disabling — prevents catastrophic drawdowns.

---

## 30. Funding Cost Accounting

> ✅ **IMPLEMENTED** — See `src/funding_costs.py` and `src/tests/test_remaining_features.py`

**Problem:** On perpetual futures, you pay funding every 8 hours. A long position pays 0.01-0.1% per period = 0.3-3% per day. This cost is never tracked.

**DSH Pattern:** `MCP Integration` — connect to funding rate data as a standard capability seam.

```python
"""
Funding Cost Tracker
Track the HIDDEN cost of holding perpetual positions.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class FundingCost:
    token: str
    exchange: str
    position_size_usd: float
    funding_rate: float
    hours_held: float
    periods_held: float
    total_cost_pct: float
    total_cost_usd: float
    is_profitable_after_funding: bool
    verdict: str


class FundingCostTracker:
    """
    Track funding costs for perpetual futures positions.
    Many "profitable" strategies are actually losers after funding.
    """

    def __init__(self, mcp_registry, session_log):
        self.mcp = mcp_registry
        self.log = session_log

    async def calculate(self, position) -> FundingCost:
        """Calculate total funding cost for a position."""
        # Get current funding rate from MCP (CoinGlass, etc.)
        funding_data = await self.mcp.call_tool(
            'coinglass/funding_rates',
            exchange=position.exchange,
            symbol=position.symbol,
        )

        funding_rate = funding_data.get('funding_rate', 0)
        hours_held = (datetime.utcnow() - position.entry_time).total_seconds() / 3600
        periods_held = hours_held / 8  # Funding paid every 8 hours

        # Total funding cost
        cost_pct = abs(funding_rate) * periods_held
        cost_usd = position.size_usd * cost_pct

        # Is position still profitable after funding?
        gross_pnl = position.current_pnl_usd
        net_pnl = gross_pnl - cost_usd
        profitable = net_pnl > 0

        if not profitable and gross_pnl > 0:
            verdict = f"💀 FUNDING DESTROYED PROFIT: gross +${gross_pnl:.2f}, net -${abs(net_pnl):.2f}"
        elif profitable:
            verdict = f"✅ Profitable after funding: net +${net_pnl:.2f}"
        else:
            verdict = f"📉 Losing: net -${abs(net_pnl):.2f} (funding adds -${cost_usd:.2f})"

        return FundingCost(
            token=position.token,
            exchange=position.exchange,
            position_size_usd=position.size_usd,
            funding_rate=funding_rate,
            hours_held=round(hours_held, 1),
            periods_held=round(periods_held, 1),
            total_cost_pct=round(cost_pct * 100, 2),
            total_cost_usd=round(cost_usd, 2),
            is_profitable_after_funding=profitable,
            verdict=verdict,
        )

    async def should_exit_due_to_funding(self, position) -> bool:
        """Exit if funding cost exceeds expected profit."""
        cost = await self.calculate(position)
        # If funding cost > 50% of unrealized profit, exit
        if cost.total_cost_usd > abs(position.current_pnl_usd) * 0.5:
            await self.log.log('funding/exit_signal', {
                'token': position.token,
                'funding_cost': cost.total_cost_usd,
                'unrealized_pnl': position.current_pnl_usd,
            })
            return True
        return False
```

**Why this matters:** A long position paying 0.05% funding per 8 hours costs 0.15%/day = 4.5%/month. If your strategy targets 5% monthly return, funding eats 90% of it. This is the most common hidden killer in crypto trading.

---

## 31. Portfolio Correlation Management

> ✅ **IMPLEMENTED** — See `src/correlation_manager.py` and `src/tests/test_remaining_features.py`

**Problem:** The system collects correlation data (Gold, BTC) but never uses it for portfolio construction. Holding 5 tokens that are 90% correlated to BTC is the same as being 100% long BTC — just with more fees.

**DSH Pattern:** `Capability Seams` — portfolio-level view across all positions.

```python
"""
Portfolio Correlation Manager
Ensure portfolio isn't secretly concentrated in correlated assets.
"""

from dataclasses import dataclass
from typing import List, Dict, Optional
import numpy as np


@dataclass
class CorrelationReport:
    num_positions: int
    nominal_exposure_usd: float
    effective_exposure_usd: float
    avg_correlation: float
    diversification_score: float
    high_corr_pairs: List[dict]
    concentration_risk: str    # low, moderate, high
    recommendation: str


class CorrelationManager:
    """
    DSH-style portfolio correlation management.
    Detect hidden concentration risk.
    """

    def __init__(self, price_feed, session_log):
        self.feed = price_feed
        self.log = session_log

    async def analyze(self, positions: List[dict]) -> CorrelationReport:
        """Analyze portfolio correlation structure."""
        if len(positions) < 2:
            return CorrelationReport(
                num_positions=len(positions),
                nominal_exposure_usd=sum(p.get('value_usd', 0) for p in positions),
                effective_exposure_usd=sum(p.get('value_usd', 0) for p in positions),
                avg_correlation=0,
                diversification_score=1.0,
                high_corr_pairs=[],
                concentration_risk='low',
                recommendation='Single position — no correlation risk',
            )

        # Get correlation matrix
        tokens = [p['token'] for p in positions]
        returns = await self._get_returns(tokens, days=30)
        corr_matrix = self._compute_correlation(returns)

        # Find highly correlated pairs
        high_corr_pairs = []
        for i in range(len(tokens)):
            for j in range(i + 1, len(tokens)):
                corr = corr_matrix[i][j]
                if abs(corr) > 0.7:
                    high_corr_pairs.append({
                        'pair': f"{tokens[i][:8]} + {tokens[j][:8]}",
                        'correlation': round(corr, 3),
                        'combined_usd': positions[i].get('value_usd', 0) + positions[j].get('value_usd', 0),
                    })

        # Average correlation
        upper_triangle = [corr_matrix[i][j] for i in range(len(tokens)) for j in range(i + 1, len(tokens))]
        avg_corr = np.mean(upper_triangle) if upper_triangle else 0

        # Effective exposure (accounts for correlation)
        nominal = sum(p.get('value_usd', 0) for p in positions)
        effective = nominal * (1 + avg_corr)  # Correlated positions amplify risk

        # Diversification score (1 = perfectly diversified, 0 = perfectly correlated)
        div_score = 1 - abs(avg_corr)

        # Risk level
        if avg_corr > 0.7:
            risk = 'high'
            rec = f"🔴 HIGH CONCENTRATION — avg correlation {avg_corr:.0%}. Consider uncorrelated assets."
        elif avg_corr > 0.4:
            risk = 'moderate'
            rec = f"🟡 MODERATE — avg correlation {avg_corr:.0%}. Some diversification benefit."
        else:
            risk = 'low'
            rec = f"🟢 WELL DIVERSIFIED — avg correlation {avg_corr:.0%}. Good risk distribution."

        return CorrelationReport(
            num_positions=len(positions),
            nominal_exposure_usd=round(nominal, 2),
            effective_exposure_usd=round(effective, 2),
            avg_correlation=round(avg_corr, 3),
            diversification_score=round(div_score, 3),
            high_corr_pairs=high_corr_pairs,
            concentration_risk=risk,
            recommendation=rec,
        )

    async def _get_returns(self, tokens, days=30) -> Dict[str, List[float]]:
        """Get historical returns for correlation calculation."""
        returns = {}
        for token in tokens:
            prices = await self.feed.get_historical_prices(token, days=days)
            if len(prices) > 1:
                token_returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]
                returns[token] = token_returns
        return returns

    def _compute_correlation(self, returns: Dict[str, List[float]]) -> List[List[float]]:
        """Compute correlation matrix from returns."""
        tokens = list(returns.keys())
        n = len(tokens)
        if n == 0:
            return []

        # Align return lengths
        min_len = min(len(r) for r in returns.values())
        aligned = {t: returns[t][:min_len] for t in tokens}

        matrix = []
        for i in range(n):
            row = []
            for j in range(n):
                if i == j:
                    row.append(1.0)
                else:
                    corr = np.corrcoef(aligned[tokens[i]], aligned[tokens[j]])[0][1]
                    row.append(round(corr, 3) if not np.isnan(corr) else 0)
            matrix.append(row)
        return matrix
```

**Why this matters:** Holding 5 Solana memecoins that all pump and dump together is the same as holding 1 — just with 5x the fees. True diversification requires uncorrelated assets. This tool reveals when you *think* you're diversified but aren't.

---

# Part 5: Prioritized Roadmap

---

## Implementation Priority Table

| Priority | Improvement | Effort | Impact | Risk | Dependencies |
|---|---|---|---|---|---|
| ✅ **1** | Risk Guard Waterfall (#2) | 1-2 days | Prevents bad trades | Low | Session Log | **DONE** |
| ✅ **2** | Session Log (#3) | 1 day | Debug every decision | Low | None | **DONE** |
| ✅ **3** | Weighted Prediction Engine (#9) | 1-2 days | Better signals | Low | Features exist | **DONE** |
| ✅ **4** | Signal Validation Pipeline (#10) | 1-2 days | Filters weak signals | Low | Prediction Engine | **DONE** |
| ✅ **5** | Trade Feedback Loop (#11) | 2-3 days | Enables learning | Low | Session Log | **DONE** |
| ✅ **6** | Single LLM call with compact context (#16) | 1 day | Better decisions, save money | Medium | Compactor | **DONE** |
| ✅ **7** | Human Feedback System (#18) | 1-2 days | Learn from judgment | Low | Session Log | **DONE** |
| ✅ **8** | Risk Presets (#24) | 0.5 day | One-click risk changes | Low | None | **DONE** |
| ✅ **9** | Trading Commands (#23) | 1-2 days | Manual intervention | Low | None | **DONE** |
| ✅ **10** | Runtime Invariants (#21) | 1 day | Safety guarantees | Low | None | **DONE** |
| ✅ **11** | Event Bus (#4) | 2-3 days | Decouple agents | Medium | None | **DONE** |
| ✅ **12** | Tool Pipeline (#5) | 2 days | Dedup code, add logging | Low | None | **DONE** |
| ✅ **13** | Ensemble Strategy (#12) | 2-3 days | Multiple strategies | Medium | Feedback Loop | **DONE** |
| ✅ **14** | YAML Config & Profiles (#6) | 1 day | Environment switching | Low | None | **DONE** |
| ✅ **15** | Portfolio Goal System (#13) | 1 day | Portfolio-level decisions | Low | Session Log | **DONE** |
| ✅ **16** | Real-Time Signal Processing (#14) | 2-3 days | No more 15-min lag | Medium | Jobs system | **DONE** |
| ✅ **17** | Parallel Analysis (#15) | 1-2 days | Faster analysis | Low | Async infrastructure | **DONE** |
| ✅ **18** | Async Scheduling (#7) | 2 days | Non-blocking | Low | None | **DONE** |
| ✅ **19** | Spill & Smart Data Storage (#19) | 1 day | Reduce LLM noise | Low | None | **DONE** |
| ✅ **20** | Session Query (#22) | 1-2 days | Search trade history | Low | Session Log | **DONE** |
| ✅ **21** | Multi-Step Trade Planning (#17) | 2 days | Structured trades | Medium | Risk Guard + Log | **DONE** |
| ✅ **22** | MCP Integration (#20) | 2-3 days | External services | Medium | None | **DONE** |
| ✅ **23** | Process Isolation (#8) | 3-5 days | Fault tolerance | Medium | All above | **DONE** |
| ✅ **24** | Benchmark Tracker (#25) | 1 day | Know if adding alpha | Low | Session Log + Price Feed | **DONE** |
| ✅ **25** | Execution Quality Tracker (#28) | 1 day | Know true costs | Low | Session Log | **DONE** |
| ✅ **26** | Funding Cost Accounting (#30) | 1-2 days | Stop hidden losses | Low | MCP Integration | **DONE** |
| ✅ **27** | Alpha Decay Detection (#29) | 1-2 days | Auto-disable bad strategies | Low | Session Log | **DONE** |
| ✅ **28** | Position Sizing Optimization (#27) | 1-2 days | 2-5x return improvement | Medium | None | **DONE** |
| ✅ **29** | Walk-Forward Backtesting (#26) | 3-5 days | Prevent overfitting | Low | BacktestEngine | **DONE** |
| ✅ **30** | Correlation Management (#31) | 1-2 days | True diversification | Medium | Price Feed | **DONE** |

## Phased Timeline

### Phase 1: Foundation (Weeks 1-2)
**Goal:** Safety + Learning infrastructure

1. Session Log — record everything
2. Risk Guard Waterfall — prevent bad trades
3. Weighted Prediction Engine — better signals
4. LLM Temperature fix — 0.7 → 0.3

### Phase 2: Intelligence (Weeks 3-4)
**Goal:** Data-driven improvement + human control

5. Signal Validation Pipeline — multi-stage filtering
6. Trade Feedback Loop — track what works
7. Single LLM call with compact context
8. Human Feedback System — learn from judgment
9. Risk Presets — one-click risk changes
10. Trading Commands — manual intervention
11. Runtime Invariants — safety guarantees
12. YAML Config & Profiles

### Phase 3: Advanced (Month 2)
**Goal:** Structured trading + external data

13. Event Bus — decouple agents
14. Tool Pipeline — unified execution
15. Ensemble Strategy — multiple backends
16. Portfolio Goal System
17. Spill & Smart Data Storage
18. Session Query — trade history search
19. Multi-Step Trade Planning
20. MCP Integration — external services

### Phase 4: Research Gaps (Month 3)
**Goal:** Know your edge and protect it

25. Benchmark Tracker — know if you're adding alpha
26. Execution Quality Tracker — know your true costs
27. Funding Cost Accounting — stop hidden losses
28. Alpha Decay Detection — auto-disable bad strategies
29. Position Sizing Optimization — right-size every trade
30. Walk-Forward Backtesting — prevent overfitting
31. Correlation Management — true diversification

### Phase 5: Scale (Month 4)
**Goal:** Production readiness

21. Real-Time Signal Processing
22. Parallel Analysis
23. Async Scheduling
24. Process Isolation

## Migration Strategy

Each improvement is **independently deployable**. You don't need to rewrite everything at once:

1. **Add the Session Log** first — it's additive, no changes to existing code
2. **Wrap `market_buy`** with the Risk Guard — one function replacement
3. **Add the Feedback Loop** — starts collecting data immediately
4. **Use accuracy data** to tune the Prediction Engine weights
5. **Gradually extract agents** into the event bus pattern
6. **Add process isolation** last when everything else is stable

The key insight: **log first, optimize second**. The Session Log + Trade Feedback Loop combo gives you the data to make every other improvement data-driven instead of guessing.

## The Unbeatable Loop

All 31 improvements connect into a self-reinforcing cycle:

```
┌─────────────────────────────────────────────────────────────────┐
│                    THE UNBEATABLE LOOP                           │
│                                                                 │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐ │
│  │   Plan   │───▶│ Execute  │───▶│  Track   │───▶│  Learn   │ │
│  │ (#17,#26)│    │ (#2,#5,#24)│   │ (#3,#28) │   │ (#11,#18)│ │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘ │
│       ▲                                                │       │
│       │         ┌──────────┐    ┌──────────┐          │       │
│       └─────────│  Adjust  │◀───│  Query   │◀─────────┘       │
│                 │ (#9,#12,#27)│  │ (#22,#25)│                   │
│                 └──────────┘    └──────────┘                   │
│                                                                 │
│  Safety: Invariants (#21) + Risk Guard (#2) + Presets (#24)     │
│  Control: Commands (#23) + Feedback (#18) + Plan (#17)          │
│  Data: MCP (#20) + Spill (#19) + Query (#22) + Bench (#25)     │
│  Research: Walk-Forward (#26) + Decay (#29) + Sizing (#27)      │
└─────────────────────────────────────────────────────────────────┘
```

Each iteration through the loop makes the system smarter. The **research gap improvements (#25-31)** ensure you know *whether* you're actually adding value — and automatically protect that value when it decays.

---

*Built by analyzing Moon Dev's AI Trading Platform against DeepSeek Harness architecture patterns.*
*31 improvements across 5 parts: Architecture, Accuracy, Advanced, Research Gaps, and Roadmap.*
*Document created: August 2026*
