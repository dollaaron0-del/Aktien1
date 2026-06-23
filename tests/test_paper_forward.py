"""
Tests für strategy_lab.paper_forward (Roadmap-Punkt c – Paper-Forward-Validierung).

Netzfrei: Marktdaten über injizierte Loader, Allokator-Signale per monkeypatch.
Prüft die Exit-Mechanik (spiegelt engine._simulate), die Vorwärts-Logik
(OFFEN solange Zukunft fehlt) sowie record/update/summary/replay.
"""
import numpy as np
import pandas as pd

from backtesting.engine import BacktestConfig
from strategy_lab import allocator, paper_forward as pf


# ── Synthetische Kurspfade ──────────────────────────────────────────────────────
def _ramp_df(n=60, daily=0.015, base=100.0, start="2022-01-03"):
    """Monotoner Pfad; Open=High=Low=Close für präzise TP/SL-Kontrolle."""
    price = base * (1 + daily) ** np.arange(n)
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame({"Open": price, "High": price, "Low": price,
                         "Close": price, "Volume": np.full(n, 1e6)}, index=idx)


# ── _resolve: Exit-Mechanik ─────────────────────────────────────────────────────
def test_resolve_uptrend_closes_with_tp_and_timestop():
    df = _ramp_df(n=60, daily=0.015)            # steigt klar über TP1/TP2
    res = pf._resolve(df, 0, BacktestConfig())
    assert res["status"] == pf.CLOSED
    assert res["exit_reason"] == "time_stop"
    assert res["tp1_hit"] and res["tp2_hit"]
    assert res["return_pct"] > 0


def test_resolve_downtrend_hits_stop_loss():
    df = _ramp_df(n=60, daily=-0.02)            # fällt → SL
    res = pf._resolve(df, 0, BacktestConfig())
    assert res["status"] == pf.CLOSED
    assert res["exit_reason"] == "SL"
    assert res["return_pct"] < 0


def test_resolve_stays_open_when_future_missing():
    df = _ramp_df(n=6, daily=0.01)              # weit weniger als max_hold (45) Balken
    res = pf._resolve(df, 0, BacktestConfig())
    assert res["status"] == pf.OPEN
    assert res["exit_date"] is None
    assert res["return_pct"] > 0                # mark-to-market, steigend


def test_entry_index_first_bar_after_signal():
    df = _ramp_df(n=10)
    sig = pf._to_day(df.index[3])
    assert pf._entry_index(df, sig) == 4
    # Signal auf letztem Balken → noch kein Entry-Balken
    assert pf._entry_index(df, pf._to_day(df.index[-1])) is None


# ── record_signals: Aufnahme + Dedup ────────────────────────────────────────────
def _fire(monkeypatch, fired):
    monkeypatch.setattr(allocator, "current_signals", lambda *a, **k: fired)


def test_record_creates_and_dedupes(monkeypatch):
    _fire(monkeypatch, {"ts_momentum": ["AAA", "BBB"]})
    plan = [{"strategy": "ts_momentum", "weight": 1.0, "params": {}}]
    ledger = {"positions": []}
    loader = lambda t, y: _ramp_df()
    n1 = pf.record_signals(ledger, "2022-03-01", ["AAA", "BBB"], loader, plan=plan)
    assert n1 == 2
    assert {p["ticker"] for p in ledger["positions"]} == {"AAA", "BBB"}
    # erneut: offene Positionen werden nicht doppelt eröffnet
    n2 = pf.record_signals(ledger, "2022-03-02", ["AAA", "BBB"], loader, plan=plan)
    assert n2 == 0


def test_record_empty_plan_noop():
    ledger = {"positions": []}
    assert pf.record_signals(ledger, "2022-03-01", ["AAA"], lambda t, y: _ramp_df(), plan=[]) == 0


# ── Voller Lebenszyklus PENDING → OPEN → CLOSED ─────────────────────────────────
def test_lifecycle_pending_open_closed(monkeypatch):
    df = _ramp_df(n=200, daily=0.01)
    loader = lambda t, y: df
    plan = [{"strategy": "ts_momentum", "weight": 1.0, "params": {}}]
    _fire(monkeypatch, {"ts_momentum": ["AAA"]})

    sig_date = pf._to_day(df.index[100])
    ledger = {"positions": []}
    pf.record_signals(ledger, sig_date, ["AAA"], loader, plan=plan)

    # as_of == Signal-Tag → Entry-Balken existiert noch nicht → PENDING
    pf.update_positions(ledger, loader, as_of=df.index[100])
    assert ledger["positions"][0]["status"] == pf.PENDING

    # ein paar Balken später, aber < max_hold → OPEN
    pf.update_positions(ledger, loader, as_of=df.index[110])
    p = ledger["positions"][0]
    assert p["status"] == pf.OPEN
    assert p["entry_price"] is not None

    # weit nach der Haltedauer → CLOSED (time_stop, steigend → positiv)
    pf.update_positions(ledger, loader, as_of=df.index[199])
    p = ledger["positions"][0]
    assert p["status"] == pf.CLOSED
    assert p["return_pct"] > 0

    # idempotent: erneutes update lässt Geschlossene unverändert
    before = dict(p)
    pf.update_positions(ledger, loader, as_of=df.index[199])
    assert ledger["positions"][0] == before


