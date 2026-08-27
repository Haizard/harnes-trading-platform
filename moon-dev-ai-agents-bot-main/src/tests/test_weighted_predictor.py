"""
Tests for Moon Dev's Weighted Prediction Engine
"""

import pytest
import asyncio
import os
import json
from src.weighted_predictor import (
    WeightedPredictor, Prediction, Regime, FactorConfig,
    RegimeProfile, DEFAULT_FACTORS, DEFAULT_REGIME_PROFILES,
    create_weighted_predictor,
)


# ── Fixtures ───────────────────────────────────────────────────

@pytest.fixture
def predictor(tmp_path):
    history_path = str(tmp_path / "history.jsonl")
    return WeightedPredictor(history_path=history_path)


@pytest.fixture
def bullish_features():
    """Features indicating strong bullish signal."""
    return {
        'indicators': {'RSI_14': 28.0},
        'autonomous': {
            'volume_spike': 2.5,
            'momentum_5m_pct': 0.35,
            'buy_pressure': 0.75,
            'volatility_20': 30.0,
        },
        'microstructure': {'volume_imbalance': 0.25},
    }


@pytest.fixture
def bearish_features():
    """Features indicating strong bearish signal."""
    return {
        'indicators': {'RSI_14': 75.0},
        'autonomous': {
            'volume_spike': 2.0,
            'momentum_5m_pct': -0.30,
            'buy_pressure': 0.25,
            'volatility_20': 35.0,
        },
        'microstructure': {'volume_imbalance': -0.20},
    }


@pytest.fixture
def neutral_features():
    """Features indicating neutral/hold signal."""
    return {
        'indicators': {'RSI_14': 50.0},
        'autonomous': {
            'volume_spike': 1.0,
            'momentum_5m_pct': 0.02,
            'buy_pressure': 0.50,
            'volatility_20': 20.0,
        },
        'microstructure': {'volume_imbalance': 0.02},
    }


@pytest.fixture
def volatile_features():
    """Features indicating volatile market."""
    return {
        'indicators': {'RSI_14': 55.0},
        'autonomous': {
            'volume_spike': 3.0,
            'momentum_5m_pct': 0.50,
            'buy_pressure': 0.55,
            'volatility_20': 80.0,
        },
        'microstructure': {'volume_imbalance': 0.10},
    }


# ── Test Prediction Dataclass ─────────────────────────────────

class TestPrediction:
    def test_creation(self):
        p = Prediction(
            symbol='BTC', signal='BUY', score=0.5, raw_score=0.6,
            confidence=0.8, regime=Regime.TRENDING_UP,
            factors={'rsi': 0.8}, weights={'rsi': 1.2}, reasons=['test'],
        )
        assert p.signal == 'BUY'
        assert p.regime == Regime.TRENDING_UP

    def test_to_dict(self):
        p = Prediction(
            symbol='BTC', signal='BUY', score=0.5, raw_score=0.6,
            confidence=0.8, regime=Regime.RANGING,
            factors={'rsi': 0.5}, weights={'rsi': 1.0}, reasons=['test'],
        )
        d = p.to_dict()
        assert d['symbol'] == 'BTC'
        assert d['signal'] == 'BUY'
        assert d['regime'] == 'ranging'


# ── Test Factor Config ────────────────────────────────────────

class TestFactorConfig:
    def test_defaults(self):
        config = FactorConfig(name='test')
        assert config.weight == 1.0
        assert config.enabled is True

    def test_custom(self):
        config = FactorConfig(name='rsi', weight=1.5, sensitivity=0.8)
        assert config.weight == 1.5
        assert config.sensitivity == 0.8


# ── Test Regime Detection ─────────────────────────────────────

