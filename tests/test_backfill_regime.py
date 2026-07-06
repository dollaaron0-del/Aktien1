"""
Tests für scripts/backfill_regime.py — Regime-Nachlabeln historischer Zeilen.

Netzfrei: synthetische Bars, DB in tmp_path. Der yfinance-Pfad (_load_bars)
wird nicht getestet (bereits über backfill_outcomes abgedeckt).
"""
import math

import pytest

from analyzers.experience_store import ExperienceStore
from scripts.backfill_regime import (
    label_for, regime_by_date, to_bot_label, run,
)


def _bars(closes, start_day=1):
    """Chronologische Tages-Bars ab 2026-01-<start_day> (nur Werktage egal —
    regime_by_date nutzt die Strings nur zum Sortieren/Nachschlagen)."""
    out = []
    for i, c in enumerate(closes):
        day = start_day + i
        out.append({"date": f"2026-01-{day:02d}" if day <= 31 else f"2026-02-{day-31:02d}",
                    "close": c})
    return out


# ── Mapping ────────────────────────────────────────────────────────────────────
def test_to_bot_label_mapping():
    assert to_bot_label("BEAR_VOLATILE") == "CRISIS"
    assert to_bot_label("BEAR_CALM") == "BEAR"
    assert to_bot_label("SIDE_CALM") == "NEUTRAL"
    assert to_bot_label("SIDE_VOLATILE") == "NEUTRAL"
    assert to_bot_label("BULL_CALM") == "BULL"
    assert to_bot_label("BULL_VOLATILE") == "BULL"
    assert to_bot_label("UNKNOWN") is None
    assert to_bot_label("") is None


# ── regime_by_date ─────────────────────────────────────────────────────────────
def test_rising_market_labels_bull():
    closes = [100 * (1.004 ** i) for i in range(50)]   # stetig aufwärts
    by = regime_by_date(_bars(closes), window=40, min_bars=25)
    assert by, "genügend Historie → Labels vorhanden"
    # späte Tage sind BULL
    assert by[max(by)] == "BULL"


def test_crashing_market_labels_crisis_or_bear():
    closes = [100 * (0.99 ** i) for i in range(50)]    # -1%/Tag → tiefer Drawdown
    by = regime_by_date(_bars(closes), window=40, min_bars=25)
    assert by[max(by)] in ("BEAR", "CRISIS")


def test_early_days_have_no_label():
    closes = [100 + i * 0.1 for i in range(40)]
    by = regime_by_date(_bars(closes), window=40, min_bars=25)
    # die ersten (min_bars-1) Tage dürfen NICHT gelabelt sein (kein Raten)
    assert min(by) >= _bars(closes)[24]["date"]


def test_nan_and_missing_closes_ignored():
    closes = [100 + i * 0.1 for i in range(40)]
    bars = _bars(closes)
    bars[5]["close"] = float("nan")
    bars[6]["close"] = None
    by = regime_by_date(bars, window=40, min_bars=25)
    assert by  # NaN/None-Bars werden verworfen, kein Crash


# ── label_for (Datums-Fallback) ────────────────────────────────────────────────
def test_label_for_falls_back_to_previous_trading_day():
    by = {"2026-01-09": "BULL", "2026-01-12": "BEAR"}
    assert label_for("2026-01-12T10:00:00", by) == "BEAR"    # exakter Tag
    assert label_for("2026-01-10T08:00:00", by) == "BULL"    # Samstag → Freitag
    assert label_for("2026-01-01T08:00:00", by) is None      # vor aller Historie
    assert label_for("", by) is None


# ── run() Ende-zu-Ende gegen tmp-DB (Preisquelle gemockt) ──────────────────────
def test_run_labels_only_null_regime(tmp_path, monkeypatch):
    db = str(tmp_path / "exp.db")
    s = ExperienceStore(db_path=db)
    # 2 Zeilen ohne Regime + 1 mit bereits gesetztem (bleibt unangetastet)
    s.upsert_decision({"decided_at": "2026-01-28T10:00:00", "ticker": "AAA"})
    s.upsert_decision({"decided_at": "2026-01-30T10:00:00", "ticker": "BBB"})
    s.upsert_decision({"decided_at": "2026-01-30T11:00:00", "ticker": "CCC",
                       "regime": "CRISIS"})
    s.close()

    closes = [100 * (1.004 ** i) for i in range(30)]         # klarer Bulle
    import scripts.backfill_regime as br
    monkeypatch.setattr(br, "_load_bars", lambda t, st: _bars(closes))

    counts = run(db_path=db, window=30)
    assert counts["candidates"] == 2                          # CCC nicht dabei
    assert counts["labeled"] == 2
    assert counts["distribution"] == {"BULL": 2}

    s2 = ExperienceStore(db_path=db)
    rows = {r["ticker"]: r["regime"] for r in
            s2._conn.execute("SELECT ticker, regime FROM decisions")}
    s2.close()
    assert rows["AAA"] == "BULL" and rows["BBB"] == "BULL"
    assert rows["CCC"] == "CRISIS"                            # unangetastet

    # Idempotent: zweiter Lauf findet nichts mehr
    monkeypatch.setattr(br, "_load_bars", lambda t, st: _bars(closes))
    counts2 = run(db_path=db, window=30)
    assert counts2["candidates"] == 0


def test_run_aborts_honestly_on_thin_history(tmp_path, monkeypatch):
    db = str(tmp_path / "exp.db")
    s = ExperienceStore(db_path=db)
    s.upsert_decision({"decided_at": "2026-01-28T10:00:00", "ticker": "AAA"})
    s.close()

    import scripts.backfill_regime as br
    monkeypatch.setattr(br, "_load_bars", lambda t, st: _bars([100, 101, 102]))
    counts = run(db_path=db)
    assert counts["labeled"] == 0                             # lieber Lücke als Raten
