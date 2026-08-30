"""
Category Agents — Each Solana token category gets its own agent.

Each agent handles:
  - Discovery: how to find tokens in this category
  - Scoring: what makes a good token in this category
  - Trading style: SL/TP/hold/position sizing
  - Personality: how aggressive/conservative

Architecture:
  CategoryAgent (base)
  ├── AIAgentAgent        — AI agent tokens (AI16Z, GOAT, VIRTUAL)
  ├── PoliticalAgent      — Political/event-driven (TRUMP, MAGA)
  ├── MemecoinAgent       — Animal/community tokens (cat, dog, pepe)
  ├── PumpFunAgent        — Fresh pump.fun launches
  ├── TrendingAgent       — DexScreener trending momentum
  └── BoostedAgent        — Paid-boosted tokens

Security: READ-ONLY discovery. Never executes trades.
"""

import time
import requests
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict
from enum import Enum

# DexScreener endpoints (FREE, no API key)
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search"
DEXSCREENER_TOKENS = "https://api.dexscreener.com/tokens/v1/solana/"


# ── Category Enum ────────────────────────────────────────────

class TokenCategory(str, Enum):
    AI_AGENT = "ai_agent"
    POLITICAL = "political"
    MEMECOIN = "memecoin"
    PUMP_FUN = "pump_fun"
    TRENDING = "trending"
    BOOSTED = "boosted"
    UNKNOWN = "unknown"

    def __str__(self):
        return self.value


# ── Trade Parameters ─────────────────────────────────────────

@dataclass
class TradeParams:
    stop_loss_pct: float
    take_profit_pct: float
    max_hold_hours: float
    position_size_pct: float  # % of capital per trade
    max_positions: int        # max concurrent positions in this category
    description: str = ""


CATEGORY_PARAMS: Dict[TokenCategory, TradeParams] = {
    TokenCategory.AI_AGENT: TradeParams(
        stop_loss_pct=5.0, take_profit_pct=50.0, max_hold_hours=48.0,
        position_size_pct=0.20, max_positions=3,
        description="AI agent — let narratives play out, wide TP, tight SL",
    ),
    TokenCategory.POLITICAL: TradeParams(
        stop_loss_pct=15.0, take_profit_pct=20.0, max_hold_hours=6.0,
        position_size_pct=0.15, max_positions=2,
        description="Political — fast in, fast out, event-driven",
    ),
    TokenCategory.MEMECOIN: TradeParams(
        stop_loss_pct=10.0, take_profit_pct=30.0, max_hold_hours=12.0,
        position_size_pct=0.25, max_positions=4,
        description="Memecoin — standard momentum trading",
    ),
    TokenCategory.PUMP_FUN: TradeParams(
        stop_loss_pct=20.0, take_profit_pct=40.0, max_hold_hours=2.0,
        position_size_pct=0.10, max_positions=2,
        description="Pump.fun — quick scalp, high risk, tight time limit",
    ),
    TokenCategory.TRENDING: TradeParams(
        stop_loss_pct=10.0, take_profit_pct=35.0, max_hold_hours=12.0,
        position_size_pct=0.20, max_positions=3,
        description="Trending — ride the momentum, slightly wider TP",
    ),
    TokenCategory.BOOSTED: TradeParams(
        stop_loss_pct=12.0, take_profit_pct=25.0, max_hold_hours=8.0,
        position_size_pct=0.10, max_positions=2,
        description="Boosted — someone paid to promote, be cautious",
    ),
    TokenCategory.UNKNOWN: TradeParams(
        stop_loss_pct=10.0, take_profit_pct=30.0, max_hold_hours=12.0,
        position_size_pct=0.15, max_positions=3,
        description="Unknown — standard defaults",
    ),
}


def get_category_params(category: TokenCategory) -> TradeParams:
    return CATEGORY_PARAMS.get(category, CATEGORY_PARAMS[TokenCategory.UNKNOWN])


# ── Base Category Agent ──────────────────────────────────────