class TestRegimeDetection:
    @pytest.mark.asyncio
    async def test_trending_up(self, predictor):
        features = {
            'indicators': {'RSI_14': 60.0},
            'autonomous': {'volatility_20': 60.0, 'momentum_5m_pct': 0.30, 'volume_spike': 2.0},
            'microstructure': {},
        }
        regime = predictor._detect_regime(features)
        assert regime == Regime.TRENDING_UP

    @pytest.mark.asyncio
    async def test_trending_down(self, predictor):
        features = {
            'indicators': {'RSI_14': 40.0},
            'autonomous': {'volatility_20': 60.0, 'momentum_5m_pct': -0.30, 'volume_spike': 2.0},
            'microstructure': {},
        }
        regime = predictor._detect_regime(features)
        assert regime == Regime.TRENDING_DOWN

    @pytest.mark.asyncio
    async def test_volatile(self, predictor):
        features = {
            'indicators': {'RSI_14': 50.0},
            'autonomous': {'volatility_20': 80.0, 'momentum_5m_pct': 0.05, 'volume_spike': 1.0},
            'microstructure': {},
        }
        regime = predictor._detect_regime(features)
        assert regime == Regime.VOLATILE

    @pytest.mark.asyncio
    async def test_ranging(self, predictor):
        features = {
            'indicators': {'RSI_14': 50.0},
            'autonomous': {'volatility_20': 20.0, 'momentum_5m_pct': 0.02, 'volume_spike': 1.0},
            'microstructure': {},
        }
        regime = predictor._detect_regime(features)
        assert regime == Regime.RANGING


# ── Test Predictions ──────────────────────────────────────────

class TestPredictions:
    @pytest.mark.asyncio
    async def test_bullish_prediction(self, predictor, bullish_features):
        result = await predictor.predict('TEST', features=bullish_features)
        assert result.signal in ('BUY', 'WEAK_BUY')
        assert result.score > 0
        assert result.confidence > 0.3

    @pytest.mark.asyncio
    async def test_bearish_prediction(self, predictor, bearish_features):
        result = await predictor.predict('TEST', features=bearish_features)
        assert result.signal in ('SELL', 'WEAK_SELL')
        assert result.score < 0
        assert result.confidence > 0.3

    @pytest.mark.asyncio
    async def test_neutral_prediction(self, predictor, neutral_features):
        result = await predictor.predict('TEST', features=neutral_features)
        assert result.signal == 'HOLD'
        assert abs(result.score) < 0.5

    @pytest.mark.asyncio
    async def test_volatile_reduces_confidence(self, predictor, volatile_features):
        result = await predictor.predict('TEST', features=volatile_features)
        # Volatile regime should cap confidence
        assert result.confidence <= 0.7

    @pytest.mark.asyncio
    async def test_no_features(self, predictor):
        result = await predictor.predict('NODATA', features={})
        assert result.signal == 'HOLD'
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_prediction_has_all_fields(self, predictor, bullish_features):
        result = await predictor.predict('TEST', features=bullish_features)
        assert result.symbol == 'TEST'
        assert result.regime in Regime
        assert len(result.factors) > 0
        assert len(result.weights) > 0
        assert len(result.reasons) > 0
        assert result.timestamp


# ── Test Factor Scoring ───────────────────────────────────────

class TestFactorScoring:
    def test_rsi_oversold_positive(self, predictor):
        config = DEFAULT_FACTORS['rsi']
        score = predictor._score_factor('rsi', config, 25.0, Regime.RANGING)
        assert score > 0.5  # Deeply oversold = strong positive

    def test_rsi_overbought_negative(self, predictor):
        config = DEFAULT_FACTORS['rsi']
        score = predictor._score_factor('rsi', config, 80.0, Regime.RANGING)
        assert score < -0.5

    def test_rsi_neutral(self, predictor):
        config = DEFAULT_FACTORS['rsi']
        score = predictor._score_factor('rsi', config, 50.0, Regime.RANGING)
        assert abs(score) < 0.2

    def test_volume_spike_positive(self, predictor):
        config = DEFAULT_FACTORS['volume_spike']
        score = predictor._score_factor('volume_spike', config, 3.0, Regime.RANGING)
        assert score > 0

    def test_momentum_positive(self, predictor):
        config = DEFAULT_FACTORS['momentum']
        score = predictor._score_factor('momentum', config, 0.30, Regime.RANGING)
        assert score > 0

    def test_momentum_negative(self, predictor):
        config = DEFAULT_FACTORS['momentum']
        score = predictor._score_factor('momentum', config, -0.30, Regime.RANGING)
        assert score < 0

    def test_buy_pressure_bullish(self, predictor):
        config = DEFAULT_FACTORS['buy_pressure']
        score = predictor._score_factor('buy_pressure', config, 0.80, Regime.RANGING)
        assert score > 0

    def test_buy_pressure_bearish(self, predictor):
        config = DEFAULT_FACTORS['buy_pressure']
        score = predictor._score_factor('buy_pressure', config, 0.20, Regime.RANGING)
        assert score < 0


