"""
Full SentimentAgent — BERTweet ML Model + Twitter Scraping
Ported from System 1 (SentimentAgent) to System 2 (MicroEngine).

Uses:
- BERTweet model (finiteautomata/bertweet-base-sentiment-analysis) for ML sentiment
- twikit for Twitter scraping (real-time tweets)
- Keyword-based fallback when ML model unavailable

Flow:
1. Search Twitter for token mentions
2. Analyze sentiment with BERTweet (or keyword fallback)
3. Score and cache results
4. Feed into AI Orchestrator as additional signal
"""

import os
import json
import time
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
from random import randint
from termcolor import cprint


# ── ML Model (BERTweet) ──────────────────────────────────────
_BERTWEET_AVAILABLE = False
_tokenizer = None
_model = None

try:
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    _BERTWEET_AVAILABLE = True
except ImportError:
    pass

# ── Twitter Client ──────────────────────────────────────
_TWIKIT_AVAILABLE = False
try:
    from twikit import Client as TwikitClient
    _TWIKIT_AVAILABLE = True
except ImportError:
    pass


# ── Keyword Lists (fallback when ML unavailable) ──────────────
POSITIVE_KEYWORDS = [
    "moon", "moonshot", "bullish", "pump", "pumping", "breakout", "squeeze",
    "accumulate", "buy", "buying", "long", "hodl", "hold", "gem", "alpha",
    "undervalued", "cheap", "entry", "launch", "launching", "early", "massive",
    "huge", "gains", "profit", "up", "rising", "rally", "surge", "rocket",
    "fire", "letsgo", "wagmi", "bullrun", "ath", "new high",
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

NOISE_WORDS = [
    "t.co", "discord", "join", "telegram", "discount", "pay", "subscribe",
    "follow", "like", "retweet", "giveaway", "airdrop", "free", "bot",
]


class FullSentimentAgent:
    """
    Full sentiment analysis with BERTweet ML model + Twitter scraping.
    
    Priority:
      1. BERTweet ML model (if torch + transformers installed)
      2. Keyword-based scoring (always available)
      3. Cached results (fast)
    """

    def __init__(self):
        self.data_dir = Path("src/data/sentiment")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sentiment_file = self.data_dir / "token_sentiment.json"
        self.cache = self._load_cache()
        self._client = None
        self._model_loaded = False
        
        # Try to load BERTweet model
        if _BERTWEET_AVAILABLE:
            try:
                cprint("[SENTIMENT] Loading BERTweet model...", "cyan")
                _tokenizer = AutoTokenizer.from_pretrained(
                    "finiteautomata/bertweet-base-sentiment-analysis"
                )
                _model = AutoModelForSequenceClassification.from_pretrained(
                    "finiteautomata/bertweet-base-sentiment-analysis"
                )
                _model.eval()
                self._model_loaded = True
                cprint("[SENTIMENT] BERTweet model loaded successfully", "green")
            except Exception as e:
                cprint(f"[SENTIMENT] BERTweet load failed: {e} — using keyword fallback", "yellow")
        
        mode = "BERTweet ML" if self._model_loaded else "Keyword fallback"
        cprint(f"[SENTIMENT] Full SentimentAgent initialized — {mode}", "white", "on_blue")

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

    def _init_twitter_client(self):
        """Initialize Twitter client using saved cookies."""
        if not _TWIKIT_AVAILABLE:
            return None
        if not os.path.exists("cookies.json"):
            return None
        try:
            client = TwikitClient()
            client.load_cookies("cookies.json")
            return client
        except Exception as e:
            cprint(f"[SENTIMENT] Twitter client init failed: {e}", "yellow")
            return None

    def _search_tweets(self, query: str, limit: int = 20) -> List[str]:
        """Search Twitter for tweets matching query."""
        if not self._client:
            self._client = self._init_twitter_client()
        if not self._client:
            return []
        
        try:
            time.sleep(randint(1, 3))
            tweets = self._client.search_tweet(query, "Latest", count=limit)
            return [tweet.text for tweet in tweets]
        except Exception as e:
            cprint(f"[SENTIMENT] Twitter search error: {e}", "yellow")
            return []

    def analyze_sentiment_ml(self, texts: List[str]) -> float:
        """Analyze sentiment using BERTweet ML model."""
        if not self._model_loaded or not texts:
            return 0.0
        
        try:
            import torch
            
            sentiments = []
            batch_size = 8
            
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                inputs = _tokenizer(
                    batch, padding=True, truncation=True,
                    max_length=128, return_tensors="pt"
                )
                with torch.no_grad():
                    outputs = _model(**inputs)
                    predictions = torch.nn.functional.softmax(outputs.logits, dim=-1)
                    sentiments.extend(predictions.tolist())
            
            # Convert to -1 to 1 scale (NEG=0, NEU=1, POS=2)
            scores = []
            for s in sentiments:
                neg, neu, pos = s
                score = pos - neg
                scores.append(score)
            
            return sum(scores) / len(scores) if scores else 0.0
            
        except Exception as e:
            cprint(f"[SENTIMENT] ML analysis error: {e}", "yellow")
            return 0.0

    def analyze_sentiment_keywords(self, texts: List[str]) -> float:
        """Analyze sentiment using keyword matching (fallback)."""
        if not texts:
            return 0.0
        
        total_score = 0
        for text in texts:
            text_lower = text.lower()
            words = set(text_lower.split())
            
            pos_count = sum(1 for w in POSITIVE_KEYWORDS if w in text_lower)
            neg_count = sum(1 for w in NEGATIVE_KEYWORDS if w in text_lower)
            noise_count = sum(1 for w in NOISE_WORDS if w in text_lower)
            
            if pos_count + neg_count == 0:
                continue
            
            score = (pos_count - neg_count) / max(pos_count + neg_count, 1)
            total_score += score
        
        return total_score / len(texts) if texts else 0.0

    def get_token_sentiment(self, symbol: str, use_twitter: bool = True) -> dict:
        """
        Get sentiment for a token.
        
        Args:
            symbol: Token symbol (e.g., "PUMP", "FART")
            use_twitter: Whether to search Twitter (True) or use cache only (False)
        
        Returns:
            dict with score, label, tweet_count, source, timestamp
        """
        # Check cache first (5-minute TTL)
        cache_key = symbol.upper()
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            cached_time = datetime.fromisoformat(cached.get("timestamp", "2000-01-01"))
            if (datetime.now(timezone.utc) - cached_time).total_seconds() < 300:
                return cached
        
        result = {
            "symbol": symbol.upper(),
            "score": 0.0,
            "label": "neutral",
            "tweet_count": 0,
            "source": "cache",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        if not use_twitter:
            return result
        
        # Search Twitter
        queries = [
            f"${symbol.upper()} solana",
            f"{symbol.upper()} solana crypto",
            f"${symbol.upper()} pump.fun",
        ]
        
        all_texts = []
        for query in queries:
            tweets = self._search_tweets(query, limit=10)
            all_texts.extend(tweets)
            if len(all_texts) >= 15:
                break
        
        if not all_texts:
            result["source"] = "no_tweets"
            return result
        
        result["tweet_count"] = len(all_texts)
        
        # Analyze with ML or keywords
        if self._model_loaded:
            score = self.analyze_sentiment_ml(all_texts)
            result["source"] = "bertweet"
        else:
            score = self.analyze_sentiment_keywords(all_texts)
            result["source"] = "keywords"
        
        result["score"] = round(score, 3)
        
        # Convert to label
        if score > 0.3:
            result["label"] = "very_bullish"
        elif score > 0.1:
            result["label"] = "bullish"
        elif score > -0.1:
            result["label"] = "neutral"
        elif score > -0.3:
            result["label"] = "bearish"
        else:
            result["label"] = "very_bearish"
        
        # Cache result
        self.cache[cache_key] = result
        self._save_cache()
        
        cprint(
            f"[SENTIMENT] {symbol}: {result['label']} "
            f"(score={score:+.2f}, tweets={len(all_texts)}, "
            f"source={result['source']})",
            "green" if score > 0.1 else ("red" if score < -0.1 else "yellow")
        )
        
        return result

    def get_stats(self) -> dict:
        """Get sentiment statistics."""
        return {
            "cached_tokens": len(self.cache),
            "bertweet_available": self._model_loaded,
            "twitter_available": self._client is not None,
        }


# ── Singleton ──────────────────────────────────────────────
_sentiment_instance = None

def get_full_sentiment_agent() -> FullSentimentAgent:
    """Get or create the singleton FullSentimentAgent instance."""
    global _sentiment_instance
    if _sentiment_instance is None:
        _sentiment_instance = FullSentimentAgent()
    return _sentiment_instance
