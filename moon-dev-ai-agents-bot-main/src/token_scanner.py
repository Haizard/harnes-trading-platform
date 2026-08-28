"""Token Scanner for micro-cap Solana tokens."""
import os, time, json, asyncio, requests
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Callable
from pathlib import Path

BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
BIRDEYE_BASE = "https://public-api.birdeye.so"
JUPITER_API = "https://quote-api.jup.ag/v6"
SOL_MINT = "So11111111111111111111111111111111111111112"
MIN_VOLUME_1H = 10000
MIN_LIQUIDITY = 5000

@dataclass
class TokenCandidate:
    address: str
    symbol: str
    name: str
    price_usd: float = 0.0
    volume_1h: float = 0.0
    volume_24h: float = 0.0
    liquidity_usd: float = 0.0
    market_cap: float = 0.0
    price_change_1h: float = 0.0
    price_change_24h: float = 0.0
    holder_count: int = 0
    score: float = 0.0
    signals: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    def to_dict(self):
        return {"address": self.address, "symbol": self.symbol, "name": self.name,
                "price_usd": self.price_usd, "volume_1h": self.volume_1h,
                "volume_24h": self.volume_24h, "liquidity_usd": self.liquidity_usd,
                "market_cap": self.market_cap, "score": self.score,
                "signals": self.signals, "timestamp": self.timestamp.isoformat()}

class BirdeyeClient:
    def __init__(self, api_key=None):
        self.api_key = api_key or BIRDEYE_API_KEY
        self.base_url = BIRDEYE_BASE
        self._last_request_time = 0
    def _get_headers(self):
        return {"X-API-KEY": self.api_key, "x-chain": "solana"}
    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < 0.1: time.sleep(0.1 - elapsed)
        self._last_request_time = time.time()
    def get_new_tokens(self, limit=50):
        self._throttle()
        try:
            resp = requests.get(f"{self.base_url}/defi/v3/token/new_listing",
                headers=self._get_headers(), params={"limit": limit, "meme_platform_enabled": True}, timeout=10)
            if resp.status_code == 200: return resp.json().get("data", {}).get("items", [])
        except: pass
        return []
    def get_token_overview(self, address):
        self._throttle()
        try:
            resp = requests.get(f"{self.base_url}/defi/v3/token/overview",
                headers=self._get_headers(), params={"address": address}, timeout=10)
            if resp.status_code == 200: return resp.json().get("data", {})
        except: pass
        return None

class JupiterChecker:
    def check_liquidity(self, token_address, amount_sol=0.01):
        try:
            resp = requests.get(f"{JUPITER_API}/quote",
                params={"inputMint": SOL_MINT, "outputMint": token_address,
                        "amount": str(int(amount_sol * 1e9)), "slippageBps": 500}, timeout=10)
            if resp.status_code == 200:
                q = resp.json()
                pi = float(q.get("priceImpactPct", 0))
                return {"available": True, "price_impact_pct": pi, "good_entry": abs(pi) < 5.0}
        except: pass
        return {"available": False}
    def get_price(self, token_address):
        try:
            resp = requests.get(f"{JUPITER_API}/price", params={"ids": token_address}, timeout=10)
            if resp.status_code == 200:
                return float(resp.json().get("data", {}).get(token_address, {}).get("price", 0))
        except: pass
        return None

class TokenScorer:
    def score(self, c):
        s, sig = 0.0, []
        if c.volume_1h > 50000: s += 30; sig.append("High volume")
        elif c.volume_1h > 20000: s += 25; sig.append("Strong volume")
        elif c.volume_1h > 10000: s += 15; sig.append("Decent volume")
        if c.liquidity_usd > 50000: s += 25; sig.append("Deep liquidity")
        elif c.liquidity_usd > 20000: s += 20; sig.append("Good liquidity")
        elif c.liquidity_usd > 10000: s += 15; sig.append("Adequate liquidity")
        elif c.liquidity_usd > 5000: s += 10; sig.append("Low liquidity")
        if c.holder_count > 1000: s += 15; sig.append("Large community")
        elif c.holder_count > 500: s += 10; sig.append("Growing community")
        elif c.holder_count > 100: s += 5; sig.append("Small community")
        if c.market_cap < 100000: s += 10; sig.append("Micro-cap")
        elif c.market_cap < 500000: s += 7; sig.append("Small-cap")
        c.score = min(s, 100); c.signals = sig
        return c.score

class TokenScanner:
    def __init__(self, callback=None):
        self.birdeye = BirdeyeClient()
        self.jupiter = JupiterChecker()
        self.scorer = TokenScorer()
        self.callback = callback
        self._seen_tokens = set()
        self._scan_count = 0
        self.data_dir = Path("src/data/scanner")
        self.data_dir.mkdir(parents=True, exist_ok=True)
    def scan_once(self):
        candidates = []
        for td in self.birdeye.get_new_tokens(limit=50):
            addr = td.get("address", "")
            if addr in self._seen_tokens: continue
            ov = self.birdeye.get_token_overview(addr)
            if not ov: continue
            v24 = float(ov.get("v24hUSD", 0) or 0)
            liq = float(ov.get("liquidity", 0) or 0)
            mc = float(ov.get("mc", 0) or 0)
            px = float(ov.get("price", 0) or 0)
            hl = int(ov.get("holder", 0) or 0)
            c = TokenCandidate(address=addr, symbol=td.get("symbol", "?"),
                name=td.get("name", "Unknown"), price_usd=px,
                volume_1h=v24/24, volume_24h=v24, liquidity_usd=liq,
                market_cap=mc, holder_count=hl)
            if c.volume_1h < MIN_VOLUME_1H or liq < MIN_LIQUIDITY: continue
            self.scorer.score(c)
            if c.score >= 40:
                candidates.append(c); self._seen_tokens.add(addr)
                self._log_candidate(c)
                if self.callback: self.callback(c)
        self._scan_count += 1
        return candidates
    def _log_candidate(self, c):
        with open(self.data_dir / "scanner_results.jsonl", "a") as f:
            f.write(json.dumps(c.to_dict()) + chr(10))
    def get_scan_stats(self):
        return {"total_scans": self._scan_count, "unique_tokens_seen": len(self._seen_tokens)}

async def scan_for_tokens(callback=None):
    return TokenScanner(callback=callback).scan_once()
