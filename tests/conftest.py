"""
Shared pytest fixtures for the Aktien test suite.
"""
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest

# Ensure the project root is on sys.path so imports like `from logger import ...` work
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Decision-Log (prozessweites Singleton) in Tests IMMER in ein Temp-File
# umlenken — Seams wie process_signal_queue loggen sonst in die echte data/.
os.environ.setdefault(
    "DECISION_LOG_PATH",
    os.path.join(tempfile.gettempdir(), f"decision_log_test_{os.getpid()}.db"),
)


@pytest.fixture(autouse=True)
def _isolate_sl_cooldown(tmp_path, monkeypatch):
    """SL-Cooldown-Datei IMMER in ein Temp-File umlenken: der Executor-Hook
    (Clean-SL → record()) feuert sonst aus beliebigen Exit-Tests heraus in die
    echte data/sl_cooldown.json und sperrt dort Ticker für Folge-Tests."""
    import analyzers.sl_cooldown as slc_mod
    monkeypatch.setattr(slc_mod, "_FILE", str(tmp_path / "sl_cooldown_test.json"))


@pytest.fixture(autouse=True)
def _isolate_order_log(tmp_path, monkeypatch):
    """Order-Log (broker/order_log.py, Roadmap 1.5f) IMMER in eine Temp-DB
    umlenken: der log_order()-Decorator sitzt außen um IBKRBroker/PaperBroker
    buy()/sell()/buy_crypto()/sell_crypto() und würde sonst aus jedem Test,
    der diese Methoden direkt aufruft (z.B. test_ibkr_whatif.py,
    test_ibkr_stops.py), in die echte data/order_log.db schreiben."""
    import broker.order_log as ol_mod
    monkeypatch.setattr(
        ol_mod, "_instance", ol_mod.OrderLog(db_path=str(tmp_path / "order_log_test.db"))
    )


@pytest.fixture(autouse=True)
def _isolate_factory_history(tmp_path, monkeypatch):
    """Fabrik-Zustands-Schnappschüsse (H2.1, dashboard/factory/state.py)
    IMMER in eine Temp-Datei umlenken: das Fabrik-Tab-Fragment
    (tabs/factory.py, _maybe_snapshot) schreibt sonst aus jedem Test, der
    factory.render()/read_state() end-to-end aufruft, in die echte
    data/factory_history.jsonl."""
    import dashboard.factory.state as fstate_mod
    monkeypatch.setattr(fstate_mod, "HISTORY_FILE", str(tmp_path / "factory_history_test.jsonl"))


@pytest.fixture(autouse=True)
def _isolate_position_notes(tmp_path, monkeypatch):
    """Positions-Notizen (H1.4, dashboard/position_notes.py) IMMER in eine
    Temp-Datei umlenken: der Konstruktor legt beim ersten Aufruf schon die
    DB-Datei + Tabelle an, auch nur beim Anzeigen (kein Klick nötig) —
    jeder Test, der tabs/portfolio.py oder tabs/factory.py end-to-end
    rendert (z.B. der volle App-Vollrender in test_dashboard_kiosk.py),
    würde sonst in die echte data/position_notes.db schreiben."""
    import dashboard.position_notes as pn_mod
    monkeypatch.setattr(pn_mod, "_DB_PATH", str(tmp_path / "position_notes_test.db"))


@pytest.fixture(autouse=True)
def _isolate_logbook(tmp_path, monkeypatch):
    """Schichtbuch (H7.3, dashboard/logbook.py) IMMER in eine Temp-Datei
    umlenken — vorsorglich (Muster: _isolate_factory_history/
    _isolate_position_notes, jeweils erst nach einem echten Daten-Leak
    gefunden). write_entry() wird zwar nur per Button-Klick ausgelöst,
    nicht beim bloßen Rendern, aber ein AppTest-Klick in einem künftigen
    Test soll trotzdem nie in die echte data/logbook.jsonl schreiben."""
    import dashboard.logbook as lb_mod
    monkeypatch.setattr(lb_mod, "LOGBOOK_FILE", str(tmp_path / "logbook_test.jsonl"))


