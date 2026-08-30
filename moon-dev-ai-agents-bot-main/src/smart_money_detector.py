"""
Smart Money Detector - Multi-Wallet Consensus Signals

Detects when multiple high-scoring (profitable) wallets buy the same
token within a configurable time window. Generates smart money consensus
signals that become an input to the AI agent's trading decisions.

Architecture:
  WalletTracker (activity) + WalletScorer (scores)
       |
       v
  SmartMoneyDetector
       |
       |-- Detects N+ wallets buying same token within window
       |-- Computes aggregate volume, weighted wallet quality
       |-- Generates SmartMoneySignal
       |
       v
  MCP Server (get_smart_money_flow) + AI Agent
"""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from collections import defaultdict


DATA_DIR = Path("src/data")
SMART_MONEY_LOG = DATA_DIR / "wallet_tracker" / "smart_money_signals.jsonl"
CONSENSUS_LOG = DATA_DIR / "wallet_tracker" "smart_money_consensus.jsonl"


@dataclass
class SmartMoneySignal:
    """A detected smart money consensus signal."""
    token_address: str
    token_symbol: str
    wallets_buying: int
    wallets_selling: int
    aggregate_buy_sol: float
    aggregate_sell_sol: float
    avg_wallet_score: float
    weighted_quality: float
    confidence: float
    time_window_seconds: int
    first_buy_time: str
    last_buy_time: str
    buying_wallets: list
    timestamp: str

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict):
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


