"""Token Scanner for micro-cap Solana tokens using DexScreener (FREE, no API key)."""
import os
import time
import json
import requests
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Callable, Dict
from pathlib import Path

# -- API Endpoints (all FREE, no API key needed) ---------------

JUPITER_QUOTE = "https://api.jup.ag/swap/v1/quote"
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search"
DEXSCREENER_TRENDING_METAS = "https://api.dexscreener.com/metas/trending/v1"
DEXSCREENER_META_TOKENS = "https://api.dexscreener.com/metas/meta/v1"
DEXSCREENER_BOOSTED = "https://api.dexscreener.com/token-boosts/latest/v1"
DEXSCREENER_BOOSTED_TOP = "https://api.dexscreener.com/token-boosts/top/v1"
SOL_MINT = "So11111111111111111111111111111111111111112"

# Search terms that surface new/trending Solana memecoins
SCAN_TERMS = [
    "pump.fun",
    "solana memecoin",
    "solana new token",
    "BONK",
    "POPCAT",
    "WIF",
    "FARTCOIN",
    "MEW",
    "BOME",
    "MYRO",
    "RETARDIO",
    "GUMMY",
]

MIN_LIQUIDITY_USD = 50_000       # Real depth — avoid illiquid micro-caps
MIN_VOLUME_24H = 10_000          # Active trading required
MIN_HOLDERS = 10
MAX_MARKET_CAP = 10_000_000      # Exclude blue-chip memecoins (Bonk, POPCAT, etc.)
SCAN_INTERVAL_SECONDS = 30


# -- Token Categories ──────────────────────────────────────────

class TokenCategory(str, Enum):
    """Categories for token-aware trading logic."""
    AI_AGENT = "ai_agent"          # AI agent tokens (AI16Z, GOAT, etc.)
    POLITICAL = "political"        # Political/event-driven tokens
    MEMECOIN = "memecoin"          # Animal/community memecoins
    PUMP_FUN = "pump_fun"          # Fresh pump.fun launches
    TRENDING = "trending"          # Currently trending on DexScreener
    BOOSTED = "boosted"            # Paid-boosted tokens
    UNKNOWN = "unknown"

    def __str__(self):
        return self.value


# Per-category exit parameters: (stop_loss%, take_profit%, max_hold_hours)
CATEGORY_TRADE_PARAMS: Dict[TokenCategory, dict] = {
    TokenCategory.AI_AGENT: {
        "stop_loss_pct": 5.0,
        "take_profit_pct": 50.0,
        "max_hold_hours": 48.0,
        "description": "AI agent — let narratives play out",
    },
    TokenCategory.POLITICAL: {
        "stop_loss_pct": 15.0,
        "take_profit_pct": 20.0,
        "max_hold_hours": 6.0,
        "description": "Political — fast in, fast out",
    },
    TokenCategory.MEMECOIN: {
        "stop_loss_pct": 10.0,
        "take_profit_pct": 30.0,
        "max_hold_hours": 12.0,
        "description": "Memecoin — standard defaults",
    },
    TokenCategory.PUMP_FUN: {
        "stop_loss_pct": 20.0,
        "take_profit_pct": 40.0,
        "max_hold_hours": 2.0,
        "description": "Pump.fun — high risk, quick exit",
    },
    TokenCategory.TRENDING: {
        "stop_loss_pct": 10.0,
        "take_profit_pct": 35.0,
        "max_hold_hours": 12.0,
        "description": "Trending — slightly wider TP",
    },
    TokenCategory.BOOSTED: {
        "stop_loss_pct": 12.0,
        "take_profit_pct": 25.0,
        "max_hold_hours": 8.0,
        "description": "Boosted — someone paid to promote, be cautious",
    },
    TokenCategory.UNKNOWN: {
        "stop_loss_pct": 10.0,
        "take_profit_pct": 30.0,
        "max_hold_hours": 12.0,
        "description": "Unknown — standard defaults",
    },
}


def get_category_params(category: TokenCategory) -> dict:
    """Get trade parameters for a token category."""
    return CATEGORY_TRADE_PARAMS.get(category, CATEGORY_TRADE_PARAMS[TokenCategory.UNKNOWN])


