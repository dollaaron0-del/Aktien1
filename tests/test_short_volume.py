"""
Tests für collectors/short_volume_collector.py – FINRA Short-Volumen.
"""
from collectors.short_volume_collector import ShortVolumeCollector

_SAMPLE = (
    "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"
    "20260612|AAPL|600000|0|1000000|Q\n"
    "20260612|XYZ|70000|0|100000|Q\n"
    "20260612|BAD|abc|0|xyz|Q\n"
    "20260612|ZERO|0|0|0|Q\n"
)


def test_parse_skips_header_and_bad_rows():
    m = ShortVolumeCollector._parse(_SAMPLE)
    assert m["AAPL"] == (600000, 1000000)
    assert m["XYZ"] == (70000, 100000)
    assert "BAD" not in m          # nicht-numerisch
    assert "ZERO" not in m         # total == 0


def test_ratio_for_averages_days():
    c = ShortVolumeCollector()
    maps = [
        {"XYZ": (70000, 100000)},   # 0.70
        {"XYZ": (60000, 100000)},   # 0.60
    ]
    ratio, n, avg_short = c._ratio_for("XYZ", maps)
    assert n == 2
    assert abs(ratio - 0.65) < 1e-9
    assert avg_short == 65000


def test_high_ratio_emits_signal(monkeypatch):
    c = ShortVolumeCollector(min_ratio=0.55)
    monkeypatch.setattr(c, "_recent_maps", lambda: [{"XYZ": (70000, 100000)}])
    items = c.collect("XYZ")
    assert len(items) == 1
    assert items[0]["source"] == "FINRA/ShortVolume"
    assert items[0]["priority"] == "HIGH"          # 0.70 >= 0.65


def test_low_ratio_no_signal(monkeypatch):
    c = ShortVolumeCollector(min_ratio=0.55)
    monkeypatch.setattr(c, "_recent_maps", lambda: [{"XYZ": (30000, 100000)}])
    assert c.collect("XYZ") == []


def test_unknown_ticker_no_signal(monkeypatch):
    c = ShortVolumeCollector()
    monkeypatch.setattr(c, "_recent_maps", lambda: [{"AAPL": (60000, 100000)}])
    assert c.collect("NOPE") == []
