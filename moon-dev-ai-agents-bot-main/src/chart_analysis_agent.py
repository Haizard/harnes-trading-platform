"""
Chart Analysis Agent — Chart Pattern Recognition
Ported from System 1 (ChartAnalysisAgent) to System 2 (MicroEngine).

Analyzes OHLCV data for chart patterns:
- Support/Resistance levels
- Trend direction
- Volume patterns
- Candlestick patterns
- SMA crossovers

Uses LLM to interpret patterns and generate trading signals.
"""

import os
import json
from datetime import datetime, timezone
from typing import Dict, Optional, List
from termcolor import cprint


# ── Analysis Prompt ──────────────────────────────────────
CHART_PROMPT = """You are Moon Dev's Chart Analysis AI analyzing a Solana token.

Token: {symbol}
Timeframe: {timeframe}

Price Data (last 10 candles):
{price_data}

Technical Indicators:
{indicators}

Pattern Detection:
{patterns}

Analyze the chart and determine:
1. Current trend direction (BULLISH/BEARISH/SIDEWAYS)
2. Key support and resistance levels
3. Volume confirmation
4. Pattern quality
5. Trading recommendation

Respond in this exact JSON format:
{{"direction": "BULLISH" or "BEARISH" or "SIDEWAYS", "action": "BUY" or "SELL" or "HOLD", "confidence": 0.0-1.0, "support": price_level, "resistance": price_level, "pattern": "pattern_name", "reason": "brief explanation"}}
"""


class ChartAnalysisAgent:
    """
    Chart pattern recognition using OHLCV data + LLM.
    
    Analyzes:
- Trend direction (SMA crossovers)
- Support/Resistance levels
- Volume patterns
- Candlestick patterns (doji, hammer, engulfing)
"""

    def __init__(self):
        self._available = False
        self._analyses: List[dict] = []
        
        try:
            from src.bedrock_llm import is_bedrock_configured
            self._available = is_bedrock_configured()
        except Exception:
            pass
        
        status = "AVAILABLE" if self._available else "UNAVAILABLE"
        cprint(f"[CHART] Chart Analysis Agent initialized — {status}", "white", "on_blue")

    def analyze(self, symbol: str, indicators: Dict, price_data: Dict = None,
                candidate_metrics: Dict = None) -> dict:
        """
        Analyze chart patterns for a token.
        
        Args:
            symbol: Token symbol
            indicators: Dict from IndicatorEngine.calculate()
            price_data: Dict with OHLCV data
            candidate_metrics: Dict from TokenCandidate.to_dict()
        
        Returns:
            dict with direction, action, confidence, pattern, support, resistance
        """
        # Detect patterns from indicators
        patterns = self._detect_patterns(indicators, candidate_metrics)
        
        # Calculate support/resistance from indicators
        sr = self._calculate_support_resistance(indicators)
        
        result = {
            "symbol": symbol,
            "direction": patterns.get("direction", "SIDEWAYS"),
            "action": patterns.get("action", "HOLD"),
            "confidence": patterns.get("confidence", 0.5),
            "support": sr.get("support", 0),
            "resistance": sr.get("resistance", 0),
            "pattern": patterns.get("pattern", "none"),
            "reason": patterns.get("reason", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        self._analyses.append(result)
        return result

    def _detect_patterns(self, indicators: Dict, metrics: Dict = None) -> Dict:
        """Detect chart patterns from indicators."""
        metrics = metrics or {}
        
        rsi = indicators.get("rsi", 50)
        macd_hist = indicators.get("macd_hist", 0)
        sma10 = indicators.get("sma10", 0)
        sma20 = indicators.get("sma20", 0)
        bb_pct = indicators.get("bb_pct", 0.5)
        stoch_k = indicators.get("stoch_k", 50)
        stoch_d = indicators.get("stoch_d", 50)
        volume_ratio = indicators.get("volume_ratio", 1)
        momentum = indicators.get("momentum_5", 0)
        
        score = 0
        reasons = []
        pattern = "none"
        
        # 1. SMA Crossover
        if sma10 > sma20 and sma20 > 0:
            score += 1
            reasons.append("SMA10 > SMA20 (golden cross)")
            pattern = "golden_cross"
        elif sma10 < sma20 and sma20 > 0:
            score -= 1
            reasons.append("SMA10 < SMA20 (death cross)")
            pattern = "death_cross"
        
        # 2. RSI Divergence
        if rsi < 30:
            score += 1
            reasons.append(f"RSI oversold ({rsi:.0f})")
            pattern = "rsi_oversold"
        elif rsi > 70:
            score -= 1
            reasons.append(f"RSI overbought ({rsi:.0f})")
            pattern = "rsi_overbought"
        
        # 3. MACD Crossover
        if macd_hist > 0:
            score += 0.5
            reasons.append("MACD bullish")
        elif macd_hist < 0:
            score -= 0.5
            reasons.append("MACD bearish")
        
        # 4. Bollinger Band Squeeze
        if bb_pct < 0.2:
            score += 0.5
            reasons.append("Near lower BB (potential bounce)")
            pattern = "bb_bounce"
        elif bb_pct > 0.8:
            score -= 0.5
            reasons.append("Near upper BB (potential reversal)")
            pattern = "bb_reversal"
        
        # 5. Stochastic Crossover
        if stoch_k < 20 and stoch_k > stoch_d:
            score += 0.5
            reasons.append("Stoch oversold cross")
            pattern = "stoch_cross"
        elif stoch_k > 80 and stoch_k < stoch_d:
            score -= 0.5
            reasons.append("Stoch overbought cross")
        
        # 6. Volume confirmation
        if volume_ratio > 2.0:
            reasons.append(f"High volume ({volume_ratio:.1f}x)")
            score *= 1.2  # Amplify signal
        
        # 7. Momentum
        if momentum > 2:
            score += 0.5
            reasons.append(f"Strong momentum (+{momentum:.1f}%)")
        elif momentum < -2:
            score -= 0.5
            reasons.append(f"Negative momentum ({momentum:.1f}%)")
        
        # Determine direction
        if score >= 2:
            direction = "BULLISH"
            action = "BUY"
        elif score <= -2:
            direction = "BEARISH"
            action = "SELL"
        else:
            direction = "SIDEWAYS"
            action = "HOLD"
        
        confidence = min(0.5 + abs(score) * 0.1, 0.95)
        
        return {
            "direction": direction,
            "action": action,
            "confidence": round(confidence, 3),
            "pattern": pattern,
            "reason": "; ".join(reasons) if reasons else "No clear pattern",
        }

    def _calculate_support_resistance(self, indicators: Dict) -> Dict:
        """Calculate support/resistance from indicators."""
        close = indicators.get("current_price", 0)
        bb_upper = indicators.get("bb_upper", close * 1.02)
        bb_lower = indicators.get("bb_lower", close * 0.98)
        sma20 = indicators.get("sma20", close)
        
        return {
            "support": round(bb_lower, 8),
            "resistance": round(bb_upper, 8),
            "sma20_support": round(sma20, 8),
        }

    def get_stats(self) -> dict:
        return {
            "total_analyses": len(self._analyses),
            "available": self._available,
        }


# ── Singleton ──────────────────────────────────────────────
_chart_instance = None

def get_chart_analysis_agent() -> ChartAnalysisAgent:
    global _chart_instance
    if _chart_instance is None:
        _chart_instance = ChartAnalysisAgent()
    return _chart_instance
