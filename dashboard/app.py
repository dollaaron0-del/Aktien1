"""
Streamlit Dashboard – Stock Sentiment Trading Bot
Starten: streamlit run dashboard/app.py
     oder: python main.py --dashboard
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import streamlit as st
import pandas as pd
from datetime import datetime, timezone

from config import config
from broker.paper_broker import PaperBroker
from portfolio.portfolio import Portfolio
from portfolio.performance_tracker import PerformanceTracker
from portfolio.phase_controller import PhaseController
from portfolio.focus_mode import FocusController, FocusMode
from portfolio.trade_journal import TradeJournal
from portfolio.signal_queue import SignalQueue
from analyzers.reflection_engine import ReflectionEngine
from analyzers.recession_detector import RecessionDetector, BULL, NEUTRAL, BEAR, CRISIS
from analyzers.technical_indicators import TechnicalIndicators
from analyzers.dynamic_watchlist import DynamicWatchlist
from analyzers.signal_expander import SignalDrivenExpander
from analyzers.eu_stock_scanner import EU_UNIVERSE
from analyzers.weekend_prep import WeekendPrep
from analyzers.analysis_log import AnalysisLog
from analyzers.bot_scorer import BotScorer, MILESTONES, get_modifiers
from portfolio.goal_risk_assessor import GoalRiskAssessor, OK, CAUTION, DANGER, UNREACHABLE

# ─── Ticker → Firmenname ──────────────────────────────────────────────────────
_US_NAMES = {
    # Mega-Cap Tech
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "TSLA": "Tesla",
    "AMZN": "Amazon", "GOOGL": "Alphabet", "GOOG": "Alphabet", "META": "Meta",
    "AVGO": "Broadcom", "ORCL": "Oracle", "ADBE": "Adobe", "CRM": "Salesforce",
    "AMD": "AMD", "INTC": "Intel", "QCOM": "Qualcomm", "CSCO": "Cisco",
    "TXN": "Texas Instruments", "INTU": "Intuit", "NOW": "ServiceNow",
    "PANW": "Palo Alto Networks", "SNOW": "Snowflake", "PLTR": "Palantir",
    "TSM": "TSMC", "ASML": "ASML", "AMAT": "Applied Materials",
    "LRCX": "Lam Research", "MU": "Micron", "MRVL": "Marvell", "ARM": "ARM Holdings",
    # Finanzen
    "JPM": "JPMorgan", "BAC": "Bank of America", "GS": "Goldman Sachs",
    "MS": "Morgan Stanley", "WFC": "Wells Fargo", "BLK": "BlackRock",
    "V": "Visa", "MA": "Mastercard", "AXP": "American Express", "PYPL": "PayPal",
    "COIN": "Coinbase",
    # Healthcare
    "LLY": "Eli Lilly", "NVO": "Novo Nordisk", "UNH": "UnitedHealth",
    "JNJ": "J&J", "MRK": "Merck", "PFE": "Pfizer", "ABBV": "AbbVie",
    "TMO": "Thermo Fisher", "ABT": "Abbott",
    # Energie / Industrie
    "XOM": "ExxonMobil", "CVX": "Chevron", "COP": "ConocoPhillips",
    "CAT": "Caterpillar", "BA": "Boeing", "GE": "GE Aerospace",
    "RTX": "Raytheon", "HON": "Honeywell",
    # Konsum / Retail
    "WMT": "Walmart", "COST": "Costco", "HD": "Home Depot",
    "MCD": "McDonald's", "NKE": "Nike", "SBUX": "Starbucks",
    "DIS": "Disney", "NFLX": "Netflix",
    # Wachstum
    "SHOP": "Shopify", "UBER": "Uber", "ABNB": "Airbnb",
    "RIVN": "Rivian", "LCID": "Lucid", "SOFI": "SoFi", "HOOD": "Robinhood",
    # ETFs / Inverse
    "SPY": "S&P 500 ETF", "QQQ": "Nasdaq ETF", "VTI": "Total Market ETF",
    "SH": "S&P 500 Inv.", "PSQ": "Nasdaq Inv.", "SQQQ": "Nasdaq 3× Inv.",
    # Krypto
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana",
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana",
}
_EU_NAMES = {ticker: name for ticker, (name, *_) in EU_UNIVERSE.items()}
# Bekannte EU-Ticker die nicht im EU_UNIVERSE sind
_EU_NAMES.update({
    "RHM.DE": "Rheinmetall", "DB1.DE": "Deutsche Börse", "MTX.DE": "MTU Aero",
    "SHL.DE": "Siemens Healthineers", "ZAL.DE": "Zalando", "ENR.DE": "Siemens Energy",
    "DHL.DE": "DHL Group", "HFG.DE": "HelloFresh", "WAF.DE": "Siltronic",
    "DHER.DE": "Delivery Hero", "O2D.DE": "Telefónica DE",
    "NDA-SE.ST": "Nordea", "ERIC-B.ST": "Ericsson",
    "NOVO-B.CO": "Novo Nordisk B", "ORSTED.CO": "Ørsted",
})
_ALL_NAMES = {**_US_NAMES, **_EU_NAMES}


def ticker_label(ticker: str) -> str:
    name = _ALL_NAMES.get(ticker.upper())
    return f"{ticker} ({name})" if name else ticker


# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Stock Sentiment Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="auto",
)

from dashboard.auth import require_login  # noqa: E402
require_login()

# ─── Custom CSS ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Tighter metric cards */
[data-testid="metric-container"] {
    background: #1e2130;
    border: 1px solid #2d3250;
    border-radius: 10px;
    padding: 12px 16px;
}
/* Tab font */
button[data-baseweb="tab"] { font-size: 0.9rem; font-weight: 600; }
/* Regime badge helpers */
.regime-bull    { color: #00e676; font-weight: 700; font-size: 1.1rem; }
.regime-neutral { color: #ffd740; font-weight: 700; font-size: 1.1rem; }
.regime-bear    { color: #ff7043; font-weight: 700; font-size: 1.1rem; }
.regime-crisis  { color: #f44336; font-weight: 700; font-size: 1.1rem; }
.badge-pending  { color: #ffd740; }
.badge-ok       { color: #00e676; }
.badge-red      { color: #f44336; }
</style>
""", unsafe_allow_html=True)