class SmartMoneyDetector:
    """
    Detects smart money consensus signals.

    When 2+ wallets with score >= min_wallet_score buy the same token
    within time_window_seconds, a SmartMoneySignal is generated.

    Usage:
        detector = SmartMoneyDetector(tracker, scorer)
        signals = detector.scan()
    """

    def __init__(self, tracker=None, scorer=None, data_dir: Path = None):
        self.data_dir = data_dir or DATA_DIR
        self.tracker = tracker
        self.scorer = scorer
        self.min_wallet_score = 40.0
        self.time_window_seconds = 120  # 2 minutes
        self.min_wallets = 2
        self.signals: List[SmartMoneySignal] = []
        self._load_signals()

    def scan(self, hours: int = 1) -> List[SmartMoneySignal]:
        """Scan recent activity for smart money consensus."""
        if not self.tracker:
            return []

        # Get recent activity
        activity = self.tracker.get_recent_activity(hours=hours)
        if not activity:
            return []

        # Get wallet scores
        wallet_scores = {}
        if self.scorer:
            if not self.scorer.scores:
                self.scorer.score_all_wallets()
            wallet_scores = {addr: s.score for addr, s in self.scorer.scores.items()}

        # Filter to high-quality wallets
        quality_buys = []
        quality_sells = []

        for event in activity:
            wallet = event.get("wallet", "")
            score = wallet_scores.get(wallet, 0)

            if score < self.min_wallet_score:
                continue

            enriched = dict(event)
            enriched["wallet_score"] = score

            if event.get("direction") == "buy":
                quality_buys.append(enriched)
            elif event.get("direction") == "sell":
                quality_sells.append(enriched)

        # Group buys by token
        buys_by_token = defaultdict(list)
        for buy in quality_buys:
            token = buy.get("token_address", "")
            if token:
                buys_by_token[token].append(buy)

        # Group sells by token
        sells_by_token = defaultdict(list)
        for sell in quality_sells:
            token = sell.get("token_address", "")
            if token:
                sells_by_token[token].append(sell)

        # Detect consensus
        new_signals = []
        for token, buys in buys_by_token.items():
            signal = self._detect_consensus(token, buys, sells_by_token.get(token, []))
            if signal:
                new_signals.append(signal)
                self.signals.append(signal)

        if new_signals:
            self._append_signals(new_signals)

        return new_signals

    def _detect_consensus(
        self, token_address: str, buys: List[dict], sells: List[dict]
    ) -> Optional[SmartMoneySignal]:
        """Detect if multiple wallets buying the same token form consensus."""

        # Deduplicate wallets (keep most recent buy per wallet)
        wallet_latest_buy = {}
        for buy in buys:
            wallet = buy.get("wallet", "")
            if wallet not in wallet_latest_buy or buy.get("block_time", 0) > wallet_latest_buy[wallet].get("block_time", 0):
                wallet_latest_buy[wallet] = buy

        buying_wallets = list(wallet_latest_buy.values())

        if len(buying_wallets) < self.min_wallets:
            return None

        # Check time window
        buy_times = [b.get("block_time", 0) for b in buying_wallets]
        buy_times = [t for t in buy_times if t > 0]
        if not buy_times:
            return None

        time_span = max(buy_times) - min(buy_times)
        if time_span > self.time_window_seconds:
            # Too spread out, not consensus
            return None

        # Aggregate volumes
        buy_sol = sum(b.get("amount_sol", 0) for b in buying_wallets)

        # Deduplicate sell wallets
        wallet_latest_sell = {}
        for sell in sells:
            wallet = sell.get("wallet", "")
            if wallet not in wallet_latest_sell or sell.get("block_time", 0) > wallet_latest_sell[wallet].get("block_time", 0):
                wallet_latest_sell[wallet] = sell
        sell_wallets = list(wallet_latest_sell.values())
        sell_sol = sum(s.get("amount_sol", 0) for s in sell_wallets)

        # Score quality
        scores = [b.get("wallet_score", 0) for b in buying_wallets]
        avg_score = sum(scores) / len(scores)
        weighted_quality = sum(s * (b.get("amount_sol", 0) / max(buy_sol, 0.001)) for s, b in zip(scores, buying_wallets))

        # Confidence: based on wallet count, quality, and volume
        count_factor = min(1.0, len(buying_wallets) / 5)
        quality_factor = avg_score / 100
        volume_factor = min(1.0, buy_sol / 10)
        confidence = (count_factor * 0.4 + quality_factor * 0.3 + volume_factor * 0.3)

        # Need at least moderate confidence
        if confidence < 0.3:
            return None

        first_buy = min(buy_times)
        last_buy = max(buy_times)

        return SmartMoneySignal(
            token_address=token_address,
            token_symbol="",  # Resolved later if needed
            wallets_buying=len(buying_wallets),
            wallets_selling=len(sell_wallets),
            aggregate_buy_sol=round(buy_sol, 4),
            aggregate_sell_sol=round(sell_sol, 4),
            avg_wallet_score=round(avg_score, 1),
            weighted_quality=round(weighted_quality, 2),
            confidence=round(confidence, 3),
            time_window_seconds=int(time_span),
            first_buy_time=datetime.fromtimestamp(first_buy, tz=timezone.utc).isoformat() if first_buy else "",
            last_buy_time=datetime.fromtimestamp(last_buy, tz=timezone.utc).isoformat() if last_buy else "",
            buying_wallets=[
                {"wallet": b.get("wallet", "")[:12] + "...", "score": b.get("wallet_score", 0), "sol": b.get("amount_sol", 0)}
                for b in buying_wallets
            ],
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ── Queries ────────────────────────────────────────────────

    def get_recent_signals(self, hours: int = 24, min_confidence: float = 0.3) -> List[dict]:
        """Get recent smart money signals."""
        cutoff = time.time() - (hours * 3600)
        return [
            s.to_dict() for s in self.signals
            if s.confidence >= min_confidence
        ]

    def get_token_smart_money(self, token_address: str) -> List[dict]:
        """Get smart money signals for a specific token."""
        return [
            s.to_dict() for s in self.signals
            if s.token_address == token_address
        ]

    def get_active_consensus(self) -> List[dict]:
        """Get signals from the last hour (still actionable)."""
        return self.get_recent_signals(hours=1, min_confidence=0.4)

    # ── Storage ────────────────────────────────────────────────

    def _load_signals(self):
        """Load historical signals."""
        if SMART_MONEY_LOG.exists():
            try:
                for line in open(SMART_MONEY_LOG):
                    try:
                        d = json.loads(line)
                        self.signals.append(SmartMoneySignal.from_dict(d))
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass

    def _append_signals(self, signals: List[SmartMoneySignal]):
        """Append new signals to log."""
        SMART_MONEY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SMART_MONEY_LOG, "a") as f:
            for sig in signals:
                f.write(json.dumps(sig.to_dict(), default=str) + "\n")

    # ── Stats ──────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return detector statistics."""
        recent = self.get_recent_signals(hours=24)
        return {
            "total_signals": len(self.signals),
            "signals_24h": len(recent),
            "avg_confidence": round(
                sum(s.confidence for s in self.signals[-50:]) / min(50, len(self.signals)), 3
            ) if self.signals else 0,
            "unique_tokens": len(set(s.token_address for s in recent)),
        }


def create_smart_money_detector(tracker=None, scorer=None) -> SmartMoneyDetector:
    """Create a smart money detector."""
    return SmartMoneyDetector(tracker=tracker, scorer=scorer)
