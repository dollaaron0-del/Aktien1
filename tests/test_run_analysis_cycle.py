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
    def execute(self, res, *, days_held=0, analysis=None, sources_breakdown=None):
        return f"VERKAUFT 5 {res.ticker} @ $100"


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


# ── Echter Analyse-Zweig: analyzer.analyze() → StrategyResult → Executor ────
#
# Erreicht den Kern-Pfad hinter `if not news: continue` – Claude-Analyse (gefakt),
# Cache/Log-Persistenz (gefakte Singletons, s.u. – die echten sind Modul-Singletons
# OHNE Env-Path-Override und würden sonst in echte data/-Dateien schreiben),
# strategy.evaluate()/Executor.execute() und decision_log. Netzwerk-lastige
# Analyzer (ChartPatternAnalyzer→yfinance, EarningsPredictor→requests) werden
# gefakt statt real aufgerufen – dieselbe "0 echte Netz-/Broker-/Claude-Aufrufe"-
# Grenze wie beim No-News-Pfad.

from analyzers.claude_analyzer import AnalysisResult
from strategy.swing_strategy import StrategyResult


class _FakeChartPatternAnalyzer:
    def analyze(self, ticker):          return None


class _FakeNewsVelocity:
    def analyze(self, ticker):
        return SimpleNamespace(acceleration="NORMAL", articles_24h=0,
                               baseline_per_day=0, signal_boost=1.0)


class _FakeMTFSentiment:
    def analyze(self, ticker, by_date): return SimpleNamespace(trend="FLAT")
    def to_text(self, result):          return ""


class _FakeReEntryTracker:
    def get_all_watched(self):          return []
    def update_prices(self, prices):    pass


class _FakeEarningsPredictor:
    def predict(self, ticker):          return {}


class _FakeSignalExpander:
    def process_news_items(self, news): return []


class _FakeAnalysisCache:
    def __init__(self):                 self.stored = []
    def get(self, ticker):              return None
    def store(self, ticker, direction, sentiment_score, confidence, recommendation):
        self.stored.append((ticker, direction, sentiment_score, confidence, recommendation))


class _FakeAnalysisLog:
    def __init__(self):                 self.stored = []
    def store(self, analysis, sources_breakdown=None):
        self.stored.append(analysis)
        return len(self.stored)


class _FakeBuyAnalyzer:
    """Ersatz für ClaudeAnalyzer: liefert ein festes BUY-Urteil ohne Claude-Call."""
    def analyze(self, **kwargs):
        return AnalysisResult(
            ticker=kwargs["ticker"], sentiment_score=0.85, direction="BULLISH",
            confidence="HIGH", recommendation="BUY", entry_rationale="Testthese",
            related_tickers=[], entry_trigger_price=None,
        )


class _FakeBuyExecutor:
    def __init__(self, *a, **k):        pass
    def execute(self, res, *, days_held=0, analysis=None, sources_breakdown=None):
        assert res.action == "BUY"
        return f"GEKAUFT {res.shares} {res.ticker} @ ${res.price}"


@pytest.fixture
def buy_cycle_env(cycle_env, monkeypatch):
    """Baut auf cycle_env auf: News vorhanden + BUY-Empfehlung → echter Analyse-
    Zweig statt No-News-Skip. Alle netzwerk-/disk-lastigen Kollaborateure gefakt."""
    news_items = [{"title": "FOCUS Nachricht", "source": "TestWire",
                  "published": "2026-07-13"}]
    breakdown = {k: 0 for k in _BREAKDOWN_KEYS}
    breakdown["wire"] = 1
    monkeypatch.setattr(runner_mod, "collect_news",
                        lambda t, archive, collectors: (list(news_items), dict(breakdown)))
    monkeypatch.setattr(runner_mod, "ClaudeAnalyzer", _FakeBuyAnalyzer)
    monkeypatch.setattr(runner_mod, "ChartPatternAnalyzer", _FakeChartPatternAnalyzer)
    monkeypatch.setattr(runner_mod, "NewsVelocityAnalyzer", _FakeNewsVelocity)
    monkeypatch.setattr(runner_mod, "MultiTimeframeSentiment", _FakeMTFSentiment)
    monkeypatch.setattr(runner_mod, "ReEntryTracker", _FakeReEntryTracker)
    monkeypatch.setattr(runner_mod, "_earnings_predictor", _FakeEarningsPredictor())
    monkeypatch.setattr(runner_mod, "_signal_expander", _FakeSignalExpander())
    monkeypatch.setattr(runner_mod, "_analysis_cache", _FakeAnalysisCache())
    monkeypatch.setattr(runner_mod, "_analysis_log", _FakeAnalysisLog())
    monkeypatch.setattr(runner_mod, "_get_experience_store", lambda: None)
    monkeypatch.setattr("analyzers.macro_context.get_macro_context",
                        lambda: SimpleNamespace(bias_score=lambda: 0.0), raising=False)
    monkeypatch.setattr("strategy.executor.TradeExecutor", _FakeBuyExecutor)

    env = cycle_env
    monkeypatch.setattr(env.strategy, "evaluate",
                        lambda ticker, analysis, cur_px, regime, rl_agent=None: StrategyResult(
                            action="BUY", ticker=ticker, reason="Alle Kriterien erfüllt",
                            shares=3, price=cur_px, stop_loss=cur_px * 0.93,
                            take_profit=cur_px * 1.20,
                        ), raising=False)
    return env


