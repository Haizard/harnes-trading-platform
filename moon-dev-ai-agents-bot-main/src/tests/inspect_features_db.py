"""Inspect last 2 features_dataset docs to debug autonomous field storage."""
import asyncio
import sys
import os

sys.path.insert(0, os.getcwd())

async def inspect():
    from src.data.storage.mongo_db import MongoStorage
    s = MongoStorage()
    await s.connect()

    docs = await s.db["features_dataset"].find(
        {"symbol": "BTCUSDT"}, sort=[("_id", -1)]
    ).limit(3).to_list(3)

    for i, d in enumerate(docs):
        ts   = d.get("timestamp")
        data = d.get("data", {})
        auto = data.get("autonomous", {})
        keys = list(auto.keys()) if auto else []
        bp   = auto.get("buy_pressure", "MISSING")
        print(f"Doc {i}: ts={ts}")
        print(f"  top-level keys: {list(data.keys())}")
        print(f"  autonomous_keys: {keys}")
        print(f"  buy_pressure: {bp}")
        print()

    await s.close()

if __name__ == "__main__":
    asyncio.run(inspect())
