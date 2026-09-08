"""Deterministic server-side indicator registry for chart snapshots."""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class IndicatorDefinition:
    name: str
    version: str
    warmup: int
    compute: Callable[[list[dict[str, Any]]], dict[str, Any]]


_REGISTRY: dict[str, IndicatorDefinition] = {}


def register_indicator(definition: IndicatorDefinition) -> None:
    _REGISTRY[definition.name] = definition


def list_indicators() -> list[dict[str, Any]]:
    return [{"name": item.name, "version": item.version, "warmup": item.warmup} for item in _REGISTRY.values()]


def compute_indicators(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for definition in _REGISTRY.values():
        series = definition.compute(candles)
        series.update({"name": definition.name, "version": definition.version, "warmup": definition.warmup})
        result.append(series)
    return result


def _ema(period: int) -> Callable[[list[dict[str, Any]]], dict[str, Any]]:
    def compute(candles: list[dict[str, Any]]) -> dict[str, Any]:
        if len(candles) < period:
            return {"values": [], "ready": False}
        multiplier = 2 / (period + 1)
        value = sum(float(candle["close"]) for candle in candles[:period]) / period
        values = [{"time": candles[period - 1]["bucket_time"], "value": value}]
        for candle in candles[period:]:
            value = (float(candle["close"]) - value) * multiplier + value
            values.append({"time": candle["bucket_time"], "value": value})
        return {"values": values, "ready": True}
    return compute


register_indicator(IndicatorDefinition("ema_9", "1", 9, _ema(9)))
register_indicator(IndicatorDefinition("ema_21", "1", 21, _ema(21)))