class CategoryAgent(ABC):
    """
    Base class for all category agents.

    Each agent discovers, scores, and trades tokens in its category
    with its own personality and rules.
    """

    def __init__(self, category: TokenCategory):
        self.category = category
        self.params = get_category_params(category)
        self._search_cache: Dict[str, float] = {}  # query -> last search time
        self._search_cache_ttl = 300  # 5 min cache

    @property
    def name(self) -> str:
        return self.category.value

    # ── Discovery (abstract) ──────────────────────────────────

    @abstractmethod
    def get_search_terms(self) -> List[str]:
        """Return category-specific DexScreener search terms."""
        pass

    def discover(self) -> List[dict]:
        """
        Discover tokens via DexScreener search.
        Returns list of pair dicts (DexScreener format).
        """
        all_pairs = []
        for term in self.get_search_terms():
            # Rate limit: skip if searched recently
            now = time.time()
            if term in self._search_cache and (now - self._search_cache[term]) < self._search_cache_ttl:
                continue

            try:
                resp = requests.get(
                    DEXSCREENER_SEARCH,
                    params={"q": term},
                    timeout=15,
                )
                if resp.status_code != 200:
                    continue
                pairs = resp.json().get("pairs", []) or []
                sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                all_pairs.extend(sol_pairs)
                self._search_cache[term] = now
                time.sleep(0.3)  # Rate limit
            except Exception as e:
                print(f"[{self.name.upper()}] Search error for '{term}': {e}", flush=True)

        return all_pairs

    # ── Scoring (abstract) ────────────────────────────────────

    @abstractmethod
    def score(self, candidate) -> float:
        """
        Score a token candidate 0-100.
        Each category weights metrics differently.
        """
        pass

    # ── Trading Style ─────────────────────────────────────────

    def get_trade_params(self) -> TradeParams:
        return self.params

    def should_trade(self, candidate) -> tuple:
        """
        Category-specific trade decision.
        Returns (bool, reason).
        """
        return True, f"{self.name} agent approves"

    def get_position_size_pct(self, score: float) -> float:
        """
        Adjust position size based on score and category personality.
        Higher score = larger position, within category limits.
        """
        base = self.params.position_size_pct
        if score >= 80:
            return min(base * 1.2, 0.40)  # Up to 40% max
        elif score >= 60:
            return base
        else:
            return base * 0.7  # Reduce for lower scores


# ── AI Agent Token Agent ─────────────────────────────────────

class AIAgentAgent(CategoryAgent):
    """
    Discovers and trades AI agent tokens on Solana.

    Personality: Patient. Lets narratives play out.
    - Wide TP (50%) — AI agent narratives can run hard
    - Tight SL (5%) — cut losers fast, let winners run
    - Long hold (48h) — AI narratives take time to develop
    - Prefers tokens with real utility or strong community
    """

    def __init__(self):
        super().__init__(TokenCategory.AI_AGENT)

    def get_search_terms(self) -> str:
        return [
            "virtuals protocol",
            "ai16z",
            "goatse",
            "ai agent solana",
            "eliza os",
            "ai memecoin",
            "agent token",
            "virtuals ai",
        ]

    def score(self, candidate) -> float:
        s = 0.0
        sigs = []

        # Volume (max 25) — AI agents need real trading interest
        if candidate.volume_24h > 200_000:
            s += 25; sigs.append("High AI agent volume")
        elif candidate.volume_24h > 50_000:
            s += 20; sigs.append("Strong AI agent volume")
        elif candidate.volume_24h > 20_000:
            s += 15; sigs.append("Good AI agent volume")

        # Liquidity depth (max 20)
        if candidate.liquidity_usd > 500_000:
            s += 20; sigs.append("Deep AI agent liquidity")
        elif candidate.liquidity_usd > 100_000:
            s += 15; sigs.append("Good AI agent liquidity")

        # Buy pressure (max 20) — AI agents attract buyers
        total = candidate.txns_1h_buys + candidate.txns_1h_sells
        if total > 0:
            buy_ratio = candidate.txns_1h_buys / total
            if buy_ratio > 0.7:
                s += 20; sigs.append("Strong AI agent buy pressure")
            elif buy_ratio > 0.5:
                s += 10; sigs.append("Moderate AI agent buy pressure")

        # Momentum (max 15) — AI agents ride narratives
        if candidate.price_change_1h > 5:
            s += 15; sigs.append("AI agent momentum surge")
        elif candidate.price_change_1h > 0:
            s += 8; sigs.append("AI agent positive momentum")

        # Market cap sweet spot (max 10) — not too big, not too small
        if 500_000 < candidate.market_cap < 5_000_000:
            s += 10; sigs.append("AI agent ideal mcap range")
        elif candidate.market_cap < 500_000:
            s += 5; sigs.append("AI agent micro-cap")

        # Bonus: signals
        if hasattr(candidate, 'signals'):
            candidate.signals = sigs

        return min(s, 100)

    def should_trade(self, candidate) -> tuple:
        # AI agents need at least some volume
        if candidate.volume_24h < 10_000:
            return False, "AI agent volume too low"
        # AI agents need liquidity
        if candidate.liquidity_usd < 50_000:
            return False, "AI agent liquidity too low"
        return True, "AI agent meets criteria"