def classify_token(name: str, symbol: str, description: str = "") -> TokenCategory:
    """Classify a token into a category based on name/symbol/description."""
    text = f"{name} {symbol} {description}".lower()

    # AI agent tokens
    ai_keywords = ["ai", "agent", "gpt", "llm", "openai", "anthropic", "neural",
                    "robot", "bot", "machine learning", "deepseek", "qwen"]
    if any(kw in text for kw in ai_keywords):
        return TokenCategory.AI_AGENT

    # Political tokens
    political_keywords = ["trump", "biden", "maga", "election", "president",
                          "vote", "politic", "kamala", "elon", "doge"]
    if any(kw in text for kw in political_keywords):
        return TokenCategory.POLITICAL

    # Pump.fun tokens (address ends with "pump" or name contains pump.fun)
    if "pump" in text or (len(symbol) > 4 and symbol.lower().endswith("pump")):
        return TokenCategory.PUMP_FUN

    # Memecoins (animal names, emoji references, community tokens)
    meme_keywords = ["cat", "dog", "bear", "bull", "frog", "ape", "monkey",
                     "pepe", "wojak", "chad", "retard", "degen", "moon",
                     "inu", "shib", "bonk", "popcat", "bome", "fart"]
    if any(kw in text for kw in meme_keywords):
        return TokenCategory.MEMECOIN

    return TokenCategory.UNKNOWN


@dataclass
class TokenCandidate:
    address: str
    symbol: str
    name: str
    pair_address: str = ""
    price_usd: float = 0.0
    volume_1h: float = 0.0
    volume_24h: float = 0.0
    liquidity_usd: float = 0.0
    market_cap: float = 0.0
    fdv: float = 0.0
    price_change_1h: float = 0.0
    price_change_24h: float = 0.0
    txns_1h_buys: int = 0
    txns_1h_sells: int = 0
    pair_age_hours: float = 0.0
    dex: str = ""
    score: float = 0.0
    signals: List[str] = field(default_factory=list)
    category: TokenCategory = TokenCategory.UNKNOWN
    source: str = "search"  # "search", "trending", "boosted"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "address": self.address, "symbol": self.symbol, "name": self.name,
            "pair_address": self.pair_address, "price_usd": self.price_usd,
            "volume_1h": self.volume_1h, "volume_24h": self.volume_24h,
            "liquidity_usd": self.liquidity_usd, "market_cap": self.market_cap,
            "fdv": self.fdv, "price_change_1h": self.price_change_1h,
            "price_change_24h": self.price_change_24h,
            "txns_1h_buys": self.txns_1h_buys, "txns_1h_sells": self.txns_1h_sells,
            "pair_age_hours": round(self.pair_age_hours, 1),
            "dex": self.dex, "score": self.score,
            "signals": self.signals, "category": str(self.category),
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
        }


