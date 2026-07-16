"""
Tests für dashboard/departures.py (Design D8.1 — Werksbahnhof).

Netzfrei: Earnings laufen gegen ein injiziertes Filter-Objekt, die
Makro-Datei gegen eine Temp-Kopie, systemd gegen gemocktes subprocess.
"""
import json
from datetime import datetime

import pytest

from dashboard import departures

_NOW = datetime(2026, 7, 16, 12, 0, 0)


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


def _no_system(monkeypatch):
    monkeypatch.setattr(departures, "_system_rows", lambda now: [])


# ── Makro-Quelle ─────────────────────────────────────────────────────────────

def test_macro_rows_filters_past_and_broken(_macro_file, monkeypatch):
    _no_system(monkeypatch)
    rows = departures.upcoming_events(now=_NOW)
    labels = [r["label"] for r in rows]
    assert "FOMC-Zinsentscheid" in labels
    assert "Uralt-Event" not in labels      # Vergangenheit raus
    assert "Kaputt" not in labels           # kaputtes Datum raus
    assert "Zu weit weg" not in labels      # jenseits days_ahead raus


def test_upcoming_sorted_and_limited(_macro_file, monkeypatch):
    _no_system(monkeypatch)
    rows = departures.upcoming_events(now=_NOW, limit=1)
    assert len(rows) == 1
    assert rows[0]["label"] == "FOMC-Zinsentscheid"  # das früheste


def test_missing_macro_file_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(departures, "_MACRO_FILE", str(tmp_path / "nix.json"))
    _no_system(monkeypatch)
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