# ── Political Token Agent ────────────────────────────────────

class PoliticalAgent(CategoryAgent):
    """
    Discovers and trades political/event-driven tokens.

    Personality: Fast and reactive.
    - Tight TP (20%) — political tokens spike and dump
    - Wide SL (15%) — volatile, need room
    - Short hold (6h) — event-driven, fast in/out
    - Triggers on news/events
    """

    def __init__(self):
        super().__init__(TokenCategory.POLITICAL)

    def get_search_terms(self) -> str:
        return [
            "trump coin",
            "maga token",
            "political memecoin",
            "election token",
            "kamala coin",
            "elon coin",
            "doge government",
        ]

    def score(self, candidate) -> float:
        s = 0.0
        sigs = []

        # Volume spikes matter most for political tokens (max 30)
        if candidate.volume_24h > 500_000:
            s += 30; sigs.append("Political volume spike")
        elif candidate.volume_24h > 100_000:
            s += 20; sigs.append("Strong political volume")

        # Recent momentum (max 25) — political tokens are event-driven
        if candidate.price_change_1h > 10:
            s += 25; sigs.append("Political momentum explosion")
        elif candidate.price_change_1h > 3:
            s += 15; sigs.append("Political positive momentum")

        # Buy pressure (max 20)
        total = candidate.txns_1h_buys + candidate.txns_1h_sells
        if total > 0:
            buy_ratio = candidate.txns_1h_buys / total
            if buy_ratio > 0.6:
                s += 20; sigs.append("Political buy rush")

        # Liquidity (max 15)
        if candidate.liquidity_usd > 100_000:
            s += 15; sigs.append("Political adequate liquidity")

        # Transaction count (max 10) — activity matters
        if total > 50:
            s += 10; sigs.append("Political high activity")

        if hasattr(candidate, 'signals'):
            candidate.signals = sigs

        return min(s, 100)

    def should_trade(self, candidate) -> tuple:
        # Political tokens need recent momentum
        if candidate.price_change_1h < -5:
            return False, "Political token dumping"
        # Need volume
        if candidate.volume_24h < 20_000:
            return False, "Political volume too low"
        return True, "Political meets criteria"


# ── Memecoin Agent ───────────────────────────────────────────

