"""
🚀 Moon Dev's Order Executor — DSH-style integrated order flow
All orders flow through: Event Bus → Risk Guard Waterfall → Execution → Session Log

This replaces direct calls to nice_funcs with a safe, logged, risk-checked pipeline.

Before (old flow):
    n.ai_entry(token, amount)           # Direct to Jupiter — no validation
    n.chunk_kill(token, size, slippage) # Direct to Jupiter — no validation

After (DSH flow):
    executor.buy(token, amount)         # Event Bus → Risk Guard → Execution → Log
    executor.sell(token, amount)        # Event Bus → Risk Guard → Execution → Log

Usage:
    executor = OrderExecutor(event_bus=event_bus, session_log=log)
    result = await executor.buy('FARTCOIN', 25.0)
    if result['executed']:
        print(f"Filled at {result['fill_price']}")
    else:
        print(f"Rejected: {result['reason']}")
"""

import time
from typing import Optional, Dict, Any
from termcolor import cprint

from src.event_bus import EventBus, Events, DispatchMode, WaterfallResult
from src.session_log import SessionLog, EventType


# ── Order Result ──────────────────────────────────────────────

class OrderResult:
    """Result of an order execution attempt."""

    def __init__(self, executed: bool = False, reason: str = None,
                 fill_price: float = 0.0, slippage: float = 0.0,
                 amount_usd: float = 0.0, token: str = "",
                 side: str = "", source: str = "",
                 modifications: list = None):
        self.executed = executed
        self.reason = reason
        self.fill_price = fill_price
        self.slippage = slippage
        self.amount_usd = amount_usd
        self.token = token
        self.side = side
        self.source = source
        self.modifications = modifications or []
        self.timestamp = time.time()

    def to_dict(self) -> dict:
        return {
            'executed': self.executed,
            'reason': self.reason,
            'fill_price': self.fill_price,
            'slippage': self.slippage,
            'amount_usd': self.amount_usd,
            'token': self.token[:12] + '...' if len(self.token) > 12 else self.token,
            'side': self.side,
            'source': self.source,
            'modifications': self.modifications,
            'timestamp': self.timestamp,
        }

    def __repr__(self):
        status = "✅" if self.executed else "❌"
        return f"{status} {self.side.upper()} ${self.amount_usd:.2f} {self.token[:8]}... — {self.reason or 'filled'}"


# ── Order Executor ────────────────────────────────────────────