# ── summary ─────────────────────────────────────────────────────────────────────
def test_summary_metrics():
    ledger = {"positions": [
        {"strategy": "a", "ticker": "X", "signal_date": "2022-01-01", "weight": 0.5,
         "cfg": {}, "status": pf.CLOSED, "entry_date": "2022-01-02", "entry_price": 100.0,
         "tp1_hit": True, "tp2_hit": False, "exit_date": "2022-02-01", "exit_price": 110.0,
         "return_pct": 0.1, "exit_reason": "time_stop", "last_checked": "2022-02-01"},
        {"strategy": "a", "ticker": "Y", "signal_date": "2022-01-01", "weight": 0.5,
         "cfg": {}, "status": pf.CLOSED, "entry_date": "2022-01-02", "entry_price": 100.0,
         "tp1_hit": False, "tp2_hit": False, "exit_date": "2022-02-01", "exit_price": 95.0,
         "return_pct": -0.05, "exit_reason": "SL", "last_checked": "2022-02-01"},
        {"strategy": "b", "ticker": "Z", "signal_date": "2022-01-01", "weight": 0.3,
         "cfg": {}, "status": pf.OPEN, "entry_date": "2022-01-02", "entry_price": 100.0,
         "tp1_hit": False, "tp2_hit": False, "exit_date": None, "exit_price": None,
         "return_pct": 0.03, "exit_reason": "open", "last_checked": "2022-01-20"},
    ]}
    s = pf.summary(ledger)
    assert s["n_positions"] == 3
    assert s["n_closed"] == 2 and s["n_open"] == 1
    assert s["win_rate"] == 0.5
    assert abs(s["avg_return_closed"] - 0.025) < 1e-9
    assert s["by_strategy"]["a"]["closed"] == 2
    assert s["by_strategy"]["a"]["win_rate"] == 0.5


def _closed(ticker, ret, entry="2022-01-02", exit_="2022-02-01", weight=0.5):
    return {"strategy": "a", "ticker": ticker, "signal_date": "2022-01-01", "weight": weight,
            "cfg": {}, "status": pf.CLOSED, "entry_date": entry, "entry_price": 100.0,
            "tp1_hit": False, "tp2_hit": False, "exit_date": exit_, "exit_price": 100.0,
            "return_pct": ret, "exit_reason": "time_stop", "last_checked": exit_}


def test_summary_risk_metrics():
    ledger = {"positions": [_closed("X", 0.1), _closed("Y", -0.05)]}
    s = pf.summary(ledger)
    assert abs(s["max_drawdown"] - (-0.05)) < 1e-9        # Equity 1.1→1.045
    assert s["sharpe_trade"] == 0.2357                    # mean/std (ddof=1), trade-level
    assert s["avg_holding_days"] == 30.0                  # 02.01 → 01.02


def test_benchmark_buy_hold():
    df = _ramp_df(n=60, daily=0.01)
    ledger = {"positions": [
        _closed("X", 0.05, entry=pf._to_day(df.index[10]), exit_=pf._to_day(df.index[40]))]}
    bench = pf.benchmark_buy_hold(ledger, lambda t, y: df)
    assert bench["n_tickers"] == 1
    # Buy&Hold über 30 Balken zu +1%/Balken
    assert abs(bench["buy_hold_return"] - (1.01 ** 30 - 1)) < 1e-3


def test_benchmark_none_when_no_entries():
    ledger = {"positions": [{"strategy": "a", "ticker": "X", "signal_date": "2022-01-01",
                             "weight": 1.0, "cfg": {}, "status": pf.PENDING, "entry_date": None,
                             "entry_price": None, "tp1_hit": False, "tp2_hit": False,
                             "exit_date": None, "exit_price": None, "return_pct": 0.0,
                             "exit_reason": "", "last_checked": None}]}
    assert pf.benchmark_buy_hold(ledger, lambda t, y: _ramp_df()) is None


# ── Ledger-IO ────────────────────────────────────────────────────────────────────
def test_ledger_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "pf.json"
    monkeypatch.setattr(pf, "_LEDGER_FILE", str(f))
    led = pf.load_ledger()
    led["positions"].append({"strategy": "a", "ticker": "X", "signal_date": "2022-01-01",
                             "weight": 1.0, "cfg": {}, "status": pf.PENDING,
                             "entry_date": None, "entry_price": None, "tp1_hit": False,
                             "tp2_hit": False, "exit_date": None, "exit_price": None,
                             "return_pct": 0.0, "exit_reason": "", "last_checked": None})
    pf.save_ledger(led)
    again = pf.load_ledger()
    assert again["positions"][0]["ticker"] == "X"
    assert "updated_at" in again


# ── replay-Bootstrap ─────────────────────────────────────────────────────────────
def test_replay_bootstraps_track(monkeypatch):
    df = _ramp_df(n=160, daily=0.01)
    base_loader = lambda t, y: df
    plan = [{"strategy": "ts_momentum", "weight": 1.0, "params": {}}]

    calls = {"n": 0}
    def fake_signals(universe, loader, plan=None, years=2):
        calls["n"] += 1
        return {"ts_momentum": ["AAA"]} if calls["n"] == 1 else {"ts_momentum": []}
    monkeypatch.setattr(allocator, "current_signals", fake_signals)

    ledger = pf.replay(["AAA"], base_loader, plan,
                       start=df.index[0], end=df.index[-1], step_days=5)
    assert ledger["mode"] == "replay"
    assert ledger["positions"], "Replay sollte mindestens eine Position eröffnen"
    # bei steigendem Pfad + voller Historie schließt die Position positiv
    closed = [p for p in ledger["positions"] if p["status"] == pf.CLOSED]
    assert closed and all(p["return_pct"] > 0 for p in closed)
