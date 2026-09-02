"""
Category Agents — Each Solana token category gets its own agent.

DSH Architecture:
  - Each agent emits events via EventBus (AGENT_DISCOVERY, AGENT_SCORED, AGENT_TRADE_SIGNAL)
  - Each agent is registered as a scheduled job in AsyncScheduler
  - Agent decisions flow through the event bus to the orchestrator
  - SessionLog records all agent actions for audit trail

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
import json
import asyncio
import requests
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict
from enum import Enum
from src.event_bus import _fire_and_forget

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

    DSH Architecture:
      - Emits events via EventBus (AGENT_DISCOVERY, AGENT_SCORED, AGENT_TRADE_SIGNAL)
      - Registered as scheduled jobs in AsyncScheduler
      - Decisions flow through event bus to orchestrator
      - SessionLog records all actions for audit trail

    Each agent discovers, scores, and trades tokens in its category
    with its own personality and rules.
    """

    def __init__(self, category: TokenCategory, event_bus=None, scheduler=None):
        self.category = category
        self.params = get_category_params(category)
        self._search_cache: Dict[str, float] = {}  # query -> last search time
        self._search_cache_ttl = 300  # 5 min cache
        # DSH components
        self.event_bus = event_bus
        self.scheduler = scheduler
        self._db_available = False
        try:
            from src.db_storage import get_pool
            self._db_available = get_pool() is not None
        except Exception:
            pass

    @property
    def name(self) -> str:
        return self.category.value

    # ── DSH Event Emission ────────────────────────────────────

    def _emit_event(self, event_name: str, payload: dict):
        """Emit event to EventBus (DSH pattern)."""
        if not self.event_bus:
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _fire_and_forget(self.event_bus.emit(event_name, payload))
            else:
                loop.run_until_complete(self.event_bus.emit(event_name, payload))
        except RuntimeError:
            pass

    def _log_to_db(self, event_type: str, data: dict):
        """Log event to DB (DSH audit trail)."""
        if not self._db_available:
            return
        try:
            from src.db_storage import log_event
            log_event(event_type, data)
        except Exception:
            pass

    def _log_session(self, description: str, data: dict = None):
        """Log to session event log (DSH SessionLog)."""
        self._log_to_db(self.name + "/" + description, data or {})

    # ── Discovery (abstract) ──────────────────────────────────

    @abstractmethod
    def get_search_terms(self) -> List[str]:
        """Return category-specific DexScreener search terms."""
        pass

    def discover(self) -> List[dict]:
        """
        Discover tokens via DexScreener search.
        DSH: Emits AGENT_DISCOVERY event with results.
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

        # DSH: Emit discovery event
        if all_pairs:
            self._emit_event("agent/discovery", {
                "agent": self.name,
                "category": str(self.category),
                "tokens_found": len(all_pairs),
                "search_terms": self.get_search_terms(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self._log_session("discovery", {
                "agent": self.name,
                "tokens_found": len(all_pairs),
            })

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
        DSH: Emits AGENT_TRADE_SIGNAL when approving.
        Returns (bool, reason).
        """
        should, reason = True, f"{self.name} agent approves"

        # DSH: Emit trade signal event
        if should:
            self._emit_event("agent/trade_signal", {
                "agent": self.name,
                "category": str(self.category),
                "token_address": candidate.address,
                "symbol": candidate.symbol,
                "score": candidate.score,
                "liquidity_usd": candidate.liquidity_usd,
                "volume_24h": candidate.volume_24h,
                "market_cap": candidate.market_cap,
                "trade_params": {
                    "stop_loss_pct": self.params.stop_loss_pct,
                    "take_profit_pct": self.params.take_profit_pct,
                    "max_hold_hours": self.params.max_hold_hours,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self._log_session("trade_signal", {
                "agent": self.name,
                "symbol": candidate.symbol,
                "score": candidate.score,
            })

        return should, reason

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

    def __init__(self, event_bus=None, scheduler=None):
        super().__init__(TokenCategory.AI_AGENT, event_bus=event_bus, scheduler=scheduler)

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

    def __init__(self, event_bus=None, scheduler=None):
        super().__init__(TokenCategory.POLITICAL, event_bus=event_bus, scheduler=scheduler)

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

    def __init__(self, event_bus=None, scheduler=None):
        super().__init__(TokenCategory.MEMECOIN, event_bus=event_bus, scheduler=scheduler)

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

    def __init__(self, event_bus=None, scheduler=None):
        super().__init__(TokenCategory.PUMP_FUN, event_bus=event_bus, scheduler=scheduler)

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

    def __init__(self, event_bus=None, scheduler=None):
        super().__init__(TokenCategory.TRENDING, event_bus=event_bus, scheduler=scheduler)

    def get_search_terms(self) -> str:
        return [
            "trending solana",
            "hot token solana",
            "gaining volume",
            "trending memecoin",
        ]

    def score(self, candidate) -> float:
        """
        Improved trending token scoring.

        Key insight: trending tokens are momentum plays. We want:
        1. Strong but not parabolic momentum (sweet spot: 2-15% 1h)
        2. Volume that's growing, not just high
        3. Deep liquidity relative to volume (safer exits)
        4. Strong buy/sell imbalance (conviction)
        5. Sweet spot market cap ($500K-$5M for maximum upside)
        """
        s = 0.0
        sigs = []

        # --- 1. Momentum Quality (max 25) ---
        # Sweet spot: 2-15% = sustained, >15% = parabolic (risky)
        pc = candidate.price_change_1h
        if 5 < pc < 15:
            s += 25; sigs.append("Momentum sweet spot (5-15%)")
        elif 2 < pc <= 5:
            s += 18; sigs.append("Building momentum (2-5%)")
        elif 15 <= pc < 25:
            s += 12; sigs.append("Parabolic momentum (caution)")
        elif pc >= 25:
            s += 5; sigs.append("Extreme pump (high risk)")
        elif 0 < pc <= 2:
            s += 8; sigs.append("Early momentum forming")

        # 24h momentum confirms trend (not just 1h spike)
        if candidate.price_change_24h > 10:
            s += 5; sigs.append("24h trend confirmed")

        # --- 2. Volume Quality (max 20) ---
        vol = candidate.volume_24h
        if vol > 500_000:
            s += 20; sigs.append("Very high volume")
        elif vol > 200_000:
            s += 16; sigs.append("High volume")
        elif vol > 50_000:
            s += 12; sigs.append("Strong volume")
        elif vol > 20_000:
            s += 6; sigs.append("Moderate volume")

        # --- 3. Liquidity Depth Ratio (max 20) ---
        # Liquidity relative to volume = exit safety
        # High ratio = deep pool, easy to exit
        if candidate.liquidity_usd > 0 and vol > 0:
            liq_ratio = candidate.liquidity_usd / vol
            if liq_ratio > 5:
                s += 20; sigs.append("Very deep liquidity pool")
            elif liq_ratio > 2:
                s += 15; sigs.append("Deep liquidity")
            elif liq_ratio > 1:
                s += 10; sigs.append("Adequate liquidity")
        elif candidate.liquidity_usd > 200_000:
            s += 12; sigs.append("High absolute liquidity")

        # --- 4. Buy/Sell Imbalance (max 20) ---
        total = candidate.txns_1h_buys + candidate.txns_1h_sells
        if total > 0:
            buy_ratio = candidate.txns_1h_buys / total
            buy_skew = buy_ratio - 0.5  # How far from 50/50
            if buy_skew > 0.3 and total > 30:
                s += 20; sigs.append("Strong buy conviction (>65%, 30+ txns)")
            elif buy_skew > 0.2 and total > 15:
                s += 14; sigs.append("Moderate buy pressure")
            elif buy_skew > 0.1:
                s += 8; sigs.append("Slight buy bias")
            elif buy_skew < -0.2:
                s -= 5; sigs.append("Sell pressure detected (penalty)")

        # --- 5. Transaction Activity (max 10) ---
        if total > 200:
            s += 10; sigs.append("Very high activity")
        elif total > 50:
            s += 7; sigs.append("High activity")
        elif total > 20:
            s += 4; sigs.append("Moderate activity")

        # --- 6. Market Cap Sweet Spot (max 5) ---
        mcap = candidate.market_cap
        if 500_000 < mcap < 5_000_000:
            s += 5; sigs.append("Ideal mcap for trending (500K-5M)")
        elif 100_000 < mcap <= 500_000:
            s += 3; sigs.append("Small mcap trending")
        elif mcap > 10_000_000:
            s -= 3; sigs.append("Large mcap — limited upside (penalty)")

        if hasattr(candidate, 'signals'):
            candidate.signals = sigs

        return max(min(s, 100), 0)

    def should_trade(self, candidate) -> tuple:
        """
        Trending tokens need:
        - Minimum volume to prove interest
        - Positive or neutral momentum (not dumping)
        - Minimum liquidity for safe exit
        """
        if candidate.volume_24h < 10_000:
            return False, "Trending volume too low"
        if candidate.price_change_1h < -10:
            return False, "Trending token dumping"
        if candidate.liquidity_usd < 30_000:
            return False, "Trending liquidity too low for safe exit"
        # Reject if sell pressure dominates
        total = candidate.txns_1h_buys + candidate.txns_1h_sells
        if total > 10:
            buy_ratio = candidate.txns_1h_buys / total
            if buy_ratio < 0.35:
                return False, "Trending has strong sell pressure"
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

    def __init__(self, event_bus=None, scheduler=None):
        super().__init__(TokenCategory.BOOSTED, event_bus=event_bus, scheduler=scheduler)

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

def get_all_agents(event_bus=None, scheduler=None) -> List[CategoryAgent]:
    """Return all category agents with DSH components."""
    return [
        AIAgentAgent(event_bus=event_bus, scheduler=scheduler),
        PoliticalAgent(event_bus=event_bus, scheduler=scheduler),
        MemecoinAgent(event_bus=event_bus, scheduler=scheduler),
        PumpFunAgent(event_bus=event_bus, scheduler=scheduler),
        TrendingAgent(event_bus=event_bus, scheduler=scheduler),
        BoostedAgent(event_bus=event_bus, scheduler=scheduler),
    ]


def get_agent_for_category(category: TokenCategory, event_bus=None, scheduler=None) -> CategoryAgent:
    """Get the agent for a specific category with DSH components."""
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
        return agent_cls(event_bus=event_bus, scheduler=scheduler)
    # Default: use memecoin agent for unknown
    return MemecoinAgent(event_bus=event_bus, scheduler=scheduler)


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
