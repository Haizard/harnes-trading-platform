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

# Category agents for discovery, scoring, and trading style
from src.event_bus import _fire_and_forget
from src.category_agents import (
    TokenCategory, TradeParams, get_category_params, get_all_agents,
    get_agent_for_category, classify_token, CATEGORY_PARAMS,
)

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

# Legacy compat — old code may reference these
CATEGORY_TRADE_PARAMS = {k: vars(v) for k, v in CATEGORY_PARAMS.items()}


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


class TradingViewDiscoverer:
    """Discover tokens via TradingView Screener (FREE, no auth).

    DSH Pattern: EventBus -> DB -> Singleton

    Scans global crypto markets for momentum signals:
    - High relative volume, RSI, MACD, recommendation
    Filters for Solana-related pairs when possible.
    """

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._last_scan = 0
        self._scan_interval = 300
        self._cache = []

    def _emit_event(self, event_name, payload):
        """Emit event via EventBus (DSH pattern)."""
        try:
            from src.db_storage import log_event
            log_event(event_name, payload)
        except: pass
        if self.event_bus:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    _fire_and_forget(self.event_bus.emit(event_name, payload))
            except RuntimeError:
                pass

    def discover(self) -> List[dict]:
        """Scan TradingView crypto screener for momentum tokens."""
        now = time.time()
        if now - self._last_scan < self._scan_interval:
            return []
        self._last_scan = now

        try:
            from tradingview_screener import Query, col

            # Scan top crypto by volume with momentum signals
            q = (Query()
                .select(
                    'name', 'close', 'volume', 'market_cap_basic',
                    'change', 'change_abs', 'relative_volume_10d_calc',
                    'Recommend.All', 'RSI', 'MACD.macd', 'MACD.signal',
                    'price_52_week_high', 'price_52_week_low',
                )
                .where(
                    col('volume') > 1_000_000,  # Min $1M volume
                    col('market_cap_basic') > 100_000,  # Min $100K mcap
                )
                .order_by('relative_volume_10d_calc', ascending=False)
                .limit(30)
            )

            count, df = q.get_scanner_data()

            results = []
            for _, row in df.iterrows():
                name = str(row.get('name', ''))
                # Filter for Solana-related or major memecoins
                sol_keywords = ['SOL', 'BONK', 'WIF', 'POPCAT', 'FARTCOIN', 'MEW',
                               'BOME', 'MYRO', 'RETARDIO', 'GUMMY', 'TRUMP', 'PEPE']
                is_sol_related = any(kw.upper() in name.upper() for kw in sol_keywords)

                rec = float(row.get('Recommend.All', 0) or 0)
                rsi = float(row.get('RSI', 50) or 50)
                macd = float(row.get('MACD.macd', 0) or 0)
                macd_signal = float(row.get('MACD.signal', 0) or 0)
                rel_vol = float(row.get('relative_volume_10d_calc', 1) or 1)
                change = float(row.get('change', 0) or 0)

                # Build a pair-like dict for compatibility with scanner
                pair = {
                    'baseToken': {
                        'address': '',  # Will be resolved later if needed
                        'symbol': name.split(':')[1] if ':' in name else name,
                        'name': name,
                    },
                    'priceUsd': str(row.get('close', 0)),
                    'volume': {'h24': row.get('volume', 0)},
                    'priceChange': {'h24': change},
                    'liquidity': {'usd': row.get('market_cap_basic', 0) or 0},
                    'marketCap': row.get('market_cap_basic', 0),
                    'chainId': 'solana' if is_sol_related else 'unknown',
                    'dexId': 'tradingview',
                    # TradingView-specific data for scoring
                    '_tv_recommendation': rec,
                    '_tv_rsi': rsi,
                    '_tv_macd': macd,
                    '_tv_macd_signal': macd_signal,
                    '_tv_rel_volume': rel_vol,
                    '_tv_change': change,
                    '_tv_source': True,  # Flag: came from TradingView
                }
                results.append(pair)

            self._cache = results
            print('[TRADINGVIEW] Screener found ' + str(len(results)) + ' candidates', flush=True)

            # DSH: emit event
            from datetime import datetime, timezone
            self._emit_event('tradingview/screener_discovery', {
                'count': len(results),
                'candidates': [r['baseToken']['symbol'] for r in results[:10]],
                'timestamp': datetime.now(timezone.utc).isoformat(),
            })

            return results

        except Exception as e:
            print('[TRADINGVIEW] Screener error: ' + str(e), flush=True)
            return []


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
            s += 15; sig.append("Moderate volume")
        elif c.volume_24h > 1000:
            s += 10; sig.append("Some volume")
        elif c.volume_24h > 100:
            s += 5; sig.append("Low volume")

        # Liquidity scoring (max 25)
        if c.liquidity_usd > 100000:
            s += 25; sig.append("Deep liquidity")
        elif c.liquidity_usd > 50000:
            s += 20; sig.append("Good liquidity")
        elif c.liquidity_usd > 20000:
            s += 15; sig.append("Adequate liquidity")
        elif c.liquidity_usd > 5000:
            s += 12; sig.append("Low liquidity")
        elif c.liquidity_usd > 1000:
            s += 8; sig.append("Very low liquidity")
        elif c.liquidity_usd > 100:
            s += 5; sig.append("Micro liquidity")

        # Momentum scoring (max 20)
        if c.price_change_1h > 10:
            s += 20; sig.append("Strong 1h momentum")
        elif c.price_change_1h > 5:
            s += 15; sig.append("Good 1h momentum")
        elif c.price_change_1h > 2:
            s += 12; sig.append("Positive 1h momentum")
        elif c.price_change_1h > 0:
            s += 8; sig.append("Slight uptrend")
        elif c.price_change_1h > -5:
            s += 3; sig.append("Flat/down 1h")

        # Buy/sell ratio scoring (max 15)
        total_txns = c.txns_1h_buys + c.txns_1h_sells
        if total_txns > 0:
            buy_ratio = c.txns_1h_buys / total_txns
            if buy_ratio > 0.7:
                s += 15; sig.append("Strong buy pressure")
            elif buy_ratio > 0.55:
                s += 12; sig.append("Buy-side dominant")
            elif buy_ratio > 0.45:
                s += 8; sig.append("Balanced flow")
            elif total_txns > 10:
                s += 5; sig.append("Active trading")
        elif c.txns_1h_buys > 0:
            s += 10; sig.append("Buys only")

        # Market cap scoring (max 10) - prefer micro-caps
        if 0 < c.market_cap < 1000000:
            s += 10; sig.append("Micro-cap")
        elif 0 < c.market_cap < 5000000:
            s += 7; sig.append("Small-cap")
        elif 0 < c.market_cap < 20000000:
            s += 5; sig.append("Mid-cap")
        elif c.market_cap > 0:
            s += 2; sig.append("Large-cap")

        # TradingView signal bonus (max 15) — TV technical analysis boost
        tv_data = getattr(c, '_tv_data', None)
        if tv_data:
            rec = tv_data.get('recommendation', 0)
            rsi = tv_data.get('rsi', 50)
            macd = tv_data.get('macd', 0)
            macd_sig = tv_data.get('macd_signal', 0)
            rel_vol = tv_data.get('rel_volume', 1)

            # STRONG_BUY (+12-15), BUY (+8-10), NEUTRAL (+2-3)
            if rec >= 2:
                s += 15; sig.append('TV: STRONG BUY')
            elif rec >= 0.5:
                s += 10; sig.append('TV: BUY signal')
            elif rec >= 0:
                s += 3; sig.append('TV: NEUTRAL')
            elif rec >= -0.5:
                s += 1; sig.append('TV: SELL signal')

            # RSI oversold bonus (potential reversal)
            if rsi < 30:
                s += 5; sig.append('TV: RSI oversold (' + str(int(rsi)) + ')')
            elif rsi < 40:
                s += 2; sig.append('TV: RSI low (' + str(int(rsi)) + ')')

            # MACD bullish crossover
            if macd > macd_sig and macd_sig != 0:
                s += 3; sig.append('TV: MACD bullish')

            # High relative volume = strong momentum
            if rel_vol > 3:
                s += 5; sig.append('TV: High rel volume (' + str(round(rel_vol, 1)) + 'x)')
            elif rel_vol > 2:
                s += 3; sig.append('TV: Elevated volume (' + str(round(rel_vol, 1)) + 'x)')

        # Category bonus (max 5) — trending/boosted tokens get a small edge
        if c.source == "trending":
            s += 5; sig.append("Trending category")
        elif c.source == "boosted":
            s += 3; sig.append("Boosted token")
        elif c.source == "pump_fun":
            s += 4; sig.append("Pump.fun launch")
        elif c.source == "memecoin":
            s += 3; sig.append("Memecoin")

        c.score = min(s, 100.0)
        c.signals = sig
        return c.score


