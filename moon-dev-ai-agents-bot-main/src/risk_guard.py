"""
🛡️ Moon Dev's Risk Guard Waterfall
DSH Pattern: Waterfall Events — staged pre-trade validation.

Every order passes through validation stages before execution.
Any stage can REJECT (block the trade) or MODIFY (adjust size/params).
Stages run in priority order. All rejections are logged for analysis.

Usage:
    guard = RiskGuard(config)
    result = await guard.validate_order(order)
    if result.approved:
        execute(order)
    else:
        log(f"Rejected at {result.stage}: {result.reason}")
"""

import os
import json
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Callable, Awaitable, Any, Dict
from enum import Enum
from termcolor import cprint


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass
class Order:
    """Represents a trade order to be validated."""
    token: str
    side: OrderSide
    order_type: OrderType
    amount_usd: float
    slippage: float = 0.05
    price: Optional[float] = None  # For limit orders
    source: str = "unknown"  # Which agent initiated this
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'token': self.token[:12] + '...' if len(self.token) > 12 else self.token,
            'side': self.side.value,
            'order_type': self.order_type.value,
            'amount_usd': round(self.amount_usd, 2),
            'slippage': self.slippage,
            'source': self.source,
        }


@dataclass
class ValidationResult:
    """Result of running an order through the waterfall."""
    approved: bool
    stage: Optional[str] = None  # Which stage rejected
    reason: Optional[str] = None
    modified_order: Optional[Order] = None  # If a stage modified the order
    adjustments: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            'approved': self.approved,
            'stage': self.stage,
            'reason': self.reason,
            'adjustments': self.adjustments,
            'timestamp': self.timestamp,
        }


class GuardStage:
    """A single validation stage in the waterfall."""

    def __init__(self, name: str, priority: int, fn: Callable, enabled: bool = True):
        self.name = name
        self.priority = priority
        self.fn = fn
        self.enabled = enabled

    def __repr__(self):
        status = "✅" if self.enabled else "❌"
        return f"{status} {self.name} (priority={self.priority})"


