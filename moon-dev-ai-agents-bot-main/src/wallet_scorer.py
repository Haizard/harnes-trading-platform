"""
📊 Moon Dev's Wallet Scorer — Smart Money Quality Assessment

Scores wallets based on trading performance metrics:
  - Realized PnL over time
  - Win rate and average ROI
  - Maximum drawdown
  - Profit consistency (not just one lucky trade)
  - Average holding time
  - Liquidity quality of traded tokens
  - Trade frequency and survival

Architecture:
  WalletActivity (from WalletTracker)
       │
       ▼
  WalletScorer → WalletScore (0-100)
       │
       ▼
  SmartMoneyDetector (uses scores for consensus)

The score determines how "smart" a wallet is. Only high-scoring
wallets contribute to smart money consensus signals.

Security: READ-ONLY. Never executes trades.
"""

import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from collections import defaultdict


# ── Constants ─────────────────────────────────────────────────

# Use path relative to this file, not CWD (works in Docker)
_MODULE_DIR = Path(__file__).resolve().parent
DATA_DIR = _MODULE_DIR.parent / "data"
WALLET_SCORES_PATH = DATA_DIR / "wallet_tracker" / "wallet_scores.json"
WALLET_ACTIVITY_LOG = DATA_DIR / "wallet_tracker" / "wallet_activity.jsonl"

# Scoring weights
WEIGHTS = {
    "win_rate": 0.20,
    "roi": 0.15,
    "consistency": 0.20,
    "drawdown": 0.15,
    "trade_count": 0.10,
    "holding_time": 0.10,
    "volume": 0.10,
}

# Minimum trades needed for a meaningful score
MIN_TRADES_FOR_SCORE = 5


@dataclass
class WalletScore:
    """A scored assessment of a wallet's trading quality."""
    wallet: str
    label: str
    score: float                    # 0-100 overall score
    win_rate: float                 # 0-100
    avg_roi_pct: float              # average return per trade
    max_drawdown_pct: float         # worst peak-to-trough
    trade_count: int                # total trades
    avg_holding_minutes: float      # average hold time
    consistency_score: float        # 0-100
    total_pnl_usd: float            # cumulative PnL
    total_sol_volume: float         # total SOL volume
    profit_factor: float            # gross_profit / gross_loss
    sharpe_like: float              # mean_return / std_return
    last_trade_time: str            # ISO timestamp
    grade: str                      # S/A/B/C/D/F
    confidence: float               # how confident in this score (0-1)
    data_points: int                # number of trades used

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict):
        valid_fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid_fields})


