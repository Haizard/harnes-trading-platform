"""
Lightweight Token Sentiment — No torch required.

Tracks individual tokens by searching Twitter for their name/symbol.
Uses keyword-based sentiment scoring (no ML model needed).

Flow:
  1. Scanner finds token → "PUMP"
  2. Search Twitter for "$PUMP" + "PUMP solana"
  3. Score tweets with keyword rules
  4. Save per-token sentiment to JSON
  5. DataGatherer reads it for the AI orchestrator

Setup:
  - Run twitter_login.py once to generate cookies.json
  - TWITTER_USERNAME, TWITTER_EMAIL, TWITTER_PASSWORD in .env
"""

import os
import json
import time
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
from random import randint
from src.event_bus import _fire_and_forget


# Keyword lists for sentiment scoring
POSITIVE_KEYWORDS = [
    "moon", "moonshot", "bullish", "pump", "pumping", "breakout", "squeeze",
    "accumulate", "buy", "buying", "long", "hodl", "hold", "gem", "alpha",
    "undervalued", "cheap", "entry", "launch", "launching", "early", "massive",
    "huge", "gains", "profit", "up", "rising", "rally", "surge", "rocket",
    "fire", "letsgo", "wagmi", "athy", "bullrun", "ath", "new high",
    "shill", "viral", "trending", "community", "diamond", "hands", "ape",
]

NEGATIVE_KEYWORDS = [
    "scam", "rug", "rugpull", "honeypot", "ponzi", "dump", "dumping",
    "bearish", "sell", "selling", "short", "dead", "deadcoin", "exit",
    "rekt", "loss", "losses", "crash", "down", "falling", "bleed",
    "bleeding", "avoid", "stay away", "trash", "shitcoin", "vaporware",
    "fraud", "stolen", "hack", "exploit", "drained", "gone", "worthless",
    "memecoin", "casino", "gamble", "gambling", "pvp", "dev sold",
    "team sold", "liquidity pull",
]

# Words to ignore (noise)
NOISE_WORDS = [
    "t.co", "discord", "join", "telegram", "discount", "pay", "subscribe",
    "follow", "like", "retweet", "giveaway", "airdrop", "free", "bot",
]