# ─── Resource loading ─────────────────────────────────────────────────────────
@st.cache_resource
def load_resources():
    broker       = PaperBroker()
    portfolio    = Portfolio(config.initial_capital)
    tracker      = PerformanceTracker()
    phase_ctrl   = PhaseController(
        initial_capital=config.initial_capital,
        growth_target_multiple=config.growth_target_multiple,
        monthly_target_eur=config.monthly_distribution_eur,
        buffer_months=config.distribution_buffer_months,
    )
    focus_ctrl   = FocusController(
        mode=config.focus_mode,
        target_amount=config.target_goal_amount or None,
        target_date=config.target_goal_date or None,
        initial_capital=config.initial_capital,
    )
    journal      = TradeJournal()
    reflection   = ReflectionEngine(tracker, journal)
    sig_queue    = SignalQueue(max_age_hours=config.signal_queue_max_age_hours)
    detector     = RecessionDetector(anthropic_api_key=config.anthropic_api_key)
    weekend_prep = WeekendPrep(
        anthropic_api_key=config.anthropic_api_key,
        watchlist=config.watchlist,
    )
    return (broker, portfolio, tracker, phase_ctrl, focus_ctrl,
            journal, reflection, sig_queue, detector, weekend_prep)


(broker, portfolio, tracker, phase_ctrl, focus_ctrl,
 journal, reflection, sig_queue, detector, weekend_prep) = load_resources()

# ─── Live data ────────────────────────────────────────────────────────────────
prices      = broker.get_prices(list(portfolio.all_positions().keys()))
total_value = portfolio.total_value(prices)
phase_info  = phase_ctrl.get_info(total_value)
acc         = tracker.get_accuracy_report()


def _real_trade_stats(pf) -> dict:
    """Win-Rate/Ø-Rendite direkt aus den echten Portfolio-Trades (SELLs mit P&L).

    Fallback für alle Anzeigen, die am Prediction-Tracking hängen: die
    predictions-Tabelle ist erst seit dem Tracking-Wiring (14.6.) im Spiel und
    füllt sich nur bei neuen Kauf→Verkauf-Paaren — die echten Trades davor
    wären sonst unsichtbar."""
    try:
        sells = [t for t in pf.trade_history() if t.action == "SELL" and t.pnl is not None]
    except Exception:
        return {}
    if not sells:
        return {}
    wins = sum(1 for t in sells if t.pnl > 0)
    rets = []
    for t in sells:
        basis = t.price * t.shares - t.pnl   # Einstiegsbasis = Verkaufswert − P&L
        if basis > 0:
            rets.append(t.pnl / basis * 100)
    return {
        "total_closed":   len(sells),
        "win_rate_pct":   round(wins / len(sells) * 100, 1),
        "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else 0.0,
        "total_pnl":      round(sum(t.pnl for t in sells), 2),
    }


