"""
Tests für dashboard/filter_xray.py (Roadmap L6.2 — Lern-Filter-Röntgen).

Netzfrei: Gewichts-Datei als Temp-Kopie.
"""
import json

from dashboard import filter_xray


def _weights_file(tmp_path, payload):
    f = tmp_path / "rl_weights.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    return str(f)


def test_maps_names_to_german_labels_and_sorts(tmp_path):
    path = _weights_file(tmp_path, {
        "weights": [0.02, 0.09, 0.05],
        "feature_names": ["vix_level", "sentiment_score", "news_velocity"],
        "trade_count": 6,
    })
    data = filter_xray.feature_weights(path)
    assert data["trade_count"] == 6
    assert [f["label"] for f in data["features"]] == [
        "Sentiment-Score", "News-Tempo", "VIX-Stand",   # nach Gewicht absteigend
    ]
    assert data["features"][0]["weight"] == 0.09


def test_unknown_feature_name_falls_back_readable(tmp_path):
    path = _weights_file(tmp_path, {
        "weights": [0.1], "feature_names": ["neues_merkmal"], "trade_count": 3,
    })
    f = filter_xray.feature_weights(path)["features"][0]
    assert f["key"] == "neues_merkmal"
    assert f["label"] == "neues merkmal"     # lesbar, aber nicht gedeutet


def test_missing_feature_names_does_not_guess(tmp_path):
    """Ältere Datei ohne feature_names: nummerieren statt raten."""
    path = _weights_file(tmp_path, {"weights": [0.1, 0.2], "trade_count": 1})
    labels = [f["key"] for f in filter_xray.feature_weights(path)["features"]]
    assert labels == ["feature_1", "feature_0"]   # nach Gewicht sortiert


def test_non_numeric_weight_skipped(tmp_path):
    path = _weights_file(tmp_path, {
        "weights": [0.1, "kaputt"], "feature_names": ["a", "b"], "trade_count": 2,
    })
    assert len(filter_xray.feature_weights(path)["features"]) == 1


def test_fail_open_missing_file(tmp_path):
    data = filter_xray.feature_weights(str(tmp_path / "weg.json"))
    assert data == {"trade_count": 0, "features": []}


def test_fail_open_broken_json(tmp_path):
    f = tmp_path / "kaputt.json"
    f.write_text("{nicht json", encoding="utf-8")
    assert filter_xray.feature_weights(str(f))["features"] == []


def test_reads_real_file_with_honest_trade_count():
    """Gegen die ECHTE Datei: die sechs Merkmale sind da, und
    trade_count ist klein (Stand 16.7.: 6) — genau darum ist der
    Warnhinweis im Tab Pflicht."""
    data = filter_xray.feature_weights()
    assert len(data["features"]) == 6
    assert data["trade_count"] < 30
    assert "Sentiment-Score" in [f["label"] for f in data["features"]]
