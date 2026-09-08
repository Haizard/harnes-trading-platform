"""Shared chart snapshot and overlay contracts for native chart clients."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def normalize_confidence(value: object) -> float | None:
    """Normalize numeric and textual confidence values to the range [0, 1].
    
    Handles common formats:
    - Numeric: 0.72, 72
    - Percentage strings: '72%', 'Confidence: 72%'
    - Ranges: '70-80%' (takes midpoint)
    - Returns None for unparseable values
    """
    if value is None or isinstance(value, bool):
        return None
    raw = value
    try:
        if isinstance(value, str):
            text = value.strip().lower()
            percent = "%" in text
            
            # Handle ranges like '70-80%' by taking midpoint
            range_match = __import__("re").search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", text)
            if range_match:
                num1 = float(range_match.group(1))
                num2 = float(range_match.group(2))
                number = (num1 + num2) / 2
                if percent or number > 1:
                    number /= 100
            else:
                # Single number pattern
                match = __import__("re").search(r"-?\d+(?:\.\d+)?", text)
                if not match:
                    return None
                number = float(match.group())
                if percent or number > 1:
                    number /= 100
        else:
            number = float(value)
            if number > 1:
                number /= 100
    except (TypeError, ValueError):
        return None
    normalized = max(0.0, min(1.0, number))
    return round(normalized, 4)


_ALLOWED_CONTRIBUTION_TYPES = {"marker", "price_zone", "horizontal_level", "text_note", "panel_signal"}


def normalize_chart_contributions(contributions: object, source: str = "bedrock") -> list["ChartOverlay"]:
    """Validate model/agent chart contributions against the closed overlay contract."""
    if not isinstance(contributions, list):
        return []
    normalized = []
    for index, item in enumerate(contributions[:40]):
        if not isinstance(item, dict) or item.get("type") not in _ALLOWED_CONTRIBUTION_TYPES:
            continue
        try:
            normalized.append(ChartOverlay(
                id=str(item.get("id") or f"{source}-{index}"),
                source=source,
                type=item["type"],
                label=str(item.get("label") or item.get("text") or ""),
                bucket_time=item.get("bucket_time"),
                price=float(item["price"]) if item.get("price") is not None else None,
                price_low=float(item["price_low"]) if item.get("price_low") is not None else None,
                price_high=float(item["price_high"]) if item.get("price_high") is not None else None,
                side=item.get("side"),
                color=str(item.get("color") or "#ad9aff"),
                confidence=item.get("confidence"),
                severity=str(item.get("severity") or "info"),
                panel=item.get("panel"),
                details={"raw_contribution": item},
            ))
        except (TypeError, ValueError):
            continue
    return normalized


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

    def __post_init__(self) -> None:
        if self.confidence is not None:
            self.details.setdefault("raw_confidence", self.confidence)
            self.confidence = normalize_confidence(self.confidence)


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
