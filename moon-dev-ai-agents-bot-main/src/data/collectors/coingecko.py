"""
🌙 Moon Dev's CoinGecko Collector
Macro market data collection for broad research
Built with love by Moon Dev 🚀
"""

import requests
import asyncio
import pandas as pd
from termcolor import colored, cprint
from datetime import datetime
from src.data.storage.mongo_db import MongoStorage

class CoinGeckoCollector:
    def __init__(self):
        self.base_url = "https://api.coingecko.com/api/v3"
        self.storage = MongoStorage()
        cprint("[MACRO] Moon Dev's CoinGecko Collector initialized", "white", "on_blue")

    async def get_market_data(self, vs_currency="usd", ids="bitcoin,ethereum,solana"):
        """Fetch current market data for top coins and save to storage"""
        cprint(f"[MACRO] Moon Dev's AI Agent fetching macro data for {ids}...", "white", "on_blue")
        
        url = f"{self.base_url}/coins/markets"
        params = {
            "vs_currency": vs_currency,
            "ids": ids,
            "order": "market_cap_desc",
            "per_page": 100,
            "page": 1,
            "sparkline": False
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            # Save to MongoDB
            await self.storage.connect()
            for coin in data:
                await self.storage.save_market_data(coin['symbol'], "macro", coin)
            await self.storage.close()
            
            df = pd.DataFrame(data)
            df = df[['id', 'symbol', 'name', 'current_price', 'market_cap', 'total_volume', 'price_change_percentage_24h']]
            
            cprint("[SUCCESS] Macro data fetched and stored successfully", "white", "on_green")
            return df
            
        except Exception as e:
            cprint(f"[ERROR] Error fetching/storing CoinGecko data: {str(e)}", "white", "on_red")
            return None

if __name__ == "__main__":
    collector = CoinGeckoCollector()
    try:
        data = asyncio.run(collector.get_market_data())
        if data is not None:
            print(data)
    except KeyboardInterrupt:
        pass
