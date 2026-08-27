"""
🌙 Moon Dev's FRED Collector
Fetches macro economic data (Rates, Inflation, GDP)
Built with love by Moon Dev 🚀
"""

import os
import httpx
from termcolor import colored, cprint
from src.data.storage.mongo_db import MongoStorage

class FREDCollector:
    def __init__(self):
        self.api_key = os.getenv("FRED_API_KEY")
        self.base_url = "https://api.stlouisfed.org/fred"
        self.storage = MongoStorage()
        cprint("[FRED] Moon Dev's FRED Collector initialized", "white", "on_blue")

    async def get_series_data(self, series_id):
        """Fetch series data from FRED"""
        if not self.api_key:
            cprint("⚠️ FRED_API_KEY not found in .env", "yellow")
            return None

        cprint(f"[FRED] Fetching series {series_id}...", "cyan")
        url = f"{self.base_url}/series/observations"
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 10
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params)
                data = response.json()
                
                if "observations" in data:
                    await self.storage.save_market_data(series_id, "macro", data["observations"])
                    cprint(f"[SUCCESS] Saved {series_id} macro data to MongoDB", "green")
                    return data["observations"]
                return None
        except Exception as e:
            cprint(f"❌ Error fetching FRED data: {str(e)}", "red")
            return None

if __name__ == "__main__":
    import asyncio
    collector = FREDCollector()
    # Test with Fed Funds Rate (FEDFUNDS)
    asyncio.run(collector.get_series_data("FEDFUNDS"))