class RiskGuard:
    """
    DSH-style waterfall risk guard.

    Orders flow through ordered stages:
    1. Minimum order size check
    2. Maximum position size check
    3. Daily loss limit check
    4. Correlation / concentration check
    5. Liquidity check
    6. Fee profitability check

    Any stage can:
    - approve: pass to next stage
    - reject: block the order with a reason
    - modify: adjust order size, then pass to next stage
    """

    def __init__(self, config=None):
        self.stages: List[GuardStage] = []
        self.rejection_log: List[dict] = []
        self.config = config or {}
        self._setup_defaults()

    def _setup_defaults(self):
        """Register default guard stages."""
        self.register("minimum_order_size", self._check_minimum_order, priority=10)
        self.register("maximum_position_size", self._check_maximum_position, priority=20)
        self.register("daily_loss_limit", self._check_daily_loss, priority=30)
        self.register("concentration_limit", self._check_concentration, priority=40)
        self.register("fee_profitability", self._check_fee_profitability, priority=50)

    def register(self, name: str, fn: Callable, priority: int = 100, enabled: bool = True):
        """Register a new guard stage."""
        stage = GuardStage(name=name, priority=priority, fn=fn, enabled=enabled)
        self.stages.append(stage)
        self.stages.sort(key=lambda s: s.priority)

    def enable(self, name: str):
        """Enable a guard stage by name."""
        for stage in self.stages:
            if stage.name == name:
                stage.enabled = True

    def disable(self, name: str):
        """Disable a guard stage by name."""
        for stage in self.stages:
            if stage.name == name:
                stage.enabled = False

    async def validate_order(self, order: Order) -> ValidationResult:
        """
        Run order through all enabled stages.
        Returns the first rejection, or approval if all pass.
        """
        original_order = order
        modified = False
        all_adjustments = []

        for stage in self.stages:
            if not stage.enabled:
                continue

            try:
                result = await stage.fn(order)
            except Exception as e:
                # Guard failure = reject (fail-closed)
                self._log_rejection(order, stage.name, f"Guard error: {str(e)}")
                return ValidationResult(
                    approved=False,
                    stage=stage.name,
                    reason=f"Guard stage crashed: {str(e)}",
                )

            if not result.approved:
                self._log_rejection(order, stage.name, result.reason)
                cprint(
                    f"🛑 REJECTED at [{stage.name}]: {result.reason}",
                    "white", "on_red"
                )
                return result

            # Stage may have modified the order
            if result.modified_order and result.modified_order is not order:
                order = result.modified_order
                modified = True
                if result.adjustments:
                    all_adjustments.extend(result.adjustments)
                    cprint(
                        f"⚠️ MODIFIED at [{stage.name}]: {'; '.join(result.adjustments)}",
                        "yellow"
                    )

        # All stages passed
        cprint(
            f"✅ APPROVED: {order.side.value.upper()} ${order.amount_usd:.2f} of {order.token[:8]}...",
            "white", "on_green"
        )
        return ValidationResult(
            approved=True,
            modified_order=order if modified else None,
            adjustments=all_adjustments,
        )

    def _log_rejection(self, order: Order, stage: str, reason: str):
        """Log a rejection for analysis."""
        entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'token': order.token,
            'side': order.side.value,
            'amount_usd': order.amount_usd,
            'source': order.source,
            'stage': stage,
            'reason': reason,
        }
        self.rejection_log.append(entry)
        self._persist_rejection(entry)

    def _persist_rejection(self, entry: dict):
        """Save rejection to CSV for analysis."""
        try:
            log_dir = os.path.join(os.path.dirname(__file__), 'data')
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, 'risk_rejections.csv')

            df = pd.DataFrame([entry])
            if os.path.exists(log_path):
                df.to_csv(log_path, mode='a', header=False, index=False)
            else:
                df.to_csv(log_path, index=False)
        except Exception:
            pass  # Don't crash on logging failure

    def get_rejection_stats(self) -> dict:
        """Analyze rejection patterns."""
        if not self.rejection_log:
            return {'total': 0, 'by_stage': {}, 'by_source': {}}

        by_stage = {}
        by_source = {}
        for entry in self.rejection_log:
            stage = entry['stage']
            source = entry['source']
            by_stage[stage] = by_stage.get(stage, 0) + 1
            by_source[source] = by_source.get(source, 0) + 1

        return {
            'total': len(self.rejection_log),
            'by_stage': by_stage,
            'by_source': by_source,
        }

    # ── Default Guard Stages ──────────────────────────────────────

    async def _check_minimum_order(self, order: Order) -> ValidationResult:
        """Reject orders below minimum size (would lose money to fees)."""
        min_size = self.config.get('min_order_usd', 1.0)

        if order.amount_usd < min_size:
            return ValidationResult(
                approved=False,
                stage="minimum_order_size",
                reason=f"Order ${order.amount_usd:.2f} below minimum ${min_size:.2f}",
            )
        return ValidationResult(approved=True)

    async def _check_maximum_position(self, order: Order) -> ValidationResult:
        """Reject orders that would create too large a position."""
        max_position = self.config.get('max_position_usd', 500.0)
        max_pct_portfolio = self.config.get('max_position_pct', 0.25)
        portfolio_value = self.config.get('portfolio_value', 1000.0)

        # Calculate max allowed by percentage
        max_by_pct = portfolio_value * max_pct_portfolio
        effective_max = min(max_position, max_by_pct)

        if order.amount_usd > effective_max:
            return ValidationResult(
                approved=False,
                stage="maximum_position_size",
                reason=(
                    f"Order ${order.amount_usd:.2f} exceeds max position "
                    f"${effective_max:.2f} ({max_pct_portfolio:.0%} of ${portfolio_value:.2f})"
                ),
            )

        # If over 80% of max, recommend reducing
        if order.amount_usd > effective_max * 0.8:
            reduced = effective_max * 0.75
            modified = Order(
                token=order.token,
                side=order.side,
                order_type=order.order_type,
                amount_usd=reduced,
                slippage=order.slippage,
                price=order.price,
                source=order.source,
                metadata=order.metadata,
            )
            return ValidationResult(
                approved=True,
                modified_order=modified,
                adjustments=[f"Reduced from ${order.amount_usd:.2f} to ${reduced:.2f} (near limit)"],
            )

        return ValidationResult(approved=True)

    async def _check_daily_loss(self, order: Order) -> ValidationResult:
        """Reject new BUY orders if daily loss limit is hit."""
        if order.side == OrderSide.SELL:
            # Always allow sells (we want to be able to exit)
            return ValidationResult(approved=True)

        daily_loss_limit = self.config.get('daily_loss_usd', 50.0)
        daily_loss_pct = self.config.get('daily_loss_pct', 5.0)
        daily_pnl = self.config.get('daily_pnl', 0.0)  # Negative = loss
        portfolio_value = self.config.get('portfolio_value', 1000.0)

        # Check absolute loss
        if daily_pnl <= -daily_loss_limit:
            return ValidationResult(
                approved=False,
                stage="daily_loss_limit",
                reason=f"Daily loss ${abs(daily_pnl):.2f} exceeds limit ${daily_loss_limit:.2f}",
            )

        # Check percentage loss
        daily_loss_pct_actual = (daily_pnl / portfolio_value * 100) if portfolio_value > 0 else 0
        if daily_loss_pct_actual <= -daily_loss_pct:
            return ValidationResult(
                approved=False,
                stage="daily_loss_limit",
                reason=f"Daily loss {daily_loss_pct_actual:.1f}% exceeds limit {daily_loss_pct:.1f}%",
            )

        return ValidationResult(approved=True)

    async def _check_concentration(self, order: Order) -> ValidationResult:
        """Reject if buying would over-concentrate in one token."""
        if order.side == OrderSide.SELL:
            return ValidationResult(approved=True)

        max_concentration_pct = self.config.get('max_concentration_pct', 0.30)
        portfolio_value = self.config.get('portfolio_value', 1000.0)
        current_positions = self.config.get('current_positions', {})

        current_value = current_positions.get(order.token, 0.0)
        new_total = current_value + order.amount_usd
        concentration = new_total / portfolio_value if portfolio_value > 0 else 1.0

        if concentration > max_concentration_pct:
            return ValidationResult(
                approved=False,
                stage="concentration_limit",
                reason=(
                    f"Would concentrate {concentration:.0%} in one token "
                    f"(limit: {max_concentration_pct:.0%})"
                ),
            )

        return ValidationResult(approved=True)

    async def _check_fee_profitability(self, order: Order) -> ValidationResult:
        """Reject if fees would exceed expected profit."""
        estimated_fee_pct = self.config.get('estimated_fee_pct', 0.01)  # 1%
        min_profit_ratio = self.config.get('min_profit_ratio', 3.0)  # Risk:reward >= 3:1
        expected_slippage = order.slippage

        total_cost_pct = estimated_fee_pct + expected_slippage
        # If we expect less than 3x the cost in profit, reject
        # This is a rough heuristic — real implementation would use expected alpha
        if order.amount_usd * total_cost_pct > order.amount_usd * 0.02:
            # Cost exceeds 2% of order — likely not profitable for small trades
            if order.amount_usd < 5.0:
                return ValidationResult(
                    approved=False,
                    stage="fee_profitability",
                    reason=(
                        f"Fees + slippage ({total_cost_pct:.1%}) on ${order.amount_usd:.2f} "
                        f"likely exceeds profit. Minimum recommended: $5+"
                    ),
                )

        return ValidationResult(approved=True)


