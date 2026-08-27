"""
Moon Dev's Multi-Model Consensus Engine

Architecture from ChatGPT research:
  DeepSeek V3.2     -> Primary trading analyst
  Qwen3-Next-80B    -> Confirmation analyst
  Kimi K2.5         -> Independent/contrarian analyst

The AI outputs structured JSON:
  {
    "direction": "LONG",
    "confidence": 0.78,
    "market_regime": "TRENDING",
    "setup": "LIQUIDITY_SWEEP_RECLAIM",
    "entry_quality": 0.81,
    "risk_quality": 0.76,
    "expected_rr": 2.8,
    "invalidation": 109420,
    "reason_codes": ["positive_cvd", "bullish_delta"],
    "action": "CONSIDER_ENTRY"
  }

Python decides whether the trade is actually permitted.
"""

import json
import asyncio
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from termcolor import cprint

from src.bedrock_llm import (
    bedrock_chat,
    ChatMessage,
    ChatOptions,
    is_bedrock_configured,
)


# ── Structured Signal Format ─────────────────────────────────
SIGNAL_SCHEMA = {
    "direction": "LONG | SHORT | NO_TRADE",
    "confidence": "0.0 - 1.0",
    "market_regime": "TRENDING_UP | TRENDING_DOWN | RANGING | VOLATILE | LOW_VOL",
    "setup": "description of the setup",
    "entry_quality": "0.0 - 1.0",
    "risk_quality": "0.0 - 1.0",
    "expected_rr": "expected risk-reward ratio",
    "invalidation": "price level that invalidates the setup",
    "reason_codes": ["list", "of", "reason", "codes"],
    "action": "CONSIDER_ENTRY | SKIP | WAIT",
}


# ── Analyst Prompts ──────────────────────────────────────────
PRIMARY_ANALYST_PROMPT = """You are a professional crypto trading analyst.

Analyze the following market state and provide a structured trading signal.

MARKET STATE:
{market_state}

Respond in EXACTLY this JSON format (no other text):
{{
    "direction": "LONG | SHORT | NO_TRADE",
    "confidence": 0.0-1.0,
    "market_regime": "TRENDING_UP | TRENDING_DOWN | RANGING | VOLATILE | LOW_VOL",
    "setup": "brief setup description",
    "entry_quality": 0.0-1.0,
    "risk_quality": 0.0-1.0,
    "expected_rr": 2.0,
    "invalidation": price_level,
    "reason_codes": ["code1", "code2"],
    "action": "CONSIDER_ENTRY | SKIP | WAIT"
}}

RULES:
- Only suggest LONG if multiple bullish factors align
- Only suggest SHORT if multiple bearish factors align
- When uncertain, output NO_TRADE with action SKIP
- confidence reflects how many factors support the direction
- entry_quality reflects how clean the entry setup is
- risk_quality reflects how well risk is defined
- invalidation is the price level that would invalidate the setup"""


SECONDARY_ANALYST_PROMPT = """You are an independent confirmation analyst for crypto trading.

You receive a market state AND a primary analyst's signal.
Your job is to INDEPENDENTLY evaluate whether you agree.

PRIMARY ANALYST SIGNAL:
{primary_signal}

MARKET STATE:
{market_state}

Respond in EXACTLY this JSON format:
{{
    "agree": true | false,
    "confidence": 0.0-1.0,
    "direction": "LONG | SHORT | NO_TRADE",
    "reasoning": "brief explanation",
    "additional_factors": ["factor1", "factor2"],
    "risk_concern": "any risk the primary analyst missed"
}}"""


INDEPENDENT_ANALYST_PROMPT = """You are a contrarian/third-opinion analyst for crypto trading.
Your job is to look for reasons the OTHER analysts might be WRONG.

PRIMARY SIGNAL: {primary_signal}
SECONDARY SIGNAL: {secondary_signal}
MARKET STATE: {market_state}

Respond in EXACTLY this JSON format:
{{
    "contrarian_view": "what could go wrong with this trade",
    "agree_with_consensus": true | false,
    "confidence": 0.0-1.0,
    "additional_risk": "risk not captured by other analysts",
    "alternative_setup": "if you disagree, what would you do instead"
}}"""


# ── Data Classes ─────────────────────────────────────────────
@dataclass
class AnalystSignal:
    """A single analyst's signal"""
    model_name: str
    role: str
    raw_response: str
    parsed: Dict[str, Any]
    success: bool
    error: Optional[str] = None


