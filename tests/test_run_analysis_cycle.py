"""
Charakterisierungs-Testgerüst für bot.runner.run_analysis_cycle – Roadmap 4.4a.

run_analysis_cycle ist der eigentliche Monolith (1005 Zeilen, 42 if-Zweige): die
kleinen Helfer sind bereits abgesichert (test_runner_helpers.py), aber die
Zyklus-Business-Logik selbst war ungetestet – genau der Code, der VOR einer
Extraktion ein Sicherheitsnetz braucht. Ansatz: die Funktion mit vollständig
gefakten Abhängigkeiten (Portfolio/Broker/Strategie/Analyzer/Collectoren/Telegram)
end-to-end fahren, ohne einen einzigen echten Netz-/Broker-/Claude-Aufruf.

Dieser erste Test pinnt das SKELETT über den „kein-News"-Fokuspfad:
  Setup → Regime-Default → Exits prüfen (leeres Portfolio) → Watchlist=Fokus →
  Prefetch (keine News) → serielle Schleife (`if not news: continue`) →
  Abschluss (Snapshot, Archiv-Cleanup, Tages-Aktionen, Live-Idle).
Auf diesem Pfad werden analyzer.analyze / Executor-Käufe / RL / Earnings bewusst
NICHT erreicht (die liegen hinter `if not news`), was ihn zum sicheren, stabilen
Erst-Anker macht. Weitere Zweige (echte Analyse, Trades) folgen inkrementell.
"""
from types import SimpleNamespace

import pytest

import bot.runner as runner_mod
from bot.runner import run_analysis_cycle

# Schlüssel, die die Zyklus-Schleife im Quellen-Breakdown-Print erwartet (Z. ~1294).
_BREAKDOWN_KEYS = ("yahoo", "newsapi", "sec_edgar", "wire", "stocktwits", "twitter",
                   "insider", "usaspending", "options_flow", "european_news")


class _FakePortfolio:
    cash = 10_000.0
    def all_positions(self):            return {}
    def get_position(self, ticker):     return None
    def total_value(self, prices):      return 10_000.0


class _FakeBroker:
    def get_prices(self, tickers):      return {}
    def get_price(self, ticker):        return 100.0
    def get_crypto_price(self, ticker): return 100.0


class _FakeStrategy:
    journal = None
    def check_exits(self, prices, regime):          return []
    def build_open_position_context(self, ticker):  return None


class _FakeTracker:
    def __init__(self):                 self.snapshots = []
    def record_snapshot(self, total_value, cash, n_positions):
        self.snapshots.append((total_value, cash, n_positions))
    def open_prediction_id(self, ticker):           return None


class _FakePhaseCtrl:
    def current_phase(self, total):     return "GROWTH"
    def get_info(self, total):
        return {"phase": "GROWTH", "progress_pct": 10.0, "growth_target": 100_000,
                "remaining_to_goal": 90_000}


class _FakeArchive:
    def __init__(self):                 self.cleaned = False
    def get_history(self, ticker, days=30, exclude_titles=None):  return []
    def cleanup_old(self, keep_days=32): self.cleaned = True


class _FakeYahoo:
    def get_price_data(self, ticker):
        return {"current_price": 100.0, "volume": 1000, "price_change": 0.0}


class _FakeAnalyzer:            # Ersatz für ClaudeAnalyzer – analyze() darf nicht fallen
    def analyze(self, **kwargs):        raise AssertionError("analyze() auf No-News-Pfad nie erwartet")


class _FakeNotifier:
    sent = []
    def send(self, msg, level=None):    _FakeNotifier.sent.append(msg)