class OrderExecutor:
    """
    DSH-style order executor.

    Every order flows through:
    1. Event Bus waterfall (Risk Guard can reject/modify)
    2. Execution (nice_funcs with retry)
    3. Session Log (full audit trail)

    This is the single entry point for all trading actions.
    Agents never call nice_funcs directly.
    """

    def __init__(self, event_bus: EventBus = None, session_log: SessionLog = None,
                 config: dict = None):
        self.bus = event_bus or EventBus()
        self.log = session_log
        self.config = config or {}
        self._execution_count = 0
        self._rejection_count = 0

    # ── Public API ────────────────────────────────────────────

    async def buy(self, token: str, amount_usd: float,
                  slippage: float = None, source: str = "trading_agent") -> OrderResult:
        """
        Buy a token. Goes through Risk Guard waterfall before execution.

        Args:
            token: Token address
            amount_usd: Amount in USD to buy
            slippage: Slippage tolerance (overrides config)
            source: Which agent initiated this order

        Returns:
            OrderResult with execution details
        """
        slippage = slippage or self.config.get('slippage', 199)

        return await self._execute_order(
            token=token,
            side="buy",
            amount_usd=amount_usd,
            slippage=slippage,
            source=source,
        )

    async def sell(self, token: str, amount_usd: float,
                   slippage: float = None, source: str = "trading_agent") -> OrderResult:
        """
        Sell a token. Goes through Risk Guard waterfall before execution.
        """
        slippage = slippage or self.config.get('slippage', 199)

        return await self._execute_order(
            token=token,
            side="sell",
            amount_usd=amount_usd,
            slippage=slippage,
            source=source,
        )

    async def close_position(self, token: str, slippage: float = None,
                             source: str = "risk_agent") -> OrderResult:
        """
        Close an entire position. Uses chunk_kill for gradual exit.
        """
        slippage = slippage or self.config.get('slippage', 199)

        # Get current position size
        try:
            from src import nice_funcs as n
            position_usd = n.get_token_balance_usd(token)
        except Exception:
            position_usd = 0.0

        if position_usd <= 0:
            return OrderResult(
                executed=False,
                reason=f"No position to close for {token[:8]}...",
                token=token,
                side="sell",
                source=source,
            )

        return await self._execute_order(
            token=token,
            side="sell",
            amount_usd=position_usd,
            slippage=slippage,
            source=source,
            use_chunk_kill=True,
        )

    async def intent(self, token: str, side: str, amount_usd: float,
                     source: str = "unknown") -> OrderResult:
        """
        Declare order intent without executing.
        Useful for pre-trade logging and planning.
        """
        # Emit through the bus
        await self.bus.emit(Events.ORDER_INTENT, {
            'token': token,
            'side': side,
            'amount_usd': amount_usd,
            'source': source,
        })

        # Log the intent
        if self.log:
            await self.log.log(EventType.ORDER_INTENT, {
                'token': token,
                'side': side,
                'amount_usd': amount_usd,
                'source': source,
            })

        return OrderResult(
            executed=False,
            reason="Intent only — not executed",
            token=token,
            side=side,
            amount_usd=amount_usd,
            source=source,
        )

    # ── Stats ─────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            'total_executions': self._execution_count,
            'total_rejections': self._rejection_count,
            'approval_rate': (
                self._execution_count / (self._execution_count + self._rejection_count)
                if (self._execution_count + self._rejection_count) > 0
                else 0
            ),
        }

    # ── Internal ──────────────────────────────────────────────

    async def _execute_order(self, token: str, side: str, amount_usd: float,
                             slippage: float, source: str,
                             use_chunk_kill: bool = False) -> OrderResult:
        """
        Core order execution pipeline:

        1. Emit order/intent event
        2. Run through Risk Guard waterfall (order/submit)
        3. If approved → execute via nice_funcs
        4. Log result through Session Log
        5. Return OrderResult
        """

        # ── Step 1: Log intent ────────────────────────────────
        if self.log:
            await self.log.log(EventType.ORDER_INTENT, {
                'token': token,
                'side': side,
                'amount_usd': amount_usd,
                'slippage': slippage,
                'source': source,
            })

        # ── Step 2: Risk Guard Waterfall ──────────────────────
        waterfall_result = await self.bus.waterfall(
            Events.ORDER_SUBMIT,
            {
                'token': token,
                'side': side,
                'amount_usd': amount_usd,
                'slippage': slippage / 10000,  # Convert bps to decimal
                'order_type': 'market',
                'source': source,
            }
        )

        if not waterfall_result.approved:
            self._rejection_count += 1
            reason = f"Risk Guard: {waterfall_result.reason}"

            cprint(f"🛑 ORDER REJECTED: {side.upper()} ${amount_usd:.2f} {token[:8]}...", "white", "on_red")
            cprint(f"   Reason: {reason}", "red")

            # Log rejection
            if self.log:
                await self.log.log(EventType.RISK_DENIED, {
                    'token': token,
                    'side': side,
                    'amount_usd': amount_usd,
                    'reason': waterfall_result.reason,
                    'guard': waterfall_result.rejected_by,
                    'source': source,
                })

            return OrderResult(
                executed=False,
                reason=reason,
                token=token,
                side=side,
                amount_usd=amount_usd,
                source=source,
            )

        # ── Step 3: Apply any modifications from waterfall ────
        final_amount = waterfall_result.payload.get('amount_usd', amount_usd)
        modifications = waterfall_result.payload.get('modifications', [])

        # Also capture any modifications tracked by the waterfall framework
        if waterfall_result.modifications:
            modifications = modifications + waterfall_result.modifications

        if final_amount != amount_usd:
            cprint(f"⚠️ Order modified: ${amount_usd:.2f} → ${final_amount:.2f}", "yellow")
            amount_usd = final_amount

        # ── Step 4: Execute via nice_funcs ────────────────────
        cprint(f"🚀 EXECUTING: {side.upper()} ${amount_usd:.2f} {token[:8]}...", "white", "on_green")

        try:
            from src import nice_funcs as n

            if use_chunk_kill:
                # Gradual exit
                max_order = self.config.get('max_usd_order_size', 3.0)
                n.chunk_kill(token, max_order, slippage / 10000)
                fill_price = 0  # chunk_kill doesn't return price
            elif side == "buy":
                n.ai_entry(token, amount_usd)
                fill_price = 0  # ai_entry doesn't return price
            else:
                n.market_sell(token, amount_usd, slippage / 10000)
                fill_price = 0  # market_sell doesn't return price

            self._execution_count += 1

            # ── Step 5: Log success ───────────────────────────
            if self.log:
                await self.log.log(EventType.ORDER_SUBMITTED, {
                    'token': token,
                    'side': side,
                    'amount_usd': amount_usd,
                    'source': source,
                    'modifications': modifications,
                })

            cprint(f"✅ ORDER FILLED: {side.upper()} ${amount_usd:.2f} {token[:8]}...", "white", "on_green")

            return OrderResult(
                executed=True,
                reason="Filled",
                fill_price=fill_price,
                amount_usd=amount_usd,
                token=token,
                side=side,
                source=source,
                modifications=modifications,
            )

        except Exception as e:
            self._rejection_count += 1
            reason = f"Execution error: {str(e)}"

            cprint(f"❌ ORDER FAILED: {side.upper()} ${amount_usd:.2f} {token[:8]}...", "white", "on_red")
            cprint(f"   Error: {reason}", "red")

            # Log failure
            if self.log:
                await self.log.log(EventType.ORDER_FAILED, {
                    'token': token,
                    'side': side,
                    'amount_usd': amount_usd,
                    'error': str(e),
                    'source': source,
                })

            return OrderResult(
                executed=False,
                reason=reason,
                token=token,
                side=side,
                amount_usd=amount_usd,
                source=source,
            )


