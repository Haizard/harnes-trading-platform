"""
🔗 Moon Dev's Correlation Management
True diversification — detect hidden concentration risk.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class CorrelationResult:
    token_a: str
    token_b: str
    correlation: float
    strength: str

    def to_dict(self):
        return {'token_a': self.token_a, 'token_b': self.token_b,
                'correlation': round(self.correlation, 3), 'strength': self.strength}


class CorrelationManager:
    """Analyzes correlation between tokens for diversification."""

    def __init__(self, high_threshold: float = 0.7, low_threshold: float = 0.3):
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold
        self._price_history: Dict[str, List[float]] = {}

    def update_price(self, token: str, price: float):
        if token not in self._price_history:
            self._price_history[token] = []
        self._price_history[token].append(price)
        if len(self._price_history[token]) > 500:
            self._price_history[token] = self._price_history[token][-500:]

    def calculate_correlation(self, token_a: str, token_b: str) -> Optional[CorrelationResult]:
        prices_a = self._price_history.get(token_a, [])
        prices_b = self._price_history.get(token_b, [])
        if len(prices_a) < 10 or len(prices_b) < 10:
            return None

        min_len = min(len(prices_a), len(prices_b))
        a = np.array(prices_a[-min_len:])
        b = np.array(prices_b[-min_len:])

        returns_a = np.diff(a) / a[:-1]
        returns_b = np.diff(b) / b[:-1]

        if np.std(returns_a) == 0 or np.std(returns_b) == 0:
            corr = 0.0
        else:
            corr = float(np.corrcoef(returns_a, returns_b)[0, 1])

        if abs(corr) >= self.high_threshold:
            strength = "high"
        elif abs(corr) <= self.low_threshold:
            strength = "low"
        else:
            strength = "moderate"

        return CorrelationResult(token_a, token_b, corr, strength)

    def get_all_correlations(self) -> List[CorrelationResult]:
        tokens = list(self._price_history.keys())
        results = []
        for i in range(len(tokens)):
            for j in range(i+1, len(tokens)):
                r = self.calculate_correlation(tokens[i], tokens[j])
                if r: results.append(r)
        return results

    def get_portfolio_risk(self) -> dict:
        corrs = self.get_all_correlations()
        high_corr = [c for c in corrs if c.strength == "high"]
        avg_corr = sum(c.correlation for c in corrs) / len(corrs) if corrs else 0

        if avg_corr > 0.7:
            risk = "high"
        elif avg_corr > 0.4:
            risk = "moderate"
        else:
            risk = "low"

        return {
            'avg_correlation': round(avg_corr, 3),
            'risk_level': risk,
            'high_correlation_pairs': len(high_corr),
            'total_pairs': len(corrs),
            'recommendation': 'Consider uncorrelated assets' if risk == 'high' else 'Good diversification',
        }
