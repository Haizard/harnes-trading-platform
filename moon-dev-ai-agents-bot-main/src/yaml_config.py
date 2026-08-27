"""
📁 Moon Dev's YAML Config System
DSH Pattern: Profile → Bundles → Patches. Layered YAML composition.

Switch between paper-trading, production, and backtest by changing ONE line.

Usage:
    config = load_config('paper-trading')
    print(config.risk.max_loss_usd)     # 100000 (paper mode)
    print(config.exchange.slippage_bps)  # 0 (paper mode)

    config = load_config('production')
    print(config.risk.max_loss_usd)     # 25 (tight risk)
    print(config.exchange.slippage_bps)  # 199 (real slippage)
"""

import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime


# ── Config Data Classes ───────────────────────────────────────

@dataclass
class ExchangeConfig:
    """Exchange connection and execution settings."""
    mode: str = "paper"              # paper, live, backtest
    slippage_bps: int = 199          # Slippage in basis points (500 = 5%)
    priority_fee: int = 100000       # Priority fee in lamports
    orders_per_open: int = 3         # Multiple orders for better fills
    tx_sleep: int = 30               # Sleep between transactions (seconds)

@dataclass
class RiskConfig:
    """Risk management settings."""
    max_loss_usd: float = 25.0
    max_gain_usd: float = 25.0
    max_loss_pct: float = 5.0
    max_gain_pct: float = 5.0
    use_percentage: bool = False     # True = use pct, False = use USD
    minimum_balance_usd: float = 50.0
    max_position_pct: float = 30.0   # Max % per position
    cash_percentage: float = 20.0    # Min % to keep in USDC
    use_ai_confirmation: bool = True
    max_loss_gain_check_hours: int = 12

@dataclass
class ModelConfig:
    """AI model settings."""
    primary: str = "gemini-2.0-flash"
    temperature: float = 0.7
    max_tokens: int = 4096
    fallback: str = "claude-3-haiku-20240307"

@dataclass
class TokenConfig:
    """A single monitored token."""
    address: str = ""
    name: str = ""
    enabled: bool = True

@dataclass
class TradingConfig:
    """Trading parameters."""
    usd_size: float = 25.0           # Position size
    max_usd_order_size: float = 3.0  # Max single order
    buy_under: float = 0.0946
    sell_over: float = 1.0
    sleep_after_close: int = 600
    sleep_between_runs: int = 15     # Minutes
    enable_strategies: bool = True
    strategy_min_confidence: float = 0.7

@dataclass
class DataConfig:
    """Data collection settings."""
    daysback: int = 3
    timeframe: str = "1H"
    save_ohlcv: bool = False

@dataclass
class WalletConfig:
    """Wallet addresses."""
    usdc_address: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    sol_address: str = "So111111111111111111111111111111111111111112"
    wallet_address: str = ""  # User's wallet

@dataclass
class MoonDevConfig:
    """Root configuration — all sections combined."""
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    data: DataConfig = field(default_factory=DataConfig)
    wallet: WalletConfig = field(default_factory=WalletConfig)
    tokens: List[TokenConfig] = field(default_factory=list)
    profile: str = "default"
    loaded_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def monitored_addresses(self) -> List[str]:
        """Get list of enabled token addresses."""
        return [t.address for t in self.tokens if t.enabled and t.address]

    @property
    def excluded_tokens(self) -> List[str]:
        """Get list of excluded token addresses."""
        return [self.wallet.usdc_address, self.wallet.sol_address]

    def to_dict(self) -> dict:
        """Convert to dict for serialization."""
        return {
            'profile': self.profile,
            'loaded_at': self.loaded_at,
            'exchange': self.exchange.__dict__,
            'risk': self.risk.__dict__,
            'model': self.model.__dict__,
            'trading': self.trading.__dict__,
            'data': self.data.__dict__,
            'wallet': self.wallet.__dict__,
            'tokens': [t.__dict__ for t in self.tokens],
        }


