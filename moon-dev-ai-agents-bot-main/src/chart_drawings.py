"""Persistent time/price drawing records for the native chart."""

from typing import Any

from src.db_storage import get_events, log_event


_ALLOWED = {"trend_line", "horizontal_level", "measure", "ray", "fibonacci"}


def save_drawing(drawing: dict[str, Any]) -> dict[str, Any]:
    if drawing.get("type") not in _ALLOWED:
        raise ValueError("Unsupported drawing type")
    if not drawing.get("id") or not drawing.get("symbol"):
        raise ValueError("Drawing id and symbol are required")
    record = {key: drawing.get(key) for key in ("id", "type", "symbol", "timeframe", "anchors", "style")}
    log_event("chart_drawing_saved", record)
    return record


def get_drawings(symbol: str, timeframe: str) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    deleted_ids = set()

    for row in get_events("chart_drawing_deleted", 200):
        item = row.get("data", row)
        deleted_id = item.get("id")
        if deleted_id:
            deleted_ids.add(str(deleted_id))

    for row in get_events("chart_drawing_saved", 200):
        item = row.get("data", row)
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if not item_id or item_id in deleted_ids:
            continue
        if item.get("symbol") == symbol and item.get("timeframe") == timeframe:
            records[item_id] = item
    return list(records.values())


def delete_drawing(drawing_id: str) -> bool:
    log_event("chart_drawing_deleted", {"id": drawing_id})
    return True
