"""
Moon Dev LLM Exit Decider — AI-Driven Exit Decisions
Ported from System 1 (TradingAgent.handle_exits) to System 2 (MicroEngine).

Instead of only using fixed SL/TP, this module asks the LLM whether to
hold or exit each position based on:
- Current P&L
- Technical indicators
- Strategy signals
- Market conditions
"""

import os
import json
from datetime import datetime, timezone
from typing import Dict, Optional, List
from termcolor import cprint


# ── Exit Decision Prompt ──────────────────────────────────────
EXIT_PROMPT = """You are Moon Dev's AI Trading Assistant analyzing whether to EXIT a position.

Current Position:
- Token: {symbol}
- Entry Price: ${entry_price:.8f}
- Current Price: ${current_price:.8f}
- Amount Invested: ${amount_usd:.2f}
- Current P&L: {pnl_pct:+.1f}% (${pnl_usd:+.2f})
- Time Held: {hours_held:.1f} hours
- Stop Loss: {stop_loss_pct}%
- Take Profit: {take_profit_pct}%

Technical Indicators:
{indicators}

Strategy Signals:
{strategy_signals}

Market Context:
{market_context}

Decide: Should we EXIT (sell) or HOLD this position?

Consider:
1. Is the trend still favorable or has it reversed?
2. Are technical indicators showing weakness?
3. Has the position been held too long without progress?
4. Is there better opportunity elsewhere?
5. Risk/reward from this point forward?

Respond in this exact JSON format:
{{"action": "EXIT" or "HOLD", "confidence": 0.0-1.0, "reason": "brief explanation"}}
"""


class LLMExitDecider:
    """
    AI-driven exit decisions for open positions.
    DSH Pattern: EventBus events + PostgreSQL persistence.
    
    Instead of only fixed SL/TP, this asks the LLM to analyze each position
    and decide whether to exit or hold based on market conditions.
    """

    def __init__(self, event_bus=None):
        self._available = False
        self._decisions: List[dict] = []
        self.event_bus = event_bus  # DSH EventBus
        
        # Check if Bedrock is available
        try:
            from src.bedrock_llm import is_bedrock_configured
            self._available = is_bedrock_configured()
        except Exception:
            pass
        
        status = "AVAILABLE" if self._available else "UNAVAILABLE (using fixed SL/TP only)"
        cprint(f"[EXIT] LLM Exit Decider initialized — {status}", "white", "on_blue")

    def should_exit(self, position_data: dict, indicators: Dict = None,
                    strategy_signals: Dict = None) -> dict:
        """
        Decide whether to exit a position using AI.
        
        Args:
            position_data: Dict with symbol, entry_price, current_price, amount_usd, etc.
            indicators: Dict from IndicatorEngine.calculate()
            strategy_signals: Dict from StrategyBridge.to_dict()
        
        Returns:
            dict with action (EXIT/HOLD), confidence, reason
        """
        if not self._available:
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": "LLM not available — using fixed SL/TP",
                "source": "fallback",
            }
        
        # DSH: Log decision to DB
        def _log_decision(decision):
            try:
                from src.db_storage import log_event
                log_event("llm_exit/decision", {
                    "symbol": position_data.get("symbol", ""),
                    **decision,
                })
            except Exception:
                pass
            
            # DSH: Emit to EventBus
            if self.event_bus:
                try:
                    import asyncio
                    loop = asyncio.get_event_loop()
                    payload = {"symbol": position_data.get("symbol", ""), **decision}
                    if loop.is_running():
                        asyncio.ensure_future(self.event_bus.emit("llm_exit/decision", payload))
                    else:
                        loop.run_until_complete(self.event_bus.emit("llm_exit/decision", payload))
                except Exception:
                    pass

        try:
            from src.bedrock_llm import bedrock_chat, ChatMessage, ChatOptions

            # Build context
            indicators_str = json.dumps(indicators or {}, indent=2, default=str)
            strategy_str = json.dumps(strategy_signals or {}, indent=2, default=str)
            
            pnl_usd = position_data.get("pnl_usd", 0)
            pnl_pct = position_data.get("pnl_pct", 0)
            
            prompt = EXIT_PROMPT.format(
                symbol=position_data.get("symbol", "UNKNOWN"),
                entry_price=position_data.get("entry_price", 0),
                current_price=position_data.get("current_price", 0),
                amount_usd=position_data.get("amount_usd", 0),
                pnl_pct=pnl_pct,
                pnl_usd=pnl_usd,
                hours_held=position_data.get("hours_held", 0),
                stop_loss_pct=position_data.get("stop_loss_pct", 10),
                take_profit_pct=position_data.get("take_profit_pct", 30),
                indicators=indicators_str,
                strategy_signals=strategy_str,
                market_context=f"Current time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            )

            import asyncio
            response_obj = asyncio.run(bedrock_chat(
                [ChatMessage(role="user", content=prompt)],
                ChatOptions(
                    system_prompt="You are Moon Dev's AI Trading Assistant. Respond only in JSON.",
                    max_tokens=200,
                    temperature=0.2,
                ),
            ))

            # Parse JSON response
            text = response_obj.text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                decision = json.loads(text[start:end])
                decision["source"] = "llm"
                self._decisions.append(decision)
                _log_decision(decision)
                return decision

        except Exception as e:
            cprint(f"[EXIT] LLM error for {position_data.get('symbol', '?')}: {e}", "yellow")

        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reason": "LLM decision failed — defaulting to HOLD",
            "source": "fallback",
        }

    def get_stats(self) -> dict:
        """Get decision statistics."""
        exits = [d for d in self._decisions if d.get("action") == "EXIT"]
        holds = [d for d in self._decisions if d.get("action") == "HOLD"]
        return {
            "total_decisions": len(self._decisions),
            "exits": len(exits),
            "holds": len(holds),
            "available": self._available,
        }


# ── Singleton ──────────────────────────────────────────────
_exit_decider_instance = None

def get_llm_exit_decider(event_bus=None) -> LLMExitDecider:
    """Get or create the singleton LLMExitDecider instance."""
    global _exit_decider_instance
    if _exit_decider_instance is None:
        _exit_decider_instance = LLMExitDecider(event_bus=event_bus)
    return _exit_decider_instance
