"""
🛡️ Moon Dev's Runtime Invariants — Safety Guarantees
DSH Pattern: ctx.invariants — assertions that run continuously.

Invariants are conditions that MUST always be true. If any invariant
is violated, the system halts trading and alerts. This prevents:
- Portfolio going negative
- Exposure exceeding limits
- Orders bypassing risk checks
- State corruption

Usage:
    invariants = InvariantSystem()
    invariants.register('max_exposure', check_max_exposure, critical=True)
    await invariants.check_all(portfolio_state)
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Optional, Any
from enum import Enum
from termcolor import cprint


class Severity(str, Enum):
    """Invariant violation severity."""
    CRITICAL = "critical"    # Halt trading immediately
    WARNING = "warning"      # Log but continue
    INFO = "info"            # Informational only


class InvariantStatus(str, Enum):
    """Current status of an invariant."""
    PASSING = "passing"
    FAILING = "failing"
    UNKNOWN = "unknown"


@dataclass
class InvariantResult:
    """Result of an invariant check."""
    name: str
    status: InvariantStatus
    severity: Severity
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'status': self.status.value,
            'severity': self.severity.value,
            'message': self.message,
            'details': self.details,
            'checked_at': self.checked_at,
        }


@dataclass
class Invariant:
    """A registered invariant check."""
    name: str
    fn: Callable
    severity: Severity = Severity.CRITICAL
    enabled: bool = True
    last_result: Optional[InvariantResult] = None
    description: str = ""


# ── Built-in Invariants ───────────────────────────────────────

async def check_portfolio_not_negative(state: dict) -> InvariantResult:
    """Portfolio value must never go negative."""
    value = state.get('portfolio_value', 0)
    if value < 0:
        return InvariantResult(
            name='portfolio_not_negative',
            status=InvariantStatus.FAILING,
            severity=Severity.CRITICAL,
            message=f'Portfolio is negative: ${value:.2f}',
            details={'portfolio_value': value},
        )
    return InvariantResult(
        name='portfolio_not_negative',
        status=InvariantStatus.PASSING,
        severity=Severity.CRITICAL,
    )


async def check_max_exposure(state: dict) -> InvariantResult:
    """Total exposure must not exceed limit."""
    exposure_pct = state.get('exposure_pct', 0)
    max_exposure = state.get('max_exposure_pct', 80)

    if exposure_pct > max_exposure:
        return InvariantResult(
            name='max_exposure',
            status=InvariantStatus.FAILING,
            severity=Severity.CRITICAL,
            message=f'Exposure {exposure_pct:.1f}% exceeds max {max_exposure:.1f}%',
            details={'exposure_pct': exposure_pct, 'max': max_exposure},
        )
    return InvariantResult(
        name='max_exposure',
        status=InvariantStatus.PASSING,
        severity=Severity.CRITICAL,
    )


async def check_max_positions(state: dict) -> InvariantResult:
    """Number of positions must not exceed limit."""
    count = state.get('position_count', 0)
    max_count = state.get('max_positions', 5)

    if count > max_count:
        return InvariantResult(
            name='max_positions',
            status=InvariantStatus.FAILING,
            severity=Severity.WARNING,
            message=f'{count} positions exceeds max {max_count}',
            details={'count': count, 'max': max_count},
        )
    return InvariantResult(
        name='max_positions',
        status=InvariantStatus.PASSING,
        severity=Severity.WARNING,
    )


async def check_daily_loss_limit(state: dict) -> InvariantResult:
    """Daily loss must not exceed limit."""
    daily_pnl = state.get('daily_pnl', 0)
    max_loss = state.get('max_daily_loss_usd', 25)

    if daily_pnl < -max_loss:
        return InvariantResult(
            name='daily_loss_limit',
            status=InvariantStatus.FAILING,
            severity=Severity.CRITICAL,
            message=f'Daily loss ${abs(daily_pnl):.2f} exceeds limit ${max_loss:.2f}',
            details={'daily_pnl': daily_pnl, 'limit': max_loss},
        )
    return InvariantResult(
        name='daily_loss_limit',
        status=InvariantStatus.PASSING,
        severity=Severity.CRITICAL,
    )


async def check_cash_buffer(state: dict) -> InvariantResult:
    """Cash buffer must not drop below minimum."""
    cash_pct = state.get('cash_pct', 100)
    min_buffer = state.get('min_cash_buffer_pct', 20)

    if cash_pct < min_buffer:
        return InvariantResult(
            name='cash_buffer',
            status=InvariantStatus.FAILING,
            severity=Severity.WARNING,
            message=f'Cash {cash_pct:.1f}% below minimum {min_buffer:.1f}%',
            details={'cash_pct': cash_pct, 'min': min_buffer},
        )
    return InvariantResult(
        name='cash_buffer',
        status=InvariantStatus.PASSING,
        severity=Severity.WARNING,
    )


async def check_order_size(state: dict) -> InvariantResult:
    """No single order should exceed max position size."""
    order_usd = state.get('order_usd', 0)
    max_position_usd = state.get('max_position_usd', 500)

    if order_usd > max_position_usd:
        return InvariantResult(
            name='order_size',
            status=InvariantStatus.FAILING,
            severity=Severity.CRITICAL,
            message=f'Order ${order_usd:.2f} exceeds max position ${max_position_usd:.2f}',
            details={'order_usd': order_usd, 'max': max_position_usd},
        )
    return InvariantResult(
        name='order_size',
        status=InvariantStatus.PASSING,
        severity=Severity.CRITICAL,
    )


# ── Invariant System ──────────────────────────────────────────

class InvariantSystem:
    """
    DSH-style invariant system.

    Registers safety checks that run continuously.
    Any CRITICAL violation halts trading.
    """

    def __init__(self):
        self._invariants: Dict[str, Invariant] = {}
        self._violation_history: List[dict] = []
        self._halted = False
        self._halt_reason = ""

        # Register built-in invariants
        self.register('portfolio_not_negative', check_portfolio_not_negative,
                      Severity.CRITICAL, description='Portfolio must not go negative')
        self.register('max_exposure', check_max_exposure,
                      Severity.CRITICAL, description='Total exposure must not exceed limit')
        self.register('max_positions', check_max_positions,
                      Severity.WARNING, description='Position count must not exceed limit')
        self.register('daily_loss_limit', check_daily_loss_limit,
                      Severity.CRITICAL, description='Daily loss must not exceed limit')
        self.register('cash_buffer', check_cash_buffer,
                      Severity.WARNING, description='Cash buffer must stay above minimum')
        self.register('order_size', check_order_size,
                      Severity.CRITICAL, description='Order size must not exceed max position')

    def register(self, name: str, fn: Callable, severity: Severity = Severity.CRITICAL,
                 description: str = ""):
        """Register an invariant check."""
        self._invariants[name] = Invariant(
            name=name, fn=fn, severity=severity, description=description,
        )

    def enable(self, name: str):
        """Enable an invariant."""
        if name in self._invariants:
            self._invariants[name].enabled = True

    def disable(self, name: str):
        """Disable an invariant."""
        if name in self._invariants:
            self._invariants[name].enabled = False

    async def check_all(self, state: dict) -> List[InvariantResult]:
        """
        Run all enabled invariants.

        Returns list of results. If any CRITICAL invariant fails,
        the system is halted.
        """
        results = []

        for inv in self._invariants.values():
            if not inv.enabled:
                continue

            try:
                result = await inv.fn(state)
                inv.last_result = result
                results.append(result)

                if result.status == InvariantStatus.FAILING:
                    self._record_violation(result)

                    if result.severity == Severity.CRITICAL:
                        self._halted = True
                        self._halt_reason = result.message
                        cprint(f"🚨 CRITICAL INVARIANT VIOLATED: {result.name}", "white", "on_red")
                        cprint(f"   {result.message}", "red")
                        cprint(f"   TRADING HALTED", "white", "on_red")
                    elif result.severity == Severity.WARNING:
                        cprint(f"⚠️  WARNING: {result.name} — {result.message}", "yellow")

            except Exception as e:
                result = InvariantResult(
                    name=inv.name,
                    status=InvariantStatus.FAILING,
                    severity=inv.severity,
                    message=f'Check error: {str(e)}',
                )
                inv.last_result = result
                results.append(result)

        return results

    async def check_single(self, name: str, state: dict) -> Optional[InvariantResult]:
        """Run a single invariant check."""
        inv = self._invariants.get(name)
        if not inv or not inv.enabled:
            return None

        try:
            result = await inv.fn(state)
            inv.last_result = result
            return result
        except Exception as e:
            return InvariantResult(
                name=name,
                status=InvariantStatus.FAILING,
                severity=inv.severity,
                message=f'Check error: {str(e)}',
            )

    def is_halted(self) -> bool:
        """Check if trading is halted due to critical violation."""
        return self._halted

    def resume(self):
        """Resume trading after a halt. Use with caution!"""
        self._halted = False
        self._halt_reason = ""
        cprint("✅ Trading resumed", "green")

    def get_status(self) -> dict:
        """Get current invariant status summary."""
        passing = 0
        failing = 0
        details = {}

        for name, inv in self._invariants.items():
            if inv.last_result:
                if inv.last_result.status == InvariantStatus.PASSING:
                    passing += 1
                else:
                    failing += 1
                details[name] = inv.last_result.to_dict()
            else:
                details[name] = {'status': 'unknown'}

        return {
            'halted': self._halted,
            'halt_reason': self._halt_reason,
            'total': len(self._invariants),
            'passing': passing,
            'failing': failing,
            'details': details,
        }

    def get_violations(self, limit: int = 20) -> List[dict]:
        """Get recent violations."""
        return self._violation_history[-limit:]

    def _record_violation(self, result: InvariantResult):
        """Record a violation for history."""
        self._violation_history.append(result.to_dict())
        if len(self._violation_history) > 1000:
            self._violation_history = self._violation_history[-1000:]


# ── CLI Demo ──────────────────────────────────────────────────

async def main():
    """Demo the invariant system."""
    system = InvariantSystem()

    print("\n🛡️ Moon Dev Runtime Invariants — Demo\n")

    # Test 1: Healthy state
    print("--- Test 1: Healthy state ---")
    state = {
        'portfolio_value': 1000,
        'exposure_pct': 40,
        'max_exposure_pct': 80,
        'position_count': 3,
        'max_positions': 5,
        'daily_pnl': -5,
        'max_daily_loss_usd': 25,
        'cash_pct': 30,
        'min_cash_buffer_pct': 20,
        'order_usd': 25,
        'max_position_usd': 100,
    }
    results = await system.check_all(state)
    for r in results:
        status = "✅" if r.status.value == "passing" else "❌"
        print(f"  {status} {r.name}: {r.message or 'OK'}")
    print(f"  Halted: {system.is_halted()}\n")

    # Test 2: Critical violation
    print("--- Test 2: Daily loss limit exceeded ---")
    state['daily_pnl'] = -30  # Over $25 limit
    results = await system.check_all(state)
    for r in results:
        if r.status.value == "failing":
            print(f"  ❌ {r.name}: {r.message}")
    print(f"  Halted: {system.is_halted()}\n")

    # Resume
    system.resume()
    print(f"  Halted after resume: {system.is_halted()}\n")

    # Status
    print("--- Status ---")
    status = system.get_status()
    print(f"  Total: {status['total']} | Passing: {status['passing']} | Failing: {status['failing']}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
