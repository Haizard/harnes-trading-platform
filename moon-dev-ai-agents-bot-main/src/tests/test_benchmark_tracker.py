"""
Tests for Moon Dev's Benchmark Tracker
"""

import pytest
import asyncio
import os
import pandas as pd
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock
from src.benchmark_tracker import BenchmarkTracker, BenchmarkReport


class TestBenchmarkReport:
    """Test BenchmarkReport dataclass."""

    def test_report_creation(self):
        """Test creating a benchmark report."""
        report = BenchmarkReport(
            period='1d',
            period_days=1,
            bot_return_pct=2.5,
            btc_return_pct=5.0,
            sol_return_pct=3.0,
            alpha_vs_btc=-2.5,
            alpha_vs_sol=-0.5,
            verdict='🔴 UNDERPERFORMING',
        )

        assert report.period == '1d'
        assert report.bot_return_pct == 2.5
        assert report.alpha_vs_btc == -2.5
        assert report.verdict == '🔴 UNDERPERFORMING'

    def test_report_to_dict(self):
        """Test converting report to dictionary."""
        report = BenchmarkReport(
            period='7d',
            period_days=7,
            bot_return_pct=10.0,
            btc_return_pct=5.0,
            sol_return_pct=8.0,
            alpha_vs_btc=5.0,
            alpha_vs_sol=2.0,
            verdict='🟢 BEATING BTC',
            total_trades=15,
            win_rate=0.6,
        )

        d = report.to_dict()
        assert d['period'] == '7d'
        assert d['bot_return_pct'] == 10.0
        assert d['alpha_vs_btc'] == 5.0
        assert d['total_trades'] == 15
        assert d['win_rate'] == 0.6
        assert 'timestamp' in d


