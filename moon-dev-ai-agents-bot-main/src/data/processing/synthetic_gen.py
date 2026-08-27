"""
🌙 Moon Dev's Synthetic Data Generator
Simulates crashes, spikes, and liquidity scenarios
Built with love by Moon Dev 🚀
"""

import pandas as pd
import numpy as np
from termcolor import colored, cprint

class SyntheticGenerator:
    def __init__(self):
        cprint("[SYNTHETIC] Moon Dev's Synthetic Data Generator initialized", "white", "on_blue")

    def generate_crash(self, df, intensity=0.1, duration_bars=10):
        """Simulate a sudden market crash"""
        cprint(f"[SYNTHETIC] Injecting synthetic crash (Intensity: {intensity}) into dataset...", "white", "on_blue")
        
        df_synthetic = df.copy()
        start_idx = len(df_synthetic) // 2
        
        for i in range(duration_bars):
            if start_idx + i < len(df_synthetic):
                # Apply exponential decay to price
                decay = 1 - (intensity * (i + 1) / duration_bars)
                df_synthetic.iloc[start_idx + i, df_synthetic.columns.get_loc('Close')] *= decay
                
        cprint("[SUCCESS] Synthetic crash scenario generated", "white", "on_green")
        return df_synthetic

    def add_volatility_noise(self, df, std_dev=0.01):
        """Add Gaussian noise to simulate high volatility"""
        cprint(f"[SYNTHETIC] Injecting volatility noise (StdDev: {std_dev}) into dataset...", "white", "on_blue")
        
        df_synthetic = df.copy()
        noise = np.random.normal(1.0, std_dev, len(df_synthetic))
        df_synthetic['Close'] *= noise
        
        cprint("[SUCCESS] Volatility noise injected", "white", "on_green")
        return df_synthetic

if __name__ == "__main__":
    # Example: Create a 5% crash in Bitcoin data
    from src.data.collectors.binance_rest import BinanceREST
    collector = BinanceREST()
    real_data = collector.fetch_historical_klines()
    
    gen = SyntheticGenerator()
    crash_data = gen.generate_crash(real_data, intensity=0.05)
    print(crash_data.tail())