_rt_stats = _real_trade_stats(portfolio)
regime_data = detector.get_latest()
pending_cnt = sig_queue.count_pending()
from analyzers.user_request_queue import peek as _peek_analysis_queue
_analysis_queue = _peek_analysis_queue()

# ─── Helper: regime color / label ────────────────────────────────────────────
_REGIME_COLOR = {BULL: "#00e676", NEUTRAL: "#ffd740", BEAR: "#ff7043", CRISIS: "#f44336"}
_REGIME_ICON  = {BULL: "🟢", NEUTRAL: "🟡", BEAR: "🟠", CRISIS: "🔴"}
_REGIME_CSS   = {BULL: "regime-bull", NEUTRAL: "regime-neutral", BEAR: "regime-bear", CRISIS: "regime-crisis"}


def regime_badge(regime: str) -> str:
    icon  = _REGIME_ICON.get(regime, "⚪")
    css   = _REGIME_CSS.get(regime, "")
    return f'<span class="{css}">{icon} {regime}</span>'


# Sprechende Namen für Collector-Schlüssel im sources_breakdown (Roadmap 1.4a).
# Unbekannte Schlüssel werden unverändert angezeigt — kein Pflege-Zwang.
_SOURCE_NAMES = {
    "yahoo": "Yahoo Finance", "reddit": "Reddit", "newsapi": "NewsAPI",
    "sec": "SEC EDGAR", "finra": "FINRA Short-Volume", "fda": "FDA-Kalender",
    "trends": "Google Trends", "wiki": "Wikipedia-Views",
    "insider": "Insider-Trades", "congress": "Congress-Trades",
}


def render_sources_breakdown(raw, total=None) -> None:
    """Quellen-Provenienz einer Analyse anzeigen: welche Collectors haben
    wie viele Beiträge geliefert (Roadmap 1.4a). Fail-open: kaputtes JSON
    → nur die Gesamtzahl."""
    breakdown = {}
    if raw:
        try:
            breakdown = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (json.JSONDecodeError, TypeError):
            breakdown = {}
    if breakdown:
        parts = [
            f"{_SOURCE_NAMES.get(src, src)} ×{cnt}"
            for src, cnt in sorted(breakdown.items(), key=lambda kv: -int(kv[1] or 0))
            if cnt
        ]
        leer = [_SOURCE_NAMES.get(s, s) for s, c in breakdown.items() if not c]
        st.markdown("**📡 Quellen:** " + (" · ".join(parts) if parts else "keine Treffer"))
        if leer:
            st.caption("Ohne Treffer: " + ", ".join(leer))
    elif total is not None:
        st.caption(f"Quellenlage: {total} Beiträge (kein Detail-Breakdown gespeichert)")


# ═══════════════════════════════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════════════════════════════
c_logo, c_title, c_refresh = st.columns([1, 8, 2])
with c_logo:
    st.markdown("## 📈")
with c_title:
    st.markdown(
        f"## Stock Sentiment Trading Bot  "
        f"<small style='color:#888; font-size:0.65em;'>Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')} "
        f"· Broker: **{config.broker_mode.upper()}**</small>",
        unsafe_allow_html=True,
    )
with c_refresh:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🔄 Aktualisieren", width="stretch"):
        st.cache_resource.clear()
        st.rerun()

# ─── Globaler Bot-Status + Datenstand ────────────────────────────────────────
# Sichtbar auf JEDEM Tab (nicht nur in der Sidebar): läuft der Bot überhaupt,
# und von wann stammen die angezeigten Daten? Ohne das wirken eingefrorene
# Panels wie Defekte, obwohl der Bot schlicht pausiert ist.
from system import bot_control as _bc_hdr
_hdr_paused = False
try:
    _hdr_status = _bc_hdr.get_status()
    _hdr_paused = bool(_hdr_status.get("paused"))