class TestBenchmarkTracker:
    """Test BenchmarkTracker class."""

    def test_init(self):
        """Test tracker initialization."""
        tracker = BenchmarkTracker()
        assert tracker.portfolio_csv == 'src/data/portfolio_balance.csv'

    def test_init_custom_path(self):
        """Test tracker with custom CSV path."""
        tracker = BenchmarkTracker(portfolio_balance_csv='/tmp/test.csv')
        assert tracker.portfolio_csv == '/tmp/test.csv'

    def test_generate_verdict_excellent(self):
        """Test verdict generation for excellent performance."""
        tracker = BenchmarkTracker()
        verdict = tracker._generate_verdict(alpha_btc=15.0, win_rate=0.7, total_trades=20)
        assert 'CRUSHING BTC' in verdict
        assert '🟢' in verdict

    def test_generate_verdict_good(self):
        """Test verdict generation for good performance."""
        tracker = BenchmarkTracker()
        verdict = tracker._generate_verdict(alpha_btc=6.0, win_rate=0.6, total_trades=15)
        assert 'BEATING BTC' in verdict
        assert '🟢' in verdict

    def test_generate_verdict_marginal(self):
        """Test verdict generation for marginal performance."""
        tracker = BenchmarkTracker()
        verdict = tracker._generate_verdict(alpha_btc=3.0, win_rate=0.55, total_trades=10)
        assert 'MILDLY BEATING' in verdict
        assert '🟡' in verdict

    def test_generate_verdict_underperforming(self):
        """Test verdict generation for underperformance."""
        tracker = BenchmarkTracker()
        verdict = tracker._generate_verdict(alpha_btc=-2.0, win_rate=0.4, total_trades=20)
        assert 'UNDERPERFORMING BTC' in verdict
        assert '🟠' in verdict

    def test_generate_verdict_catastrophic(self):
        """Test verdict generation for catastrophic performance."""
        tracker = BenchmarkTracker()
        verdict = tracker._generate_verdict(alpha_btc=-15.0, win_rate=0.3, total_trades=25)
        assert 'CATASTROPHIC' in verdict
        assert '🔴' in verdict

    def test_generate_verdict_insufficient_data(self):
        """Test verdict generation with insufficient data."""
        tracker = BenchmarkTracker()
        verdict = tracker._generate_verdict(alpha_btc=100.0, win_rate=1.0, total_trades=1)
        assert 'INSUFFICIENT DATA' in verdict
        assert '⚪' in verdict

    @pytest.mark.asyncio
    async def test_get_portfolio_values_no_file(self):
        """Test portfolio values when no CSV exists."""
        tracker = BenchmarkTracker(portfolio_balance_csv='/nonexistent/file.csv')
        start, end = await tracker._get_portfolio_values(days=1)
        assert start == 25.0  # Default
        assert end == 25.0

    @pytest.mark.asyncio
    async def test_get_portfolio_values_with_file(self, tmp_path):
        """Test portfolio values from CSV."""
        csv_path = tmp_path / "portfolio.csv"
        df = pd.DataFrame({
            'timestamp': [
                (datetime.utcnow() - timedelta(days=2)).isoformat(),
                (datetime.utcnow() - timedelta(days=1)).isoformat(),
                datetime.utcnow().isoformat(),
            ],
            'balance': [25.0, 27.5, 30.0]
        })
        df.to_csv(csv_path, index=False)

        tracker = BenchmarkTracker(portfolio_balance_csv=str(csv_path))
        start, end = await tracker._get_portfolio_values(days=3)
        assert start == 25.0
        assert end == 30.0

    @pytest.mark.asyncio
    async def test_get_btc_price_mocked(self):
        """Test BTC price fetching with injectable fetcher."""
        async def mock_fetcher(symbol, days):
            if symbol == 'BTCUSDT':
                return 95000.0, 96500.0
            return 140.0, 148.0

        tracker = BenchmarkTracker(price_fetcher=mock_fetcher)
        start, end = await tracker._get_btc_price(days=1)
        assert start == 95000.0
        assert end == 96500.0

    @pytest.mark.asyncio
    async def test_get_sol_price_mocked(self):
        """Test SOL price fetching with injectable fetcher."""
        async def mock_fetcher(symbol, days):
            if symbol == 'SOLUSDT':
                return 140.0, 148.0
            return 95000.0, 96500.0

        tracker = BenchmarkTracker(price_fetcher=mock_fetcher)
        start, end = await tracker._get_sol_price(days=1)
        assert start == 140.0
        assert end == 148.0

    @pytest.mark.asyncio
    async def test_full_report_integration(self, tmp_path):
        """Test full report generation with mocked data."""
        csv_path = tmp_path / "portfolio.csv"
        # Create data: start at $25, grow to $30 = +20% overall
        # 5 data points = 4 balance changes = 4 "trades" for verdict calculation
        now = datetime.utcnow()
        df = pd.DataFrame({
            'timestamp': [
                (now - timedelta(days=6)).isoformat(),
                (now - timedelta(days=4)).isoformat(),
                (now - timedelta(days=3)).isoformat(),
                (now - timedelta(days=1)).isoformat(),
                now.isoformat(),
            ],
            'balance': [25.0, 27.0, 26.5, 29.0, 30.0]
        })
        df.to_csv(csv_path, index=False)

        # Mock price fetcher: BTC 92000->98000, SOL 135->145
        async def mock_fetcher(symbol, days):
            if symbol == 'BTCUSDT':
                return 92000.0, 98000.0
            elif symbol == 'SOLUSDT':
                return 135.0, 145.0
            return 0, 0

        tracker = BenchmarkTracker(
            portfolio_balance_csv=str(csv_path),
            price_fetcher=mock_fetcher
        )

        report = await tracker._report(period='7d', days=7)

        # Bot: 25 -> 30 = +20%
        assert report.bot_return_pct == 20.0
        # Should have enough trades for a verdict
        assert report.total_trades >= 3

        # BTC: 92000 -> 98000 = +6.52%
        assert report.btc_return_pct > 6.0

        # SOL: 135 -> 145 = +7.41%
        assert report.sol_return_pct > 7.0

        # Alpha should be positive (bot beat both)
        assert report.alpha_vs_btc > 10
        assert report.alpha_vs_sol > 10

        assert 'CRUSHING' in report.verdict

    def test_print_report(self, capsys):
        """Test report printing."""
        tracker = BenchmarkTracker()
        report = BenchmarkReport(
            period='1d',
            period_days=1,
            bot_return_pct=5.0,
            btc_return_pct=3.0,
            sol_return_pct=4.0,
            alpha_vs_btc=2.0,
            alpha_vs_sol=1.0,
            verdict='🟢 BEATING BTC',
            total_trades=10,
            win_rate=0.6,
            portfolio_start=25.0,
            portfolio_end=26.25,
            btc_start=95000.0,
            btc_end=97850.0,
            sol_start=140.0,
            sol_end=145.6,
        )

        tracker.print_report(report)
        captured = capsys.readouterr()
        assert 'BENCHMARK REPORT' in captured.out
        assert '🟢 BEATING BTC' in captured.out


class TestBenchmarkCLI:
    """Test CLI interface."""

    @pytest.mark.asyncio
    async def test_main_runs(self):
        """Test that daily_report() runs without errors."""
        async def mock_fetcher(symbol, days):
            return 100.0, 110.0

        tracker = BenchmarkTracker(
            portfolio_balance_csv='/nonexistent.csv',
            price_fetcher=mock_fetcher
        )

        daily = await tracker.daily_report()
        assert isinstance(daily, BenchmarkReport)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
