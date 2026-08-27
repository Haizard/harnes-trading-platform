"""
🌙 Moon Dev's Data Fusion Layer
Unifies signals from multiple sources (Binance, BirdEye, etc.)
Built with love by Moon Dev 🚀
"""

import asyncio
from termcolor import colored, cprint
from src.data.storage.mongo_db import MongoStorage

class DataFusion:
    def __init__(self):
        self.storage = MongoStorage()
        cprint("[FUSION] Data Fusion Layer initialized", "white", "on_blue")

    async def get_unified_signal(self, token_address, binance_symbol="BTCUSDT"):
        """
        Combine BirdEye token data with Binance market trends.
        """
        try:
            await self.storage.connect()
            
            # 1. Fetch latest BirdEye data for the token
            token_doc = await self.storage.db["tokens"].find_one(
                {"address": token_address},
                sort=[("timestamp", -1)]
            )
            
            # 2. Fetch latest Binance trend (e.g., BTC)
            market_doc = await self.storage.db["trades"].find_one(
                {"symbol": binance_symbol.upper()},
                sort=[("timestamp", -1)]
            )
            
            if not token_doc or not market_doc:
                return {"signal": "NEUTRAL", "reason": "Missing data for fusion"}

            # Logic: Confirm BirdEye volume spike with Binance sentiment
            token_data = token_doc["data"]["overview"]
            market_data = market_doc["data"]
            
            # Dummy logic for demonstration
            # In production, this would use the pre-calculated features from 'features_dataset'
            token_v_spike = token_data.get('v24hChangePercent', 0) > 10
            market_bullish = market_data.get('price', 0) > 0 # Simple check
            
            if token_v_spike and market_bullish:
                signal = "BULLISH"
                reason = "BirdEye volume spike confirmed by market trend"
            elif not market_bullish:
                signal = "CAUTION"
                reason = "Token signal ignored due to bearish market trend"
            else:
                signal = "NEUTRAL"
                reason = "No fused signal detected"

            return {
                "signal": signal,
                "reason": reason,
                "token_price": token_data.get('price'),
                "market_price": market_data.get('price')
            }
            
        except Exception as e:
            cprint(f"[ERROR] Data fusion failed: {str(e)}", "white", "on_red")
            return {"signal": "ERROR", "reason": str(e)}

if __name__ == "__main__":
    fusion = DataFusion()
    # Replace with a real token address from MONITORED_TOKENS
    addr = "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"
    print(asyncio.run(fusion.get_unified_signal(addr)))
