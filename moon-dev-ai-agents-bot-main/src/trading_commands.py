"""
⌨️ Moon Dev's Trading Commands — Manual Intervention
DSH Pattern: ctx.commands — register commands that dispatch without a model turn.

Allows manual overrides and emergency actions:
- Emergency sell all positions
- Override risk limits temporarily
- Force a specific trade
- Check system status

Usage:
    registry = CommandRegistry()
    registry.register('status', cmd_status, description='Show system status')
    registry.register('sell_all', cmd_sell_all, description='Emergency sell all')

    result = await registry.execute('status')
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Optional, Any
from enum import Enum
from termcolor import cprint


class CommandResult(str, Enum):
    """Command execution result."""
    SUCCESS = "success"
    FAILED = "failed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass
class CommandOutput:
    """Result of a command execution."""
    command: str
    status: CommandResult
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    executed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            'command': self.command,
            'status': self.status.value,
            'message': self.message,
            'data': self.data,
            'executed_at': self.executed_at,
        }


@dataclass
class Command:
    """A registered command."""
    name: str
    fn: Callable
    description: str = ""
    requires_risk_check: bool = True  # Whether to run risk invariants before executing
    enabled: bool = True


class CommandRegistry:
    """
    DSH-style command registry.

    Commands are registered functions that can be executed on demand.
    They bypass the LLM — direct manual intervention.
    """

    def __init__(self):
        self._commands: Dict[str, Command] = {}
        self._history: List[CommandOutput] = []
        self._invariants = None  # Optional InvariantSystem reference
        self._risk_guard = None  # Optional RiskGuard reference

    def set_invariants(self, invariants):
        """Set the invariant system for pre-execution checks."""
        self._invariants = invariants

    def set_risk_guard(self, risk_guard):
        """Set the risk guard for order validation."""
        self._risk_guard = risk_guard

    def register(self, name: str, fn: Callable, description: str = "",
                 requires_risk_check: bool = True):
        """Register a command."""
        self._commands[name] = Command(
            name=name, fn=fn, description=description,
            requires_risk_check=requires_risk_check,
        )

    def list_commands(self) -> List[dict]:
        """List all registered commands."""
        return [
            {
                'name': cmd.name,
                'description': cmd.description,
                'enabled': cmd.enabled,
            }
            for cmd in self._commands.values()
        ]

    async def execute(self, name: str, args: dict = None) -> CommandOutput:
        """
        Execute a command.

        1. Check invariants (if required)
        2. Run the command
        3. Record result
        """
        cmd = self._commands.get(name)
        if not cmd:
            return CommandOutput(
                command=name,
                status=CommandResult.UNKNOWN,
                message=f"Command '{name}' not found",
            )

        if not cmd.enabled:
            return CommandOutput(
                command=name,
                status=CommandResult.REJECTED,
                message=f"Command '{name}' is disabled",
            )

        # Pre-execution invariant check
        if cmd.requires_risk_check and self._invariants:
            if self._invariants.is_halted():
                return CommandOutput(
                    command=name,
                    status=CommandResult.REJECTED,
                    message="System halted — resume before executing commands",
                )

        # Execute
        try:
            result = await cmd.fn(args or {})
            if isinstance(result, CommandOutput):
                output = result
            elif isinstance(result, dict):
                output = CommandOutput(
                    command=name,
                    status=CommandResult.SUCCESS,
                    data=result,
                    message=result.get('message', 'Executed'),
                )
            else:
                output = CommandOutput(
                    command=name,
                    status=CommandResult.SUCCESS,
                    message=str(result) if result else "Executed",
                )
        except Exception as e:
            output = CommandOutput(
                command=name,
                status=CommandResult.FAILED,
                message=f"Error: {str(e)}",
            )

        self._history.append(output)
        return output

    def get_history(self, limit: int = 20) -> List[dict]:
        """Get command execution history."""
        return [h.to_dict() for h in self._history[-limit:]]


# ── Built-in Commands ─────────────────────────────────────────

def create_default_commands(order_executor=None, session_log=None,
                           benchmark_tracker=None, risk_guard=None,
                           invariants=None) -> CommandRegistry:
    """Create a CommandRegistry with built-in commands."""
    registry = CommandRegistry()
    registry.set_invariants(invariants)
    registry.set_risk_guard(risk_guard)

    # Status command
    async def cmd_status(args):
        """Show system status."""
        status = {
            'invariants': invariants.get_status() if invariants else 'not configured',
            'risk_guard': 'active' if risk_guard else 'not configured',
            'order_executor': 'active' if order_executor else 'not configured',
            'session_log': 'active' if session_log else 'not configured',
            'benchmark': 'active' if benchmark_tracker else 'not configured',
        }
        return {'message': 'System status', **status}

    registry.register('status', cmd_status,
                     description='Show system status',
                     requires_risk_check=False)

    # Sell all command
    async def cmd_sell_all(args):
        """Emergency sell all positions."""
        if not order_executor:
            return {'message': 'Order executor not configured'}

        from src import nice_funcs as n
        from src.config import MONITORED_TOKENS, EXCLUDED_TOKENS

        results = []
        for token in MONITORED_TOKENS:
            if token in EXCLUDED_TOKENS:
                continue
            try:
                balance = n.get_token_balance_usd(token)
                if balance > 0:
                    result = await order_executor.sell(token, balance, source='command_sell_all')
                    results.append({
                        'token': token[:12],
                        'amount': balance,
                        'executed': result.executed,
                    })
            except Exception as e:
                results.append({
                    'token': token[:12],
                    'error': str(e),
                })

        return {'message': f'Sell all: {len(results)} tokens processed', 'results': results}

    registry.register('sell_all', cmd_sell_all,
                     description='Emergency sell all positions',
                     requires_risk_check=False)  # Emergency — bypass risk check

    # Emergency stop command
    async def cmd_emergency_stop(args):
        """Halt all trading immediately."""
        if invariants:
            from src.invariants import InvariantResult, InvariantStatus, Severity
            invariants._halted = True
            invariants._halt_reason = "Manual emergency stop"
        return {'message': 'Trading halted via emergency stop'}

    registry.register('emergency_stop', cmd_emergency_stop,
                     description='Halt all trading immediately',
                     requires_risk_check=False)

    # Resume command
    async def cmd_resume(args):
        """Resume trading after halt."""
        if invariants:
            invariants.resume()
        return {'message': 'Trading resumed'}

    registry.register('resume', cmd_resume,
                     description='Resume trading after halt',
                     requires_risk_check=False)

    # Benchmark command
    async def cmd_benchmark(args):
        """Show benchmark report."""
        if not benchmark_tracker:
            return {'message': 'Benchmark tracker not configured'}
        report = await benchmark_tracker.weekly_report()
        return {
            'message': 'Benchmark report',
            'bot_return': f'{report.bot_return_pct:+.2f}%',
            'btc_return': f'{report.btc_return_pct:+.2f}%',
            'alpha': f'{report.alpha_vs_btc:+.2f}%',
            'verdict': report.verdict,
        }

    registry.register('benchmark', cmd_benchmark,
                     description='Show weekly benchmark report',
                     requires_risk_check=False)

    # Risk status command
    async def cmd_risk_status(args):
        """Show current risk guard status."""
        if not risk_guard:
            return {'message': 'Risk guard not configured'}
        stats = risk_guard.get_rejection_stats() if hasattr(risk_guard, 'get_rejection_stats') else {}
        return {
            'message': 'Risk guard status',
            'config': risk_guard.config if hasattr(risk_guard, 'config') else {},
            'rejections': stats,
        }

    registry.register('risk_status', cmd_risk_status,
                     description='Show risk guard configuration and stats',
                     requires_risk_check=False)

    return registry


# ── CLI Demo ──────────────────────────────────────────────────

async def main():
    """Demo the command registry."""
    registry = CommandRegistry()

    # Register a test command
    async def hello(args):
        return {'message': 'Hello from Moon Dev!', 'args': args}

    registry.register('hello', hello, description='Say hello')

    print("\n⌨️ Moon Dev Trading Commands — Demo\n")

    print("Available commands:")
    for cmd in registry.list_commands():
        print(f"  • {cmd['name']}: {cmd['description']}")

    print("\n--- Execute 'hello' ---")
    result = await registry.execute('hello', {'name': 'Trader'})
    print(f"  Status: {result.status.value}")
    print(f"  Message: {result.message}")
    print(f"  Data: {result.data}")

    print("\n--- Execute unknown command ---")
    result = await registry.execute('nonexistent')
    print(f"  Status: {result.status.value}")
    print(f"  Message: {result.message}")

    print("\n--- History ---")
    for h in registry.get_history():
        print(f"  [{h['status']}] {h['command']}: {h['message']}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
