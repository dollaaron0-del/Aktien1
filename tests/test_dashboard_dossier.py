"""
Tests für dashboard/dossier.py (Design-Roadmap L1.1 — Personalakten-Kartei).

Netzfrei: alle Quellen gegen isolierte Temp-Dateien/DBs (Muster:
test_dashboard_dry_run.py / test_dashboard_genealogy.py).
"""
import json

import pytest

from dashboard import dossier


@pytest.fixture()
def _profiles(tmp_path, monkeypatch):
    f = tmp_path / "profiles.json"
    f.write_text(json.dumps({"NVDA": {"sector": "Technology",
                                      "industry": "Semiconductors",
                                      "company": "NVIDIA Corp"}}))
    monkeypatch.setattr(dossier, "_PROFILES_FILE", str(f))
    return f


def _seed_analysis(monkeypatch, tmp_path, ticker="ZDOS1", n=3):
    import analyzers.analysis_log as alog_mod
    db_path = str(tmp_path / "analysis_log_test.db")
    monkeypatch.setattr(alog_mod, "DB_PATH", db_path)
    alog = alog_mod.AnalysisLog()
    for i in range(n):
        alog._conn.execute(
            "INSERT INTO analyses (analyzed_at, ticker, recommendation, direction, "
            "sentiment_score, confidence, entry_rationale) VALUES (?,?,?,?,?,?,?)",
            (f"2026-07-{10+i:02d}T09:00:00", ticker, "BUY", "BULLISH",
             0.5 + i * 0.1, "HIGH", "Grund"),
        )
    alog._conn.commit()
    return db_path


def _seeded_store(tmp_path, ticker="ZDOS1"):
    """Baut einen ExperienceStore mit explizitem db_path (Muster
    test_dashboard_calibration_curve.py) — kein DB_PATH-Monkeypatch,
    das griffe wegen des zur Ladezeit gebundenen Default-Parameters
    in ExperienceStore.__init__ nicht."""
    import analyzers.experience_store as es_mod
    store = es_mod.ExperienceStore(db_path=str(tmp_path / "experience_test.db"))
    did = store.upsert_decision({
        "decided_at": "2026-07-10T09:00:00", "ticker": ticker,
        "recommendation": "BUY", "direction": "BULLISH",
        "sentiment_score": 0.8, "confidence": "HIGH",
    })
    store.attach_outcome(did, {"pnl_pct": 4.2, "outcome": "WIN",
                               "exit_reason": "TP", "hold_days": 8,
                               "label_source": "backfill"})
    did2 = store.upsert_decision({
        "decided_at": "2026-07-01T09:00:00", "ticker": ticker,
        "recommendation": "BUY", "direction": "BULLISH",
        "sentiment_score": 0.6, "confidence": "MEDIUM",
    })
    store.attach_outcome(did2, {"pnl_pct": -2.0, "outcome": "LOSS",
                                "exit_reason": "SL", "hold_days": 3,
                                "label_source": "backfill"})
    return store


# ── Profil ───────────────────────────────────────────────────────────────────

def test_profile_reads_known_ticker(_profiles):
    d = dossier.dossier("NVDA")
    assert d["profile"]["sector"] == "Technology"


def test_profile_missing_file_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(dossier, "_PROFILES_FILE", str(tmp_path / "nix.json"))
    d = dossier.dossier("NVDA")
    assert d["profile"] == {}


# ── Analyse-Historie ─────────────────────────────────────────────────────────

def test_analysis_history_chronological_ascending(monkeypatch, tmp_path):
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDOS2", n=3)
    hist = dossier.analysis_history("ZDOS2")
    assert len(hist) == 3
    assert hist[0]["analyzed_at"] < hist[-1]["analyzed_at"]


def test_analysis_history_empty_for_unknown_ticker(monkeypatch, tmp_path):
    _seed_analysis(monkeypatch, tmp_path, ticker="ZDOS3")
    assert dossier.analysis_history("GIBTSNICHT") == []


def test_all_known_tickers_sorted_by_count(monkeypatch, tmp_path):
    import analyzers.analysis_log as alog_mod
    db_path = str(tmp_path / "alog.db")
    monkeypatch.setattr(alog_mod, "DB_PATH", db_path)
    alog = alog_mod.AnalysisLog()
    for ticker, n in (("ZDOS_A", 1), ("ZDOS_B", 3)):
        for i in range(n):
            alog._conn.execute(
                "INSERT INTO analyses (analyzed_at, ticker, recommendation, "
                "direction, sentiment_score, confidence) VALUES (?,?,?,?,?,?)",
                (f"2026-07-{10+i:02d}T09:00:00", ticker, "BUY", "BULLISH", 0.5, "HIGH"),
            )
    alog._conn.commit()
    known = dossier.all_known_tickers()
    tickers = [r["ticker"] for r in known]
    assert tickers.index("ZDOS_B") < tickers.index("ZDOS_A")


def test_all_known_tickers_fail_open(monkeypatch):
    import analyzers.analysis_log as alog_mod

    class _Boom:
        pass

    monkeypatch.setattr(alog_mod, "AnalysisLog", _Boom)
    assert dossier.all_known_tickers() == []


