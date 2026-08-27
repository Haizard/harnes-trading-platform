"""
🌙 Moon Dev's Binance WebSocket Collector
Real-time trade and depth data collection
Built with love by Moon Dev 🚀
"""

import os
import json
import asyncio
from binance.websocket.spot.websocket_stream import SpotWebsocketStreamClient
from termcolor import colored, cprint
from datetime import datetime

# Import Moon Dev components
from src.data.storage.mongo_db import MongoStorage
from src.data.processing.cleaner import DataCleaner

class BinanceWS:
    def __init__(self, symbol="btcusdt"):
        self.symbol = symbol.lower()
        self.queue = asyncio.Queue()
        self.loop = None
        self.client = None
        
        # Initialize storage and cleaning agents
        self.storage = MongoStorage()
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
            
            # 3. Save to MongoDB
            await self.storage.save_to_collection("trades", self.symbol, cleaned_data)
        except Exception as e:
            cprint(f"[ERROR] Error in process_trade: {str(e)}", "white", "on_red")

    async def process_depth(self, data):
        """Process and store partial depth updates"""
        try:
            # Save raw depth data for order book reconstruction
            await self.storage.save_to_collection("orderbook_snapshots", self.symbol, data)
        except Exception as e:
            cprint(f"[ERROR] Error in process_depth: {str(e)}", "white", "on_red")

    async def start(self):
        """Start the WebSocket streams and processor"""
        cprint(f"[WS] Moon Dev's AI Agent starting streams for {self.symbol.upper()}...", "white", "on_blue")
        
        self.loop = asyncio.get_running_loop()
        
        try:
            # Connect to MongoDB first
            await self.storage.connect()
            
            # Initialize client with thread-safe handler
            self.client = SpotWebsocketStreamClient(on_message=self.handle_message)
            
            # Subscribe to aggregate trades
            self.client.agg_trade(symbol=self.symbol)
            
            # Subscribe to partial book depth (5 levels, 100ms update)
            self.client.partial_book_depth(symbol=self.symbol, level=5, speed=100)
            
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
        await self.storage.close()

if __name__ == "__main__":
    collector = BinanceWS()
    try:
        asyncio.run(collector.start())
    except KeyboardInterrupt:
        pass
