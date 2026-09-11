"""Public Binance candle history used to seed chart and agent context."""

import json
import urllib.request
from datetime import datetime, timezone


_INTERVAL_NAMES = {
    60: "1m",
    180: "3m",
    300: "5m",
    900: "15m",
    1800: "30m",
    3600: "1h",
    7200: "2h",
    14400: "4h",
    21600: "6h",
    28800: "8h",
    43200: "12h",
    86400: "1d",
}


def fetch_binance_klines(symbol: str, interval_seconds: int, limit: int = 100) -> list[dict]:
    """Fetch bounded OHLCV history for chart warmup and strategy replay."""
    interval = _INTERVAL_NAMES.get(interval_seconds)
    if interval is None:
        raise ValueError(f"Unsupported Binance kline interval: {interval_seconds}")
    params = f"symbol={symbol.upper()}&interval={interval}&limit={max(1, min(int(limit), 1000))}"
    url = f"https://api.binance.com/api/v3/klines?{params}"
    req = urllib.request.Request(url)
    # Bypass proxy env vars by using a direct opener with no proxy
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    resp = opener.open(req, timeout=15)
    raw = json.loads(resp.read())
    candles = []
    for row in raw:
        candles.append({
            "bucket_time": datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        })
    return candles