SEEN_TOKENS_RESET_SECONDS = 1800  # Reset seen tokens every 30 minutes


class TokenScanner:
    def __init__(self, callback=None, event_bus=None, scheduler=None):
        self.dexscreener = DexScreenerSource()
        self.jupiter = JupiterChecker()
        self.scorer = TokenScorer()
        self.trending = TrendingDiscoverer(self.dexscreener)
        self.callback = callback
        self.event_bus = event_bus
        self.tradingview = TradingViewDiscoverer(event_bus=self.event_bus)
        self.scheduler = scheduler
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

        # --- Category Agent Discovery (DSH: agents get event_bus + scheduler) ---
        all_pairs = {}
        agent_sources = {}  # addr -> (agent, source_label)
        agents = get_all_agents(event_bus=self.event_bus, scheduler=self.scheduler)

        for agent in agents:
            try:
                agent_pairs = agent.discover()
                for pair in agent_pairs:
                    addr = pair.get("baseToken", {}).get("address", "")
                    if addr and addr not in all_pairs:
                        all_pairs[addr] = pair
                        agent_sources[addr] = (agent, agent.name)
                if agent_pairs:
                    print("[" + agent.name.upper() + "] Found " + str(len(agent_pairs)) + " tokens", flush=True)
            except Exception as e:
                print("[" + agent.name.upper() + "] Discovery error: " + str(e), flush=True)

        # --- Also scan legacy trending/boosted sources ---
        trending_pairs = self.trending.discover()
        for pair in trending_pairs:
            addr = pair.get("baseToken", {}).get("address", "")
            if addr and addr not in all_pairs:
                all_pairs[addr] = pair
                agent_sources[addr] = (None, "trending")

        # --- TradingView Screener discovery ---
        tv_pairs = self.tradingview.discover()
        for pair in tv_pairs:
            addr = pair.get("baseToken", {}).get("address", "")
            symbol = pair.get("baseToken", {}).get("symbol", "")
            # Use symbol as key if no address (TradingView pairs may not have Solana addresses)
            key = addr if addr else symbol
            if key and key not in all_pairs:
                all_pairs[key] = pair
                agent_sources[key] = (None, "tradingview")

        print("[SCANNER] Total unique tokens from all sources: " + str(len(all_pairs)), flush=True)

        # --- Score and filter all candidates ---
        for addr, pair in all_pairs.items():
            if addr in self._seen_tokens:
                continue

            # Determine source and agent for this pair
            agent, source = agent_sources.get(addr, (None, "search"))

            # Check boosted cache
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

            # Attach TradingView data if available
            tv_source = pair.get('_tv_source', False)
            if tv_source:
                candidate._tv_data = {
                    'recommendation': pair.get('_tv_recommendation', 0),
                    'rsi': pair.get('_tv_rsi', 50),
                    'macd': pair.get('_tv_macd', 0),
                    'macd_signal': pair.get('_tv_macd_signal', 0),
                    'rel_volume': pair.get('_tv_rel_volume', 1),
                }
                if "tradingview" not in candidate.signals:
                    candidate.signals.append("TradingView screener")

            # Classify and assign category
            candidate.category = classify_token(candidate.name, candidate.symbol)

            # Use category agent for scoring if available
            if agent:
                candidate.score = agent.score(candidate)
            else:
                self.scorer.score(candidate)

            # Let agent decide if trade is viable
            if agent:
                should_trade, reason = agent.should_trade(candidate)
                if not should_trade:
                    print("[" + agent.name.upper() + "] SKIP " + candidate.symbol + ": " + reason, flush=True)
                    continue

            jup = self.jupiter.check_liquidity(addr)
            if not jup.get("available"):
                print("[SCANNER] " + candidate.symbol + ": No Jupiter liquidity", flush=True)
                continue

            if candidate.score >= 30:
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
        print("     Exit params: SL=" + str(params.stop_loss_pct) + "% TP=" + str(params.take_profit_pct) + "% Hold=" + str(params.max_hold_hours) + "h", flush=True)

    def _log_candidate(self, c):
        log_path = self.data_dir / "scanner_results.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(c.to_dict(), default=str) + chr(10))

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
