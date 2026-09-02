"""SMC Pattern Detection Engine - DSH Pattern"""

import json
from datetime import datetime, timezone
from typing import List, Dict, Any
from pathlib import Path
from dataclasses import dataclass, field, asdict
from src.event_bus import _fire_and_forget

DATA_DIR = Path("src/data/smc")
DATA_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class OrderBlock:
    type: str
    high: float
    low: float
    time_start: int
    time_end: int
    strength: float
    mitigated: bool = False
    def to_dict(self): return asdict(self)


@dataclass
class FairValueGap:
    type: str
    high: float
    low: float
    time: int
    filled: bool = False
    def to_dict(self): return asdict(self)


@dataclass
class LiquidityLevel:
    price: float
    type: str
    touches: int
    time: int
    def to_dict(self): return asdict(self)


@dataclass
class MarketStructure:
    type: str
    price: float
    time: int
    def to_dict(self): return asdict(self)


@dataclass
class BreakOfStructure:
    direction: str
    price: float
    time: int
    broken_level: float
    def to_dict(self): return asdict(self)


@dataclass
class SMCOverlay:
    order_blocks: List[Dict] = field(default_factory=list)
    fvgs: List[Dict] = field(default_factory=list)
    liquidity: List[Dict] = field(default_factory=list)
    structure: List[Dict] = field(default_factory=list)
    bos: List[Dict] = field(default_factory=list)
    markers: List[Dict] = field(default_factory=list)
    def to_dict(self): return asdict(self)


