"""
Moon Dev Agent Orchestrator — The Brain That Wires Everything Together

Instead of isolated agents, this module coordinates:
  TokenScanner → RugPullDetector → ConsensusEngine → RiskAgent → SessionLog → FeedbackLoop

Every decision flows through the team. No agent works in isolation.
"""

import json
import time
import asyncio, concurrent.futures
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

# DSH modules — the team
from src.event_bus import EventBus, Events, DispatchMode
from src.execution_tracker import ExecutionTracker
from src.data_gatherer import DataGatherer
from src.session_log import SessionLog, EventType
from src.feedback_loop import TradeFeedbackLoop

# Bedrock LLM — the AI brain
try:
    from src.bedrock_llm import bedrock_chat, ChatMessage, ChatOptions, is_bedrock_configured
    BEDROCK_AVAILABLE = True
except ImportError:
    BEDROCK_AVAILABLE = False

# Consensus Engine — multi-model analysis
try:
    from src.agents.consensus_engine import ConsensusEngine
    CONSENSUS_AVAILABLE = True
except ImportError:
    CONSENSUS_AVAILABLE = False

# Risk Guard — pre-trade validation
try:
    from src.risk_guard import RiskGuard, TradeProposal
    RISK_GUARD_AVAILABLE = True
except ImportError:
    RISK_GUARD_AVAILABLE = False

# MCP — external service connectivity (optional)
try:
    from src.mcp_registry import MCPRegistry, create_default_mcp_registry
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


# ── Consensus Prompt for Micro-Cap Tokens ──────────────────────
MICRO_CAP_PROMPT = """You are a professional Solana memecoin trading analyst.

Analyze this micro-cap token and provide a structured trading signal.

TOKEN DATA:
{token_data}

MARKET CONTEXT:
- This is a Solana memecoin discovered by our scanner
- Trading via Jupiter DEX with ~$10-50 position sizes
- Focus on: liquidity depth, buy pressure, momentum, safety
- MCP data includes: security metrics, whale data, sentiment, portfolio state
- If mcp_security is present, use holder_count and top_10_holder_pct for rug risk
- If mcp_whale_data is present, use large_holders count for concentration risk
- If mcp_sentiment is present, factor social sentiment into decision
- If mcp_portfolio is present, consider current open positions and win rate
- If mcp_risk is present, factor in recent rejection patterns

Respond in EXACTLY this JSON format:
{{
    "direction": "LONG | NO_TRADE",
    "confidence": 0.0-1.0,
    "setup": "brief description of the setup",
    "entry_quality": 0.0-1.0,
    "risk_quality": 0.0-100.0,
    "reason_codes": ["code1", "code2"],
    "action": "CONSIDER_ENTRY | SKIP | WAIT",
    "reasoning": "1-2 sentence explanation"
}}

RULES:
- Be conservative — most memecoins are scams or rugs
- Only suggest LONG if multiple factors align positively
- Risk quality 0-100 where 100 = safest possible
- When uncertain, output NO_TRADE with action SKIP
"""