class LightweightSentiment:
    """Track per-token Twitter sentiment without heavy ML dependencies.
    DSH Pattern: EventBus events + PostgreSQL persistence.
    """

    def __init__(self, event_bus=None):
        self.data_dir = Path("src/data/sentiment")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sentiment_file = self.data_dir / "token_sentiment.json"
        self.tweets_dir = self.data_dir / "tweets"
        self.tweets_dir.mkdir(parents=True, exist_ok=True)
        self.cache = self._load_cache()
        self._client = None
        self.event_bus = event_bus  # DSH EventBus
        print("[SENTIMENT] Lightweight Sentiment initialized (no torch required)")

    def _load_cache(self) -> dict:
        if self.sentiment_file.exists():
            try:
                with open(self.sentiment_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_cache(self):
        try:
            with open(self.sentiment_file, "w") as f:
                json.dump(self.cache, f, indent=2, default=str)
        except Exception:
            pass

    def _init_twikit(self):
        """Initialize twikit client for Twitter scraping."""
        if self._client:
            return self._client
        try:
            import asyncio

            async def _init():
                from twikit import Client
                if not os.path.exists("cookies.json"):
                    print("[SENTIMENT] No cookies.json found — Twitter scraping disabled")
                    return None
                client = Client()
                await client.load_cookies("cookies.json")
                return client

            self._client = asyncio.run(_init())
            if self._client:
                print("[SENTIMENT] Twitter client connected via twikit")
            return self._client
        except ImportError:
            print("[SENTIMENT] twikit not installed — using mock sentiment")
            return None
        except Exception as e:
            print(f"[SENTIMENT] twikit init failed: {e}")
            return None

    def _score_text(self, text: str) -> float:
        """Score a single text using keyword rules. Returns -1 to +1."""
        text_lower = text.lower()
        words = re.findall(r'\w+', text_lower)

        pos = sum(1 for w in words if w in POSITIVE_KEYWORDS)
        neg = sum(1 for w in words if w in NEGATIVE_KEYWORDS)

        total = pos + neg
        if total == 0:
            return 0.0
        return (pos - neg) / total

    def _filter_noise(self, text: str) -> bool:
        """Return True if tweet should be ignored."""
        text_lower = text.lower()
        return any(word in text_lower for word in NOISE_WORDS)

    async def _fetch_tweets(self, query: str, limit: int = 20) -> List[dict]:
        """Fetch tweets from Twitter. Returns list of {text, score, user, time}."""
        client = self._init_twikit()
        if not client:
            return []

        tweets = []
        try:
            import asyncio
            results = await client.search_tweet(query, product="Latest")
            if results:
                for tweet in results:
                    if len(tweets) >= limit:
                        break
                    if self._filter_noise(tweet.text):
                        continue
                    tweets.append({
                        "text": tweet.text,
                        "user": tweet.user.name if hasattr(tweet, 'user') else "unknown",
                        "time": tweet.created_at if hasattr(tweet, 'created_at') else "",
                        "retweets": getattr(tweet, 'retweet_count', 0),
                        "likes": getattr(tweet, 'favorite_count', 0),
                    })
        except Exception as e:
            print(f"[SENTIMENT] Tweet fetch failed: {e}")

        return tweets

    def analyze_token(self, symbol: str) -> dict:
        """
        Analyze Twitter sentiment for a specific token.
        Returns: {score: float, label: str, tweet_count: int, tweets: list}
        """
        symbol = symbol.strip().upper()
        cache_key = symbol.lower()

        # Check cache (fresh for 15 minutes)
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            cached_time = datetime.fromisoformat(cached.get("timestamp", "2000-01-01"))
            if (datetime.now(timezone.utc) - cached_time.replace(tzinfo=timezone.utc)).total_seconds() < 900:
                return cached

        # Search Twitter for this token
        import asyncio
        queries = [f"${symbol}", f"{symbol} solana"]
        all_tweets = []

        for q in queries:
            try:
                tweets = asyncio.run(self._fetch_tweets(q, limit=15))
                all_tweets.extend(tweets)
                time.sleep(randint(1, 3))  # Rate limit protection
            except Exception:
                continue

        if not all_tweets:
            # No tweets found — return neutral
            result = {
                "score": 0.0,
                "label": "neutral",
                "tweet_count": 0,
                "positive_pct": 0,
                "negative_pct": 0,
                "top_bulls": [],
                "top_bears": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.cache[cache_key] = result
            self._save_cache()
            return result

        # Score all tweets
        scored = []
        for t in all_tweets:
            score = self._score_text(t["text"])
            scored.append({**t, "sentiment": score})

        # Calculate aggregate
        scores = [s["sentiment"] for s in scored]
        avg_score = sum(scores) / len(scores) if scores else 0
        pos_count = sum(1 for s in scores if s > 0.1)
        neg_count = sum(1 for s in scores if s < -0.1)

        # Label
        if avg_score > 0.3:
            label = "very bullish"
        elif avg_score > 0.1:
            label = "bullish"
        elif avg_score > -0.1:
            label = "neutral"
        elif avg_score > -0.3:
            label = "bearish"
        else:
            label = "very bearish"

        # Top bulls and bears
        sorted_scored = sorted(scored, key=lambda x: x["sentiment"], reverse=True)
        top_bulls = [{"user": s["user"], "text": s["text"][:100], "score": round(s["sentiment"], 2)}
                     for s in sorted_scored[:3] if s["sentiment"] > 0]
        top_bears = [{"user": s["user"], "text": s["text"][:100], "score": round(s["sentiment"], 2)}
                     for s in sorted_scored[-3:] if s["sentiment"] < 0]

        result = {
            "score": round(avg_score, 3),
            "label": label,
            "tweet_count": len(scored),
            "positive_pct": round(pos_count / len(scored) * 100, 1) if scored else 0,
            "negative_pct": round(neg_count / len(scored) * 100, 1) if scored else 0,
            "top_bulls": top_bulls,
            "top_bears": top_bears,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Save tweets to file
        tweet_file = self.tweets_dir / f"{symbol.lower()}_tweets.json"
        try:
            with open(tweet_file, "w") as f:
                json.dump(scored[:30], f, indent=2, default=str)
        except Exception:
            pass

        # Cache result
        self.cache[cache_key] = result
        self._save_cache()
        
        # DSH: Save to PostgreSQL
        try:
            from src.db_storage import log_event
            log_event("sentiment/token", {
                "symbol": symbol.upper(),
                "score": result.get("score", 0),
                "label": result.get("label", "unknown"),
                "tweet_count": result.get("tweet_count", 0),
            })
        except Exception:
            pass
        
        # DSH: Emit to EventBus
        if self.event_bus:
            try:
                import asyncio
                payload = {
                    "symbol": symbol.upper(),
                    "score": result.get("score", 0),
                    "label": result.get("label", "unknown"),
                }
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    _fire_and_forget(self.event_bus.emit("sentiment/token", payload))
                else:
                    loop.run_until_complete(self.event_bus.emit("sentiment/token", payload))
            except Exception:
                pass

        return result

    def get_token_sentiment(self, symbol: str) -> Optional[dict]:
        """Get cached sentiment for a token (no API call)."""
        key = symbol.strip().lower()
        return self.cache.get(key)

    def get_market_overview(self) -> dict:
        """Get overall market sentiment from all tracked tokens."""
        if not self.cache:
            return {"overall": "unknown", "tokens_tracked": 0}

        scores = [v["score"] for v in self.cache.values() if "score" in v]
        if not scores:
            return {"overall": "unknown", "tokens_tracked": 0}

        avg = sum(scores) / len(scores)
        bullish = sum(1 for s in scores if s > 0.1)
        bearish = sum(1 for s in scores if s < -0.1)

        return {
            "overall": "bullish" if avg > 0.1 else "bearish" if avg < -0.1 else "neutral",
            "avg_score": round(avg, 3),
            "tokens_tracked": len(scores),
            "bullish_tokens": bullish,
            "bearish_tokens": bearish,
        }


# Singleton
_sentiment_instance = None

def get_lightweight_sentiment(event_bus=None) -> LightweightSentiment:
    global _sentiment_instance
    if _sentiment_instance is None:
        _sentiment_instance = LightweightSentiment(event_bus=event_bus)
    return _sentiment_instance