class MemecoinAgent(CategoryAgent):
    """
    Discovers and trades animal/community memecoins.

    Personality: Balanced momentum trader.
    - Standard SL/TP (10%/30%)
    - Medium hold (12h)
    - Looks for community signals, social buzz
    - Prefers established memecoins with track record
    """

    def __init__(self):
        super().__init__(TokenCategory.MEMECOIN)

    def get_search_terms(self) -> str:
        return [
            "solana memecoin",
            "cat token solana",
            "dog token solana",
            "pepe solana",
            "inu token",
            "ape coin solana",
            "frog token",
            "monkey coin",
        ]

    def score(self, candidate) -> float:
        s = 0.0
        sigs = []

        # Volume (max 25)
        if candidate.volume_24h > 100_000:
            s += 25; sigs.append("High memecoin volume")
        elif candidate.volume_24h > 30_000:
            s += 18; sigs.append("Good memecoin volume")

        # Liquidity (max 20)
        if candidate.liquidity_usd > 200_000:
            s += 20; sigs.append("Deep memecoin liquidity")
        elif candidate.liquidity_usd > 50_000:
            s += 12; sigs.append("Adequate memecoin liquidity")

        # Buy/sell ratio (max 20)
        total = candidate.txns_1h_buys + candidate.txns_1h_sells
        if total > 0:
            buy_ratio = candidate.txns_1h_buys / total
            if buy_ratio > 0.65:
                s += 20; sigs.append("Strong memecoin buy pressure")
            elif buy_ratio > 0.5:
                s += 10; sigs.append("Balanced memecoin activity")

        # Momentum (max 20)
        if 0 < candidate.price_change_1h < 10:
            s += 20; sigs.append("Memecoin steady momentum")
        elif candidate.price_change_1h > 10:
            s += 12; sigs.append("Memecoin pump (caution)")

        # Market cap (max 15)
        if 100_000 < candidate.market_cap < 3_000_000:
            s += 15; sigs.append("Memecoin sweet spot mcap")

        if hasattr(candidate, 'signals'):
            candidate.signals = sigs

        return min(s, 100)

    def should_trade(self, candidate) -> tuple:
        if candidate.volume_24h < 5_000:
            return False, "Memecoin volume too low"
        if candidate.liquidity_usd < 25_000:
            return False, "Memecoin liquidity too low"
        return True, "Memecoin meets criteria"


# ── Pump.fun Agent ───────────────────────────────────────────

class PumpFunAgent(CategoryAgent):
    """
    Discovers and trades fresh pump.fun launches.

    Personality: Quick scalper.
    - Wide SL (20%) — fresh tokens are volatile
    - Wide TP (40%) — early entries can 2-5x
    - Very short hold (2h) — get in, get out
    - High risk, small positions
    - Looks for early momentum signals
    """

    def __init__(self):
        super().__init__(TokenCategory.PUMP_FUN)

    def get_search_terms(self) -> str:
        return [
            "pump.fun",
            "pumpswap",
            "pump.fun solana",
            "new solana token",
            "solana launch",
            "bonding curve",
        ]

    def score(self, candidate) -> float:
        s = 0.0
        sigs = []

        # Freshness matters most (max 25) — pump.fun tokens are new
        if hasattr(candidate, 'pair_age_hours') and candidate.pair_age_hours < 6:
            s += 25; sigs.append("Very fresh pump.fun launch")
        elif hasattr(candidate, 'pair_age_hours') and candidate.pair_age_hours < 24:
            s += 15; sigs.append("Recent pump.fun launch")

        # Volume surge (max 25) — early volume = early interest
        if candidate.volume_24h > 50_000:
            s += 25; sigs.append("Pump.fun volume surge")
        elif candidate.volume_24h > 10_000:
            s += 15; sigs.append("Pump.fun building volume")

        # Buy pressure (max 25) — early buyers = conviction
        total = candidate.txns_1h_buys + candidate.txns_1h_sells
        if total > 0:
            buy_ratio = candidate.txns_1h_buys / total
            if buy_ratio > 0.7:
                s += 25; sigs.append("Pump.fun strong buy rush")
            elif buy_ratio > 0.5:
                s += 12; sigs.append("Pump.fun moderate buying")

        # Micro-cap bonus (max 15)
        if candidate.market_cap < 500_000:
            s += 15; sigs.append("Pump.fun micro-cap opportunity")
        elif candidate.market_cap < 2_000_000:
            s += 8; sigs.append("Pump.fun small-cap")

        # DEX bonus (max 10)
        if candidate.dex in ("pumpswap", "raydium"):
            s += 10; sigs.append("Pump.fun on native DEX")

        if hasattr(candidate, 'signals'):
            candidate.signals = sigs

        return min(s, 100)

    def should_trade(self, candidate) -> tuple:
        # Pump.fun tokens need to be fresh
        if hasattr(candidate, 'pair_age_hours') and candidate.pair_age_hours > 48:
            return False, "Pump.fun token too old"
        # Need minimum volume
        if candidate.volume_24h < 5_000:
            return False, "Pump.fun volume too low"
        return True, "Pump.fun meets criteria"


