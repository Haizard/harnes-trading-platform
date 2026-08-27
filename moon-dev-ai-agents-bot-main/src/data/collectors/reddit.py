"""
🌙 Moon Dev's Reddit Sentiment Collector
Scrapes crypto-related subreddits for sentiment signals
Built with love by Moon Dev 🚀
"""

import httpx
import os
from termcolor import colored, cprint
from src.data.storage.mongo_db import MongoStorage

class RedditCollector:
    def __init__(self):
        self.storage = MongoStorage()
        cprint("[REDDIT] Moon Dev's Reddit Collector initialized", "white", "on_blue")

    async def get_subreddit_posts(self, subreddit="CryptoCurrency", limit=25):
        """Fetch latest posts from a subreddit (Public JSON API)"""
        cprint(f"[REDDIT] Fetching latest posts from r/{subreddit}...", "cyan")
        url = f"https://www.reddit.com/r/{subreddit}/new.json?limit={limit}"
        headers = {'User-Agent': 'MoonDevTradingBot/1.0'}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
                data = response.json()
                
                if "data" in data and "children" in data["data"]:
                    posts = [child["data"] for child in data["data"]["children"]]
                    await self.storage.save_market_data(f"r/{subreddit}", "sentiment", posts)
                    cprint(f"[SUCCESS] Saved {len(posts)} Reddit posts to MongoDB", "green")
                    return posts
                return None
        except Exception as e:
            cprint(f"❌ Error fetching Reddit data: {str(e)}", "red")
            return None

if __name__ == "__main__":
    import asyncio
    collector = RedditCollector()
    asyncio.run(collector.get_subreddit_posts())
