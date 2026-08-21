"""
Tests für die Fabrik-Zustands-Schnappschüsse (Ausbau-Roadmap H2.1,
docs/DASHBOARD_HORIZONT.md) — Grundlage für Zeitreise/Replay (H2.2/H2.3).
"""
import json

from dashboard.factory.state import (
    FactoryState,
    MachineState,
    read_history,
    snapshot,
)
from dashboard.tabs import factory as factory_tab


def _state(ts: str, paused: bool = False) -> FactoryState:
    return FactoryState(
        machines={"gate": MachineState(id="gate", label="Tor", status="ok",
                                       tooltip=["IB-Gateway erreichbar"])},
        paused=paused, generated_at=ts,
    )


# ── snapshot() / read_history() Roundtrip ────────────────────────────────────

def test_snapshot_and_read_history_roundtrip(tmp_path):
    path = str(tmp_path / "history.jsonl")
    snapshot(_state("2026-07-15T10:00:00"), path=path)
    snapshot(_state("2026-07-15T10:10:00", paused=True), path=path)
    snapshot(_state("2026-07-16T09:00:00"), path=path)  # anderer Tag

    rows = read_history("2026-07-15", path=path)
    assert len(rows) == 2
    assert rows[0]["ts"] == "2026-07-15T10:00:00"
    assert rows[0]["paused"] is False
    assert rows[1]["paused"] is True
    assert rows[0]["machines"]["gate"]["status"] == "ok"
    assert rows[0]["machines"]["gate"]["tooltip"] == ["IB-Gateway erreichbar"]
    assert "payload" not in rows[0]["machines"]["gate"]  # bewusst klein gehalten


def test_read_history_empty_for_missing_file(tmp_path):
    assert read_history("2026-07-15", path=str(tmp_path / "nope.jsonl")) == []


