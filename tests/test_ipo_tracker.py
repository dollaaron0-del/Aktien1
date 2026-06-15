"""
Tests für analyzers/ipo_tracker.py – Namensvalidierung beim IPO-Live-Check.

Verhindert die Ticker-Kollisionen, die geratene Platzhalter-Ticker auf fremde
Firmen matchen ließen (z.B. SPXC=SPX Technologies statt SpaceX,
ANTH=Anthera Pharma statt Anthropic, CHME=China Medicine statt Chime).
"""
import analyzers.ipo_tracker as ipo
from analyzers.ipo_tracker import CANDIDATES, _check_ticker_live


# Reale yfinance-Auflösungen (Stand: Live-Check 06/2026)
_FAKE_MARKET = {
    "SPXC": (True, "SPX Technologies, Inc."),          # NICHT SpaceX
    "SPCX": (True, "Space Exploration Technologies"),  # echter SpaceX
    "ANTH": (True, "ANTHERA PHARMACEUTICALS INC"),     # NICHT Anthropic
    "CHME": (True, "China Medicine Corp."),            # NICHT Chime
    "CHYM": (True, "Chime Financial, Inc."),           # echter Chime
    "KLAR": (True, "Klarna Group plc"),                # echter Klarna
}


def _fake_resolve(ticker):
    return _FAKE_MARKET.get(ticker, (False, ""))


def test_name_validation_rejects_ticker_collision(monkeypatch):
    monkeypatch.setattr(ipo, "_resolve_ticker", _fake_resolve)
    # SPXC hat einen Kurs, gehört aber zu SPX Technologies → KEIN SpaceX-Treffer
    ok, name = _check_ticker_live("SPXC", ["SpaceX", "Space Exploration"])
    assert ok is False
    assert "SPX Technologies" in name


def test_name_validation_accepts_correct_company(monkeypatch):
    monkeypatch.setattr(ipo, "_resolve_ticker", _fake_resolve)
    ok, name = _check_ticker_live("SPCX", ["SpaceX", "Space Exploration"])
    assert ok is True
    assert "Space Exploration" in name


def test_pre_ipo_ticker_has_no_price(monkeypatch):
    monkeypatch.setattr(ipo, "_resolve_ticker", _fake_resolve)
    assert _check_ticker_live("OAIT", ["OpenAI"]) == (False, "")


def test_without_match_terms_keeps_legacy_behaviour(monkeypatch):
    monkeypatch.setattr(ipo, "_resolve_ticker", _fake_resolve)
    # Ohne Abgleich zählt jeder echte Kurs als live (Alt-Verhalten)
    ok, _ = _check_ticker_live("SPXC", None)
    assert ok is True


def test_candidate_detection_picks_real_spacex(monkeypatch):
    """SPACEX-Kandidat: trifft den echten SPCX, nicht eine Kollision."""
    monkeypatch.setattr(ipo, "_resolve_ticker", _fake_resolve)
    cand = CANDIDATES["SPACEX"]
    tickers = ([cand.expected_ticker] if cand.expected_ticker else []) + cand.alt_tickers
    hit = next((t for t in tickers if _check_ticker_live(t, cand.match_terms)[0]), None)
    assert hit == "SPCX"


def test_anthropic_stays_pre_ipo(monkeypatch):
    """Anthropic ist privat – ANTH (Anthera) darf NICHT als live gelten."""
    monkeypatch.setattr(ipo, "_resolve_ticker", _fake_resolve)
    cand = CANDIDATES["ANTHROPIC"]
    tickers = ([cand.expected_ticker] if cand.expected_ticker else []) + cand.alt_tickers
    hit = next((t for t in tickers if _check_ticker_live(t, cand.match_terms)[0]), None)
    assert hit is None
