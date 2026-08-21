"""
Tests für dashboard/dry_run.py (Ausbau-Roadmap H1.5 — Trockenlauf).

Schwerpunkt ist die KERNANFORDERUNG des Punkts: Seiteneffekt-Freiheit.
Die wird hier nicht behauptet, sondern nachgewiesen — Portfolio-Datei,
Signal-Queue und Decision-Log werden vor/nach dem Lauf verglichen.
"""
import json

import pytest

from dashboard.dry_run import dry_run, latest_analysis


@pytest.fixture(autouse=True)
def _stub_price(monkeypatch):
    """Kurs-Abruf in ALLEN Tests fest verdrahten: der Trockenlauf holt den
    Kurs über den echten Broker-Weg (yfinance) — im Test darf das weder
    Netz brauchen noch je nach Marktlage andere Ergebnisse liefern."""
    monkeypatch.setattr("dashboard.dry_run._current_price", lambda t: 120.0)


def _seed_analysis(monkeypatch, tmp_path, ticker="ZDRY1", recommendation="BUY",
                   score=0.95, confidence="HIGH", target_price=120.0, sources=8):
    import analyzers.analysis_log as alog_mod
    db_path = str(tmp_path / "analysis_log_test.db")
    monkeypatch.setattr(alog_mod, "DB_PATH", db_path)
    alog = alog_mod.AnalysisLog()
    alog._conn.execute(
        "INSERT INTO analyses (analyzed_at, ticker, recommendation, direction, "
        "sentiment_score, confidence, entry_rationale, target_price, "
        "suggested_hold, sources_used) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("2026-07-16T09:00:00", ticker, recommendation, "BULLISH", score,
         confidence, "Starkes Momentum", target_price, 10, sources),
    )
    alog._conn.commit()
    return db_path


# ── Grundlagen / Fehlerfälle ─────────────────────────────────────────────────

def test_dry_run_without_ticker():
    res = dry_run("")
    assert res["ok"] is False
    assert "Kein Ticker" in res["error"]


def test_dry_run_without_any_analysis(monkeypatch, tmp_path):
    _seed_analysis(monkeypatch, tmp_path)  # nur ZDRY1 existiert
    res = dry_run("GIBTSNICHT")
    assert res["ok"] is False
    assert "keine Analyse" in res["error"]
    # Der Hinweis muss auf den richtigen Weg zeigen (H1.2-Queue):
    assert "Werksauftrag" in res["error"]


def test_dry_run_without_usable_price(monkeypatch, tmp_path):
    """Ohne Kurs kein Lauf — und die Meldung sagt ehrlich, dass der echte
    Bot den Ticker dann genauso überspringen würde."""
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDRY2")
    monkeypatch.setattr("dashboard.dry_run._current_price", lambda t: None)
    res = dry_run("ZDRY2")
    assert res["ok"] is False
    assert "Kein aktueller Kurs" in res["error"]


def test_dry_run_uses_broker_price_not_claude_target(monkeypatch, tmp_path, fresh_portfolio):
    """Der Kurs MUSS vom Broker kommen, nicht aus `target_price` — das ist
    Claudes Kursziel und ergäbe eine falsche Positionsgröße."""
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDRYP", target_price=999.0)
    monkeypatch.setattr("dashboard.dry_run._current_price", lambda t: 42.0)
    res = dry_run("ZDRYP")
    assert res["price"] == 42.0  # nicht 999.0


def test_latest_analysis_reads_real_row(monkeypatch, tmp_path):
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDRY3")
    row = latest_analysis("zdry3")  # Kleinschreibung muss gehen
    assert row is not None
    assert row["ticker"] == "ZDRY3"


def test_latest_analysis_fail_open(monkeypatch):
    import analyzers.analysis_log as alog_mod

    class _Boom:
        def get_recent(self, limit=1, ticker=None):
            raise RuntimeError("kaputt")

    monkeypatch.setattr(alog_mod, "AnalysisLog", _Boom)
    assert latest_analysis("X") is None


# ── Der eigentliche Lauf ─────────────────────────────────────────────────────

def test_dry_run_returns_a_decision(monkeypatch, tmp_path, fresh_portfolio):
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDRY4")
    res = dry_run("ZDRY4")
    assert res["ok"] is True
    assert res["action"] in ("BUY", "SELL", "HOLD", "SKIP")
    assert res["ticker"] == "ZDRY4"
    assert res["price"] == 120.0
    assert res["analysis"]["ticker"] == "ZDRY4"


