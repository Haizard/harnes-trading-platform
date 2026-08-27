"""
Tests for Risk Presets, Runtime Invariants, and Trading Commands
"""

import pytest
import asyncio
from src.risk_presets import RiskPresetManager, RiskPreset, PRESETS, apply_preset_to_guard
from src.invariants import (
    InvariantSystem, InvariantResult, InvariantStatus, Severity,
    check_portfolio_not_negative, check_max_exposure, check_max_positions,
    check_daily_loss_limit, check_cash_buffer, check_order_size,
)
from src.trading_commands import CommandRegistry, CommandOutput, CommandResult, create_default_commands


# ── Risk Presets Tests ────────────────────────────────────────

class TestRiskPresets:
    def test_all_presets_exist(self):
        assert 'conservative' in PRESETS
        assert 'moderate' in PRESETS
        assert 'aggressive' in PRESETS
        assert 'survival' in PRESETS
        assert 'paper' in PRESETS

    def test_preset_values(self):
        p = PRESETS['conservative']
        assert p.max_position_pct == 10
        assert p.min_confidence == 0.8
        assert p.max_daily_loss_usd == 10

    def test_preset_to_dict(self):
        d = PRESETS['moderate'].to_dict()
        assert 'name' in d
        assert 'max_position_pct' in d

    def test_preset_manager_get(self):
        manager = RiskPresetManager()
        preset = manager.get('aggressive')
        assert preset is not None
        assert preset.name == 'aggressive'

    def test_preset_manager_list(self):
        manager = RiskPresetManager()
        presets = manager.list_presets()
        assert len(presets) >= 5

    def test_preset_manager_apply(self):
        manager = RiskPresetManager()
        preset = manager.apply('survival')
        assert preset is not None
        assert manager.active == 'survival'

    def test_preset_manager_apply_nonexistent(self):
        manager = RiskPresetManager()
        preset = manager.apply('nonexistent')
        assert preset is None

    def test_preset_manager_register_custom(self):
        manager = RiskPresetManager()
        custom = RiskPreset(
            name='custom', description='Custom preset',
            max_position_pct=15, min_confidence=0.7,
        )
        manager.register(custom)
        assert manager.get('custom') is not None

    def test_preset_change_listener(self):
        manager = RiskPresetManager()
        changes = []
        manager.on_preset_change(lambda p, o: changes.append((p.name, o)))
        manager.apply('aggressive')
        assert len(changes) == 1
        assert changes[0] == ('aggressive', None)

    def test_preset_risk_ranking(self):
        """Conservative should be tighter than aggressive."""
        c = PRESETS['conservative']
        a = PRESETS['aggressive']
        assert c.max_position_pct < a.max_position_pct
        assert c.min_confidence > a.min_confidence
        assert c.max_daily_loss_usd < a.max_daily_loss_usd


# ── Runtime Invariants Tests ──────────────────────────────────

