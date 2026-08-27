"""
💰 Moon Dev's Funding Cost Accounting
Stop hidden perpetual funding losses from eating into profits.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime


@dataclass
class FundingRecord:
    symbol: str
    rate: float
    cost_usd: float
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class FundingCostTracker:
    """Tracks perpetual funding costs across positions."""

    def __init__(self):
        self._records: List[FundingRecord] = []
        self._rates: Dict[str, float] = {}

    def update_rate(self, symbol: str, rate: float):
        self._rates[symbol] = rate

    def record_cost(self, symbol: str, position_usd: float, rate: float = None):
        r = rate or self._rates.get(symbol, 0)
        cost = position_usd * r / 100  # rate is in %
        self._records.append(FundingRecord(symbol=symbol, rate=r, cost_usd=cost))

    def get_total_cost(self, days: int = 30) -> float:
        return sum(r.cost_usd for r in self._records[-days*24:])

    def get_cost_by_symbol(self) -> Dict[str, float]:
        costs = {}
        for r in self._records:
            costs[r.symbol] = costs.get(r.symbol, 0) + r.cost_usd
        return costs

    def get_annualized_cost(self, position_usd: float) -> float:
        if not self._rates or position_usd <= 0:
            return 0
        avg_rate = sum(self._rates.values()) / len(self._rates)
        return position_usd * avg_rate / 100 * 365 * 3  # 3 funding intervals per day

    def get_report(self, days: int = 30) -> dict:
        return {
            'total_cost_usd': round(self.get_total_cost(days), 2),
            'by_symbol': {k: round(v, 2) for k, v in self.get_cost_by_symbol().items()},
            'current_rates': dict(self._rates),
            'record_count': len(self._records),
        }