def test_read_history_skips_corrupt_lines(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text(
        json.dumps({"ts": "2026-07-15T10:00:00", "paused": False, "machines": {}}) + "\n"
        "{kaputtes json\n"
        + json.dumps({"ts": "2026-07-15T11:00:00", "paused": False, "machines": {}}) + "\n"
    )
    rows = read_history("2026-07-15", path=str(path))
    assert len(rows) == 2
    assert [r["ts"] for r in rows] == ["2026-07-15T10:00:00", "2026-07-15T11:00:00"]


def test_snapshot_is_fail_open_on_write_error(tmp_path, monkeypatch):
    # Verzeichnis existiert nicht und kann nicht angelegt werden (Pfad
    # zeigt auf eine Datei statt eines Verzeichnisses) — snapshot() darf
    # trotzdem nicht crashen.
    blocked_dir = tmp_path / "not_a_dir"
    blocked_dir.write_text("x")
    bad_path = str(blocked_dir / "history.jsonl")
    snapshot(_state("2026-07-15T10:00:00"), path=bad_path)  # darf nicht raisen


# ── Deckelung ─────────────────────────────────────────────────────────────────

def test_snapshot_caps_file_to_newer_half_when_oversized(tmp_path, monkeypatch):
    import dashboard.factory.state as st_mod
    monkeypatch.setattr(st_mod, "_HISTORY_MAX_BYTES", 200)  # künstlich klein

    path = str(tmp_path / "history.jsonl")
    for i in range(20):
        snapshot(_state(f"2026-07-15T{i:02d}:00:00"), path=path)

    rows = read_history("2026-07-15", path=path)
    # Nach der Deckelung müssen die ÄLTESTEN Zeilen weg sein, die
    # neuesten müssen bleiben:
    assert rows[-1]["ts"] == "2026-07-15T19:00:00"
    assert len(rows) < 20


# ── Drossel (tabs/factory.py) ────────────────────────────────────────────────

def test_maybe_snapshot_throttles_to_one_write_per_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(factory_tab, "_last_snapshot_ts", 0.0)
    calls = []
    monkeypatch.setattr(factory_tab, "snapshot", lambda state: calls.append(state))

    state = _state("2026-07-15T10:00:00")
    assert factory_tab._maybe_snapshot(state) is True
    assert factory_tab._maybe_snapshot(state) is False  # sofort danach: gedrosselt
    assert len(calls) == 1


def test_maybe_snapshot_writes_again_after_interval(monkeypatch):
    monkeypatch.setattr(factory_tab, "_last_snapshot_ts", 0.0)
    calls = []
    monkeypatch.setattr(factory_tab, "snapshot", lambda state: calls.append(state))

    state = _state("2026-07-15T10:00:00")
    assert factory_tab._maybe_snapshot(state) is True
    # Uhr künstlich weit genug vorstellen:
    monkeypatch.setattr(
        factory_tab, "_last_snapshot_ts",
        factory_tab._last_snapshot_ts - factory_tab._SNAPSHOT_INTERVAL_S - 1,
    )
    assert factory_tab._maybe_snapshot(state) is True
    assert len(calls) == 2


# ── reconstruct_from_snapshot() (H2.2) ───────────────────────────────────────

def test_reconstruct_from_snapshot_rebuilds_machine_state(tmp_path):
    from dashboard.factory.state import reconstruct_from_snapshot

    path = str(tmp_path / "history.jsonl")
    snapshot(_state("2026-07-15T10:00:00", paused=True), path=path)
    row = read_history("2026-07-15", path=path)[0]

    rebuilt = reconstruct_from_snapshot(row)
    assert rebuilt.paused is True
    assert rebuilt.generated_at == "2026-07-15T10:00:00"
    m = rebuilt.machines["gate"]
    assert m.status == "ok"
    assert m.tooltip == ["IB-Gateway erreichbar"]
    assert m.label == "Verladetor"  # aus MACHINE_LABELS, nicht in der Zeile gespeichert
    assert m.payload == {}  # bewusst payload-los


def test_reconstruct_from_snapshot_handles_empty_row():
    from dashboard.factory.state import reconstruct_from_snapshot

    rebuilt = reconstruct_from_snapshot({})
    assert rebuilt.machines == {}
    assert rebuilt.paused is False


# ── read_feed_events_until() (H2.3 Tages-Replay) ─────────────────────────────

def _seed_feed(db_path, rows):
    import system.live_status as ls_mod
    feed = ls_mod.ActivityFeed(db_path=db_path)
    for ts, event, ticker, detail in rows:
        feed._conn.execute(
            "INSERT INTO events (ts, event, ticker, detail) VALUES (?,?,?,?)",
            (ts, event, ticker, detail),
        )
    feed._conn.commit()
    return feed


def test_read_feed_events_until_filters_day_and_cutoff(tmp_path):
    from dashboard.factory.state import read_feed_events_until

    db_path = str(tmp_path / "feed.db")
    _seed_feed(db_path, [
        ("2026-07-15T09:00:00", "cycle_start", None, None),
        ("2026-07-15T09:05:00", "trade", "AAPL", "GEKAUFT 3 @ $100"),
        ("2026-07-15T11:00:00", "cycle_end", None, "1 Trade"),  # nach dem Cutoff
        ("2026-07-14T09:00:00", "trade", "NVDA", "anderer Tag"),
    ])

    rows = read_feed_events_until("2026-07-15", "2026-07-15T10:00:00", db_path=db_path)
    assert [r["event"] for r in rows] == ["cycle_start", "trade"]
    assert rows[0]["ts"] < rows[1]["ts"]  # älteste zuerst


def test_read_feed_events_until_empty_when_db_missing(tmp_path):
    from dashboard.factory.state import read_feed_events_until

    rows = read_feed_events_until("2026-07-15", "2026-07-15T23:59:59",
                                  db_path=str(tmp_path / "nope.db"))
    assert rows == []


def test_read_feed_events_until_fail_open_on_corrupt_db(tmp_path):
    from dashboard.factory.state import read_feed_events_until

    bad_db = tmp_path / "corrupt.db"
    bad_db.write_bytes(b"not a real sqlite file")
    rows = read_feed_events_until("2026-07-15", "2026-07-15T23:59:59", db_path=str(bad_db))
    assert rows == []
