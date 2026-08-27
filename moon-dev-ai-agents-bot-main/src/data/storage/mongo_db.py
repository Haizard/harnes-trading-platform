"""
🌙 Moon Dev's MongoDB Storage Layer
Efficient time-series storage for market data
Built with love by Moon Dev 🚀
"""

import os
from motor.motor_asyncio import AsyncIOMotorClient
from termcolor import colored, cprint
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class MongoStorage:
    def __init__(self):
        self.mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        self.db_name = os.getenv("MONGO_DB_NAME", "moon_dev_trading")
        self.client = None
        self.db = None
        cprint("[STORAGE] Moon Dev's MongoDB Storage Layer initialized", "white", "on_blue")

    async def connect(self):
        """Establish connection to MongoDB"""
        try:
            self.client = AsyncIOMotorClient(self.mongo_uri)
            self.db = self.client[self.db_name]
            # Test connection
            await self.client.admin.command('ping')
            cprint("[SUCCESS] Successfully connected to MongoDB", "white", "on_green")
            
            # Initialize collections and indexes
            await self._setup_indexes()
        except Exception as e:
            cprint(f"[ERROR] Failed to connect to MongoDB: {str(e)}", "white", "on_red")
            raise

    async def _setup_indexes(self):
        """Setup necessary indexes for performance"""
        try:
            # Common indexes for time-series data
            collections = ["trades", "orderbook_snapshots", "ohlcv", "tokens", "market_metrics"]
            for coll in collections:
                await self.db[coll].create_index([("symbol", 1), ("timestamp", -1)])
                await self.db[coll].create_index([("timestamp", -1)])
            
            # Special index for token metadata
            await self.db["tokens"].create_index([("address", 1)], unique=True, sparse=True)
            
            cprint("[SUCCESS] MongoDB indexes initialized", "white", "on_green")
        except Exception as e:
            cprint(f"[WARN] Index creation skipped/failed: {str(e)}", "white", "on_yellow")

    async def save_to_collection(self, collection_name, symbol, payload):
        """Save data to a specific collection"""
        if self.db is None or self.client is None:
            await self.connect()
            
        document = {
            "symbol": symbol.upper(),
            "timestamp": datetime.utcnow(),
            "data": payload
        }
        
        try:
            await self.db[collection_name].insert_one(document)
        except Exception as e:
            cprint(f"[ERROR] Error saving to {collection_name}: {str(e)}", "white", "on_red")

    async def save_market_data(self, symbol, data_type, payload):
        """Save raw market data to the legacy market_data collection"""
        if self.db is None or self.client is None:
            await self.connect()
            
        if self.db is not None:
            collection = self.db["market_data"]
            document = {
                "symbol": symbol.upper(),
                "type": data_type,
                "timestamp": datetime.utcnow(),
                "data": payload
            }
            
            try:
                await collection.insert_one(document)
            except Exception as e:
                cprint(f"[ERROR] Error saving market data: {str(e)}", "white", "on_red")

    async def close(self):
        """Close the MongoDB connection"""
        if self.client:
            self.client.close()
            cprint("[CLOSED] MongoDB connection closed", "white", "on_blue")
