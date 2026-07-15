"""
Tests für dashboard/tabs/factory.py (Vision W1.3/W1.4).

Headless via streamlit.testing.v1 AppTest auf einem isolierten Mini-Skript
(Muster: tests/test_dashboard_auth.py) statt des vollen app.py.
"""
from streamlit.testing.v1 import AppTest

_SCRIPT = """
class _Ctx:
    pass

from dashboard.tabs import factory
factory.render(_Ctx())
"""


def test_factory_tab_renders_svg_scene():
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "<svg" in html_out


def test_factory_tab_shows_legend():
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    caption_out = "".join(str(c.value) for c in at.get("caption"))
    assert "aktiv/gesund" in caption_out


def test_factory_tab_shows_paused_banner_when_bot_paused(monkeypatch):
    monkeypatch.setattr("system.bot_control.is_paused", lambda: True)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "Werk pausiert" in html_out


def test_factory_tab_no_paused_banner_when_bot_active(monkeypatch):
    monkeypatch.setattr("system.bot_control.is_paused", lambda: False)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "Werk pausiert" not in html_out


# ── W3.2/W3.3: Klick-Fokus + Detail-Panels ───────────────────────────────────

def test_no_detail_panel_without_query_param():
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    assert len(at.get("markdown")) > 0
    assert not any("Status:" in str(c.value) for c in at.get("caption"))


def test_unknown_factory_id_is_ignored():
    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "does-not-exist"
    at.run()
    assert not at.exception
    assert not any("Status:" in str(c.value) for c in at.get("caption"))


def test_known_machine_id_shows_generic_detail_panel():
    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "gate"
    at.run()
    assert not at.exception
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "Status:" in captions


def test_conveyor_detail_panel_shows_funnel_metrics():
    from analyzers.decision_log import DecisionLog
    dlog = DecisionLog()
    dlog.log({"ticker": "AAPL", "action": "BUY", "reason": "Test",
              "recommendation": "BUY", "sentiment_score": 0.8})

    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "conveyor"
    at.run()
    assert not at.exception
    metric_labels = [m.label for m in at.get("metric")]
    assert "Analysiert heute" in metric_labels


def test_warehouse_detail_panel_shows_positions_table(fresh_portfolio):
    fresh_portfolio._conn.execute(
        "INSERT INTO positions (ticker, shares, entry_price, entry_date, "
        "stop_loss, take_profit, target_hold_days) VALUES (?,?,?,?,?,?,?)",
        ("NVDA", 5.0, 100.0, "2026-07-01", 90.0, 130.0, 14),
    )
    fresh_portfolio._conn.commit()

    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "warehouse"
    at.run()
    assert not at.exception
    assert len(at.get("table")) == 1


# ── H2.2: Zeitreise-Regler ────────────────────────────────────────────────────

def test_archive_shows_hint_when_no_history_for_today():
    """Ohne vorherige snapshot()-Aufrufe für heute muss der Archiv-
    Expander einen Hinweis zeigen statt zu crashen."""
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    caption_out = "".join(str(c.value) for c in at.get("caption"))
    assert "Keine Aufzeichnung" in caption_out


def test_archive_renders_archived_scene_with_warning():
    from datetime import date, datetime, timezone

    import dashboard.factory.state as st_mod

    today = date.today().isoformat()
    ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    state = st_mod.FactoryState(
        machines={"gate": st_mod.MachineState(
            id="gate", label="Verladetor", status="err",
            tooltip=["IB-Gateway nicht erreichbar"],
        )},
        paused=True, generated_at=ts,
    )
    st_mod.snapshot(state)

    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception

    warnings = [str(w.value) for w in at.get("warning")]
    assert any("ARCHIV-ANSICHT" in w for w in warnings)
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert html_out.count("<svg") >= 2  # Live-Szene + Archiv-Szene
    assert "Verladetor" in html_out


def test_archive_reconstructed_machine_has_no_extras_but_no_crash():
    """Payload-lose Rekonstruktion darf keine Fabrik-Detailtiefe-Extras
    (Kisten/Zähler/Batterie) crashen lassen — die sind fail-open."""
    import dashboard.factory.state as st_mod
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    state = st_mod.FactoryState(
        machines={"warehouse": st_mod.MachineState(
            id="warehouse", label="Hochregallager", status="ok", tooltip=["x"],
        )},
        paused=False, generated_at=ts,
    )
    st_mod.snapshot(state)

    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception


# ── H2.3: Tages-Replay-Terminal ───────────────────────────────────────────────

