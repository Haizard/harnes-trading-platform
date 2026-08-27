"""
Tests for Moon Dev's YAML Config System
"""

import pytest
import os
import tempfile
import yaml
from src.yaml_config import (
    MoonDevConfig, ExchangeConfig, RiskConfig, ModelConfig,
    TradingConfig, DataConfig, WalletConfig, TokenConfig,
    ProfileLoader, load_config, create_default_profiles,
)


# ── Fixtures ───────────────────────────────────────────────────

@pytest.fixture
def profiles_dir(tmp_path):
    """Create a temporary profiles directory with test configs."""
    base = tmp_path / "profiles"

    # Default profile
    default_dir = base / "default"
    default_dir.mkdir(parents=True)
    (default_dir / "config.yml").write_text(yaml.dump({
        'exchange': {'mode': 'paper', 'slippage_bps': 100},
        'risk': {'max_loss_usd': 50, 'use_ai_confirmation': False},
        'model': {'primary': 'test-model', 'temperature': 0.5},
    }))

    # Paper trading profile
    paper_dir = base / "paper-trading"
    paper_dir.mkdir(parents=True)
    (paper_dir / "config.yml").write_text(yaml.dump({
        'exchange': {'mode': 'paper', 'slippage_bps': 0},
        'risk': {'max_loss_usd': 999999},
    }))

    # Production profile
    prod_dir = base / "production"
    prod_dir.mkdir(parents=True)
    (prod_dir / "config.yml").write_text(yaml.dump({
        'exchange': {'mode': 'live', 'slippage_bps': 199},
        'risk': {'max_loss_usd': 10, 'use_ai_confirmation': True},
        'model': {'temperature': 0.2},
    }))

    # Backtest profile
    bt_dir = base / "backtest"
    bt_dir.mkdir(parents=True)
    (bt_dir / "config.yml").write_text(yaml.dump({
        'exchange': {'mode': 'backtest'},
        'risk': {'max_loss_usd': 200},
    }))

    # Tokens profile
    tokens_dir = base / "with-tokens"
    tokens_dir.mkdir(parents=True)
    (tokens_dir / "config.yml").write_text(yaml.dump({
        'tokens': [
            {'address': 'AAA', 'name': 'TokenA'},
            {'address': 'BBB', 'name': 'TokenB', 'enabled': False},
            'CCC',  # Simple string format
        ],
    }))

    return str(base)


# ── Test Data Classes ─────────────────────────────────────────

class TestConfigs:
    def test_exchange_defaults(self):
        config = ExchangeConfig()
        assert config.mode == "paper"
        assert config.slippage_bps == 199

    def test_risk_defaults(self):
        config = RiskConfig()
        assert config.max_loss_usd == 25.0
        assert config.use_ai_confirmation is True

    def test_model_defaults(self):
        config = ModelConfig()
        assert config.primary == "qwen.qwen3-coder-next"
        assert config.temperature == 0.3

    def test_token_config(self):
        t = TokenConfig(address="AAA", name="Test", enabled=True)
        assert t.address == "AAA"
        assert t.enabled is True


# ── Test MoonDevConfig ────────────────────────────────────────

class TestMoonDevConfig:
    def test_defaults(self):
        config = MoonDevConfig()
        assert config.profile == "default"
        assert config.exchange.mode == "paper"
        assert config.risk.max_loss_usd == 25.0

    def test_monitored_addresses(self):
        config = MoonDevConfig()
        config.tokens = [
            TokenConfig(address="AAA", enabled=True),
            TokenConfig(address="BBB", enabled=False),
            TokenConfig(address="CCC", enabled=True),
        ]
        assert config.monitored_addresses == ["AAA", "CCC"]

    def test_excluded_tokens(self):
        config = MoonDevConfig()
        assert len(config.excluded_tokens) == 2
        assert config.wallet.usdc_address in config.excluded_tokens

    def test_to_dict(self):
        config = MoonDevConfig()
        d = config.to_dict()
        assert 'exchange' in d
        assert 'risk' in d
        assert 'profile' in d


