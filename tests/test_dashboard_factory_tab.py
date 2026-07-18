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

_KIOSK_SCRIPT = """
from dashboard.tabs import factory
factory.render(None)
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


def test_known_machine_id_shows_detail_panel():
    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "gate"
    at.run()
    assert not at.exception
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "Status:" in captions


def test_unregistered_machine_falls_back_to_generic_panel(monkeypatch):
    """Alle elf Maschinen haben inzwischen einen eigenen Detail-Renderer
    (17.7.-Ausbau) — der generische Fallback (Vision W3.3) bleibt trotzdem
    Pflicht als zweite Sicherheitsnetz-Schicht für künftige Maschinen ohne
    eigenen Block. Simuliert das, statt auf eine (nicht mehr existierende)
    unbehandelte echte Maschine zu warten."""
    from dashboard.tabs import factory
    monkeypatch.delitem(factory._DETAIL_RENDERERS, "gate")
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


# ── 17.7.: Detail-Panels für die restlichen sechs Maschinen ─────────────────
# (User-Feedback "Fabrik soll das Hauptding werden" — jede Maschine liefert
# jetzt dieselben Infos, die sonst nur in den Tabellen-Tabs stünden.)

def test_analyzer_claude_detail_panel_shows_route_breakdown(monkeypatch):
    class _FakeLog:
        def get_recent(self, limit=50):
            return [{"provenance": {"model_route": "claude"}}]

    monkeypatch.setattr("analyzers.analysis_log.AnalysisLog", _FakeLog)
    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "analyzer_claude"
    at.run()
    assert not at.exception
    tables = at.get("table")
    assert len(tables) == 1
    assert any("claude" in str(row) for row in tables[0].value.to_dict("records"))


def test_breaker_detail_panel_shows_daily_and_drawdown_metrics(fresh_portfolio):
    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "breaker"
    at.run()
    assert not at.exception
    metric_labels = [m.label for m in at.get("metric")]
    assert "Tagesverlust" in metric_labels
    assert "Drawdown vom Hoch" in metric_labels


def test_gate_detail_panel_shows_host_and_port(monkeypatch):
    from config import config
    monkeypatch.setattr(config, "ibkr_host", "127.0.0.1")
    monkeypatch.setattr(config, "ibkr_port", 1)
    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "gate"
    at.run()
    assert not at.exception
    md = "".join(str(m.value) for m in at.get("markdown"))
    assert "127.0.0.1:1" in md


def test_weather_detail_panel_shows_regime(tmp_path, monkeypatch):
    import json
    from dashboard.factory import state as st_mod
    regime_file = tmp_path / "current_regime.json"
    regime_file.write_text(json.dumps({"regime": "BULL", "timestamp": "2026-07-17T12:00:00"}))
    monkeypatch.setattr(st_mod, "_REGIME_FILE", str(regime_file))

    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "weather"
    at.run()
    assert not at.exception
    md = "".join(str(m.value) for m in at.get("markdown"))
    assert "BULL" in md


_REGIME_CTX_SCRIPT = """
from analyzers.recession_detector import BULL, NEUTRAL, BEAR, CRISIS

class _Detector:
    def get_history(self, days):
        return []

class _Portfolio:
    def all_positions(self):
        return {}

class _Config:
    enable_hedging = False

class _Ctx:
    regime_data = {
        "regime": BULL, "recession_score": 0.12, "vix": 14.2,
        "yield_spread": 0.55, "components": {}, "macro_summary": "",
        "recorded_at": "2099-01-01T00:00:00",
    }
    _REGIME_COLOR = {BULL: "#0f0", NEUTRAL: "#ff0", BEAR: "#f80", CRISIS: "#f00"}
    _REGIME_ICON = {BULL: "B", NEUTRAL: "N", BEAR: "R", CRISIS: "C"}
    detector = _Detector()
    portfolio = _Portfolio()
    config = _Config()
    prices = {}

from dashboard.tabs import factory
factory.render(_Ctx())
"""


def test_weather_detail_panel_includes_full_regime_panel_with_ctx(tmp_path, monkeypatch):
    """Tab-Umbau 18.7.2026: das frühere Markt-Regime-Tab lebt jetzt im
    Wetterstation-Detailpanel — mit echtem ctx erscheint der volle
    Regime-Block (Score-Gauge etc.), nicht nur die Kurzinfo."""
    at = AppTest.from_string(_REGIME_CTX_SCRIPT)
    at.query_params["factory"] = "weather"
    at.run()
    assert not at.exception
    all_text = "".join(str(m.value) for m in at.get("markdown"))
    subheaders = "".join(str(s.value) for s in at.get("subheader"))
    assert "Rezessions-Score-Gauge" in subheaders, \
        "Volles Regime-Panel fehlt im Wetterstation-Detail"


def test_weather_detail_panel_without_ctx_regime_data_stays_lean():
    """Kiosk/kaputter ctx: Regime-Panel fällt still aus (fail-open),
    die Wetter-Kurzinfo bleibt."""
    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "weather"
    at.run()
    assert not at.exception
    subheaders = "".join(str(s.value) for s in at.get("subheader"))
    assert "Rezessions-Score-Gauge" not in subheaders


_SETTINGS_CTX_SCRIPT = """
from config import config