def test_archive_shows_replay_terminal_up_to_slider_time(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    import dashboard.factory.state as st_mod
    import system.live_status as ls_mod

    ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    today = ts[:10]
    state = st_mod.FactoryState(
        machines={"gate": st_mod.MachineState(id="gate", label="Verladetor", status="ok")},
        paused=False, generated_at=ts,
    )
    st_mod.snapshot(state)

    feed_db = str(tmp_path / "feed.db")
    monkeypatch.setattr(ls_mod, "FEED_PATH", feed_db)
    feed = ls_mod.ActivityFeed(db_path=feed_db)
    feed._conn.execute(
        "INSERT INTO events (ts, event, ticker, detail) VALUES (?,?,?,?)",
        (f"{today}T00:00:01", "trade", "AAPL", "GEKAUFT 3 @ $100"),
    )
    feed._conn.commit()

    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "px-terminal" in html_out
    assert "AAPL" in html_out
    assert "GEKAUFT 3 @ $100" in html_out


def test_archive_replay_terminal_shows_hint_without_events():
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    caption_out = "".join(str(c.value) for c in at.get("caption"))
    assert "Keine Aufzeichnung" in caption_out  # kein Snapshot -> Archiv zeigt gar nichts


# ── H1.4: Positions-Notizen read-only im Lager-Detail-Panel ──────────────────

def test_warehouse_detail_shows_saved_note_readonly(fresh_portfolio, tmp_path, monkeypatch):
    import dashboard.position_notes as pn_mod
    monkeypatch.setattr(pn_mod, "_DB_PATH", str(tmp_path / "notes.db"))
    pn_mod.PositionNotes().set("NVDA", "Warte auf Earnings")

    fresh_portfolio._conn.execute(
        "INSERT INTO positions (ticker, shares, entry_price, entry_date, "
        "stop_loss, take_profit, target_hold_days) VALUES (?,?,?,?,?,?,?)",
        ("NVDA", 5.0, 100.0, "2026-07-01", 90.0, 130.0, 14),
    )
    fresh_portfolio._conn.commit()

    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "warehouse"
    at.run()
    assert not at.exception
    caption_out = "".join(str(c.value) for c in at.get("caption"))
    assert "Warte auf Earnings" in caption_out


def test_warehouse_detail_no_notes_section_without_saved_notes(fresh_portfolio, tmp_path, monkeypatch):
    import dashboard.position_notes as pn_mod
    monkeypatch.setattr(pn_mod, "_DB_PATH", str(tmp_path / "notes.db"))

    fresh_portfolio._conn.execute(
        "INSERT INTO positions (ticker, shares, entry_price, entry_date, "
        "stop_loss, take_profit, target_hold_days) VALUES (?,?,?,?,?,?,?)",
        ("NVDA", 5.0, 100.0, "2026-07-01", 90.0, 130.0, 14),
    )
    fresh_portfolio._conn.commit()

    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "warehouse"
    at.run()
    assert not at.exception
    allmd = "".join(str(m.value) for m in at.get("markdown"))
    assert "Notizen:" not in allmd


# ── H1.2: Ticker-Schnellanalyse an den Docks ──────────────────────────────────

def test_ticker_form_queues_new_ticker(tmp_path, monkeypatch):
    import analyzers.user_request_queue as urq_mod
    monkeypatch.setattr(urq_mod, "_FILE", str(tmp_path / "q.json"))

    at = AppTest.from_string(_SCRIPT)
    at.run()
    ti = next(t for t in at.get("text_input")
              if t.label == "Werksauftrag: Ticker zur Analyse einwerfen")
    ti.set_value("NVDA")
    submit = next(b for b in at.get("button") if b.label == "📥 Einwerfen")
    submit.click().run()

    assert not at.exception
    assert "NVDA" in urq_mod.peek()
    success = "".join(str(s.value) for s in at.get("success"))
    assert "NVDA" in success and "hinzugefügt" in success


def test_ticker_form_shows_already_queued_message(tmp_path, monkeypatch):
    import analyzers.user_request_queue as urq_mod
    monkeypatch.setattr(urq_mod, "_FILE", str(tmp_path / "q.json"))
    urq_mod.add_ticker("NVDA")

    at = AppTest.from_string(_SCRIPT)
    at.run()
    ti = next(t for t in at.get("text_input")
              if t.label == "Werksauftrag: Ticker zur Analyse einwerfen")
    ti.set_value("nvda")  # Kleinschreibung -> muss normalisiert werden
    submit = next(b for b in at.get("button") if b.label == "📥 Einwerfen")
    submit.click().run()

    assert not at.exception
    success = "".join(str(s.value) for s in at.get("success"))
    assert "bereits" in success and "vorgemerkt" in success
    assert urq_mod.peek() == ["NVDA"]  # kein Duplikat


def test_ticker_form_ignores_empty_submission(tmp_path, monkeypatch):
    import analyzers.user_request_queue as urq_mod
    monkeypatch.setattr(urq_mod, "_FILE", str(tmp_path / "q.json"))

    at = AppTest.from_string(_SCRIPT)
    at.run()
    submit = next(b for b in at.get("button") if b.label == "📥 Einwerfen")
    submit.click().run()

    assert not at.exception
    assert urq_mod.peek() == []
