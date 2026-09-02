
"""Pine Script Backtest Pipeline - DSH Pattern: EventBus -> DB -> Singleton"""

import os, time, json
from datetime import datetime, timezone
from typing import Dict, Any, List
from pathlib import Path
from src.event_bus import _fire_and_forget

DATA_DIR = Path("src/data/pine_backtest")
DATA_DIR.mkdir(parents=True, exist_ok=True)


class PineBacktestPipeline:
    def __init__(self, event_bus=None):
        self.event_bus = event_bus

    def _emit_event(self, event_name, payload):
        try:
            from src.db_storage import log_event
            log_event(event_name, payload)
        except: pass
        if self.event_bus:
            try: _fire_and_forget(self.event_bus.emit(event_name, payload))
            except: pass

    def get_strategy_signals(self, symbol):
        try:
            from src.tradingview_feed import get_tradingview_feed
            feed = get_tradingview_feed(event_bus=self.event_bus)
            ohlcv = feed.get_ohlcv_candles(symbol=symbol, interval="1h", limit=100)
            if ohlcv.get("error") or not ohlcv.get("candles"):
                return {"signal": "HOLD", "reason": "No OHLCV data", "strategies": []}
            candles = ohlcv["candles"]
            if len(candles) < 30:
                return {"signal": "HOLD", "reason": "Insufficient candles", "strategies": []}
            closes = [c["close"] for c in candles]
            volumes = [c["volume"] for c in candles]
            signals = [
                self._check_rsi(closes),
                self._check_ema(closes),
                self._check_macd_bb(closes),
                self._check_vol_breakout(closes, volumes),
            ]
            buy_count = sum(1 for s in signals if s["signal"] == "BUY")
            sell_count = sum(1 for s in signals if s["signal"] == "SELL")
            total = len(signals)
            if buy_count >= 3: final, conf = "STRONG_BUY", 0.85
            elif buy_count >= 2: final, conf = "BUY", 0.65
            elif sell_count >= 3: final, conf = "STRONG_SELL", 0.85
            elif sell_count >= 2: final, conf = "SELL", 0.65
            else: final, conf = "HOLD", 0.5
            result = {"symbol": symbol, "signal": final, "confidence": round(conf, 3),
                      "buy_strategies": buy_count, "sell_strategies": sell_count,
                      "strategies": signals, "source": "pine_backtest_pipeline",
                      "timestamp": datetime.now(timezone.utc).isoformat()}
            self._emit_event("pine_backtest/result", {"symbol": symbol, "signal": final,
                "confidence": conf, "timestamp": datetime.now(timezone.utc).isoformat()})
            return result
        except Exception as e:
            return {"signal": "HOLD", "reason": str(e), "strategies": []}

    def _ema(self, data, period):
        k = 2 / (period + 1)
        r = [data[0]]
        for v in data[1:]: r.append(v * k + r[-1] * (1 - k))
        return r

    def _check_rsi(self, closes):
        if len(closes) < 15: return {"name": "RSI_Reversion", "signal": "HOLD", "reason": "insufficient data"}
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(0, d)); losses.append(max(0, -d))
        ag = sum(gains[-14:]) / 14; al = sum(losses[-14:]) / 14
        rsi = 100 if al == 0 else 100 - (100 / (1 + ag / al))
        if rsi < 30: return {"name": "RSI_Reversion", "signal": "BUY", "reason": "RSI oversold (%.1f)" % rsi}
        elif rsi > 70: return {"name": "RSI_Reversion", "signal": "SELL", "reason": "RSI overbought (%.1f)" % rsi}
        return {"name": "RSI_Reversion", "signal": "HOLD", "reason": "RSI neutral (%.1f)" % rsi}

    def _check_ema(self, closes):
        if len(closes) < 25: return {"name": "EMA_Crossover", "signal": "HOLD", "reason": "insufficient data"}
        f, s = self._ema(closes, 9), self._ema(closes, 21)
        if f[-1] > s[-1] and f[-2] <= s[-2]: return {"name": "EMA_Crossover", "signal": "BUY", "reason": "Fast crossed above slow"}
        elif f[-1] < s[-1] and f[-2] >= s[-2]: return {"name": "EMA_Crossover", "signal": "SELL", "reason": "Fast crossed below slow"}
        elif f[-1] > s[-1]: return {"name": "EMA_Crossover", "signal": "BUY", "reason": "Fast above slow"}
        elif f[-1] < s[-1]: return {"name": "EMA_Crossover", "signal": "SELL", "reason": "Fast below slow"}
        return {"name": "EMA_Crossover", "signal": "HOLD", "reason": "EMAs flat"}

    def _check_macd_bb(self, closes):
        if len(closes) < 30: return {"name": "MACD_Bollinger", "signal": "HOLD", "reason": "insufficient data"}
        ema12, ema26 = self._ema(closes, 12), self._ema(closes, 26)
        macd_l = [a - b for a, b in zip(ema12, ema26)]
        sig_l = self._ema(macd_l, 9)
        sma = sum(closes[-20:]) / 20
        std = (sum((c - sma) ** 2 for c in closes[-20:]) / 20) ** 0.5
        p = closes[-1]
        if p < sma - 2 * std and macd_l[-1] > sig_l[-1]: return {"name": "MACD_Bollinger", "signal": "BUY", "reason": "Below lower BB + MACD bullish"}
        elif p > sma + 2 * std and macd_l[-1] < sig_l[-1]: return {"name": "MACD_Bollinger", "signal": "SELL", "reason": "Above upper BB + MACD bearish"}
        return {"name": "MACD_Bollinger", "signal": "HOLD", "reason": "No clear signal"}

    def _check_vol_breakout(self, closes, volumes):
        if len(closes) < 20 or len(volumes) < 20: return {"name": "Vol_Breakout", "signal": "HOLD", "reason": "insufficient data"}
        avg = sum(volumes[-20:]) / 20; recent = sum(volumes[-3:]) / 3
        vr = recent / avg if avg > 0 else 1.0
        pc = (closes[-1] - closes[-3]) / closes[-3] * 100 if closes[-3] > 0 else 0
        if pc > 5 and vr > 2: return {"name": "Vol_Breakout", "signal": "BUY", "reason": "Vol spike %.1fx + price up %.1f%%" % (vr, pc)}
        elif pc < -5 and vr > 2: return {"name": "Vol_Breakout", "signal": "SELL", "reason": "Vol spike %.1fx + price down %.1f%%" % (vr, pc)}
        return {"name": "Vol_Breakout", "signal": "HOLD", "reason": "No volume breakout"}


_instance = None

def get_pine_backtest_pipeline(event_bus=None):
    global _instance
    if _instance is None: _instance = PineBacktestPipeline(event_bus=event_bus)
    return _instance

async def tool_pine_backtest_check(symbol):
    return get_pine_backtest_pipeline().get_strategy_signals(symbol)

async def tool_pine_backtest_strategies():
    return {"strategies": ["RSI_Reversion", "EMA_Crossover", "MACD_Bollinger", "Vol_Breakout"],
            "count": 4, "source": "pine_backtest_pipeline"}
