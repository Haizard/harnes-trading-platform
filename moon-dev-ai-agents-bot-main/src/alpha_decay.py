"""
📉 Moon Dev's Alpha Decay Detection
DSH Pattern: Invariants + Compaction — monitor strategy health.

Detects when a strategy stops being profitable and auto-disables it.
Strategies that worked last month may not work now — this catches that.

Usage:
    detector = AlphaDecayDetector()
    status = detector.check_strategy('momentum', recent_trades)
    if status.decayed:
        print(f"Strategy {status.name} has decayed — disabling")
"""

import os
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum
from termcolor import cprint


class DecayStatus(str, Enum):
    HEALTHY = "healthy"
    DECLINING = "declining"
    DECAYED = "decayed"
    DEAD = "dead"


@dataclass
class StrategyHealth:
    """Health assessment for a single strategy."""
    name: str
    status: DecayStatus
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    total_trades: int = 0
    recent_win_rate: float = 0.0    # Last N trades
    historical_win_rate: float = 0.0  # All trades
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    days_since_last_win: int = 0
    recommendation: str = ""
    disabled: bool = False

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'status': self.status.value,
            'win_rate': round(self.win_rate, 3),
            'avg_pnl': round(self.avg_pnl, 4),
            'total_trades': self.total_trades,
            'recent_win_rate': round(self.recent_win_rate, 3),
            'historical_win_rate': round(self.historical_win_rate, 3),
            'sharpe_ratio': round(self.sharpe_ratio, 3),
            'max_drawdown': round(self.max_drawdown, 4),
            'days_since_last_win': self.days_since_last_win,
            'recommendation': self.recommendation,
            'disabled': self.disabled,
        }