# ── Profile Loader ────────────────────────────────────────────

class ProfileLoader:
    """
    DSH-style layered config loader.

    Config resolution order (later wins):
    1. Default values (dataclass defaults)
    2. profiles/default/config.yml
    3. profiles/{profile_name}/config.yml
    4. Environment variable overrides (MOONDEV_RISK__MAX_LOSS_USD=50)
    """

    def __init__(self, profiles_dir: str = None):
        if profiles_dir is None:
            profiles_dir = os.path.join(os.path.dirname(__file__), '..', 'profiles')
        self.profiles_dir = Path(profiles_dir)

    def load(self, profile_name: str = 'default') -> MoonDevConfig:
        """Load a profile config, merging layers."""
        # Layer 1: Default dataclass values
        config = MoonDevConfig()

        # Layer 2: Default YAML
        base = self._load_yaml('default/config.yml')
        self._apply_dict(config, base)

        # Layer 3: Profile YAML
        if profile_name != 'default':
            profile = self._load_yaml(f'{profile_name}/config.yml')
            self._apply_dict(config, profile)

        # Layer 4: Environment overrides
        self._apply_env_overrides(config)

        config.profile = profile_name
        config.loaded_at = datetime.utcnow().isoformat()

        return config

    def _load_yaml(self, relative_path: str) -> dict:
        """Load a YAML file, return empty dict if not found."""
        full = self.profiles_dir / relative_path
        if not full.exists():
            return {}
        try:
            with open(full, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}

    def _apply_dict(self, config: MoonDevConfig, data: dict):
        """Apply a dict to a config object, section by section."""
        if not data:
            return

        # Exchange
        if 'exchange' in data:
            self._update_dataclass(config.exchange, data['exchange'])

        # Risk
        if 'risk' in data:
            self._update_dataclass(config.risk, data['risk'])

        # Model
        if 'model' in data:
            self._update_dataclass(config.model, data['model'])

        # Trading
        if 'trading' in data:
            self._update_dataclass(config.trading, data['trading'])

        # Data
        if 'data' in data:
            self._update_dataclass(config.data, data['data'])

        # Wallet
        if 'wallet' in data:
            self._update_dataclass(config.wallet, data['wallet'])

        # Tokens
        if 'tokens' in data:
            config.tokens = []
            for t in data['tokens']:
                if isinstance(t, dict):
                    config.tokens.append(TokenConfig(
                        address=t.get('address', ''),
                        name=t.get('name', ''),
                        enabled=t.get('enabled', True),
                    ))
                elif isinstance(t, str):
                    # Simple address string
                    config.tokens.append(TokenConfig(address=t, name=''))

    def _update_dataclass(self, dc, data: dict):
        """Update a dataclass fields from a dict."""
        for key, value in data.items():
            if hasattr(dc, key):
                setattr(dc, key, value)

    def _apply_env_overrides(self, config: MoonDevConfig):
        """
        Apply environment variable overrides.

        Format: MOONDEV_{SECTION}__{KEY}=value
        Example: MOONDEV_RISK__MAX_LOSS_USD=50
        """
        prefix = "MOONDEV_"
        for env_key, env_value in os.environ.items():
            if not env_key.startswith(prefix):
                continue

            parts = env_key[len(prefix):].lower().split('__', 1)
            if len(parts) != 2:
                continue

            section_name, key_name = parts
            section_obj = getattr(config, section_name, None)
            if section_obj is None:
                continue

            # Type coercion
            current_value = getattr(section_obj, key_name, None)
            if current_value is None:
                continue

            try:
                if isinstance(current_value, bool):
                    setattr(section_obj, key_name, env_value.lower() in ('true', '1', 'yes'))
                elif isinstance(current_value, int):
                    setattr(section_obj, key_name, int(env_value))
                elif isinstance(current_value, float):
                    setattr(section_obj, key_name, float(env_value))
                else:
                    setattr(section_obj, key_name, env_value)
            except (ValueError, TypeError):
                pass  # Skip invalid values


