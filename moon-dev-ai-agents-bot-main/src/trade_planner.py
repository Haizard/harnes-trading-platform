"""
📋 Moon Dev's Multi-Step Trade Planner
DSH Pattern: Plan Mode — structured step-by-step planning before execution.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum
import time


class PlanStep(str, Enum):
    ANALYZE = "analyze"
    VALIDATE = "validate"
    SIZE = "size"
    ENTRY = "entry"
    MONITOR = "monitor"
    EXIT = "exit"
    RECORD = "record"


class PlanStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TradePlan:
    token: str
    side: str
    status: PlanStatus = PlanStatus.DRAFT
    current_step: PlanStep = PlanStep.ANALYZE
    entry_price: float = 0.0
    exit_price: float = 0.0
    position_size_usd: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    holding_minutes: float = 0.0
    pnl_usd: float = 0.0
    steps_completed: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self):
        return {
            'token': self.token, 'side': self.side, 'status': self.status.value,
            'current_step': self.current_step.value, 'entry_price': self.entry_price,
            'position_size_usd': self.position_size_usd, 'stop_loss': self.stop_loss,
            'take_profit': self.take_profit, 'pnl_usd': self.pnl_usd,
            'steps_completed': self.steps_completed, 'notes': self.notes,
        }


class TradePlanner:
    """Structured trade lifecycle management."""

    def __init__(self):
        self._plans: Dict[str, TradePlan] = {}
        self._history: List[TradePlan] = []

    def create_plan(self, token: str, side: str, entry_price: float = 0,
                   size_usd: float = 0, stop_loss: float = 0,
                   take_profit: float = 0) -> TradePlan:
        plan = TradePlan(token=token, side=side, entry_price=entry_price,
                        position_size_usd=size_usd, stop_loss=stop_loss,
                        take_profit=take_profit)
        self._plans[f"{token}_{side}_{int(time.time())}"] = plan
        return plan

    def advance(self, plan: TradePlan, note: str = "") -> TradePlan:
        steps = list(PlanStep)
        idx = steps.index(plan.current_step)
        plan.steps_completed.append(plan.current_step.value)
        if note: plan.notes.append(note)
        if idx + 1 < len(steps):
            plan.current_step = steps[idx + 1]
            plan.status = PlanStatus.ACTIVE
        else:
            plan.status = PlanStatus.COMPLETED
            self._history.append(plan)
        return plan

    def cancel(self, plan: TradePlan, reason: str = ""):
        plan.status = PlanStatus.CANCELLED
        if reason: plan.notes.append(f"CANCELLED: {reason}")
        self._history.append(plan)

    def get_active_plans(self) -> List[TradePlan]:
        return [p for p in self._plans.values() if p.status == PlanStatus.ACTIVE]

    def get_history(self, limit: int = 20) -> List[dict]:
        return [p.to_dict() for p in self._history[-limit:]]
