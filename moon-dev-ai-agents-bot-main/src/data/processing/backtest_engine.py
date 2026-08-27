"""
🌙 Moon Dev's Realistic Backtest Engine
Simulates trades with slippage and spread modeling
Built with love by Moon Dev 🚀
"""

import pandas as pd
from termcolor import colored, cprint

class BacktestEngine:
    def __init__(self, initial_capital=1000, slippage_bps=10):
        self.capital = initial_capital
        self.slippage_bps = slippage_bps
        self.positions = {}
        cprint(f"[BACKTEST] Engine initialized with ${initial_capital} capital", "white", "on_blue")

    def estimate_execution_price(self, side, nominal_price, depth_snapshot=None):
        """
        Estimate price with slippage.
        If depth_snapshot is provided, use the actual best bid/ask and top-level depth.
        Otherwise, use fixed slippage_bps.
        """
        slippage_factor = self.slippage_bps / 10000
        
        if depth_snapshot:
            # depth_snapshot is expected to be the 'data' field from orderbook_snapshots
            bids = depth_snapshot.get('b', [])
            asks = depth_snapshot.get('a', [])
            
            if side.upper() == "BUY" and asks:
                best_ask = float(asks[0][0])
                # Conservative estimate: execution is best_ask + spread/2 (simplified) or just best_ask
                execution_price = best_ask * (1 + slippage_factor)
            elif side.upper() == "SELL" and bids:
                best_bid = float(bids[0][0])
                execution_price = best_bid * (1 - slippage_factor)
            else:
                execution_price = nominal_price * (1 + (slippage_factor if side.upper() == "BUY" else -slippage_factor))
        else:
            execution_price = nominal_price * (1 + (slippage_factor if side.upper() == "BUY" else -slippage_factor))
            
        return execution_price

    def calculate_trade_pnl(self, entry_price, exit_price, quantity, side):
        """Calculate PnL for a trade"""
        if side.upper() == "BUY":
            return (exit_price - entry_price) * quantity
        else:
            return (entry_price - exit_price) * quantity