# ── Test ProfileLoader ────────────────────────────────────────

class TestProfileLoader:
    def test_load_default(self, profiles_dir):
        loader = ProfileLoader(profiles_dir=profiles_dir)
        config = loader.load('default')
        assert config.exchange.mode == 'paper'
        assert config.exchange.slippage_bps == 100

    def test_load_paper_trading(self, profiles_dir):
        loader = ProfileLoader(profiles_dir=profiles_dir)
        config = loader.load('paper-trading')
        assert config.exchange.mode == 'paper'
        assert config.exchange.slippage_bps == 0  # Overridden
        assert config.risk.max_loss_usd == 999999  # Overridden

    def test_load_production(self, profiles_dir):
        loader = ProfileLoader(profiles_dir=profiles_dir)
        config = loader.load('production')
        assert config.exchange.mode == 'live'
        assert config.risk.max_loss_usd == 10
        assert config.model.temperature == 0.2

    def test_load_backtest(self, profiles_dir):
        loader = ProfileLoader(profiles_dir=profiles_dir)
        config = loader.load('backtest')
        assert config.exchange.mode == 'backtest'
        assert config.risk.max_loss_usd == 200

    def test_profile_name_set(self, profiles_dir):
        loader = ProfileLoader(profiles_dir=profiles_dir)
        config = loader.load('production')
        assert config.profile == 'production'

    def test_missing_profile_uses_defaults(self, profiles_dir):
        loader = ProfileLoader(profiles_dir=profiles_dir)
        config = loader.load('nonexistent')
        # Should use default values, not crash
        assert config.exchange.mode == 'paper'  # Default

    def test_empty_profiles_dir(self, tmp_path):
        loader = ProfileLoader(profiles_dir=str(tmp_path / "empty"))
        config = loader.load('default')
        # Should use dataclass defaults
        assert config.risk.max_loss_usd == 25.0


# ── Test Token Loading ───────────────────────────────────────

class TestTokenLoading:
    def test_dict_tokens(self, profiles_dir):
        loader = ProfileLoader(profiles_dir=profiles_dir)
        config = loader.load('with-tokens')
        assert len(config.tokens) == 3
        assert config.tokens[0].address == 'AAA'
        assert config.tokens[0].name == 'TokenA'
        assert config.tokens[1].enabled is False

    def test_string_token(self, profiles_dir):
        loader = ProfileLoader(profiles_dir=profiles_dir)
        config = loader.load('with-tokens')
        assert config.tokens[2].address == 'CCC'
        assert config.tokens[2].name == ''

    def test_monitored_addresses_filtered(self, profiles_dir):
        loader = ProfileLoader(profiles_dir=profiles_dir)
        config = loader.load('with-tokens')
        # Only enabled tokens
        assert config.monitored_addresses == ['AAA', 'CCC']


# ── Test Environment Overrides ───────────────────────────────

class TestEnvOverrides:
    def test_env_override(self, profiles_dir):
        os.environ['MOONDEV_RISK__MAX_LOSS_USD'] = '42'
        try:
            loader = ProfileLoader(profiles_dir=profiles_dir)
            config = loader.load('default')
            assert config.risk.max_loss_usd == 42
        finally:
            del os.environ['MOONDEV_RISK__MAX_LOSS_USD']

    def test_env_override_bool(self, profiles_dir):
        os.environ['MOONDEV_RISK__USE_AI_CONFIRMATION'] = 'false'
        try:
            loader = ProfileLoader(profiles_dir=profiles_dir)
            config = loader.load('default')
            assert config.risk.use_ai_confirmation is False
        finally:
            del os.environ['MOONDEV_RISK__USE_AI_CONFIRMATION']

    def test_env_override_float(self, profiles_dir):
        os.environ['MOONDEV_MODEL__TEMPERATURE'] = '0.1'
        try:
            loader = ProfileLoader(profiles_dir=profiles_dir)
            config = loader.load('default')
            assert config.model.temperature == 0.1
        finally:
            del os.environ['MOONDEV_MODEL__TEMPERATURE']

    def test_env_override_invalid_ignored(self, profiles_dir):
        os.environ['MOONDEV_RISK__MAX_LOSS_USD'] = 'not-a-number'
        try:
            loader = ProfileLoader(profiles_dir=profiles_dir)
            config = loader.load('default')
            # Should keep default value
            assert config.risk.max_loss_usd == 50  # From default.yml
        finally:
            del os.environ['MOONDEV_RISK__MAX_LOSS_USD']