except Exception:
    _hdr_status = {}
try:
    _alog_hdr = AnalysisLog()
    _hdr_last = _alog_hdr.get_recent(limit=1)
    _last_analysis_ts = _hdr_last[0]["analyzed_at"][:16].replace("T", " ") if _hdr_last else None
except Exception:
    _last_analysis_ts = None
_regime_ts = ((regime_data.get("recorded_at") or "")[:16].replace("T", " ")
              if regime_data else None)
_stand_txt = " · ".join(
    p for p in (
        f"Letzte Analyse: {_last_analysis_ts}" if _last_analysis_ts else None,
        f"Regime-Snapshot: {_regime_ts}" if _regime_ts else None,
    ) if p
) or "Noch keine Analysedaten"

if _hdr_paused:
    _since_hdr = ""
    try:
        if _hdr_status.get("since"):
            _since_hdr = " seit " + datetime.fromisoformat(_hdr_status["since"]).strftime("%d.%m. %H:%M")
    except Exception:
        pass
    st.error(
        f"⏸ **Bot ist pausiert{_since_hdr}** – alle Panels zeigen den letzten Stand "
        f"vor der Pause. {_stand_txt}."
    )
else:
    _stale_analysis = False
    try:
        if _hdr_last:
            _age_h = (datetime.now(timezone.utc).replace(tzinfo=None)
                      - datetime.fromisoformat(_hdr_last[0]["analyzed_at"])).total_seconds() / 3600
            _stale_analysis = _age_h > 48
    except Exception:
        pass
    if _stale_analysis:
        st.warning(f"🟡 Bot aktiv, aber letzte Analyse liegt über 48 h zurück. {_stand_txt}.")
    else:
        st.caption(f"🟢 Bot aktiv · {_stand_txt}")

# ─── Live-Status-Zeile (Roadmap 1.5a): was macht der Bot GERADE? ─────────────
try:
    from system import live_status as _live_hdr
    _ls = _live_hdr.read_status()
    _ls_age = _live_hdr.status_age_seconds(_ls)
except Exception:
    _ls, _ls_age = None, None
if _ls and not _hdr_paused:
    if _ls.get("state") == "cycle":
        # Zyklus-Schreiber melden sich im Minutentakt; >30 min ohne Update
        # ist ein Crash-Rest, keine laufende Analyse.
        if _ls_age is not None and _ls_age < 1800:
            _parts = [f"🔄 **Live:** {_ls.get('phase') or 'Zyklus'}"]
            if _ls.get("ticker"):
                _pt = ticker_label(_ls["ticker"])
                if _ls.get("idx") and _ls.get("total"):
                    _pt += f" {_ls['idx']}/{_ls['total']}"
                _parts.append(_pt)
            if _ls.get("cycle_started_at"):
                _parts.append(f"seit {_ls['cycle_started_at'][11:16]} Uhr")
                # Grobe ETA aus bisherigem Tempo (erst ab ein paar Titeln belastbar)
                try:
                    if (_ls.get("idx") or 0) >= 3 and _ls.get("total"):
                        _el = (datetime.now(timezone.utc).replace(tzinfo=None)
                               - datetime.fromisoformat(_ls["cycle_started_at"])).total_seconds()
                        _eta = datetime.now() + __import__("datetime").timedelta(
                            seconds=_el / _ls["idx"] * (_ls["total"] - _ls["idx"]))
                        _parts.append(f"ETA ~{_eta.strftime('%H:%M')}")
                except Exception:
                    pass
            st.info(" · ".join(_parts))
        else:
            st.caption("⚪ Live-Status veraltet (Zyklus abgebrochen?) — "
                       "letzter Stand: " + str(_ls.get("phase") or "?"))
    elif _ls.get("state") == "idle" and _ls.get("next_run"):
        st.caption(f"💤 Idle · nächster geplanter Lauf: "
                   f"{_ls['next_run'][:16].replace('T', ' ')} Uhr")

