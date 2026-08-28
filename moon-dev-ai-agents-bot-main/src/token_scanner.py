"""Token Scanner for micro-cap Solana tokens using DexScreener (FREE, no API key)."""
import os
import time
import json
import requests
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Callable
from pathlib import Path

# -- API Endpoints (all FREE, no API key needed) ---------------

JUPITER_QUOTE = "https://api.jup.ag/swap/v1/quote"
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search"
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

MIN_LIQUIDITY_USD = 5000
MIN_VOLUME_24H = 1000
MIN_HOLDERS = 10
SCAN_INTERVAL_SECONDS = 30


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
            "signals": self.signals, "timestamp": self.timestamp.isoformat(),
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

    def pair_to_candidate(self, pair: dict) -> Optional[TokenCandidate]:
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

        return TokenCandidate(
            address=addr,
            symbol=base.get("symbol", "UNKNOWN"),
            name=base.get("name", "Unknown"),
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

        c.score = min(s, 100.0)
        c.signals = sig
        return c.score


class TokenScanner:
    def __init__(self, callback=None):
        self.dexscreener = DexScreenerSource()
        self.jupiter = JupiterChecker()
        self.scorer = TokenScorer()
        self.callback = callback
        self._seen_tokens = set()
        self._scan_count = 0
        self.data_dir = Path("src/data/scanner")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def scan_once(self):
        candidates = []
        self._scan_count += 1
        print("[SCANNER] Scan #" + str(self._scan_count) + " starting...", flush=True)
        all_pairs = {}
        for term in SCAN_TERMS:
            pairs = self.dexscreener.search(term)
            for pair in pairs:
                addr = pair.get("baseToken", {}).get("address", "")
                if addr and addr not in all_pairs:
                    all_pairs[addr] = pair
            time.sleep(0.3)
        print("[SCANNER] Found " + str(len(all_pairs)) + " unique tokens from DexScreener", flush=True)
        for addr, pair in all_pairs.items():
            if addr in self._seen_tokens:
                continue
            candidate = self.dexscreener.pair_to_candidate(pair)
            if not candidate:
                continue
            if candidate.liquidity_usd < MIN_LIQUIDITY_USD:
                continue
            if candidate.volume_24h < MIN_VOLUME_24H:
                continue
            jup = self.jupiter.check_liquidity(addr)
            if not jup.get("available"):
                print("[SCANNER] " + candidate.symbol + ": No Jupiter liquidity", flush=True)
                continue
            self.scorer.score(candidate)
            if candidate.score >= 30:
                candidates.append(candidate)
                self._seen_tokens.add(addr)
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
        print("  NEW CANDIDATE: " + c.symbol, flush=True)
        print("     Score: " + str(int(c.score)) + "/100", flush=True)
        print("     Volume 24h: $" + "{:,.0f}".format(c.volume_24h), flush=True)
        print("     Liquidity: $" + "{:,.0f}".format(c.liquidity_usd), flush=True)
        print("     Market Cap: $" + "{:,.0f}".format(c.market_cap), flush=True)
        print("     1h Change: " + ("+" if c.price_change_1h >= 0 else "") + str(round(c.price_change_1h, 1)) + "%", flush=True)
        print("     Buy/Sell 1h: " + str(c.txns_1h_buys) + "/" + str(c.txns_1h_sells), flush=True)
        print("     DEX: " + c.dex, flush=True)
        print("     Signals: " + ", ".join(c.signals), flush=True)

    def _log_candidate(self, c):
        log_path = self.data_dir / "scanner_results.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(c.to_dict()) + chr(10))

    def get_scan_stats(self):
        return {"total_scans": self._scan_count, "unique_tokens_seen": len(self._seen_tokens)}


async def scan_for_tokens(callback=None):
    scanner = TokenScanner(callback=callback)
    return scanner.scan_once()
