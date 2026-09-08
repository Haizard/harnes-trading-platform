import src.chart_drawings as chart_drawings


def test_get_drawings_omits_deleted_records(monkeypatch):
    saved = [{
        "data": {
            "id": "draw-1",
            "type": "trend_line",
            "symbol": "BTCUSDT",
            "timeframe": "5m",
            "anchors": [{"time": "2024-05-01T00:00:00Z", "price": 60000}],
            "style": {"color": "#fff"},
        }
    }, {
        "data": {
            "id": "draw-2",
            "type": "ray",
            "symbol": "BTCUSDT",
            "timeframe": "5m",
            "anchors": [{"time": "2024-05-01T00:00:00Z", "price": 61000}],
            "style": {"color": "#fff"},
        }
    }]
    deleted = [{"data": {"id": "draw-1"}}]

    monkeypatch.setattr(chart_drawings, "get_events", lambda event_type, limit=200: saved if event_type == "chart_drawing_saved" else deleted if event_type == "chart_drawing_deleted" else [])

    drawings = chart_drawings.get_drawings("BTCUSDT", "5m")

    assert [item["id"] for item in drawings] == ["draw-2"]


def test_save_drawing_with_rectangle_type(monkeypatch):
    """Test saving a rectangle drawing with price levels."""
    events = []
    def mock_log_event(event_type, data):
        events.append((event_type, data))
    monkeypatch.setattr(chart_drawings, "log_event", mock_log_event)
    
    drawing = {
        "id": "rect-1",
        "type": "rectangle",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "anchors": [
            {"time": "2024-05-01T00:00:00Z", "price": 60000},
            {"time": "2024-05-01T01:00:00Z", "price": 62000}
        ],
        "style": {"color": "#64e6a0", "fill_opacity": 0.3},
    }
    
    result = chart_drawings.save_drawing(drawing)
    
    assert result["type"] == "rectangle"
    assert len(events) == 1
    assert events[0][0] == "chart_drawing_saved"


def test_save_drawing_with_text_note(monkeypatch):
    """Test saving a text note drawing."""
    events = []
    def mock_log_event(event_type, data):
        events.append((event_type, data))
    monkeypatch.setattr(chart_drawings, "log_event", mock_log_event)
    
    drawing = {
        "id": "text-1",
        "type": "text_note",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "anchors": [{"time": "2024-05-01T00:00:00Z", "price": 60000}],
        "style": {"color": "#ffd700", "font_size": 14},
        "text": "Support level at 60k",
    }
    
    result = chart_drawings.save_drawing(drawing)
    
    assert result["type"] == "text_note"
    assert result["text"] == "Support level at 60k"


def test_update_drawing(monkeypatch):
    """Test updating a drawing's properties."""
    events = []
    def mock_log_event(event_type, data):
        events.append((event_type, data))
    monkeypatch.setattr(chart_drawings, "log_event", mock_log_event)
    
    updates = {
        "style": {"color": "#ff7887"},
        "anchors": [{"time": "2024-05-01T02:00:00Z", "price": 61000}],
    }
    
    result = chart_drawings.update_drawing("draw-1", updates)
    
    assert result["id"] == "draw-1"
    assert result["style"] == {"color": "#ff7887"}
    assert len(events) == 1
    assert events[0][0] == "chart_drawing_updated"


def test_get_available_colors():
    """Test getting the color palette."""
    colors = chart_drawings.get_available_colors()
    assert len(colors) >= 5
    assert "#58d9e8" in colors  # Default cyan
    assert "#ff7887" in colors  # Red


def test_get_drawing_types():
    """Test getting available drawing types."""
    types = chart_drawings.get_drawing_types()
    assert len(types) >= 10
    
    # Check categories exist
    categories = {t["category"] for t in types}
    assert "line" in categories
    assert "shape" in categories
    assert "text" in categories
    assert "marker" in categories
    
    # Check specific types
    type_names = {t["type"] for t in types}
    assert "rectangle" in type_names
    assert "text_note" in type_names
    assert "arrow_up" in type_names