# ─── KPI strip ───────────────────────────────────────────────────────────────
delta_pct  = (total_value - config.initial_capital) / config.initial_capital * 100
invested   = sum(pos.shares * prices.get(t, pos.entry_price) for t, pos in portfolio.all_positions().items())
cash_pct   = portfolio.cash / total_value * 100 if total_value else 0
regime_str = (regime_data["regime"] if regime_data else "–")
regime_score = (regime_data["recession_score"] if regime_data else None)

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Gesamtwert",      f"${total_value:,.2f}", f"{delta_pct:+.1f}%")
k2.metric("Cash",            f"${portfolio.cash:,.2f}", f"{cash_pct:.0f}% des Portfolios")
k3.metric("Offene Positionen", len(portfolio.all_positions()))
k4.metric(
    "Marktregime",
    regime_str,
    f"Score {regime_score:.2f}" if regime_score is not None else "–",
    delta_color="inverse",
)
if acc.get("total_closed"):
    k5.metric("Win-Rate", f"{acc['win_rate_pct']}%", f"{acc['total_closed']} Trades")
elif _rt_stats:
    k5.metric("Win-Rate", f"{_rt_stats['win_rate_pct']}%",
              f"{_rt_stats['total_closed']} Trades (Portfolio-Historie)")
else:
    k5.metric("Win-Rate", "–", "0 Trades")
k6.metric(
    "Signal-Warteschlange",
    f"{pending_cnt} ausstehend",
    delta_color="off",
)

st.divider()


@st.cache_data(ttl=3600)
def _get_spy_benchmark(days: int, start_value: float) -> "pd.DataFrame":
    """SPY-Kursverlauf normiert auf start_value für Benchmark-Overlay."""
    try:
        import yfinance as _yf
        from datetime import timedelta as _td2
        _end = datetime.now(timezone.utc).replace(tzinfo=None)
        _start = _end - _td2(days=days + 5)
        _spy = _yf.Ticker("SPY").history(
            start=_start.strftime("%Y-%m-%d"),
            end=_end.strftime("%Y-%m-%d"),
        )
        if _spy.empty:
            return pd.DataFrame()
        _spy = _spy.reset_index()[["Date", "Close"]].rename(
            columns={"Date": "snapshot_date", "Close": "spy_value"}
        )
        _spy["snapshot_date"] = pd.to_datetime(_spy["snapshot_date"]).dt.tz_localize(None)
        _spy["spy_value"] = _spy["spy_value"] / _spy["spy_value"].iloc[0] * start_value
        return _spy
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def _get_ticker_news(ticker: str) -> list:
    try:
        import yfinance as _yf
        return (_yf.Ticker(ticker).news or [])[:4]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════════════════════════════════════
# Kontext-Bündel für ausgelagerte Tab-Module (Roadmap 4.4a, Monolith-Split):
# alles, was bis hierher im Modul-Namensraum steht (broker/portfolio/config/
# Helper-Funktionen/…), automatisch statt einzeln durchgereicht — siehe
# dashboard/tabs/__init__.py-Docstring für die Begründung.
import types as _types
_ctx = _types.SimpleNamespace(**locals())

tab_portfolio, tab_live, tab_decisions, tab_regime, tab_queue, tab_network, tab_briefing, tab_trades, tab_tech, tab_watchlist, tab_log, tab_settings = st.tabs([
    "📊 Portfolio",
    "📡 Live",
    "🧠 Entscheidungen",
    "🛡 Markt-Regime",
    f"📋 Signal-Queue ({pending_cnt})" + (f" · 🔍{len(_analysis_queue)}" if _analysis_queue else ""),
    "🕸 Aktien-Netzwerk",
    "📰 Wochenbriefing",
    "📈 Trades & Lernen",
    "📉 Technicals",
    "🔭 Watchlist",
    "🔍 Analyse-Log",
    "⚙️ Einstellungen",
])


# ══════════════════════════════════════════════════════════
# TAB "LIVE" – Aktivitätsfeed + Nächste Aktionen (Roadmap 1.5b+c)
# ══════════════════════════════════════════════════════════
with tab_live:
    from dashboard.tabs import live as _tab_live
    _tab_live.render(_ctx)


# ══════════════════════════════════════════════════════════
# TAB 1 – PORTFOLIO
# ══════════════════════════════════════════════════════════
with tab_portfolio:
    from dashboard.tabs import portfolio as _tab_portfolio
    _tab_portfolio.render(_ctx)


# ══════════════════════════════════════════════════════════
# TAB 2 – ENTSCHEIDUNGEN (Warum tut der Bot, was er tut?)
# ══════════════════════════════════════════════════════════
with tab_decisions:
    from dashboard.tabs import decisions as _tab_decisions
    _tab_decisions.render(_ctx)