# ── Trade-Bilanz ─────────────────────────────────────────────────────────────

def test_trade_bilanz_wins_losses_avg(tmp_path):
    store = _seeded_store(tmp_path, ticker="ZDOS4")
    b = dossier.trade_bilanz("ZDOS4", store=store)
    assert b["n_trades"] == 2
    assert b["wins"] == 1
    assert b["losses"] == 1
    assert b["avg_pnl_pct"] == pytest.approx((4.2 - 2.0) / 2)
    assert b["rows"][0]["decided_at"] > b["rows"][1]["decided_at"]  # DESC


def test_trade_bilanz_ignores_other_tickers(tmp_path):
    store = _seeded_store(tmp_path, ticker="ZDOS5")
    b = dossier.trade_bilanz("ANDERER", store=store)
    assert b["n_trades"] == 0
    assert b["avg_pnl_pct"] is None


def test_trade_bilanz_fail_open(monkeypatch):
    import analyzers.experience_store as es_mod

    class _Boom:
        pass

    monkeypatch.setattr(es_mod, "ExperienceStore", _Boom)
    b = dossier.trade_bilanz("X")
    assert b == {"n_trades": 0, "wins": 0, "losses": 0, "avg_pnl_pct": None, "rows": []}


# ── Themen/Verwandte ─────────────────────────────────────────────────────────

def test_themes_and_related_real_data():
    out = dossier.themes_and_related("NVDA")
    assert "AI_CHIPS" in out["themes"] or "SEMICONDUCTORS" in out["themes"]
    assert isinstance(out["related"], list)


def test_themes_and_related_unknown_ticker():
    out = dossier.themes_and_related("GIBTSNICHTXYZ")
    assert out == {"themes": [], "related": []}


# ── News-Puls ────────────────────────────────────────────────────────────────

def test_news_pulse_aggregates_daily(tmp_path, monkeypatch):
    f = tmp_path / "news_velocity.json"
    f.write_text(json.dumps({"ZDOS6": [
        {"ts": "2026-07-10T05:00:00+00:00", "count": 10},
        {"ts": "2026-07-10T09:00:00+00:00", "count": 5},
        {"ts": "2026-07-11T05:00:00+00:00", "count": 3},
    ]}))
    monkeypatch.setattr(dossier, "_NEWS_VELOCITY_FILE", str(f))
    pulse = dossier.news_pulse("ZDOS6", days=3)
    by_date = {p["date"]: p["count"] for p in pulse}
    assert by_date["2026-07-10"] == 15
    assert by_date["2026-07-11"] == 3
    assert len(pulse) == 3


def test_news_pulse_missing_ticker_fail_open(tmp_path, monkeypatch):
    f = tmp_path / "news_velocity.json"
    f.write_text(json.dumps({"OTHER": []}))
    monkeypatch.setattr(dossier, "_NEWS_VELOCITY_FILE", str(f))
    assert dossier.news_pulse("ZDOS7") == []


def test_news_pulse_broken_file_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(dossier, "_NEWS_VELOCITY_FILE", str(tmp_path / "nix.json"))
    assert dossier.news_pulse("X") == []


# ── Notiz ────────────────────────────────────────────────────────────────────

def test_note_fail_open(monkeypatch):
    import dashboard.position_notes as pn_mod

    class _Boom:
        pass

    monkeypatch.setattr(pn_mod, "PositionNotes", _Boom)
    assert dossier.note("X") == ""


# ── Zusammenbau ──────────────────────────────────────────────────────────────

# ── L1.4: Querverweise ───────────────────────────────────────────────────────

def test_akte_links_md_builds_links():
    md = dossier.akte_links_md(["nvda", "TSM"])
    assert md == "[NVDA](?factory=warehouse&dossier=NVDA) · [TSM](?factory=warehouse&dossier=TSM)"


def test_akte_links_md_dedupes_and_skips_empty():
    md = dossier.akte_links_md(["NVDA", "nvda", "", None, "TSM"])
    assert md == "[NVDA](?factory=warehouse&dossier=NVDA) · [TSM](?factory=warehouse&dossier=TSM)"


def test_akte_links_md_respects_limit():
    md = dossier.akte_links_md([f"Z{i}" for i in range(30)], limit=3)
    assert md.count("&dossier=") == 3


def test_akte_links_md_empty_input():
    assert dossier.akte_links_md([]) == ""
    assert dossier.akte_links_md(None) == ""


def test_dossier_uppercases_ticker(_profiles):
    d = dossier.dossier("nvda")
    assert d["ticker"] == "NVDA"


def test_dossier_one_broken_source_does_not_break_others(monkeypatch, _profiles):
    import analyzers.experience_store as es_mod

    class _Boom:
        pass

    monkeypatch.setattr(es_mod, "ExperienceStore", _Boom)
    d = dossier.dossier("NVDA")
    assert d["trades"]["n_trades"] == 0     # kaputte Quelle -> leer
    assert d["profile"]["sector"] == "Technology"  # andere Quelle unberührt
