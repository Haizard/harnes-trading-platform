"""
🎯 Moon Dev's Portfolio Goal System
DSH Pattern: Goal System — persistent session objectives.

Sets portfolio-level goals that influence every trade decision:
- Target return per period
- Maximum drawdown tolerance
- Cash reserve targets
- Diversification requirements

Usage:
    goals = PortfolioGoalManager()
    goals.set_goal('monthly_return', 0.10)  # 10% monthly target
    goals.set_goal('max_drawdown', 0.10)     # 10% max drawdown

    # Check if trade aligns with goals
    alignment = goals.check_alignment(trade, portfolio_state)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum
from datetime import datetime


class GoalType(str, Enum):
    """Types of portfolio goals."""
    RETURN_TARGET = "return_target"       # Target return per period
    DRAWDOWN_LIMIT = "drawdown_limit"     # Maximum drawdown tolerance
    CASH_RESERVE = "cash_reserve"         # Minimum cash percentage
    DIVERSIFICATION = "diversification"   # Max concentration per token
    POSITION_COUNT = "position_count"     # Target number of positions
    RISK_ADJUSTED = "risk_adjusted"       # Minimum Sharpe ratio


@dataclass
class PortfolioGoal:
    """A single portfolio goal."""
    name: str
    goal_type: GoalType
    target_value: float
    current_value: float = 0.0
    weight: float = 1.0  # Importance (0.0 to 1.0)
    enabled: bool = True
    description: str = ""

    @property
    def progress(self) -> float:
        """How close we are to the goal (0.0 to 1.0+)."""
        if self.target_value == 0:
            return 1.0
        if self.goal_type in (GoalType.DRAWDOWN_LIMIT, GoalType.CASH_RESERVE):
            # Lower is better for these
            return min(self.current_value / self.target_value, 2.0)
        return min(self.current_value / self.target_value, 2.0)

    @property
    def achieved(self) -> bool:
        """Has the goal been achieved?"""
        if self.goal_type == GoalType.DRAWDOWN_LIMIT:
            return self.current_value <= self.target_value
        if self.goal_type == GoalType.CASH_RESERVE:
            return self.current_value >= self.target_value
        return self.current_value >= self.target_value

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'type': self.goal_type.value,
            'target': self.target_value,
            'current': round(self.current_value, 4),
            'progress': f"{self.progress:.0%}",
            'achieved': self.achieved,
            'weight': self.weight,
            'enabled': self.enabled,
        }


@dataclass
class TradeAlignment:
    """How a proposed trade aligns with portfolio goals."""
    overall_score: float  # 0.0 to 1.0 (1.0 = perfect alignment)
    goal_scores: Dict[str, float] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    approved: bool = True

    def to_dict(self) -> dict:
        return {
            'overall_score': round(self.overall_score, 3),
            'goal_scores': {k: round(v, 3) for k, v in self.goal_scores.items()},
            'recommendations': self.recommendations,
            'approved': self.approved,
        }


class PortfolioGoalManager:
    """
    Manages portfolio-level goals that influence trading decisions.

    Every trade is checked against goals before execution.
    """

    def __init__(self):
        self._goals: Dict[str, PortfolioGoal] = {}
        self._history: List[dict] = []

    def set_goal(self, name: str, goal_type: GoalType, target: float,
                weight: float = 1.0, description: str = ""):
        """Set or update a portfolio goal."""
        self._goals[name] = PortfolioGoal(
            name=name,
            goal_type=goal_type,
            target_value=target,
            weight=weight,
            description=description,
        )

    def update_progress(self, name: str, current_value: float):
        """Update current progress toward a goal."""
        if name in self._goals:
            self._goals[name].current_value = current_value

    def get_goal(self, name: str) -> Optional[PortfolioGoal]:
        return self._goals.get(name)

    def get_all_goals(self) -> List[dict]:
        return [g.to_dict() for g in self._goals.values()]

    def get_achieved_goals(self) -> List[str]:
        return [name for name, g in self._goals.items() if g.achieved]

    def check_alignment(self, trade: dict, portfolio_state: dict) -> TradeAlignment:
        """
        Check how a proposed trade aligns with portfolio goals.

        Args:
            trade: {'token': str, 'side': str, 'amount_usd': float, 'confidence': float}
            portfolio_state: {'value': float, 'exposure_pct': float, 'positions': int, ...}

        Returns:
            TradeAlignment with score and recommendations
        """
        scores = {}
        recommendations = []

        for name, goal in self._goals.items():
            if not goal.enabled:
                continue

            score = 1.0  # Default: aligned

            if goal.goal_type == GoalType.RETURN_TARGET:
                # Check if trade helps reach return target
                expected_return = trade.get('confidence', 0.5) * 0.05
                score = min(expected_return / max(goal.target_value, 0.01), 1.0)
                if score < 0.3:
                    recommendations.append(
                        f"Trade return potential ({expected_return:.1%}) low vs goal ({goal.target_value:.1%})"
                    )

            elif goal.goal_type == GoalType.DRAWDOWN_LIMIT:
                # Check if trade increases drawdown risk
                current_dd = portfolio_state.get('drawdown_pct', 0)
                if current_dd > goal.target_value * 0.8:
                    score = 0.2
                    recommendations.append(
                        f"Approaching drawdown limit ({current_dd:.1%} vs {goal.target_value:.1%})"
                    )

            elif goal.goal_type == GoalType.CASH_RESERVE:
                # Check if trade reduces cash below minimum
                cash_pct = portfolio_state.get('cash_pct', 100)
                trade_impact = trade.get('amount_usd', 0) / max(portfolio_state.get('value', 1), 1)
                new_cash = cash_pct / 100 - trade_impact
                if new_cash < goal.target_value:
                    score = new_cash / goal.target_value
                    recommendations.append(
                        f"Trade would reduce cash to {new_cash:.1%} (min: {goal.target_value:.1%})"
                    )

            elif goal.goal_type == GoalType.DIVERSIFICATION:
                # Check if trade increases concentration too much
                token = trade.get('token', '')
                current_token_pct = portfolio_state.get('token_pcts', {}).get(token, 0)
                trade_pct = trade.get('amount_usd', 0) / max(portfolio_state.get('value', 1), 1)
                new_pct = current_token_pct + trade_pct
                if new_pct > goal.target_value:
                    score = goal.target_value / new_pct
                    recommendations.append(
                        f"Would concentrate {new_pct:.1%} in one token (max: {goal.target_value:.1%})"
                    )

            elif goal.goal_type == GoalType.POSITION_COUNT:
                # Check if adding another position is appropriate
                current_count = portfolio_state.get('positions', 0)
                if trade.get('side') == 'buy' and current_count >= goal.target_value:
                    score = 0.3
                    recommendations.append(
                        f"Already at {current_count} positions (target: {goal.target_value})"
                    )

            elif goal.goal_type == GoalType.RISK_ADJUSTED:
                # Check if trade improves risk-adjusted returns
                score = 0.7  # Default neutral

            scores[name] = score * goal.weight

        # Overall score (weighted average)
        if scores:
            total_weight = sum(g.weight for g in self._goals.values() if g.enabled)
            overall = sum(scores.values()) / max(total_weight, 0.01)
        else:
            overall = 1.0

        approved = overall >= 0.3  # Minimum threshold

        return TradeAlignment(
            overall_score=max(0, min(1, overall)),
            goal_scores=scores,
            recommendations=recommendations,
            approved=approved,
        )

    def get_status_summary(self) -> dict:
        """Get a summary of all goal statuses."""
        achieved = self.get_achieved_goals()
        total = len(self._goals)
        enabled = sum(1 for g in self._goals.values() if g.enabled)

        return {
            'total_goals': total,
            'enabled': enabled,
            'achieved': len(achieved),
            'achieved_names': achieved,
            'goals': self.get_all_goals(),
        }


# ── Factory ───────────────────────────────────────────────────

def create_default_goals() -> PortfolioGoalManager:
    """Create a PortfolioGoalManager with sensible defaults."""
    manager = PortfolioGoalManager()

    manager.set_goal(
        'monthly_return', GoalType.RETURN_TARGET, 0.10,
        weight=1.0, description='10% monthly return target',
    )
    manager.set_goal(
        'max_drawdown', GoalType.DRAWDOWN_LIMIT, 0.10,
        weight=1.0, description='Maximum 10% drawdown',
    )
    manager.set_goal(
        'cash_reserve', GoalType.CASH_RESERVE, 0.20,
        weight=0.8, description='Keep at least 20% in cash',
    )
    manager.set_goal(
        'max_concentration', GoalType.DIVERSIFICATION, 0.30,
        weight=0.6, description='No more than 30% in one token',
    )
    manager.set_goal(
        'target_positions', GoalType.POSITION_COUNT, 5,
        weight=0.4, description='Target 5 active positions',
    )

    return manager
