"""
Tests für dashboard/departures.py (Design D8.1 — Werksbahnhof,
L3.1 — Positions-Abfahrten).

Netzfrei: Earnings laufen gegen ein injiziertes Filter-Objekt, die
Makro-Datei gegen eine Temp-Kopie, systemd gegen gemocktes subprocess,
Positionen gegen ein Fake-Portfolio.
"""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from dashboard import departures

_NOW = datetime(2026, 7, 16, 12, 0, 0)


def _fake_portfolio(monkeypatch, positions: dict):
    """Portfolio.all_positions() gegen synthetische Positionen tauschen —
    departures importiert die Klasse erst im Funktionskörper, darum am
    Ursprungsmodul patchen."""
    import portfolio.portfolio as port_mod

    class _FakePortfolio:
        def all_positions(self):
            return positions

    monkeypatch.setattr(port_mod, "Portfolio", _FakePortfolio)


def _pos(entry_date="2026-07-01", hold=15):
    return SimpleNamespace(entry_date=entry_date, target_hold_days=hold,
                           shares=10, entry_price=100.0)


@pytest.fixture()
def _macro_file(tmp_path, monkeypatch):
    f = tmp_path / "macro_calendar.json"
    f.write_text(json.dumps({"events": [
        {"name": "FOMC-Zinsentscheid", "date": "2026-07-29", "impact": "HIGH"},
        {"name": "CPI (geschätzt)", "date": "2026-08-12", "impact": "HIGH"},
        {"name": "Uralt-Event", "date": "2026-01-01", "impact": "HIGH"},
        {"name": "Kaputt", "date": "kein-datum", "impact": "HIGH"},
        {"name": "Zu weit weg", "date": "2027-06-01", "impact": "LOW"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(departures, "_MACRO_FILE", str(f))
    return f


def _only_macro(monkeypatch):
    """Nur die Makro-Quelle aktiv lassen — System (systemd/live_status)
    und Positionen (echtes Portfolio) hier stilllegen, damit die
    Makro-Tests nicht von echter Umgebung/Depot-Lage abhängen."""
    monkeypatch.setattr(departures, "_system_rows", lambda now: [])
    monkeypatch.setattr(departures, "_position_rows", lambda now: [])


# ── Makro-Quelle ─────────────────────────────────────────────────────────────

def test_macro_rows_filters_past_and_broken(_macro_file, monkeypatch):
    _only_macro(monkeypatch)
    rows = departures.upcoming_events(now=_NOW)
    labels = [r["label"] for r in rows]
    assert "FOMC-Zinsentscheid" in labels
    assert "Uralt-Event" not in labels      # Vergangenheit raus
    assert "Kaputt" not in labels           # kaputtes Datum raus
    assert "Zu weit weg" not in labels      # jenseits days_ahead raus


def test_upcoming_sorted_and_limited(_macro_file, monkeypatch):
    _only_macro(monkeypatch)
    rows = departures.upcoming_events(now=_NOW, limit=1)
    assert len(rows) == 1
    assert rows[0]["label"] == "FOMC-Zinsentscheid"  # das früheste


def test_missing_macro_file_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(departures, "_MACRO_FILE", str(tmp_path / "nix.json"))
    _only_macro(monkeypatch)
    assert departures.upcoming_events(now=_NOW) == []


# ── System-Quellen ───────────────────────────────────────────────────────────

def test_system_rows_include_next_run(monkeypatch, tmp_path):
    import system.live_status as ls_mod
    status_file = tmp_path / "status.json"
    status_file.write_text(json.dumps({"next_run": "2026-07-17T09:00:00"}))
    monkeypatch.setattr(ls_mod, "STATUS_PATH", str(status_file))
    monkeypatch.setattr(departures, "_next_backup", lambda now: "2026-07-17")
    rows = departures._system_rows(_NOW)
    labels = [r["label"] for r in rows]
    assert any("Bot-Zyklus" in l for l in labels)
    assert any("Backup" in l for l in labels)
    assert all(r["kind"] == "system" for r in rows)


def test_next_backup_parses_systemctl_output(monkeypatch):
    class _Out:
        stdout = "Fri 2026-07-17 03:00:00 CEST\n"
    monkeypatch.setattr(departures.subprocess, "run", lambda *a, **k: _Out())
    assert departures._next_backup(_NOW) == "2026-07-17"


def test_next_backup_fail_open(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("kein systemctl")
    monkeypatch.setattr(departures.subprocess, "run", _boom)
    assert departures._next_backup(_NOW) is None


def test_next_backup_handles_empty_and_na(monkeypatch):
    for raw in ("", "n/a", "0", "infinity"):
        out = type("_Out", (), {"stdout": raw})()
        monkeypatch.setattr(departures.subprocess, "run",
                            lambda *a, _out=out, **k: _out)
        assert departures._next_backup(_NOW) is None


# ── Earnings (netzfrei via injiziertem Filter) ───────────────────────────────

class _FakeFilter:
    def __init__(self, mapping):
        self._m = mapping

    def next_earnings(self, ticker):
        v = self._m.get(ticker)
        if isinstance(v, Exception):
            raise v
        return v


def test_earnings_rows_maps_dates_and_skips_failures():
    rows = departures.earnings_rows(
        ["NVDA", "TSLA", "BOOM", "OLD"],
        now=_NOW,
        filter_obj=_FakeFilter({
            "NVDA": datetime(2026, 8, 7),
            "TSLA": None,
            "BOOM": RuntimeError("yfinance kaputt"),
            "OLD": datetime(2026, 1, 5),
        }),
    )
    assert [r["label"] for r in rows] == ["NVDA Earnings"]
    assert rows[0]["kind"] == "earnings"


def test_earnings_of_held_ticker_is_frachtrisiko():
    """L3.1: Earnings eines Titels, den wir HALTEN, sind ein anderes
    Kaliber als ein Watchlist-Termin — das muss die Tafel unterscheiden."""
    rows = departures.earnings_rows(
        ["ZHELD", "ZWATCH"], now=_NOW,
        filter_obj=_FakeFilter({"ZHELD": datetime(2026, 8, 7),
                                "ZWATCH": datetime(2026, 8, 8)}),
        held=["zheld"],  # Kleinschreibung muss gehen
    )
    by_label = {r["label"]: r for r in rows}
    assert by_label["ZHELD Earnings (im Depot)"]["impact"] == "FRACHTRISIKO"
    assert by_label["ZWATCH Earnings"]["impact"] == "EARNINGS"


# ── L3.1: Positions-Abfahrten ────────────────────────────────────────────────

def test_position_rows_due_date_from_entry_plus_hold(monkeypatch):
    _fake_portfolio(monkeypatch, {"ZTSM": _pos(entry_date="2026-07-10", hold=15)})
    rows = departures._position_rows(_NOW)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-07-25"      # 10.7. + 15 Tage
    assert rows[0]["kind"] == "position"
    assert rows[0]["impact"] == "ABFAHRT"
    assert "ZTSM" in rows[0]["label"]


def test_position_rows_marks_overdue(monkeypatch):
    """Ziel überschritten → bleibt sichtbar, als ÜBERFÄLLIG markiert."""
    _fake_portfolio(monkeypatch, {"ZOLD": _pos(entry_date="2026-06-01", hold=10)})
    rows = departures._position_rows(_NOW)
    assert rows[0]["impact"] == "ÜBERFÄLLIG"


def test_position_rows_broken_position_skipped(monkeypatch):
    _fake_portfolio(monkeypatch, {
        "ZOK": _pos(),
        "ZBAD": SimpleNamespace(entry_date="kaputt", target_hold_days=5),
    })
    rows = departures._position_rows(_NOW)
    assert [r["label"].split(" ")[0] for r in rows] == ["ZOK"]


def test_position_rows_no_positions(monkeypatch):
    _fake_portfolio(monkeypatch, {})
    assert departures._position_rows(_NOW) == []


def test_position_rows_fail_open(monkeypatch):
    import portfolio.portfolio as port_mod

    class _Boom:
        def __init__(self):
            raise RuntimeError("DB kaputt")

    monkeypatch.setattr(port_mod, "Portfolio", _Boom)
    assert departures._position_rows(_NOW) == []


def test_overdue_position_survives_horizon_filter(monkeypatch, _macro_file):
    """Der Horizont schneidet nur nach VORNE ab — eine überfällige
    Abfahrt darf nicht stillschweigend verschwinden und steht durch die
    Datums-Sortierung oben."""
    monkeypatch.setattr(departures, "_system_rows", lambda now: [])
    _fake_portfolio(monkeypatch, {"ZOLD": _pos(entry_date="2026-06-01", hold=10)})
    rows = departures.upcoming_events(now=_NOW)
    assert rows[0]["kind"] == "position"
    assert rows[0]["impact"] == "ÜBERFÄLLIG"


def test_held_tickers_reads_portfolio(monkeypatch):
    _fake_portfolio(monkeypatch, {"ZTSM": _pos(), "ZLLY": _pos()})
    assert sorted(departures.held_tickers()) == ["ZLLY", "ZTSM"]


def test_held_tickers_fail_open(monkeypatch):
    import portfolio.portfolio as port_mod

    class _Boom:
        def __init__(self):
            raise RuntimeError("kaputt")

    monkeypatch.setattr(port_mod, "Portfolio", _Boom)
    assert departures.held_tickers() == []


def test_board_html_shows_overdue_wording():
    html_out = departures.board_html([
        {"date": "2026-07-13", "label": "ZOLD — planmäßige Abfahrt",
         "impact": "ÜBERFÄLLIG", "kind": "position"},
    ], now=_NOW)
    assert "überfällig (3 Tage)" in html_out
    assert "in -3 Tagen" not in html_out


def test_board_html_overdue_singular():
    html_out = departures.board_html([
        {"date": "2026-07-15", "label": "X", "impact": "ÜBERFÄLLIG",
         "kind": "position"},
    ], now=_NOW)
    assert "überfällig (1 Tag)" in html_out


def test_watchlist_tickers_reads_file(tmp_path, monkeypatch):
    f = tmp_path / "wl.json"
    f.write_text(json.dumps({"tickers": ["nvda", "TSLA"]}))
    monkeypatch.setattr(departures, "_WATCHLIST_FILE", str(f))
    assert departures.watchlist_tickers() == ["NVDA", "TSLA"]


def test_watchlist_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(departures, "_WATCHLIST_FILE", str(tmp_path / "nix.json"))
    assert departures.watchlist_tickers() == []


# ── Darstellung ──────────────────────────────────────────────────────────────

def test_board_html_escapes_labels():
    html_out = departures.board_html([{
        "date": "2026-07-29",
        "label": "<script>alert(1)</script>",
        "impact": "HIGH",
        "kind": "makro",
    }], now=_NOW)
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_board_html_empty_state():
    html_out = departures.board_html([], now=_NOW)
    assert "Keine anstehenden Termine" in html_out


def test_board_html_relative_days():
    html_out = departures.board_html([
        {"date": "2026-07-16", "label": "A", "impact": "", "kind": "system"},
        {"date": "2026-07-17", "label": "B", "impact": "", "kind": "system"},
        {"date": "2026-07-29", "label": "C", "impact": "HIGH", "kind": "makro"},
    ], now=_NOW)
    assert "heute" in html_out
    assert "morgen" in html_out
    assert "in 13 Tagen" in html_out
