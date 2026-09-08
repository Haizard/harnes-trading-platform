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