# ── Trending Agent ───────────────────────────────────────────

class TrendingAgent(CategoryAgent):
    """
    Discovers and trades tokens trending on DexScreener.

    Personality: Momentum rider.
    - Standard SL (10%) — trending tokens have support
    - Wider TP (35%) — trends can extend
    - Medium hold (12h) — ride the trend
    - Uses DexScreener trending categories
    - Looks for sustained momentum, not just spikes
    """

    def __init__(self):
        super().__init__(TokenCategory.TRENDING)

    def get_search_terms(self) -> str:
        return [
            "trending solana",
            "hot token solana",
            "gaining volume",
            "trending memecoin",
        ]

    def score(self, candidate) -> float:
        s = 0.0
        sigs = []

        # Volume (max 30) — trending = high volume
        if candidate.volume_24h > 200_000:
            s += 30; sigs.append("Trending high volume")
        elif candidate.volume_24h > 50_000:
            s += 22; sigs.append("Trending strong volume")

        # Liquidity (max 20)
        if candidate.liquidity_usd > 300_000:
            s += 20; sigs.append("Trending deep liquidity")
        elif candidate.liquidity_usd > 100_000:
            s += 12; sigs.append("Trending adequate liquidity")

        # Sustained momentum (max 20) — not just a spike
        if 2 < candidate.price_change_1h < 15:
            s += 20; sigs.append("Trending sustained momentum")
        elif candidate.price_change_1h > 15:
            s += 10; sigs.append("Trending parabolic (caution)")

        # Buy pressure (max 15)
        total = candidate.txns_1h_buys + candidate.txns_1h_sells
        if total > 20:
            buy_ratio = candidate.txns_1h_buys / total
            if buy_ratio > 0.6:
                s += 15; sigs.append("Trending strong buy pressure")

        # Activity level (max 15)
        if total > 100:
            s += 15; sigs.append("Trending very active")
        elif total > 30:
            s += 8; sigs.append("Trending active")

        if hasattr(candidate, 'signals'):
            candidate.signals = sigs

        return min(s, 100)

    def should_trade(self, candidate) -> tuple:
        if candidate.volume_24h < 10_000:
            return False, "Trending volume too low"
        # Trending tokens should have positive momentum
        if candidate.price_change_1h < -10:
            return False, "Trending token dumping"
        return True, "Trending meets criteria"


# ── Boosted Agent ────────────────────────────────────────────

