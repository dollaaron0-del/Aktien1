"""
Tests für dashboard/genealogy.py (Ausbau-Roadmap H3.2 — Entscheidungs-
Genealogie). order_lineage() gegen präparierte, eigenständige Order-/
Analysis-Log-DBs (kein Eingriff in broker/order_log.py oder
analyzers/analysis_log.py nötig — beide Module bleiben unverändert).
"""
import json

from dashboard.genealogy import lineage_svg, order_lineage
from dashboard.theme import PALETTE


def _seed_order(tmp_path, ticker="AAPL", action="BUY", ts="2026-07-15T10:00:00",
                shares=3.0, fill_price=100.0, status="filled"):
    from broker.order_log import OrderLog
    ol = OrderLog(db_path=str(tmp_path / "order_log_test.db"))
    cur = ol._conn.execute(
        "INSERT INTO orders (ts, ticker, action, mode, status, shares, fill_price, "
        "order_id, partial, reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ts, ticker, action, "paper", status, shares, fill_price, None, 0, "Test"),
    )
    ol._conn.commit()
    return cur.lastrowid, str(tmp_path / "order_log_test.db")


def _seed_analysis(tmp_path, monkeypatch, rows):
    import analyzers.analysis_log as alog_mod
    db_path = str(tmp_path / "analysis_log_test.db")
    monkeypatch.setattr(alog_mod, "DB_PATH", db_path)
    alog = alog_mod.AnalysisLog()
    for analyzed_at, ticker, recommendation, score, sources_breakdown in rows:
        alog._conn.execute(
            "INSERT INTO analyses (analyzed_at, ticker, recommendation, direction, "
            "sentiment_score, confidence, sources_breakdown) VALUES (?,?,?,?,?,?,?)",
            (analyzed_at, ticker, recommendation, "BULLISH", score, "HIGH",
             json.dumps(sources_breakdown) if sources_breakdown else None),
        )
    alog._conn.commit()
    return db_path


# ── order_lineage() ───────────────────────────────────────────────────────────

def test_order_lineage_full_chain(tmp_path, monkeypatch):
    order_id, order_db = _seed_order(tmp_path, ticker="AAPL", ts="2026-07-15T10:00:00")
    analysis_db = _seed_analysis(tmp_path, monkeypatch, [
        ("2026-07-15T09:45:00", "AAPL", "BUY", 0.8, {"yahoo": 5, "reddit": 2}),
    ])
    lineage = order_lineage(order_id, order_db_path=order_db, analysis_db_path=analysis_db)
    assert lineage["order"]["ticker"] == "AAPL"
    assert lineage["analysis"]["recommendation"] == "BUY"
    assert lineage["sources"] == {"yahoo": 5, "reddit": 2}


def test_order_lineage_picks_nearest_analysis_before_order_not_after(tmp_path, monkeypatch):
    order_id, order_db = _seed_order(tmp_path, ticker="NVDA", ts="2026-07-15T10:00:00")
    analysis_db = _seed_analysis(tmp_path, monkeypatch, [
        ("2026-07-14T09:00:00", "NVDA", "HOLD", 0.5, None),   # zu alt, aber davor
        ("2026-07-15T09:50:00", "NVDA", "BUY", 0.9, None),    # nächstliegend davor
        ("2026-07-15T11:00:00", "NVDA", "SKIP", 0.2, None),   # NACH der Order — ignorieren
    ])
    lineage = order_lineage(order_id, order_db_path=order_db, analysis_db_path=analysis_db)
    assert lineage["analysis"]["analyzed_at"] == "2026-07-15T09:50:00"
    assert lineage["analysis"]["recommendation"] == "BUY"


def test_order_lineage_missing_order_returns_all_none(tmp_path):
    _, order_db = _seed_order(tmp_path)
    lineage = order_lineage(9999, order_db_path=order_db)
    assert lineage == {"order": None, "analysis": None, "sources": None}


def test_order_lineage_missing_analysis_keeps_order(tmp_path, monkeypatch):
    order_id, order_db = _seed_order(tmp_path, ticker="TSLA")
    analysis_db = _seed_analysis(tmp_path, monkeypatch, [])  # leer angelegt
    lineage = order_lineage(order_id, order_db_path=order_db, analysis_db_path=analysis_db)
    assert lineage["order"] is not None
    assert lineage["analysis"] is None
    assert lineage["sources"] is None


def test_order_lineage_fail_open_on_missing_dbs(tmp_path):
    lineage = order_lineage(1, order_db_path=str(tmp_path / "nope.db"))
    assert lineage == {"order": None, "analysis": None, "sources": None}


def test_order_lineage_sources_empty_dict_on_corrupt_breakdown(tmp_path, monkeypatch):
    order_id, order_db = _seed_order(tmp_path, ticker="MSFT")
    import analyzers.analysis_log as alog_mod
    analysis_db = str(tmp_path / "analysis_log_test.db")
    monkeypatch.setattr(alog_mod, "DB_PATH", analysis_db)
    alog = alog_mod.AnalysisLog()
    alog._conn.execute(
        "INSERT INTO analyses (analyzed_at, ticker, recommendation, direction, "
        "sentiment_score, confidence, sources_breakdown) VALUES (?,?,?,?,?,?,?)",
        ("2026-07-15T09:00:00", "MSFT", "BUY", "BULLISH", 0.7, "HIGH", "{not valid json"),
    )
    alog._conn.commit()
    lineage = order_lineage(order_id, order_db_path=order_db, analysis_db_path=analysis_db)
    assert lineage["sources"] == {}


# ── lineage_svg() ─────────────────────────────────────────────────────────────

def test_lineage_svg_shows_full_chain():
    lineage = {
        "order": {"action": "BUY", "ticker": "AAPL", "shares": 3.0, "fill_price": 100.0,
                  "ts": "2026-07-15T10:00:00", "status": "filled"},
        "analysis": {"recommendation": "BUY", "sentiment_score": 0.8,
                    "analyzed_at": "2026-07-15T09:45:00", "confidence": "HIGH"},
        "sources": {"yahoo": 5},
    }
    svg = lineage_svg(lineage)
    assert svg.startswith("<svg")
    assert "AAPL" in svg
    assert "yahoo: 5" in svg
    assert "(keine Analyse gefunden)" not in svg


def test_lineage_svg_shows_placeholder_for_missing_analysis():
    lineage = {
        "order": {"action": "BUY", "ticker": "AAPL", "shares": 3.0, "fill_price": 100.0,
                  "ts": "2026-07-15T10:00:00", "status": "filled"},
        "analysis": None,
        "sources": None,
    }
    svg = lineage_svg(lineage)
    assert "(keine Analyse gefunden)" in svg
    assert "(kein Breakdown gespeichert)" in svg


def test_lineage_svg_shows_placeholder_for_missing_order():
    svg = lineage_svg({"order": None, "analysis": None, "sources": None})
    assert "(nicht gefunden)" in svg


def test_lineage_svg_escapes_dynamic_text():
    lineage = {
        "order": {"action": "<script>", "ticker": "AAPL", "shares": 3.0,
                  "fill_price": 100.0, "ts": "x", "status": "<b>x</b>"},
        "analysis": None, "sources": None,
    }
    svg = lineage_svg(lineage)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
