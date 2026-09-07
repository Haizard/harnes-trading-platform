"""Pure Binance footprint aggregation for research and chart delivery."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Iterable


def _bucket_time(event_time: datetime, interval_seconds: int) -> datetime:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    timestamp = event_time.timestamp()
    bucket_timestamp = timestamp - (timestamp % interval_seconds)
    return datetime.fromtimestamp(bucket_timestamp, tz=event_time.tzinfo)


def _price_level(price: Decimal, tick_size: Decimal | None) -> Decimal:
    if tick_size is None or tick_size <= 0:
        return price
    return (price / tick_size).to_integral_value(rounding=ROUND_DOWN) * tick_size


def aggregate_trades(
    trades: Iterable[dict],
    interval_seconds: int = 60,
    tick_size: str | Decimal | None = None,
) -> list[dict]:
    """Aggregate normalized Binance trades into footprint price levels.

    Each trade must contain ``event_time``, ``price``, ``quantity``, and
    ``aggressor_side`` (BUY or SELL). The raw trade rows remain the audit source.
    """
    normalized_tick = Decimal(str(tick_size)) if tick_size is not None else None
    levels = defaultdict(lambda: {"buy_volume": Decimal("0"), "sell_volume": Decimal("0"), "trade_count": 0})

    for trade in trades:
        event_time = trade["event_time"]
        if not isinstance(event_time, datetime):
            raise TypeError("event_time must be a datetime")
        bucket = _bucket_time(event_time, interval_seconds)
        price = _price_level(Decimal(str(trade["price"])), normalized_tick)
        quantity = Decimal(str(trade["quantity"]))
        side = trade["aggressor_side"].upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("aggressor_side must be BUY or SELL")

        level = levels[(bucket, price)]
        level["buy_volume" if side == "BUY" else "sell_volume"] += quantity
        level["trade_count"] += 1

    result = []
    for (bucket, price), level in sorted(levels.items()):
        buy_volume = level["buy_volume"]
        sell_volume = level["sell_volume"]
        result.append({
            "bucket_time": bucket,
            "price": price,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "delta": buy_volume - sell_volume,
            "total_volume": buy_volume + sell_volume,
            "trade_count": level["trade_count"],
        })
    return result


def aggregate_ohlc(
    trades: Iterable[dict],
    interval_seconds: int = 60,
) -> list[dict]:
    """Build true OHLC bars from trade arrival order."""
    bars = {}
    for trade in trades:
        event_time = trade["event_time"]
        if not isinstance(event_time, datetime):
            raise TypeError("event_time must be a datetime")
        bucket = _bucket_time(event_time, interval_seconds)
        price = Decimal(str(trade["price"]))
        bar = bars.get(bucket)
        if bar is None:
            bars[bucket] = {"bucket_time": bucket, "open": price, "high": price, "low": price, "close": price}
        else:
            bar["high"] = max(bar["high"], price)
            bar["low"] = min(bar["low"], price)
            bar["close"] = price

    return sorted(bars.values(), key=lambda bar: bar["bucket_time"])
