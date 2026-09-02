"""
Strategy Runner for Walk-Forward Validation
Dynamically loads and runs generated backtest strategies on price windows.
"""

import sys
import os
import re
from pathlib import Path
from typing import List, Union
import pandas as pd
import numpy as np


def _build_reusable_code(backtest_code: str) -> str:
    """
    Extract reusable code from a generated backtest script.
    Keeps: helper functions, indicator calculations, Strategy class
    Strips: data loading, Backtest execution, data-path boilerplate, main block
    """
    lines = backtest_code.split('\n')
    result_lines = []
    skip_block = False
    skip_indent = 0
    
    for line in lines:
        stripped = line.strip()
        current_indent = len(line) - len(line.lstrip())
        
        # If we're inside a block to skip, keep skipping until dedent
        if skip_block:
            if stripped == '' or current_indent > skip_indent:
                continue
            else:
                skip_block = False
        
        if not stripped:
            result_lines.append('')
            continue
            
        # Skip import statements
        if stripped.startswith('import ') or stripped.startswith('from '):
            continue
        
        # Skip DATA_PATH line
        if re.match(r'DATA_PATH\s*=', stripped):
            continue
        
        # Skip pd.read_csv
        if 'pd.read_csv' in stripped:
            skip_block = True
            skip_indent = current_indent
            continue
        
        # Skip df.columns assignment
        if re.match(r'df\.columns\s*=', stripped):
            skip_block = True
            skip_indent = current_indent
            continue
        
        # Skip df = df.rename(...) 
        if re.match(r'df\s*=\s*df\.rename', stripped):
            skip_block = True
            skip_indent = current_indent
            continue
        
        # Skip mapping line
        if re.match(r"mapping\s*=\s*\{", stripped):
            continue
        
        # Skip the datetime/timestamp detection block
        if re.match(r"if\s+['\"]datetime['\"]\s+in\s+df\.columns", stripped):
            skip_block = True
            skip_indent = current_indent
            continue
        if re.match(r"elif\s+['\"]timestamp['\"]\s+in\s+df\.columns", stripped):
            skip_block = True
            skip_indent = current_indent
            continue
        
        # Skip df.dropna
        if re.match(r'df\s*=\s*df\.dropna', stripped):
            continue
        
        # Skip standalone df = assignments (data loading)
        if re.match(r'^df\s*=\s*pd\.', stripped):
            continue
        
        # Skip Backtest creation and execution
        if re.match(r'bt\s*=\s*Backtest\(', stripped):
            skip_block = True
            skip_indent = current_indent
            continue
        if re.match(r'results\s*=\s*bt\.run\(', stripped):
            skip_block = True
            skip_indent = current_indent
            continue
        if re.match(r'print\(results\)', stripped):
            continue
        
        # Skip df['hour'] and df['session'] assignments
        if re.match(r"df\['hour'\]\s*=", stripped):
            continue
        if re.match(r"df\['session'\]\s*=", stripped):
            continue
        if re.match(r"df\['session_high'\]\s*=.*groupby", stripped):
            continue
        if re.match(r"df\['session_low'\]\s*=.*groupby", stripped):
            continue
        
        # Skip the TickSize if block
        if re.match(r"if\s+['\"]TickSize['\"]\s+not\s+in\s+df\.columns", stripped):
            skip_block = True
            skip_indent = current_indent
            continue
        
        # Skip the __main__ block
        if stripped == 'if __name__ == "__main__":' or stripped == "if __name__ == '__main__':":
            skip_block = True
            skip_indent = current_indent
            continue
        
        # Skip lines that reference 'data = df' or 'stats = bt.run()' or 'print(stats'
        if re.match(r'data\s*=\s*df', stripped):
            continue
        if re.match(r'stats\s*=\s*bt\.run\(', stripped):
            continue
        if re.match(r'print\(stats', stripped):
            continue
        
        result_lines.append(line)
    
    # Add our own imports at the top
    code = '\n'.join([
        'import pandas as pd',
        'import numpy as np',
        'import talib',
        'from backtesting import Strategy, Backtest',
        '',
        *result_lines,
    ])
    
    return code