@pytest.fixture(autouse=True)
def _isolate_achievements(tmp_path, monkeypatch):
    """Plaketten-Wand (H7.2, dashboard/achievements.py) IMMER in eine
    Temp-Datei umlenken — vorsorglich (Muster: _isolate_logbook).
    unlocked() schreibt bei jeder neu erreichten Plakette in
    ACHIEVEMENTS_FILE, auch beim bloßen Rendern des Fabrik-Tabs (kein
    Klick nötig, anders als beim Schichtbuch) — ohne Isolation würde
    das in die echte data/achievements.json schreiben."""
    import dashboard.achievements as ach_mod
    monkeypatch.setattr(ach_mod, "ACHIEVEMENTS_FILE", str(tmp_path / "achievements_test.json"))


@pytest.fixture(autouse=True)
def _isolate_bot_pause(tmp_path, monkeypatch):
    """Pause-Flag (system/bot_control.py) IMMER in eine Temp-Datei
    umlenken. SICHERHEITSKRITISCH, nicht nur Hygiene: Der Bot ist seit
    21.6.2026 BEWUSST pausiert (data/bot_paused.json). Ein Test, der den
    H1.1-Schalter auf "Weiter" klickt, würde diesen echten Flag ohne
    Isolation löschen — die Pause wäre still aufgehoben, sobald der
    systemd-Dienst wieder startet. Der Zustand darf ausschließlich durch
    den User selbst kippen, nie durch einen Testlauf."""
    import system.bot_control as bc_mod
    monkeypatch.setattr(bc_mod, "_PAUSE_FILE", str(tmp_path / "bot_paused_test.json"))


@pytest.fixture(autouse=True)
def _isolate_circuit_breaker_state(tmp_path, monkeypatch):
    """Circuit-Breaker-State (portfolio/circuit_breaker.py) IMMER in eine
    Temp-Datei umlenken. Ebenfalls sicherheitskritisch: Der H1.3-Reset
    überschreibt die echten Risiko-Referenzwerte (Tagesöffnung/
    Allzeithoch) — ein Testlauf darf die scharfe Drawdown-Schwelle des
    echten Portfolios niemals verstellen."""
    import portfolio.circuit_breaker as cb_mod
    monkeypatch.setattr(cb_mod, "_CACHE_FILE", str(tmp_path / "circuit_breaker_test.json"))


@pytest.fixture()
def tmp_data_dir(tmp_path, monkeypatch):
    """
    Creates a temporary directory and points all module-level DATA_FILE /
    _CACHE_FILE / _WEIGHTS_FILE path constants to it.
    Returns the tmp_path object.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Patch the env var used by circuit_breaker limits (keep defaults)
    monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "0.05")
    monkeypatch.setenv("MAX_DRAWDOWN_PCT", "0.15")

    return tmp_path


@pytest.fixture()
def fresh_portfolio(tmp_data_dir, monkeypatch):
    """
    Returns a Portfolio instance backed by a temp SQLite DB (10 000 cash).
    Patches portfolio.portfolio.PORTFOLIO_DB so no real data/portfolio.db is touched.
    """
    import portfolio.portfolio as port_mod

    (tmp_data_dir / "data").mkdir(exist_ok=True)
    db_file = str(tmp_data_dir / "data" / "portfolio.db")
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", db_file)

    from portfolio.portfolio import Portfolio
    return Portfolio(initial_capital=10_000.0)


@pytest.fixture()
def sample_position():
    """A realistic Position fixture for AAPL."""
    from portfolio.portfolio import Position
    return Position(
        ticker="AAPL",
        shares=10,
        entry_price=150.0,
        entry_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        stop_loss=138.0,
        take_profit=180.0,
        target_hold_days=14,
    )
