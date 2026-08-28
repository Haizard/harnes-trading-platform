"""
Tests for Micro-Cap Trading Engine
Tests token_scanner, micro_sniper, and micro_engine modules.
"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime

# Import modules under test
from src.token_scanner import (
    TokenCandidate, TokenScorer, JupiterChecker, BirdeyeClient, TokenScanner
)
from src.micro_sniper import (
    Position, TradeSignal, MicroPositionSizer, MicroRiskManager, MicroSniper
)


# ── TokenCandidate Tests ─────────────────────────────────────

class TestTokenCandidate:
    def test_creation(self):
        candidate = TokenCandidate(
            address="test_address",
            symbol="TEST",
            name="Test Token",
        )
        assert candidate.address == "test_address"
        assert candidate.symbol == "TEST"
        assert candidate.score == 0.0
        assert candidate.signals == []
    
    def test_to_dict(self):
        candidate = TokenCandidate(
            address="test_address",
            symbol="TEST",
            name="Test Token",
            price_usd=0.001,
            volume_1h=15000,
            liquidity_usd=10000,
        )
        d = candidate.to_dict()
        assert d["address"] == "test_address"
        assert d["price_usd"] == 0.001
        assert d["volume_1h"] == 15000
        assert "timestamp" in d


# ── TokenScorer Tests ────────────────────────────────────────

class TestTokenScorer:
    def setup_method(self):
        self.scorer = TokenScorer()
    
    def test_high_volume_high_score(self):
        candidate = TokenCandidate(
            address="test", symbol="TEST", name="Test",
            volume_1h=60000,
            liquidity_usd=60000,
            price_change_1h=60,
            holder_count=1500,
            market_cap=50000,
        )
        score = self.scorer.score(candidate)
        assert score >= 80
        assert len(candidate.signals) > 0
    
    def test_low_volume_low_score(self):
        candidate = TokenCandidate(
            address="test", symbol="TEST", name="Test",
            volume_1h=1000,
            liquidity_usd=1000,
            price_change_1h=0,
            holder_count=10,
            market_cap=1000000,
        )
        score = self.scorer.score(candidate)
        assert score < 40
    
    def test_score_capped_at_100(self):
        candidate = TokenCandidate(
            address="test", symbol="TEST", name="Test",
            volume_1h=100000,
            liquidity_usd=100000,
            price_change_1h=100,
            holder_count=5000,
            market_cap=10000,
        )
        score = self.scorer.score(candidate)
        assert score <= 100


# ── JupiterChecker Tests ─────────────────────────────────────

class TestJupiterChecker:
    def setup_method(self):
        self.checker = JupiterChecker()
    
    @patch('requests.get')
    def test_check_liquidity_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "outAmount": "1000000",
            "priceImpactPct": "2.5",
            "routePlan": [],
        }
        mock_get.return_value = mock_resp
        
        result = self.checker.check_liquidity("test_token")
        assert result["available"] is True
        assert result["good_entry"] is True
    
    @patch('requests.get')
    def test_check_liquidity_high_impact(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "outAmount": "500000",
            "priceImpactPct": "15.0",
            "routePlan": [],
        }
        mock_get.return_value = mock_resp
        
        result = self.checker.check_liquidity("test_token")
        assert result["available"] is True
        assert result["good_entry"] is False
    
    @patch('requests.get')
    def test_check_liquidity_unavailable(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp
        
        result = self.checker.check_liquidity("test_token")
        assert result["available"] is False


# ── MicroPositionSizer Tests ─────────────────────────────────

class TestMicroPositionSizer:
    def test_high_score_large_position(self):
        sizer = MicroPositionSizer(total_capital=25.0)
        position = sizer.calculate_position(score=85, liquidity=50000)
        assert position == 10.0  # 40% of $25
    
    def test_medium_score_medium_position(self):
        sizer = MicroPositionSizer(total_capital=25.0)
        position = sizer.calculate_position(score=65, liquidity=50000)
        assert position == 6.25  # 25% of $25
    
    def test_low_score_small_position(self):
        sizer = MicroPositionSizer(total_capital=25.0)
        position = sizer.calculate_position(score=45, liquidity=50000)
        assert position == 3.75  # 15% of $25
    
    def test_liquidity_constraint(self):
        sizer = MicroPositionSizer(total_capital=25.0)
        position = sizer.calculate_position(score=85, liquidity=5000)
        # 40% of $25 = $10, but 10% of $5000 = $500, so $10 wins
        assert position == 10.0
    
    def test_minimum_trade_size(self):
        sizer = MicroPositionSizer(total_capital=5.0)
        position = sizer.calculate_position(score=40, liquidity=100)
        assert position >= 1.0  # Minimum $1


# ── MicroRiskManager Tests ──────────────────────────────────

class TestMicroRiskManager:
    def setup_method(self):
        self.risk = MicroRiskManager(total_capital=25.0)
    
    def test_can_trade_initially(self):
        can, reason = self.risk.can_trade()
        assert can is True
    
    def test_max_positions_limit(self):
        # Add 3 positions
        for i in range(3):
            self.risk.open_positions.append(
                Position(token_address=f"token_{i}", symbol=f"T{i}", side="buy", entry_price=1.0, amount_usd=5.0)
            )
        can, reason = self.risk.can_trade()
        assert can is False
        assert "Max 3" in reason
    
    def test_daily_loss_limit(self):
        self.risk.daily_pnl = -10.0  # Lost $10 (40% of $25)
        can, reason = self.risk.can_trade()
        assert can is False
        assert "Daily loss" in reason
    
    def test_stop_loss_check(self):
        position = Position(
            token_address="test", symbol="TEST", side="buy",
            entry_price=1.0, amount_usd=10.0
        )
        # Price dropped 15% - should trigger stop loss
        assert self.risk.check_stop_loss(position, 0.85) is True
        # Price dropped 5% - should not trigger
        assert self.risk.check_stop_loss(position, 0.95) is False
    
    def test_take_profit_check(self):
        position = Position(
            token_address="test", symbol="TEST", side="buy",
            entry_price=1.0, amount_usd=10.0
        )
        # Price up 35% - should trigger take profit
        assert self.risk.check_take_profit(position, 1.35) is True
        # Price up 20% - should not trigger
        assert self.risk.check_take_profit(position, 1.20) is False


# ── MicroSniper Tests ───────────────────────────────────────

class TestMicroSniper:
    def setup_method(self):
        self.sniper = MicroSniper(capital=25.0)
    
    def test_initialization(self):
        assert self.sniper.capital == 25.0
        assert len(self.sniper.positions) == 0
        assert len(self.sniper.history) == 0
    
    def test_evaluate_signal_high_score(self):
        signal = self.sniper.evaluate_signal(
            token_address="test_token",
            symbol="TEST",
            score=85,
            liquidity=50000,
        )
        assert signal is not None
        assert signal.side == "buy"
        assert signal.amount_usd > 0
    
    def test_evaluate_signal_low_score(self):
        signal = self.sniper.evaluate_signal(
            token_address="test_token",
            symbol="TEST",
            score=30,
            liquidity=50000,
        )
        assert signal is None
    
   
