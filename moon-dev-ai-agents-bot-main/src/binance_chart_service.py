"""Compose normalized Binance chart snapshots for UI and AI consumers."""

from collections import defaultdict
from typing import Any

from src.binance_footprint import aggregate_ohlc, aggregate_trades, detect_order_flow_signals
from src.chart_contracts import ChartOverlay, ChartSnapshot, timeframe_label, utc_now


class BinanceChartService:
    """Build one bounded chart context from normalized market data."""

    def build_snapshot(
        self,
        symbol: str,
        trades: list[dict[str, Any]],
        interval_seconds: int,
        tick_size: str | None,
        order_book: dict[str, Any] | None = None,
        agent_signals: list[dict[str, Any]] | None = None,
    ) -> ChartSnapshot:
        levels = aggregate_trades(trades, interval_seconds, tick_size)
        candles = aggregate_ohlc(trades, interval_seconds)
        signals = detect_order_flow_signals(levels, candles)
        serialized_levels = [self._serialize_level(level) for level in levels]
        serialized_candles = [self._serialize_candle(candle) for candle in candles]
        serialized_signals = [
            {**signal, "bucket_time": signal["bucket_time"].isoformat()}
            for signal in signals
        ]
        overlays = self._signal_overlays(serialized_signals)
        profile = self._volume_profile(serialized_levels)
        delta = sum(float(level["delta"]) for level in serialized_levels)
        buy_volume = sum(float(level["buy_volume"]) for level in serialized_levels)
        sell_volume = sum(float(level["sell_volume"]) for level in serialized_levels)
        order_book = order_book or {"bids": [], "asks": [], "available": False}
        bid_depth = sum(float(level.get("quantity", 0)) for level in order_book.get("bids", []))
        ask_depth = sum(float(level.get("quantity", 0)) for level in order_book.get("asks", []))

        agent_signals = agent_signals or []
        return ChartSnapshot(
            symbol=symbol.upper(),
            timeframe=timeframe_label(interval_seconds),
            as_of=utc_now(),
            candles=serialized_candles,
            footprint={
                "levels": serialized_levels,
                "signals": serialized_signals,
                "buy_volume": buy_volume,
                "sell_volume": sell_volume,
                "delta": delta,
                "volume_profile": profile,
                "poc": profile.get("poc"),
            },
            order_book={
                **order_book,
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
                "imbalance": self._ratio(bid_depth, ask_depth),
            },
            overlays=overlays + self._agent_overlays(agent_signals),
            agent_signals=agent_signals,
            warnings=[] if trades else ["No Binance trades were available for this window."],
        )

    @staticmethod
    def _serialize_level(level: dict[str, Any]) -> dict[str, Any]:
        return {
            "bucket_time": level["bucket_time"].isoformat(),
            "price": float(level["price"]),
            "buy_volume": float(level["buy_volume"]),
            "sell_volume": float(level["sell_volume"]),
            "delta": float(level["delta"]),
            "total_volume": float(level["total_volume"]),
            "trade_count": level["trade_count"],
        }

    @staticmethod
    def _serialize_candle(candle: dict[str, Any]) -> dict[str, Any]:
        return {
            "bucket_time": candle["bucket_time"].isoformat(),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
        }

    @staticmethod
    def _ratio(left: float, right: float) -> float | None:
        total = left + right
        return round((left - right) / total, 4) if total else None

    @staticmethod
    def _volume_profile(levels: list[dict[str, Any]]) -> dict[str, Any]:
        by_price: dict[float, float] = defaultdict(float)
        for level in levels:
            by_price[level["price"]] += level["total_volume"]
        if not by_price:
            return {"poc": None, "levels": []}
        ordered = sorted(by_price.items(), key=lambda item: item[1], reverse=True)
        total = sum(by_price.values())
        value_target = total * 0.7
        running = 0.0
        value_prices = []
        for price, volume in sorted(by_price.items()):
            running += volume
            value_prices.append(price)
            if running >= value_target:
                break
        return {
            "poc": ordered[0][0],
            "value_area_low": min(value_prices),
            "value_area_high": max(value_prices),
            "levels": [{"price": price, "volume": volume} for price, volume in ordered[:40]],
        }

    @staticmethod
    def _signal_overlays(signals: list[dict[str, Any]]) -> list[ChartOverlay]:
        overlays = []
        for index, signal in enumerate(signals):
            overlays.append(ChartOverlay(
                id=f"footprint-{signal['type'].lower()}-{index}",
                source="binance_footprint",
                type="signal",
                label=signal["type"].replace("_", " "),
                bucket_time=signal["bucket_time"],
                price=signal.get("price"),
                side=signal.get("side"),
                color="#64e6a0" if signal.get("side") == "BUY" else "#ff7887",
                confidence=min(1.0, float(signal.get("strength", 1)) / 5),
                details=signal,
            ))
        return overlays

    @staticmethod
    def _agent_overlays(signals: list[dict[str, Any]]) -> list[ChartOverlay]:
        overlays = []
        for index, signal in enumerate(signals):
            overlays.append(ChartOverlay(
                id=f"agent-{signal.get('source', 'unknown')}-{index}",
                source=signal.get("source", "agent"),
                type=signal.get("type", "signal"),
                label=signal.get("label", signal.get("text", "Agent signal")),
                bucket_time=signal.get("bucket_time"),
                price=signal.get("price"),
                side=signal.get("direction", signal.get("side")),
                color=signal.get("color", "#ad9aff"),
                confidence=signal.get("confidence", signal.get("strength")),
                details=signal,
            ))
        return overlays