# ══════════════════════════════════════════════════════════
# TAB 3 – MARKT-REGIME
# ══════════════════════════════════════════════════════════
with tab_regime:
    from dashboard.tabs import regime as _tab_regime
    _tab_regime.render(_ctx)


# ══════════════════════════════════════════════════════════
# TAB 3 – SIGNAL-QUEUE
# ══════════════════════════════════════════════════════════
with tab_queue:
    from dashboard.tabs import queue as _tab_queue
    _tab_queue.render(_ctx)


# ══════════════════════════════════════════════════════════
# TAB 4 – AKTIEN-NETZWERK
# ══════════════════════════════════════════════════════════
with tab_network:
    from dashboard.tabs import network as _tab_network
    _tab_network.render(_ctx)


# ══════════════════════════════════════════════════════════
# TAB 5 – WOCHENBRIEFING
# ══════════════════════════════════════════════════════════
with tab_briefing:
    from dashboard.tabs import briefing as _tab_briefing
    _tab_briefing.render(_ctx)


# ══════════════════════════════════════════════════════════
# TAB 6 – TRADES & LERNEN
# ══════════════════════════════════════════════════════════
with tab_trades:
    from dashboard.tabs import trades as _tab_trades
    _tab_trades.render(_ctx)


# ══════════════════════════════════════════════════════════
# TAB 7 – TECHNICALS
# ══════════════════════════════════════════════════════════
with tab_tech:
    from dashboard.tabs import tech as _tab_tech
    _tab_tech.render(_ctx)


# ══════════════════════════════════════════════════════════
# TAB 8 – DYNAMISCHE WATCHLIST
# ══════════════════════════════════════════════════════════
with tab_watchlist:
    from dashboard.tabs import watchlist as _tab_watchlist
    _tab_watchlist.render(_ctx)


# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## ⚙️ Bot-Status")
    st.caption(f"Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    # Nächste Analyse: echter Marktkalender (Börsen + Vorlauf) statt festem
    # 07:30-Countdown; bei pausiertem Bot gibt es schlicht keine nächste Analyse.
    if _hdr_paused:
        st.error("⏸ Bot pausiert – keine geplanten Analysen.")
    else:
        try:
            from analyzers.market_schedule import MarketSchedule as _MS
            _nxt = _MS(config.market_exchanges, config.market_lead_minutes).next_window()
            if _nxt:
                st.info(f"⏰ Nächste Analyse: **{_nxt['analysis_local']}**")
            else:
                st.info("⏰ Kein Handelstag – nächste Analyse am nächsten Börsentag.")
        except Exception:
            st.caption("Analyse-Zeitplan nicht verfügbar.")

    # Auto-Refresh: läuft als Fragment-Timer, stößt regelmäßig einen kompletten
    # Seiten-Rerun an (Streamlit lädt sonst nur bei Interaktion neu).
    st.toggle("🔄 Auto-Refresh (60 s)", value=False, key="auto_refresh")

    # Live regime badge
    if regime_data:
        r   = regime_data["regime"]
        sc  = regime_data["recession_score"]
        col = _REGIME_COLOR.get(r, "#888")
        st.markdown(
            f"**Regime:** <span style='color:{col};font-weight:700;'>"
            f"{_REGIME_ICON[r]} {r}</span> (Score: {sc:.2f})",
            unsafe_allow_html=True,
        )
    else:
        st.markdown("**Regime:** – (noch kein Snapshot)")

    st.markdown(f"**Offene Positions:** {len(portfolio.all_positions())}")
    st.markdown(f"**Signal-Queue:** {pending_cnt} ausstehend")
    st.divider()

    # ── Pause-Schalter ──────────────────────────────────────────────────────────
    # Hält den Bot vorübergehend KOMPLETT an (alle Jobs inkl. SL/TP-Überwachung).
    # Der systemd-Service läuft weiter; nach dem Deaktivieren nimmt der Bot die
    # Arbeit beim nächsten Schleifendurchlauf (max. 60 Sek.) automatisch wieder auf.
    from system import bot_control as _bot_control
    _pause_status = _bot_control.get_status()
    _is_paused = _pause_status["paused"]

    if _is_paused:
        _since_txt = ""
        if _pause_status.get("since"):
            try:
                _since_dt = datetime.fromisoformat(_pause_status["since"])
                _since_txt = f" seit {_since_dt.strftime('%d.%m. %H:%M')}"
            except Exception:
                pass
        st.error(f"⏸ **Bot ist pausiert**{_since_txt} – es laufen keine Jobs (auch keine SL/TP-Überwachung).")
    else:
        st.success("▶️ Bot läuft – alle Jobs aktiv.")

    _new_paused = st.toggle(
        "⏸ Bot pausieren (kompletter Stopp)",
        value=_is_paused,
        key="bot_pause_toggle",
        help="Hält ALLE Bot-Aktivitäten an, inkl. Stop-Loss/Take-Profit. "
             "Offene Positionen werden während der Pause NICHT automatisch abgesichert. "
             "Greift innerhalb von max. 60 Sekunden.",
    )
    if _new_paused != _is_paused:
        _bot_control.set_paused(_new_paused, by="dashboard")
        if _new_paused:
            st.warning("Bot wird angehalten … (wirkt in max. 60 Sek.)")
        else:
            st.info("Bot wird fortgesetzt … (wirkt in max. 60 Sek.)")
        st.rerun()
    st.divider()

    # Focus mode
    st.markdown("### 🎯 Fokus-Modus")
    fm_info   = focus_ctrl.get_info(total_value)
    scale_info = focus_ctrl.scaling_info(total_value)
    st.markdown(f"**{fm_info['label']}**")
    st.caption(fm_info["description"])
    st.write(f"SL: **{fm_info['stop_loss_pct']*100:.0f}%** · TP: **{fm_info['take_profit_pct']*100:.0f}%**")
    st.write(f"Max Position: **{scale_info['max_position_pct']*100:.0f}%** (${scale_info['max_position_usd']:,.0f}) · Halt: **{fm_info['preferred_hold_days']}d**")
    st.write(f"Max Positionen: **{scale_info['max_positions']}** · Min Sentiment: **{fm_info['min_sentiment']:.2f}**")

    open_count = len(portfolio.all_positions())
    slots_free = scale_info["max_positions"] - open_count
    st.progress(
        min(open_count / scale_info["max_positions"], 1.0),
        text=f"Positionen: {open_count}/{scale_info['max_positions']} ({slots_free} frei)"
    )

    if fm_info["mode"] == FocusMode.TARGET_GOAL and fm_info.get("target_amount"):
        st.progress(
            fm_info["progress_pct"] / 100,
            text=f"{fm_info['progress_pct']:.1f}% · noch {fm_info['days_remaining']} Tage",
        )
    st.divider()

    # ── Ollama / API-Kosten ────────────────────────────────────────────────────
    st.markdown("### 🤖 KI-Backend & Kosten")
    try:
        from analyzers.api_cost_tracker import APICostTracker
        cost_summary = APICostTracker().summary()
        ollama_active = config.ollama_enabled

        if ollama_active:
            st.success(f"Ollama aktiv: `{config.ollama_model}`")
        else:
            st.info("Nur Claude API (Ollama deaktiviert)")

        c1, c2 = st.columns(2)
        with c1:
            st.metric("Claude-Aufrufe gesamt", cost_summary["claude_calls"])
            st.metric(f"Heute Claude-Kosten (Limit {cost_summary.get('daily_limit_eur', 1.0):.2f}€)",
                      f"{cost_summary['today_cost_eur']:.2f}€")
            st.metric("Gesamt Claude-Kosten",  f"{cost_summary['total_cost_eur']:.2f}€")
        with c2:
            st.metric("Ollama-Skips gesamt",   cost_summary["ollama_skips"],
                      help="Analysen die Claude nicht gerufen haben")
            st.metric("Heute gespart",         f"{cost_summary['today_saved_eur']:.2f}€")
            st.metric("Gesamt gespart",        f"{cost_summary['total_saved_eur']:.2f}€")

        skip_pct = cost_summary["skip_rate_pct"]
        if cost_summary["total_analyses"] > 0:
            st.progress(skip_pct / 100,
                        text=f"Ollama-Einsparrate: {skip_pct:.1f}% der Analysen ohne Claude")
    except Exception:
        st.caption("Kosten-Tracking noch nicht verfügbar")
    st.divider()

    # Config summary
    st.markdown("### ⚙️ Konfiguration")
    st.write(f"**Claude Modell:** {config.claude_model}")
    st.write(f"**Ollama Modell:** {config.ollama_model if config.ollama_enabled else '–'}")
    st.write(f"**Broker:** {config.broker_mode.upper()}")
    st.write(f"**Watchlist:** {', '.join(ticker_label(t) for t in config.watchlist)}")
    st.write(f"**Kauf-Schwelle:** {config.buy_threshold:.2f}")
    st.write(f"**Hedge ab:** {config.hedge_from_regime}")
    st.write(f"**Max Hedge:** {config.max_hedge_pct*100:.0f}%")
    st.write(f"**Börsen:** {', '.join(config.market_exchanges)}")
    st.write(f"**Social Scan:** ✗ (deaktiviert)")
    st.write(f"**SL/TP-Check:** alle 30 Min")
    st.write(f"**Aging-Check:** alle 4h")
    st.write(f"**Kelly Sizing:** {'✓' if config.use_kelly_sizing else '✗'}")
    st.divider()

    st.markdown("### 🔧 Analyse-Features")
    features = [
        "30-Tage Nachrichtenarchiv",
        "Thesis-Überprüfung",
        "Congressional Insider-Trades",
        "SEC Form 4 Insidermeldungen",
        "US-Bundesaufträge (usaspending)",
        "SEC EDGAR 8-K Meldungen",
        "StockTwits Sentiment",
        "PRNewswire / BusinessWire",
        "EU-Nachrichten (Google RSS)",
        "Options-Flow (C/P-Ratio)",
        "Earnings-Filter",
        "Sektor-Korrelationscheck",
        "Kelly-Criterion Sizing",
        "Rezessions-Detektor",
        "Inverse ETF Hedging",
        "Signal-Warteschlange",
        "Wochenbriefing (KI)",
        "Technische Indikatoren (RSI, MACD, BB, EMA, ATR)",
    ]
    for f in features:
        st.write(f"✓ {f}")

    # Ehrlicher Quellen-Status statt statischer ✓-Liste: was ist bewusst
    # abgeschaltet (tote Endpoints), was scheitert an fehlenden API-Keys?
    _dead_srcs = sorted(set(getattr(config, "collectors_disabled", [])))
    if _dead_srcs:
        st.write("✗ **Deaktivierte Quellen** (Endpoint tot, `COLLECTORS_DISABLED`): "
                 + ", ".join(_dead_srcs))
    _keyless = [
        _n for _n, _k in (
            ("Twitter/X", "twitter_bearer_token"),
            ("Quiver (Congress-Trades)", "quiver_api_key"),
            ("NewsAPI", "newsapi_key"),
        ) if not getattr(config, _k, "")
    ]
    if _keyless:
        st.write("✗ **Ohne API-Key inaktiv:** " + ", ".join(_keyless))

    st.divider()
    if st.button("🔄 Cache leeren & neu laden", width="stretch"):
        st.cache_resource.clear()
        st.rerun()


# ══════════════════════════════════════════════════════════
# TAB 9 – ANALYSE-LOG
# ══════════════════════════════════════════════════════════
with tab_log:
    from dashboard.tabs import log as _tab_log
    _tab_log.render(_ctx)


# ══════════════════════════════════════════════════════════
# TAB 10 – EINSTELLUNGEN
# ══════════════════════════════════════════════════════════
with tab_settings:
    from dashboard.tabs import settings as _tab_settings
    _tab_settings.render(_ctx)


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-REFRESH
# ═══════════════════════════════════════════════════════════════════════════════
# Streamlit lädt nur bei Interaktion neu — für ein Monitoring-Dashboard braucht
# es einen Timer. Das Fragment feuert alle 60 s und stößt einen kompletten
# App-Rerun an; der Zeitstempel-Guard verhindert eine Endlos-Schleife direkt
# nach einem vollen Lauf.
import time as _ar_time

st.session_state["_last_full_run"] = _ar_time.time()

if st.session_state.get("auto_refresh"):
    @st.fragment(run_every="60s")
    def _auto_refresh_tick():
        if _ar_time.time() - st.session_state.get("_last_full_run", 0.0) >= 55:
            st.rerun(scope="app")

    _auto_refresh_tick()
