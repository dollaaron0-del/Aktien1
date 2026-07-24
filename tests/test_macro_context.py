"""
Tests für analyzers/macro_context.py und die Makro-Injektion in die Analyzer.

Alle Sub-Analyzer werden gemockt – es werden keine echten HTTP-/yfinance-/DB-
Aufrufe gemacht. Verifiziert wird die Aggregation, der Prompt-Block, der
Bias-Score, der Sizing-Modifier sowie die fehler-isolierte Rückfall-Logik.
"""
import pytest
from unittest.mock import MagicMock, patch

import analyzers.macro_context as mc


@pytest.fixture(autouse=True)
def _reset_cache():
    """Modul-Cache vor jedem Test leeren, sonst leaken Snapshots zwischen Tests."""
    mc._CACHE = {}
    mc._CACHED_AT = None
    mc._INSTANCE = None
    yield
    mc._CACHE = {}
    mc._CACHED_AT = None
    mc._INSTANCE = None


# ── Risk-Off-Szenario: voll bestücktes Snapshot ──────────────────────────────

@pytest.fixture()
def risk_off_patches():
    ov = MagicMock()
    ov.full_assessment.return_value = {
        "regime": "BEAR", "vix": 31.2, "vix_label": "Stress", "overall": "VORSICHT",
        "buy_blocked": True, "position_size_modifier": 0.5,
        "cross_asset": {"recommendation": "RISK_OFF", "risk_appetite_score": -0.4,
                        "yield_curve_inverted": True, "yield_curve_spread": -0.35},
    }
    cal = MagicMock()
    ev = MagicMock()
    ev.name = "FOMC-Zinsentscheid"; ev.days_until = 2; ev.impact = "HIGH"
    ev.release_date.isoformat.return_value = "2026-06-16"
    cal.get_upcoming_events.return_value = [ev]
    cal.get_position_size_modifier.return_value = 0.5
    rec = MagicMock()
    rec.get_latest.return_value = {"recession_score": 0.7, "regime": "CRISIS"}
    sr = MagicMock()
    sr.get_risk_mode.return_value = ("RISK_OFF", -0.9)
    strong = [MagicMock(name="s1"), MagicMock(name="s2")]
    strong[0].name = "Energie"; strong[1].name = "Versorger"
    weak = [MagicMock(name="w1"), MagicMock(name="w2")]
    weak[0].name = "Tech"; weak[1].name = "Zykliker"
    sr.get_strong_sectors.return_value = strong
    sr.get_weak_sectors.return_value = weak
    sr.get_position_size_modifier.return_value = 0.7
    with patch("analyzers.market_overview.MarketOverview", return_value=ov), \
         patch("analyzers.macro_calendar.MacroCalendar", return_value=cal), \
         patch("analyzers.recession_detector.RecessionDetector", return_value=rec), \
         patch("analyzers.sector_rotation.SectorRotation", return_value=sr):
        yield


def test_snapshot_composes_all_sources(risk_off_patches):
    snap = mc.MacroContext().snapshot(force=True)
    assert snap["regime"] == "BEAR"
    assert snap["vix"] == 31.2
    assert snap["cross_asset_rec"] == "RISK_OFF"
    assert snap["recession_score"] == 0.7
    assert snap["sector_risk_mode"] == "RISK_OFF"
    assert snap["macro_events"][0]["name"] == "FOMC-Zinsentscheid"


def test_prompt_block_contains_key_facts(risk_off_patches):
    block = mc.MacroContext().as_prompt_block()
    assert "Regime BEAR" in block
    assert "VIX: 31.2" in block
    assert "RISK_OFF" in block
    assert "Rezessionsrisiko: 70%" in block
    assert "FOMC" in block and "in 2d" in block
    assert "Energie" in block and "Tech" in block


def test_bias_score_strongly_negative_in_risk_off(risk_off_patches):
    bias = mc.MacroContext().bias_score()
    assert -1.0 <= bias <= 1.0
    assert bias <= -0.8


def test_size_modifier_clamped_at_floor(risk_off_patches):
    # 0.5 (vix) * 0.5 (kalender) * 0.7 (sektor) = 0.175 → clamp auf 0.3
    assert mc.MacroContext().size_modifier("AAPL") == 0.3


# ── Fehler-Isolation: alle Quellen werfen → sauberer Rückfall ────────────────

