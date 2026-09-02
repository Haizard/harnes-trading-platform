"""
TradingView Data Feed - OHLCV candles, technical indicators, and market screener.

DSH Pattern: EventBus -> DB -> Singleton

Free tier compatible: tradingview-ta + tradingview-screener (no auth needed)
"""

import os, time, asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List


class TradingViewFeed:
    """DSH-compliant TradingView data feed."""

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._cache = {}
        self._cache_ttl = 60

    def _emit_event(self, event_name, payload):
        try:
            from src.db_storage import log_event
            log_event(event_name, payload)
        except: pass
        if self.event_bus:
            try: asyncio.ensure_future(self.event_bus.emit(event_name, payload))
            except: pass

    def _get_cache(self, key):
        if key in self._cache:
            ts, data = self._cache[key]
            if time.time() - ts < self._cache_ttl: return data
        return None

    def _set_cache(self, key, data):
        self._cache[key] = (time.time(), data)
    def get_analysis(self, symbol, exchange="BINANCE", screener="crypto", interval="1h"):
        cache_key = f"ta:{symbol}:{exchange}:{screener}:{interval}"
        cached = self._get_cache(cache_key)
        if cached: return cached
        try:
            from tradingview_ta import TA_Handler, Interval
            imap = {"1m": Interval.INTERVAL_1_MINUTE, "5m": Interval.INTERVAL_5_MINUTES,
                    "15m": Interval.INTERVAL_15_MINUTES, "1h": Interval.INTERVAL_1_HOUR,
                    "4h": Interval.INTERVAL_4_HOURS, "1d": Interval.INTERVAL_1_DAY}
            handler = TA_Handler(symbol=symbol, exchange=exchange, screener=screener,
                                  interval=imap.get(interval, Interval.INTERVAL_1_HOUR), timeout=10)
            a = handler.get_analysis()
            result = {"symbol": symbol, "exchange": exchange, "screener": screener,
                      "interval": interval, "timestamp": datetime.now(timezone.utc).isoformat(),
                      "recommendation": a.summary.get("RECOMMENDATION", "NEUTRAL"),
                      "summary": a.summary, "oscillators": a.oscillators,
                      "moving_averages": a.moving_averages,
                      "indicators": {
                          "open": a.indicators.get("open"), "close": a.indicators.get("close"),
                          "high": a.indicators.get("high"), "low": a.indicators.get("low"),
                          "volume": a.indicators.get("volume"), "change_pct": a.indicators.get("change"),
                          "RSI": a.indicators.get("RSI"), "RSI_1": a.indicators.get("RSI[1]"),
                          "MACD": a.indicators.get("MACD.macd"), "MACD_signal": a.indicators.get("MACD.signal"),
                          "Stoch_K": a.indicators.get("Stoch.K"), "Stoch_D": a.indicators.get("Stoch.D"),
                          "CCI": a.indicators.get("CCI20"), "ADX": a.indicators.get("ADX"),
                          "BB_lower": a.indicators.get("BB.lower"), "BB_upper": a.indicators.get("BB.upper"),
                          "EMA20": a.indicators.get("EMA20"), "EMA50": a.indicators.get("EMA50"),
                          "EMA200": a.indicators.get("EMA200"), "SMA50": a.indicators.get("SMA50"),
                          "SMA200": a.indicators.get("SMA200"), "VWMA": a.indicators.get("VWMA"),
                      }, "source": "tradingview"}
            self._set_cache(cache_key, result)
            self._emit_event("tradingview/analysis", {"symbol": symbol, "interval": interval,
                                  "recommendation": result["recommendation"], "timestamp": result["timestamp"]})
            return result
        except Exception as e:
            return {"symbol": symbol, "error": str(e), "source": "tradingview"}
    def get_multi_analysis(self, symbols, screener="crypto", interval="1h"):
        try:
            from tradingview_ta import get_multiple_analysis, Interval
            imap = {"1m": Interval.INTERVAL_1_MINUTE, "5m": Interval.INTERVAL_5_MINUTES,
                    "1h": Interval.INTERVAL_1_HOUR, "4h": Interval.INTERVAL_4_HOURS, "1d": Interval.INTERVAL_1_DAY}
            raw = get_multiple_analysis(screener=screener,
                interval=imap.get(interval, Interval.INTERVAL_1_HOUR), symbols=symbols)
            results = {}
            for key, a in raw.items():
                if a is None: results[key] = {"error": "No data"}; continue
                results[key] = {"symbol": key,
                    "recommendation": a.summary.get("RECOMMENDATION", "NEUTRAL"),
                    "RSI": a.indicators.get("RSI"), "MACD": a.indicators.get("MACD.macd"),
                    "close": a.indicators.get("close"), "volume": a.indicators.get("volume"),
                    "change_pct": a.indicators.get("change")}
            self._emit_event("tradingview/multi_analysis", {"symbols": symbols,
                "count": len(results), "timestamp": datetime.now(timezone.utc).isoformat()})
            return {"results": results, "count": len(results), "source": "tradingview"}
        except Exception as e:
            return {"error": str(e), "symbols": symbols, "source": "tradingview"}

    def scan_market(self, market="crypto", limit=20, min_volume=0):
        try:
            from tradingview_screener import Query, col
            if market == "crypto":
                from tradingview_screener import crypto; q = crypto()
            elif market in ("stocks", "america"):
                from tradingview_screener import stocks; q = stocks("america")
            elif market == "forex":
                from tradingview_screener import forex; q = forex()
            else: q = Query()
            q = q.select("name", "close", "volume", "market_cap_basic",
                "change", "Recommend.All", "RSI", "MACD.macd", "MACD.signal")
            if min_volume > 0: q = q.where(col("volume") > min_volume)
            q = q.order_by("volume", ascending=False).limit(limit)
            count, df = q.get_scanner_data()
            results = []
            for _, row in df.iterrows():
                results.append({"name": str(row.get("name","")),
                    "close": float(row.get("close",0)), "volume": float(row.get("volume",0)),
                    "market_cap": float(row.get("market_cap_basic",0) or 0),
                    "change_pct": float(row.get("change",0) or 0),
                    "RSI": float(row.get("RSI",0) or 0),
                    "MACD": float(row.get("MACD.macd",0) or 0)})
            self._emit_event("tradingview/screener_scan", {"market": market,
                "count": len(results), "timestamp": datetime.now(timezone.utc).isoformat()})
            return {"results": results, "total": count, "market": market, "source": "tradingview"}
        except Exception as e:
            return {"error": str(e), "market": market, "source": "tradingview"}
    def get_ohlcv_candles(self, symbol="SOLUSDT", interval="1h", limit=100):
        """Get OHLCV candle data from Binance (free, no auth)."""
        try:
            import requests as req
            url = "https://api.binance.com/api/v3/klines"
            r = req.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=10)
            if r.status_code != 200:
                return {"error": f"Binance API error: {r.status_code}", "source": "binance"}
            raw = r.json()
            candles = []
            for k in raw:
                candles.append({
                    "time": int(k[0]) // 1000,  # Unix seconds
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            self._emit_event("tradingview/ohlcv_fetch", {
                "symbol": symbol, "interval": interval, "count": len(candles),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return {"candles": candles, "symbol": symbol, "interval": interval,
                    "count": len(candles), "source": "binance"}
        except Exception as e:
            return {"error": str(e), "symbol": symbol, "source": "binance"}

    def search_symbol(self, query):
        try:
            from tradingview_ta import TradingView
            results = TradingView.search(query)
            self._emit_event("tradingview/symbol_search", {"query": query,
                "results_count": len(results), "timestamp": datetime.now(timezone.utc).isoformat()})
            return {"results": results, "count": len(results), "source": "tradingview"}
        except Exception as e:
            return {"error": str(e), "query": query, "source": "tradingview"}

    def get_market_overview(self):
        pairs = [("BTCUSDT","BINANCE","crypto"), ("ETHUSDT","BINANCE","crypto"), ("SOLUSDT","BINANCE","crypto")]
        results = {}
        for sym, exc, scr in pairs:
            try:
                a = self.get_analysis(sym, exc, scr, "1h")
                results[sym] = {"recommendation": a.get("recommendation","NEUTRAL"),
                    "close": a.get("indicators",{}).get("close"),
                    "RSI": a.get("indicators",{}).get("RSI"),
                    "MACD": a.get("indicators",{}).get("MACD"),
                    "change_pct": a.get("indicators",{}).get("change_pct")}
            except Exception as e: results[sym] = {"error": str(e)}
        self._emit_event("tradingview/market_overview", {"pairs": list(results.keys()),
            "timestamp": datetime.now(timezone.utc).isoformat()})
        return {"markets": results, "source": "tradingview"}


_feed_instance = None

def get_tradingview_feed(event_bus=None):
    global _feed_instance
    if _feed_instance is None: _feed_instance = TradingViewFeed(event_bus=event_bus)
    return _feed_instance


async def tool_tv_get_analysis(symbol, exchange="BINANCE", screener="crypto", interval="1h"):
    return get_tradingview_feed().get_analysis(symbol, exchange, screener, interval)

async def tool_tv_multi_analysis(symbols, screener="crypto", interval="1h"):
    return get_tradingview_feed().get_multi_analysis([s.strip() for s in symbols.split(",")], screener, interval)

async def tool_tv_scan_market(market="crypto", limit=20, min_volume=0):
    return get_tradingview_feed().scan_market(market, limit, min_volume)

async def tool_tv_search_symbol(query):
    return get_tradingview_feed().search_symbol(query)

async def tool_tv_get_ohlcv(symbol="SOLUSDT", interval="1h", limit=100):
    return get_tradingview_feed().get_ohlcv_candles(symbol, interval, limit)

async def tool_tv_market_overview():
    return get_tradingview_feed().get_market_overview()