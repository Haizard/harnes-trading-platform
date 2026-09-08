from src.chart_contracts import ChartOverlay, ChartSnapshot, normalize_chart_contributions, normalize_confidence


def test_normalize_confidence_accepts_common_formats():
    assert normalize_confidence(0.72) == 0.72
    assert normalize_confidence(72) == 0.72
    assert normalize_confidence("72%") == 0.72
    assert normalize_confidence("Confidence: 72%") == 0.72
    assert normalize_confidence("unknown") is None
    # Pathological ranges (issue #10)
    assert normalize_confidence("70-80%") == 0.75  # midpoint of 70 and 80
    assert normalize_confidence("65.5-75.5%") == 0.705  # midpoint with decimals
    assert normalize_confidence("Range: 60-90%") == 0.75  # prefix + range


def test_overlay_preserves_raw_confidence_and_serializes():
    overlay = ChartOverlay(id="x", source="test", type="signal", confidence="72%")
    snapshot = ChartSnapshot(symbol="BTCUSDT", timeframe="5m", as_of="now", overlays=[overlay])
    payload = snapshot.to_dict()
    assert payload["overlays"][0]["confidence"] == 0.72
    assert payload["overlays"][0]["details"]["raw_confidence"] == "72%"


def test_empty_snapshot_is_serializable():
    snapshot = ChartSnapshot(symbol="BTCUSDT", timeframe="5m", as_of="now", warnings=["empty"])
    assert snapshot.to_dict()["warnings"] == ["empty"]


def test_chart_contributions_reject_unknown_types():
    contributions = normalize_chart_contributions([
        {"type": "price_zone", "price_low": 10, "price_high": 12, "confidence": "80%"},
        {"type": "execute_javascript", "text": "bad"},
    ])
    assert len(contributions) == 1
    assert contributions[0].confidence == 0.8