class TestInvariantSystem:
    @pytest.fixture
    def system(self):
        return InvariantSystem()

    @pytest.fixture
    def healthy_state(self):
        return {
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

    @pytest.mark.asyncio
    async def test_healthy_state_passes(self, system, healthy_state):
        results = await system.check_all(healthy_state)
        assert all(r.status == InvariantStatus.PASSING for r in results)

    @pytest.mark.asyncio
    async def test_negative_portfolio_halts(self, system, healthy_state):
        healthy_state['portfolio_value'] = -100
        results = await system.check_all(healthy_state)
        failures = [r for r in results if r.status == InvariantStatus.FAILING]
        assert len(failures) >= 1
        assert system.is_halted()

    @pytest.mark.asyncio
    async def test_daily_loss_halts(self, system, healthy_state):
        healthy_state['daily_pnl'] = -30  # Over $25 limit
        results = await system.check_all(healthy_state)
        failures = [r for r in results if r.status == InvariantStatus.FAILING]
        assert len(failures) >= 1

    @pytest.mark.asyncio
    async def test_over_exposure_fails(self, system, healthy_state):
        healthy_state['exposure_pct'] = 90  # Over 80% limit
        results = await system.check_all(healthy_state)
        failures = [r for r in results if r.status == InvariantStatus.FAILING]
        assert len(failures) >= 1

    @pytest.mark.asyncio
    async def test_resume_clears_halt(self, system, healthy_state):
        healthy_state['portfolio_value'] = -100
        await system.check_all(healthy_state)
        assert system.is_halted()
        system.resume()
        assert not system.is_halted()

    @pytest.mark.asyncio
    async def test_check_single(self, system, healthy_state):
        result = await system.check_single('max_exposure', healthy_state)
        assert result is not None
        assert result.status == InvariantStatus.PASSING

    @pytest.mark.asyncio
    async def test_disable_invariant(self, system, healthy_state):
        system.disable('portfolio_not_negative')
        healthy_state['portfolio_value'] = -100
        results = await system.check_all(healthy_state)
        # portfolio_not_negative should not appear as failing
        failures = [r for r in results if r.status == InvariantStatus.FAILING
                   and r.name == 'portfolio_not_negative']
        assert len(failures) == 0

    @pytest.mark.asyncio
    async def test_get_status(self, system, healthy_state):
        await system.check_all(healthy_state)
        status = system.get_status()
        assert status['total'] > 0
        assert status['halted'] is False

    @pytest.mark.asyncio
    async def test_violation_history(self, system, healthy_state):
        healthy_state['portfolio_value'] = -100
        await system.check_all(healthy_state)
        violations = system.get_violations()
        assert len(violations) > 0


# ── Individual Invariant Functions ─────────────────────────────

class TestInvariantFunctions:
    @pytest.mark.asyncio
    async def test_portfolio_positive(self):
        result = await check_portfolio_not_negative({'portfolio_value': 100})
        assert result.status == InvariantStatus.PASSING

    @pytest.mark.asyncio
    async def test_portfolio_negative(self):
        result = await check_portfolio_not_negative({'portfolio_value': -50})
        assert result.status == InvariantStatus.FAILING

    @pytest.mark.asyncio
    async def test_exposure_ok(self):
        result = await check_max_exposure({'exposure_pct': 50, 'max_exposure_pct': 80})
        assert result.status == InvariantStatus.PASSING

    @pytest.mark.asyncio
    async def test_exposure_over(self):
        result = await check_max_exposure({'exposure_pct': 90, 'max_exposure_pct': 80})
        assert result.status == InvariantStatus.FAILING

    @pytest.mark.asyncio
    async def test_positions_ok(self):
        result = await check_max_positions({'position_count': 3, 'max_positions': 5})
        assert result.status == InvariantStatus.PASSING

    @pytest.mark.asyncio
    async def test_positions_over(self):
        result = await check_max_positions({'position_count': 6, 'max_positions': 5})
        assert result.status == InvariantStatus.FAILING


# ── Trading Commands Tests ────────────────────────────────────

class TestTradingCommands:
    @pytest.fixture
    def registry(self):
        return CommandRegistry()

    @pytest.mark.asyncio
    async def test_register_and_list(self, registry):
        async def test_cmd(args):
            return {'message': 'test'}
        registry.register('test', test_cmd, description='Test command')
        commands = registry.list_commands()
        assert len(commands) == 1
        assert commands[0]['name'] == 'test'

    @pytest.mark.asyncio
    async def test_execute_command(self, registry):
        async def hello(args):
            return {'message': f"Hello {args.get('name', 'World')}"}
        registry.register('hello', hello)
        result = await registry.execute('hello', {'name': 'Moon'})
        assert result.status == CommandResult.SUCCESS
        assert 'Hello Moon' in result.message

    @pytest.mark.asyncio
    async def test_execute_unknown(self, registry):
        result = await registry.execute('nonexistent')
        assert result.status == CommandResult.UNKNOWN

    @pytest.mark.asyncio
    async def test_execute_disabled(self, registry):
        async def test_cmd(args):
            return {'message': 'test'}
        cmd = registry.register('test', test_cmd)
        # Manually disable
        registry._commands['test'].enabled = False
        result = await registry.execute('test')
        assert result.status == CommandResult.REJECTED

    @pytest.mark.asyncio
    async def test_execute_with_error(self, registry):
        async def bad_cmd(args):
            raise RuntimeError("Something broke")
        registry.register('bad', bad_cmd)
        result = await registry.execute('bad')
        assert result.status == CommandResult.FAILED
        assert 'Something broke' in result.message

    @pytest.mark.asyncio
    async def test_history(self, registry):
        async def test_cmd(args):
            return {'message': 'ok'}
        registry.register('test', test_cmd)
        await registry.execute('test')
        await registry.execute('test')
        history = registry.get_history()
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_halted_blocks_commands(self, registry):
        from src.invariants import InvariantSystem
        system = InvariantSystem()
        registry.set_invariants(system)
        system._halted = True
        system._halt_reason = "Test halt"

        async def test_cmd(args):
            return {'message': 'ok'}
        registry.register('test', test_cmd, requires_risk_check=True)
        result = await registry.execute('test')
        assert result.status == CommandResult.REJECTED
        assert 'halted' in result.message.lower()

    @pytest.mark.asyncio
    async def test_emergency_stop_bypasses_risk(self, registry):
        from src.invariants import InvariantSystem
        system = InvariantSystem()
        registry.set_invariants(system)

        async def emergency(args):
            system._halted = True
            return {'message': 'halted'}
        registry.register('emergency', emergency, requires_risk_check=False)

        result = await registry.execute('emergency')
        assert result.status == CommandResult.SUCCESS
        assert system.is_halted()


# ── Integration Tests ─────────────────────────────────────────

class TestIntegration:
    def test_apply_preset_to_guard(self):
        """Test applying a preset to a mock risk guard."""
        class MockGuard:
            def __init__(self):
                self.config = {}

        guard = MockGuard()
        preset = PRESETS['conservative']
        apply_preset_to_guard(guard, preset)

        assert guard.config['max_position_pct'] == 0.10  # 10% / 100
        assert guard.config['daily_loss_usd'] == 10

    def test_create_default_commands(self):
        registry = create_default_commands()
        commands = registry.list_commands()
        names = [c['name'] for c in commands]
        assert 'status' in names
        assert 'sell_all' in names
        assert 'emergency_stop' in names
        assert 'resume' in names
        assert 'benchmark' in names
