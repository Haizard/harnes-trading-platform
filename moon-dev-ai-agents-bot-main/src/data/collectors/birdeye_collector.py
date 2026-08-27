"""
🌙 Moon Dev's BirdEye Resilient Collector
Caching layer + Rate limiting + Fallback
Built with love by Moon Dev 🚀
"""

import asyncio
import os
from datetime import datetime, timedelta
from termcolor import cprint
from dotenv import load_dotenv

from src.data.storage.mongo_db import MongoStorage
from src.config import MONITORED_TOKENS, MIN_TRADES_LAST_HOUR

# Only import if available
try:
    from src.nice_funcs import token_overview
    BIRDEYE_AVAILABLE = True
except Exception:
    BIRDEYE_AVAILABLE = False

load_dotenv()

# ── Config ────────────────────────────────────────────────────
CACHE_TTL_MINUTES = 5          # How long overview cache is valid
API_CONCURRENCY   = 2          # Max parallel BirdEye requests (rate limit)

# 🛡️ Security heuristics (replaces premium token_security endpoint)
MIN_LIQUIDITY          = 5_000    # Minimum USD liquidity pool depth
MIN_VOLUME_1H          = 500      # Minimum 1h trading volume (USD)
MIN_TOKEN_AGE_MINUTES  = 30       # Token must be at least 30 min old


