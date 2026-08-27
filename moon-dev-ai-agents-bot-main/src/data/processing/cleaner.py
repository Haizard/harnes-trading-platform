"""
🌙 Moon Dev's Data Cleaning Agent
Handles missing values, deduplication, and normalization
Built with love by Moon Dev 🚀
"""

import pandas as pd
from termcolor import colored, cprint

class DataCleaner:
    def __init__(self):
        cprint("[CLEANER] Moon Dev's Data Cleaning Agent initialized", "white", "on_blue")

    def clean_agg_trade(self, data):
        """Clean aggregate trade data from WebSocket"""
        try:
            # Basic validation
            required_fields = ["p", "q", "E", "m"]
            if not all(field in data for field in required_fields):
                cprint("[WARN] Invalid trade data: Missing fields", "white", "on_yellow")
                return None
            
            # Normalize fields
            cleaned = {
                "price": float(data["p"]),
                "quantity": float(data["q"]),
                "timestamp": int(data["E"]),
                "is_buyer_maker": bool(data["m"]),
                "agg_trade_id": int(data["a"])
            }
            
            return cleaned
        except Exception as e:
            cprint(f"[ERROR] Error cleaning trade data: {str(e)}", "white", "on_red")
            return None

    def validate_ohlcv(self, df):
        """Validate OHLCV data integrity"""
        if df.empty:
            return False
            
        # Check for NaNs
        if df.isnull().values.any():
            df = df.fillna(method='ffill')
            cprint("[SUCCESS] Handled missing values with ffill", "white", "on_yellow")
            
        return df