class SMCPatternDetector:
    def __init__(self, event_bus=None):
        self.event_bus = event_bus

    def _emit_event(self, name, payload):
        try:
            from src.db_storage import log_event
            log_event(name, payload)
        except: pass
        if self.event_bus:
            try: _fire_and_forget(self.event_bus.emit(name, payload))
            except: pass

    def detect_all(self, candles):
        if not candles or len(candles) < 10: return SMCOverlay()
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        opens = [c["open"] for c in candles]
        closes = [c["close"] for c in candles]
        times = [c["time"] for c in candles]
        obs = self._detect_order_blocks(candles, opens, closes, times)
        fvgs = self._detect_fvgs(candles, times)
        liq = self._detect_liquidity(highs, lows, times)
        struct = self._detect_structure(highs, lows, times)
        bos = self._detect_bos(candles, struct, times)
        markers = self._build_markers(obs, fvgs, bos)
        self._emit_event("smc/detected", {"ob": len(obs), "fvg": len(fvgs), "ts": datetime.now(timezone.utc).isoformat()})
        return SMCOverlay([o.to_dict() for o in obs], [f.to_dict() for f in fvgs], [l.to_dict() for l in liq], [s.to_dict() for s in struct], [b.to_dict() for b in bos], markers)

    def _detect_order_blocks(self, candles, opens, closes, times):
        obs = []
        for i in range(2, len(candles) - 1):
            c = candles[i]
            # Demand: down-candle before bullish impulse
            if closes[i-1] < opens[i-1] and closes[i] > opens[i] and closes[i+1] > opens[i+1] and candles[i+1]["close"] > c["high"]:
                s = min(1.0, (candles[i+1]["close"] - c["high"]) / max(c["low"], 1e-10) * 10)
                obs.append(OrderBlock("demand", c["high"], c["low"], times[i], times[min(i+5, len(times)-1)], s))
            # Supply: up-candle before bearish impulse
            if closes[i-1] > opens[i-1] and closes[i] < opens[i] and closes[i+1] < opens[i+1] and candles[i+1]["close"] < c["low"]:
                s = min(1.0, (c["low"] - candles[i+1]["close"]) / max(c["high"], 1e-10) * 10)
                obs.append(OrderBlock("supply", c["high"], c["low"], times[i], times[min(i+5, len(times)-1)], s))
        return obs[-20:]

    def _detect_fvgs(self, candles, times):
        fvgs = []
        for i in range(1, len(candles) - 1):
            prev_h = candles[i-1]["high"]
            nxt_l = candles[i+1]["low"]
            if nxt_l > prev_h and candles[i]["close"] > candles[i]["open"]:
                fvgs.append(FairValueGap("bullish", nxt_l, prev_h, times[i]))
            prev_l = candles[i-1]["low"]
            nxt_h = candles[i+1]["high"]
            if prev_l > nxt_h and candles[i]["close"] < candles[i]["open"]:
                fvgs.append(FairValueGap("bearish", prev_l, nxt_h, times[i]))
        return fvgs[-15:]

    def _detect_liquidity(self, highs, lows, times):
        liq = []
        tol = 0.002
        swing_h = [(i, highs[i]) for i in range(2, len(highs)-2) if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]]
        swing_l = [(i, lows[i]) for i in range(2, len(lows)-2) if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]]
        for i in range(len(swing_h)):
            for j in range(i+1, len(swing_h)):
                if abs(swing_h[i][1] - swing_h[j][1]) / max(swing_h[i][1], 1e-10) < tol:
                    liq.append(LiquidityLevel((swing_h[i][1]+swing_h[j][1])/2, "equal_highs", 2, times[swing_h[j][0]]))
        for i in range(len(swing_l)):
            for j in range(i+1, len(swing_l)):
                if abs(swing_l[i][1] - swing_l[j][1]) / max(swing_l[i][1], 1e-10) < tol:
                    liq.append(LiquidityLevel((swing_l[i][1]+swing_l[j][1])/2, "equal_lows", 2, times[swing_l[j][0]]))
        return liq[-10:]

    def _detect_structure(self, highs, lows, times):
        struct = []
        swing_h = [(i, highs[i]) for i in range(2, len(highs)-2) if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]]
        swing_l = [(i, lows[i]) for i in range(2, len(lows)-2) if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]]
        for k in range(1, len(swing_h)):
            t = "HH" if swing_h[k][1] > swing_h[k-1][1] else "LH"
            struct.append(MarketStructure(t, swing_h[k][1], times[swing_h[k][0]]))
        for k in range(1, len(swing_l)):
            t = "HL" if swing_l[k][1] > swing_l[k-1][1] else "LL"
            struct.append(MarketStructure(t, swing_l[k][1], times[swing_l[k][0]]))
        struct.sort(key=lambda s: s.time)
        return struct[-20:]

    def _detect_bos(self, candles, structure, times):
        bos = []
        hh = [s for s in structure if s.type == "HH"]
        ll = [s for s in structure if s.type == "LL"]
        for i in range(1, len(candles)):
            for h in hh:
                if candles[i]["close"] > h.price and candles[i-1]["close"] <= h.price:
                    bos.append(BreakOfStructure("bullish", candles[i]["close"], times[i], h.price))
                    break
            for l in ll:
                if candles[i]["close"] < l.price and candles[i-1]["close"] >= l.price:
                    bos.append(BreakOfStructure("bearish", candles[i]["close"], times[i], l.price))
                    break
        return bos[-10:]

    def _build_markers(self, obs, fvgs, bos):
        markers = []
        for ob in obs[-5:]:
            markers.append({"time": ob.time_start, "position": "belowBar" if ob.type=="demand" else "aboveBar",
                "color": "#4ade80" if ob.type=="demand" else "#f87171",
                "shape": "arrowUp" if ob.type=="demand" else "arrowDown", "text": f"OB {ob.type}"})
        for f in fvgs[-5:]:
            markers.append({"time": f.time, "position": "belowBar" if f.type=="bullish" else "aboveBar",
                "color": "#fbbf24", "shape": "circle", "text": "FVG"})
        for b in bos[-5:]:
            markers.append({"time": b.time, "position": "belowBar" if b.direction=="bullish" else "aboveBar",
                "color": "#22d3ee" if b.direction=="bullish" else "#a78bfa",
                "shape": "arrowUp" if b.direction=="bullish" else "arrowDown", "text": f"BOS {b.direction}"})
        markers.sort(key=lambda m: m["time"])
        return markers[-20:]


_instance = None

def get_smc_detector(event_bus=None):
    global _instance
    if _instance is None: _instance = SMCPatternDetector(event_bus=event_bus)
    return _instance

async def tool_smc_detect(symbol="SOLUSDT", interval="1h", limit=100):
    det = get_smc_detector()
    from src.tradingview_feed import get_tradingview_feed
    feed = get_tradingview_feed()
    ohlcv = feed.get_ohlcv_candles(symbol=symbol, interval=interval, limit=limit)
    if ohlcv.get("error") or not ohlcv.get("candles"):
        return {"error": "No OHLCV data", "symbol": symbol}
    result = det.detect_all(ohlcv["candles"])
    return {"symbol": symbol, "interval": interval, "candles": len(ohlcv["candles"]), **result.to_dict(), "source": "smc_patterns"}