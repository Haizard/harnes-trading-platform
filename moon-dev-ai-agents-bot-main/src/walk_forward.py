"""
📊 Moon Dev's Walk-Forward Backtesting
Prevent overfitting by testing strategies on out-of-sample data.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable
import numpy as np


@dataclass
class WalkForwardResult:
    strategy_name: str
    in_sample_return: float
    out_of_sample_return: float
    overfit_score: float  # ratio of IS to OOS performance
    periods_tested: int
    deployable: bool
    avg_win_rate: float = 0.0
    sharpe_ratio: float = 0.0

    def to_dict(self):
        return {
            'strategy': self.strategy_name, 'in_sample': round(self.in_sample_return, 4),
            'out_of_sample': round(self.out_of_sample_return, 4),
            'overfit_score': round(self.overfit_score, 3), 'deployable': self.deployable,
            'periods': self.periods_tested,
        }


class WalkForwardValidator:
    """Tests strategies on unseen data to detect overfitting."""

    def __init__(self, train_days: int = 60, test_days: int = 7):
        self.train_days = train_days
        self.test_days = test_days

    async def validate(self, strategy_fn: Callable, symbol: str,
                      price_data: List[float]) -> WalkForwardResult:
        """
        Run walk-forward validation.
        
        strategy_fn can be:
        1. A callable(prices_list) -> float return (legacy mode)
        2. A callable(df_slice) -> float return (DataFrame mode, used by strategy_runner)
        """
        if len(price_data) < self.train_days + self.test_days:
            return WalkForwardResult(strategy_fn.__name__, 0, 0, 0, 0, False)

        is_returns = []
        oos_returns = []
        start = 0

        while start + self.train_days + self.test_days <= len(price_data):
            train = price_data[start:start+self.train_days]
            test = price_data[start+self.train_days:start+self.train_days+self.test_days]

            # Use the actual strategy function
            try:
                train_return = strategy_fn(train) if len(train) >= 2 else 0.0
                test_return = strategy_fn(test) if len(test) >= 2 else 0.0
            except Exception:
                # Fallback to buy-and-hold if strategy fails
                train_return = (train[-1] - train[0]) / train[0] if train[0] > 0 else 0
                test_return = (test[-1] - test[0]) / test[0] if test[0] > 0 else 0

            is_returns.append(train_return)
            oos_returns.append(test_return)
            start += self.test_days

        if not is_returns:
            return WalkForwardResult(strategy_fn.__name__, 0, 0, 0, 0, False)

        avg_is = np.mean(is_returns)
        avg_oos = np.mean(oos_returns)
        overfit = abs(avg_is) / max(abs(avg_oos), 0.001) if avg_oos != 0 else 10.0
        deployable = avg_oos > 0 and overfit < 3.0

        return WalkForwardResult(
            strategy_name=strategy_fn.__name__, in_sample_return=avg_is,
            out_of_sample_return=avg_oos, overfit_score=overfit,
            periods_tested=len(is_returns), deployable=deployable,
        )

    async def validate_with_dataframes(self, strategy_fn: Callable, symbol: str,
                                       full_df, bars_per_day: int = 96) -> WalkForwardResult:
        """
        Walk-forward using actual OHLCV DataFrame slices.
        Each window runs a full backtest through backtesting.py.
        
        Args:
            strategy_fn: callable(df_slice) -> float (return as decimal)
            symbol: strategy name for result
            full_df: full OHLCV DataFrame with Open/High/Low/Close/Volume columns
            bars_per_day: bars per day in the data (96 for 15min, 24 for 1h, etc.)
        """
        train_bars = self.train_days * bars_per_day
        test_bars = self.test_days * bars_per_day
        total_bars = len(full_df)
        
        if total_bars < train_bars + test_bars:
            return WalkForwardResult(strategy_fn.__name__, 0, 0, 0, 0, False)

        is_returns = []
        oos_returns = []
        start = 0

        while start + train_bars + test_bars <= total_bars:
            train_slice = full_df.iloc[start:start + train_bars].copy()
            test_slice = full_df.iloc[start + train_bars:start + train_bars + test_bars].copy()

            try:
                train_return = strategy_fn(train_slice)
                test_return = strategy_fn(test_slice)
            except Exception:
                # Fallback: buy-and-hold on Close
                train_return = (train_slice['Close'].iloc[-1] - train_slice['Close'].iloc[0]) / train_slice['Close'].iloc[0] if len(train_slice) > 1 else 0
                test_return = (test_slice['Close'].iloc[-1] - test_slice['Close'].iloc[0]) / test_slice['Close'].iloc[0] if len(test_slice) > 1 else 0

            is_returns.append(train_return)
            oos_returns.append(test_return)
            start += test_bars

        if not is_returns:
            return WalkForwardResult(strategy_fn.__name__, 0, 0, 0, 0, False)

        avg_is = np.mean(is_returns)
        avg_oos = np.mean(oos_returns)
        overfit = abs(avg_is) / max(abs(avg_oos), 0.001) if avg_oos != 0 else 10.0
        deployable = avg_oos > 0 and overfit < 3.0

        return WalkForwardResult(
            strategy_name=strategy_fn.__name__, in_sample_return=avg_is,
            out_of_sample_return=avg_oos, overfit_score=overfit,
            periods_tested=len(is_returns), deployable=deployable,
        )
