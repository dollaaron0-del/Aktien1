"""
Charakterisierungs-Tests für bot/runner.py – Roadmap 4.4a Vorbereitung.

runner.py ist mit 1745 Zeilen riskanter als scheduler.py: die Business-Logik
selbst (run_analysis_cycle, 1005 Zeilen) ist der Monolith, nicht nur eine
Registrierungsstruktur. Diese Tests sichern zunächst die 13 kleinen, echten
Helper-Funktionen ab (bisher 0% Abdeckung, bis auf _valid_price/_make_collectors
in anderen Testdateien) – als risikoarmer erster Schritt, bevor run_analysis_cycle
selbst angefasst wird.
"""
import io
import json
import os
from types import SimpleNamespace

import pytest
from rich.console import Console

import bot.runner as runner_mod
from bot.runner import (
    _base_symbol,
    _ensure_current_price,
    _get_experience_store,
    _is_crypto,
    _is_eu_stock,
    _make_focus_ctrl,
    _make_phase_ctrl,
    _normalize_ticker,
    _print_analysis,
    _progress_bar,
    _safe_collect,
    collect_news,
    pop_daily_actions,
    record_daily_actions,
)


# ── _get_experience_store (lazy Singleton, fail-open) ────────────────────────

@pytest.fixture(autouse=True)
def _reset_experience_store_singleton(monkeypatch):
    monkeypatch.setattr(runner_mod, "_experience_store", None)
    monkeypatch.setattr(runner_mod, "_experience_store_tried", False)


def test_get_experience_store_caches_success(monkeypatch):
    calls = []

    class _FakeStore:
        def __init__(self):
            calls.append(1)

    import analyzers.experience_store as es_mod
    monkeypatch.setattr(es_mod, "ExperienceStore", _FakeStore)

    first = _get_experience_store()
    second = _get_experience_store()
    assert isinstance(first, _FakeStore)
    assert first is second
    assert len(calls) == 1  # nur einmal konstruiert, danach gecacht


def test_get_experience_store_fail_open_on_error(monkeypatch):
    import analyzers.experience_store as es_mod

    def _boom():
        raise RuntimeError("DB kaputt")

    monkeypatch.setattr(es_mod, "ExperienceStore", _boom)

    assert _get_experience_store() is None
    # Zweiter Aufruf versucht NICHT erneut (bereits als "tried" markiert)
    assert _get_experience_store() is None


# ── _ensure_current_price ─────────────────────────────────────────────────────

def test_ensure_current_price_none_dict_creates_ticker_entry():
    out = _ensure_current_price("AAPL", None, broker=None)
    assert out == {"ticker": "AAPL"}


def test_ensure_current_price_passes_through_valid_price():
    out = _ensure_current_price("AAPL", {"current_price": 150.0}, broker=None)
    assert out["current_price"] == 150.0


def test_ensure_current_price_broker_fallback_fills_nan():
    class _Broker:
        def get_price(self, ticker):
            return 42.5

    out = _ensure_current_price("SPCX", {"current_price": float("nan")}, broker=_Broker())
    assert out["current_price"] == 42.5


def test_ensure_current_price_broker_without_get_price_unchanged():
    out = _ensure_current_price("SPCX", {"current_price": None}, broker=object())
    assert out["current_price"] is None


def test_ensure_current_price_broker_raises_returns_original():
    class _BrokenBroker:
        def get_price(self, ticker):
            raise ConnectionError("IBKR down")

    out = _ensure_current_price("SPCX", {"current_price": 0}, broker=_BrokenBroker())
    assert out["current_price"] == 0  # unverändert, kein Crash


def test_ensure_current_price_broker_returns_invalid_price_unchanged():
    class _Broker:
        def get_price(self, ticker):
            return float("nan")

    out = _ensure_current_price("SPCX", {"current_price": None}, broker=_Broker())
    assert out["current_price"] is None


# ── record_daily_actions / pop_daily_actions ─────────────────────────────────

@pytest.fixture()
def _daily_actions_path(tmp_path, monkeypatch):
    path = str(tmp_path / "daily_actions.json")
    monkeypatch.setattr(runner_mod, "_DAILY_ACTIONS_PATH", path)
    return path


def test_record_then_pop_roundtrip(_daily_actions_path):
    record_daily_actions(["BUY AAPL"])
    record_daily_actions(["SELL TSLA"])
    assert pop_daily_actions() == ["BUY AAPL", "SELL TSLA"]


def test_pop_daily_actions_no_file_returns_empty(_daily_actions_path):
    assert pop_daily_actions() == []


def test_pop_daily_actions_clears_file(_daily_actions_path):
    record_daily_actions(["BUY AAPL"])
    pop_daily_actions()
    assert not os.path.exists(_daily_actions_path)


