"""
🌙 Moon Dev's Benchmark Tracker
Compares bot performance against passive buy-and-hold of BTC and SOL.
If you can't beat BTC, you shouldn't be trading.

DSH Pattern: Session Query + Session Log — track benchmark alongside every trade.
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from termcolor import cprint
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class BenchmarkReport:
    """A performance comparison report."""
    period: str
    period_days: int
    bot_return_pct: float
    btc_return_pct: float
    sol_return_pct: float
    alpha_vs_btc: float
    alpha_vs_sol: float
    verdict: str
    total_trades: int = 0
    win_rate: float = 0.0
    avg_trade_pnl: float = 0.0
    portfolio_start: float = 0.0
    portfolio_end: float = 0.0
    btc_start: float = 0.0
    btc_end: float = 0.0
    sol_start: float = 0.0
    sol_end: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            'period': self.period,
            'period_days': self.period_days,
            'bot_return_pct': self.bot_return_pct,
            'btc_return_pct': self.btc_return_pct,
            'sol_return_pct': self.sol_return_pct,
            'alpha_vs_btc': self.alpha_vs_btc,
            'alpha_vs_sol': self.alpha_vs_sol,
            'verdict': self.verdict,
            'total_trades': self.total_trades,
            'win_rate': self.win_rate,
            'portfolio_start': self.portfolio_start,
            'portfolio_end': self.portfolio_end,
            'timestamp': self.timestamp,
        }


class BenchmarkTracker:
    """
    DSH-style benchmark tracking.
    Every performance report includes a comparison to passive hold.

    Usage:
        tracker = BenchmarkTracker()
        report = await tracker.daily_report()
        tracker.print_report(report)
    """

    def __init__(self, portfolio_balance_csv='src/data/portfolio_balance.csv',
                 trades_csv=None, price_fetcher=None):
        self.portfolio_csv = portfolio_balance_csv
        self.trades_csv = trades_csv
        self._price_fetcher = price_fetcher  # Injectable for testing

    async def daily_report(self) -> BenchmarkReport:
        """Generate a daily benchmark report."""
        return await self._report(period='1d', days=1)

    async def weekly_report(self) -> BenchmarkReport:
        """Generate a weekly benchmark report."""
        return await self._report(period='7d', days=7)

    async def monthly_report(self) -> BenchmarkReport:
        """Generate a monthly benchmark report."""
        return await self._report(period='30d', days=30)

    async def all_time_report(self) -> BenchmarkReport:
        """Generate an all-time benchmark report."""
        return await self._report(period='all', days=90)

    async def _report(self, period: str, days: int) -> BenchmarkReport:
        """Generate a benchmark report for the given period."""

        # 1. Get portfolio returns
        portfolio_start, portfolio_end = await self._get_portfolio_values(days)
        if portfolio_start == 0:
            bot_return = 0
        else:
            bot_return = ((portfolio_end - portfolio_start) / portfolio_start) * 100

        # 2. Get BTC returns
        btc_start, btc_end = await self._get_btc_price(days)
        btc_return = ((btc_end - btc_start) / btc_start) * 100

        # 3. Get SOL returns
        sol_start, sol_end = await self._get_sol_price(days)
        sol_return = ((sol_end - sol_start) / sol_start) * 100

        # 4. Calculate alpha
        alpha_btc = bot_return - btc_return
        alpha_sol = bot_return - sol_return

        # 5. Get trade stats
        total_trades, win_rate = await self._get_trade_stats(days)

        # 6. Generate verdict
        verdict = self._generate_verdict(alpha_btc, win_rate, total_trades)

        return BenchmarkReport(
            period=period,
            period_days=days,
            bot_return_pct=round(bot_return, 2),
            btc_return_pct=round(btc_return, 2),
            sol_return_pct=round(sol_return, 2),
            alpha_vs_btc=round(alpha_btc, 2),
            alpha_vs_sol=round(alpha_sol, 2),
            verdict=verdict,
            total_trades=total_trades,
            win_rate=round(win_rate, 2),
            portfolio_start=round(portfolio_start, 2),
            portfolio_end=round(portfolio_end, 2),
            btc_start=round(btc_start, 2),
            btc_end=round(btc_end, 2),
            sol_start=round(sol_start, 2),
            sol_end=round(sol_end, 2),
        )

    async def _get_portfolio_values(self, days: int) -> tuple:
        """Get portfolio value at start and end of period."""
        try:
            if not os.path.exists(self.portfolio_csv):
                cprint("[BENCHMARK] No portfolio balance file found, using $25 default", "yellow")
                return 25.0, 25.0

            df = pd.read_csv(self.portfolio_csv)
            if df.empty:
                return 25.0, 25.0

            df['timestamp'] = pd.to_datetime(df['timestamp'])
            cutoff = datetime.utcnow() - timedelta(days=days)

            recent = df[df['timestamp'] >= cutoff]
            if recent.empty or len(recent) < 2:
                # Use all available data
                return float(df['balance'].iloc[0]), float(df['balance'].iloc[-1])

            return float(recent['balance'].iloc[0]), float(recent['balance'].iloc[-1])

        except Exception as e:
            cprint(f"[BENCHMARK] Error reading portfolio: {e}", "red")
            return 25.0, 25.0

    async def _get_btc_price(self, days: int) -> tuple:
        """Get BTC price at start and end of period."""
        if self._price_fetcher:
            return await self._price_fetcher('BTCUSDT', days)

        try:
            from binance.client import Client
            client = Client()

            end_time = datetime.utcnow()
            start_time = end_time - timedelta(days=days)

            klines = client.get_historical_klines(
                "BTCUSDT",
                Client.KLINE_INTERVAL_1DAY,
                start_time.strftime("%d %b, %Y"),
                end_time.strftime("%d %b, %Y")
            )

            if len(klines) < 2:
                ticker = client.get_symbol_ticker(symbol="BTCUSDT")
                price = float(ticker['price'])
                return price, price

            btc_start = float(klines[0][1])  # Open price of first candle
            btc_end = float(klines[-1][4])   # Close price of last candle

            return btc_start, btc_end

        except Exception as e:
            cprint(f"[BENCHMARK] Error fetching BTC price: {e}", "red")
            return 100000.0, 100000.0

    async def _get_sol_price(self, days: int) -> tuple:
        """Get SOL price at start and end of period."""
        if self._price_fetcher:
            return await self._price_fetcher('SOLUSDT', days)

        try:
            from binance.client import Client
            client = Client()

            end_time = datetime.utcnow()
            start_time = end_time - timedelta(days=days)

            klines = client.get_historical_klines(
                "SOLUSDT",
                Client.KLINE_INTERVAL_1DAY,
                start_time.strftime("%d %b, %Y"),
                end_time.strftime("%d %b, %Y")
            )

            if len(klines) < 2:
                ticker = client.get_symbol_ticker(symbol="SOLUSDT")
                price = float(ticker['price'])
                return price, price

            sol_start = float(klines[0][1])
            sol_end = float(klines[-1][4])

            return sol_start, sol_end

        except Exception as e:
            cprint(f"[BENCHMARK] Error fetching SOL price: {e}", "red")
            return 150.0, 150.0

    async def _get_trade_stats(self, days: int) -> tuple:
        """Get trade count and win rate from portfolio balance history."""
        try:
            if not os.path.exists(self.portfolio_csv):
                return 0, 0.0

            df = pd.read_csv(self.portfolio_csv)
            if df.empty or len(df) < 2:
                return 0, 0.0

            df['timestamp'] = pd.to_datetime(df['timestamp'])
            cutoff = datetime.utcnow() - timedelta(days=days)
            recent = df[df['timestamp'] >= cutoff]

            if len(recent) < 2:
                return 0, 0.0

            # Count balance changes as proxy for trades
            balance_changes = recent['balance'].diff().dropna()
            trades = len(balance_changes[abs(balance_changes) > 0.01])  # Filter noise
            wins = len(balance_changes[balance_changes > 0.01])
            win_rate = wins / trades if trades > 0 else 0.0

            return trades, win_rate

        except Exception as e:
            return 0, 0.0

    def _generate_verdict(self, alpha_btc: float, win_rate: float, total_trades: int) -> str:
        """Generate a human-readable verdict."""
        if total_trades < 3:
            return "⚪ INSUFFICIENT DATA — need more trades for meaningful comparison"

        if alpha_btc > 10:
            return f"🟢 CRUSHING BTC — +{alpha_btc:.1f}% alpha. Excellent performance!"
        elif alpha_btc > 5:
            return f"🟢 BEATING BTC — +{alpha_btc:.1f}% alpha. Adding real value."
        elif alpha_btc > 2:
            return f"🟡 MILDLY BEATING BTC — +{alpha_btc:.1f}% alpha. Marginal edge."
        elif alpha_btc > 0:
            return f"🟡 BARELY BEATING BTC — +{alpha_btc:.1f}% alpha. Re-evaluate strategy."
        elif alpha_btc > -3:
            return f"🟠 UNDERPERFORMING BTC — {alpha_btc:.1f}% vs passive hold."
        elif alpha_btc > -10:
            return f"🔴 FAR BEHIND BTC — {alpha_btc:.1f}% vs passive hold. Consider shutting down."
        else:
            return f"🔴 CATASTROPHIC — {alpha_btc:.1f}% vs BTC. Trading is destroying value."

    def print_report(self, report: BenchmarkReport):
        """Print a formatted benchmark report."""
        print()
        print("=" * 60)
        print(f"  📊 BENCHMARK REPORT — {report.period.upper()}")
        print(f"  Period: {report.period_days} days")
        print("=" * 60)
        print()
        print(f"  💼 Portfolio:")
        print(f"     Start:    ${report.portfolio_start:.2f}")
        print(f"     End:      ${report.portfolio_end:.2f}")
        print(f"     Return:   {report.bot_return_pct:+.2f}%")
        print()
        print(f"  ₿  Bitcoin (BTC):")
        print(f"     Start:    ${report.btc_start:,.2f}")
        print(f"     End:      ${report.btc_end:,.2f}")
        print(f"     Return:   {report.btc_return_pct:+.2f}%")
        print()
        print(f"  ◎  Solana (SOL):")
        print(f"     Start:    ${report.sol_start:,.2f}")
        print(f"     End:      ${report.sol_end:,.2f}")
        print(f"     Return:   {report.sol_return_pct:+.2f}%")
        print()
        print(f"  📈 Alpha (Bot - Benchmark):")
        print(f"     vs BTC:   {report.alpha_vs_btc:+.2f}%")
        print(f"     vs SOL:   {report.alpha_vs_sol:+.2f}%")
        print()
        print(f"  📋 Trades: {report.total_trades} | Win Rate: {report.win_rate:.0%}")
        print()
        print(f"  {report.verdict}")
        print()
        print("=" * 60)

    def print_quick_status(self, report: BenchmarkReport):
        """Print a one-line status for frequent monitoring."""
        color = "green" if report.alpha_vs_btc > 0 else "red"
        cprint(
            f"[BENCHMARK] Bot {report.bot_return_pct:+.1f}% | "
            f"BTC {report.btc_return_pct:+.1f}% | "
            f"Alpha {report.alpha_vs_btc:+.1f}% | "
            f"Trades {report.total_trades} | "
            f"WR {report.win_rate:.0%}",
            "white", f"on_{color}"
        )


# ── CLI Interface ───────────────────────────────────────────────────

async def main():
    """Run benchmark reports from command line."""
    tracker = BenchmarkTracker()

    print("\n🌙 Moon Dev Benchmark Tracker\n")

    # Daily
    daily = await tracker.daily_report()
    tracker.print_report(daily)

    # Weekly
    weekly = await tracker.weekly_report()
    tracker.print_report(weekly)

    # Monthly
    monthly = await tracker.monthly_report()
    tracker.print_report(monthly)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