def test_dry_run_reports_gate_reason_for_weak_signal(monkeypatch, tmp_path, fresh_portfolio):
    """Ein schwaches Signal muss an einem echten Gate scheitern — und der
    Grund ist genau das, was der Punkt sichtbar machen soll."""
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDRY5",
                   recommendation="SKIP", score=0.05, confidence="LOW")
    res = dry_run("ZDRY5")
    assert res["ok"] is True
    assert res["action"] == "SKIP"
    assert res["reason"]  # es MUSS einen nachvollziehbaren Grund geben


def test_dry_run_fail_open_on_broken_strategy(monkeypatch, tmp_path, fresh_portfolio):
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDRY6")
    import strategy.swing_strategy as ss_mod

    class _Boom:
        pass

    monkeypatch.setattr(ss_mod, "SwingStrategy", _Boom)
    res = dry_run("ZDRY6")
    assert res["ok"] is False
    assert "fehlgeschlagen" in res["error"]


# ══ SEITENEFFEKT-FREIHEIT — die Kernanforderung, nachgewiesen ═══════════════

def test_dry_run_does_not_touch_the_portfolio_file(monkeypatch, tmp_path, fresh_portfolio):
    """Das Portfolio darf sich durch einen Trockenlauf nicht ändern —
    weder Cash noch Positionen noch die Datei selbst."""
    import portfolio.portfolio as port_mod

    db = port_mod.PORTFOLIO_DB
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDRY7")

    with open(db, "rb") as fh:
        before = fh.read()
    cash_before = fresh_portfolio.cash

    dry_run("ZDRY7")

    with open(db, "rb") as fh:
        assert fh.read() == before, "Trockenlauf hat die Portfolio-Datei verändert!"
    from portfolio.portfolio import Portfolio
    assert Portfolio().cash == cash_before


def test_dry_run_restores_portfolio_db_path(monkeypatch, tmp_path, fresh_portfolio):
    """Der Pfad-Zeiger muss danach wieder auf die echte DB zeigen — sonst
    schriebe der NÄCHSTE echte Aufruf ins Temp-Verzeichnis."""
    import portfolio.portfolio as port_mod

    before = port_mod.PORTFOLIO_DB
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDRY8")
    dry_run("ZDRY8")
    assert port_mod.PORTFOLIO_DB == before


def test_dry_run_restores_path_even_when_strategy_raises(monkeypatch, tmp_path, fresh_portfolio):
    import portfolio.portfolio as port_mod
    import strategy.swing_strategy as ss_mod

    before = port_mod.PORTFOLIO_DB
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDRY9")
    monkeypatch.setattr(ss_mod, "SwingStrategy", type("_Boom", (), {}))
    dry_run("ZDRY9")
    assert port_mod.PORTFOLIO_DB == before  # finally greift


def test_dry_run_never_enqueues_a_signal(monkeypatch, tmp_path, fresh_portfolio):
    """evaluate() würde ein BUY bei vollen Slots in die Signal-Queue
    VORMERKEN (swing_strategy.py ~343). Der Trockenlauf übergibt
    signal_queue=None — es darf nie etwas eingereiht werden."""
    from portfolio.signal_queue import SignalQueue

    enqueued = []
    monkeypatch.setattr(
        SignalQueue, "enqueue",
        lambda self, **kw: enqueued.append(kw),
    )
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDRY10")
    dry_run("ZDRY10")
    assert enqueued == [], "Trockenlauf hat ein Signal in die Queue geschrieben!"


def test_dry_run_writes_nothing_to_decision_log(monkeypatch, tmp_path, fresh_portfolio):
    """Der Trockenlauf darf keine Entscheidung protokollieren — sonst
    verfälschte er den Funnel im Entscheidungen-Tab."""
    from analyzers.decision_log import DecisionLog

    logged = []
    monkeypatch.setattr(DecisionLog, "log", lambda self, entry: logged.append(entry))
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDRY11")
    dry_run("ZDRY11")
    assert logged == []


def test_dry_run_places_no_order(monkeypatch, tmp_path, fresh_portfolio):
    """Kein Broker-Aufruf — der Trockenlauf nutzt gar keinen Broker."""
    from broker.paper_broker import PaperBroker

    bought = []
    monkeypatch.setattr(PaperBroker, "buy",
                        lambda self, *a, **k: bought.append(a))
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDRY12")
    dry_run("ZDRY12")
    assert bought == []


def test_dry_run_makes_no_llm_call(monkeypatch, tmp_path, fresh_portfolio):
    """Kein Claude, kein Ollama: der Trockenlauf nutzt die GESPEICHERTE
    Analyse. Ein LLM-Aufruf würde Geld kosten bzw. (gemessen 0,12 tok/s)
    den Request minutenlang blockieren."""
    calls = []
    monkeypatch.setattr("requests.post",
                        lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
                            AssertionError("Netz-Aufruf im Trockenlauf!")))
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDRY13")
    res = dry_run("ZDRY13")
    assert res["ok"] is True
    assert calls == []