# ── Integration Helper ──────────────────────────────────────────

def create_guard_from_config() -> RiskGuard:
    """Create a RiskGuard with values from Moon Dev's config.py."""
    try:
        from src.config import (
            MINIMUM_BALANCE_USD, MAX_LOSS_USD, MAX_LOSS_PERCENT,
            MONITORED_TOKENS, slippage, max_usd_order_size,
        )
    except ImportError:
        # Fallback defaults
        return RiskGuard({})

    # Try to get portfolio value
    portfolio_value = 25.0  # Default
    try:
        balance_file = os.path.join(os.path.dirname(__file__), 'data', 'portfolio_balance.csv')
        if os.path.exists(balance_file):
            df = pd.read_csv(balance_file)
            if not df.empty:
                portfolio_value = float(df['balance'].iloc[-1])
    except Exception:
        pass

    config = {
        'min_order_usd': 1.0,
        'max_position_usd': max_usd_order_size if 'max_usd_order_size' in dir() else 500.0,
        'max_position_pct': 0.25,
        'daily_loss_usd': MAX_LOSS_USD if 'MAX_LOSS_USD' in dir() else 50.0,
        'daily_loss_pct': MAX_LOSS_PERCENT if 'MAX_LOSS_PERCENT' in dir() else 5.0,
        'daily_pnl': 0.0,  # Will be updated at runtime
        'portfolio_value': portfolio_value,
        'current_positions': {},
        'max_concentration_pct': 0.30,
        'estimated_fee_pct': 0.01,
        'min_profit_ratio': 3.0,
    }

    return RiskGuard(config)


