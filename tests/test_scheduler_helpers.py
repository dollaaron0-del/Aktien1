"""
Charakterisierungs-Tests für bot/scheduler.py — Roadmap 4.4a Vorbereitung.

Die Datei war bisher zu 0% getestet, obwohl hier die beiden früheren echten
Bugs (Watchdog-Zeitzonen, Headline-Trigger-Crash) entstanden. Diese Tests
sichern das AKTUELLE Verhalten ab, BEVOR run_bot_loop in kleinere Module
aufgeteilt wird — Sicherheitsnetz statt Golden-Master (kein Streamlit-
Äquivalent für einen Scheduler-Loop verfügbar).

Reine, bereits top-level stehende Helper-Funktionen — kein Mocking nötig.
"""
from bot.scheduler import (
    _regime_buy_adj,
    _regime_config_pct,
    _regime_sl,
    _regime_tp,
    _scanner_notify,
    _subtract_minutes,
)


# ── _subtract_minutes ────────────────────────────────────────────────────────

def test_subtract_minutes_basic():
    assert _subtract_minutes("09:30", 30) == "09:00"
    assert _subtract_minutes("10:00", 15) == "09:45"


def test_subtract_minutes_hour_boundary():
    assert _subtract_minutes("00:10", 20) == "00:00"   # geclamped, kein Wrap in Vortag


def test_subtract_minutes_no_negative_wraparound():
    assert _subtract_minutes("00:00", 60) == "00:00"


def test_subtract_minutes_large_minutes():
    assert _subtract_minutes("12:00", 90) == "10:30"


# ── Regime-Anzeige-Helper (reine Lookup-Tabellen) ────────────────────────────

def test_regime_config_pct_known_regimes():
    assert _regime_config_pct("BULL") == "100%"
    assert _regime_config_pct("NEUTRAL") == "80%"
    assert _regime_config_pct("BEAR") == "50%"
    assert _regime_config_pct("CRISIS") == "25%"


def test_regime_config_pct_unknown_regime():
    assert _regime_config_pct("FOO") == "?"


def test_regime_sl_known_regimes():
    assert _regime_sl("BULL") == "6%"
    assert _regime_sl("CRISIS") == "4%"


def test_regime_tp_known_regimes():
    assert _regime_tp("BULL") == "22%"
    assert _regime_tp("CRISIS") == "8%"


def test_regime_buy_adj_known_regimes():
    assert _regime_buy_adj("BULL") == "–3% (lockerer)"
    assert _regime_buy_adj("CRISIS") == "+10% (sehr streng)"


def test_regime_helpers_all_four_regimes_covered():
    # Jede Regime-Stufe muss in JEDER der vier Tabellen ein echtes Ergebnis
    # liefern (kein "?") — sonst zeigt eine Telegram-Nachricht ein kaputtes
    # Feld bei einem Regime-Wechsel.
    for regime in ("BULL", "NEUTRAL", "BEAR", "CRISIS"):
        assert _regime_config_pct(regime) != "?"
        assert _regime_sl(regime) != "?"
        assert _regime_tp(regime) != "?"
        assert _regime_buy_adj(regime) != "?"


# ── _scanner_notify (Quiet-Mode-Unterdrückung) ───────────────────────────────

class _FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


def test_scanner_notify_sends_when_not_quiet(monkeypatch):
    import bot.scheduler as sched_mod
    monkeypatch.setattr(sched_mod.config, "quiet_mode", False)
    notifier = _FakeNotifier()
    _scanner_notify(notifier, "Test-Meldung")
    assert notifier.sent == ["Test-Meldung"]


def test_scanner_notify_suppressed_when_quiet(monkeypatch):
    import bot.scheduler as sched_mod
    monkeypatch.setattr(sched_mod.config, "quiet_mode", True)
    notifier = _FakeNotifier()
    _scanner_notify(notifier, "Test-Meldung")
    assert notifier.sent == []           # unterdrückt, NICHT gesendet


def test_scanner_notify_never_raises_on_broken_notifier(monkeypatch):
    import bot.scheduler as sched_mod
    monkeypatch.setattr(sched_mod.config, "quiet_mode", False)

    class _BrokenNotifier:
        def send(self, msg):
            raise ConnectionError("Telegram down")

    _scanner_notify(_BrokenNotifier(), "Test")   # darf NICHT werfen