# ── Test load_config Convenience ──────────────────────────────

class TestLoadConfig:
    def test_load_config(self, profiles_dir):
        config = load_config('production', profiles_dir=profiles_dir)
        assert config.exchange.mode == 'live'
        assert config.profile == 'production'


# ── Test create_default_profiles ──────────────────────────────

class TestCreateDefaultProfiles:
    def test_creates_files(self, tmp_path):
        create_default_profiles(str(tmp_path / "profiles"))

        assert (tmp_path / "profiles" / "default" / "config.yml").exists()
        assert (tmp_path / "profiles" / "paper-trading" / "config.yml").exists()
        assert (tmp_path / "profiles" / "production" / "config.yml").exists()
        assert (tmp_path / "profiles" / "backtest" / "config.yml").exists()

    def test_created_configs_loadable(self, tmp_path):
        create_default_profiles(str(tmp_path / "profiles"))

        for name in ['default', 'paper-trading', 'production', 'backtest']:
            config = load_config(name, profiles_dir=str(tmp_path / "profiles"))
            assert config.profile == name
            assert config.exchange.mode in ('paper', 'live', 'backtest')


# ── Test Profile Differences ──────────────────────────────────

class TestProfileDifferences:
    def test_paper_vs_production(self, profiles_dir):
        loader = ProfileLoader(profiles_dir=profiles_dir)
        paper = loader.load('paper-trading')
        prod = loader.load('production')

        # Paper has unlimited loss, production is tight
        assert paper.risk.max_loss_usd > prod.risk.max_loss_usd
        # Paper has no slippage, production has real slippage
        assert paper.exchange.slippage_bps < prod.exchange.slippage_bps

    def test_backtest_mode(self, profiles_dir):
        loader = ProfileLoader(profiles_dir=profiles_dir)
        config = loader.load('backtest')
        assert config.exchange.mode == 'backtest'


# ── Test Edge Cases ───────────────────────────────────────────

class TestEdgeCases:
    def test_corrupt_yaml(self, tmp_path):
        profile_dir = tmp_path / "profiles" / "bad"
        profile_dir.mkdir(parents=True)
        (profile_dir / "config.yml").write_text("{{invalid yaml:::")

        loader = ProfileLoader(profiles_dir=str(tmp_path / "profiles"))
        config = loader.load('bad')
        # Should use defaults, not crash
        assert config.risk.max_loss_usd == 25.0

    def test_empty_yaml(self, tmp_path):
        profile_dir = tmp_path / "profiles" / "empty"
        profile_dir.mkdir(parents=True)
        (profile_dir / "config.yml").write_text("")

        loader = ProfileLoader(profiles_dir=str(tmp_path / "profiles"))
        config = loader.load('empty')
        assert config.risk.max_loss_usd == 25.0

    def test_partial_profile(self, tmp_path):
        """Profile only overrides some fields — rest should come from default."""
        default_dir = tmp_path / "profiles" / "default"
        default_dir.mkdir(parents=True)
        (default_dir / "config.yml").write_text(yaml.dump({
            'exchange': {'mode': 'paper', 'slippage_bps': 100},
            'risk': {'max_loss_usd': 50},
        }))

        partial_dir = tmp_path / "profiles" / "partial"
        partial_dir.mkdir(parents=True)
        (partial_dir / "config.yml").write_text(yaml.dump({
            'risk': {'max_loss_usd': 10},  # Only override this
        }))

        loader = ProfileLoader(profiles_dir=str(tmp_path / "profiles"))
        config = loader.load('partial')

        # Risk overridden
        assert config.risk.max_loss_usd == 10
        # Exchange comes from default
        assert config.exchange.slippage_bps == 100
