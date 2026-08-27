import asyncio
from src.data.storage.mongo_db import MongoStorage

async def check():
    s = MongoStorage()
    await s.connect()
    doc = await s.db['market_data'].find_one({'symbol': 'GC=F'})
    if doc:
        print(f"Keys for GC=F data: {doc['data'].keys()}")
    
    doc_btc = await s.db['market_data'].find_one({'symbol': 'BTC-USD'})
    if doc_btc:
        print(f"Keys for BTC-USD data: {doc_btc['data'].keys()}")
    await s.close()

if __name__ == "__main__":
    asyncio.run(check())