@pytest.fixture
def cycle_env(tmp_path, monkeypatch):
    """Verdrahtet run_analysis_cycle vollständig netz-/broker-frei."""
    _FakeNotifier.sent = []
    # Live-Status/Feed in tmp lenken (fail-open, aber kein Schreiben ins echte data/).
    monkeypatch.setenv("BOT_STATUS_PATH", str(tmp_path / "bot_status.json"))
    monkeypatch.setenv("ACTIVITY_FEED_PATH", str(tmp_path / "activity_feed.db"))
    monkeypatch.setattr(runner_mod, "_DAILY_ACTIONS_PATH", str(tmp_path / "daily_actions.json"))

    # Schwere Modul-Abhängigkeiten faken.
    monkeypatch.setattr(runner_mod, "ClaudeAnalyzer", _FakeAnalyzer)
    monkeypatch.setattr(runner_mod, "TelegramNotifier", _FakeNotifier)
    monkeypatch.setattr(runner_mod, "_make_collectors", lambda: {"yahoo": _FakeYahoo()})
    breakdown = {k: 0 for k in _BREAKDOWN_KEYS}
    monkeypatch.setattr(runner_mod, "collect_news",
                        lambda t, archive, collectors: ([], dict(breakdown)))
    monkeypatch.setattr(runner_mod, "_get_watchlist", lambda portfolio: (["FOCUS"], {}))
    # Makro-Brief ohne Netz.
    monkeypatch.setattr("analyzers.macro_context.get_macro_brief", lambda: "")
    # TradingView-Zweige deterministisch aus.
    monkeypatch.setattr(runner_mod.config, "tradingview_webhook_enabled", False)

    return SimpleNamespace(
        portfolio=_FakePortfolio(), broker=_FakeBroker(), strategy=_FakeStrategy(),
        tracker=_FakeTracker(), phase_ctrl=_FakePhaseCtrl(), archive=_FakeArchive(),
        daily_actions_path=tmp_path / "daily_actions.json",
    )


def test_no_news_focus_cycle_completes_skeleton(cycle_env):
    env = cycle_env
    # Fokus-Lauf auf einen Ticker ohne News → durchläuft das komplette Skelett,
    # erreicht aber weder Claude noch einen Trade.
    run_analysis_cycle(
        env.portfolio, env.broker, env.strategy, env.tracker, env.phase_ctrl,
        env.archive, only_tickers=["FOCUS"],
    )
    # Abschluss erreicht: genau ein Portfolio-Snapshot (leeres Depot).
    assert env.tracker.snapshots == [(10_000.0, 10_000.0, 0)]
    # Archiv-Cleanup lief.
    assert env.archive.cleaned is True
    # Keine Trades → record_daily_actions ist auf [] ein No-Op, schreibt nichts.
    assert not env.daily_actions_path.exists()


def test_no_news_cycle_makes_no_trades_and_no_telegram_spam(cycle_env):
    env = cycle_env
    # Läuft der Zyklus überhaupt durch, war _FakeAnalyzer.analyze nie dran (würde
    # sonst AssertionError werfen) – d.h. der No-News-Skip greift wie erwartet.
    run_analysis_cycle(
        env.portfolio, env.broker, env.strategy, env.tracker, env.phase_ctrl,
        env.archive, only_tickers=["FOCUS"],
    )
    # Keine Trades persistiert (kein Tagesspeicher angelegt).
    assert not env.daily_actions_path.exists()
    # Fokuslauf ohne announce_start → keine Start-/Heartbeat-Telegram-Nachricht.
    assert _FakeNotifier.sent == []


def test_announce_start_sends_pre_market_telegram(cycle_env):
    env = cycle_env
    run_analysis_cycle(
        env.portfolio, env.broker, env.strategy, env.tracker, env.phase_ctrl,
        env.archive, announce_start=True, only_tickers=["FOCUS"],
    )
    # announce_start → genau die vorbörsliche Start-Nachricht (kein Trade-Spam).
    assert any("Vorbörsliche Analyse gestartet" in m for m in _FakeNotifier.sent)


class _FakeExecutor:
    def __init__(self, *a, **k):        pass
    def execute(self, res, days_held=0): return f"VERKAUFT 5 {res.ticker} @ $100"


def test_exit_action_flows_into_daily_actions(cycle_env, monkeypatch):
    env = cycle_env
    # Strategie meldet einen Exit; der (gefakte) Executor bestätigt den Verkauf.
    monkeypatch.setattr(env.strategy, "check_exits",
                        lambda prices, regime: [SimpleNamespace(ticker="FOCUS")])
    monkeypatch.setattr("strategy.executor.TradeExecutor", _FakeExecutor)

    run_analysis_cycle(
        env.portfolio, env.broker, env.strategy, env.tracker, env.phase_ctrl,
        env.archive, only_tickers=["FOCUS"],
    )
    # Der Exit landet in den Tages-Aktionen und zählt als Trade.
    import json
    actions = json.loads(env.daily_actions_path.read_text())["actions"]
    assert any("VERKAUFT 5 FOCUS" in a for a in actions)
