"""
Tests für die Genealogie-Einbindung im Tab "Trades & Lernen" (H3.2).
Isoliertes Mini-Skript (Muster: test_dashboard_thesis_board_tab.py) statt
des vollen render(ctx) — _render_genealogy() hängt an keiner der
schweren ctx-Abhängigkeiten.
"""
from streamlit.testing.v1 import AppTest

_SCRIPT = """
from dashboard.tabs.trades import _render_genealogy
_render_genealogy()
"""


def _seed_order(ticker="AAPL", action="BUY", ts="2026-07-15T10:00:00"):
    from broker.order_log import get_order_log
    ol = get_order_log()
    cur = ol._conn.execute(
        "INSERT INTO orders (ts, ticker, action, mode, status, shares, fill_price, "
        "order_id, partial, reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ts, ticker, action, "paper", "filled", 3.0, 100.0, None, 0, "Test"),
    )
    ol._conn.commit()
    return cur.lastrowid


def test_genealogy_empty_state_without_orders():
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "Noch keine Orders protokolliert" in captions


def test_genealogy_shows_expander_per_order(monkeypatch):
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    _seed_order(ticker="AAPL")
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    labels = [e.label for e in at.get("expander")]
    assert any("AAPL" in label for label in labels)
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "<svg" in html_out


def test_genealogy_plain_mode_shows_text_without_svg(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "plain")
    # Bewusst ein synthetischer, garantiert nie real analysierter Ticker:
    # order_lineage() sucht (mangels expliziten analysis_db_path-Override
    # in _render_genealogy()) bewusst in der ECHTEN analysis_log.db — ein
    # realer Ticker wie AAPL/NVDA könnte dort tatsächlich einen Treffer
    # liefern und den "keine Analyse gefunden"-Fall verdecken.
    _seed_order(ticker="ZZTESTXYZ")
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "<svg" not in html_out
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "keine Analyse gefunden" in captions