class AlphaDecayDetector:
    """
    Monitors strategy performance and detects alpha decay.

    A strategy "decays" when:
    - Win rate drops below threshold
    - Sharpe ratio goes negative
    - No wins in N days
    - Drawdown exceeds limit
    """

    def __init__(self, config: dict = None):
        config = config or {}
        self.min_trades = config.get('min_trades', 10)
        self.decay_win_rate = config.get('decay_win_rate', 0.35)
        self.dead_win_rate = config.get('dead_win_rate', 0.25)
        self.declining_threshold = config.get('declining_threshold', 0.45)
        self.no_win_days = config.get('no_win_days', 14)
        self.max_drawdown = config.get('max_drawdown', 0.15)
        self.min_sharpe = config.get('min_sharpe', 0.0)

        self._strategy_data: Dict[str, List[dict]] = {}
        self._disabled: Dict[str, bool] = {}

    def record_trade(self, strategy: str, pnl_pct: float, timestamp: str = None):
        """Record a trade outcome for a strategy."""
        if strategy not in self._strategy_data:
            self._strategy_data[strategy] = []

        self._strategy_data[strategy].append({
            'pnl_pct': pnl_pct,
            'timestamp': timestamp or datetime.utcnow().isoformat(),
        })

    def check_strategy(self, strategy: str,
                      recent_window: int = 20) -> StrategyHealth:
        """Check the health of a single strategy."""
        trades = self._strategy_data.get(strategy, [])

        if len(trades) < self.min_trades:
            return StrategyHealth(
                name=strategy,
                status=DecayStatus.HEALTHY,
                total_trades=len(trades),
                recommendation=f"Only {len(trades)} trades — need {self.min_trades} for analysis",
                disabled=self._disabled.get(strategy, False),
            )

        # Split into recent and historical
        recent = trades[-recent_window:]
        historical = trades[:-recent_window] if len(trades) > recent_window else trades

        # Calculate metrics
        all_pnl = [t['pnl_pct'] for t in trades]
        recent_pnl = [t['pnl_pct'] for t in recent]

        win_rate = sum(1 for p in all_pnl if p > 0) / len(all_pnl)
        recent_win_rate = sum(1 for p in recent_pnl if p > 0) / len(recent_pnl)
        avg_pnl = sum(all_pnl) / len(all_pnl)
        sharpe = self._sharpe_ratio(all_pnl)
        max_dd = self._max_drawdown(all_pnl)

        # Days since last win
        days_since_win = 0
        for t in reversed(trades):
            if t['pnl_pct'] > 0:
                try:
                    trade_date = datetime.fromisoformat(t['timestamp'])
                    days_since_win = (datetime.utcnow() - trade_date).days
                except Exception:
                    days_since_win = 0
                break

        # Determine status
        status, recommendation = self._assess_health(
            win_rate, recent_win_rate, sharpe, max_dd, days_since_win
        )

        return StrategyHealth(
            name=strategy,
            status=status,
            win_rate=win_rate,
            avg_pnl=avg_pnl,
            total_trades=len(trades),
            recent_win_rate=recent_win_rate,
            historical_win_rate=win_rate,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            days_since_last_win=days_since_win,
            recommendation=recommendation,
            disabled=self._disabled.get(strategy, False),
        )

    def check_all_strategies(self) -> Dict[str, StrategyHealth]:
        """Check health of all strategies."""
        return {
            name: self.check_strategy(name)
            for name in self._strategy_data.keys()
        }

    def auto_disable(self, strategy: str):
        """Auto-disable a decayed strategy."""
        self._disabled[strategy] = True
        cprint(f"📉 AUTO-DISABLED: Strategy '{strategy}' — alpha decay detected", "red")

    def re_enable(self, strategy: str):
        """Re-enable a previously disabled strategy."""
        self._disabled[strategy] = False
        cprint(f"📈 RE-ENABLED: Strategy '{strategy}'", "green")

    def is_disabled(self, strategy: str) -> bool:
        """Check if a strategy is disabled."""
        return self._disabled.get(strategy, False)

    def get_disabled_strategies(self) -> List[str]:
        """Get list of disabled strategies."""
        return [s for s, d in self._disabled.items() if d]

    # ── Internal ──────────────────────────────────────────────

    def _assess_health(self, win_rate, recent_wr, sharpe, max_dd, days_win):
        """Assess strategy health and return status + recommendation."""

        # DEAD: Very low win rate or extreme drawdown
        if win_rate < self.dead_win_rate or max_dd > self.max_drawdown * 2:
            return DecayStatus.DEAD, (
                f"CRITICAL: Win rate {win_rate:.0%} (min {self.dead_win_rate:.0%}) "
                f"or drawdown {max_dd:.1%} — disable immediately"
            )

        # DECAYED: Below threshold
        if win_rate < self.decay_win_rate or sharpe < self.min_sharpe:
            return DecayStatus.DECAYED, (
                f"DECAYED: Win rate {win_rate:.0%} or Sharpe {sharpe:.2f} "
                f"below minimums — consider disabling"
            )

        # DECLINING: Recent performance worse than historical
        if recent_wr < win_rate * 0.8 and recent_wr < self.declining_threshold:
            return DecayStatus.DECLINING, (
                f"DECLINING: Recent win rate {recent_wr:.0%} < historical {win_rate:.0%} "
                f"— monitor closely"
            )

        # No wins recently
        if days_win > self.no_win_days:
            return DecayStatus.DECLINING, (
                f"No wins in {days_win} days — strategy may be stale"
            )

        return DecayStatus.HEALTHY, "Strategy performing within expected parameters"

    def _sharpe_ratio(self, returns: list, risk_free: float = 0.0) -> float:
        """Calculate Sharpe ratio."""
        if len(returns) < 2:
            return 0.0

        import numpy as np
        returns_arr = np.array(returns)
        mean_return = np.mean(returns_arr)
        std_return = np.std(returns_arr)

        if std_return == 0:
            return 0.0

        return (mean_return - risk_free) / std_return

    def _max_drawdown(self, returns: list) -> float:
        """Calculate maximum drawdown from returns."""
        if not returns:
            return 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for r in returns:
            cumulative += r
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / max(abs(peak), 0.001)
            if dd > max_dd:
                max_dd = dd

        return max_dd
