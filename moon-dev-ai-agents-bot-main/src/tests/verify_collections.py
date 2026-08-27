import asyncio
import os
import sys
from datetime import datetime
from termcolor import cprint

# Add root to path
sys.path.append(os.getcwd())

from src.data.storage.mongo_db import MongoStorage

async def verify_data_collection():
    cprint("🌙 Moon Dev's Data Collection Audit 🚀", "white", "on_blue")
    
    storage = MongoStorage()
    await storage.connect()
    
    collections_to_check = [
        "trades",
        "orderbook_snapshots",
        "tokens",
        "market_data",
        "features_dataset"
    ]
    
    print("-" * 80)
    print(f"{'Collection':<20} | {'Count':<10} | {'Latest Update':<20} | {'Status'}")
    print("-" * 80)
    
    for coll_name in collections_to_check:
        try:
            collection = storage.db[coll_name]
            count = await collection.count_documents({})
            
            latest_doc = await collection.find_one(sort=[("_id", -1)])
            latest_ts = "N/A"
            if latest_doc:
                # Try to find a timestamp field
                ts = latest_doc.get("timestamp") or latest_doc.get("data", {}).get("timestamp")
                if not ts:
                    # Fallback to ObjectId timestamp
                    ts = latest_doc.get("_id").generation_time
                
                if isinstance(ts, datetime):
                    latest_ts = ts.strftime("%Y-%m-%d %H:%M")
                else:
                    latest_ts = str(ts)[:16]
            
            status = "✅ ACTIVE" if count > 0 else "❌ EMPTY"
            print(f"{coll_name:<20} | {count:<10} | {latest_ts:<20} | {status}")
            
        except Exception as e:
            print(f"{coll_name:<20} | {'ERROR':<10} | {str(e)[:20]:<20} | ⚠️ FAILED")

    print("-" * 80)
    await storage.close()

if __name__ == "__main__":
    asyncio.run(verify_data_collection())