def test_buy_signal_flows_to_trade_and_daily_actions(buy_cycle_env):
    env = buy_cycle_env
    run_analysis_cycle(
        env.portfolio, env.broker, env.strategy, env.tracker, env.phase_ctrl,
        env.archive, only_tickers=["FOCUS"],
    )
    import json
    actions = json.loads(env.daily_actions_path.read_text())["actions"]
    assert any("GEKAUFT 3 FOCUS" in a for a in actions)


def test_buy_signal_stores_analysis_in_cache_and_log(buy_cycle_env, monkeypatch):
    env = buy_cycle_env
    cache = runner_mod._analysis_cache
    log = runner_mod._analysis_log
    run_analysis_cycle(
        env.portfolio, env.broker, env.strategy, env.tracker, env.phase_ctrl,
        env.archive, only_tickers=["FOCUS"],
    )
    assert cache.stored and cache.stored[0][0] == "FOCUS"          # ticker
    assert cache.stored[0][4] == "BUY"                              # recommendation
    assert log.stored and log.stored[0].ticker == "FOCUS"


# ── Daten-Qualitäts-Gate (Roadmap 1.8): ungültiger Kurs → SKIP vor Claude ────

class _FakeBadYahoo:
    def get_price_data(self, ticker):
        return {"current_price": None, "volume": 0}   # kein gültiger Kurs


def test_invalid_price_is_gate_blocked_before_analyze(cycle_env, monkeypatch):
    env = cycle_env
    # News vorhanden (sonst greift der No-News-Skip statt des Daten-Gates),
    # aber der Kurs ist ungültig – das Gate muss VOR analyze() greifen.
    news_items = [{"title": "FOCUS Nachricht", "source": "TestWire"}]
    breakdown = {k: 0 for k in _BREAKDOWN_KEYS}
    monkeypatch.setattr(runner_mod, "collect_news",
                        lambda t, archive, collectors: (list(news_items), dict(breakdown)))
    monkeypatch.setattr(runner_mod, "_make_collectors", lambda: {"yahoo": _FakeBadYahoo()})
    # ClaudeAnalyzer bleibt der werfende _FakeAnalyzer aus cycle_env – ein Aufruf
    # hier wäre ein Beweis, dass das Gate NICHT gegriffen hat.
    monkeypatch.setattr(env.broker, "get_price", lambda ticker: None)  # kein Broker-Fallback

    run_analysis_cycle(
        env.portfolio, env.broker, env.strategy, env.tracker, env.phase_ctrl,
        env.archive, only_tickers=["FOCUS"],
    )
    # Kein Trade, kein Tagesspeicher – der Zyklus lief bis zum Ende durch
    # (sonst wäre analyze() erreicht und hätte AssertionError geworfen).
    assert not env.daily_actions_path.exists()


# ── RL-Veto (analyzers/rl_agent.py): rl_agent nur bei config.rl_veto_enabled ─
# Vor 4.4a-Extraktion der seriellen Kern-Schleife noch ungepinnter Zweig
# (s. [[monolith-split-4-4a-status]]): runner.py reicht den globalen
# _rl_agent NUR durch, wenn config.rl_veto_enabled gesetzt ist, sonst None –
# strategy.evaluate() selbst (swing_strategy.py) entscheidet, was das Veto
# tatsächlich bewirkt, das ist hier NICHT Gegenstand.

def test_rl_veto_disabled_by_default_passes_no_rl_agent(buy_cycle_env, monkeypatch):
    env = buy_cycle_env
    captured = {}

    def _spy_evaluate(ticker, analysis, cur_px, regime, rl_agent=None):
        captured["rl_agent"] = rl_agent
        return StrategyResult(action="BUY", ticker=ticker, reason="x", shares=1, price=cur_px,
                              stop_loss=cur_px * 0.9, take_profit=cur_px * 1.2)

    monkeypatch.setattr(env.strategy, "evaluate", _spy_evaluate, raising=False)
    monkeypatch.setattr(runner_mod.config, "rl_veto_enabled", False)
    run_analysis_cycle(
        env.portfolio, env.broker, env.strategy, env.tracker, env.phase_ctrl,
        env.archive, only_tickers=["FOCUS"],
    )
    assert "rl_agent" in captured
    assert captured["rl_agent"] is None


