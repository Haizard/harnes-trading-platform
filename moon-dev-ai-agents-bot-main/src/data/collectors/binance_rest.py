"""
🌙 Moon Dev's Binance REST Collector
Historical data bootstrapping for backtesting and research
Built with love by Moon Dev 🚀
"""

import os
import pandas as pd
from binance.client import Client
from termcolor import colored, cprint
from datetime import datetime, timedelta

class BinanceREST:
    def __init__(self):
        # Public data doesn't require API keys
        self.client = Client()
        cprint("[REST] Moon Dev's Binance REST Collector initialized", "white", "on_blue")

    def fetch_historical_klines(self, symbol="BTCUSDT", interval="1h", days_back=30):
        """Fetch historical OHLCV data"""
        cprint(f"[REST] Moon Dev's AI Agent fetching {days_back} days of {interval} data for {symbol}...", "white", "on_blue")
        
        try:
            # Calculate start time
            start_str = (datetime.now() - timedelta(days=days_back)).strftime("%d %b, %Y")
            
            # Fetch klines
            klines = self.client.get_historical_klines(symbol, interval, start_str)
            
            # Process into DataFrame
            df = pd.DataFrame(klines, columns=[
                'Open time', 'Open', 'High', 'Low', 'Close', 'Volume',
                'Close time', 'Quote asset volume', 'Number of trades',
                'Taker buy base asset volume', 'Taker buy quote asset volume', 'Ignore'
            ])
            
            # Conver timestamps to readable dates
            df['Datetime'] = pd.to_datetime(df['Open time'], unit='ms')
            
            # Select and format columns
            df = df[['Datetime', 'Open', 'High', 'Low', 'Close', 'Volume']]
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df[col] = df[col].astype(float)
                
            cprint(f"[SUCCESS] Successfully fetched {len(df)} candles for {symbol}", "white", "on_green")
            return df
            
        except Exception as e:
            cprint(f"[ERROR] Error fetching historical data: {str(e)}", "white", "on_red")
            return None

if __name__ == "__main__":
    collector = BinanceREST()
    data = collector.fetch_historical_klines()
    if data is not None:
        print(data.tail())
