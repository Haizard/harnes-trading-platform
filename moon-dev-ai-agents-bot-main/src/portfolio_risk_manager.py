"""
Moon Dev Portfolio Risk Manager
System 1 feature ported to System 2 (MicroEngine).

Monitors portfolio-level risk:
- Max loss/gain limits (USD and percentage)
- Minimum balance protection
- Circuit breaker (stops new trades when limits hit)
- Close-all-positions capability
- AI override decisions during risk events
"""

import os
import json
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable
from termcolor import cprint
from src.event_bus import _fire_and_forget


@dataclass
class RiskEvent:
    """A risk event that was detected."""
    event_type: str  # "max_loss", "max_gain", "min_balance", "circuit_breaker"
    severity: str    # "warning", "critical"
    message: str
    portfolio_value: float
    threshold: float
    current_pnl: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "severity": self.severity,
            "message": self.message,
            "portfolio_value": round(self.portfolio_value, 2),
            "threshold": self.threshold,
            "current_pnl": round(self.current_pnl, 2),
            "timestamp": self.timestamp,
        }


class PortfolioRiskManager:
    """
    Portfolio-level risk management for MicroEngine.
    DSH Pattern: EventBus events + PostgreSQL persistence.
    
    Monitors total portfolio value and enforces circuit breakers:
    - Max loss: stops new trades if total loss exceeds threshold
    - Max gain: stops new trades if total gain exceeds threshold (take profit)
    - Min balance: closes all positions if balance drops too low
    - Circuit breaker: prevents new trades during risk events
    """

    def __init__(self, initial_capital: float = 100.0, mode: str = "paper", event_bus=None):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.mode = mode
        self.event_bus = event_bus  # DSH EventBus for inter-module communication
        
        # Risk limits (from config.py defaults)
        self.max_loss_usd = float(os.environ.get("MAX_LOSS_USD", "25"))
        self.max_gain_usd = float(os.environ.get("MAX_GAIN_USD", "25"))
        self.max_loss_pct = float(os.environ.get("MAX_LOSS_PERCENT", "5"))
        self.max_gain_pct = float(os.environ.get("MAX_GAIN_PERCENT", "5"))
        self.min_balance_usd = float(os.environ.get("MINIMUM_BALANCE_USD", "50"))
        self.use_percentage = os.environ.get("USE_PERCENTAGE", "false").lower() == "true"
        
        # Circuit breaker state
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.circuit_breaker_since: Optional[datetime] = None
        self.override_active = False
        
        # Event log
        self.events: List[RiskEvent] = []
        self._listeners: List[Callable] = []
        
        # Track daily P&L
        self._daily_pnl = 0.0
        self._daily_reset_date = datetime.now(timezone.utc).date()
        
        cprint(
            f"[RISK] Portfolio Risk Manager initialized — "
            f"Capital: ${initial_capital:.2f} | Max Loss: ${self.max_loss_usd} | "
            f"Max Gain: ${self.max_gain_usd} | Min Balance: ${self.min_balance_usd}",
            "white", "on_blue"
        )

    def update_capital(self, new_capital: float):
        """Update current capital from paper trader."""
        self.current_capital = new_capital
        
        # Reset daily P&L at midnight UTC
        today = datetime.now(timezone.utc).date()
        if today != self._daily_reset_date:
            self._daily_pnl = 0.0
            self._daily_reset_date = today

    def record_trade_pnl(self, pnl_usd: float):
        """Record P&L from a closed trade."""
        self._daily_pnl += pnl_usd

    def get_portfolio_stats(self) -> dict:
        """Get current portfolio risk stats."""
        total_pnl = self.current_capital - self.initial_capital
        pnl_pct = (total_pnl / self.initial_capital * 100) if self.initial_capital > 0 else 0
        
        return {
            "initial_capital": round(self.initial_capital, 2),
            "current_capital": round(self.current_capital, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(pnl_pct, 2),
            "daily_pnl": round(self._daily_pnl, 2),
            "circuit_breaker_active": self.circuit_breaker_active,
            "circuit_breaker_reason": self.circuit_breaker_reason,
            "override_active": self.override_active,
            "max_loss_usd": self.max_loss_usd,
            "max_gain_usd": self.max_gain_usd,
            "min_balance_usd": self.min_balance_usd,
        }

    def check_risk(self) -> Optional[RiskEvent]:
        """
        Check all portfolio risk conditions.
        Returns a RiskEvent if any limit is breached, None if all OK.
        """
        total_pnl = self.current_capital - self.initial_capital
        pnl_pct = (total_pnl / self.initial_capital * 100) if self.initial_capital > 0 else 0

        # 1. Check minimum balance
        if self.current_capital < self.min_balance_usd:
            event = RiskEvent(
                event_type="min_balance",
                severity="critical",
                message=f"Balance ${self.current_capital:.2f} below minimum ${self.min_balance_usd:.2f}",
                portfolio_value=self.current_capital,
                threshold=self.min_balance_usd,
                current_pnl=total_pnl,
            )
            self._emit_event(event)
            return event

        # 2. Check max loss
        if self.use_percentage:
            if pnl_pct <= -self.max_loss_pct:
                event = RiskEvent(
                    event_type="max_loss",
                    severity="critical",
                    message=f"Loss {pnl_pct:.1f}% exceeds limit {self.max_loss_pct}%",
                    portfolio_value=self.current_capital,
                    threshold=self.max_loss_pct,
                    current_pnl=total_pnl,
                )
                self._emit_event(event)
                return event
        else:
            if total_pnl <= -self.max_loss_usd:
                event = RiskEvent(
                    event_type="max_loss",
                    severity="critical",
                    message=f"Loss ${abs(total_pnl):.2f} exceeds limit ${self.max_loss_usd:.2f}",
                    portfolio_value=self.current_capital,
                    threshold=self.max_loss_usd,
                    current_pnl=total_pnl,
                )
                self._emit_event(event)
                return event

        # 3. Check max gain
        if self.use_percentage:
            if pnl_pct >= self.max_gain_pct:
                event = RiskEvent(
                    event_type="max_gain",
                    severity="warning",
                    message=f"Gain {pnl_pct:.1f}% exceeds target {self.max_gain_pct}%",
                    portfolio_value=self.current_capital,
                    threshold=self.max_gain_pct,
                    current_pnl=total_pnl,
                )
                self._emit_event(event)
                return event
        else:
            if total_pnl >= self.max_gain_usd:
                event = RiskEvent(
                    event_type="max_gain",
                    severity="warning",
                    message=f"Gain ${total_pnl:.2f} exceeds target ${self.max_gain_usd:.2f}",
                    portfolio_value=self.current_capital,
                    threshold=self.max_gain_usd,
                    current_pnl=total_pnl,
                )
                self._emit_event(event)
                return event

        # All clear — deactivate circuit breaker if it was active
        if self.circuit_breaker_active and not self.override_active:
            self.circuit_breaker_active = False
            self.circuit_breaker_reason = ""
            cprint("[RISK] Circuit breaker DEACTIVATED — all limits OK", "green")

        return None

    def activate_circuit_breaker(self, reason: str):
        """Activate the circuit breaker to block new trades."""
        self.circuit_breaker_active = True
        self.circuit_breaker_reason = reason
        self.circuit_breaker_since = datetime.now(timezone.utc)
        cprint(f"[RISK] Circuit breaker ACTIVATED: {reason}", "white", "on_red")

    def deactivate_circuit_breaker(self):
        """Deactivate the circuit breaker."""
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.circuit_breaker_since = None
        cprint("[RISK] Circuit breaker DEACTIVATED", "green")

    def is_trading_allowed(self) -> bool:
        """Check if new trades are allowed."""
        if self.circuit_breaker_active and not self.override_active:
            return False
        return True

    def set_override(self, active: bool):
        """Set AI override (allows trading during circuit breaker)."""
        self.override_active = active
        if active:
            cprint("[RISK] AI Override ACTIVATED — trading allowed during circuit breaker", "yellow")
        else:
            cprint("[RISK] AI Override DEACTIVATED", "yellow")

    def _emit_event(self, event: RiskEvent):
        """Emit a risk event to listeners + DB + EventBus (DSH pattern)."""
        self.events.append(event)
        
        # Keep only last 100 events
        if len(self.events) > 100:
            self.events = self.events[-100:]
        
        # Print
        if event.severity == "critical":
            cprint(f"[RISK] CRITICAL: {event.message}", "white", "on_red")
        else:
            cprint(f"[RISK] WARNING: {event.message}", "yellow")
        
        # DSH: Save to PostgreSQL
        try:
            from src.db_storage import log_event
            log_event("risk/event", event.to_dict())
        except Exception:
            pass
        
        # DSH: Emit to EventBus
        if self.event_bus:
            try:
                import asyncio
                from src.event_bus import Events
                payload = event.to_dict()
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    _fire_and_forget(self.event_bus.emit("risk/event", payload))
                else:
                    loop.run_until_complete(self.event_bus.emit("risk/event", payload))
            except Exception:
                pass
        
        # Notify listeners
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass

    def on_risk_event(self, callback: Callable):
        """Register a listener for risk events."""
        self._listeners.append(callback)

    def get_event_history(self, hours: int = 24) -> List[RiskEvent]:
        """Get risk events from the last N hours."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return [
            e for e in self.events
            if datetime.fromisoformat(e.timestamp.replace("Z", "+00:00")) > cutoff
        ]


# ── Singleton ──────────────────────────────────────────────
_risk_manager_instance = None

def get_portfolio_risk_manager(initial_capital: float = 100.0, mode: str = "paper", event_bus=None) -> PortfolioRiskManager:
    """Get or create the singleton PortfolioRiskManager instance."""
    global _risk_manager_instance
    if _risk_manager_instance is None:
        _risk_manager_instance = PortfolioRiskManager(initial_capital=initial_capital, mode=mode, event_bus=event_bus)
    return _risk_manager_instance