class AgentOrchestrator:
    """
    The Brain — coordinates all agents as a team.
    
    Flow:
      Candidate → Consensus AI → Risk Check → Decision → Log → Learn
    """

    def __init__(self, capital=25.0, mode="paper"):
        self.capital = capital
        self.mode = mode
        self.data_dir = Path("src/data/orchestrator")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Initialize team members
        self.session_log = SessionLog()
        self.data_gatherer = DataGatherer()
        # Wire EventBus to SessionLog (DSH pattern)
        self.event_bus = EventBus()
        self.execution_tracker = ExecutionTracker()
        self.feedback_loop = TradeFeedbackLoop()
        async def _log_event(payload):
            await self.session_log.log(payload.get("event_type", "unknown"), payload)
        for evt in [Events.SIGNAL_GENERATED, Events.ORDER_SUBMITTED, Events.ORDER_FILLED, Events.POSITION_OPENED, Events.POSITION_CLOSED]:
            self.event_bus.on(evt, _log_event, mode=DispatchMode.EMIT, tag="session_log")

        # Consensus engine (multi-model AI)
        self.consensus = None
        if CONSENSUS_AVAILABLE:
            try:
                self.consensus = ConsensusEngine()
                print("[ORCH] Consensus Engine connected")
            except Exception as e:
                print("[ORCH] Consensus Engine unavailable: " + str(e))# Risk guard
        self.risk_guard = None
        if RISK_GUARD_AVAILABLE:
            try:
                self.risk_guard = RiskGuard()
                print("[ORCH] Risk Guard connected")
            except Exception as e:
                print("[ORCH] Risk Guard unavailable: " + str(e))

        # MCP — internal trading data tools (read-only)
        self.mcp_registry = None
        if MCP_AVAILABLE:
            try:
                self.mcp_registry = create_default_mcp_registry()
                print("[ORCH] MCP Registry connected (" + str(len(self.mcp_registry.list_tool_names())) + " tools)")
            except Exception as e:
                print("[ORCH] MCP Registry unavailable: " + str(e))


        # Stats
        self._decisions = 0
        self._ai_approved = 0
        self._ai_rejected = 0
        self._algo_approved = 0

        print("[ORCH] Agent Orchestrator initialized — all agents wired")

    def analyze_candidate(self, candidate_dict: Dict) -> Dict:
        """
        The main decision pipeline. Takes a token candidate and returns a trade decision.
        
        Returns:
            {
                "action": "BUY" | "SKIP",
                "confidence": 0.0-1.0,
                "reason": "...",
                "ai_analysis": {...},
                "risk_check": {...},
                "source": "consensus" | "algorithmic"
            }
        """
        self._decisions += 1
        symbol = candidate_dict.get("symbol", "UNKNOWN")

        decision = {
            "symbol": symbol,
            "token_address": candidate_dict.get("address", ""),
            "action": "SKIP",
            "confidence": 0.0,
            "reason": "",
            "ai_analysis": None,
            "risk_check": None,
            "source": "algorithmic",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Step 1: AI Consensus Analysis (if available)
        ai_result = self._run_consensus(candidate_dict)
        if ai_result:
            decision["ai_analysis"] = ai_result
            decision["source"] = "consensus"

            if ai_result.get("action") == "CONSIDER_ENTRY":
                decision["action"] = "BUY"
                decision["confidence"] = ai_result.get("confidence", 0.5)
                decision["reason"] = ai_result.get("reasoning", "AI consensus approved")
                self._ai_approved += 1
            else:
                decision["action"] = "SKIP"
                decision["confidence"] = 1.0 - ai_result.get("confidence", 0.5)
                decision["reason"] = ai_result.get("reasoning", "AI consensus rejected")
                self._ai_rejected += 1
        else:
            # Step 2: Fallback to algorithmic scoring
            algo_score = candidate_dict.get("score", 0) / 100.0
            decision["confidence"] = algo_score
            if algo_score >= 0.6:
                decision["action"] = "BUY"
                decision["reason"] = "Algorithmic score " + str(int(algo_score * 100)) + "/100"
                self._algo_approved += 1
            else:
                decision["action"] = "SKIP"
                decision["reason"] = "Score too low: " + str(int(algo_score * 100)) + "/100"

        # Step 3: Log to session
        self._log_decision(decision, candidate_dict)

        # Step 4: Record signal for feedback loop
        self._record_signal(decision, candidate_dict)

        return decision

    def _run_consensus(self, candidate_dict: Dict) -> Optional[Dict]:
        """Run the consensus engine (multi-model AI) on a candidate."""
        if not BEDROCK_AVAILABLE:
            return None

        try:
            # Build market state for the AI
            market_state = self._build_market_state(candidate_dict)
            enriched = self.data_gatherer.gather_all(candidate_dict.get("address", ""), candidate_dict.get("symbol", ""))
            market_state["enriched_data"] = enriched
            prompt = MICRO_CAP_PROMPT.format(token_data=json.dumps(market_state, indent=2))

            # Call Bedrock directly (lighter than full ConsensusEngine)
            with concurrent.futures.ThreadPoolExecutor() as pool:
                response = pool.submit(asyncio.run, bedrock_chat(
                [ChatMessage(role="user", content=prompt)],
                ChatOptions(
                    system_prompt="You are a Solana memecoin analyst. Respond only in JSON.",
                    max_tokens=500,
                    temperature=0.3,
                ),
                )).result()

            # Parse JSON response
            text = response.text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])

        except json.JSONDecodeError:
            pass
        except Exception as e:
            print("[ORCH] Consensus error for " + candidate_dict.get("symbol", "?") + ": " + str(e))

        return None

    def _build_market_state(self, candidate_dict: Dict) -> Dict:
        """Build a market state dict from candidate data for AI analysis.
        
        Enriches with MCP data: security metrics, whale data, portfolio context,
        sentiment, and market context when MCP is available.
        """
        address = candidate_dict.get("address", "")
        symbol = candidate_dict.get("symbol", "UNKNOWN")

        state = {
            "symbol": symbol,
            "token_address": address,
            "price_usd": candidate_dict.get("price_usd", 0),
            "volume_24h": candidate_dict.get("volume_24h", 0),
            "volume_1h": candidate_dict.get("volume_1h", 0),
            "liquidity_usd": candidate_dict.get("liquidity_usd", 0),
            "market_cap": candidate_dict.get("market_cap", 0),
            "price_change_1h": candidate_dict.get("price_change_1h", 0),
            "price_change_24h": candidate_dict.get("price_change_24h", 0),
            "buy_sell_ratio_1h": str(candidate_dict.get("txns_1h_buys", 0)) + "/" + str(candidate_dict.get("txns_1h_sells", 0)),
            "dex": candidate_dict.get("dex", "unknown"),
            "pair_age_hours": candidate_dict.get("pair_age_hours", 0),
            "scanner_score": candidate_dict.get("score", 0),
            "scanner_signals": candidate_dict.get("signals", []),
        }

        # Enrich with MCP tools when available
        if self.mcp_registry and address:
            try:
                import concurrent.futures
                # Fetch security + whale data in parallel via MCP
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    sec_future = pool.submit(
                        asyncio.run,
                        self.mcp_registry.call_tool("get_token_security", {"token_address": address})
                    )
                    whale_future = pool.submit(
                        asyncio.run,
                        self.mcp_registry.call_tool("get_whale_data", {"token_address": address})
                    )
                    sent_future = pool.submit(
                        asyncio.run,
                        self.mcp_registry.call_tool("get_token_sentiment", {"symbol": symbol})
                    )

                    sec_result = sec_future.result()
                    whale_result = whale_future.result()
                    sent_result = sent_future.result()

                if sec_result and sec_result.success and sec_result.data:
                    state["mcp_security"] = sec_result.data
                if whale_result and whale_result.success and whale_result.data:
                    state["mcp_whale_data"] = whale_result.data
                if sent_result and sent_result.success and sent_result.data:
                    state["mcp_sentiment"] = sent_result.data

                # Add portfolio context (existing positions)
                port_result = asyncio.run(
                    self.mcp_registry.call_tool("get_portfolio_state", {})
                )
                if port_result and port_result.success and port_result.data:
                    state["mcp_portfolio"] = {
                        "open_positions": port_result.data.get("open_count", 0),
                        "win_rate": port_result.data.get("win_rate", 0),
                        "total_pnl": port_result.data.get("total_pnl", 0),
                    }

                # Add risk state
                risk_result = asyncio.run(
                    self.mcp_registry.call_tool("get_risk_state", {})
                )
                if risk_result and risk_result.success and risk_result.data:
                    state["mcp_risk"] = {
                        "rejections_today": risk_result.data.get("rejections_today", 0),
                    }

            except Exception as e:
                print("[ORCH] MCP enrichment error: " + str(e))

        return state

    def _log_decision(self, decision, candidate_dict):
        log_path = self.data_dir / "orchestrator_events.jsonl"
        event = {
            "type": "orchestrator/decision",
            "data": decision,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(event, default=str) + chr(10))

    def _record_signal(self, decision, candidate_dict):
        try:
            import uuid
            factors = {
                "scanner_score": candidate_dict.get("score", 0),
                "volume_24h": candidate_dict.get("volume_24h", 0),
                "liquidity_usd": candidate_dict.get("liquidity_usd", 0),
                "price_change_1h": candidate_dict.get("price_change_1h", 0),
            }
            if decision.get("ai_analysis"):
                factors["ai_confidence"] = decision["ai_analysis"].get("confidence", 0)
                factors["ai_entry_quality"] = decision["ai_analysis"].get("entry_quality", 0)
            record = {
                "signal_id": str(uuid.uuid4())[:8],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": decision["symbol"],
                "signal": decision["action"],
                "confidence": decision["confidence"],
                "factors": factors,
            }
            self.feedback_loop._append_jsonl(self.feedback_loop.signals_path, record)
        except Exception:
            pass

    def record_trade_outcome(self, symbol, pnl_usd, pnl_pct, holding_minutes):
        try:
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "holding_minutes": holding_minutes,
            }
            self.feedback_loop._append_jsonl(self.feedback_loop.outcomes_path, record)
        except Exception:
            pass

    def get_stats(self):
        mcp_tools = 0
        mcp_calls = 0
        if self.mcp_registry:
            mcp_tools = len(self.mcp_registry.list_tool_names())
            mcp_calls = len(self.mcp_registry.get_call_history())
        return {
            "total_decisions": self._decisions,
            "ai_approved": self._ai_approved,
            "ai_rejected": self._ai_rejected,
            "algo_approved": self._algo_approved,
            "bedrock_configured": BEDROCK_AVAILABLE and is_bedrock_configured() if BEDROCK_AVAILABLE else False,
            "consensus_available": CONSENSUS_AVAILABLE,
            "risk_guard_available": RISK_GUARD_AVAILABLE,
            "mcp_available": MCP_AVAILABLE,
            "mcp_tools": mcp_tools,
            "mcp_calls": mcp_calls,
        }
