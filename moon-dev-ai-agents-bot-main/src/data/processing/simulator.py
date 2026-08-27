"""
🌙 Moon Dev's Market Replay Engine
Simulates live market conditions using historical data from MongoDB
Built with love by Moon Dev 🚀
"""

import asyncio
import json
from datetime import datetime
from termcolor import colored, cprint
from src.data.storage.mongo_db import MongoStorage

class MarketReplay:
    def __init__(self, symbol="btcusdt", speed=1.0):
        self.symbol = symbol.lower()
        self.speed = speed
        self.storage = MongoStorage()
        cprint(f"[REPLAY] Moon Dev's Market Replay Engine initialized for {self.symbol.upper()} (Speed: {self.speed}x)", "white", "on_blue")

    async def start_replay(self, start_time, end_time=None):
        """Replay market data from MongoDB specialized collections"""
        cprint(f"[REPLAY] Moon Dev's AI Agent starting multi-source replay from {start_time}...", "white", "on_blue")
        
        try:
            await self.storage.connect()
            
            # Query criteria
            query = {
                "symbol": self.symbol.upper(),
                "timestamp": {"$gte": start_time}
            }
            if end_time:
                query["timestamp"]["$lte"] = end_time
                
            # Create cursors for both sources
            trade_cursor = self.storage.db["trades"].find(query).sort("timestamp", 1)
            depth_cursor = self.storage.db["orderbook_snapshots"].find(query).sort("timestamp", 1)
            
            # Simple interleaving logic
            next_trade = await trade_cursor.to_list(length=100)
            next_depth = await depth_cursor.to_list(length=100)
            
            last_msg_time = None
            
            while next_trade or next_depth:
                # Determine which message comes first
                t_time = next_trade[0]["timestamp"] if next_trade else datetime.max
                d_time = next_depth[0]["timestamp"] if next_depth else datetime.max
                
                if t_time < d_time:
                    doc = next_trade.pop(0)
                    doc["type"] = "trade" # Inject type back for generic emitter
                    if not next_trade:
                        next_trade = await trade_cursor.to_list(length=100)
                else:
                    doc = next_depth.pop(0)
                    doc["type"] = "depth"
                    if not next_depth:
                        next_depth = await depth_cursor.to_list(length=100)
                
                current_msg_time = doc["timestamp"]
                
                # Simulate time delay
                if last_msg_time:
                    delay = (current_msg_time - last_msg_time).total_seconds() / self.speed
                    if delay > 0 and delay < 10: # Cap delay to prevent long stalls
                        await asyncio.sleep(delay)
                
                # Emit data
                self.emit_data(doc)
                last_msg_time = current_msg_time
                
            cprint("[REPLAY] Multi-source replay completed!", "white", "on_green")
                
            cprint("[REPLAY] Replay completed!", "white", "on_green")
            
        except Exception as e:
            cprint(f"[ERROR] Error during replay: {str(e)}", "white", "on_red")
        finally:
            await self.storage.close()

    def emit_data(self, doc):
        """Simulate sending data to agents"""
        data_type = doc["type"]
        payload = doc["data"]
        timestamp = doc["timestamp"].strftime('%H:%M:%S')
        
        if data_type == "trade":
            price = payload.get("price")
            side = "SELL" if payload.get("is_buyer_maker") else "BUY"
            cprint(f"🎞️ REPLAY | {timestamp} | {side} | {price}", "cyan")
        elif data_type == "depth":
            cprint(f"🎞️ REPLAY | {timestamp} | DEPTH UPDATE", "blue")

if __name__ == "__main__":
    # Example usage: Replay last hour of data
    from datetime import datetime, timedelta
    replay = MarketReplay(speed=10.0) # 10x speed
    start = datetime.utcnow() - timedelta(hours=1)
    asyncio.run(replay.start_replay(start))