@pytest.fixture()
def all_failing_patches():
    # Kern-Analyzer UND die (teils keylosen, cache-lesenden) Collectors neutralisieren,
    # damit der "alles leer"-Test hermetisch ist und nicht von Cache-Dateien auf der
    # Platte abhängt (z. B. data/weather_macro.json beim keylosen Wetter-Collector).
    with patch("analyzers.market_overview.MarketOverview", side_effect=Exception("x")), \
         patch("analyzers.macro_calendar.MacroCalendar", side_effect=Exception("x")), \
         patch("analyzers.recession_detector.RecessionDetector", side_effect=Exception("x")), \
         patch("analyzers.sector_rotation.SectorRotation", side_effect=Exception("x")), \
         patch("collectors.fred_collector.FREDCollector", side_effect=Exception("x")), \
         patch("collectors.eia_collector.EIACollector", side_effect=Exception("x")), \
         patch("collectors.entsoe_collector.ENTSOECollector", side_effect=Exception("x")), \
         patch("collectors.weather_collector.WeatherCollector", side_effect=Exception("x")), \
         patch("collectors.dbnomics_collector.DBnomicsCollector", side_effect=Exception("x")), \
         patch("collectors.tanker_flow_collector.get_latest", side_effect=Exception("x")), \
         patch("collectors.eonet_collector.EONETCollector", side_effect=Exception("x")), \
         patch("collectors.usgs_collector.USGSCollector", side_effect=Exception("x")), \
         patch("collectors.aviation_collector.AviationCollector", side_effect=Exception("x")), \
         patch("collectors.cyber_collector.CyberCollector", side_effect=Exception("x")):
        yield


def test_empty_snapshot_yields_empty_block(all_failing_patches):
    ctx = mc.MacroContext()
    assert ctx.as_prompt_block() == ""
    assert ctx.bias_score() == 0.0
    assert ctx.size_modifier("AAPL") == 1.0


# ── summarize_sources (Roadmap 1.4c: Verarbeitungs-Trace) ────────────────────

def test_summarize_sources_all_true_when_snapshot_full(risk_off_patches):
    snap = mc.MacroContext().snapshot(force=True)
    sources = mc.summarize_sources(snap)
    assert sources["market_overview"] is True
    assert sources["macro_calendar"] is True
    assert sources["recession_detector"] is True
    assert sources["sector_rotation"] is True


def test_summarize_sources_all_false_when_all_sources_failed(all_failing_patches):
    snap = mc.MacroContext().snapshot(force=True)
    sources = mc.summarize_sources(snap)
    assert all(ok is False for ok in sources.values())


def test_get_macro_brief_never_raises(all_failing_patches):
    # Komfort-Funktion ist zusätzlich gekapselt → liefert immer einen String
    assert mc.get_macro_brief() == ""


# ── Bullishes Szenario: positiver Bias ───────────────────────────────────────

def test_bias_score_positive_in_risk_on():
    ov = MagicMock()
    ov.full_assessment.return_value = {
        "regime": "BULL", "vix": 14.0, "vix_label": "ruhig", "overall": "BULLISH",
        "buy_blocked": False, "position_size_modifier": 1.0,
        "cross_asset": {"recommendation": "RISK_ON"},
    }
    cal = MagicMock()
    cal.get_upcoming_events.return_value = []
    cal.get_position_size_modifier.return_value = 1.0
    rec = MagicMock(); rec.get_latest.return_value = {"recession_score": 0.1, "regime": "BULL"}
    sr = MagicMock()
    sr.get_risk_mode.return_value = ("RISK_ON", 0.8)
    sr.get_strong_sectors.return_value = []
    sr.get_weak_sectors.return_value = []
    sr.get_position_size_modifier.return_value = 1.1
    with patch("analyzers.market_overview.MarketOverview", return_value=ov), \
         patch("analyzers.macro_calendar.MacroCalendar", return_value=cal), \
         patch("analyzers.recession_detector.RecessionDetector", return_value=rec), \
         patch("analyzers.sector_rotation.SectorRotation", return_value=sr):
        ctx = mc.MacroContext()
        assert ctx.bias_score() >= 0.8
        # Sizing-Modifier ist nach oben auf 1.15 geklemmt
        assert ctx.size_modifier("AAPL") == 1.1


# ── Template-Injektion in die Analyzer ───────────────────────────────────────

def test_claude_analyzer_context_block_and_template():
    from analyzers.claude_analyzer import ClaudeAnalyzer, _USER_TEMPLATE_STANDARD
    cb = ClaudeAnalyzer._build_context_block("MAKRO-LAGE\n- Regime BEAR", "Nahost-Eskalation")
    assert "MAKRO-LAGE" in cb and "GEOPOLITIK: Nahost-Eskalation" in cb
    assert ClaudeAnalyzer._build_context_block("", None) == ""
    # Template formatiert mit und ohne Block ohne KeyError
    p1 = _USER_TEMPLATE_STANDARD.format(ticker="AAPL", price=150.0, news_text="n", context_block=cb)
    p2 = _USER_TEMPLATE_STANDARD.format(ticker="AAPL", price=150.0, news_text="n", context_block="")
    assert "MAKRO-LAGE" in p1
    assert "MAKRO-LAGE" not in p2


def test_multi_agent_context_block_and_template():
    from analyzers.multi_agent_analyzer import MultiAgentAnalyzer, _AGENT_USER_TEMPLATE
    cb = MultiAgentAnalyzer._build_context_block("MAKRO X", "Geo Y")
    assert "MAKRO X" in cb and "GEOPOLITIK" in cb
    msg = _AGENT_USER_TEMPLATE.format(
        ticker="AAPL", price_data="p", news_text="n", n_news=3, context_block=cb
    )
    assert "MAKRO X" in msg