# ── Test Regime Profiles ──────────────────────────────────────

class TestRegimeProfiles:
    def test_all_regimes_have_profiles(self):
        for regime in Regime:
            assert regime in DEFAULT_REGIME_PROFILES

    def test_trending_boosts_momentum(self):
        profile = DEFAULT_REGIME_PROFILES[Regime.TRENDING_UP]
        assert profile.factor_adjustments.get('momentum', 1.0) > 1.0

    def test_ranging_boosts_rsi(self):
        profile = DEFAULT_REGIME_PROFILES[Regime.RANGING]
        assert profile.factor_adjustments.get('rsi', 1.0) > 1.0

    def test_volatile_caps_confidence(self):
        profile = DEFAULT_REGIME_PROFILES[Regime.VOLATILE]
        assert profile.confidence_ceiling < 1.0


# ── Test History Tracking ─────────────────────────────────────

class TestHistoryTracking:
    @pytest.mark.asyncio
    async def test_prediction_recorded(self, predictor, bullish_features, tmp_path):
        await predictor.predict('TEST', features=bullish_features)
        assert os.path.exists(predictor.history_path)

    @pytest.mark.asyncio
    async def test_accuracy_report_empty(self, predictor):
        report = await predictor.get_accuracy_report()
        assert report['total'] == 0

    @pytest.mark.asyncio
    async def test_accuracy_report_with_data(self, predictor, tmp_path):
        # Manually write some history
        predictor.history_path = str(tmp_path / "history.jsonl")
        with open(predictor.history_path, 'w') as f:
            for i in range(5):
                f.write(json.dumps({
                    'timestamp': '2026-08-27T12:00:00',
                    'symbol': 'TEST',
                    'signal': 'BUY' if i % 2 == 0 else 'SELL',
                    'confidence': 0.7,
                    'regime': 'ranging',
                    'factors': {},
                }) + '\n')

        report = await predictor.get_accuracy_report(days=30)
        assert report['total'] == 5
        assert 'BUY' in report['by_signal']


# ── Test Factory ──────────────────────────────────────────────

class TestFactory:
    def test_create_weighted_predictor(self):
        predictor = create_weighted_predictor()
        assert isinstance(predictor, WeightedPredictor)


# ── Test Custom Factors ───────────────────────────────────────

class TestCustomFactors:
    @pytest.mark.asyncio
    async def test_disabled_factor(self, tmp_path):
        """Disabled factors should not contribute to score."""
        factors = DEFAULT_FACTORS.copy()
        factors['rsi'] = FactorConfig(name='RSI', weight=1.0, enabled=False)

        predictor = WeightedPredictor(factors=factors, history_path=str(tmp_path / "h.jsonl"))
        features = {
            'indicators': {'RSI_14': 25.0},  # Strongly oversold
            'autonomous': {'volume_spike': 1.0, 'momentum_5m_pct': 0.0, 'buy_pressure': 0.5, 'volatility_20': 10.0},
            'microstructure': {'volume_imbalance': 0.0},
        }
        result = await predictor.predict('TEST', features=features)
        # RSI is disabled, so it shouldn't contribute
        assert 'RSI' not in result.factors or result.factors.get('RSI', 0) == 0

    @pytest.mark.asyncio
    async def test_custom_weights(self, tmp_path):
        """Custom weights should change the score."""
        factors = DEFAULT_FACTORS.copy()
        factors['rsi'] = FactorConfig(name='RSI', weight=3.0, buy_threshold=35, sell_threshold=65)  # Triple RSI importance

        predictor = WeightedPredictor(factors=factors, history_path=str(tmp_path / "h.jsonl"))
        features = {
            'indicators': {'RSI_14': 25.0},
            'autonomous': {'volume_spike': 1.0, 'momentum_5m_pct': 0.0, 'buy_pressure': 0.5, 'volatility_20': 10.0},
            'microstructure': {'volume_imbalance': 0.0},
        }
        result = await predictor.predict('TEST', features=features)
        # With high RSI weight, should be strongly bullish
        assert result.score > 0.25  # Score is positive and significant