# ── Convenience Functions ─────────────────────────────────────

def load_config(profile: str = 'default', profiles_dir: str = None) -> MoonDevConfig:
    """
    Load a Moon Dev config profile.

    Args:
        profile: Profile name ('default', 'paper-trading', 'production', 'backtest')
        profiles_dir: Path to profiles directory (optional)

    Returns:
        Fully merged MoonDevConfig
    """
    loader = ProfileLoader(profiles_dir=profiles_dir)
    return loader.load(profile)


def create_default_profiles(base_dir: str = None):
    """
    Create the default profile YAML files.
    Run this once to set up the profiles directory.
    """
    if base_dir is None:
        base_dir = os.path.join(os.path.dirname(__file__), '..', 'profiles')

    profiles = {
        'default': {
            'exchange': {
                'mode': 'paper',
                'slippage_bps': 199,
                'priority_fee': 100000,
                'orders_per_open': 3,
                'tx_sleep': 30,
            },
            'risk': {
                'max_loss_usd': 25,
                'max_gain_usd': 25,
                'max_loss_pct': 5,
                'max_gain_pct': 5,
                'use_percentage': False,
                'minimum_balance_usd': 50,
                'max_position_pct': 30,
                'cash_percentage': 20,
                'use_ai_confirmation': True,
            },
            'model': {
                'primary': 'gemini-2.0-flash',
                'temperature': 0.7,
                'max_tokens': 4096,
            },
            'trading': {
                'usd_size': 25,
                'max_usd_order_size': 3,
                'sleep_between_runs': 15,
                'enable_strategies': True,
            },
            'data': {
                'daysback': 3,
                'timeframe': '1H',
                'save_ohlcv': False,
            },
            'tokens': [
                {'address': '9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump', 'name': 'FART'},
                {'address': 'HeLp6NuQkmYB4pYWo2zYs22mESHXPQYzXbB8n4V98jwC', 'name': 'AI16Z'},
            ],
        },
        'paper-trading': {
            'exchange': {
                'mode': 'paper',
                'slippage_bps': 0,
            },
            'risk': {
                'max_loss_usd': 100000,
                'max_gain_usd': 100000,
                'use_ai_confirmation': False,
            },
            'model': {
                'temperature': 0.3,
            },
        },
        'production': {
            'exchange': {
                'mode': 'live',
                'slippage_bps': 199,
            },
            'risk': {
                'max_loss_usd': 25,
                'max_gain_usd': 25,
                'use_ai_confirmation': True,
            },
            'model': {
                'temperature': 0.3,
            },
        },
        'backtest': {
            'exchange': {
                'mode': 'backtest',
            },
            'risk': {
                'max_loss_usd': 100,
                'max_gain_usd': 100,
            },
        },
    }

    for profile_name, data in profiles.items():
        profile_dir = os.path.join(base_dir, profile_name)
        os.makedirs(profile_dir, exist_ok=True)
        config_path = os.path.join(profile_dir, 'config.yml')

        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        print(f"✅ Created {config_path}")


# ── CLI Interface ─────────────────────────────────────────────

def main():
    """Demo the config system."""
    print("\n📁 Moon Dev YAML Config System — Demo\n")

    # Create default profiles
    create_default_profiles()

    # Load each profile
    for profile_name in ['default', 'paper-trading', 'production', 'backtest']:
        print(f"\n--- Profile: {profile_name} ---")
        config = load_config(profile_name)
        print(f"  Exchange mode:    {config.exchange.mode}")
        print(f"  Slippage:         {config.exchange.slippage_bps} bps")
        print(f"  Max loss:         ${config.risk.max_loss_usd}")
        print(f"  AI confirmation:  {config.risk.use_ai_confirmation}")
        print(f"  Model:            {config.model.primary}")
        print(f"  Temperature:      {config.model.temperature}")
        print(f"  Tokens:           {[t.name for t in config.tokens]}")


if __name__ == "__main__":
    main()