def test_pop_daily_actions_ignores_stale_date(_daily_actions_path):
    with open(_daily_actions_path, "w", encoding="utf-8") as f:
        json.dump({"date": "2000-01-01", "actions": ["OLD ACTION"]}, f)
    assert pop_daily_actions() == []


def test_record_daily_actions_empty_list_is_noop(_daily_actions_path):
    record_daily_actions([])
    assert not os.path.exists(_daily_actions_path)


# ── _is_crypto / _normalize_ticker / _is_eu_stock / _base_symbol ────────────

def test_is_crypto_known_symbol():
    assert _is_crypto("BTC") is True


def test_is_crypto_usd_pair_suffix():
    assert _is_crypto("SOMECOIN/USD") is True


def test_is_crypto_stock_ticker_is_false():
    assert _is_crypto("AAPL") is False


def test_normalize_ticker_known_correction():
    assert _normalize_ticker("VW.DE") == "VOW3.DE"
    assert _normalize_ticker("lvmh") == "MC.PA"  # case-insensitiv


def test_normalize_ticker_unknown_passthrough():
    assert _normalize_ticker("AAPL") == "AAPL"


def test_is_eu_stock_suffix_match():
    assert _is_eu_stock("SAP.DE") is True
    assert _is_eu_stock("AAPL") is False


def test_base_symbol_strips_suffix():
    assert _base_symbol("ASML.AS") == "ASML"
    assert _base_symbol("AAPL") == "AAPL"


# ── _make_phase_ctrl / _make_focus_ctrl ──────────────────────────────────────

def test_make_phase_ctrl_uses_config_values(monkeypatch):
    monkeypatch.setattr(runner_mod.config, "initial_capital", 10_000.0)
    monkeypatch.setattr(runner_mod.config, "growth_target_multiple", 3.0)
    monkeypatch.setattr(runner_mod.config, "monthly_distribution_eur", 500.0)
    monkeypatch.setattr(runner_mod.config, "distribution_buffer_months", 6)

    ctrl = _make_phase_ctrl()
    assert ctrl.initial_capital == 10_000.0
    assert ctrl.growth_target == 30_000.0
    assert ctrl.monthly_target == 500.0
    assert ctrl.buffer_months == 6


def test_make_focus_ctrl_uses_config_values(monkeypatch):
    monkeypatch.setattr(runner_mod.config, "focus_mode", "WEALTH_BUILDING")
    monkeypatch.setattr(runner_mod.config, "target_goal_amount", None)
    monkeypatch.setattr(runner_mod.config, "target_goal_date", None)
    monkeypatch.setattr(runner_mod.config, "initial_capital", 10_000.0)

    ctrl = _make_focus_ctrl()
    assert ctrl.mode == "WEALTH_BUILDING"
    assert ctrl.initial_capital == 10_000.0


# ── _safe_collect ──────────────────────────────────────────────────────────

def test_safe_collect_returns_result():
    assert _safe_collect("dummy", lambda: [{"title": "x"}]) == [{"title": "x"}]


def test_safe_collect_none_result_becomes_empty_list():
    assert _safe_collect("dummy", lambda: None) == []


def test_safe_collect_exception_returns_empty_and_notes_error(monkeypatch):
    noted = []

    class _FakeMonitor:
        def note_error(self, name):
            noted.append(name)

    import analyzers.source_monitor as sm_mod
    monkeypatch.setattr(sm_mod, "get_monitor", lambda: _FakeMonitor())

    def _boom():
        raise ConnectionError("Quelle down")

    assert _safe_collect("broken_source", _boom) == []
    assert noted == ["broken_source"]


# ── _progress_bar ─────────────────────────────────────────────────────────

def test_progress_bar_zero_and_full():
    assert _progress_bar(0.0) == "[░░░░░░░░░░░░░░░░░░░░]"
    assert _progress_bar(100.0) == "[████████████████████]"


def test_progress_bar_partial_width():
    assert _progress_bar(50.0, width=10) == "[█████░░░░░]"


# ── _print_analysis (nur: darf nicht crashen + zeigt Kerninfos) ─────────────