class WalletScorer:
    """
    Scores wallets based on their trading activity.

    Reads swap events from wallet_activity.jsonl and computes:
    - Win rate (% of profitable trades)
    - ROI per trade
    - Max drawdown
    - Consistency (std dev of returns)
    - Holding time patterns
    - Volume profile

    Usage:
        scorer = WalletScorer()
        score = scorer.score_wallet("7xK...abc")
        all_scores = scorer.score_all_wallets()
    """

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or DATA_DIR
        self.activity_log = self.data_dir / "wallet_tracker" / "wallet_activity.jsonl"
        self.scores_path = self.data_dir / "wallet_tracker" / "wallet_scores.json"
        self.scores: Dict[str, WalletScore] = {}
        self._load_scores()

    # ── Scoring ────────────────────────────────────────────────

    def score_wallet(self, wallet_address: str, activity: List[dict] = None) -> Optional[WalletScore]:
        """
        Score a single wallet based on its activity.

        Returns WalletScore if enough data, None otherwise.
        """
        if activity is None:
            activity = self._get_wallet_activity(wallet_address)

        if len(activity) < MIN_TRADES_FOR_SCORE:
            return None

        # Group trades by token to compute per-token PnL
        token_trades = defaultdict(list)
        for event in activity:
            token = event.get("token_address", "")
            if token:
                token_trades[token].append(event)

        # Compute trade outcomes (pair buys and sells)
        outcomes = self._compute_trade_outcomes(token_trades)

        if not outcomes:
            return None

        # ── Win Rate ────────────────────────────────────────
        wins = len([o for o in outcomes if o["pnl_sol"] > 0])
        win_rate = (wins / len(outcomes)) * 100 if outcomes else 0

        # ── ROI ─────────────────────────────────────────────
        rois = [o.get("roi_pct", 0) for o in outcomes]
        avg_roi = sum(rois) / len(rois) if rois else 0

        # ── PnL ─────────────────────────────────────────────
        total_pnl = sum(o.get("pnl_sol", 0) for o in outcomes)
        gross_profit = sum(o["pnl_sol"] for o in outcomes if o["pnl_sol"] > 0)
        gross_loss = abs(sum(o["pnl_sol"] for o in outcomes if o["pnl_sol"] < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 10.0

        # ── Max Drawdown ────────────────────────────────────
        cumulative = 0
        peak = 0
        max_dd = 0
        for o in outcomes:
            cumulative += o.get("pnl_sol", 0)
            peak = max(peak, cumulative)
            dd = (peak - cumulative) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        # ── Holding Time ────────────────────────────────────
        hold_times = [o.get("holding_minutes", 0) for o in outcomes if o.get("holding_minutes", 0) > 0]
        avg_holding = sum(hold_times) / len(hold_times) if hold_times else 0

        # ── Consistency (std dev of returns) ────────────────
        if len(rois) >= 2:
            mean_roi = sum(rois) / len(rois)
            variance = sum((r - mean_roi) ** 2 for r in rois) / len(rois)
            std_roi = variance ** 0.5
            consistency = max(0, 100 - (std_roi * 2))  # Lower std = higher consistency
        else:
            consistency = 50

        # ── Sharpe-like ─────────────────────────────────────
        if len(rois) >= 2:
            mean_roi = sum(rois) / len(rois)
            variance = sum((r - mean_roi) ** 2 for r in rois) / len(rois)
            std_roi = variance ** 0.5
            sharpe = mean_roi / std_roi if std_roi > 0 else 0
        else:
            sharpe = 0

        # ── Volume ──────────────────────────────────────────
        total_sol_vol = sum(e.get("amount_sol", 0) for e in activity)

        # ── Composite Score ─────────────────────────────────
        win_score = min(100, win_rate)
        roi_score = min(100, max(0, 50 + avg_roi * 2))
        consistency_score = min(100, max(0, consistency))
        dd_score = max(0, 100 - max_dd)  # Lower drawdown = better
        trade_count_score = min(100, len(outcomes) * 10)
        holding_score = min(100, 50 + (20 - avg_holding) * 2) if avg_holding < 60 else 40
        volume_score = min(100, total_sol_vol * 10)

        # Confidence based on data quantity
        confidence = min(1.0, len(outcomes) / 20)

        composite = (
            win_score * WEIGHTS["win_rate"] +
            roi_score * WEIGHTS["roi"] +
            consistency_score * WEIGHTS["consistency"] +
            dd_score * WEIGHTS["drawdown"] +
            trade_count_score * WEIGHTS["trade_count"] +
            holding_score * WEIGHTS["holding_time"] +
            volume_score * WEIGHTS["volume"]
        )

        # Apply confidence penalty for low data
        composite *= (0.5 + 0.5 * confidence)

        composite = max(0, min(100, composite))

        # ── Grade ───────────────────────────────────────────
        if composite >= 90:
            grade = "S"
        elif composite >= 80:
            grade = "A"
        elif composite >= 65:
            grade = "B"
        elif composite >= 50:
            grade = "C"
        elif composite >= 35:
            grade = "D"
        else:
            grade = "F"

        # Last trade time
        last_time = max((e.get("block_time", 0) for e in activity), default=0)
        last_trade_dt = datetime.fromtimestamp(last_time, tz=timezone.utc).isoformat() if last_time else ""

        # Resolve label from config
        label = self._get_wallet_label(wallet_address)

        score = WalletScore(
            wallet=wallet_address,
            label=label,
            score=round(composite, 1),
            win_rate=round(win_rate, 1),
            avg_roi_pct=round(avg_roi, 2),
            max_drawdown_pct=round(max_dd, 1),
            trade_count=len(outcomes),
            avg_holding_minutes=round(avg_holding, 1),
            consistency_score=round(consistency, 1),
            total_pnl_usd=round(total_pnl * 150, 2),  # Rough SOL->USD
            total_sol_volume=round(total_sol_vol, 4),
            profit_factor=round(profit_factor, 2),
            sharpe_like=round(sharpe, 2),
            last_trade_time=last_trade_dt,
            grade=grade,
            confidence=round(confidence, 2),
            data_points=len(outcomes),
        )

        self.scores[wallet_address] = score
        return score

    def score_all_wallets(self) -> Dict[str, WalletScore]:
        """Score all wallets that have activity."""
        activity_by_wallet = self._group_activity_by_wallet()
        for wallet, activity in activity_by_wallet.items():
            if len(activity) >= MIN_TRADES_FOR_SCORE:
                self.score_wallet(wallet, activity)
        self._save_scores()
        return self.scores

    def get_score(self, wallet_address: str) -> Optional[WalletScore]:
        """Get cached score for a wallet, or compute it."""
        if wallet_address in self.scores:
            return self.scores[wallet_address]
        return self.score_wallet(wallet_address)

    def get_top_wallets(self, limit: int = 10) -> List[WalletScore]:
        """Get top-scoring wallets."""
        if not self.scores:
            self.score_all_wallets()
        sorted_scores = sorted(self.scores.values(), key=lambda s: s.score, reverse=True)
        return sorted_scores[:limit]

    def get_smart_wallets(self, min_score: float = 60) -> List[WalletScore]:
        """Get wallets that qualify as 'smart money'."""
        if not self.scores:
            self.score_all_wallets()
        return [s for s in self.scores.values() if s.score >= min_score]

    # ── Trade Outcome Computation ──────────────────────────────

    def _compute_trade_outcomes(self, token_trades: Dict[str, List[dict]]) -> List[dict]:
        """
        Compute trade outcomes by pairing buys and sells for each token.

        Uses FIFO matching: first buy matches first sell.
        """
        outcomes = []

        for token, trades in token_trades.items():
            # Sort by time
            trades.sort(key=lambda t: t.get("block_time", 0))

            # FIFO matching
            buy_queue = []
            for trade in trades:
                direction = trade.get("direction", "")
                amount_sol = trade.get("amount_sol", 0)
                block_time = trade.get("block_time", 0)
                price = trade.get("price_usd", 0)

                if direction == "buy":
                    buy_queue.append({
                        "amount_sol": amount_sol,
                        "block_time": block_time,
                        "price": price,
                    })
                elif direction == "sell" and buy_queue:
                    buy = buy_queue.pop(0)
                    pnl_sol = amount_sol - buy["amount_sol"]
                    hold_minutes = (block_time - buy["block_time"]) / 60 if buy["block_time"] else 0
                    roi_pct = ((amount_sol / buy["amount_sol"]) - 1) * 100 if buy["amount_sol"] > 0 else 0

                    outcomes.append({
                        "token": token,
                        "buy_sol": buy["amount_sol"],
                        "sell_sol": amount_sol,
                        "pnl_sol": pnl_sol,
                        "roi_pct": roi_pct,
                        "holding_minutes": hold_minutes,
                        "entry_price": buy["price"],
                        "exit_price": price,
                        "buy_time": buy["block_time"],
                        "sell_time": block_time,
                    })

        # Sort by sell time
        outcomes.sort(key=lambda o: o.get("sell_time", 0))
        return outcomes

    # ── Data Loading ───────────────────────────────────────────

    def _get_wallet_activity(self, wallet_address: str) -> List[dict]:
        """Get all activity for a wallet from the activity log."""
        if not self.activity_log.exists():
            return []

        events = []
        try:
            for line in open(self.activity_log):
                try:
                    e = json.loads(line)
                    if e.get("wallet", "") == wallet_address:
                        events.append(e)
                except json.JSONDecodeError:
                    continue
        except FileNotFoundError:
            pass

        return events

    def _group_activity_by_wallet(self) -> Dict[str, List[dict]]:
        """Group all activity by wallet address."""
        wallet_activity = defaultdict(list)
        if not self.activity_log.exists():
            return dict(wallet_activity)

        try:
            for line in open(self.activity_log):
                try:
                    e = json.loads(line)
                    wallet = e.get("wallet", "")
                    if wallet:
                        wallet_activity[wallet].append(e)
                except json.JSONDecodeError:
                    continue
        except FileNotFoundError:
            pass

        return dict(wallet_activity)

    def _get_wallet_label(self, wallet_address: str) -> str:
        """Get wallet label from config."""
        wallets_config = self.data_dir / "tracked_wallets.json"
        if wallets_config.exists():
            try:
                wallets = json.loads(wallets_config.read_text())
                if isinstance(wallets, list):
                    for w in wallets:
                        if w.get("address") == wallet_address:
                            return w.get("label", wallet_address[:8])
            except Exception:
                pass
        return wallet_address[:8]

    # ── Storage ────────────────────────────────────────────────

    def _load_scores(self):
        """Load cached scores."""
        if self.scores_path.exists():
            try:
                data = json.loads(self.scores_path.read_text())
                for addr, score_data in data.items():
                    self.scores[addr] = WalletScore.from_dict(score_data)
            except Exception:
                pass

    def _save_scores(self):
        """Persist scores."""
        self.scores_path.parent.mkdir(parents=True, exist_ok=True)
        data = {addr: s.to_dict() for addr, s in self.scores.items()}
        self.scores_path.write_text(json.dumps(data, indent=2, default=str))

    # ── Stats ──────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return scorer statistics."""
        smart = self.get_smart_wallets()
        return {
            "total_scored": len(self.scores),
            "smart_wallets": len(smart),
            "avg_score": round(sum(s.score for s in self.scores.values()) / len(self.scores), 1) if self.scores else 0,
            "top_grade": self.scores[max(self.scores, key=lambda k: self.scores[k].score)].grade if self.scores else "N/A",
        }


# ── Factory ───────────────────────────────────────────────────

def create_wallet_scorer() -> WalletScorer:
    """Create a wallet scorer."""
    return WalletScorer()
