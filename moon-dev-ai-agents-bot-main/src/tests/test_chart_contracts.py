from src.chart_contracts import ChartOverlay, ChartSnapshot, normalize_confidence


def test_normalize_confidence_accepts_common_formats():
    assert normalize_confidence(0.72) == 0.72
    assert normalize_confidence(72) == 0.72
    assert normalize_confidence("72%") == 0.72
    assert normalize_confidence("Confidence: 72%") == 0.72
    assert normalize_confidence("unknown") is None


def test_overlay_preserves_raw_confidence_and_serializes():
    overlay = ChartOverlay(id="x", source="test", type="signal", confidence="72%")
    snapshot = ChartSnapshot(symbol="BTCUSDT", timeframe="5m", as_of="now", overlays=[overlay])
    payload = snapshot.to_dict()
    assert payload["overlays"][0]["confidence"] == 0.72
    assert payload["overlays"][0]["details"]["raw_confidence"] == "72%"


def test_empty_snapshot_is_serializable():
    snapshot = ChartSnapshot(symbol="BTCUSDT", timeframe="5m", as_of="now", warnings=["empty"])
    assert snapshot.to_dict()["warnings"] == ["empty"]