def _fake_analysis(**overrides):
    base = dict(
        direction="NEUTRAL", sentiment_score=0.0, confidence="LOW",
        recommendation="SKIP", bull_case="", bear_case="", debate_winner=None,
        entry_rationale="", target_price=None, target_price_rationale="",
        thesis_valid=None, thesis_break_reason="", key_catalysts=[], risk_factors=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_print_analysis_shows_recommendation_and_confidence(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(runner_mod, "console", Console(file=buf, width=120))

    a = _fake_analysis(direction="BULLISH", confidence="HIGH", recommendation="BUY", sentiment_score=0.8)
    _print_analysis(a)

    out = buf.getvalue()
    assert "BULLISH" in out
    assert "HIGH" in out
    assert "BUY" in out


def test_print_analysis_shows_thesis_broken(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(runner_mod, "console", Console(file=buf, width=120))

    a = _fake_analysis(thesis_valid=False, thesis_break_reason="Umsatzwarnung")
    _print_analysis(a)

    assert "THESE GEBROCHEN" in buf.getvalue()
    assert "Umsatzwarnung" in buf.getvalue()


def test_print_analysis_never_raises_on_minimal_fields(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(runner_mod, "console", Console(file=buf, width=120))
    _print_analysis(_fake_analysis())  # darf NICHT werfen


# ── collect_news ─────────────────────────────────────────────────────────────

class _FakeArchive:
    def __init__(self):
        self.stored = []

    def store(self, ticker, items):
        self.stored.append((ticker, items))


class _FakeNewsVelocity:
    calls = []

    def record_articles(self, ticker, articles):
        _FakeNewsVelocity.calls.append((ticker, articles))


class _FakeCollector:
    def __init__(self, items):
        self._items = items
        self.called_with = None

    def collect(self, ticker):
        self.called_with = ticker
        return self._items


@pytest.fixture(autouse=True)
def _isolate_collect_news_side_effects(monkeypatch):
    """collect_news() schreibt normalerweise in echte SQLite/JSON/Singleton-Zustände
    (NewsArchive, source_monitor, NewsVelocityAnalyzer) – hier neutralisiert, damit
    der Test hermetisch bleibt und keine echten data/*-Dateien anfasst."""
    import analyzers.source_monitor as sm_mod

    class _NoopMonitor:
        def note_result(self, *a, **kw):
            pass

    monkeypatch.setattr(sm_mod, "get_monitor", lambda: _NoopMonitor())
    monkeypatch.setattr(runner_mod, "NewsVelocityAnalyzer", _FakeNewsVelocity)
    monkeypatch.setattr(runner_mod, "_semantic_dedup", None)  # deterministischer Dedup-Pfad
    _FakeNewsVelocity.calls = []


def test_collect_news_aggregates_and_dedupes():
    archive = _FakeArchive()
    collectors = {
        "yahoo": _FakeCollector([{"source": "yahoo", "title": "Same Headline"}]),
        "newsapi": _FakeCollector([{"source": "newsapi", "title": "Same Headline"}]),
        "wire": _FakeCollector([{"source": "wire", "title": "Different"}]),
    }

    unique, breakdown = collect_news("AAPL", archive, collectors)

    assert breakdown == {"yahoo": 1, "newsapi": 1, "wire": 1}
    assert len(unique) == 2  # "Same Headline" dedupliziert, "Different" bleibt
    assert archive.stored[0][0] == "AAPL"
    assert len(archive.stored[0][1]) == 3  # ungefiltert gespeichert, Dedup nur im Rückgabewert


def test_collect_news_crypto_ticker_skips_stock_only_collectors():
    archive = _FakeArchive()
    stock_only = _FakeCollector([{"source": "sec_edgar", "title": "8-K"}])
    crypto_ok = _FakeCollector([{"source": "yahoo", "title": "BTC news"}])
    collectors = {"sec_edgar": stock_only, "yahoo": crypto_ok}

    unique, breakdown = collect_news("BTC", archive, collectors)

    assert stock_only.called_with is None  # nie aufgerufen – nicht crypto-relevant
    assert crypto_ok.called_with == "BTC"
    assert breakdown["sec_edgar"] == 0
    assert breakdown["yahoo"] == 1


def test_collect_news_none_collector_counted_as_zero():
    archive = _FakeArchive()
    collectors = {"broken": None, "yahoo": _FakeCollector([{"source": "yahoo", "title": "x"}])}

    unique, breakdown = collect_news("AAPL", archive, collectors)

    assert breakdown["broken"] == 0
    assert breakdown["yahoo"] == 1


def test_collect_news_empty_collectors_returns_empty():
    """Regressionstest für einen echten Bug: ThreadPoolExecutor(max_workers=0)
    warf ValueError, wenn active_collectors leer ist – passierte nicht nur bei
    einem leeren Dict, sondern auch wenn ALLE Collector-Inits fehlschlagen
    (_make_collectors() setzt sie dann auf None) oder für einen Crypto-Ticker
    keiner der Collectors in _CRYPTO_ALLOWED verfügbar ist. Gefixt in
    collect_news() (bot/runner.py) durch Guard um den ThreadPoolExecutor-Block."""
    archive = _FakeArchive()

    unique, breakdown = collect_news("AAPL", archive, {})

    assert unique == []
    assert breakdown == {}
    assert archive.stored[0] == ("AAPL", [])