class _Ctx:
    config = config

from dashboard.tabs import factory
factory.render(_Ctx())
"""


def test_control_room_detail_panel_includes_full_settings_panel_with_ctx():
    """Karten-Umbau 18.7.2026: das frühere Einstellungen-Tab lebt jetzt im
    Kontrollraum-Detailpanel — mit echtem ctx erscheint das volle
    Einstellungen-Formular, nicht nur die Status-Kacheln."""
    at = AppTest.from_string(_SETTINGS_CTX_SCRIPT)
    at.query_params["factory"] = "control_room"
    at.run()
    assert not at.exception
    subheaders = "".join(str(s.value) for s in at.get("subheader"))
    assert "Bot-Einstellungen" in subheaders, \
        "Volles Einstellungen-Panel fehlt im Kontrollraum-Detail"


def test_control_room_detail_panel_without_ctx_stays_lean():
    """Kiosk/kaputter ctx: Einstellungen-Panel fällt still aus (fail-open),
    die Status-Kacheln bleiben."""
    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "control_room"
    at.run()
    assert not at.exception
    subheaders = "".join(str(s.value) for s in at.get("subheader"))
    assert "Bot-Einstellungen" not in subheaders


_WAREHOUSE_CTX_SCRIPT = """
from config import config
from portfolio.portfolio import Portfolio

class _Ctx:
    config = config
    portfolio = Portfolio()
    ticker_label = staticmethod(lambda t: t)
    _ALL_NAMES = {}

from dashboard.tabs import factory
factory.render(_Ctx())
"""


def test_warehouse_detail_panel_includes_stock_browser_and_watchlist_with_ctx():
    """Karten-Umbau 18.7.2026, User-Vorgabe: Klick aufs Lager öffnet die
    durchsuchbare Kartei (früher eigener Tab) + Watchlist/IPO-Pipeline
    (früher eigener Tab)."""
    at = AppTest.from_string(_WAREHOUSE_CTX_SCRIPT, default_timeout=30)
    at.query_params["factory"] = "warehouse"
    at.run()
    assert not at.exception
    md = "".join(str(m.value) for m in at.get("markdown"))
    assert "Alle Aktien durchsuchen" in md
    subheaders = "".join(str(s.value) for s in at.get("subheader"))
    assert "IPO-Pipeline" in subheaders


def test_warehouse_detail_panel_without_ctx_stays_lean():
    """Echter Kiosk-Fall (ctx=None, wie H6.1 render(None) aufruft) — die
    Stock-Browser-Sektion braucht echten ctx (Portfolio/Config) und muss
    dort komplett entfallen, nicht nur ihren inneren Fail-open zeigen."""
    at = AppTest.from_string(_KIOSK_SCRIPT)
    at.query_params["factory"] = "warehouse"
    at.run()
    assert not at.exception
    md = "".join(str(m.value) for m in at.get("markdown"))
    assert "Alle Aktien durchsuchen" not in md


def test_backup_bot_detail_panel_shows_recent_backups_table(tmp_path, monkeypatch):
    import os as _os
    from datetime import datetime as _dt
    from dashboard.factory import state as st_mod

    p = tmp_path / "aktien_backup_test.tar.gz"
    p.write_bytes(b"0" * 2048)
    ts = _dt.now().timestamp() - 3600
    _os.utime(p, (ts, ts))
    monkeypatch.setattr(st_mod, "_BACKUPS_DIR", str(tmp_path))

    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "backup_bot"
    at.run()
    assert not at.exception
    tables = at.get("table")
    assert len(tables) == 1
    assert any("aktien_backup_test.tar.gz" in str(row)
              for row in tables[0].value.to_dict("records"))


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


# ── H1.1/H1.3: Steuerpult nur mit echtem ctx (nicht im Kiosk-Wandbild) ───────

def test_control_panel_absent_without_ctx():
    """Kiosk-Modus (H6.1) ruft render(None) — ein Not-Aus-Reset-Knopf
    gehört nicht auf ein Dauer-Wandbild, und ohne ctx gäbe es keinen
    echten Depotwert als Reset-Referenz."""
    at = AppTest.from_string(_SCRIPT)  # _SCRIPT nutzt _Ctx ohne total_value
    at.run()
    assert not at.exception
    labels = [e.label for e in at.get("expander")]
    assert "🎛 Steuerpult" not in labels


def test_control_panel_present_with_real_ctx():
    script = """
class _Ctx:
    total_value = 100000.0

from dashboard.tabs import factory
factory.render(_Ctx())
"""
    at = AppTest.from_string(script)
    at.run()
    assert not at.exception
    labels = [e.label for e in at.get("expander")]
    assert "🎛 Steuerpult" in labels
