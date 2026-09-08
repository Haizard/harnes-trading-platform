"""Persistent time/price drawing records for the native chart."""

from typing import Any

from src.db_storage import get_events, log_event


# Expanded drawing types with support for rectangles, text notes, and more (#5)
_ALLOWED = {
    # Line-based
    "trend_line", "horizontal_level", "ray", "fibonacci", "measure",
    # Shape-based
    "rectangle", "ellipse", "triangle",
    # Text
    "text_note", "price_label",
    # Markers
    "arrow_up", "arrow_down", "diamond", "crosshair",
}

# Default color palette for multi-color support
DEFAULT_COLORS = ["#58d9e8", "#ff7887", "#64e6a0", "#ad9aff", "#ffd700", "#ff6b35"]


def save_drawing(drawing: dict[str, Any]) -> dict[str, Any]:
    if drawing.get("type") not in _ALLOWED:
        raise ValueError("Unsupported drawing type")
    if not drawing.get("id") or not drawing.get("symbol"):
        raise ValueError("Drawing id and symbol are required")
    
    # Extract and validate style with color support
    style = drawing.get("style", {})
    if not style.get("color"):
        style["color"] = DEFAULT_COLORS[0]  # Default color
    
    record = {
        key: drawing.get(key) for key in (
            "id", "type", "symbol", "timeframe", "anchors", "style",
            "text",  # For text notes
            "price_low", "price_high",  # For rectangles
        )
    }
    record["style"] = style
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


def update_drawing(drawing_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Update an existing drawing's properties (move, edit, recolor)."""
    # Get current drawings to find the one to update
    # Note: This logs an update event that the frontend can use to patch the drawing
    update_record = {
        "id": drawing_id,
        **{k: v for k, v in updates.items() if k in (
            "anchors", "style", "text", "price_low", "price_high"
        )}
    }
    log_event("chart_drawing_updated", update_record)
    return update_record


def get_available_colors() -> list[str]:
    """Return the available color palette for drawings."""
    return DEFAULT_COLORS.copy()


def get_drawing_types() -> list[dict[str, str]]:
    """Return available drawing types with their categories."""
    return [
        {"type": "trend_line", "category": "line", "label": "Trend Line"},
        {"type": "horizontal_level", "category": "line", "label": "Horizontal Level"},
        {"type": "ray", "category": "line", "label": "Ray"},
        {"type": "fibonacci", "category": "line", "label": "Fibonacci Retracement"},
        {"type": "measure", "category": "line", "label": "Measure"},
        {"type": "rectangle", "category": "shape", "label": "Rectangle"},
        {"type": "ellipse", "category": "shape", "label": "Ellipse"},
        {"type": "triangle", "category": "shape", "label": "Triangle"},
        {"type": "text_note", "category": "text", "label": "Text Note"},
        {"type": "price_label", "category": "text", "label": "Price Label"},
        {"type": "arrow_up", "category": "marker", "label": "Arrow Up"},
        {"type": "arrow_down", "category": "marker", "label": "Arrow Down"},
        {"type": "diamond", "category": "marker", "label": "Diamond"},
        {"type": "crosshair", "category": "marker", "label": "Crosshair"},
    ]
