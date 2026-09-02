"""
trading_confluence.py - DSH Pattern
7-in-1 TradingView Value Integrator
"""

import time
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field, asdict
from pathlib import Path
from src.event_bus import _fire_and_forget

DATA_DIR = Path("src/data/confluence")
DATA_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ConfluenceResult:
    token_address: str = ""
    symbol: str = ""
    confirmation_passed: bool = False
    confirmation_score: float = 0.0
    confirmation_reason: str = ""
    mtf_passed: bool = False
    mtf_score: float = 0.0
    mtf_alignment: str = ""
    mtf_details: Dict = field(default_factory=dict)
    sm_technical_passed: bool = False
    sm_technical_score: float = 0.0
    sm_signal: str = ""
    ohlcv_validated: bool = False
    ohlcv_volume_confirmed: bool = False
    ohlcv_volume_ratio: float = 0.0
    macro_signal: str = ""
    macro_score: float = 0.0
    final_score: float = 0.0
    final_signal: str = "HOLD"
    should_trade: bool = False
    rejection_reasons: List[str] = field(default_factory=list)
    confluence_level: str = "none"
    timestamp: str = ""
    def to_dict(self):
        return asdict(self)


class TradingConfluence:
    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._cache = {}
        self._cache_ttl = 120

    def _emit_event(self, name, payload):
        try:
            from src.db_storage import log_event
            log_event(name, payload)
        except: pass
        if self.event_bus:
            try: _fire_and_forget(self.event_bus.emit(name, payload))
            except: pass

    def _get_cache(self, key):
        if key in self._cache:
            ts, data = self._cache[key]
            if time.time() - ts < self._cache_ttl: return data
        return None

    def _set_cache(self, key, data):
        self._cache[key] = (time.time(), data)

    def _resolve_tv_symbol(self, symbol):
        symbol = symbol.upper()
        mappings = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
            "BONK": "BONKUSDT", "WIF": "WIFUSDT", "POPCAT": "POPCATUSDT",
            "FARTCOIN": "FARTCOINUSDT", "PEPE": "PEPEUSDT",
            "TRUMP": "TRUMPUSDT", "MEW": "MEWUSDT", "BOME": "BOMEUSDT",
            "MYRO": "MYROUSDT", "RETARDIO": "RETARDIOUSDT"}
        if symbol in mappings: return mappings[symbol]
        if not symbol.endswith("USDT") and not symbol.endswith("USD"):
            return symbol + "USDT"
        return symbol

    def check_confirmation(self, candidate, tv_data=None):
        ck = f"confirm:{candidate.address}:{candidate.symbol}"
        c = self._get_cache(ck)
        if c: return c
        score = 0.0; reasons = []
        if tv_data is None:
            try:
                from src.tradingview_feed import get_tradingview_feed
                feed = get_tradingview_feed(event_bus=self.event_bus)
                tv_sym = self._resolve_tv_symbol(candidate.symbol)
                if tv_sym: tv_data = feed.get_analysis(tv_sym, interval="1h")
            except: pass
        if tv_data is None or tv_data.get("error"):
            r = (True, 50.0, "No TV data - neutral"); self._set_cache(ck, r); return r
        rec = tv_data.get("recommendation", "NEUTRAL")
        rsi = tv_data.get("indicators", {}).get("RSI", 50)
        macd = tv_data.get("indicators", {}).get("MACD", 0)
        macd_sig = tv_data.get("indicators", {}).get("MACD_signal", 0)
        rec_scores = {"STRONG_BUY": 95, "BUY": 80, "NEUTRAL": 50, "SELL": 20, "STRONG_SELL": 5}
        score = rec_scores.get(rec, 50)
        reasons.append(f"TV: {rec} (score={score})")
        if hasattr(candidate, "price_change_1h") and candidate.price_change_1h > 5 and rsi > 70:
            score -= 15; reasons.append(f"RSI overbought ({int(rsi)}) during pump")
        elif hasattr(candidate, "price_change_1h") and candidate.price_change_1h > 2 and 40 < rsi < 60:
            score += 10; reasons.append(f"RSI healthy ({int(rsi)})")
        if macd > macd_sig and macd_sig != 0: score += 10; reasons.append("MACD bullish")
        elif macd < macd_sig and macd_sig != 0: score -= 10; reasons.append("MACD bearish")
        passed = score >= 40
        r = (passed, score, "; ".join(reasons)); self._set_cache(ck, r); return r

    def check_multi_timeframe(self, symbol, exchange="BINANCE"):
        ck = f"mtf:{symbol}:{exchange}"
        c = self._get_cache(ck)
        if c: return c
        try:
            from src.tradingview_feed import get_tradingview_feed
            feed = get_tradingview_feed(event_bus=self.event_bus)
            intervals = {}
            for iv in ["1d", "4h", "1h"]:
                try:
                    a = feed.get_analysis(symbol, exchange=exchange, screener="crypto", interval=iv)
                    if not a.get("error"): intervals[iv] = a
                except: pass
            tv_scores = {"STRONG_BUY": 2, "BUY": 1, "NEUTRAL": 0, "SELL": -1, "STRONG_SELL": -2}
            details = {}; total = 0; count = 0; dirs = []
            for iv, data in intervals.items():
                rec = data.get("recommendation", "NEUTRAL")
                s = tv_scores.get(rec, 0); total += s; count += 1; dirs.append(s)
                rsi = data.get("indicators", {}).get("RSI", 50)
                details[iv] = {"recommendation": rec, "score": s, "RSI": round(rsi, 1) if rsi else None}
            if count == 0:
                r = (True, 50.0, "No TV data", details); self._set_cache(ck, r); return r
            avg = total / count
            if all(d > 0 for d in dirs): alignment = "all_bullish"; score = min(95, 70 + avg * 15)
            elif all(d < 0 for d in dirs): alignment = "all_bearish"; score = max(5, 30 + avg * 15)
            elif avg > 0.5: alignment = "mostly_bullish"; score = 60 + avg * 10
            elif avg < -0.5: alignment = "mostly_bearish"; score = 30 + avg * 10
            else: alignment = "mixed"; score = 45
            passed = score >= 40
            reason = f"MTF: {alignment} (avg={round(avg, 2)}, timeframes={count})"
            r = (passed, score, reason, details); self._set_cache(ck, r); return r
        except Exception as e:
            r = (True, 50.0, f"MTF error: {e}", {}); self._set_cache(ck, r); return r

    def check_smart_money_technical(self, candidate, smart_money_data=None, tv_data=None):
        ck = f"smt:{candidate.address}"
        c = self._get_cache(ck)
        if c: return c
        score = 0.0; reasons = []
        if smart_money_data is None:
            try:
                from src.smart_money_detector import SmartMoneyDetector
                det = SmartMoneyDetector(event_bus=self.event_bus)
                recent = det.get_recent_signals(hours=1)
                sigs = [s for s in recent if s.get("token_address") == candidate.address]
                if sigs: smart_money_data = sigs[-1]
            except: pass
        if tv_data is None:
            try:
                from src.tradingview_feed import get_tradingview_feed
                feed = get_tradingview_feed(event_bus=self.event_bus)
                tv_sym = self._resolve_tv_symbol(candidate.symbol)
                if tv_sym: tv_data = feed.get_analysis(tv_sym, interval="1h")
            except: pass
        sm_signal = "NONE"
        if smart_money_data:
            buying = smart_money_data.get("wallets_buying", 0)
            selling = smart_money_data.get("wallets_selling", 0)
            conf = smart_money_data.get("confidence", 0)
            if buying > selling and buying >= 2:
                sm_signal = "BUY"; score += 30 + min(20, buying * 5)
                reasons.append(f"Smart money BUY: {buying} wallets")
            elif selling > buying and selling >= 2:
                sm_signal = "SELL"; score -= 30 + min(20, selling * 5)
                reasons.append(f"Smart money SELL: {selling} wallets")
        tv_signal = "NEUTRAL"
        if tv_data and not tv_data.get("error"):
            rec = tv_data.get("recommendation", "NEUTRAL"); tv_signal = rec
            tv_scores = {"STRONG_BUY": 25, "BUY": 15, "NEUTRAL": 0, "SELL": -15, "STRONG_SELL": -25}
            score += tv_scores.get(rec, 0); reasons.append(f"TV: {rec}")
        if sm_signal == "BUY" and tv_signal in ("BUY", "STRONG_BUY"):
            score += 20; reasons.append("CONVERGENCE: Both bullish!")
        elif sm_signal == "SELL" and tv_signal in ("SELL", "STRONG_SELL"):
            score -= 20; reasons.append("CONVERGENCE: Both bearish")
        normalized = max(0, min(100, 50 + score))
        passed = normalized >= 40
        r = (passed, normalized, "; ".join(reasons)); self._set_cache(ck, r); return r

    def check_ohlcv_validation(self, candidate):
        ck = f"ohlcv:{candidate.address}:{candidate.symbol}"
        c = self._get_cache(ck)
        if c: return c
        tv_sym = self._resolve_tv_symbol(candidate.symbol)
        if not tv_sym:
            r = (True, False, 0.0, "Cannot resolve symbol"); self._set_cache(ck, r); return r
        try:
            from src.tradingview_feed import get_tradingview_feed
            feed = get_tradingview_feed(event_bus=self.event_bus)
            ohlcv = feed.get_ohlcv_candles(symbol=tv_sym, interval="1h", limit=24)
            if ohlcv.get("error") or not ohlcv.get("candles"):
                r = (True, False, 0.0, "No OHLCV data"); self._set_cache(ck, r); return r
            candles = ohlcv["candles"]
            if len(candles) < 5:
                r = (True, False, 0.0, "Insufficient candles"); self._set_cache(ck, r); return r
            vols = [c.get("volume", 0) for c in candles]
            avg_vol = sum(vols) / len(vols); recent_vol = sum(vols[-3:]) / 3
            vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0
            vol_confirmed = vol_ratio > 1.5; validated = True
            reported = getattr(candidate, "volume_24h", 0)
            if reported > 100000 and vol_ratio < 0.5:
                validated = False; reason = "SUSPICIOUS: vol mismatch"
            elif vol_ratio > 3.0: reason = f"Volume spike: {vol_ratio:.1f}x"
            elif vol_ratio > 1.5: reason = f"Volume expanding: {vol_ratio:.1f}x"
            else: reason = f"Volume stable: {vol_ratio:.1f}x"
            r = (validated, vol_confirmed, vol_ratio, reason); self._set_cache(ck, r); return r
        except Exception as e:
            r = (True, False, 0.0, f"OHLCV error: {e}"); self._set_cache(ck, r); return r

    def check_cross_market(self):
        ck = "cross_market"
        c = self._get_cache(ck)
        if c: return c
        try:
            from src.tradingview_feed import get_tradingview_feed
            feed = get_tradingview_feed(event_bus=self.event_bus)
            macros = {}
            for name, sym, exch, scr in [("gold", "XAUUSD", "FX_IDC", "forex"),
                ("dxy", "DXY", "TVC", "forex"), ("btc", "BTCUSDT", "BINANCE", "crypto")]:
                try:
                    a = feed.get_analysis(sym, exchange=exch, screener=scr, interval="1h")
                    if not a.get("error"): macros[name] = a.get("recommendation", "NEUTRAL")
                except: pass
            rs = 0
            if macros.get("btc") in ("BUY", "STRONG_BUY"): rs += 2
            elif macros.get("btc") in ("SELL", "STRONG_SELL"): rs -= 2
            if macros.get("gold") in ("SELL", "STRONG_SELL"): rs += 1
            elif macros.get("gold") in ("BUY", "STRONG_BUY"): rs -= 1
            if macros.get("dxy") in ("SELL", "STRONG_SELL"): rs += 1
            elif macros.get("dxy") in ("BUY", "STRONG_BUY"): rs -= 1
            if rs >= 2: signal, score = "risk_on", 75 + min(20, rs * 5)
            elif rs <= -2: signal, score = "risk_off", 25 + max(-20, rs * 5)
            else: signal, score = "neutral", 50
            r = (signal, score, f"Macro: {signal}")
            self._set_cache(ck, r); return r
        except Exception as e:
            r = ("neutral", 50.0, f"Error: {e}"); self._set_cache(ck, r); return r

    def check(self, candidate, smart_money_data=None):
        result = ConfluenceResult(token_address=candidate.address, symbol=candidate.symbol,
            timestamp=datetime.now(timezone.utc).isoformat())
        try: p,s,r = self.check_confirmation(candidate); result.confirmation_passed=p; result.confirmation_score=s; result.confirmation_reason=r
        except Exception as e: result.confirmation_reason=str(e)
        try:
            tv_sym = self._resolve_tv_symbol(candidate.symbol)
            if tv_sym:
                p,s,r,d = self.check_multi_timeframe(tv_sym)
                result.mtf_passed=p; result.mtf_score=s
                result.mtf_alignment = r.split("MTF: ")[1].split(" (")[0] if "MTF:" in r else "unknown"
                result.mtf_details=d
        except: pass
        try: p,s,r = self.check_smart_money_technical(candidate, smart_money_data=smart_money_data); result.sm_technical_passed=p; result.sm_technical_score=s; result.sm_signal="BUY" if s>60 else "SELL" if s<40 else "NEUTRAL"
        except: pass
        try: v,vc,vr,_ = self.check_ohlcv_validation(candidate); result.ohlcv_validated=v; result.ohlcv_volume_confirmed=vc; result.ohlcv_volume_ratio=vr
        except: pass
        try: ms,mscore,_ = self.check_cross_market(); result.macro_signal=ms; result.macro_score=mscore
        except: pass
        w = {"c": 0.25, "m": 0.25, "s": 0.25, "o": 0.15, "x": 0.10}
        scores = [result.confirmation_score*w["c"], result.mtf_score*w["m"], result.sm_technical_score*w["s"],
            (80 if result.ohlcv_validated else 30)*w["o"], result.macro_score*w["x"]]
        result.final_score = sum(scores)
        result.rejection_reasons = []
        if not result.confirmation_passed: result.rejection_reasons.append("Confirmation failed")
        if not result.mtf_passed: result.rejection_reasons.append(f"MTF failed: {result.mtf_alignment}")
        if not result.sm_technical_passed: result.rejection_reasons.append("SM+Tech failed")
        if not result.ohlcv_validated: result.rejection_reasons.append("OHLCV failed")
        gates = sum([result.confirmation_passed, result.mtf_passed, result.sm_technical_passed, result.ohlcv_validated, result.macro_score >= 40])
        if gates >= 4: result.confluence_level="strong"; result.final_signal="BUY"; result.should_trade=True
        elif gates >= 3 and result.final_score >= 55: result.confluence_level="moderate"; result.final_signal="BUY"; result.should_trade=True
        elif gates >= 2 and result.final_score >= 60: result.confluence_level="weak"
        else: result.confluence_level="none"
        if result.macro_signal == "risk_off" and result.confluence_level != "strong":
            result.should_trade=False; result.rejection_reasons.append("Risk-off macro")
        self._emit_event("confluence/checked", {"token": candidate.address, "symbol": candidate.symbol,
            "final_score": round(result.final_score, 2), "final_signal": result.final_signal,
            "should_trade": result.should_trade, "confluence_level": result.confluence_level,
            "gates_passed": gates, "timestamp": result.timestamp})
        try:
            lp = DATA_DIR / "confluence_results.jsonl"
            with open(lp, "a") as f: f.write(json.dumps(result.to_dict(), default=str) + chr(10))
        except: pass
        return result


_instance = None

def get_trading_confluence(event_bus=None):
    global _instance
    if _instance is None: _instance = TradingConfluence(event_bus=event_bus)
    return _instance

async def tool_tv_confluence_check(token_address, symbol=""):
    confluence = get_trading_confluence()
    class Mock:
        def __init__(self, a, s):
            self.address=a; self.symbol=s; self.price_change_1h=0; self.volume_24h=0
    return confluence.check(Mock(token_address, symbol or token_address[:8])).to_dict()

async def tool_tv_macro_context():
    c = get_trading_confluence(); s, sc, r = c.check_cross_market()
    return {"signal": s, "score": round(sc, 1), "reason": r, "source": "trading_confluence"}

async def tool_tv_mtf_check(symbol):
    c = get_trading_confluence(); p, s, r, d = c.check_multi_timeframe(symbol)
    return {"symbol": symbol, "passed": p, "score": round(s, 1), "reason": r, "details": d, "source": "trading_confluence"}