@dataclass
class ConsensusResult:
    """Final consensus result from all analysts"""
    direction: str  # LONG, SHORT, NO_TRADE
    consensus_confidence: float
    primary_signal: Dict[str, Any]
    secondary_agreement: bool
    independent_agreement: bool
    consensus_met: bool  # Did enough models agree?
    signals: List[AnalystSignal] = field(default_factory=list)
    risk_gates: List[str] = field(default_factory=list)  # Why trade was rejected

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "consensus_confidence": self.consensus_confidence,
            "primary_signal": self.primary_signal,
            "secondary_agreement": self.secondary_agreement,
            "independent_agreement": self.independent_agreement,
            "consensus_met": self.consensus_met,
            "risk_gates": self.risk_gates,
        }


# ── Consensus Engine ─────────────────────────────────────────
class ConsensusEngine:
    """
    Multi-model consensus engine for trading signals.

    Uses:
      - DeepSeek V3.2 (primary)
      - Qwen3-Next-80B (confirmation)
      - Kimi K2.5 (contrarian)

    Consensus requires:
      - Primary: confidence > 0.7 and direction != NO_TRADE
      - Secondary: agrees with primary direction
      - Risk gates: all pass
    """

    def __init__(self):
        self._client = None

    def _get_client(self):
        """Lazy-load bedrock client"""
        if self._client is None:
            import boto3
            import os
            from botocore.config import Config
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=os.environ.get("AWS_BEDROCK_REGION", "us-east-1"),
                config=Config(
                    retries={"max_attempts": 3, "mode": "adaptive"},
                    connect_timeout=10,
                    read_timeout=60,
                ),
            )
        return self._client

    def _call_model(self, model_id: str, system_prompt: str, user_content: str, temperature: float = 0.3) -> str:
        """Call a specific Bedrock model directly"""
        import os
        import json as json_mod

        client = self._get_client()

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})

        body = json_mod.dumps({
            "messages": messages,
            "max_tokens": 4096,
            "temperature": temperature,
            "top_p": 0.85,
        })

        response = client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=body,
        )

        response_body = json_mod.loads(response["body"].read().decode("utf-8"))

        # Extract text
        if isinstance(response_body.get("choices"), list) and response_body["choices"]:
            msg = response_body["choices"][0].get("message", {})
            return msg.get("content", "")
        if isinstance(response_body.get("content"), list) and response_body["content"]:
            return response_body["content"][0].get("text", "")

        return json_mod.dumps(response_body)

    def _parse_json_response(self, text: str) -> Dict[str, Any]:
        """Extract JSON from model response"""
        import re

        # Try ```json ... ``` blocks first
        match = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try raw JSON
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return {"raw_text": text, "parse_error": True}

    async def get_consensus(self, market_state: str, risk_gates: Optional[List[str]] = None) -> ConsensusResult:
        """
        Get consensus signal from multiple models.

        Args:
            market_state: Formatted market data string
            risk_gates: List of risk gate descriptions to check
        """
        cprint("\n[CONSENSUS] Starting multi-model analysis...", "cyan")

        # ── Step 1: Primary Analyst (DeepSeek V3.2) ──────────
        cprint("[CONSENSUS] Querying primary analyst (DeepSeek V3.2)...", "cyan")
        try:
            primary_raw = self._call_model(
                "deepseek.v3.2",
                PRIMARY_ANALYST_PROMPT.format(market_state=market_state),
                "Analyze this market state and provide a trading signal.",
                temperature=0.3,
            )
            primary_parsed = self._parse_json_response(primary_raw)
            primary_signal = AnalystSignal(
                model_name="deepseek.v3.2",
                role="primary_analyst",
                raw_response=primary_raw,
                parsed=primary_parsed,
                success=not primary_parsed.get("parse_error", False),
            )
        except Exception as e:
            cprint(f"[CONSENSUS] Primary analyst failed: {e}", "red")
            primary_signal = AnalystSignal(
                model_name="deepseek.v3.2",
                role="primary_analyst",
                raw_response="",
                parsed={},
                success=False,
                error=str(e),
            )

        # ── Step 2: Secondary Analyst (Qwen3-Next) ───────────
        cprint("[CONSENSUS] Querying secondary analyst (Qwen3-Next)...", "cyan")
        try:
            secondary_raw = self._call_model(
                "qwen.qwen3-next-80b-a3b",
                SECONDARY_ANALYST_PROMPT.format(
                    primary_signal=json.dumps(primary_signal.parsed, indent=2),
                    market_state=market_state,
                ),
                "Provide your independent confirmation or disagreement.",
                temperature=0.3,
            )
            secondary_parsed = self._parse_json_response(secondary_raw)
            secondary_signal = AnalystSignal(
                model_name="qwen.qwen3-next-80b-a3b",
                role="secondary_analyst",
                raw_response=secondary_raw,
                parsed=secondary_parsed,
                success=not secondary_parsed.get("parse_error", False),
            )
        except Exception as e:
            cprint(f"[CONSENSUS] Secondary analyst failed: {e}", "red")
            secondary_signal = AnalystSignal(
                model_name="qwen.qwen3-next-80b-a3b",
                role="secondary_analyst",
                raw_response="",
                parsed={},
                success=False,
                error=str(e),
            )

        # ── Step 3: Independent Analyst (Kimi K2.5) ──────────
        cprint("[CONSENSUS] Querying independent analyst (Kimi K2.5)...", "cyan")
        try:
            independent_raw = self._call_model(
                "moonshotai.kimi-k2.5",
                INDEPENDENT_ANALYST_PROMPT.format(
                    primary_signal=json.dumps(primary_signal.parsed, indent=2),
                    secondary_signal=json.dumps(secondary_signal.parsed, indent=2),
                    market_state=market_state,
                ),
                "Provide your contrarian analysis.",
                temperature=0.3,
            )
            independent_parsed = self._parse_json_response(independent_raw)
            independent_signal = AnalystSignal(
                model_name="moonshotai.kimi-k2.5",
                role="independent_analyst",
                raw_response=independent_raw,
                parsed=independent_parsed,
                success=not independent_parsed.get("parse_error", False),
            )
        except Exception as e:
            cprint(f"[CONSENSUS] Independent analyst failed: {e}", "red")
            independent_signal = AnalystSignal(
                model_name="moonshotai.kimi-k2.5",
                role="independent_analyst",
                raw_response="",
                parsed={},
                success=False,
                error=str(e),
            )

        # ── Step 4: Evaluate Consensus ────────────────────────
        result = self._evaluate_consensus(
            primary_signal, secondary_signal, independent_signal, risk_gates
        )

        cprint(f"\n[CONSENSUS] Result: {result.direction} (confidence: {result.consensus_confidence:.0%})", "cyan")
        cprint(f"[CONSENSUS] Consensus met: {result.consensus_met}", "cyan")
        if result.risk_gates:
            for gate in result.risk_gates:
                cprint(f"[CONSENSUS] Risk gate: {gate}", "yellow")

        return result

    def _evaluate_consensus(
        self,
        primary: AnalystSignal,
        secondary: AnalystSignal,
        independent: AnalystSignal,
        risk_gates: Optional[List[str]] = None,
    ) -> ConsensusResult:
        """Evaluate whether consensus is reached"""

        # Default values
        direction = "NO_TRADE"
        confidence = 0.0
        primary_data = primary.parsed
        secondary_agrees = False
        independent_agrees = False
        gates = []

        if primary.success:
            direction = primary_data.get("direction", "NO_TRADE")
            confidence = primary_data.get("confidence", 0.0)

        # Check secondary agreement
        if secondary.success:
            secondary_agrees = secondary.parsed.get("agree", False)
            if secondary.parsed.get("direction") != direction:
                secondary_agrees = False

        # Check independent agreement
        if independent.success:
            independent_agrees = independent.parsed.get("agree_with_consensus", False)

        # ── Consensus Rules ──────────────────────────────────
        consensus_met = False

        if direction == "NO_TRADE":
            gates.append("Primary analyst says NO_TRADE")
        elif confidence < 0.7:
            gates.append(f"Primary confidence too low: {confidence:.0%} < 70%")
        elif not secondary_agrees:
            gates.append("Secondary analyst disagrees")
        else:
            consensus_met = True

        # Apply risk gates
        if risk_gates:
            for gate in risk_gates:
                gates.append(f"Risk gate: {gate}")
                consensus_met = False

        return ConsensusResult(
            direction=direction if consensus_met else "NO_TRADE",
            consensus_confidence=confidence if consensus_met else 0.0,
            primary_signal=primary_data,
            secondary_agreement=secondary_agrees,
            independent_agreement=independent_agrees,
            consensus_met=consensus_met,
            signals=[primary, secondary, independent],
            risk_gates=gates,
        )


# ── Quick Test ───────────────────────────────────────────────
async def test_consensus():
    """Quick test of the consensus engine"""
    engine = ConsensusEngine()

    market_state = """
Symbol: BTCUSDT
Timeframe: 5m
Price: 108420
Trend: Bullish
VWAP: 108110
ATR: 420
Delta: +18420
CVD: Strongly rising
Bid/Ask Imbalance: 1.82
Buy Volume: 62.4%
Sell Volume: 37.6%
Absorption: Detected at 108200
Liquidity Sweep: 108050
Reclaim: Confirmed
Volume: 2.1x average
Market Regime: Trending
Previous Resistance: 108350
Estimated R:R: 2.7
"""

    result = await engine.get_consensus(market_state)
    print("\nConsensus Result:")
    print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    asyncio.run(test_consensus())