def test_rl_veto_enabled_passes_the_rl_agent_singleton(buy_cycle_env, monkeypatch):
    env = buy_cycle_env
    sentinel = object()
    monkeypatch.setattr(runner_mod, "_rl_agent", sentinel)
    monkeypatch.setattr(runner_mod.config, "rl_veto_enabled", True)
    captured = {}

    def _spy_evaluate(ticker, analysis, cur_px, regime, rl_agent=None):
        captured["rl_agent"] = rl_agent
        return StrategyResult(action="BUY", ticker=ticker, reason="x", shares=1, price=cur_px,
                              stop_loss=cur_px * 0.9, take_profit=cur_px * 1.2)

    monkeypatch.setattr(env.strategy, "evaluate", _spy_evaluate, raising=False)
    run_analysis_cycle(
        env.portfolio, env.broker, env.strategy, env.tracker, env.phase_ctrl,
        env.archive, only_tickers=["FOCUS"],
    )
    assert captured["rl_agent"] is sentinel


# ── BUY mit related_tickers → Stock-Relations-Netz + BenchList ──────────────

class _FakeStockRelations:
    calls = []
    def add_relation(self, from_ticker, related, reason):
        _FakeStockRelations.calls.append((from_ticker, list(related), reason))


class _FakeBenchListForRelations:
    added = []
    def add(self, ticker, reason, score=0.5, **kwargs):
        _FakeBenchListForRelations.added.append((ticker, score, reason))


class _FakeBuyWithRelatedAnalyzer:
    def analyze(self, **kwargs):
        return AnalysisResult(
            ticker=kwargs["ticker"], sentiment_score=0.85, direction="BULLISH",
            confidence="HIGH", recommendation="BUY", entry_rationale="Testthese",
            related_tickers=["RELATED1", "RELATED2"], entry_trigger_price=None,
        )


def test_buy_with_related_tickers_feeds_stock_relations_and_benchlist(buy_cycle_env, monkeypatch):
    env = buy_cycle_env
    _FakeStockRelations.calls = []
    _FakeBenchListForRelations.added = []
    monkeypatch.setattr(runner_mod, "ClaudeAnalyzer", _FakeBuyWithRelatedAnalyzer)
    monkeypatch.setattr("analyzers.stock_relations.StockRelations", _FakeStockRelations)
    monkeypatch.setattr("analyzers.bench_list.BenchList", _FakeBenchListForRelations)

    run_analysis_cycle(
        env.portfolio, env.broker, env.strategy, env.tracker, env.phase_ctrl,
        env.archive, only_tickers=["FOCUS"],
    )
    assert _FakeStockRelations.calls == [("FOCUS", ["RELATED1", "RELATED2"], "Testthese")]
    assert {t for t, _, _ in _FakeBenchListForRelations.added} == {"RELATED1", "RELATED2"}


# ── SKIP mit bullischem Potential → Conditional Entry ────────────────────────

class _FakeConditionalEntryWatcher:
    added = []
    @staticmethod
    def build(ticker, trigger_price, cur_price, analysis):
        return {"ticker": ticker, "trigger_price": trigger_price, "cur_price": cur_price}
    def add(self, entry):
        _FakeConditionalEntryWatcher.added.append(entry)


class _FakeSkipBullishAnalyzer:
    def analyze(self, **kwargs):
        return AnalysisResult(
            ticker=kwargs["ticker"], sentiment_score=0.65, direction="BULLISH",
            confidence="MEDIUM", recommendation="SKIP", entry_rationale="Zu teuer für jetzt",
            entry_trigger_price=90.0,   # unter dem FakeYahoo-Kurs (100.0)
        )


def test_skip_with_bullish_potential_sets_conditional_entry(buy_cycle_env, monkeypatch):
    env = buy_cycle_env
    _FakeConditionalEntryWatcher.added = []
    monkeypatch.setattr(runner_mod, "ClaudeAnalyzer", _FakeSkipBullishAnalyzer)
    monkeypatch.setattr("analyzers.conditional_entry.ConditionalEntryWatcher",
                        _FakeConditionalEntryWatcher)

    run_analysis_cycle(
        env.portfolio, env.broker, env.strategy, env.tracker, env.phase_ctrl,
        env.archive, only_tickers=["FOCUS"],
    )
    # Der Conditional-Entry-Zweig hängt nur an analysis.recommendation=="SKIP",
    # NICHT an strategy.evaluate() (das liefert in buy_cycle_env immer BUY,
    # unabhängig von der Analyzer-Empfehlung – das ist hier bewusst kein
    # Gegenstand des Tests).
    assert _FakeConditionalEntryWatcher.added == [
        {"ticker": "FOCUS", "trigger_price": 90.0, "cur_price": 100.0}
    ]
