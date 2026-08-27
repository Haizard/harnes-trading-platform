"""
🎯 Moon Dev's Risk Presets — One-Click Risk Profiles
DSH Pattern: ctx.permissionPresets — one command changes ALL risk parameters.

Switch from aggressive to survival mode in one call during market crashes.

Usage:
    presets = RiskPresetManager()
    preset = presets.get('conservative')
    apply_to_risk_guard(risk_guard, preset)

    # Or switch via Event Bus
    await bus.emit('preset/switch', {'preset': 'survival'})
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, List, Callable
from termcolor import cprint


@dataclass
class RiskPreset:
    """A named risk configuration profile."""
    name: str
    description: str
    max_position_pct: float = 20.0        # Max % per position
    max_total_exposure_pct: float = 60.0   # Max total portfolio exposure %
    min_confidence: float = 0.6            # Min signal confidence to trade
    cash_buffer_pct: float = 25.0          # Min cash buffer %
    max_daily_loss_usd: float = 25.0       # Max daily loss USD
    slippage_bps: int = 199                # Max acceptable slippage
    stop_loss_pct: float = 0.05            # Stop-loss percentage
    take_profit_pct: float = 0.10          # Take-profit percentage
    max_positions: int = 5                 # Max number of concurrent positions
    min_order_usd: float = 1.0             # Minimum order size
    cooldown_after_close_seconds: int = 600  # Wait after closing a position

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# ── Built-in Presets ──────────────────────────────────────────

PRESETS: Dict[str, RiskPreset] = {
    'conservative': RiskPreset(
        name='conservative',
        description='Tight risk, capital preservation — survive first, profit second',
        max_position_pct=10,
        max_total_exposure_pct=40,
        min_confidence=0.8,
        cash_buffer_pct=40,
        max_daily_loss_usd=10,
        slippage_bps=100,
        stop_loss_pct=0.03,
        take_profit_pct=0.06,
        max_positions=3,
        min_order_usd=2.0,
        cooldown_after_close_seconds=1200,
    ),
    'moderate': RiskPreset(
        name='moderate',
        description='Balanced risk/reward — default for most conditions',
        max_position_pct=20,
        max_total_exposure_pct=60,
        min_confidence=0.6,
        cash_buffer_pct=25,
        max_daily_loss_usd=25,
        slippage_bps=150,
        stop_loss_pct=0.05,
        take_profit_pct=0.10,
        max_positions=5,
        min_order_usd=1.0,
        cooldown_after_close_seconds=600,
    ),
    'aggressive': RiskPreset(
        name='aggressive',
        description='High risk, high potential reward — for strong trends only',
        max_position_pct=30,
        max_total_exposure_pct=80,
        min_confidence=0.5,
        cash_buffer_pct=15,
        max_daily_loss_usd=50,
        slippage_bps=199,
        stop_loss_pct=0.08,
        take_profit_pct=0.15,
        max_positions=8,
        min_order_usd=0.5,
        cooldown_after_close_seconds=300,
    ),
    'survival': RiskPreset(
        name='survival',
        description='Market crash mode — protect capital at all costs',
        max_position_pct=5,
        max_total_exposure_pct=20,
        min_confidence=0.95,
        cash_buffer_pct=80,
        max_daily_loss_usd=5,
        slippage_bps=50,
        stop_loss_pct=0.02,
        take_profit_pct=0.03,
        max_positions=2,
        min_order_usd=5.0,
        cooldown_after_close_seconds=3600,
    ),
    'paper': RiskPreset(
        name='paper',
        description='Paper trading — no real risk, test strategies freely',
        max_position_pct=50,
        max_total_exposure_pct=100,
        min_confidence=0.3,
        cash_buffer_pct=0,
        max_daily_loss_usd=999999,
        slippage_bps=0,
        stop_loss_pct=0.10,
        take_profit_pct=0.20,
        max_positions=10,
        min_order_usd=0.1,
        cooldown_after_close_seconds=0,
    ),
}


# ── Preset Manager ────────────────────────────────────────────

class RiskPresetManager:
    """
    Manages risk presets and applies them to the system.

    DSH pattern: one command changes ALL risk parameters simultaneously.
    """

    def __init__(self):
        self._presets = PRESETS.copy()
        self._active_preset: Optional[str] = None
        self._listeners: List[Callable] = []

    def get(self, name: str) -> Optional[RiskPreset]:
        """Get a preset by name."""
        return self._presets.get(name)

    def list_presets(self) -> List[dict]:
        """List all available presets."""
        return [
            {
                'name': p.name,
                'description': p.description,
                'active': p.name == self._active_preset,
            }
            for p in self._presets.values()
        ]

    def register(self, preset: RiskPreset):
        """Register a custom preset."""
        self._presets[preset.name] = preset

    def on_preset_change(self, callback: Callable):
        """Register a callback for preset changes."""
        self._listeners.append(callback)

    def apply(self, name: str) -> Optional[RiskPreset]:
        """
        Switch to a preset. Returns the preset if found.
        Notifies all listeners of the change.
        """
        preset = self._presets.get(name)
        if not preset:
            cprint(f"❌ Preset '{name}' not found", "red")
            return None

        old = self._active_preset
        self._active_preset = name

        cprint(f"🎯 Risk Preset: {name.upper()} — {preset.description}", "white", "on_blue")
        cprint(f"   Max position: {preset.max_position_pct}% | "
               f"Max exposure: {preset.max_total_exposure_pct}% | "
               f"Min confidence: {preset.min_confidence:.0%}", "cyan")
        cprint(f"   Daily loss: ${preset.max_daily_loss_usd} | "
               f"Stop-loss: {preset.stop_loss_pct:.0%} | "
               f"Take-profit: {preset.take_profit_pct:.0%}", "cyan")

        # Notify listeners
        for listener in self._listeners:
            try:
                listener(preset, old)
            except Exception:
                pass

        return preset

    @property
    def active(self) -> Optional[str]:
        return self._active_preset

    @property
    def active_preset(self) -> Optional[RiskPreset]:
        if self._active_preset:
            return self._presets.get(self._active_preset)
        return None


# ── Integration: Apply Preset to Risk Guard ───────────────────

def apply_preset_to_guard(risk_guard, preset: RiskPreset):
    """
    Apply a RiskPreset to an existing RiskGuard instance.
    Updates all relevant config values.
    """
    if hasattr(risk_guard, 'config'):
        risk_guard.config['max_position_pct'] = preset.max_position_pct / 100
        risk_guard.config['max_position_usd'] = 1000  # Will be recalculated
        risk_guard.config['daily_loss_usd'] = preset.max_daily_loss_usd
        risk_guard.config['daily_loss_pct'] = 5.0
        risk_guard.config['min_order_usd'] = preset.min_order_usd
        risk_guard.config['max_concentration_pct'] = preset.max_total_exposure_pct / 100

    cprint(f"✅ Applied preset '{preset.name}' to Risk Guard", "green")


# ── CLI Demo ──────────────────────────────────────────────────

def main():
    """Demo the risk presets."""
    manager = RiskPresetManager()

    print("\n🎯 Moon Dev Risk Presets — Demo\n")

    print("Available presets:")
    for p in manager.list_presets():
        print(f"  • {p['name']}: {p['description']}")

    print("\n--- Switching to 'survival' ---")
    preset = manager.apply('survival')
    print(f"  Active: {manager.active}")

    print("\n--- Switching to 'aggressive' ---")
    preset = manager.apply('aggressive')
    print(f"  Active: {manager.active}")

    print("\n--- Switching to 'moderate' ---")
    preset = manager.apply('moderate')
    print(f"  Active: {manager.active}")


if __name__ == "__main__":
    main()