# ── CLI / Test Interface ────────────────────────────────────────

async def main():
    """Demo the risk guard waterfall."""
    guard = RiskGuard({
        'min_order_usd': 1.0,
        'max_position_usd': 100.0,
        'max_position_pct': 0.25,
        'daily_loss_usd': 50.0,
        'daily_loss_pct': 5.0,
        'daily_pnl': -20.0,
        'portfolio_value': 1000.0,
        'current_positions': {},
        'max_concentration_pct': 0.30,
        'estimated_fee_pct': 0.01,
    })

    print("\n🛡️ Risk Guard Waterfall — Demo\n")
    print("Active stages:")
    for stage in guard.stages:
        print(f"  {stage}")
    print()

    # Test 1: Valid order
    order1 = Order(
        token="FARTCOIN",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount_usd=25.0,
        source="trading_agent",
    )
    print("--- Test 1: Valid $25 buy ---")
    result1 = await guard.validate_order(order1)
    print(f"Result: {'✅ APPROVED' if result1.approved else '❌ REJECTED'}")
    print()

    # Test 2: Too small
    order2 = Order(
        token="FARTCOIN",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount_usd=0.50,
        source="strategy_agent",
    )
    print("--- Test 2: $0.50 buy (too small) ---")
    result2 = await guard.validate_order(order2)
    print(f"Result: {'✅ APPROVED' if result2.approved else '❌ REJECTED'}")
    if not result2.approved:
        print(f"Reason: {result2.reason}")
    print()

    # Test 3: Over position limit (should be auto-reduced)
    order3 = Order(
        token="FARTCOIN",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount_usd=90.0,
        source="trading_agent",
    )
    print("--- Test 3: $90 buy (near limit, should auto-reduce) ---")
    result3 = await guard.validate_order(order3)
    print(f"Result: {'✅ APPROVED' if result3.approved else '❌ REJECTED'}")
    if result3.adjustments:
        print(f"Adjustments: {result3.adjustments}")
    print()

    # Test 4: Daily loss limit hit
    guard.config['daily_pnl'] = -60.0  # Over $50 limit
    order4 = Order(
        token="FARTCOIN",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount_usd=25.0,
        source="trading_agent",
    )
    print("--- Test 4: $25 buy with daily loss limit hit ---")
    result4 = await guard.validate_order(order4)
    print(f"Result: {'✅ APPROVED' if result4.approved else '❌ REJECTED'}")
    if not result4.approved:
        print(f"Reason: {result4.reason}")
    print()

    # Test 5: SELL still works during loss limit
    order5 = Order(
        token="FARTCOIN",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount_usd=25.0,
        source="risk_agent",
    )
    print("--- Test 5: $25 SELL during loss limit (should pass) ---")
    result5 = await guard.validate_order(order5)
    print(f"Result: {'✅ APPROVED' if result5.approved else '❌ REJECTED'}")
    print()

    # Stats
    stats = guard.get_rejection_stats()
    print(f"📊 Rejection stats: {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
