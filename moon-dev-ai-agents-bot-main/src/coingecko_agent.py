"""
CoinGecko Agent — Market Data & Token Discovery
Ported from System 1 (CoinGeckoAgent) to System 2 (MicroEngine).

Provides:
- Trending tokens discovery
- Market cap / volume data
- Price changes and historical data
- Token fundamentals
- Category analysis

Uses CoinGecko free API (no key required for basic data).
"""

import os
import json
import time
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional
from termcolor import cprint


# ── CoinGecko Free API ──────────────────────────────────────
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")

# Solana ecosystem CoinGecko IDs
SOLANA_TOKENS = {
    "solana": "solana",
    "bonk": "bonk",
    "jupiter": "jupiter-exchange-solana",
    "raydium": "raydium",
    "jito": "jito-governance-token",
    "wif": "dogwifcoin",
    "bome": "book-of-meme",
    "popcat": "popcat",
    "myro": "myro",
    "bear": "berachain",
}


class CoinGeckoAgent:
    """
    CoinGecko market data agent.
    
    Provides:
- Trending tokens (what's hot right now)
- Market data (price, volume, market cap)
- Category analysis (DeFi, memecoin, etc.)
- Token fundamentals
"""

    def __init__(self):
        self._cache: Dict[str, dict] = {}
        self._cache_ttl = 300  # 5 minutes
        self._cache_times: Dict[str, float] = {}
        self._session = requests.Session()
        
        # Set headers for better rate limiting
        if COINGECKO_API_KEY:
            self._session.headers.update({"x-cg-demo-api-key": COINGECKO_API_KEY})
        
        cprint("[COINGECKO] CoinGecko Agent initialized", "white", "on_blue")

    def _get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        """Make a GET request to CoinGecko API with caching."""
        cache_key = f"{endpoint}_{json.dumps(params or {}, sort_keys=True)}"
        
        # Check cache
        if cache_key in self._cache:
            age = time.time() - self._cache_times.get(cache_key, 0)
            if age < self._cache_ttl:
                return self._cache[cache_key]
        
        try:
            url = f"{COINGECKO_BASE}{endpoint}"
            resp = self._session.get(url, params=params, timeout=15)
            
            if resp.status_code == 200:
                data = resp.json()
                self._cache[cache_key] = data
                self._cache_times[cache_key] = time.time()
                return data
            elif resp.status_code == 429:
                cprint("[COINGECKO] Rate limited — waiting 60s", "yellow")
                time.sleep(60)
                return None
            else:
                cprint(f"[COINGECKO] API error {resp.status_code}", "yellow")
                return None
                
        except Exception as e:
            cprint(f"[COINGECKO] Request error: {e}", "yellow")
            return None

    def get_trending(self) -> List[dict]:
        """Get trending tokens (what's hot right now)."""
        data = self._get("/search/trending")
        if not data:
            return []
        
        coins = data.get("coins", [])
        trending = []
        for item in coins[:10]:
            coin = item.get("item", {})
            trending.append({
                "id": coin.get("id", ""),
                "name": coin.get("name", ""),
                "symbol": coin.get("symbol", ""),
                "market_cap_rank": coin.get("market_cap_rank"),
                "price_btc": coin.get("price_btc", 0),
                "score": coin.get("score", 0),
            })
        
        return trending

    def get_token_data(self, coin_id: str) -> Optional[dict]:
        """Get detailed token data."""
        data = self._get(f"/coins/{coin_id}", params={
            "localization": "false",
            "tickers": "false",
            "community_data": "false",
            "developer_data": "false",
        })
        
        if not data:
            return None
        
        market = data.get("market_data", {})
        return {
            "id": data.get("id", ""),
            "name": data.get("name", ""),
            "symbol": data.get("symbol", ""),
            "description": data.get("description", {}).get("en", "")[:200],
            "price_usd": market.get("current_price", {}).get("usd", 0),
            "price_btc": market.get("current_price", {}).get("btc", 0),
            "market_cap": market.get("market_cap", {}).get("usd", 0),
            "total_volume": market.get("total_volume", {}).get("usd", 0),
            "price_change_24h": market.get("price_change_percentage_24h", 0),
            "price_change_7d": market.get("price_change_percentage_7d", 0),
            "price_change_30d": market.get("price_change_percentage_30d", 0),
            "ath": market.get("ath", {}).get("usd", 0),
            "ath_change_pct": market.get("ath_change_percentage", {}).get("usd", 0),
            "atl": market.get("atl", {}).get("usd", 0),
            "circulating_supply": market.get("circulating_supply", 0),
            "total_supply": market.get("total_supply", 0),
            "max_supply": market.get("max_supply", 0),
        }

    def get_solana_trending(self) -> List[dict]:
        """Get trending tokens specifically on Solana."""
        trending = self.get_trending()
        sol_trending = []
        
        for token in trending:
            # Check if it's a known Solana token
            if token["id"] in SOLANA_TOKENS.values():
                sol_trending.append(token)
            # Also check by platform
            token_data = self.get_token_data(token["id"])
            if token_data and token_data.get("price_usd", 0) > 0:
                sol_trending.append(token)
        
        return sol_trending[:10]

    def get_market_overview(self) -> dict:
        """Get overall crypto market data."""
        data = self._get("/global")
        if not data:
            return {}
        
        global_data = data.get("data", {})
        return {
            "total_market_cap": global_data.get("total_market_cap", {}).get("usd", 0),
            "total_volume": global_data.get("total_volume", {}).get("usd", 0),
            "btc_dominance": global_data.get("market_cap_percentage", {}).get("btc", 0),
            "eth_dominance": global_data.get("market_cap_percentage", {}).get("eth", 0),
            "active_cryptos": global_data.get("active_cryptocurrencies", 0),
            "market_cap_change_24h": global_data.get("market_cap_change_percentage_24h_usd", 0),
        }

    def get_category_data(self, category: str = "solana-ecosystem") -> List[dict]:
        """Get tokens in a specific category."""
        data = self._get(f"/coins/markets", params={
            "vs_currency": "usd",
            "category": category,
            "order": "market_cap_desc",
            "per_page": 20,
            "page": 1,
            "sparkline": "false",
        })
        
        if not data:
            return []
        
        return [{
            "id": c.get("id", ""),
            "name": c.get("name", ""),
            "symbol": c.get("symbol", ""),
            "price_usd": c.get("current_price", 0),
            "market_cap": c.get("market_cap", 0),
            "volume_24h": c.get("total_volume", 0),
            "price_change_24h": c.get("price_change_percentage_24h", 0),
            "price_change_7d": c.get("price_change_percentage_7d", 0),
        } for c in data]

    def search_token(self, query: str) -> List[dict]:
        """Search for tokens by name or symbol."""
        data = self._get("/search", params={"query": query})
        if not data:
            return []
        
        coins = data.get("coins", [])
        return [{
            "id": c.get("id", ""),
            "name": c.get("name", ""),
            "symbol": c.get("symbol", ""),
            "market_cap_rank": c.get("market_cap_rank"),
        } for c in coins[:5]]

    def get_stats(self) -> dict:
        return {
            "cache_size": len(self._cache),
            "api_key_set": bool(COINGECKO_API_KEY),
        }


# ── Singleton ──────────────────────────────────────────────
_coingecko_instance = None

def get_coingecko_agent() -> CoinGeckoAgent:
    global _coingecko_instance
    if _coingecko_instance is None:
        _coingecko_instance = CoinGeckoAgent()
    return _coingecko_instance