class BirdEyeCollector:
    """
    Production-grade BirdEye collector with:
    - MongoDB-backed caching (5-min TTL)
    - asyncio.Semaphore rate limiter
    - Graceful fallback to cached data
    - Security-free heuristic filter (no premium needed)
    """

    def __init__(self, interval: int = 300):
        self.interval  = interval
        self.storage   = MongoStorage()
        self.tokens    = MONITORED_TOKENS
        self._sem      = asyncio.Semaphore(API_CONCURRENCY)
        cprint(f"[BIRDEYE] Resilient collector initialized for {len(self.tokens)} tokens", "white", "on_blue")

    # ── Cache helpers ─────────────────────────────────────────

    async def _get_cached(self, token_address: str) -> dict | None:
        """Return cached doc if fresh, else None."""
        doc = await self.storage.db["tokens"].find_one(
            {"symbol": token_address},
            sort=[("timestamp", -1)]
        )
        if not doc:
            return None
        age = datetime.utcnow() - doc["timestamp"]
        if age < timedelta(minutes=CACHE_TTL_MINUTES):
            return doc["data"]
        return None

    async def _save_cache(self, token_address: str, payload: dict):
        """Persist payload to tokens collection."""
        await self.storage.save_to_collection("tokens", token_address, payload)

    # ── Security-free heuristic filter ───────────────────────

    def _passes_heuristics(self, overview: dict) -> bool:
        """
        🛡️ Security filter replacing the premium token_security endpoint.
        
        Rules (all must pass):
          1. Liquidity  >= MIN_LIQUIDITY          (deep enough pool)
          2. Volume 1h  >= MIN_VOLUME_1H           (active trading)
          3. Token age  >= MIN_TOKEN_AGE_MINUTES   (not brand-new rug risk)
        
        Returns True if token passes all checks.
        """
        import datetime

        liquidity = float(overview.get("liquidity", 0) or 0)
        volume_1h = float(overview.get("v1hUSD", 0) or 0)

        # Age: BirdEye returns creation timestamp in seconds (Unix epoch)
        created_at = overview.get("createdAt", None)
        if created_at:
            token_age_minutes = (
                datetime.datetime.utcnow().timestamp() - float(created_at)
            ) / 60
        else:
            token_age_minutes = MIN_TOKEN_AGE_MINUTES  # unknown → assume OK

        checks = {
            "liquidity":   liquidity >= MIN_LIQUIDITY,
            "volume_1h":   volume_1h >= MIN_VOLUME_1H,
            "token_age":   token_age_minutes >= MIN_TOKEN_AGE_MINUTES,
        }

        passed = all(checks.values())

        if not passed:
            failed = [k for k, v in checks.items() if not v]
            cprint(
                f"[SECURITY] ❌ Token failed: {failed} | "
                f"liq=${liquidity:,.0f} vol1h=${volume_1h:,.0f} "
                f"age={token_age_minutes:.1f}min",
                "yellow"
            )
        else:
            cprint(
                f"[SECURITY] ✅ Token passed | "
                f"liq=${liquidity:,.0f} vol1h=${volume_1h:,.0f} "
                f"age={token_age_minutes:.1f}min",
                "green"
            )

        return passed

    # ── BirdEye API with rate limiter ─────────────────────────

    async def _fetch_overview(self, token_address: str) -> dict | None:
        """Fetch from BirdEye with semaphore rate limiting."""
        if not BIRDEYE_AVAILABLE:
            cprint("[BIRDEYE] BirdEye library unavailable — using cache only", "yellow")
            return None
        async with self._sem:
            loop = asyncio.get_running_loop()
            try:
                overview = await loop.run_in_executor(None, token_overview, token_address)
                return overview
            except Exception as e:
                cprint(f"[BIRDEYE] API error for {token_address[:8]}: {str(e)}", "yellow")
                return None

    # ── Main collection method ────────────────────────────────

    async def collect_token_data(self, token_address: str) -> dict | None:
        """
        Fetch and persist token data.
        Strategy:
          1. Return fresh cache if available
          2. Else hit BirdEye (rate-limited)
          3. If API fails → return stale cache as fallback
          4. Apply heuristic filter (no security API)
        """
        try:
            await self.storage.connect()

            # 1️⃣ Try fresh cache first
            cached = await self._get_cached(token_address)
            if cached:
                cprint(f"[BIRDEYE] Cache HIT for {token_address[:8]}...", "cyan")
                return cached

            # 2️⃣ Hit API (rate-limited)
            overview = await self._fetch_overview(token_address)

            if not overview:
                # 3️⃣ Fallback — stale cache is better than nothing
                stale = await self.storage.db["tokens"].find_one(
                    {"symbol": token_address},
                    sort=[("timestamp", -1)]
                )
                if stale:
                    cprint(f"[BIRDEYE] API failed — using STALE cache for {token_address[:8]}", "yellow")
                    return stale["data"]
                return None

            # 4️⃣ Heuristic filter (replaces security API)
            if not self._passes_heuristics(overview):
                cprint(f"[BIRDEYE] Token {token_address[:8]} failed liquidity filter — skipping", "yellow")
                return None

            # 5️⃣ Build and persist payload
            payload = {
                "address":      token_address,
                "overview":     overview,
                "security":     None,  # Deprecated — no premium required
                "collected_at": datetime.utcnow().isoformat(),
                "source":       "birdeye_api"
            }
            await self._save_cache(token_address, payload)

            symbol = overview.get("symbol", "UNKNOWN")
            price  = overview.get("price", 0)
            cprint(f"[BIRDEYE] Saved {symbol} ({token_address[:6]}...): ${price:.6f}", "green")
            return payload

        except Exception as e:
            cprint(f"[ERROR] BirdEye collect_token_data: {str(e)}", "white", "on_red")
            return None

    # ── Continuous loop ───────────────────────────────────────

    async def start(self):
        """Token collection loop — runs tasks in parallel with rate limiting."""
        cprint("[BIRDEYE] Starting resilient collection loop...", "white", "on_blue")
        await self.storage.connect()
        while True:
            tasks = [self.collect_token_data(addr) for addr in self.tokens]
            await asyncio.gather(*tasks)
            cprint(f"[BIRDEYE] Cycle done. Sleeping {self.interval}s...", "cyan")
            await asyncio.sleep(self.interval)


if __name__ == "__main__":
    collector = BirdEyeCollector()
    asyncio.run(collector.start())
