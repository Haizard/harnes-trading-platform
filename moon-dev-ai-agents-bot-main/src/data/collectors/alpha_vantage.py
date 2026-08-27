"""
🌙 Moon Dev's Alpha Vantage Collector
Broad macro market data (Forex, Stocks, Commodities)
Built with love by Moon Dev 🚀
"""

import os
import asyncio
from alpha_vantage.timeseries import TimeSeries
from alpha_vantage.cryptocurrencies import CryptoCurrencies
from termcolor import colored, cprint
from src.data.storage.mongo_db import MongoStorage

class AlphaVantageCollector:
    def __init__(self):
        self.api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "demo")
        self.storage = MongoStorage()
        cprint("[MACRO HISTORY] Moon Dev's Alpha Vantage Collector initialized", "white", "on_blue")

    async def get_crypto_daily(self, symbol="BTC", market="USD"):
        """Fetch daily historical data for crypto and save to MongoDB"""
        cprint(f"[MACRO HISTORY] Moon Dev's AI Agent fetching daily data for {symbol} from Alpha Vantage...", "white", "on_blue")
        
        try:
            cc = CryptoCurrencies(key=self.api_key, output_format='pandas')
            data, meta_data = cc.get_digital_currency_daily(symbol=symbol, market=market)
            
            # Save to MongoDB
            await self.storage.connect()
            
            # Reset index to get Date as a column
            df_reset = data.reset_index()
            
            # Ensure all column names are strings
            df_reset.columns = [str(col) for col in df_reset.columns]
            
            json_data = df_reset.to_dict(orient='records')
            for entry in json_data:
                await self.storage.save_market_data(symbol, "macro_history", entry)
            await self.storage.close()
            
            cprint(f"[SUCCESS] Successfully fetched and stored data for {symbol}", "white", "on_green")
            return data
            
        except Exception as e:
            cprint(f"[ERROR] Error fetching/storing Alpha Vantage data: {str(e)}", "white", "on_red")
            return None

if __name__ == "__main__":
    collector = AlphaVantageCollector()
    try:
        data = asyncio.run(collector.get_crypto_daily())
        if data is not None:
            print(data.head())
    except KeyboardInterrupt:
        pass
