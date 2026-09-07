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


def detect_order_flow_signals(levels: Iterable[dict], candles: Iterable[dict], min_ratio: float = 3.0) -> list[dict]:
    """Detect conservative stacked-imbalance and absorption signals."""
    by_bucket = defaultdict(list)
    for level in levels:
        by_bucket[level["bucket_time"]].append(level)

    candle_list = list(candles)
    volumes = defaultdict(Decimal)
    for level in levels:
        volumes[level["bucket_time"]] += Decimal(str(level["total_volume"]))
    average_volume = sum(volumes.values(), Decimal("0")) / max(len(volumes), 1)
    signals = []

    for candle in candle_list:
        bucket = candle["bucket_time"]
        rows = sorted(by_bucket.get(bucket, []), key=lambda row: Decimal(str(row["price"])))
        if not rows:
            continue

        stacked = []
        current_side = None
        current_rows = []
        for row in rows:
            buy = Decimal(str(row["buy_volume"]))
            sell = Decimal(str(row["sell_volume"]))
            side = "BUY" if buy >= sell * Decimal(str(min_ratio)) and buy > 0 else None
            side = "SELL" if sell >= buy * Decimal(str(min_ratio)) and sell > 0 else side
            if side and side == current_side:
                current_rows.append(row)
            else:
                if current_side and len(current_rows) >= 3:
                    stacked.append((current_side, current_rows))
                current_side = side
                current_rows = [row] if side else []
        if current_side and len(current_rows) >= 3:
            stacked.append((current_side, current_rows))

        for side, stack in stacked:
            signals.append({
                "type": "STACKED_IMBALANCE",
                "side": side,
                "bucket_time": bucket,
                "prices": [float(row["price"]) for row in stack],
                "strength": len(stack),
            })

        volume = volumes[bucket]
        candle_range = Decimal(str(candle["high"])) - Decimal(str(candle["low"]))
        reference_price = max(Decimal(str(candle["close"])), Decimal("0.00000001"))
        delta = sum((Decimal(str(row["delta"])) for row in rows), Decimal("0"))
        if volume >= average_volume * Decimal("1.5") and candle_range / reference_price <= Decimal("0.001") and abs(delta) >= volume * Decimal("0.5"):
            signals.append({
                "type": "ABSORPTION",
                "side": "BUY" if delta > 0 else "SELL",
                "bucket_time": bucket,
                "price": float(candle["close"]),
                "strength": float(volume / max(average_volume, Decimal("0.00000001"))),
            })

    return signals
