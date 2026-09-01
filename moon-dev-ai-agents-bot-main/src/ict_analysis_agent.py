"""
ICT Analysis Agent — Smart Money Concepts
Ported from System 1 (RBIAgent) to System 2 (MicroEngine).

Analyzes OHLCV data for ICT/Smart Money concepts:
- Order Blocks (OB) — institutional supply/demand zones
- Fair Value Gaps (FVG) — price imbalance zones
- Liquidity Sweeps — stop hunts above/below key levels
- Market Structure — Higher Highs/Lower Lows
- Break of Structure (BOS) — trend continuation
- Change of Character (CHoCH) — trend reversal

These concepts help identify where institutional money is operating.
"""

import numpy as np
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from termcolor import cprint


class ICTAnalysisAgent:
    """
    ICT/Smart Money Concepts analysis for Solana tokens.
    
    Analyzes OHLCV data for institutional trading patterns:
- Order Blocks: zones where institutions placed large orders
- Fair Value Gaps: price imbalances that tend to fill
- Liquidity Sweeps: stop hunts that trap retail traders
- Market Structure: trend direction via swing highs/lows
"""

    def __init__(self):
        cprint("[ICT] ICT Analysis Agent initialized", "white", "on_blue")

    def analyze(self, indicators: Dict, candidate_metrics: Dict = None,
                price_data: Dict = None) -> dict:
        """
        Perform ICT analysis on a token.
        
        Args:
            indicators: Dict from IndicatorEngine.calculate()
            candidate_metrics: Dict from TokenCandidate.to_dict()
            price_data: Dict with OHLCV candles
        
        Returns:
            dict with ICT signals, order blocks, FVGs, market structure
        """
        candidate_metrics = candidate_metrics or {}
        
        # Calculate ICT concepts
        market_structure = self._analyze_market_structure(indicators)
        order_blocks = self._detect_order_blocks(indicators, candidate_metrics)
        fvg = self._detect_fair_value_gaps(indicators)
        liquidity = self._analyze_liquidity(indicators, candidate_metrics)
        
        # Combine signals
        signal = self._combine_signals(market_structure, order_blocks, fvg, liquidity)
        
        return {
            "signal": signal["direction"],
            "confidence": signal["confidence"],
            "market_structure": market_structure,
            "order_blocks": order_blocks,
            "fair_value_gaps": fvg,
            "liquidity": liquidity,
            "reason": signal["reason"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _analyze_market_structure(self, indicators: Dict) -> dict:
        """Analyze market structure (HH/HL for bullish, LL/LH for bearish)."""
        sma10 = indicators.get("sma10", 0)
        sma20 = indicators.get("sma20", 0)
        close = indicators.get("current_price", 0)
        prev_price = indicators.get("prev_price", close)
        momentum_5 = indicators.get("momentum_5", 0)
        momentum_10 = indicators.get("momentum_10", 0)
        
        # Determine structure
        if sma10 > sma20 and close > sma10:
            structure = "BULLISH"  # Higher Highs, Higher Lows
            bias = "buy"
        elif sma10 < sma20 and close < sma10:
            structure = "BEARISH"  # Lower Lows, Lower Highs
            bias = "sell"
        else:
            structure = "RANGING"
            bias = "neutral"
        
        # Break of Structure (BOS) detection
        bos = "none"
        if momentum_5 > 2 and momentum_10 > 0:
            bos = "BULLISH_BOS"
        elif momentum_5 < -2 and momentum_10 < 0:
            bos = "BEARISH_BOS"
        
        # Change of Character (CHoCH) detection
        choch = "none"
        if momentum_5 > 3 and momentum_10 < -1:
            choch = "BULLISH_CHOCH"  # Reversal from bearish to bullish
        elif momentum_5 < -3 and momentum_10 > 1:
            choch = "BEARISH_CHOCH"  # Reversal from bullish to bearish
        
        return {
            "structure": structure,
            "bias": bias,
            "bos": bos,
            "choch": choch,
            "sma_alignment": "bullish" if sma10 > sma20 else "bearish",
        }

    def _detect_order_blocks(self, indicators: Dict, metrics: Dict) -> dict:
        """Detect potential order blocks (institutional supply/demand zones)."""
        bb_upper = indicators.get("bb_upper", 0)
        bb_lower = indicators.get("bb_lower", 0)
        bb_middle = indicators.get("bb_middle", 0)
        close = indicators.get("current_price", 0)
        volume_ratio = indicators.get("volume_ratio", 1)
        
        # Demand zone: price near lower BB with high volume
        demand_zone = False
        if close <= bb_lower * 1.02 and volume_ratio > 1.5:
            demand_zone = True
        
        # Supply zone: price near upper BB with high volume
        supply_zone = False
        if close >= bb_upper * 0.98 and volume_ratio > 1.5:
            supply_zone = True
        
        # Bullish OB: price at demand with volume spike
        bullish_ob = demand_zone and volume_ratio > 2.0
        
        # Bearish OB: price at supply with volume spike
        bearish_ob = supply_zone and volume_ratio > 2.0
        
        return {
            "demand_zone": demand_zone,
            "supply_zone": supply_zone,
            "bullish_ob": bullish_ob,
            "bearish_ob": bearish_ob,
            "demand_level": round(bb_lower, 8),
            "supply_level": round(bb_upper, 8),
        }

    def _detect_fair_value_gaps(self, indicators: Dict) -> dict:
        """Detect Fair Value Gaps (price imbalances)."""
        bb_width = indicators.get("bb_width", 0)
        atr_pct = indicators.get("atr_pct", 0)
        momentum = indicators.get("momentum_5", 0)
        
        # FVG detection: large price move with gap
        bullish_fvg = momentum > 3 and atr_pct > 0.5
        bearish_fvg = momentum < -3 and atr_pct > 0.5
        
        # FVG fill probability
        fill_probability = 0.0
        if bullish_fvg or bearish_fvg:
            # Wide BB = more likely to fill
            fill_probability = min(bb_width * 10, 0.9)
        
        return {
            "bullish_fvg": bullish_fvg,
            "bearish_fvg": bearish_fvg,
            "fill_probability": round(fill_probability, 3),
            "bb_width": round(bb_width, 4),
        }

    def _analyze_liquidity(self, indicators: Dict, metrics: Dict) -> dict:
        """Analyze liquidity levels (where stops are clustered)."""
        bb_upper = indicators.get("bb_upper", 0)
        bb_lower = indicators.get("bb_lower", 0)
        sma20 = indicators.get("sma20", 0)
        close = indicators.get("current_price", 0)
        
        # Buy-side liquidity: above recent highs (BB upper)
        buy_side_liquidity = bb_upper
        
        # Sell-side liquidity: below recent lows (BB lower)
        sell_side_liquidity = bb_lower
        
        # Liquidity sweep detection
        sweep_up = close > bb_upper * 0.99
        sweep_down = close < bb_lower * 1.01
        
        return {
            "buy_side_liquidity": round(buy_side_liquidity, 8),
            "sell_side_liquidity": round(sell_side_liquidity, 8),
            "sweep_up": sweep_up,
            "sweep_down": sweep_down,
            "near_liquidity": sweep_up or sweep_down,
        }

    def _combine_signals(self, structure: Dict, ob: Dict, fvg: Dict,
                         liquidity: Dict) -> dict:
        """Combine all ICT signals into a single direction."""
        score = 0
        reasons = []
        
        # Market Structure (strongest signal)
        if structure["structure"] == "BULLISH":
            score += 2
            reasons.append("Bullish market structure (HH/HL)")
        elif structure["structure"] == "BEARISH":
            score -= 2
            reasons.append("Bearish market structure (LL/LH)")
        
        # BOS/CHoCH
        if structure["bos"] == "BULLISH_BOS":
            score += 1
            reasons.append("Bullish BOS")
        elif structure["bos"] == "BEARISH_BOS":
            score -= 1
            reasons.append("Bearish BOS")
        
        if structure["choch"] == "BULLISH_CHOCH":
            score += 2
            reasons.append("Bullish CHoCH (reversal)")
        elif structure["choch"] == "BEARISH_CHOCH":
            score -= 2
            reasons.append("Bearish CHoCH (reversal)")
        
        # Order Blocks
        if ob["bullish_ob"]:
            score += 1
            reasons.append("Bullish order block detected")
        elif ob["bearish_ob"]:
            score -= 1
            reasons.append("Bearish order block detected")
        
        # Fair Value Gaps
        if fvg["bullish_fvg"]:
            score += 0.5
            reasons.append("Bullish FVG (price may fill up)")
        elif fvg["bearish_fvg"]:
            score -= 0.5
            reasons.append("Bearish FVG (price may fill down)")
        
        # Liquidity Sweeps
        if liquidity["sweep_down"]:
            score += 1
            reasons.append("Sell-side liquidity swept (bullish)")
        elif liquidity["sweep_up"]:
            score -= 1
            reasons.append("Buy-side liquidity swept (bearish)")
        
        # Determine direction
        if score >= 2:
            direction = "BUY"
        elif score <= -2:
            direction = "SELL"
        else:
            direction = "HOLD"
        
        confidence = min(0.5 + abs(score) * 0.1, 0.95)
        
        return {
            "direction": direction,
            "confidence": round(confidence, 3),
            "reason": "; ".join(reasons) if reasons else "No clear ICT signal",
        }

    def get_stats(self) -> dict:
        return {"available": True}


# ── Singleton ──────────────────────────────────────────────
_ict_instance = None

def get_ict_analysis_agent() -> ICTAnalysisAgent:
    global _ict_instance
    if _ict_instance is None:
        _ict_instance = ICTAnalysisAgent()
    return _ict_instance
