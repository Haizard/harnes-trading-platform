"""
🌙 Moon Dev's Binance WebSocket Collector
Real-time trade and depth data collection
Built with love by Moon Dev 🚀
"""

import json
import asyncio
from binance.websocket.spot.websocket_stream import SpotWebsocketStreamClient
from termcolor import colored, cprint
from datetime import datetime

from src.data.processing.cleaner import DataCleaner
from src.db_storage import save_binance_depth_update, save_binance_market_trade

class BinanceWS:
    def __init__(self, symbol="btcusdt"):
        self.symbol = symbol.lower()
        self.queue = asyncio.Queue()
        self.loop = None
        self.client = None
        
        self.cleaner = DataCleaner()
        
        cprint(f"[WS] Moon Dev's WebSocket Collector initialized for {self.symbol.upper()}", "white", "on_blue")

    def handle_message(self, _, message):
        """Thread-safe callback to push messages into the async queue"""
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.queue.put_nowait, message)

    async def message_processor(self):
        """Background task to process messages from the queue"""
        while True:
            try:
                message = await self.queue.get()
                data = json.loads(message)
                
                # Identify message type
                if "e" in data:
                    event_type = data["e"]
                    if event_type == "aggTrade":
                        await self.process_trade(data)
                    elif event_type == "depthUpdate":
                        await self.process_depth(data)
                elif "bids" in data or "asks" in data or "b" in data or "a" in data:
                    # Partial depth stream doesn't have an "e" field
                    await self.process_depth(data)
                
                self.queue.task_done()
            except Exception as e:
                cprint(f"[ERROR] Error processing queue message: {str(e)}", "white", "on_red")

    async def process_trade(self, data):
        """Process and store aggregate trade data"""
        try:
            # 1. Clean data
            cleaned_data = self.cleaner.clean_agg_trade(data)
            if not cleaned_data:
                return

            # 2. Extract for logging
            price = cleaned_data["price"]
            quantity = cleaned_data["quantity"]
            side = "SELL" if cleaned_data["is_buyer_maker"] else "BUY"
            timestamp_str = datetime.fromtimestamp(cleaned_data["timestamp"] / 1000).strftime('%H:%M:%S')
            
            cprint(f"[*] {timestamp_str} | {side} {self.symbol.upper()} | {price} | Qty: {quantity}", "green" if side == "BUY" else "red")
            
            await asyncio.to_thread(save_binance_market_trade, self.symbol, {
                **cleaned_data,
                "side": side,
                "raw_data": data,
            })
        except Exception as e:
            cprint(f"[ERROR] Error in process_trade: {str(e)}", "white", "on_red")

    async def process_depth(self, data):
        """Process and store partial depth updates"""
        try:
            await asyncio.to_thread(save_binance_depth_update, self.symbol, {
                "event_time": data.get("E"),
                "first_update_id": data.get("U"),
                "final_update_id": data.get("u"),
                "last_update_id": data.get("lastUpdateId"),
                "bids": data.get("b", data.get("bids", [])),
                "asks": data.get("a", data.get("asks", [])),
                "raw_data": data,
            })
        except Exception as e:
            cprint(f"[ERROR] Error in process_depth: {str(e)}", "white", "on_red")

    async def start(self):
        """Start the WebSocket streams and processor"""
        cprint(f"[WS] Moon Dev's AI Agent starting streams for {self.symbol.upper()}...", "white", "on_blue")
        
        self.loop = asyncio.get_running_loop()
        
        try:
            # Initialize client with thread-safe handler
            self.client = SpotWebsocketStreamClient(on_message=self.handle_message)
            
            # Subscribe to aggregate trades
            self.client.agg_trade(symbol=self.symbol)
            
            # Subscribe to diff-depth updates with sequence IDs for reconstruction.
            self.client.diff_book_depth(symbol=self.symbol, speed=100)
            
            # Start background processor task
            processor_task = asyncio.create_task(self.message_processor())
            
            # Keep the main loop alive
            while True:
                await asyncio.sleep(1)
        except Exception as e:
            cprint(f"[ERROR] Error in WebSocket stream: {str(e)}", "white", "on_red")
        finally:
            await self.stop()

    async def stop(self):
        """Stop the WebSocket streams and close connections"""
        cprint(f"[WS] Moon Dev's WebSocket Collector shutting down gracefully...", "white", "on_blue")
        if self.client:
            self.client.stop()

if __name__ == "__main__":
    collector = BinanceWS()
    try:
        asyncio.run(collector.start())
    except KeyboardInterrupt:
        pass