class BoostedAgent(CategoryAgent):
    """
    Discovers and evaluates paid-boosted tokens.

    Personality: Cautious evaluator.
    - Tighter TP (25%) — boosted tokens often dump after boost
    - Medium SL (12%) — some room for volatility
    - Shorter hold (8h) — don't overstay
    - Small positions — boosted != quality
    - Looks for organic volume behind the boost
    """

    def __init__(self):
        super().__init__(TokenCategory.BOOSTED)

    def get_search_terms(self) -> str:
        return [
            "boosted solana",
            "promoted token",
        ]

    def discover(self) -> List[dict]:
        """
        Override: Fetch boosted tokens directly from DexScreener boosted endpoint.
        """
        pairs = []
        boosted_addrs = set()

        try:
            # Top boosted
            resp = requests.get("https://api.dexscreener.com/token-boosts/top/v1", timeout=15)
            if resp.status_code == 200:
                boosts = resp.json()
                if isinstance(boosts, list):
                    for b in boosts[:20]:
                        if b.get("chainId") == "solana":
                            addr = b.get("tokenAddress", "")
                            if addr:
                                boosted_addrs.add(addr)
        except Exception:
            pass

        try:
            # Recently boosted
            resp = requests.get("https://api.dexscreener.com/token-boosts/latest/v1", timeout=15)
            if resp.status_code == 200:
                boosts = resp.json()
                if isinstance(boosts, list):
                    for b in boosts[:20]:
                        if b.get("chainId") == "solana":
                            addr = b.get("tokenAddress", "")
                            if addr:
                                boosted_addrs.add(addr)
        except Exception:
            pass

        # Fetch pair data in batches
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
            except Exception:
                pass

        return pairs

    def score(self, candidate) -> float:
        s = 0.0
        sigs = []

        # Volume behind the boost (max 25) — organic interest matters
        if candidate.volume_24h > 100_000:
            s += 25; sigs.append("Boosted with real volume")
        elif candidate.volume_24h > 30_000:
            s += 15; sigs.append("Boosted with moderate volume")

        # Liquidity (max 20)
        if candidate.liquidity_usd > 200_000:
            s += 20; sigs.append("Boosted deep liquidity")
        elif candidate.liquidity_usd > 50_000:
            s += 10; sigs.append("Boosted adequate liquidity")

        # Buy pressure (max 20) — is the boost working?
        total = candidate.txns_1h_buys + candidate.txns_1h_sells
        if total > 0:
            buy_ratio = candidate.txns_1h_buys / total
            if buy_ratio > 0.6:
                s += 20; sigs.append("Boosted generating buys")
            elif buy_ratio > 0.4:
                s += 8; sigs.append("Boosted mixed activity")

        # Price action (max 20) — boosted tokens should at least hold
        if candidate.price_change_1h > 0:
            s += 20; sigs.append("Boosted holding value")
        elif candidate.price_change_1h > -5:
            s += 10; sigs.append("Boosted slight dip")

        # Market cap (max 15) — not too big
        if candidate.market_cap < 5_000_000:
            s += 15; sigs.append("Boosted reasonable mcap")

        if hasattr(candidate, 'signals'):
            candidate.signals = sigs

        return min(s, 100)

    def should_trade(self, candidate) -> tuple:
        # Boosted tokens need organic volume
        if candidate.volume_24h < 5_000:
            return False, "Boosted has no organic volume"
        # Don't buy if already dumping
        if candidate.price_change_1h < -10:
            return False, "Boosted token already dumping"
        return True, "Boosted meets criteria"


# ── Agent Registry ───────────────────────────────────────────

def get_all_agents() -> List[CategoryAgent]:
    """Return all category agents."""
    return [
        AIAgentAgent(),
        PoliticalAgent(),
        MemecoinAgent(),
        PumpFunAgent(),
        TrendingAgent(),
        BoostedAgent(),
    ]


def get_agent_for_category(category: TokenCategory) -> CategoryAgent:
    """Get the agent for a specific category."""
    agents = {
        TokenCategory.AI_AGENT: AIAgentAgent,
        TokenCategory.POLITICAL: PoliticalAgent,
        TokenCategory.MEMECOIN: MemecoinAgent,
        TokenCategory.PUMP_FUN: PumpFunAgent,
        TokenCategory.TRENDING: TrendingAgent,
        TokenCategory.BOOSTED: BoostedAgent,
    }
    agent_cls = agents.get(category)
    if agent_cls:
        return agent_cls()
    # Default: use memecoin agent for unknown
    return MemecoinAgent()


def classify_token(name: str, symbol: str, description: str = "") -> TokenCategory:
    """Classify a token into a category based on name/symbol/description."""
    text = f"{name} {symbol} {description}".lower()

    # AI agent tokens
    ai_keywords = ["ai", "agent", "gpt", "llm", "openai", "anthropic", "neural",
                    "robot", "bot", "machine learning", "deepseek", "qwen", "virtual",
                    "eliza", "goat", "ai16z"]
    if any(kw in text for kw in ai_keywords):
        return TokenCategory.AI_AGENT

    # Political tokens
    political_keywords = ["trump", "biden", "maga", "election", "president",
                          "vote", "politic", "kamala", "elon", "doge"]
    if any(kw in text for kw in political_keywords):
        return TokenCategory.POLITICAL

    # Pump.fun tokens
    if "pump" in text or (len(symbol) > 4 and symbol.lower().endswith("pump")):
        return TokenCategory.PUMP_FUN

    # Memecoins
    meme_keywords = ["cat", "dog", "bear", "bull", "frog", "ape", "monkey",
                     "pepe", "wojak", "chad", "retard", "degen", "moon",
                     "inu", "shib", "bonk", "popcat", "bome", "fart"]
    if any(kw in text for kw in meme_keywords):
        return TokenCategory.MEMECOIN

    return TokenCategory.UNKNOWN
