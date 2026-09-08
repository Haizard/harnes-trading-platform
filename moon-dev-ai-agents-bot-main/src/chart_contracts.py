"""Shared chart snapshot and overlay contracts for native chart clients."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ChartOverlay:
    """A renderable contribution anchored to time, price, or a panel."""

    id: str
    source: str
    type: str
    label: str = ""
    bucket_time: str | None = None
    price: float | None = None
    price_low: float | None = None
    price_high: float | None = None
    side: str | None = None
    color: str = "#58d9e8"
    confidence: float | None = None
    severity: str = "info"
    panel: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChartSnapshot:
    """Normalized chart context shared by renderers, agents, and AI."""

    symbol: str
    timeframe: str
    as_of: str
    source: str = "binance"
    candles: list[dict[str, Any]] = field(default_factory=list)
    footprint: dict[str, Any] = field(default_factory=dict)
    order_book: dict[str, Any] = field(default_factory=dict)
    indicators: list[dict[str, Any]] = field(default_factory=list)
    overlays: list[ChartOverlay] = field(default_factory=list)
    agent_signals: list[dict[str, Any]] = field(default_factory=list)
    risk_context: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["overlays"] = [asdict(overlay) for overlay in self.overlays]
        return result


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timeframe_label(interval_seconds: int) -> str:
    if interval_seconds % 3600 == 0:
        return f"{interval_seconds // 3600}h"
    if interval_seconds % 60 == 0:
        return f"{interval_seconds // 60}m"
    return f"{interval_seconds}s"
