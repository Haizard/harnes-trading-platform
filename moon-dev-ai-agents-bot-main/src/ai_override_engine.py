"""
Moon Dev AI Override Engine — AI Decisions During Risk Events
Ported from System 1 (RiskAgent.should_override_limit) to System 2 (MicroEngine).

When a risk event occurs (max loss, min balance), this module asks the LLM
whether to:
1. Close all positions immediately (RESPECT_LIMIT)
2. Hold positions despite the breach (OVERRIDE)

This prevents panic-selling during temporary drawdowns while still
protecting against catastrophic losses.
"""

import os
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
from termcolor import cprint


# ── Override Prompt ──────────────────────────────────────
OVERRIDE_PROMPT = """You are Moon Dev's Risk Management AI analyzing a risk limit breach.

Risk Event: {limit_type}
Current Portfolio Value: ${current_value:.2f}
Initial Capital: ${initial_capital:.2f}
Total P&L: ${total_pnl:.2f} ({pnl_pct:+.1f}%)

Current Positions:
{positions_data}

Should we:
1. OVERRIDE — Keep positions open (we believe they will recover)
2. RESPECT_LIMIT — Close all positions to protect capital

Consider:
1. Market conditions — is this a temporary dip or a crash?
2. Position quality — are these strong projects or risky memes?
3. Time horizon — how long can we wait for recovery?
4. Risk of further losses vs potential recovery

For max loss overrides:
- Be EXTREMELY conservative
- Only override if strong reversal signals
- Require 70%+ confidence

For max gain overrides:
- Can be more lenient — let winners run
- Look for continued momentum
- Require 60%+ confidence

Respond in this exact JSON format:
{{"decision": "OVERRIDE" or "RESPECT_LIMIT", "confidence": 0.0-1.0, "reason": "brief explanation"}}
"""


class AIOverrideEngine:
    """
    AI override decisions during risk events.
    DSH Pattern: EventBus events + PostgreSQL persistence.
    
    When PortfolioRiskManager detects a risk event, this module asks the LLM
    whether to close all positions or hold through the drawdown.
    """

    def __init__(self, event_bus=None):
        self._available = False
        self._last_check: Optional[datetime] = None
        self._check_interval = timedelta(minutes=15)  # Don't spam LLM
        self._decisions: list = []
        self.event_bus = event_bus  # DSH EventBus
        
        # Check if Bedrock is available
        try:
            from src.bedrock_llm import is_bedrock_configured
            self._available = is_bedrock_configured()
        except Exception:
            pass
        
        status = "AVAILABLE" if self._available else "UNAVAILABLE (auto-close on breach)"
        cprint(f"[OVERRIDE] AI Override Engine initialized — {status}", "white", "on_blue")

    def should_override(self, risk_event_type: str, portfolio_stats: dict,
                        open_positions: Dict = None) -> dict:
        """
        Decide whether to override a risk limit.
        
        Args:
            risk_event_type: "max_loss", "max_gain", "min_balance"
            portfolio_stats: Dict from PortfolioRiskManager.get_portfolio_stats()
            open_positions: Dict of open positions {address: position_data}
        
        Returns:
            dict with decision (OVERRIDE/RESPECT_LIMIT), confidence, reason
        """
        # Rate limit checks
        now = datetime.now(timezone.utc)
        if self._last_check and (now - self._last_check) < self._check_interval:
            return {
                "decision": "RESPECT_LIMIT",
                "confidence": 0.5,
                "reason": "Rate limited — defaulting to respect limit",
                "source": "rate_limit",
            }
        
        self._last_check = now

        if not self._available:
            return {
                "decision": "RESPECT_LIMIT",
                "confidence": 1.0,
                "reason": "LLM not available — closing all positions",
                "source": "fallback",
            }

        try:
            from src.bedrock_llm import bedrock_chat_sync, ChatMessage, ChatOptions

            # Build positions text
            positions_text = "No open positions"
            if open_positions:
                pos_lines = []
                for addr, pos in open_positions.items():
                    pos_lines.append(
                        f"- {pos.get('symbol', addr[:8])}: "
                        f"${pos.get('amount_usd', 0):.2f} invested, "
                        f"P&L: {pos.get('pnl_pct', 0):+.1f}%"
                    )
                positions_text = "\n".join(pos_lines)

            prompt = OVERRIDE_PROMPT.format(
                limit_type=risk_event_type.upper(),
                current_value=portfolio_stats.get("current_capital", 0),
                initial_capital=portfolio_stats.get("initial_capital", 0),
                total_pnl=portfolio_stats.get("total_pnl", 0),
                pnl_pct=portfolio_stats.get("total_pnl_pct", 0),
                positions_data=positions_text,
            )

            response_obj = bedrock_chat_sync(
                [ChatMessage(role="user", content=prompt)],
                ChatOptions(
                    system_prompt="You are Moon Dev's Risk Management AI. Respond only in JSON.",
                    max_tokens=200,
                    temperature=0.2,
                ),
            )

            # Parse JSON response
            text = response_obj.text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                decision = json.loads(text[start:end])
                decision["source"] = "llm"
                self._decisions.append(decision)
                
                # DSH: Log to DB
                try:
                    from src.db_storage import log_event
                    log_event("override/decision", {
                        "risk_event_type": risk_event_type,
                        **decision,
                    })
                except Exception:
                    pass
                
                # DSH: Emit to EventBus
                if self.event_bus:
                    try:
                        import asyncio
                        payload = {"risk_event_type": risk_event_type, **decision}
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.ensure_future(self.event_bus.emit("override/decision", payload))
                        else:
                            loop.run_until_complete(self.event_bus.emit("override/decision", payload))
                    except Exception:
                        pass
                
                if decision.get("decision") == "OVERRIDE":
                    cprint(
                        f"[OVERRIDE] AI OVERRIDES {risk_event_type}: "
                        f"{decision.get('reason', 'no reason')} "
                        f"(conf={decision.get('confidence', 0):.0%})",
                        "yellow"
                    )
                else:
                    cprint(
                        f"[OVERRIDE] AI RESPECTS {risk_event_type}: "
                        f"{decision.get('reason', 'no reason')}",
                        "red"
                    )
                
                return decision

        except Exception as e:
            cprint(f"[OVERRIDE] LLM error: {e}", "yellow")

        return {
            "decision": "RESPECT_LIMIT",
            "confidence": 0.5,
            "reason": "LLM decision failed — defaulting to respect limit",
            "source": "fallback",
        }

    def get_stats(self) -> dict:
        """Get decision statistics."""
        overrides = [d for d in self._decisions if d.get("decision") == "OVERRIDE"]
        respects = [d for d in self._decisions if d.get("decision") == "RESPECT_LIMIT"]
        return {
            "total_decisions": len(self._decisions),
            "overrides": len(overrides),
            "respects": len(respects),
            "available": self._available,
        }


# ── Singleton ──────────────────────────────────────────────
_override_instance = None

def get_ai_override_engine(event_bus=None) -> AIOverrideEngine:
    """Get or create the singleton AIOverrideEngine instance."""
    global _override_instance
    if _override_instance is None:
        _override_instance = AIOverrideEngine(event_bus=event_bus)
    return _override_instance
