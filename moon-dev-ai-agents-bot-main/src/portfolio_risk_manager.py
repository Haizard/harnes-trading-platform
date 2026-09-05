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
from pathlib import Path
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

        # Capital auto-reset mechanism
        # When a max-loss circuit breaker trips, capital can be automatically
        # restored to the initial amount so the engine keeps trading. Every
        # reset is counted and persisted so the dashboard can display it.
        self.auto_reset_enabled = os.environ.get("AUTO_RESET_CAPITAL", "true").lower() == "true"
        self.auto_reset_cooldown_hours = float(os.environ.get("AUTO_RESET_COOLDOWN_HOURS", "24"))
        self._reset_state_path = Path("src/data/risk/capital_resets.json")
        self._reset_state = self._load_reset_state()
        
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
            "reset_count": self._reset_state.get("reset_count", 0),
            "last_reset": self._reset_state.get("last_reset"),
            "auto_reset_enabled": self.auto_reset_enabled,
            "auto_reset_cooldown_hours": self.auto_reset_cooldown_hours,
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
        """Activate the circuit breaker to block new trades (idempotent)."""
        if self.circuit_breaker_active:
            # Already latched - don't reset the trip time or re-print/spam.
            self.circuit_breaker_reason = reason
            return
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

    # ── Capital Auto-Reset ─────────────────────────────────────

    def _load_reset_state(self) -> dict:
        """Load persisted reset counter/history (survives restarts)."""
        try:
            if self._reset_state_path.exists():
                data = json.loads(self._reset_state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("reset_count", 0)
                    data.setdefault("history", [])
                    data.setdefault("last_reset", None)
                    return data
        except Exception as e:
            cprint(f"[RISK] Failed to load reset state: {e}", "red")
        return {"reset_count": 0, "history": [], "last_reset": None}

    def _save_reset_state(self):
        """Persist reset counter/history to disk (and best-effort to DB)."""
        self._reset_state["auto_reset_enabled"] = self.auto_reset_enabled
        self._reset_state["cooldown_hours"] = self.auto_reset_cooldown_hours
        try:
            self._reset_state_path.parent.mkdir(parents=True, exist_ok=True)
            self._reset_state_path.write_text(
                json.dumps(self._reset_state, indent=2), encoding="utf-8"
            )
        except Exception as e:
            cprint(f"[RISK] Failed to persist reset state: {e}", "red")
        try:
            from src.db_storage import log_event
            log_event("risk/capital_reset_state", self.get_reset_stats())
        except Exception:
            pass

    def get_reset_stats(self) -> dict:
        """Reset stats for the dashboard / API consumers."""
        history = self._reset_state.get("history", [])
        return {
            "reset_count": self._reset_state.get("reset_count", 0),
            "last_reset": self._reset_state.get("last_reset"),
            "auto_reset_enabled": self.auto_reset_enabled,
            "cooldown_hours": self.auto_reset_cooldown_hours,
            "recent": history[-5:],
        }

    def maybe_auto_reset(self, reset_capital_fn=None) -> bool:
        """
        Auto-reset capital after a max-loss circuit breaker trip.

        Called by the engine right after the breaker latches. If auto-reset
        is enabled and the cooldown has elapsed, capital is restored via
        `reset_capital_fn` (PaperTrader.reset_capital), the reset counter is
        incremented and persisted, the breaker is cleared, and a
        'capital_reset' risk event is emitted.

        Returns True if a reset was performed.
        """
        if not self.auto_reset_enabled:
            return False

        # Cooldown guard — prevents reset loops if losses recur immediately
        last = self._reset_state.get("last_reset")
        if last:
            try:
                last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                hours_since = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
                if hours_since < self.auto_reset_cooldown_hours:
                    cprint(
                        f"[RISK] Auto-reset cooldown active "
                        f"({hours_since:.1f}h < {self.auto_reset_cooldown_hours}h) — capital NOT reset",
                        "yellow",
                    )
                    return False
            except Exception:
                pass

        capital_before = self.current_capital
        result = None
        if reset_capital_fn is not None:
            try:
                result = reset_capital_fn(reason="auto_reset_max_loss")
            except Exception as e:
                cprint(f"[RISK] Auto-reset failed: {e}", "red")
                return False
        else:
            cprint("[RISK] Auto-reset: no reset_capital_fn provided — skipping reset", "yellow")
            return False

        capital_after = result.get("capital_after", self.initial_capital)

        # Increment and persist the reset counter
        self._reset_state["reset_count"] = self._reset_state.get("reset_count", 0) + 1
        self._reset_state["last_reset"] = datetime.now(timezone.utc).isoformat()
        entry = {
            "timestamp": self._reset_state["last_reset"],
            "reset_number": self._reset_state["reset_count"],
            "reason": self.circuit_breaker_reason or "max_loss",
            "capital_before": round(capital_before, 2),
            "capital_after": round(capital_after, 2),
            "positions_cleared": result.get("positions_cleared", 0),
        }
        history = self._reset_state.setdefault("history", [])
        history.append(entry)
        if len(history) > 100:
            self._reset_state["history"] = history[-100:]
        self._save_reset_state()

        # Sync local capital + daily P&L and clear the breaker so trading resumes
        self.current_capital = capital_after
        self._daily_pnl = 0.0
        self.deactivate_circuit_breaker()

        self._emit_event(RiskEvent(
            event_type="capital_reset",
            severity="warning",
            message=(
                f"Capital auto-reset #{entry['reset_number']}: "
                f"${entry['capital_before']:.2f} -> ${entry['capital_after']:.2f} "
                f"(cleared {entry['positions_cleared']} position(s))"
            ),
            portfolio_value=capital_after,
            threshold=self.max_loss_usd,
            current_pnl=0.0,
        ))
        cprint(
            f"[RISK] Capital AUTO-RESET #{entry['reset_number']} complete — "
            f"trading resumed with ${capital_after:.2f}",
            "white", "on_green",
        )
        return True


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
