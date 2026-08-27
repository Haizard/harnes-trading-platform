"""
🌙 Moon Dev's Yahoo Finance Collector
Cross-market correlation data (Forex, Indices, Crypto)
Built with love by Moon Dev 🚀
"""

import yfinance as yf
import asyncio
import pandas as pd
from termcolor import colored, cprint
from src.data.storage.mongo_db import MongoStorage

class YahooFinanceCollector:
    def __init__(self):
        self.storage = MongoStorage()
        cprint("[CORRELATION] Moon Dev's Yahoo Finance Collector initialized", "white", "on_blue")

    async def get_historical_data(self, ticker="BTC-USD", period="1mo", interval="1h"):
        """Fetch historical data for a ticker and save to MongoDB"""
        cprint(f"[CORRELATION] Moon Dev's AI Agent fetching data for {ticker} from Yahoo Finance...", "white", "on_blue")
        
        try:
            data = yf.download(ticker, period=period, interval=interval)
            
            if data.empty:
                cprint(f"[WARN] No data found for {ticker}", "white", "on_yellow")
                return None
                
            # Save to MongoDB
            await self.storage.connect()
            
            # Reset index to get Datetime as a column
            df_reset = data.reset_index()
            
            # Ensure all column names are strings (handles multi-index if present)
            if isinstance(df_reset.columns, pd.MultiIndex):
                df_reset.columns = ['_'.join(col).strip() if col[1] else col[0] for col in df_reset.columns.values]
            else:
                df_reset.columns = [str(col) for col in df_reset.columns]
                
            json_data = df_reset.to_dict(orient='records')
            for entry in json_data:
                # Use ticker as symbol, and 'correlation' as type for cross-market data
                await self.storage.save_market_data(ticker, "correlation", entry)
            await self.storage.close()
            
            cprint(f"[SUCCESS] Successfully fetched and stored {len(data)} rows for {ticker}", "white", "on_green")
            return data
            
        except Exception as e:
            cprint(f"[ERROR] Error fetching/storing Yahoo Finance data: {str(e)}", "white", "on_red")
            return None

if __name__ == "__main__":
    collector = YahooFinanceCollector()
    try:
        data = asyncio.run(collector.get_historical_data())
        if data is not None:
            print(data.tail())
    except KeyboardInterrupt:
        pass