class DexScreenerSource:
    """Discover tokens via DexScreener search API (FREE, no key)."""

    def search(self, query: str) -> list:
        """Search DexScreener for Solana pairs matching a query."""
        try:
            resp = requests.get(
                DEXSCREENER_SEARCH,
                params={"q": query},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            pairs = data.get("pairs", []) or []
            return [p for p in pairs if p.get("chainId") == "solana"]
        except Exception as e:
            print(f"[SCANNER] DexScreener search error for '{query}': {e}", flush=True)
            return []

    def pair_to_candidate(self, pair: dict, source: str = "search",
                          category: TokenCategory = None) -> Optional[TokenCandidate]:
        """Convert a DexScreener pair to a TokenCandidate."""
        base = pair.get("baseToken", {})
        addr = base.get("address", "")
        if not addr:
            return None

        created_at = pair.get("pairCreatedAt", 0)
        age_hours = 0.0
        if created_at:
            age_hours = (time.time() * 1000 - created_at) / (1000 * 3600)

        vol = pair.get("volume", {})
        vol_1h = float(vol.get("h1", 0) or 0)
        vol_24h = float(vol.get("h24", 0) or 0)

        pc = pair.get("priceChange", {})
        pc_1h = float(pc.get("h1", 0) or 0)
        pc_24h = float(pc.get("h24", 0) or 0)

        txns = pair.get("txns", {})
        txns_1h = txns.get("h1", {})
        buys_1h = int(txns_1h.get("buys", 0) or 0)
        sells_1h = int(txns_1h.get("sells", 0) or 0)

        token_name = base.get("name", "Unknown")
        token_symbol = base.get("symbol", "UNKNOWN")

        # Auto-classify if not provided
        if category is None:
            category = classify_token(token_name, token_symbol)

        return TokenCandidate(
            address=addr,
            symbol=token_symbol,
            name=token_name,
            pair_address=pair.get("pairAddress", ""),
            price_usd=float(pair.get("priceUsd", 0) or 0),
            volume_1h=vol_1h,
            volume_24h=vol_24h,
            liquidity_usd=float(pair.get("liquidity", {}).get("usd", 0) or 0),
            market_cap=float(pair.get("marketCap", 0) or 0),
            fdv=float(pair.get("fdv", 0) or 0),
            price_change_1h=pc_1h,
            price_change_24h=pc_24h,
            txns_1h_buys=buys_1h,
            txns_1h_sells=sells_1h,
            pair_age_hours=age_hours,
            dex=pair.get("dexId", "unknown"),
            category=category,
            source=source,
        )


class JupiterChecker:
    """Check liquidity and price impact via Jupiter (FREE, no key)."""

    def check_liquidity(self, token_address: str, amount_sol: float = 0.01) -> dict:
        try:
            resp = requests.get(
                JUPITER_QUOTE,
                params={
                    "inputMint": SOL_MINT,
                    "outputMint": token_address,
                    "amount": str(int(amount_sol * 1e9)),
                    "slippageBps": 500,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                q = resp.json()
                pi = float(q.get("priceImpactPct", 0))
                out_amount = int(q.get("outAmount", 0))
                return {
                    "available": out_amount > 0,
                    "price_impact_pct": pi,
                    "good_entry": abs(pi) < 5.0,
                    "out_amount": out_amount,
                }
        except Exception as e:
            print(f"[SCANNER] Jupiter check error: {e}", flush=True)
        return {"available": False}


class TrendingDiscoverer:
    """Discover tokens via DexScreener trending categories and boosted tokens.

    Sources:
    1. Trending categories (AI, memes, politics, etc.) → tokens within them
    2. Top boosted tokens (paid promotion = strong signal)
    3. Recently boosted tokens (fresh promotion activity)

    Rate limits: 60 req/min for trending/boosted endpoints.
    """

    def __init__(self, dexscreener: DexScreenerSource):
        self.dexscreener = dexscreener
        self._last_trending_fetch = 0
        self._trending_cache = []  # Cached trending category slugs
        self._last_boosted_fetch = 0
        self._boosted_cache = []  # Cached boosted token addresses
        self._trending_interval = 600  # Re-fetch trending every 10 min
        self._boosted_interval = 300   # Re-fetch boosted every 5 min

    def discover(self) -> List[dict]:
        """Discover trending and boosted tokens. Returns list of pair dicts."""
        pairs = []
        pairs.extend(self._fetch_trending_category_tokens())
        pairs.extend(self._fetch_boosted_tokens())
        return pairs

    def _fetch_trending_category_tokens(self) -> List[dict]:
        """Fetch tokens from currently trending categories."""
        now = time.time()
        if now - self._last_trending_fetch < self._trending_interval:
            return []
        self._last_trending_fetch = now

        pairs = []
        try:
            # Step 1: Get trending categories
            resp = requests.get(DEXSCREENER_TRENDING_METAS, timeout=15)
            if resp.status_code != 200:
                return []
            metas = resp.json()
            if not isinstance(metas, list):
                return []

            # Take top 3 trending categories
            trending_slugs = []
            for meta in metas[:3]:
                slug = meta.get("slug", "")
                name = meta.get("name", "")
                volume = meta.get("volume", 0)
                if slug:
                    trending_slugs.append((slug, name, volume))
                    print("[TRENDING] Category: " + name + " (vol=$" + "{:,.0f}".format(volume) + ")", flush=True)

            self._trending_cache = trending_slugs

            # Step 2: Get tokens from each trending category
            for slug, name, _ in trending_slugs:
                try:
                    resp = requests.get(
                        f"{DEXSCREENER_META_TOKENS}/{slug}",
                        timeout=15,
                    )
                    if resp.status_code != 200:
                        continue
                    meta_data = resp.json()
                    category_pairs = meta_data.get("pairs", []) or []
                    sol_pairs = [p for p in category_pairs if p.get("chainId") == "solana"]
                    for pair in sol_pairs[:10]:  # Top 10 per category
                        pairs.append(pair)
                    print("[TRENDING] " + name + ": " + str(len(sol_pairs)) + " Solana pairs found", flush=True)
                    time.sleep(0.3)  # Rate limit respect
                except Exception as e:
                    print("[TRENDING] Error fetching " + slug + ": " + str(e), flush=True)

        except Exception as e:
            print("[TRENDING] Error fetching trending metas: " + str(e), flush=True)

        return pairs

    def _fetch_boosted_tokens(self) -> List[dict]:
        """Fetch recently and top boosted tokens."""
        now = time.time()
        if now - self._last_boosted_fetch < self._boosted_interval:
            return []
        self._last_boosted_fetch = now

        pairs = []
        boosted_addrs = set()

        try:
            # Top boosted tokens
            resp = requests.get(DEXSCREENER_BOOSTED_TOP, timeout=15)
            if resp.status_code == 200:
                boosts = resp.json()
                if isinstance(boosts, list):
                    for b in boosts[:20]:
                        if b.get("chainId") == "solana":
                            addr = b.get("tokenAddress", "")
                            if addr:
                                boosted_addrs.add(addr)
                    print("[BOOSTED] Found " + str(len(boosted_addrs)) + " top boosted Solana tokens", flush=True)
        except Exception as e:
            print("[BOOSTED] Error fetching top boosts: " + str(e), flush=True)

        try:
            # Recently boosted
            resp = requests.get(DEXSCREENER_BOOSTED, timeout=15)
            if resp.status_code == 200:
                boosts = resp.json()
                if isinstance(boosts, list):
                    for b in boosts[:20]:
                        if b.get("chainId") == "solana":
                            addr = b.get("tokenAddress", "")
                            if addr:
                                boosted_addrs.add(addr)
                    print("[BOOSTED] Found " + str(len(boosted_addrs)) + " total boosted Solana tokens", flush=True)
        except Exception as e:
            print("[BOOSTED] Error fetching recent boosts: " + str(e), flush=True)

        # Fetch pair data for boosted tokens (batch up to 30 at a time)
        addr_list = list(boosted_addrs)
        for i in range(0, len(addr_list), 30):
            batch = addr_list[i:i+30]
            try:
                resp = requests.get(
                    f"https://api.dexscreener.com/tokens/v1/solana/{','.join(batch)}",
                    timeout=15,
                )
                if resp.status_code == 200:
                    token_pairs = resp.json()
                    if isinstance(token_pairs, list):
                        for pair in token_pairs:
                            if pair.get("chainId") == "solana":
                                pairs.append(pair)
                time.sleep(0.3)
            except Exception as e:
                print("[BOOSTED] Error fetching pair data: " + str(e), flush=True)

        self._boosted_cache = addr_list
        return pairs

    def get_trending_slugs(self) -> List[str]:
        """Return cached trending category slugs."""
        return [s for s, _, _ in self._trending_cache]


class TokenScorer:
    """Score a token candidate 0-100 based on metrics."""

    def score(self, c: TokenCandidate) -> float:
        s = 0.0
        sig = []

        # Volume scoring (max 30)
        if c.volume_24h > 100000:
            s += 30; sig.append("High 24h volume")
        elif c.volume_24h > 50000:
            s += 25; sig.append("Strong 24h volume")
        elif c.volume_24h > 20000:
            s += 20; sig.append("Good 24h volume")
        elif c.volume_24h > 5000:
            s += 10; sig.append("Moderate volume")

        # Liquidity scoring (max 25)
        if c.liquidity_usd > 100000:
            s += 25; sig.append("Deep liquidity")
        elif c.liquidity_usd > 50000:
            s += 20; sig.append("Good liquidity")
        elif c.liquidity_usd > 20000:
            s += 15; sig.append("Adequate liquidity")
        elif c.liquidity_usd > 5000:
            s += 10; sig.append("Low liquidity")
        elif c.liquidity_usd > 1000:
            s += 5; sig.append("Very low liquidity")

        # Momentum scoring (max 20)
        if c.price_change_1h > 10:
            s += 20; sig.append("Strong 1h momentum")
        elif c.price_change_1h > 5:
            s += 15; sig.append("Good 1h momentum")
        elif c.price_change_1h > 2:
            s += 10; sig.append("Positive 1h momentum")

        # Buy/sell ratio scoring (max 15)
        total_txns = c.txns_1h_buys + c.txns_1h_sells
        if total_txns > 0:
            buy_ratio = c.txns_1h_buys / total_txns
            if buy_ratio > 0.7:
                s += 15; sig.append("Strong buy pressure")
            elif buy_ratio > 0.55:
                s += 10; sig.append("Buy-side dominant")
            elif buy_ratio > 0.45:
                s += 5; sig.append("Balanced flow")

        # Market cap scoring (max 10) - prefer micro-caps
        if 0 < c.market_cap < 1000000:
            s += 10; sig.append("Micro-cap")
        elif 0 < c.market_cap < 5000000:
            s += 7; sig.append("Small-cap")
        elif 0 < c.market_cap < 20000000:
            s += 3; sig.append("Mid-cap")

        # Category bonus (max 5) — trending/boosted tokens get a small edge
        if c.source == "trending":
            s += 5; sig.append("Trending category")
        elif c.source == "boosted":
            s += 3; sig.append("Boosted token")

        c.score = min(s, 100.0)
        c.signals = sig
        return c.score


SEEN_TOKENS_RESET_SECONDS = 1800  # Reset seen tokens every 30 minutes


class TokenScanner:
    def __init__(self, callback=None):
        self.dexscreener = DexScreenerSource()
        self.jupiter = JupiterChecker()
        self.scorer = TokenScorer()
        self.trending = TrendingDiscoverer(self.dexscreener)
        self.callback = callback
        self._seen_tokens = set()
        self._seen_tokens_last_reset = time.time()
        self._scan_count = 0
        self.data_dir = Path("src/data/scanner")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Load seen tokens from DB (survives deploys)
        try:
            from src.db_storage import load_scanner_seen_tokens
            db_seen = load_scanner_seen_tokens()
            self._seen_tokens.update(db_seen)
            if db_seen:
                print("[SCANNER] Loaded " + str(len(db_seen)) + " seen tokens from DB", flush=True)
        except Exception:
            pass

    def _maybe_reset_seen_tokens(self):
        """Periodically reset _seen_tokens so tokens get re-evaluated with fresh data."""
        now = time.time()
        if now - self._seen_tokens_last_reset >= SEEN_TOKENS_RESET_SECONDS:
            prev_count = len(self._seen_tokens)
            self._seen_tokens.clear()
            self._seen_tokens_last_reset = now
            print("[SCANNER] Reset seen tokens (was " + str(prev_count) + ") — re-evaluating all tokens", flush=True)

    def scan_once(self):
        candidates = []
        self._scan_count += 1
        self._maybe_reset_seen_tokens()
        print("[SCANNER] Scan #" + str(self._scan_count) + " starting...", flush=True)

        # --- Source 1: Traditional search ---
        all_pairs = {}
        for term in SCAN_TERMS:
            pairs = self.dexscreener.search(term)
            for pair in pairs:
                addr = pair.get("baseToken", {}).get("address", "")
                if addr and addr not in all_pairs:
                    all_pairs[addr] = pair
            time.sleep(0.3)
        print("[SCANNER] Found " + str(len(all_pairs)) + " unique tokens from DexScreener search", flush=True)

        # --- Source 2: Trending categories + boosted tokens ---
        trending_pairs = self.trending.discover()
        for pair in trending_pairs:
            addr = pair.get("baseToken", {}).get("address", "")
            if addr and addr not in all_pairs:
                all_pairs[addr] = pair
        if trending_pairs:
            print("[SCANNER] Added " + str(len(trending_pairs)) + " tokens from trending/boosted", flush=True)

        # --- Score and filter all candidates ---
        for addr, pair in all_pairs.items():
            if addr in self._seen_tokens:
                continue

            # Determine source for this pair
            is_trending = addr in {p.get("baseToken", {}).get("address", "") for p in trending_pairs}
            source = "trending" if is_trending else "search"

            # Check if it's a boosted token
            if addr in self.trending._boosted_cache:
                source = "boosted"

            candidate = self.dexscreener.pair_to_candidate(pair, source=source)
            if not candidate:
                continue
            if candidate.liquidity_usd < MIN_LIQUIDITY_USD:
                continue
            if candidate.volume_24h < MIN_VOLUME_24H:
                continue
            if candidate.market_cap > MAX_MARKET_CAP:
                continue
            jup = self.jupiter.check_liquidity(addr)
            if not jup.get("available"):
                print("[SCANNER] " + candidate.symbol + ": No Jupiter liquidity", flush=True)
                continue
            self.scorer.score(candidate)                if candidate.score >= 30:
                candidates.append(candidate)
                self._seen_tokens.add(addr)
                # Persist to DB (survives deploys)
                try:
                    from src.db_storage import save_scanner_seen_token
                    save_scanner_seen_token(addr)
                except Exception:
                    pass
                self._log_candidate(candidate)
                self._print_candidate(candidate)
                if self.callback:
                    try:
                        self.callback(candidate)
                    except Exception as e:
                        print("[SCANNER] Callback error: " + str(e), flush=True)
        candidates.sort(key=lambda c: c.score, reverse=True)
        print("[SCANNER] Scan #" + str(self._scan_count) + " complete. " + str(len(candidates)) + " candidates scored (" + str(len(self._seen_tokens)) + " total unique tokens seen).", flush=True)
        return candidates

    def _print_candidate(self, c):
        cat_label = str(c.category).upper()
        src_label = c.source.upper()
        print("  NEW CANDIDATE: " + c.symbol + " [" + cat_label + "/" + src_label + "]", flush=True)
        print("     Score: " + str(int(c.score)) + "/100", flush=True)
        print("     Volume 24h: $" + "{:,.0f}".format(c.volume_24h), flush=True)
        print("     Liquidity: $" + "{:,.0f}".format(c.liquidity_usd), flush=True)
        print("     Market Cap: $" + "{:,.0f}".format(c.market_cap), flush=True)
        print("     1h Change: " + ("+" if c.price_change_1h >= 0 else "") + str(round(c.price_change_1h, 1)) + "%", flush=True)
        print("     Buy/Sell 1h: " + str(c.txns_1h_buys) + "/" + str(c.txns_1h_sells), flush=True)
        print("     DEX: " + c.dex, flush=True)
        print("     Signals: " + ", ".join(c.signals), flush=True)
        params = get_category_params(c.category)
        print("     Exit params: SL=" + str(params["stop_loss_pct"]) + "% TP=" + str(params["take_profit_pct"]) + "% Hold=" + str(params["max_hold_hours"]) + "h", flush=True)

    def _log_candidate(self, c):
        log_path = self.data_dir / "scanner_results.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(c.to_dict()) + chr(10))

    def start(self, interval_seconds=30):
        """Start background scanning thread."""
        import threading
        self._stop_event = threading.Event()
        def _loop():
            while not self._stop_event.is_set():
                try:
                    self.scan_once()
                except Exception as e:
                    print("[SCANNER] Error: " + str(e), flush=True)
                self._stop_event.wait(interval_seconds)
        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()
        print("[SCANNER] Background scanner started (interval=" + str(interval_seconds) + "s)", flush=True)

    def stop(self):
        """Stop background scanning thread."""
        if hasattr(self, '_stop_event'):
            self._stop_event.set()
        print("[SCANNER] Background scanner stopped", flush=True)

    def get_scan_stats(self):
        return {"total_scans": self._scan_count, "unique_tokens_seen": len(self._seen_tokens)}


async def scan_for_tokens(callback=None):
    scanner = TokenScanner(callback=callback)
    return scanner.scan_once()
