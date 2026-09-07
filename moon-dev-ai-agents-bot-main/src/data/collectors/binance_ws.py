"""
🌙 Moon Dev's Binance WebSocket Collector
Real-time trade and depth data collection
Built with love by Moon Dev 🚀
"""

import json
import asyncio
import os
import requests
from binance.websocket.spot.websocket_stream import SpotWebsocketStreamClient
from termcolor import colored, cprint
from datetime import datetime

from src.data.processing.cleaner import DataCleaner
from src.db_storage import save_binance_depth_update, save_binance_market_trade, save_binance_market_trades_bulk, save_binance_orderbook_snapshot

class BinanceWS:
    def __init__(self, symbol="btcusdt"):
        self.symbol = symbol.lower()
        self.queue = asyncio.Queue()
        self.loop = None
        self.client = None
        self.log_trades = os.environ.get("BINANCE_RESEARCH_LOG_TRADES", "false").lower() == "true"
        
        self.cleaner = DataCleaner()
        
        cprint(f"[WS] Moon Dev's WebSocket Collector initialized for {self.symbol.upper()}", "white", "on_blue")

    async def backfill_trades(self):
        """Seed recent public aggregate trades before starting live streams."""
        try:
            target = max(1000, min(int(os.environ.get("BINANCE_RESEARCH_BACKFILL_LIMIT", "20000")), 50000))
            trades = []
            end_id = None
            while len(trades) < target:
                params = {"symbol": self.symbol.upper(), "limit": min(1000, target - len(trades))}
                if end_id is not None:
                    params["fromId"] = max(0, end_id - params["limit"])
                response = await asyncio.to_thread(requests.get, "https://api.binance.com/api/v3/aggTrades", params=params, timeout=15)
                response.raise_for_status()
                page = response.json()
                if not page:
                    break
                page_ids = [int(item["a"]) for item in page]
                end_id = min(page_ids) - 1
                for item in page:
                    cleaned = self.cleaner.clean_agg_trade({
                        "p": item["p"], "q": item["q"], "E": item["T"],
                        "m": item["m"], "a": item["a"],
                    })
                    if cleaned:
                        trades.append({
                            **cleaned,
                            "side": "SELL" if cleaned["is_buyer_maker"] else "BUY",
                            "raw_data": item,
                        })
                if len(page) < params["limit"]:
                    break
            trades.sort(key=lambda trade: trade["timestamp"])
            saved = await asyncio.to_thread(save_binance_market_trades_bulk, self.symbol, trades)
            cprint(f"[WS] Backfilled {saved}/{len(trades)} {self.symbol.upper()} trades across historical pages into PostgreSQL", "green")
        except Exception as e:
            cprint(f"[WS] Binance backfill skipped: {e}", "yellow")

    async def seed_orderbook(self):
        """Fetch the public REST snapshot required for diff-depth replay."""
        try:
            response = await asyncio.to_thread(requests.get, "https://api.binance.com/api/v3/depth", params={"symbol": self.symbol.upper(), "limit": 1000}, timeout=15)
            response.raise_for_status()
            snapshot = response.json()
            saved = await asyncio.to_thread(save_binance_orderbook_snapshot, self.symbol, {
                "last_update_id": snapshot["lastUpdateId"],
                "bids": snapshot.get("bids", []), "asks": snapshot.get("asks", []),
                "raw_data": snapshot,
            })
            cprint(f"[WS] Order-book snapshot seeded for {self.symbol.upper()} (id={saved})", "green")
        except Exception as e:
            cprint(f"[WS] Order-book snapshot skipped: {e}", "yellow")

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
            
            if self.log_trades:
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
            await self.backfill_trades()
            await self.seed_orderbook()
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