def _add_session_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add session columns that generated strategies expect."""
    df = df.copy()
    df['hour'] = df.index.hour
    
    def _get_session(hour):
        if 0 <= hour < 8:
            return 'Asian'
        elif 8 <= hour < 16:
            return 'London'
        else:
            return 'NY'
    
    df['session'] = df['hour'].apply(_get_session)
    df['session_high'] = df.groupby(df.index.date)['High'].transform('max')
    df['session_low'] = df.groupby(df.index.date)['Low'].transform('min')
    df['TickSize'] = df['Close'] * 0.001
    return df


def _run_backtest_on_df(df: pd.DataFrame, strategy_class, cash: float = None) -> float:
    """Run a backtest on a DataFrame and return the return as a decimal."""
    from backtesting import Backtest
    
    if cash is None:
        cash = max(10000, df['Close'].iloc[0] * 100)
    
    bt = Backtest(df, strategy_class, cash=cash, commission=0.003)
    results = bt.run()
    
    if hasattr(results, 'loc'):
        return_pct = results.get('Return [%]', 0.0)
        return float(return_pct) / 100.0
    
    return 0.0


def _extract_strategy_class(reusable_code: str, df: pd.DataFrame):
    """Execute the reusable code and extract the Strategy class."""
    local_ns = {'df': df}
    exec(reusable_code, local_ns)
    
    from backtesting import Strategy
    strategy_class = None
    for var_name, var_val in local_ns.items():
        if isinstance(var_val, type) and issubclass(var_val, Strategy) and var_val is not Strategy:
            strategy_class = var_val
            break
    
    return strategy_class


def create_strategy_return_fn(backtest_path: str, data_path: str) -> callable:
    """
    Create a function that evaluates a generated backtest strategy on a price window.
    
    Accepts either a list of prices (legacy) or a DataFrame slice.
    
    Args:
        backtest_path: Path to the generated backtest .py file
        data_path: Path to the original data CSV
    
    Returns:
        A function that takes prices (list or DataFrame) and returns the strategy's return
    """
    backtest_code = Path(backtest_path).read_text(encoding='utf-8')
    reusable_code = _build_reusable_code(backtest_code)
    
    def strategy_return_fn(prices_input) -> float:
        """Run the generated strategy and return the return as a decimal."""
        try:
            if isinstance(prices_input, pd.DataFrame):
                # DataFrame mode (used by validate_with_dataframes)
                df = _add_session_columns(prices_input)
                strategy_class = _extract_strategy_class(reusable_code, df)
                if strategy_class is None:
                    return 0.0
                return _run_backtest_on_df(df, strategy_class)
            
            elif isinstance(prices_input, (list, np.ndarray)):
                # Legacy mode: list of closing prices
                prices = list(prices_input)
                if len(prices) < 30:
                    return 0.0
                
                n = len(prices)
                opens = [prices[0]] + prices[:-1]
                highs = [max(o, c) * 1.002 for o, c in zip(opens, prices)]
                lows = [min(o, c) * 0.998 for o, c in zip(opens, prices)]
                
                df = pd.DataFrame({
                    'Open': opens,
                    'High': highs,
                    'Low': lows,
                    'Close': prices,
                    'Volume': [1000.0] * n,
                })
                df.index = pd.date_range('2024-01-01', periods=n, freq='15min')
                df = _add_session_columns(df)
                
                strategy_class = _extract_strategy_class(reusable_code, df)
                if strategy_class is None:
                    return 0.0
                return _run_backtest_on_df(df, strategy_class)
            
            return 0.0
            
        except Exception:
            return 0.0
    
    # Store the reusable code for use in DataFrame mode
    strategy_return_fn._reusable_code = reusable_code
    strategy_return_fn.__name__ = Path(backtest_path).stem.replace('_BTFinal', '')
    
    return strategy_return_fn