# ── Factory ───────────────────────────────────────────────────

def create_order_executor(event_bus: EventBus = None, session_log: SessionLog = None,
                          config: dict = None) -> OrderExecutor:
    """Create an OrderExecutor wired to the Event Bus and Session Log."""
    return OrderExecutor(
        event_bus=event_bus,
        session_log=session_log,
        config=config or {},
    )


# ── CLI Demo ──────────────────────────────────────────────────

async def main():
    """Demo the order executor."""
    from src.event_bus import EventBus
    from src.session_log import create_test_session_log

    bus = EventBus()
    log = create_test_session_log()

    # Register a mock risk guard that rejects large orders
    async def mock_risk_guard(payload, next_fn):
        if payload['amount_usd'] > 50:
            return {'rejected': True, 'reason': 'Position too large (mock)'}
        return await next_fn(payload)

    bus.on(Events.ORDER_SUBMIT, mock_risk_guard,
           mode=DispatchMode.WATERFALL, priority=10, tag="risk_guard")

    executor = OrderExecutor(event_bus=bus, session_log=log)

    print("\n🚀 Moon Dev Order Executor — Demo\n")

    # Test 1: Small order (should pass)
    print("--- Test 1: $25 buy (should pass) ---")
    result = await executor.buy('FARTCOIN', 25.0, source='demo')
    print(f"  Result: {result}\n")

    # Test 2: Large order (should be rejected by risk guard)
    print("--- Test 2: $100 buy (should be rejected) ---")
    result = await executor.buy('FARTCOIN', 100.0, source='demo')
    print(f"  Result: {result}\n")

    # Test 3: Intent only
    print("--- Test 3: Intent only ---")
    result = await executor.intent('FARTCOIN', 'buy', 50.0, source='demo')
    print(f"  Result: {result}\n")

    # Stats
    print("--- Stats ---")
    print(f"  {executor.stats()}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
