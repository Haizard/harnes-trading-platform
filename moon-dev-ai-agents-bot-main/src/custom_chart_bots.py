"""Custom Chart Bots - Liquidity Sweep Detector + FVG Fill Tracker
Generated for the SMC chart system. Compatible with Lightweight Charts.
"""

from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class ChartBotResult:
    markers: List[Dict] = field(default_factory=list)
    price_lines: List[Dict] = field(default_factory=list)
    panels: List[Dict] = field(default_factory=list)

    def merge(self, other):
        return ChartBotResult(
            markers=self.markers + other.markers,
            price_lines=self.price_lines + other.price_lines,
            panels=self.panels + other.panels,
        )


def detect_liquidity_sweeps(candles, lookback=20):
    """Detect liquidity sweeps - price taking out swing highs/lows then reversing."""
    if not candles or len(candles) < lookback + 5:
        return ChartBotResult()
    markers, price_lines = [], []
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    opens = [c["open"] for c in candles]
    times = [c["time"] for c in candles]
    swing_highs = []
    swing_lows = []
    for i in range(2, len(candles) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append((i, highs[i]))
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append((i, lows[i]))
    swing_highs = swing_highs[-lookback:]
    swing_lows = swing_lows[-lookback:]
    for si, sh_price in swing_highs:
        for i in range(si + 1, min(si + 10, len(candles))):
            if highs[i] > sh_price:
                rng = highs[i] - lows[i]
                wick = highs[i] - max(opens[i], closes[i])
                if rng > 0 and wick / rng > 0.5:
                    for j in range(i + 1, min(i + 4, len(candles))):
                        if closes[j] < opens[j]:
                            markers.append({"time": times[i], "position": "aboveBar", "color": "#f87171", "shape": "arrowDown", "text": "LIQ SWEEP"})
                            price_lines.append({"price": sh_price, "color": "#f87171", "lineStyle": 1, "title": "Swept High"})
                            break
                    break
    for si, sl_price in swing_lows:
        for i in range(si + 1, min(si + 10, len(candles))):
            if lows[i] < sl_price:
                rng = highs[i] - lows[i]
                wick = min(opens[i], closes[i]) - lows[i]
                if rng > 0 and wick / rng > 0.5:
                    for j in range(i + 1, min(i + 4, len(candles))):
                        if closes[j] > opens[j]:
                            markers.append({"time": times[i], "position": "belowBar", "color": "#4ade80", "shape": "arrowUp", "text": "LIQ SWEEP"})
                            price_lines.append({"price": sl_price, "color": "#4ade80", "lineStyle": 1, "title": "Swept Low"})
                            break
                    break
    return ChartBotResult(markers=markers[-15:], price_lines=price_lines[-10:])

def detect_fvg_fills(candles, tolerance=0.002):
    """Detect Fair Value Gap fills - price returning to close an FVG."""
    if not candles or len(candles) < 5:
        return ChartBotResult()
    markers, price_lines = [], []
    times = [c["time"] for c in candles]
    fvgs = []
    for i in range(1, len(candles) - 1):
        prev_h = candles[i-1]["high"]
        nxt_l = candles[i+1]["low"]
        if nxt_l > prev_h:
            fvgs.append({"type": "bullish", "high": nxt_l, "low": prev_h, "time": times[i], "idx": i, "filled": False, "fill_time": None})
        prev_l = candles[i-1]["low"]
        nxt_h = candles[i+1]["high"]
        if prev_l > nxt_h:
            fvgs.append({"type": "bearish", "high": prev_l, "low": nxt_h, "time": times[i], "idx": i, "filled": False, "fill_time": None})
    for fvg in fvgs:
        for i in range(fvg["idx"] + 2, len(candles)):
            c = candles[i]
            if c["low"] <= fvg["high"] and c["high"] >= fvg["low"]:
                if c["close"] <= fvg["high"] and c["close"] >= fvg["low"]:
                    fvg["filled"] = True
                    fvg["fill_time"] = times[i]
                    markers.append({"time": times[i], "position": "belowBar" if fvg["type"] == "bullish" else "aboveBar", "color": "#fbbf24", "shape": "circle", "text": "FVG FILL"})
                    break
    for fvg in fvgs[-10:]:
        color = "#fbbf24" if fvg["filled"] else "#a78bfa"
        style = 2 if fvg["filled"] else 1
        title = ("FVG filled" if fvg["filled"] else "FVG open") + " " + fvg["type"]
        price_lines.append({"price": fvg["high"], "color": color, "lineStyle": style, "title": title})
        price_lines.append({"price": fvg["low"], "color": color, "lineStyle": style, "title": ""})
    return ChartBotResult(markers=markers[-15:], price_lines=price_lines[-20:])


def run_custom_bots(candles):
    """Run all custom chart bots and merge results."""
    sweep_result = detect_liquidity_sweeps(candles)
    fvg_result = detect_fvg_fills(candles)
    merged = sweep_result.merge(fvg_result)
    sweep_count = len(sweep_result.markers)
    fvg_fill_count = len([m for m in fvg_result.markers if m.get("text") == "FVG FILL"])
    merged.panels.append({
        "name": "Custom Bots",
        "values": [
            {"label": "Liquidity Sweeps", "value": str(sweep_count)},
            {"label": "FVG Fills", "value": str(fvg_fill_count)},
        ]
    })
    return merged