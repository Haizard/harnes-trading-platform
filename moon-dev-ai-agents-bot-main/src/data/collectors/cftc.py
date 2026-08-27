"""
🌙 Moon Dev's CFTC COT Collector
Fetches institutional positioning (Commitment of Traders)
Built with love by Moon Dev 🚀
"""

import httpx
import pandas as pd
from io import StringIO
from termcolor import colored, cprint
from src.data.storage.mongo_db import MongoStorage

class CFTCCollector:
    def __init__(self):
        self.storage = MongoStorage()
        self.base_url = "https://www.cftc.gov/files/dea/history"
        cprint("[CFTC] Moon Dev's CFTC Collector initialized", "white", "on_blue")

    async def get_cot_data(self, year=2024):
        """Fetch COT historical data (Compressed/Annual)"""
        cprint(f"[CFTC] Fetching COT data for {year}...", "cyan")
        
        # Financial Futures Report (Traders in Financial Futures)
        year_suffix = str(year)[-2:]
        url = f"{self.base_url}/deafut{year_suffix}.zip"
        
        try:
            async with httpx.AsyncClient() as client:
                # COT data is typically distributed in large zip files for history
                # But for real-time/latest, we can use the latest report text
                latest_url = "https://www.cftc.gov/dea/newcot/fin_fut_txt.txt"
                response = await client.get(latest_url)
                
                if response.status_code == 200:
                    # Simple placeholder for COT parsing - usually requires complex mapping
                    report_text = response.text[:1000] # Save a snippet for now
                    await self.storage.save_market_data("CFTC_COT", "institutional", {"report": report_text})
                    cprint("[SUCCESS] Saved latest COT report snippet to MongoDB", "green")
                    return True
                return False
        except Exception as e:
            cprint(f"❌ Error fetching CFTC data: {str(e)}", "red")
            return False

if __name__ == "__main__":
    import asyncio
    collector = CFTCCollector()
    asyncio.run(collector.get_cot_data())
