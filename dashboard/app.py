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
    st.subheader("📡 Live-Aktivität")
    st.caption(
        "Strukturierte Bot-Events (Zyklen, Analysen, Trades) statt Log-Dateien. "
        "Füllt sich, sobald der Bot läuft."
    )

    _EV_ICON = {
        "cycle_start":   "🔄",
        "cycle_end":     "🏁",
        "analysis_done": "🔍",
        "trade":         "💼",
    }
    try:
        from system.live_status import feed_recent as _feed_recent
        _ev_rows = _feed_recent(limit=50)
    except Exception:
        _ev_rows = []

    if _ev_rows:
        for _ev in _ev_rows:
            _ic = _EV_ICON.get(_ev.get("event"), "•")
            _ts = (_ev.get("ts") or "")[:16].replace("T", " ")
            _tk = f" **{ticker_label(_ev['ticker'])}**" if _ev.get("ticker") else ""
            _dt = f" — {_ev['detail']}" if _ev.get("detail") else ""
            st.markdown(f"{_ic} `{_ts}`{_tk}{_dt}")
    else:
        st.info(
            "Noch keine Events aufgezeichnet — der Aktivitätsfeed füllt sich "
            "ab dem nächsten Bot-Lauf (Bot ist aktuell pausiert)."
            if _hdr_paused else
            "Noch keine Events aufgezeichnet — der Feed füllt sich ab dem "
            "nächsten Analyse-Zyklus."
        )

    st.divider()

    # ── Nächste Aktionen (Roadmap 1.5c) ─────────────────────────────────────
    st.subheader("⏭ Nächste Aktionen")
    _next_bits = []
    if _ls and _ls.get("state") == "idle" and _ls.get("next_run"):
        _next_bits.append("**Nächster Scheduler-Lauf:** "
                          + _ls["next_run"][:16].replace("T", " ") + " Uhr")
    if _hdr_paused:
        _next_bits.append("⏸ Bot pausiert — es sind keine Zyklen geplant, "
                          "bis er wieder gestartet wird.")
    for _nb in _next_bits:
        st.markdown(_nb)

    # systemd-Timer des Projekts (Backup, Pre-Market-Check, Quellen-Report):
    # letzter/nächster Lauf, ohne SSH + systemctl-Kommandos.
    try:
        import subprocess as _sp
        _lt = _sp.run(
            ["systemctl", "list-timers", "aktien_*", "--all", "--no-pager",
             "--output=json"],
            capture_output=True, text=True, timeout=5,
        )
        _timers = json.loads(_lt.stdout) if _lt.returncode == 0 and _lt.stdout else []
    except Exception:
        _timers = []
    if _timers:
        st.markdown("**systemd-Timer:**")
        for _t in _timers:
            _unit = _t.get("unit") or "?"
            _nxt = _t.get("next")
            _lst = _t.get("last")
            def _fmt_us(v):
                # systemd liefert µs-Epoch (int) oder Klartext, je nach Version
                try:
                    return datetime.fromtimestamp(int(v) / 1_000_000).strftime("%d.%m. %H:%M")
                except (TypeError, ValueError, OSError):
                    return str(v) if v else "–"
            st.markdown(f"- `{_unit}` · nächster Lauf: {_fmt_us(_nxt)} · "
                        f"letzter: {_fmt_us(_lst)}")
    else:
        st.caption("Keine aktiven systemd-Timer gefunden (oder Abfrage nicht "
                   "möglich) — Timer sind bei pausiertem Bot disabled.")


# ══════════════════════════════════════════════════════════
# TAB 1 – PORTFOLIO
# ══════════════════════════════════════════════════════════
with tab_portfolio:
    # Phase progress
    col_prog, col_phase_kpi = st.columns([3, 1])
    with col_prog:
        st.subheader("Wachstumsphase")
        progress = min(phase_info["progress_pct"] / 100, 1.0)
        st.progress(progress,
                    text=f"{phase_info['progress_pct']:.1f}% — ${total_value:,.2f} "
                         f"von ${phase_info['growth_target']:,.0f}")
        if phase_info["phase"] == "GROWTH":
            st.info(
                f"🌱 **Wachstumsphase** – noch "
                f"**${phase_info['remaining_to_goal']:,.2f}** bis zur Ausschüttungsphase  \n"
                f"Ziel: ${phase_info['growth_target']:,.0f} "
                f"({config.growth_target_multiple:.1f}× Startkapital)"
            )
        else:
            dist = phase_info.get("monthly_distribution", 0)
            st.success(
                f"💸 **Ausschüttungsphase!** Monatlich: **${dist:,.2f}** "
                f"(Ziel ${phase_info['monthly_target']:,.2f})  \n"
                f"Puffer: ${phase_info.get('buffer_reserve', 0):,.2f} "
                f"({config.distribution_buffer_months} Monate)"
            )
    with col_phase_kpi:
        st.metric("Startkapital",    f"${config.initial_capital:,.2f}")
        st.metric("Wachstumsziel",   f"${phase_info['growth_target']:,.0f}")
        st.metric("Investiert",      f"${invested:,.2f}")

    # Goal risk assessment (only shown when TARGET_GOAL_AMOUNT + TARGET_GOAL_DATE are set)
    _goal_assessor = GoalRiskAssessor(
        target_value=config.target_goal_amount,
        target_date_str=config.target_goal_date,
        initial_capital=config.initial_capital,
    )
    if _goal_assessor.active:
        st.divider()
        _ga = _goal_assessor.assess(total_value, acc)
        if _ga:
            _risk_colors = {OK: "success", CAUTION: "warning", DANGER: "error", UNREACHABLE: "error"}
            _risk_icons  = {OK: "✅", CAUTION: "🟡", DANGER: "⚠️", UNREACHABLE: "🚨"}
            _fn = getattr(st, _risk_colors.get(_ga.risk_level, "info"))
            _fn(
                f"{_risk_icons.get(_ga.risk_level, '')} **Ziel-Risikoanalyse** – "
                f"Risiko-Level: **{_ga.risk_level}**  \n"
                f"Ziel: ${_ga.target_value:,.0f} | "
                f"Fehlend: {_ga.shortfall_pct:.1f}% | "
                f"Noch {_ga.days_remaining} Tage | "
                f"Benötigte Rendite p.a.: {_ga.required_annual_return*100:.1f}% | "
                f"Realistisch: {_ga.realistic_annual_return*100:.1f}%  \n"
                + (f"_{_ga.note}_" if _ga.note else "")
            )
            if _ga.actions:
                with st.expander("Empfohlene Maßnahmen"):
                    for _action in _ga.actions:
                        st.write(f"→ {_action}")

    st.divider()

    # Open positions
    st.subheader("Offene Positionen")
    positions = portfolio.all_positions()
    if positions:
        rows = []
        _aging_warnings = []
        for ticker, pos in positions.items():
            price   = prices.get(ticker, pos.entry_price)
            pnl     = (price - pos.entry_price) * pos.shares
            pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
            days    = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(pos.entry_date)).days
            is_hedge = pos.rationale and pos.rationale.startswith("[HEDGE]")
            age_ratio = days / max(pos.target_hold_days, 1)
            if age_ratio >= 1.0:
                age_icon = "🔴"
            elif age_ratio >= 0.8:
                age_icon = "🟡"
            else:
                age_icon = "🟢"
            age_str = f"{age_icon} {days}/{pos.target_hold_days}d"
            if age_ratio >= 0.8 and pnl_pct < 0:
                _aging_warnings.append(
                    f"⚠️ **{ticker_label(ticker)}** seit {days}d ohne Gewinn "
                    f"(Ziel {pos.target_hold_days}d · P&L {pnl_pct:+.1f}%)"
                )
            rows.append({
                "Typ":        "🛡 Hedge" if is_hedge else "📈 Long",
                "Ticker":     ticker,
                "Stück":      pos.shares,
                "Einstieg $": pos.entry_price,
                "Aktuell $":  round(price, 2),
                "P&L $":      round(pnl, 2),
                "P&L %":      round(pnl_pct, 2),
                "SL $":       pos.stop_loss,
                "TP $":       pos.take_profit,
                "Alter":      age_str,
                "Katalysatoren": ", ".join(pos.entry_catalysts[:2]) if pos.entry_catalysts else "–",
            })

        if _aging_warnings:
            for _w in _aging_warnings:
                st.warning(_w)
        df = pd.DataFrame(rows)

        def _color_pnl(val):
            if isinstance(val, (int, float)):
                return "color: #00e676" if val >= 0 else "color: #f44336"
            return ""

        st.dataframe(
            df.style.map(_color_pnl, subset=["P&L $", "P&L %"]),
            width="stretch", hide_index=True,
        )
    else:
        st.info("Keine offenen Positionen.")

    st.divider()

    # Win-Rate summary
    if acc.get("total_closed", 0) > 0:
        wr1, wr2, wr3, wr4 = st.columns(4)
        wr1.metric("Win-Rate",          f"{acc['win_rate_pct']}%",
                   f"{acc['total_closed']} Trades")
        wr2.metric("Ø Rendite/Trade",   f"{acc['avg_return_pct']:+.2f}%")
        wr3.metric("Richtungs-Genauigkeit", f"{acc['direction_accuracy_pct']}%")
        wr4.metric("Zielkurs-Trefferquote", f"{acc['target_hit_pct']}%")
        st.divider()

    # Bot-Score Bewertungsmaßstab
    try:
        _bot_score = BotScorer().get()
        _mod = get_modifiers(_bot_score.current)
        _score_color = (
            "🟢" if _bot_score.current >= 75 else
            "🟡" if _bot_score.current >= 40 else "🔴"
        )
        with st.expander(
            f"{_score_color} **Bot-Score: {_bot_score.current:.1f}/100** — {_bot_score.label}",
            expanded=True,
        ):
            _sc1, _sc2, _sc3, _sc4 = st.columns(4)
            _sc1.metric("Score",        f"{_bot_score.current:.1f}/100",
                        f"Peak: {_bot_score.peak:.1f}")
            _sc2.metric("Trades bewertet", str(_bot_score.trades_scored))
            _sc3.metric("Verdient",     f"+{_bot_score.total_earned:.1f} Pkt")
            _sc4.metric("Verloren",     f"-{_bot_score.total_lost:.1f} Pkt")

            st.progress(min(_bot_score.current / 100, 1.0),
                        text=f"{_bot_score.bar}  {_bot_score.label}")

            st.markdown("**Aktive Verhaltens-Modifier** *(Score-Stufe: " + _mod.score_range + ")*")
            _m1, _m2, _m3, _m4 = st.columns(4)
            _thr_sign = f"{_mod.threshold_adj:+.2f}" if _mod.threshold_adj != 0 else "±0.00"
            _pos_sign = f"{_mod.position_count_adj:+d}" if _mod.position_count_adj != 0 else "±0"
            _size_pct = f"{(_mod.position_size_mult - 1) * 100:+.0f}%"
            _hold_pct = f"{(_mod.hold_days_mult - 1) * 100:+.0f}%"
            _m1.metric("Kaufschwelle",      _thr_sign)
            _m2.metric("Max. Positionen",   _pos_sign)
            _m3.metric("Positionsgröße",    _size_pct)
            _m4.metric("Haltedauer",        _hold_pct)
            st.caption(_mod.description)

            # Score-Stufen-Übersicht
            _stages = [
                (90, "Elite",        "−0.06 Schwelle, +3 Pos., +40% Größe"),
                (75, "Exzellent",    "−0.04 Schwelle, +2 Pos., +25% Größe"),
                (60, "Stark",        "−0.02 Schwelle, +1 Pos., +10% Größe"),
                (40, "Standard",     "Baseline – keine Änderung"),
                (25, "Lernend",      "+0.03 Schwelle, −1 Pos., −15% Größe"),
                ( 0, "Eingeschränkt","+0.05 Schwelle, −2 Pos., −30% Größe"),
            ]
            st.markdown("**Score-Skala:**")
            for _thr, _lbl, _desc in _stages:
                _active = "**►**" if _mod.score_range == _lbl else "  "
                st.markdown(f"{_active} `{_thr:>3}+` &nbsp; **{_lbl}** — {_desc}")

            # Persönliche Bestleistungen
            _pb = _bot_score.personal_bests
            if any(getattr(_pb, f) for f in ("best_win_rate_20", "best_avg_return_20",
                                              "best_streak", "best_single_trade")):
                st.markdown("**Persönliche Rekorde:**")
                _pb_cols = st.columns(4)
                def _pb_metric(col, label, rec):
                    if rec:
                        col.metric(label, f"{rec.value:.1f}", f"{rec.date} (×{rec.times_beaten})")
                _pb_metric(_pb_cols[0], "Win-Rate 20 Tr.",   _pb.best_win_rate_20)
                _pb_metric(_pb_cols[1], "Ø-Rendite 20 Tr.",  _pb.best_avg_return_20)
                _pb_metric(_pb_cols[2], "Gewinnserie",        _pb.best_streak)
                _pb_metric(_pb_cols[3], "Bester Trade %",     _pb.best_single_trade)

            # Letzte Score-Einträge
            if _bot_score.history:
                st.markdown("**Letzte Trades (Score-Punkte):**")
                _hist_rows = []
                for _e in reversed(_bot_score.history[-10:]):
                    _hist_rows.append({
                        "Datum":    _e.date[:10],
                        "Ticker":   _e.ticker,
                        "Δ Punkte": f"{'+' if _e.delta >= 0 else ''}{_e.delta:.1f}",
                        "Score →":  f"{_e.score_after:.1f}",
                        "Grund":    _e.reason,
                    })
                st.dataframe(pd.DataFrame(_hist_rows), width="stretch", hide_index=True)

            # Meilensteine
            if _bot_score.milestones:
                _reached = [MILESTONES[m][0] for m in sorted(_bot_score.milestones)]
                st.markdown("**Meilensteine:** " + "  ·  ".join(_reached))
    except Exception as _e:
        st.caption(f"Bot-Score nicht verfügbar: {_e}")

    # Portfolio value chart
    st.subheader("Portfoliowert – Verlauf")
    _port_range = st.radio(
        "Zeitraum", ["1 Tag", "1 Woche", "1 Monat", "Alles"],
        horizontal=True, key="port_range", index=3,
    )
    _port_days = {"1 Tag": 1, "1 Woche": 7, "1 Monat": 30, "Alles": 180}[_port_range]
    history = tracker.get_value_history(_port_days)
    if len(history) >= 2:
        import altair as _alt
        df_hist = pd.DataFrame(history[::-1])
        # snapshot_date mischt Datum-only (neu) und volle ISO-Zeitstempel (alt) →
        # format='ISO8601' parst beide Varianten ohne Inferenz-Fehler.
        df_hist["snapshot_date"] = pd.to_datetime(df_hist["snapshot_date"], format="ISO8601")
        # Ausreißer entfernen: Punkte die >5× dem Minimum (= echter Baseline) liegen sind
        # Datenfehler (z.B. korruptes Portfolio-Cash nach Mehrfach-Neustart).
        _min_val = df_hist["total_value"].min()
        _clean = df_hist[df_hist["total_value"] <= _min_val * 5]
        if not _clean.empty:
            df_hist = _clean
        _start_val = float(df_hist["total_value"].iloc[0])
        _port_line = _alt.Chart(df_hist).mark_line(color="#00e676", strokeWidth=2).encode(
            x=_alt.X("snapshot_date:T", title="Datum"),
            y=_alt.Y("total_value:Q", title="Wert ($)", scale=_alt.Scale(zero=False)),
            tooltip=[_alt.Tooltip("snapshot_date:T", title="Datum"), _alt.Tooltip("total_value:Q", title="Portfolio $", format=",.2f")],
        )
        # SPY Benchmark overlay
        _spy_df = _get_spy_benchmark(_port_days, _start_val)
        if not _spy_df.empty:
            _spy_df = _spy_df[_spy_df["snapshot_date"] >= df_hist["snapshot_date"].min()]
            _spy_line = _alt.Chart(_spy_df).mark_line(
                color="#888888", strokeDash=[6, 3], strokeWidth=1.5
            ).encode(
                x=_alt.X("snapshot_date:T"),
                y=_alt.Y("spy_value:Q", scale=_alt.Scale(zero=False)),
                tooltip=[_alt.Tooltip("snapshot_date:T", title="Datum"), _alt.Tooltip("spy_value:Q", title="S&P 500 (normiert $)", format=",.2f")],
            )
            st.altair_chart(
                _alt.layer(_port_line, _spy_line).properties(height=280).resolve_scale(y="shared"),
                width="stretch",
            )
            st.caption("🟢 Portfolio  ·  ╌╌╌ S&P 500 (normiert auf Startkapital)")
        else:
            st.altair_chart(_port_line.properties(height=280), width="stretch")
    else:
        st.info("Noch keine Chart-Daten — erscheint nach dem ersten Analysezyklus.")

    # All transactions
    with st.expander("Alle Transaktionen"):
        trades = portfolio.trade_history()
        if trades:
            trade_rows = [{
                "Datum":   t.timestamp[:10],
                "Ticker":  t.ticker,
                "Aktion":  t.action,
                "Stück":   t.shares,
                "Kurs $":  t.price,
                "P&L $":   round(t.pnl, 2) if t.pnl else 0,
                "Grund":   (t.reason or "")[:60],
            } for t in reversed(trades)]
            _df_all_trades = pd.DataFrame(trade_rows)
            st.dataframe(_df_all_trades, width="stretch", hide_index=True)
            st.download_button(
                "📥 CSV exportieren",
                _df_all_trades.to_csv(index=False).encode("utf-8"),
                f"trades_{datetime.now().strftime('%Y%m%d')}.csv",
                "text/csv",
                width="stretch",
            )
        else:
            st.info("Noch keine Transaktionen.")


# ══════════════════════════════════════════════════════════
# TAB 2 – ENTSCHEIDUNGEN (Warum tut der Bot, was er tut?)
# ══════════════════════════════════════════════════════════
with tab_decisions:
    st.subheader("🧠 Entscheidungs-Transparenz")
    st.caption(
        "Der komplette innere Ablauf pro Zyklus: Welche Aktien wurden analysiert, "
        "was hat die Strategie daraus gemacht — und **warum** (Schwelle, Quellenlage, "
        "Slots, Korrelation, Filter …)."
    )

    _BUCKET_LABELS = {
        "kein_kaufsignal":   "Kein Kaufsignal (Empfehlung/Richtung)",
        "unter_schwelle":    "Sentiment unter Kaufschwelle",
        "zu_wenige_quellen": "Zu dünne Quellenlage",
        "max_positionen":    "Alle Positions-Slots belegt",
        "earnings_sperre":   "Earnings-Sperre",
        "korrelation":       "Sektor-Korrelation zu hoch",
        "liquiditaet":       "Liquiditäts-Gate",
        "lernfilter_avoid":  "Selbstlern-Filter (AVOID)",
        "positionsgroesse":  "Positionsgröße = 0",
        "tagesverlust":      "Tagesverlust-Limit",
        "kein_kurs":         "Kein Kurs verfügbar",
        "sonstiges":         "Sonstiges",
    }
    _ACTION_ICON = {"BUY": "🟢", "SELL": "🔴", "SKIP": "⏭", "HOLD": "⏸"}
    _SOURCE_LABEL = {
        "cycle":       "Analyse-Zyklus",
        "queue":       "Signal-Queue-Drain",
        "conditional": "Bedingter Einstieg",
        "sl_tp":       "SL/TP-Überwachung",
    }

    try:
        from analyzers.decision_log import DecisionLog as _DecisionLog
        _dlog_dash = _DecisionLog()
        _dec_days = _dlog_dash.days(limit=30)
    except Exception as _dl_dash_err:
        _dlog_dash, _dec_days = None, []
        st.caption(f"Decision-Log nicht verfügbar: {_dl_dash_err}")

    if _dec_days:
        _sel_day = st.selectbox("Tag", _dec_days, key="dec_day_select")
        _fn = _dlog_dash.funnel(_sel_day)
        _acts = _fn["actions"]

        # ── Funnel des Tages ────────────────────────────────────────────────
        fc1, fc2, fc3, fc4, fc5 = st.columns(5)
        fc1.metric("Entscheidungen", _fn["total"])
        fc2.metric("🟢 Käufe",      _acts.get("BUY", 0))
        fc3.metric("🔴 Verkäufe",   _acts.get("SELL", 0))
        fc4.metric("⏸ Halten",      _acts.get("HOLD", 0))
        fc5.metric("⏭ Übersprungen", _acts.get("SKIP", 0))

        if _fn["skip_reasons"]:
            st.markdown("**Warum wurde übersprungen?**")
            _sr_total = sum(_fn["skip_reasons"].values()) or 1
            for _b, _n in _fn["skip_reasons"].items():
                _pct = _n / _sr_total
                st.progress(_pct, text=f"{_BUCKET_LABELS.get(_b, _b)} — {_n}× ({_pct:.0%})")

        st.divider()

        # ── Einzel-Entscheidungen ───────────────────────────────────────────
        _dec_filter = st.multiselect(
            "Aktionen filtern", ["BUY", "SELL", "SKIP", "HOLD"],
            default=["BUY", "SELL", "SKIP", "HOLD"], key="dec_action_filter",
        )
        _dec_entries = [e for e in _dlog_dash.get_day(_sel_day)
                        if (e.get("action") or "").upper() in _dec_filter]
        st.caption(f"{len(_dec_entries)} Entscheidungen am {_sel_day}")
        for _e in _dec_entries[:100]:
            _a = (_e.get("action") or "?").upper()
            _icon = _ACTION_ICON.get(_a, "•")
            _sc = _e.get("sentiment_score")
            _sc_txt = f" · Score {_sc:.2f}" if isinstance(_sc, (int, float)) else ""
            with st.expander(
                f"{_icon} **{ticker_label(_e['ticker'])}** — {_a}{_sc_txt} "
                f"· {(_e.get('decided_at') or '')[11:16]} Uhr "
                f"· {_SOURCE_LABEL.get(_e.get('source'), _e.get('source') or '–')}"
            ):
                st.markdown(f"**Strategie-Begründung:** {_e.get('reason') or '–'}")
                if _e.get("executed"):
                    st.markdown(f"**Ausführung:** {_e['executed']}")
                _dc1, _dc2, _dc3, _dc4 = st.columns(4)
                _dc1.metric("KI-Empfehlung", _e.get("recommendation") or "–")
                _dc2.metric("Konfidenz",     _e.get("confidence") or "–")
                _dc3.metric("Regime",        _e.get("regime") or "–")
                _mb = _e.get("macro_bias")
                _dc4.metric("Makro-Bias",
                            f"{_mb:+.2f}" if isinstance(_mb, (int, float)) else "–")
                if _e.get("sources_used") is not None:
                    st.caption(f"Quellenlage: {_e['sources_used']} Beiträge · "
                               f"Richtung: {_e.get('direction') or '–'}")
                # Verkettung → analysis_log (Roadmap 1.4b): die Analyse, die zu
                # dieser Entscheidung führte, samt Quellen-Provenienz. Nur bei
                # Einträgen ab Einbau gefüllt (Alt-Zeilen: analysis_id NULL).
                if _e.get("analysis_id"):
                    try:
                        _lk = AnalysisLog().get_by_id(int(_e["analysis_id"]))
                    except Exception:
                        _lk = None
                    if _lk:
                        st.markdown("---")
                        st.markdown(
                            f"**🔍 Zugehörige Analyse** (#{_e['analysis_id']} · "
                            f"{(_lk.get('analyzed_at') or '')[:16]})"
                        )
                        if _lk.get("entry_rationale"):
                            st.caption(_lk["entry_rationale"])
                        render_sources_breakdown(_lk.get("sources_breakdown"),
                                                 total=_lk.get("sources_used"))
                    else:
                        st.caption(f"Zugehörige Analyse #{_e['analysis_id']} "
                                   "nicht (mehr) im Analyse-Log gefunden.")
    else:
        # Noch keine Entscheidungs-Daten (Log neu / Bot pausiert) → ehrlicher
        # Hinweis + Empfehlungs-Funnel aus dem Analyse-Log als Vorschau.
        st.info(
            "Noch keine Strategie-Entscheidungen aufgezeichnet — das Decision-Log "
            "füllt sich ab dem nächsten Bot-Lauf. Bis dahin unten der "
            "Empfehlungs-Funnel aus dem Analyse-Log (KI-Sicht, ohne Strategie-Gründe)."
        )
        try:
            _alog_dec = AnalysisLog()
            _prev_cycle = _alog_dec.get_last_cycle_tickers()
            _prev_entries = _alog_dec.get_latest_per_ticker(limit=200)
            _prev_recs: dict = {}
            for _pe in _prev_entries:
                _r = _pe.get("recommendation") or "?"
                _prev_recs[_r] = _prev_recs.get(_r, 0) + 1
            if _prev_entries:
                pc1, pc2, pc3, pc4 = st.columns(4)
                pc1.metric("Aktien im Log", len(_prev_entries))
                pc2.metric("🟢 BUY-Empfehlungen", _prev_recs.get("BUY", 0))
                pc3.metric("⏸ HOLD", _prev_recs.get("HOLD", 0))
                pc4.metric("⏭ SKIP", _prev_recs.get("SKIP", 0))
                st.caption(
                    "Details pro Aktie im Tab **🔍 Analyse-Log**. Sobald der Bot "
                    "wieder läuft, erscheint hier zusätzlich der Strategie-Schritt "
                    "(gekauft / übersprungen + Grund)."
                )
        except Exception:
            pass


# ══════════════════════════════════════════════════════════
# TAB 3 – MARKT-REGIME
# ══════════════════════════════════════════════════════════
with tab_regime:
    if not regime_data:
        st.info("Noch kein Regime-Snapshot. Läuft beim nächsten Analyse-Zyklus.")
    else:
        regime  = regime_data["regime"]
        score   = regime_data["recession_score"]
        color   = _REGIME_COLOR.get(regime, "#888")

        # Top row
        r1, r2, r3, r4 = st.columns(4)
        r1.markdown(
            f"<div style='text-align:center;padding:10px;background:#1e2130;"
            f"border:2px solid {color};border-radius:12px;'>"
            f"<div style='font-size:2rem;font-weight:700;color:{color};'>{_REGIME_ICON[regime]} {regime}</div>"
            f"<div style='font-size:0.75rem;color:#aaa;margin-top:4px;'>Marktregime</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        r2.metric("Rezessions-Score", f"{score:.3f}", help="0=BULL, 1=CRISIS")
        vix_val = regime_data.get("vix")
        r3.metric("VIX", f"{vix_val:.1f}" if vix_val else "–")
        ys = regime_data.get("yield_spread")
        r4.metric("Zinskurve (10y−2y)", f"{ys:.2f}%" if ys else "–",
                  delta_color="inverse" if ys and ys < 0 else "normal")

        # Alter des Snapshots sichtbar machen — ein eingefrorener Regime-Wert
        # sieht sonst wie ein aktueller aus.
        _snap_ts = (regime_data.get("recorded_at") or "")[:16].replace("T", " ")
        if _snap_ts:
            try:
                _snap_age_h = (datetime.now(timezone.utc).replace(tzinfo=None)
                               - datetime.fromisoformat(regime_data["recorded_at"])).total_seconds() / 3600
            except Exception:
                _snap_age_h = None
            if _snap_age_h is not None and _snap_age_h > 24:
                st.warning(f"⚠️ Regime-Snapshot ist {int(_snap_age_h // 24)} Tag(e) alt "
                           f"(vom {_snap_ts}) – Werte spiegeln nicht den aktuellen Markt.")
            else:
                st.caption(f"Snapshot: {_snap_ts}")

        st.divider()

        # Score gauge – farbige Zonen + Nadel an aktueller Position
        st.subheader("Rezessions-Score-Gauge")
        _needle_pct = min(score * 100, 100)
        st.markdown(
            f"""<div style='position:relative;margin-bottom:4px;'>
              <div style='
                background:linear-gradient(to right,
                  #00c853 0%,   #00c853 25%,
                  #ffd600 25%,  #ffd600 45%,
                  #ff6d00 45%,  #ff6d00 65%,
                  #d50000 65%,  #d50000 100%);
                height:26px;border-radius:8px;position:relative;'>
                <!-- Schwellen-Markierungen -->
                <div style='position:absolute;left:25%;top:0;width:2px;height:100%;background:rgba(0,0,0,0.35);'></div>
                <div style='position:absolute;left:45%;top:0;width:2px;height:100%;background:rgba(0,0,0,0.35);'></div>
                <div style='position:absolute;left:65%;top:0;width:2px;height:100%;background:rgba(0,0,0,0.35);'></div>
                <!-- Nadel -->
                <div style='position:absolute;left:{_needle_pct:.1f}%;top:-5px;
                  transform:translateX(-50%);width:3px;height:36px;
                  background:white;border-radius:2px;
                  box-shadow:0 0 5px rgba(0,0,0,0.9);'></div>
              </div>
            </div>
            <div style='display:flex;justify-content:space-between;
                        font-size:0.72rem;color:#888;margin-top:2px;'>
              <span>0 – BULL</span><span>0.25 – NEUTRAL</span>
              <span>0.45 – BEAR</span><span>0.65+ – CRISIS</span><span>1.0</span>
            </div>""",
            unsafe_allow_html=True,
        )

        st.divider()

        # Signal components
        st.subheader("Signal-Komponenten")
        components = regime_data.get("components") or {}
        if isinstance(components, str):
            components = json.loads(components)

        comp_rows = []
        labels = {
            "vix":            ("VIX – Angst-Index",          "30%"),
            "yield_curve":    ("Zinskurve (2y vs 10y)",       "25%"),
            "sp500_ma200":    ("S&P 500 vs 200-Tage-MA",      "20%"),
            "sector_breadth": ("Marktbreite (Sektor-Trends)", "15%"),
            "credit_spread":  ("Credit Spread (HYG/IEI)",     "10%"),
            "claude_macro":   ("Claude Makro-Analyse",         "20%*"),
        }
        for key, (name, weight) in labels.items():
            comp = components.get(key, {})
            if not comp:
                continue
            score_c = comp.get("score", 0)
            label_c = comp.get("label", "") or ""
            if key == "vix" and comp.get("value") is not None:
                detail = f"VIX={comp['value']:.1f} · {label_c}"
            elif key == "yield_curve" and comp.get("spread_pct") is not None:
                detail = f"Spread={comp['spread_pct']:.2f}% · {label_c}"
            elif key == "sp500_ma200" and comp.get("gap_pct") is not None:
                detail = f"Gap={comp['gap_pct']:+.1f}% · {label_c}"
            elif key == "sector_breadth":
                detail = label_c
            elif key == "credit_spread":
                r = comp.get("ratio")
                detail = (f"HYG/IEI={r:.4f} · {label_c}" if r is not None else label_c)
            elif key == "claude_macro":
                detail = comp.get("summary", "")[:80]
            else:
                detail = label_c
            # Mini-Gauge als HTML: farbiger Balken pro Score
            _sc = min(max(float(score_c), 0.0), 1.0)
            _bar_col = ("#00c853" if _sc < 0.25 else
                        "#ffd600" if _sc < 0.45 else
                        "#ff6d00" if _sc < 0.65 else "#d50000")
            comp_rows.append({
                "Signal":   name,
                "Gewicht":  weight,
                "Score":    _sc,
                "Detail":   detail,
            })

        if comp_rows:
            df_comp = pd.DataFrame(comp_rows)
            st.dataframe(
                df_comp.style
                    .format({"Score": "{:.3f}"})
                    .background_gradient(subset=["Score"], cmap="RdYlGn_r", vmin=0, vmax=1),
                width="stretch", hide_index=True,
            )

        # Macro summary from Claude
        macro_sum = regime_data.get("macro_summary", "")
        if macro_sum:
            st.info(f"**Claude Makro-Einschätzung:** {macro_sum}")

        st.divider()

        # Regime history chart
        st.subheader("Regime-Verlauf")

        # Alle vorhandenen Daten laden um Verfügbarkeit anzuzeigen
        _all_history_r = detector.get_history(365)
        _avail_days = 0
        if _all_history_r:
            from datetime import timezone as _tz
            _oldest = pd.to_datetime(_all_history_r[-1]["recorded_at"])
            _avail_days = max(1, (datetime.now(timezone.utc).replace(tzinfo=None) - _oldest.replace(tzinfo=None)).days + 1)

        _reg_range = st.radio(
            "Zeitraum", ["1 Woche", "2 Wochen", "1 Monat"],
            horizontal=True, key="reg_range", index=2,
        )
        _reg_days = {"1 Woche": 7, "2 Wochen": 14, "1 Monat": 30}[_reg_range]

        if _avail_days < _reg_days:
            st.caption(
                f"Daten vorhanden seit: **{_avail_days} Tag(e)** — "
                f"der gewählte Zeitraum wird mit mehr Daten gefüllt sobald der Bot länger läuft."
            )

        history_r = detector.get_history(_reg_days)
        if len(history_r) >= 2:
            import altair as _alt
            df_r = pd.DataFrame(history_r[::-1])
            df_r["recorded_at"] = pd.to_datetime(df_r["recorded_at"])
            _reg_chart = _alt.Chart(df_r).mark_line(color="#4fc3f7", strokeWidth=2).encode(
                x=_alt.X("recorded_at:T", title="Datum"),
                y=_alt.Y("recession_score:Q", title="Rezessions-Score", scale=_alt.Scale(domain=[0, 1])),
                tooltip=[
                    _alt.Tooltip("recorded_at:T", title="Zeit"),
                    _alt.Tooltip("recession_score:Q", title="Score", format=".3f"),
                    _alt.Tooltip("regime:N", title="Regime"),
                ],
            ).properties(height=260)
            st.altair_chart(_reg_chart, width="stretch")
            # Regime breakdown table
            with st.expander("Datentabelle"):
                st.dataframe(
                    df_r[["recession_score", "regime", "vix", "yield_spread"]].rename(columns={
                        "recession_score": "Score", "regime": "Regime",
                        "vix": "VIX", "yield_spread": "Zinskurve",
                    }),
                    width="stretch",
                )
        else:
            st.info("Noch zu wenige Datenpunkte.")

        st.divider()

        # Hedge positions
        st.subheader("Aktive Hedge-Positionen")
        hedge_positions = [
            (t, p) for t, p in portfolio.all_positions().items()
            if p.rationale and p.rationale.startswith("[HEDGE]")
        ]
        if hedge_positions:
            hrows = []
            for ticker, pos in hedge_positions:
                price   = prices.get(ticker, pos.entry_price)
                pnl     = (price - pos.entry_price) * pos.shares
                pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
                days    = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(pos.entry_date)).days
                hrows.append({
                    "Ticker":     ticker,
                    "Stück":      pos.shares,
                    "Einstieg $": pos.entry_price,
                    "Aktuell $":  round(price, 2),
                    "P&L $":      round(pnl, 2),
                    "P&L %":      round(pnl_pct, 2),
                    "SL $":       pos.stop_loss,
                    "TP $":       pos.take_profit,
                    "Tage":       days,
                    "Rationale":  (pos.rationale or "")[:60],
                })
            st.dataframe(pd.DataFrame(hrows), width="stretch", hide_index=True)
        else:
            enabled = config.enable_hedging
            if not enabled:
                st.warning("Hedging ist deaktiviert (ENABLE_HEDGING=false in .env)")
            elif regime in (BULL, NEUTRAL):
                st.success(f"Kein Hedge nötig – Regime ist {regime}.")
            else:
                st.info("Keine Hedge-Positionen offen (noch kein Kapital zugewiesen oder gerade geschlossen).")


# ══════════════════════════════════════════════════════════
# TAB 3 – SIGNAL-QUEUE
# ══════════════════════════════════════════════════════════
with tab_queue:
    # ── Analyse-Warteschlange (user_request_queue) ────────────────────────────
    st.subheader("🔍 Nächste Analyse-Runde")
    st.caption(
        "Ticker die vom Headline-Scanner, Geopolitik-Radar oder manuell vorgemerkt wurden "
        "und beim nächsten Analyse-Zyklus zusätzlich untersucht werden."
    )
    if _analysis_queue:
        _aq_cols = st.columns(min(len(_analysis_queue), 6))
        for _i, _aq_t in enumerate(_analysis_queue):
            _aq_cols[_i % 6].info(f"🔍 **{_aq_t}**")
        st.caption(f"{len(_analysis_queue)} Ticker warten auf Analyse – Queue wird beim nächsten Zyklus geleert.")
    else:
        st.success("Keine Ticker in der Analyse-Warteschlange.")

    from analyzers.user_request_queue import add_ticker as _urq_add
    _manual_ticker = st.text_input(
        "Ticker manuell zur Analyse vormerken",
        placeholder="z.B. CRM oder SAP.DE",
        key="manual_analysis_ticker",
    ).strip().upper()
    if st.button("➕ Zur nächsten Analyse hinzufügen", key="btn_add_analysis"):
        if _manual_ticker:
            _urq_add(_manual_ticker)
            st.success(f"{_manual_ticker} wird beim nächsten Zyklus analysiert.")
            st.rerun()
        else:
            st.warning("Bitte einen Ticker eingeben.")

    st.divider()

    # ── Bedingte Einstiege (Conditional Entries) ──────────────────────────────
    st.subheader("📌 Bedingte Einstiege")
    st.caption(
        "Aktien die Claude als SKIP bewertet hat, aber bei einem günstigeren "
        "Kurs automatisch gekauft werden. Der Bot prüft alle 15 Min ob der "
        "Trigger-Kurs erreicht wurde."
    )
    try:
        from analyzers.conditional_entry import ConditionalEntryWatcher
        _ce_watcher = ConditionalEntryWatcher()
        _ce_active = _ce_watcher.get_active()
        if _ce_active:
            _ce_prices = {
                e.ticker: broker.get_price(e.ticker) or e.price_at_creation
                for e in _ce_active
            }
            for ce in _ce_active:
                _cur = _ce_prices.get(ce.ticker, ce.price_at_creation)
                # Watcher löst auf Ausbruch nach oben aus (Kurs >= Trigger).
                # _pct_away = noch fehlende Distanz nach oben bis zum Trigger.
                _pct_away = (ce.trigger_price - _cur) / _cur * 100 if _cur else 0
                _triggered = _cur >= ce.trigger_price if _cur else False
                _icon = "🟢" if _triggered else ("🟡" if _pct_away < 3 else "⚪")
                with st.expander(
                    f"{_icon} **{ce.ticker}** – Trigger ${ce.trigger_price:.2f} "
                    f"· Aktuell ${_cur:.2f} "
                    f"· {'🔥 AUSGELÖST' if _triggered else f'{_pct_away:.1f}% entfernt'}"
                    f" · läuft ab {ce.expires_at[:10]}",
                    expanded=_triggered,
                ):
                    _cc1, _cc2, _cc3 = st.columns(3)
                    _cc1.metric("Trigger-Kurs",    f"${ce.trigger_price:.2f}",
                                f"{ce.pct_to_trigger:+.1f}% vs. Analyse-Kurs")
                    _cc2.metric("Aktueller Kurs",  f"${_cur:.2f}",
                                f"{'🔥 Trigger erreicht!' if _triggered else f'noch {_pct_away:.1f}% bis Trigger'}")
                    _cc3.metric("Kursziel (Claude)", f"${ce.target_price:.2f}" if ce.target_price else "–")
                    st.markdown(f"**Bull-Case:** {ce.bull_case}")
                    st.markdown(f"**Bear-Case:** {ce.bear_case}")
                    st.markdown(f"**Begründung:** {ce.entry_rationale}")
                    if ce.key_catalysts:
                        st.markdown("**Katalysatoren:** " + " · ".join(ce.key_catalysts))
                    if st.button(f"🗑 {ce.ticker} entfernen", key=f"rm_ce_{ce.ticker}"):
                        _ce_watcher.remove(ce.ticker)
                        st.rerun()
        else:
            st.info("Keine aktiven bedingten Einstiege.")
    except Exception as _ce_dash_err:
        st.caption(f"Conditional Entries nicht verfügbar: {_ce_dash_err}")

    st.divider()
    st.subheader("Ausstehende BUY-Signale")
    st.caption(
        "Wenn ein starkes BUY-Signal eintrifft aber kein Kapital frei ist, "
        "landet es hier. Sobald ein Trade geschlossen wird, wird das Signal automatisch ausgeführt."
    )

    pending = sig_queue.get_pending()
    if pending:
        for sig in pending:
            created  = sig["created_at"][:16]
            expires  = sig["expires_at"][:16]
            catalysts = sig.get("key_catalysts") or []
            risks     = sig.get("risk_factors") or []
            conf_color = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(sig["confidence"], "⚪")
            with st.expander(
                f"{conf_color} **{sig['ticker']}** · Score {sig['sentiment_score']:.2f} "
                f"· Erstellt {created} · Verfällt {expires}"
            ):
                c1, c2 = st.columns(2)
                with c1:
                    st.metric("Sentiment-Score", f"{sig['sentiment_score']:.2f}")
                    st.metric("Konfidenz",        sig["confidence"])
                    if sig.get("target_price"):
                        st.metric("Zielkurs",    f"${sig['target_price']:.2f}")
                    st.metric("Geplante Haltedauer", f"{sig.get('suggested_hold_days', '?')}d")
                with c2:
                    st.markdown("**Begründung**")
                    st.info(sig.get("entry_rationale") or "–")
                    if catalysts:
                        st.markdown("**Katalysatoren:** " + " · ".join(catalysts))
                    if risks:
                        st.markdown("**Risiken:** " + " · ".join(risks))
    else:
        st.success("Keine ausstehenden Signale – der Bot ist vollständig investiert oder wartet auf neue Signale.")

    st.divider()
    st.subheader("Signal-Historie (letzte 20)")
    history_q = sig_queue.get_history(20)
    if history_q:
        status_icon = {
            "pending":    "⏳",
            "executed":   "✅",
            "expired":    "⏰",
            "superseded": "🔄",
        }
        df_q = pd.DataFrame([{
            "Status":      status_icon.get(s["status"], "?") + " " + s["status"].capitalize(),
            "Ticker":      s["ticker"],
            "Score":       s["sentiment_score"],
            "Konfidenz":   s["confidence"],
            "Erstellt":    s["created_at"][:16],
            "Verfällt":    s["expires_at"][:16],
            "Begründung":  (s.get("entry_rationale") or "")[:70],
        } for s in history_q])
        st.dataframe(df_q, width="stretch", hide_index=True)
    else:
        st.info("Noch keine Signal-Historie.")


# ══════════════════════════════════════════════════════════
# TAB 4 – AKTIEN-NETZWERK
# ══════════════════════════════════════════════════════════
with tab_network:
    st.subheader("🕸 Aktien-Netzwerk")
    st.caption("Alle vom Bot analysierten Aktien und ihre thematischen Verbindungen.")

    try:
        import math
        import plotly.graph_objects as go
        from analyzers.bot_data_bridge import BotDataBridge
        from analyzers.stock_relations import StockRelations

        _bridge      = BotDataBridge()
        _net_rel     = StockRelations()
        _all_states  = _bridge.get_all_states()

        if not _all_states:
            st.info("Noch keine Analyse-Daten. Der Bot muss mindestens einen Analyse-Zyklus abgeschlossen haben.")
        else:
            # ── Filter ──────────────────────────────────────────────────────────
            _net_col1, _net_col2, _net_col3 = st.columns([2, 2, 2])
            with _net_col1:
                _rec_filter = st.multiselect(
                    "Empfehlung filtern",
                    ["BUY", "HOLD", "SELL", "SKIP"],
                    default=["BUY", "HOLD", "SELL", "SKIP"],
                )
            with _net_col2:
                _show_isolated = st.checkbox("Ticker ohne Verbindungen anzeigen", value=True)
            with _net_col3:
                _show_edges = st.checkbox("Verbindungslinien anzeigen", value=False)

            # ── Cross-Listing-Deduplizierung: gleiche Firma, verschiedene Börsenplätze ──
            # Zentrale Map aus analyzers/stock_relations (Single Source of Truth).
            from analyzers.stock_relations import CROSS_LISTINGS as _CANONICAL

            # ── Node-Daten aus BotDataBridge (einheitliche Quelle) ──────────────
            _rec_color = {"BUY": "#00e676", "HOLD": "#ffd740", "SELL": "#f44336", "SKIP": "#888888"}
            _nodes: dict = {}
            # Erst alle States sammeln und nach kanonischem Ticker mergen
            # (bestes Signal gewinnt: BUY > HOLD > SELL > SKIP)
            _REC_RANK = {"BUY": 4, "HOLD": 3, "SELL": 2, "SKIP": 1, "UNKNOWN": 0}
            _merged_states: dict = {}  # canon_ticker → _ts
            for _raw_t, _ts in _all_states.items():
                _canon = _CANONICAL.get(_raw_t, _raw_t)
                if _canon not in _merged_states:
                    _merged_states[_canon] = _ts
                else:
                    # Besser bewertetes Signal gewinnt
                    _cur_rank = _REC_RANK.get(_merged_states[_canon].recommendation or "UNKNOWN", 0)
                    _new_rank = _REC_RANK.get(_ts.recommendation or "UNKNOWN", 0)
                    if _new_rank > _cur_rank:
                        _merged_states[_canon] = _ts

            _nodes: dict = {}
            for ticker, _ts in _merged_states.items():
                raw_rec = _ts.recommendation or "UNKNOWN"
                # UNKNOWN / leere Empfehlungen → SKIP (kein echter Signal)
                rec = raw_rec if raw_rec in _rec_color else "SKIP"
                if rec not in _rec_filter:
                    continue
                # Sehr alte Analysen (> 7 Tage) → immer als SKIP markieren
                _stale = False
                if _ts.analyzed_at:
                    try:
                        from datetime import timezone
                        _age_days = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(
                            _ts.analyzed_at.replace("Z", "")
                        )).days
                        _stale = _age_days > 7
                    except Exception:
                        pass
                if _stale:
                    rec = "SKIP"
                    if "SKIP" not in _rec_filter:
                        continue
                _nodes[ticker] = {
                    "rec":    rec,
                    "score":  round(_ts.score, 2),
                    "date":   _ts.analyzed_at[:10] if _ts.analyzed_at else "",
                    "color":  _rec_color[rec],
                    "source": _ts.rec_source,
                    "stale":  _stale,
                }

            # ── Kanten: dynamisch + statische Themen-Cluster ────────────────────
            _edges: list = []
            _edge_labels: list = []
            _edge_seen: set = set()
            _get_related = lambda t: _net_rel.get_related(t)[:6]

            # Themen-Mapping aus der zentralen Single Source of Truth
            # (analyzers/stock_relations.THEMES) – kein dupliziertes Cluster-Dict
            # mehr, damit Bot-Netz und Dashboard nicht auseinanderdriften.
            from analyzers.stock_relations import THEMES as _CENTRAL_THEMES
            _DASH_THEMES = {_th: list(_tks) for _th, _tks in _CENTRAL_THEMES.items()}
            # Dashboard-only Ergänzungen (rein visuelle Cluster, die der Bot
            # nicht zur Kandidaten-Expansion braucht):
            _DASH_THEMES.setdefault("ECOMMERCE_CONSUMER", []).extend(
                ["NKE","LULU","TJX","ULTA","MCD","EL","KMB","PG","CL",
                 "DIS","KO","SNAP","MO","MDLZ"]
            )
            _DASH_THEMES.setdefault("BIOTECH_HEALTH", []).extend(
                ["BIIB","ILMN","MDT","ELV","MCK"]
            )
            _DASH_T2T: dict = {}
            for _th, _tks in _DASH_THEMES.items():
                for _tk in _tks:
                    _DASH_T2T.setdefault(_tk, []).append(_th)
            _get_themes = lambda t: _DASH_T2T.get(t.upper(), [])
            # Catch-all: unklassifizierte Ticker -> Sonstige
            for _ct in list(_nodes.keys()):
                if not _DASH_T2T.get(_ct.upper(), []):
                    _DASH_T2T[_ct.upper()] = ["SONSTIGE"]
            for from_t in list(_nodes.keys()):
                for to_t in _get_related(from_t):
                    if to_t not in _nodes:
                        continue
                    key = tuple(sorted([from_t, to_t]))
                    if key in _edge_seen:
                        continue
                    _edge_seen.add(key)
                    _themes = _get_themes(from_t)
                    _lbl = _themes[0].replace("_", " ") if _themes else "Verwandt"
                    _edges.append((from_t, to_t))
                    _edge_labels.append(_lbl[:60])

            # Isolierte Knoten herausfiltern wenn gewünscht
            if not _show_isolated:
                _connected = {t for edge in _edges for t in edge}
                _nodes = {t: v for t, v in _nodes.items() if t in _connected}

            if not _nodes:
                st.info("Keine Daten für die gewählten Filter.")
            else:
                # ── Themen-basiertes Layout ──────────────────────────────────────
                _ticker_list = list(_nodes.keys())
                _pos: dict = {}

                # Jedes Thema bekommt einen festen Sektor auf dem Canvas
                _theme_centers = {
                    # ── Rüstung & Industrie (enger Block, oben links) ─────────
                    "DEFENSE_EU":         (-0.85,  0.60),
                    "DEFENSE_US":         (-0.58,  0.72),
                    "INDUSTRIALS":        (-0.60,  0.38),
                    "EU_INDUSTRIAL":      (-0.85,  0.26),
                    # ── Energie & Rohstoffe (enger Block, unten links) ────────
                    "OIL_GAS":            (-0.68, -0.42),
                    "SAFE_HAVEN":         (-0.90, -0.22),
                    "MINING_METALS":      (-0.82, -0.55),
                    "CLEAN_ENERGY":       (-0.46, -0.54),
                    # ── Gesundheit (oben Mitte, eng) ──────────────────────────
                    "BIOTECH_HEALTH":     (-0.15,  0.68),
                    "GLP1_OBESITY":       ( 0.10,  0.85),
                    # ── Finanzen (enger Block, Mitte) ─────────────────────────
                    "REAL_ESTATE":        (-0.08, -0.28),
                    "FINANCIALS":         (-0.05,  0.05),
                    "PAYMENTS_FINTECH":   ( 0.22,  0.05),
                    "CRYPTO_PROXY":       ( 0.72, -0.10),
                    # ── KI / Tech (enger Block, oben rechts) ─────────────────
                    "AI_CHIPS":           ( 0.60,  0.75),
                    "AI_HYPERSCALER":     ( 0.40,  0.55),
                    "DATA_CENTER_POWER":  ( 0.72,  0.60),
                    "SEMICONDUCTORS":     ( 0.68,  0.40),
                    # ── Software (rechts Mitte) ───────────────────────────────
                    "AI_SOFTWARE":        ( 0.60,  0.18),
                    "ENTERPRISE_SOFTWARE":( 0.48, -0.18),
                    # ── Konsum / E-Auto (enger Block, unten rechts) ───────────
                    "ECOMMERCE_CONSUMER": ( 0.40, -0.60),
                    "EV_AUTO":            ( 0.65, -0.50),
                }
                # Auto-Platzierung: zentral neu hinzugefügte Themen ohne handgesetztes
                # Zentrum landen deterministisch auf einem Außenring – so verschwindet
                # nie wieder ein Thema still, nur weil die Layout-Map nicht gepflegt wurde.
                import math as _math_ac
                _missing_themes = [th for th in _CENTRAL_THEMES if th not in _theme_centers]
                # Oberer Bogen (27°–153°): hält den unteren Rand für die
                # "Sonstige"-Gruppe frei und vermeidet Kollisionen mit dem Band.
                for _i, _th in enumerate(sorted(_missing_themes)):
                    _frac = _i / max(len(_missing_themes) - 1, 1)
                    _ang  = _math_ac.pi * (0.15 + 0.70 * _frac)
                    _theme_centers[_th] = (1.12 * _math_ac.cos(_ang), 1.12 * _math_ac.sin(_ang))
                # Primär-Thema: erster Eintrag aus get_themes() → bestimmt den Cluster
                # Mehrfachthemen landen im ersten (wichtigsten) Cluster, nicht im Durchschnitt
                _theme_to_tickers: dict = {}
                _no_theme: list = []
                for t in _ticker_list:
                    _primary = next(
                        (th for th in _get_themes(t) if th in _theme_centers), None
                    )
                    if _primary:
                        _theme_to_tickers.setdefault(_primary, []).append(t)
                    else:
                        _no_theme.append(t)

                # Knoten eines Clusters kreisförmig um den Mittelpunkt verteilen
                for _theme, _members in _theme_to_tickers.items():
                    _cx, _cy = _theme_centers[_theme]
                    _n = len(_members)
                    # Radius wächst mit Anzahl der Knoten (min 0.07, max 0.13)
                    _r = min(0.07 + 0.009 * _n, 0.13)
                    for _i, t in enumerate(sorted(_members)):  # sortiert = deterministisch
                        _angle = 2 * math.pi * _i / _n - math.pi / 2
                        _pos[t] = (_cx + _r * math.cos(_angle), _cy + _r * math.sin(_angle))

                # Ticker ohne Sektor: kompakte, beschriftete "Sonstige"-Gruppe als
                # Raster-Band am unteren Rand – zusammengefasst statt rund um die
                # Karte verstreut. Fängt auch künftige (z. B. ausländische) Ticker
                # automatisch auf, ohne dass die Mapping-Tabelle gepflegt werden muss.
                _sonstige_set = set(_no_theme)
                _n_nt = len(_no_theme)
                if _n_nt:
                    _sb_cols = max(1, min(16, math.ceil(math.sqrt(_n_nt * 3.5))))
                    _sb_rows = math.ceil(_n_nt / _sb_cols)
                    _sb_x0, _sb_x1 = -1.16,  1.16     # Bandbreite
                    _sb_yt, _sb_yb = -0.82, -1.07     # oben → unten
                    for _i, t in enumerate(sorted(_no_theme)):
                        _row, _col = divmod(_i, _sb_cols)
                        # Anzahl in dieser Reihe (letzte Reihe ggf. unvoll) → zentriert
                        _in_row = _sb_cols if _row < _sb_rows - 1 else (_n_nt - _sb_cols * (_sb_rows - 1))
                        _fx = (_col + 0.5) / _in_row
                        _fy = _row / max(_sb_rows - 1, 1)
                        _pos[t] = (_sb_x0 + _fx * (_sb_x1 - _sb_x0),
                                   _sb_yt + _fy * (_sb_yb - _sb_yt))

                _n_themed = len(_ticker_list) - _n_nt
                st.caption(
                    f"Sektoren erkannt: {len(_theme_to_tickers)} | "
                    f"Ticker mit Sektor: {_n_themed} | "
                    f"Sonstige (ohne Sektor): {_n_nt}"
                )

                # ── Theme-Farben & deutsche Labels ─────────────────────────
                # Zentral aus analyzers/stock_relations (Single Source of Truth);
                # keine lokalen Kopien mehr → kein Drift bei neuen Themen.
                from analyzers.stock_relations import (
                    THEME_COLORS as _theme_colors,
                    THEME_LABELS_DE as _theme_labels_de,
                )
                # "Sonstige" ist ein reines Anzeige-Cluster (kein Bot-Thema) –
                # Farbe & Label nur hier, damit Rand und Band-Label stimmen.
                _theme_colors    = {**_theme_colors,    "SONSTIGE": "#7c8aa0"}
                _theme_labels_de = {**_theme_labels_de, "SONSTIGE": "Sonstige"}

                # ── Hintergrund-Zonen: Polygon-Kreise (fill="toself" funktioniert immer) ──
                import math as _math
                _N_PTS   = 48                    # Punkte pro Kreis-Polygon
                _angles  = [2 * _math.pi * i / _N_PTS for i in range(_N_PTS + 1)]

                # ── Sektor-zu-Sektor Verbindungen (wirtschaftliche Abhängigkeiten) ──
                _sector_links = [
                    # Rüstung ↔ Industrie
                    ("DEFENSE_US",  "INDUSTRIALS"),
                    ("DEFENSE_EU",  "EU_INDUSTRIAL"),
                    ("INDUSTRIALS", "EU_INDUSTRIAL"),
                    ("DEFENSE_US",  "DEFENSE_EU"),
                    # Tech-Ökosystem
                    ("AI_CHIPS",       "AI_HYPERSCALER"),
                    ("AI_CHIPS",       "SEMICONDUCTORS"),
                    ("AI_CHIPS",       "DATA_CENTER_POWER"),
                    ("AI_HYPERSCALER", "AI_SOFTWARE"),
                    ("AI_HYPERSCALER", "DATA_CENTER_POWER"),
                    ("AI_SOFTWARE",    "ENTERPRISE_SOFTWARE"),
                    # Finanzen-Block
                    ("FINANCIALS",     "PAYMENTS_FINTECH"),
                    ("FINANCIALS",     "REAL_ESTATE"),
                    ("PAYMENTS_FINTECH","CRYPTO_PROXY"),
                    # Pharma
                    ("BIOTECH_HEALTH", "GLP1_OBESITY"),
                    # Energie
                    ("OIL_GAS",        "CLEAN_ENERGY"),
                    ("MINING_METALS",  "CLEAN_ENERGY"),
                    ("OIL_GAS",        "EU_INDUSTRIAL"),
                    # Konsum & Mobilität
                    ("ENTERPRISE_SOFTWARE", "ECOMMERCE_CONSUMER"),
                    ("EV_AUTO",        "ECOMMERCE_CONSUMER"),
                    ("AI_CHIPS",       "EV_AUTO"),
                    ("SEMICONDUCTORS", "EV_AUTO"),
                    ("MINING_METALS",  "EV_AUTO"),     # Batterierohstoffe
                    # Cross-Sektor
                    ("INDUSTRIALS",    "CLEAN_ENERGY"),
                    ("FINANCIALS",     "ENTERPRISE_SOFTWARE"),
                ]
                _sl_x, _sl_y = [], []
                for _t1, _t2 in _sector_links:
                    if _t1 not in _theme_centers or _t2 not in _theme_centers: continue
                    if not _theme_to_tickers.get(_t1) or not _theme_to_tickers.get(_t2): continue
                    _x1, _y1 = _theme_centers[_t1]
                    _x2, _y2 = _theme_centers[_t2]
                    _ddx, _ddy = _x2 - _x1, _y2 - _y1
                    _dd = _math.sqrt(_ddx**2 + _ddy**2) or 1e-6
                    # Kantenpunkte (Linie beginnt/endet am Kreisrand, nicht im Zentrum)
                    _rr1 = min(0.09 + 0.012 * len(_theme_to_tickers.get(_t1, [])), 0.18)
                    _rr2 = min(0.09 + 0.012 * len(_theme_to_tickers.get(_t2, [])), 0.18)
                    _sl_x += [_x1 + _rr1*_ddx/_dd, _x2 - _rr2*_ddx/_dd, None]
                    _sl_y += [_y1 + _rr1*_ddy/_dd, _y2 - _rr2*_ddy/_dd, None]

                _sector_link_trace = go.Scatter(
                    x=_sl_x, y=_sl_y,
                    mode="lines",
                    line=dict(width=1.0, color="rgba(120,140,180,0.30)"),
                    hoverinfo="none", showlegend=False,
                )

                _zone_traces  = []   # je ein Trace pro Cluster (Kreis + Label)
                _label_x2, _label_y2, _label_txt2, _label_col2 = [], [], [], []
                _label_nx2, _label_ny2 = [], []   # Richtungsvektor für xanchor/yanchor
                _label_ax2,  _label_ay2  = [], []   # Pfeilankerpunkt auf dem Kreisrand

                for _theme, (zx, zy) in _theme_centers.items():
                    _in_zone = _theme_to_tickers.get(_theme, [])
                    if not _in_zone:
                        continue
                    _n_zone = len(_in_zone)
                    _r_zone = min(0.09 + 0.012 * _n_zone, 0.18)
                    _zc = _theme_colors.get(_theme, "#666666")
                    _zl = _theme_labels_de.get(_theme, _theme)

                    # Polygon-Koordinaten des Kreises (geschlossen: erster == letzter Punkt)
                    _px = [zx + _r_zone * _math.cos(a) for a in _angles]
                    _py = [zy + _r_zone * _math.sin(a) for a in _angles]

                    # Hex → rgba für Fill und Linie
                    _r8, _g8, _b8 = int(_zc[1:3], 16), int(_zc[3:5], 16), int(_zc[5:7], 16)
                    _fill_rgba = f"rgba({_r8},{_g8},{_b8},0.18)"
                    _line_rgba = f"rgba({_r8},{_g8},{_b8},0.70)"

                    _zone_traces.append(go.Scatter(
                        x=_px, y=_py,
                        mode="lines",
                        fill="toself",
                        fillcolor=_fill_rgba,
                        line=dict(color=_line_rgba, width=1.5, dash="dot"),
                        hoverinfo="none",
                        showlegend=False,
                    ))

                    # Label-Richtung: radial nach außen, mit Overrides für problematische Cluster
                    _dir_overrides = {
                        "FINANCIALS":         (-1.0,  0.0),  # → links (war unten, deckte REAL_ESTATE)
                        "REAL_ESTATE":        ( 0.0, -1.0),
                        "INDUSTRIALS":        (-1.0,  0.0),
                        "EU_INDUSTRIAL":      (-1.0,  0.0),
                        "GLP1_OBESITY":       ( 0.0,  1.0),
                        "BIOTECH_HEALTH":     ( 0.0,  1.0),
                        "PAYMENTS_FINTECH":   ( 0.0,  1.0),
                        "AI_HYPERSCALER":     ( 0.0,  1.0),  # → oben (war oben-rechts, deckte KI-Chips)
                        "ENTERPRISE_SOFTWARE":( 1.0,  0.0),
                        # Untere Cluster: Labels seitlich, damit sie nicht ins
                        # "Sonstige"-Band am unteren Rand hineinragen.
                        "ECOMMERCE_CONSUMER": ( 1.0,  0.0),
                        "EV_AUTO":            ( 1.0,  0.0),
                        "MINING_METALS":      (-1.0,  0.0),
                        "OIL_GAS":            (-1.0,  0.0),
                        "CLEAN_ENERGY":       (-1.0,  0.0),
                    }
                    if _theme in _dir_overrides:
                        _nx, _ny = _dir_overrides[_theme]
                    else:
                        _dist = _math.sqrt(zx ** 2 + zy ** 2)
                        _nx, _ny = (zx / _dist, zy / _dist) if _dist > 0.05 else (0.0, -1.0)
                    # Label weit genug außerhalb des Kreises damit keine Knoten überdeckt werden
                    _lx = zx + (_r_zone + 0.18) * _nx
                    _ly = zy + (_r_zone + 0.18) * _ny
                    _label_x2.append(_lx)
                    _label_y2.append(_ly)
                    _label_txt2.append(_zl)
                    _label_col2.append(_zc)
                    _label_nx2.append(_nx)
                    _label_ny2.append(_ny)
                    # Ankerpunkt auf dem Kreisrand (Pfeilursprung)
                    _label_ax2.append(zx + _r_zone * _nx)
                    _label_ay2.append(zy + _r_zone * _ny)

                # Labels als Annotationen (kein Pfeil — separate Linie unten)
                _zone_annotations = []
                _conn_x, _conn_y, _conn_colors = [], [], []
                for lx, ly, lt, lc, lnx, lny, lax, lay in zip(
                    _label_x2, _label_y2, _label_txt2, _label_col2,
                    _label_nx2, _label_ny2, _label_ax2, _label_ay2,
                ):
                    _r8i = int(lc[1:3], 16)
                    _g8i = int(lc[3:5], 16)
                    _b8i = int(lc[5:7], 16)
                    _xanc = "left"   if lnx >  0.20 else ("right"  if lnx < -0.20 else "center")
                    _yanc = "bottom" if lny >  0.20 else ("top"    if lny < -0.20 else "middle")
                    _zone_annotations.append(dict(
                        x=lx, y=ly,
                        text=f"<b>{lt}</b>",
                        showarrow=False,
                        xanchor=_xanc,
                        yanchor=_yanc,
                        xref="x", yref="y",
                        font=dict(size=10, color="#ffffff"),
                        bgcolor=f"rgba({_r8i},{_g8i},{_b8i},0.85)",
                        bordercolor=lc,
                        borderwidth=1,
                        borderpad=4,
                        opacity=0.95,
                    ))
                    # Verbindungslinie Kreisrand → Label (ein Segment pro Cluster)
                    _conn_x += [lax, lx, None]
                    _conn_y += [lay, ly, None]
                    _conn_colors.append(lc)

                # ── "Sonstige"-Band: Hintergrund-Rechteck + Gruppen-Label ──
                if _sonstige_set:
                    _sc  = _theme_colors["SONSTIGE"]
                    _sr8, _sg8, _sb8 = int(_sc[1:3], 16), int(_sc[3:5], 16), int(_sc[5:7], 16)
                    _zone_traces.append(go.Scatter(
                        x=[-1.22, 1.22, 1.22, -1.22, -1.22],
                        y=[-0.78, -0.78, -1.10, -1.10, -0.78],
                        mode="lines",
                        fill="toself",
                        fillcolor=f"rgba({_sr8},{_sg8},{_sb8},0.10)",
                        line=dict(color=f"rgba({_sr8},{_sg8},{_sb8},0.55)", width=1.2, dash="dot"),
                        hoverinfo="none", showlegend=False,
                    ))
                    _zone_annotations.append(dict(
                        x=-1.20, y=-0.745,
                        text=f"<b>Sonstige · {len(_sonstige_set)}</b>",
                        showarrow=False,
                        xanchor="left", yanchor="bottom",
                        xref="x", yref="y",
                        font=dict(size=10, color="#ffffff"),
                        bgcolor=f"rgba({_sr8},{_sg8},{_sb8},0.85)",
                        bordercolor=_sc, borderwidth=1, borderpad=4, opacity=0.95,
                    ))

                # Alle Verbindungslinien als ein Trace (gleiche Farbe geht nicht pro Segment,
                # deshalb hellgrau — Label-Farbe identifiziert den Cluster bereits)
                _conn_trace = go.Scatter(
                    x=_conn_x, y=_conn_y,
                    mode="lines",
                    line=dict(width=1.2, color="rgba(180,180,180,0.45)"),
                    hoverinfo="none", showlegend=False,
                )

                # ── Kanten (nur wenn Toggle aktiv) ─────────────────────────
                _edge_x, _edge_y = [], []
                if _show_edges:
                    for (src, dst) in _edges:
                        if src in _pos and dst in _pos:
                            x0, y0 = _pos[src]
                            x1, y1 = _pos[dst]
                            _edge_x += [x0, x1, None]
                            _edge_y += [y0, y1, None]

                _edge_trace = go.Scatter(
                    x=_edge_x, y=_edge_y,
                    mode="lines",
                    line=dict(width=0.8, color="#445566"),
                    hoverinfo="none", showlegend=False,
                )

                # ── Knoten ─────────────────────────────────────────────────
                _conn_count  = {t: sum(1 for e in _edges if t in e) for t in _ticker_list}
                _node_x      = [_pos[t][0] for t in _ticker_list]
                _node_y      = [_pos[t][1] for t in _ticker_list]
                _node_colors = [_nodes[t]["color"] for t in _ticker_list]
                # Sonstige-Knoten kleiner & einheitlich → ruhiges, dichtes Band
                _node_sizes  = [
                    10 if t in _sonstige_set else 16 + 6 * _conn_count[t]
                    for t in _ticker_list
                ]

                # Knotenrand in Themenfarbe → sofort sichtbare Sektor-Zugehörigkeit
                _node_borders = [
                    _theme_colors.get((_get_themes(t) or [""])[0], "#333333")
                    for t in _ticker_list
                ]

                _node_hover = [
                    (
                        f"<b>{t}</b>  {_ALL_NAMES.get(t.upper(), '')}<br>"
                        f"Empfehlung: <b>{_nodes[t]['rec']}</b>"
                        + (" ⚠️ veraltet" if _nodes[t].get("stale") else "") +
                        f"  <i>({_nodes[t].get('source','–')})</i><br>"
                        f"Score: {_nodes[t]['score']}  |  "
                        f"Zuletzt: {_nodes[t]['date']}<br>"
                        f"Sektor: {', '.join(_get_themes(t)) or '–'}<br>"
                        f"Verbindungen: {_conn_count[t]}"
                    )
                    for t in _ticker_list
                ]

                # Label: Kürzel + 1. Wort des Firmennamens
                # (im Sonstige-Band nur das Kürzel → weniger Gedränge)
                _node_labels = []
                for t in _ticker_list:
                    if t in _sonstige_set:
                        _node_labels.append(t)
                        continue
                    _nm = _ALL_NAMES.get(t.upper(), "")
                    _short = _nm.split()[0][:9] if _nm else ""
                    _node_labels.append(f"{t} · {_short}" if _short else t)

                _node_trace = go.Scatter(
                    x=_node_x, y=_node_y,
                    mode="markers+text",
                    hoverinfo="text",
                    hovertext=_node_hover,
                    text=_node_labels,
                    textposition="top center",
                    textfont=dict(size=8, color="#cccccc"),
                    marker=dict(
                        size=_node_sizes,
                        color=_node_colors,
                        line=dict(width=2, color=_node_borders),
                    ),
                    showlegend=False,
                )

                # ── Legende ────────────────────────────────────────────────
                _legend_traces = [
                    go.Scatter(
                        x=[None], y=[None], mode="markers",
                        marker=dict(size=10, color=_rec_color[r]),
                        name=r, showlegend=True,
                    )
                    for r in ["BUY", "HOLD", "SELL", "SKIP"]
                ]

                fig = go.Figure(
                    data=_zone_traces + [_conn_trace, _edge_trace, _node_trace] + _legend_traces,
                    layout=go.Layout(
                        paper_bgcolor="#0e1117",
                        plot_bgcolor="#0e1117",
                        font=dict(color="#dddddd"),
                        xaxis=dict(showgrid=False, zeroline=False,
                                   showticklabels=False, range=[-1.30, 1.30]),
                        yaxis=dict(showgrid=False, zeroline=False,
                                   showticklabels=False, range=[-1.10, 1.15]),
                        hovermode="closest",
                        height=760,
                        margin=dict(l=10, r=10, t=20, b=10),
                        legend=dict(
                            bgcolor="#1a1a2e", bordercolor="#444",
                            borderwidth=1, font=dict(color="#dddddd"),
                        ),
                        annotations=_zone_annotations,
                    ),
                )
                st.plotly_chart(fig, width="stretch")

                # ── Kennzahlen unter der Map ─────────────────────────────────────
                _kpi1, _kpi2, _kpi3, _kpi4 = st.columns(4)
                _kpi1.metric("Analysierte Ticker", len(_nodes))
                _kpi2.metric("Verbindungen", len(_edges))
                _buy_cnt = sum(1 for v in _nodes.values() if v["rec"] == "BUY")
                _kpi3.metric("BUY-Signale", _buy_cnt)
                _most_connected = max(_ticker_list, key=lambda t: sum(1 for e in _edges if t in e), default="–")
                _kpi4.metric("Am stärksten vernetzt", _most_connected)

                # ── Verbindungstabelle ───────────────────────────────────────────
                if _edges:
                    st.divider()
                    st.subheader("Verbindungen")
                    _conn_rows = [
                        {"Von": src, "Nach": dst, "These": lbl}
                        for (src, dst), lbl in zip(_edges, _edge_labels)
                    ]
                    st.dataframe(
                        pd.DataFrame(_conn_rows),
                        width="stretch", hide_index=True
                    )

    except ImportError:
        st.warning("Plotly nicht installiert. Bitte auf dem Server ausführen: `pip install plotly networkx`")
    except Exception as _e:
        st.error(f"Netzwerk-Fehler: {_e}")


# ══════════════════════════════════════════════════════════
# TAB 5 – WOCHENBRIEFING
# ══════════════════════════════════════════════════════════
with tab_briefing:
    st.subheader("📰 Wochenbriefing")
    st.caption(
        "Claude analysiert jeden Samstag/Sonntag: Earnings-Kalender, Marktlage, "
        "Makro-News. Das Briefing fließt in alle Analysen der Folgewoche ein."
    )

    briefings = weekend_prep.get_latest_briefing(limit=3)
    current   = weekend_prep.get_current_briefing()

    b_col1, b_col2 = st.columns([5, 2])
    with b_col2:
        if st.button("🔄 Neues Briefing generieren", width="stretch"):
            with st.spinner("Claude bereitet Wochenbriefing vor…"):
                result = weekend_prep.run()
            if result:
                st.success("Briefing generiert!")
                st.rerun()
            else:
                st.error("Fehler beim Generieren (API-Key oder Daten fehlen).")

    with b_col1:
        if briefings:
            tabs_brief = st.tabs([f"KW {b['week_start']}" for b in briefings])
            for tab_b, brief in zip(tabs_brief, briefings):
                with tab_b:
                    generated = brief.get("generated_at", "")[:16]
                    is_current = brief.get("week_start") == (current and brief.get("week_start"))
                    if is_current:
                        st.success(f"✅ Aktuelles Briefing · Erstellt: {generated}")
                    else:
                        st.caption(f"Erstellt: {generated}")
                    st.markdown(brief["briefing"])
        else:
            st.info(
                "Noch kein Briefing vorhanden.  \n"
                "Wird automatisch am Wochenende generiert oder jetzt mit dem Button oben starten."
            )


# ══════════════════════════════════════════════════════════
# TAB 6 – TRADES & LERNEN
# ══════════════════════════════════════════════════════════
with tab_trades:
    # Learning KPIs
    st.subheader("Performance-Kennzahlen")
    if acc.get("total_closed", 0) == 0:
        if _rt_stats:
            st.caption(
                "ℹ️ Prediction-Tracking ist noch leer (füllt sich ab dem nächsten "
                "Kauf→Verkauf-Paar) — die Kennzahlen stammen direkt aus den echten "
                "Portfolio-Trades."
            )
            fk1, fk2, fk3, fk4 = st.columns(4)
            fk1.metric("Win-Rate",          f"{_rt_stats['win_rate_pct']}%",
                       f"{_rt_stats['total_closed']} Trades")
            fk2.metric("Ø Rendite / Trade", f"{_rt_stats['avg_return_pct']:+.2f}%")
            fk3.metric("Realisiert gesamt", f"${_rt_stats['total_pnl']:+,.2f}")
            fk4.metric("Richtung/Zielkurs", "–",
                       "braucht Prediction-Tracking", delta_color="off")
            _fb_sells = [t for t in reversed(portfolio.trade_history())
                         if t.action == "SELL"][:15]
            if _fb_sells:
                st.markdown("**Letzte Verkäufe (Portfolio-Historie):**")
                st.dataframe(
                    pd.DataFrame([{
                        "Datum":  t.timestamp[:10],
                        "Ticker": t.ticker,
                        "Stück":  t.shares,
                        "Kurs $": t.price,
                        "P&L $":  round(t.pnl, 2) if t.pnl is not None else None,
                        "Grund":  (t.reason or "")[:70],
                    } for t in _fb_sells]),
                    width="stretch", hide_index=True,
                )
        else:
            st.info("Noch keine abgeschlossenen Trades. Der Bot lernt nach dem ersten Verkauf.")
    else:
        lk1, lk2, lk3, lk4 = st.columns(4)
        lk1.metric("Win-Rate",              f"{acc['win_rate_pct']}%")
        lk2.metric("Richtungs-Genauigkeit", f"{acc['direction_accuracy_pct']}%")
        lk3.metric("Zielkurs-Trefferquote", f"{acc['target_hit_pct']}%")
        lk4.metric("Ø Rendite / Trade",     f"{acc['avg_return_pct']:+.2f}%")

        adaptive_threshold = tracker.get_adaptive_threshold(config.buy_threshold)
        threshold_delta    = adaptive_threshold - config.buy_threshold
        st.metric(
            "Adaptiver Kauf-Threshold",
            f"{adaptive_threshold:.2f}",
            f"{threshold_delta:+.2f} (Basis {config.buy_threshold:.2f})",
            delta_color="inverse",
        )
        if threshold_delta > 0:
            st.warning("⚠️ Bot wurde konservativer – Win-Rate unter 50%. Strengere Kaufbedingungen aktiv.")
        elif threshold_delta < 0:
            st.success("✅ Bot hat gute Trefferquote – Kaufbedingungen leicht gelockert.")

        st.divider()

        # Exit reasons + sentiment buckets in 2 columns
        ex_col, bucket_col = st.columns(2)

        with ex_col:
            st.subheader("Exit-Grund vs. P&L")
            exit_stats = tracker.get_exit_reason_stats()
            if exit_stats:
                labels_exit = {
                    "stop_loss":      "Stop-Loss",
                    "take_profit":    "Take-Profit",
                    "thesis_broken":  "⚠ These gebrochen",
                    "hold_expired":   "Haltedauer abgelaufen",
                    "sentiment_sell": "Sentiment-SELL",
                    "other":          "Sonstiges",
                }
                df_exit = pd.DataFrame([{
                    "Ausstiegsgrund": labels_exit.get(r["category"], r["category"]),
                    "Trades":         r["trades"],
                    "Ø Rendite %":    r["avg_return_pct"],
                    "Win-Rate %":     r["win_rate_pct"],
                } for r in exit_stats])
                st.dataframe(
                    df_exit.style.map(
                        lambda v: ("color: #00e676" if isinstance(v, (int, float)) and v >= 0
                                   else ("color: #f44336" if isinstance(v, (int, float)) and v < 0 else "")),
                        subset=["Ø Rendite %"],
                    ),
                    width="stretch", hide_index=True,
                )

        with bucket_col:
            st.subheader("Sentiment-Score vs. Performance")
            buckets = tracker.get_sentiment_score_buckets()
            if buckets:
                df_bkt = pd.DataFrame([{
                    "Score-Bereich": b["score_range"],
                    "Trades":        b["trades"],
                    "Win-Rate %":    b["win_rate_pct"],
                    "Ø Rendite %":   b["avg_return_pct"],
                } for b in buckets])
                st.dataframe(df_bkt, width="stretch", hide_index=True)

        # Source accuracy
        source_acc = tracker.get_source_accuracy()
        if source_acc:
            st.subheader("Quellen-Trefferquote pro Ticker")
            st.caption("Welche Nachrichtenquelle lieferte bei welchem Ticker die besten Signale?")
            df_src = pd.DataFrame(source_acc[:15]).rename(columns={
                "source": "Quelle", "ticker": "Ticker",
                "trades": "Trades", "win_rate_pct": "Win-Rate %", "avg_return_pct": "Ø Rendite %",
            })
            st.dataframe(
                df_src[["Quelle", "Ticker", "Trades", "Win-Rate %", "Ø Rendite %"]],
                width="stretch", hide_index=True,
            )

        st.divider()

        # Recent closed trades
        st.subheader("Letzte abgeschlossene Trades")
        recent = tracker.get_recent_trades(15)
        if recent:
            df_tr = pd.DataFrame(recent).rename(columns={
                "ticker": "Ticker", "entry_price": "Einstieg $", "sell_price": "Verkauf $",
                "actual_return_pct": "Rendite %", "actual_hold_days": "Tage (Ist)",
                "predicted_hold_days": "Tage (Plan)", "predicted_target_price": "Zielkurs $",
                "direction_correct": "Richtung ✓", "target_hit": "Zielkurs ✓",
                "sell_reason_category": "Exit-Typ", "sell_reason": "Grund",
            })
            # Gewinn $ aus portfolio.trade_history() – hat pnl für alle Trades (alt+neu)
            try:
                _th = portfolio.trade_history()
                # Neueste SELLs zuerst pro Ticker (passt zu recent = sell_date DESC)
                _sell_pnl: dict = {}
                for _t in reversed(_th):
                    if _t.action == "SELL":
                        _sell_pnl.setdefault(_t.ticker, []).append(round(_t.pnl, 2))
                _raw2 = pd.DataFrame(recent)
                def _get_pnl(row):
                    lst = _sell_pnl.get(row["ticker"], [])
                    return lst.pop(0) if lst else None
                df_tr["Gewinn $"] = _raw2.apply(_get_pnl, axis=1)
            except Exception:
                df_tr["Gewinn $"] = None
            for col in ["Richtung ✓", "Zielkurs ✓"]:
                if col in df_tr.columns:
                    df_tr[col] = df_tr[col].apply(lambda v: "✓" if v == 1 else "✗")
            desired = ["Ticker", "Einstieg $", "Verkauf $", "Gewinn $", "Rendite %", "Tage (Ist)",
                       "Tage (Plan)", "Zielkurs $", "Richtung ✓", "Zielkurs ✓", "Exit-Typ", "Grund"]
            existing = [c for c in desired if c in df_tr.columns]
            st.dataframe(
                df_tr[existing].style.map(
                    lambda v: ("color: #00e676" if isinstance(v, (int, float)) and v >= 0
                               else ("color: #f44336" if isinstance(v, (int, float)) and v < 0 else "")),
                    subset=[c for c in ["Rendite %", "Gewinn $"] if c in existing],
                ),
                width="stretch", hide_index=True,
            )
            # Full export: all closed trades
            _all_closed = tracker.get_recent_trades(500)
            if _all_closed:
                _df_export = pd.DataFrame(_all_closed)
                st.download_button(
                    "📥 Alle Trades als CSV exportieren",
                    _df_export.to_csv(index=False).encode("utf-8"),
                    f"closed_trades_{datetime.now().strftime('%Y%m%d')}.csv",
                    "text/csv",
                    width="stretch",
                )

    st.divider()

    # Trade Journal
    st.subheader("📖 Trade-Tagebuch")
    st.caption("Vollständige Entscheidungshistorie: Warum gekauft, wie überwacht, warum verkauft.")
    journal_stories = journal.get_all_trade_summaries(limit=30)
    if not journal_stories:
        st.info("Noch keine Trades.")
    else:
        tickers_all = sorted({s["ticker"] for s in journal_stories})
        j_col1, j_col2 = st.columns([2, 6])
        with j_col1:
            selected_j = st.selectbox("Ticker", ["Alle"] + tickers_all, key="jfilter")
        filtered = journal_stories if selected_j == "Alle" else [
            s for s in journal_stories if s["ticker"] == selected_j
        ]
        for s in filtered[:15]:
            is_open = s.get("is_open")
            pnl     = s.get("pnl") or 0
            icon    = "🟢" if is_open else ("🟩" if pnl >= 0 else "🔴")
            pnl_str = (f"OFFEN seit {s.get('entry_date','?')[:10]}" if is_open
                       else f"P&L {pnl:+.2f} USD ({s.get('pnl_pct',0):+.1f}%)")
            with st.expander(f"{icon} **{s['ticker']}** · {pnl_str}"):
                jc1, jc2 = st.columns(2)
                with jc1:
                    st.markdown("**Einstieg**")
                    st.write(f"📅 {s.get('entry_date','?')[:10]} · ${s.get('entry_price',0):.2f}")
                    st.write(f"🧠 Sentiment: {s.get('entry_sentiment',0):.2f}")
                    st.write(f"⏱️ Geplant: {s.get('planned_hold_days','?')}d")
                    if s.get("target_price"):
                        st.write(f"🎯 Zielkurs: ${s['target_price']:.2f}")
                    st.info(s.get("entry_rationale") or "–")
                    if s.get("catalysts"):
                        st.markdown("**Katalysatoren:** " + ", ".join(s["catalysts"]))
                    if s.get("risks"):
                        st.markdown("**Risiken:** " + ", ".join(s["risks"]))
                with jc2:
                    st.metric("Tagesprüfungen", s.get("n_daily_checks", 0))
                    st.metric("Warnungen",       s.get("n_warnings", 0))
                    if not is_open:
                        st.markdown("**Verkauf**")
                        st.write(f"📅 {s.get('exit_date','?')[:10]} · ${s.get('exit_price',0):.2f}")
                        st.write(f"⏱️ Tatsächl.: {s.get('actual_hold_days','?')}d")
                        c = "green" if pnl >= 0 else "red"
                        st.markdown(f":{c}[{pnl:+.2f} USD ({s.get('pnl_pct',0):+.1f}%)]")
                        st.warning(f"**Grund:** {s.get('exit_reason','–')}")
                with st.expander("🔍 Event-Zeitleiste"):
                    for ev in s["events"]:
                        icon_ev = {"ENTRY":"🟢","DAILY_CHECK":"👁","WARNING":"⚠️","EXIT":"🔚"}.get(ev["event_type"],"•")
                        st.text(
                            f"{icon_ev} {ev['event_date'][:16]}  {ev['event_type']:14}  "
                            f"${ev.get('price',0):.2f}  "
                            f"{(ev.get('rationale') or ev.get('reason') or '')[:100]}"
                        )

    st.divider()

    # Monthly self-assessment
    st.subheader("📋 Monatliche Selbsteinschätzung")
    reviews = reflection.get_monthly_reviews(limit=12)
    mr_col1, mr_col2 = st.columns([5, 2])
    with mr_col2:
        if st.button("🔄 Jetzt generieren", width="stretch"):
            with st.spinner("Claude reflektiert…"):
                new_content = reflection.generate_monthly_review()
            if new_content:
                st.success("Neue Einschätzung generiert.")
                st.rerun()
            else:
                st.warning("Nicht genug Trades oder API-Fehler.")
    with mr_col1:
        if reviews:
            tabs_rev = st.tabs([r["period"] or "Aktuell" for r in reviews])
            for tab_r, review in zip(tabs_rev, reviews):
                with tab_r:
                    st.caption(f"Erstellt: {review['created_at'][:16]} · {review['trades_used']} Trades")
                    st.markdown(review["content"])
        else:
            st.info("Wird am 1. des Monats automatisch generiert oder über `--reflect` manuell.")

    memo = reflection.get_active_memo()
    if memo:
        with st.expander("📚 Aktives Lessons-Learned-Memo"):
            st.info(memo)


# ══════════════════════════════════════════════════════════
# TAB 7 – TECHNICALS
# ══════════════════════════════════════════════════════════
with tab_tech:
    st.subheader("Technische Indikatoren – Watchlist")
    st.caption("RSI, MACD, Bollinger Bands, EMAs und ATR für alle beobachteten Aktien.")

    _ti = TechnicalIndicators()

    selected_ticker = st.selectbox(
        "Ticker auswählen",
        options=config.watchlist,
        format_func=ticker_label,
        key="tech_ticker_select",
    )

    with st.spinner(f"Berechne Indikatoren für {selected_ticker}…"):
        snap = _ti.calculate(selected_ticker)

    if snap is None:
        st.warning(f"Keine Kursdaten für {selected_ticker} verfügbar.")
    else:
        # Trend badge
        trend_color = {"UPTREND": "green", "DOWNTREND": "red"}.get(snap.trend.split()[0], "gray")
        st.markdown(
            f"**Trend:** <span style='color:{trend_color};font-weight:700;'>{snap.trend}</span>  "
            f"&nbsp;&nbsp;**Kurs:** ${snap.price}",
            unsafe_allow_html=True,
        )
        st.divider()

        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown("#### Momentum")
            if snap.rsi_14 is not None:
                rsi_color = "red" if snap.rsi_14 > 70 else ("green" if snap.rsi_14 < 30 else "white")
                rsi_label = "Überkauft" if snap.rsi_14 > 70 else ("Überverkauft" if snap.rsi_14 < 30 else "Neutral")
                st.metric("RSI (14)", f"{snap.rsi_14:.1f}", rsi_label)
            if snap.macd is not None:
                hist_delta = f"{snap.macd_hist:+.4f}" if snap.macd_hist is not None else "–"
                st.metric("MACD", f"{snap.macd:.4f}", f"Hist: {hist_delta}")
                st.caption(f"Signal: {snap.macd_signal:.4f}")

        with col2:
            st.markdown("#### Volatilität")
            if snap.bb_upper is not None:
                st.metric("BB Oben", f"${snap.bb_upper:.2f}")
                st.metric("BB Mitte", f"${snap.bb_middle:.2f}")
                st.metric("BB Unten", f"${snap.bb_lower:.2f}")
                if snap.bb_pct is not None:
                    pct_label = "oben" if snap.bb_pct > 0.8 else ("unten" if snap.bb_pct < 0.2 else "mittig")
                    st.caption(f"%B = {snap.bb_pct:.2f} ({pct_label})")
            if snap.atr_14 is not None and snap.price:
                atr_pct = snap.atr_14 / snap.price * 100
                st.metric("ATR (14)", f"${snap.atr_14:.2f}", f"{atr_pct:.1f}% des Kurses")

        with col3:
            st.markdown("#### Trend / EMAs")
            if snap.ema_9:
                st.metric("EMA 9", f"${snap.ema_9:.2f}")
            if snap.ema_21:
                st.metric("EMA 21", f"${snap.ema_21:.2f}")
            if snap.ema_50:
                st.metric("EMA 50", f"${snap.ema_50:.2f}")
            if snap.volume_ratio is not None:
                vol_label = "hoch" if snap.volume_ratio > 1.5 else ("niedrig" if snap.volume_ratio < 0.5 else "normal")
                st.metric("Volumen-Ratio", f"{snap.volume_ratio:.2f}×", vol_label)

        st.divider()
        st.markdown("**Rohdaten (alle Ticker)**")
        all_snaps = []
        for t in config.watchlist:
            try:
                s = _ti.calculate(t)
                if s:
                    all_snaps.append({
                        "Ticker": t,
                        "Kurs": s.price,
                        "RSI": s.rsi_14,
                        "MACD-Hist": s.macd_hist,
                        "%B": s.bb_pct,
                        "EMA9": s.ema_9,
                        "EMA21": s.ema_21,
                        "VolRatio": s.volume_ratio,
                        "Trend": s.trend,
                    })
            except Exception:
                pass
        if all_snaps:
            df_tech = pd.DataFrame(all_snaps).set_index("Ticker")
            st.dataframe(df_tech, width="stretch")


# ══════════════════════════════════════════════════════════
# TAB 8 – DYNAMISCHE WATCHLIST
# ══════════════════════════════════════════════════════════
with tab_watchlist:
    # ── IPO-Pipeline ─────────────────────────────────────────────────────────
    st.subheader("🚀 IPO-Pipeline – Demnächst an der Börse")
    st.caption(
        "Nur Unternehmen mit Bewertung ≥ $10 Mrd. werden verfolgt. "
        "Ab $25 Mrd. werden sie bei IPO-Erkennung automatisch zur Analyse-Queue hinzugefügt."
    )

    try:
        from analyzers.ipo_tracker import IPOTracker as _IPOTracker, CANDIDATES as _IPO_CANDS
        _ipo = _IPOTracker()
        _pipeline = _ipo.get_pipeline()
        if _pipeline:
            _ipo_rows = []
            for _c in _pipeline:
                _hype = _c["hype_score"]
                _hype_str = f"{_hype:.0%}" if _hype is not None else "–"
                _hype_icon = (
                    "🟢" if (_hype or 0) >= 0.6
                    else "🟡" if (_hype or 0) >= 0.4
                    else "🔴" if _hype is not None
                    else "⚪"
                )
                _status = (
                    f"✅ LIVE ({_c['live_ticker']})"
                    if _c["is_live"]
                    else "⏳ Pre-IPO"
                )
                _ipo_rows.append({
                    "Status":         _status,
                    "Unternehmen":    _c["name"],
                    "Sektor":         _c["sector"],
                    "Bew. ($Mrd.)":   _c["valuation_b"],
                    "Hype-Score":     f"{_hype_icon} {_hype_str}",
                    "Artikel/Woche":  _c["articles_7d"],
                    "Auto-Watchlist": "✅" if _c["auto_eligible"] else "❌",
                    "Zuletzt geprüft": _c["last_checked"],
                    "Info":           _c["notes"],
                })
            st.dataframe(pd.DataFrame(_ipo_rows), width="stretch", hide_index=True)

            # Detail-Expander mit Headlines
            _live_cands  = [c for c in _pipeline if c["is_live"]]
            _pre_cands   = [c for c in _pipeline if not c["is_live"] and c["articles_7d"] > 0]
            if _live_cands:
                st.success(
                    "🎉 **Neue Börsengänge erkannt:** "
                    + ", ".join(f"{c['name']} ({c['live_ticker']})" for c in _live_cands)
                )
            for _c in _pre_cands[:4]:
                with st.expander(f"📰 Headlines – {_c['name']} (letzte 7 Tage)"):
                    for _h in _c["headlines"]:
                        st.markdown(f"• {_h}")
                    if not _c["headlines"]:
                        st.caption("Noch keine Artikel gefunden.")
        else:
            st.info("Noch keine IPO-Daten. Wird täglich um 06:00 UTC aktualisiert.")
    except Exception as _ipo_err:
        st.caption(f"IPO-Tracker nicht verfügbar: {_ipo_err}")

    st.divider()

    st.subheader("🔭 Dynamische Watchlist")
    st.caption(
        "Der Bot scannt täglich ~80 Aktien und wählt automatisch die vielversprechendsten aus. "
        "Scoring: Volumen (30%) + Momentum (25%) + RSI (25%) + MACD (20%)"
    )

    _dw = DynamicWatchlist(max_picks=config.scan_max_picks or 12)

    wl_col1, wl_col2 = st.columns([4, 1])
    with wl_col2:
        if st.button("🔄 Jetzt neu scannen", width="stretch"):
            with st.spinner("Scanne Markt-Universum…"):
                active = list(portfolio.all_positions().keys())
                new_wl = _dw.force_refresh(active_tickers=active)
            st.success(f"Neue Watchlist: {', '.join(new_wl)}")
            st.rerun()

    with wl_col1:
        if not config.auto_scan_watchlist:
            st.warning(
                "Dynamische Watchlist ist deaktiviert. "
                "Setze `AUTO_SCAN_WATCHLIST=true` in der `.env` Datei."
            )
        else:
            cached = _dw._load_cache()
            if cached:
                age_h = _dw._cache_age_hours(cached)
                updated = cached.get("updated_at", "–")[:16]
                st.info(
                    f"Letzte Aktualisierung: **{updated} UTC** "
                    f"(vor {age_h:.1f}h) · Nächste in {max(0, 24 - age_h):.1f}h"
                )
                current_wl = cached["tickers"]
                st.markdown("**Aktuelle Watchlist:**")
                wl_badges = "  ".join(
                    f"`{ticker_label(t)}`" for t in current_wl
                )
                st.markdown(wl_badges)
            else:
                st.info("Noch kein Scan durchgeführt. Klicke 'Jetzt neu scannen'.")

    st.divider()

    # Scored candidates table
    st.subheader("Alle bewerteten Kandidaten")
    st.caption("Vollständige Rangliste aller gescannten Aktien mit Einzelscores.")
    with st.spinner("Lade Kandidaten-Scores…"):
        candidates = _dw.get_scored_candidates()

    if candidates:
        df_wl = pd.DataFrame(candidates)
        df_wl.insert(1, "Name", df_wl["ticker"].apply(lambda t: _ALL_NAMES.get(t.upper(), "")))
        df_wl = df_wl.rename(columns={
            "ticker":       "Ticker",
            "total_score":  "Gesamt-Score",
            "price":        "Kurs $",
            "vol_ratio":    "Vol-Ratio",
            "momentum_20d": "Momentum 20d %",
            "rsi":          "RSI",
            "macd_hist":    "MACD-Hist",
            "vol_score":    "Score Volumen",
            "mom_score":    "Score Momentum",
            "rsi_score":    "Score RSI",
            "macd_score":   "Score MACD",
        })
        # Highlight top 12 (current watchlist)
        top_n = config.scan_max_picks or 12
        st.dataframe(
            df_wl.style.background_gradient(
                subset=["Gesamt-Score"], cmap="RdYlGn", vmin=0, vmax=100
            ).map(
                lambda v: "color: #00e676; font-weight:700" if isinstance(v, (int, float)) and v >= 0 else "",
                subset=["Momentum 20d %"],
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption(f"Top {top_n} werden als Watchlist verwendet.")
    else:
        st.info("Noch keine Scan-Daten. Klicke 'Jetzt neu scannen'.")

    st.divider()

    # Signal-Ticker (Insider, Social, Options, Contracts)
    st.subheader("📡 Signal-Ticker (Small-Cap-Radar)")
    st.caption(
        "Aktien die durch Insider-Käufe, Social-Spikes, Options-Flow oder "
        "Regierungsaufträge aufgefallen sind. Werden **passiv gesammelt** (📥) und "
        "erst bei genug Signal-Gewicht **und Bestätigung aus ≥2 Quellen** zur "
        "Analyse eskaliert (🔬). Max. 3 gleichzeitig in Analyse. Temporär (7 Tage)."
    )
    _expander = SignalDrivenExpander()
    sig_entries = _expander.get_all_entries()
    if sig_entries:
        for _e in sig_entries:
            _e["sources"] = ", ".join(_e.get("sources", [])) or "–"
        df_sig = pd.DataFrame(sig_entries).rename(columns={
            "ticker":     "Ticker",
            "status":     "Status",
            "reason":     "Signal-Grund",
            "weight":     "Gewicht",
            "sources":    "Quellen",
            "n_sources":  "#Q",
            "added_at":   "Entdeckt",
            "expires_at": "Läuft ab",
            "active":     "Aktiv",
            "signals":    "Signale",
        })
        st.dataframe(
            df_sig.style.map(
                lambda v: "color: #00e676; font-weight:700" if v is True else
                          ("color: #888" if v is False else ""),
                subset=["Aktiv"],
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info(
            "Noch keine Signal-Ticker entdeckt.  \n"
            "Der Bot erkennt automatisch unbekannte Aktien aus Insider-Trades, "
            "Social-Spikes und Options-Flow während des Betriebs."
        )

    st.divider()

    # ── Watchlist bearbeiten ─────────────────────────────────────────────────
    st.subheader("✏️ Watchlist bearbeiten")
    st.caption("Änderungen werden sofort in die .env geschrieben. Bot danach neu starten.")

    def _wl_read() -> list:
        try:
            _p = os.path.join(os.path.dirname(__file__), "..", ".env")
            with open(_p) as _f:
                for _l in _f:
                    if _l.strip().startswith("WATCHLIST="):
                        return [t.strip().upper() for t in _l.strip().split("=", 1)[1].split(",") if t.strip()]
        except Exception:
            pass
        return list(config.watchlist)

    def _wl_write(tickers: list) -> None:
        _p = os.path.join(os.path.dirname(__file__), "..", ".env")
        _val = ",".join(tickers)
        try:
            with open(_p) as _f:
                _lines = _f.readlines()
        except FileNotFoundError:
            _lines = []
        _written = False
        _new = []
        for _l in _lines:
            if _l.strip().startswith("WATCHLIST="):
                _new.append(f"WATCHLIST={_val}\n")
                _written = True
            else:
                _new.append(_l)
        if not _written:
            _new.append(f"WATCHLIST={_val}\n")
        with open(_p, "w") as _f:
            _f.writelines(_new)

    _cur_wl = _wl_read()
    _wl_all_opts = sorted(set(list(_ALL_NAMES.keys()) + _cur_wl))

    _wl_col1, _wl_col2 = st.columns([3, 2])
    with _wl_col1:
        _keep = st.multiselect(
            "Aktuelle Watchlist (Häkchen entfernen = löschen)",
            options=_cur_wl,
            default=_cur_wl,
            format_func=ticker_label,
            key="wl_keep_ms",
        )
    with _wl_col2:
        _add_opt = st.selectbox(
            "Ticker hinzufügen",
            options=[""] + _wl_all_opts,
            index=0,
            format_func=lambda x: "— Ticker auswählen —" if x == "" else ticker_label(x),
            key="wl_add_select",
        )
        _add_manual = st.text_input(
            "… oder manuell eingeben",
            placeholder="z.B. TSLA oder BMW.DE",
            key="wl_add_manual",
        ).strip().upper()

    _wl_save_col, _wl_restart_col = st.columns(2)
    with _wl_save_col:
        if st.button("💾 Watchlist speichern", width="stretch", type="primary", key="wl_save_btn"):
            _final_wl = list(_keep)
            for _t in [_add_opt, _add_manual]:
                if _t and _t not in _final_wl:
                    _final_wl.append(_t)
            if _final_wl:
                _wl_write(_final_wl)
                st.success(f"✅ Gespeichert: {', '.join(ticker_label(t) for t in _final_wl)}")
                st.info("Bot neu starten damit die neue Watchlist aktiv wird.")
            else:
                st.error("Watchlist darf nicht leer sein.")
    with _wl_restart_col:
        if st.button("▶️ Bot neu starten", width="stretch", key="wl_restart_btn"):
            try:
                import subprocess as _wl_sp
                _wl_sp.run(["systemctl", "restart", "aktien_bot"], check=True, timeout=10)
                st.success("Bot wurde neu gestartet.")
            except Exception as _wl_e:
                st.error(f"Fehler: {_wl_e}")

    # ── Warteliste (BenchList) ───────────────────────────────────────────────
    st.divider()
    st.subheader("⏳ Warteliste")
    st.caption(
        "Aktien die der Bot aus News, Reddit oder Signal-Scans aufgeschnappt hat. "
        "Bei freien Positions-Slots werden sie automatisch priorisiert analysiert."
    )
    try:
        from analyzers.bench_list import BenchList as _BenchList
        _bench = _BenchList()
        _bench_entries = _bench.get_all()
        if not _bench_entries:
            st.info("Noch keine Kandidaten in der Warteliste. Der Bot füllt sie automatisch beim nächsten Zyklus.")
        else:
            _b_cols = st.columns([1, 3, 1, 1])
            _b_cols[0].markdown("**Ticker**")
            _b_cols[1].markdown("**Grund**")
            _b_cols[2].markdown("**Score**")
            _b_cols[3].markdown("**Signale**")
            for _be in sorted(_bench_entries, key=lambda x: (-x["score"], -x["signal_count"]))[:15]:
                _bc = st.columns([1, 3, 1, 1])
                _bc[0].markdown(f"`{_be['ticker']}`")
                _bc[1].caption(_be["reason"][:60])
                _score_color = "🟢" if _be["score"] >= 0.6 else "🟡" if _be["score"] >= 0.4 else "🔴"
                _bc[2].markdown(f"{_score_color} {_be['score']:.2f}")
                _bc[3].markdown(str(_be["signal_count"]))

            # Ticker manuell zur Warteliste hinzufügen
            with st.expander("➕ Ticker manuell zur Warteliste hinzufügen"):
                _manual_bench_col1, _manual_bench_col2 = st.columns([3, 1])
                _manual_bench_ticker = _manual_bench_col1.text_input(
                    "Ticker", placeholder="z.B. PLTR oder SIE.DE", key="bench_add_input"
                ).strip().upper()
                if _manual_bench_col2.button("Hinzufügen", key="bench_add_btn") and _manual_bench_ticker:
                    _bench.add(_manual_bench_ticker, reason="Manuell hinzugefügt", score=0.5)
                    st.success(f"{_manual_bench_ticker} zur Warteliste hinzugefügt.")
                    st.rerun()
    except Exception as _bench_err:
        st.caption(f"Warteliste nicht verfügbar: {_bench_err}")


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
    from analyzers.analysis_log import AnalysisLog as _AnalysisLog
    _alog = _AnalysisLog()

    st.subheader("🔍 Analyse-Log – alle betrachteten Aktien")
    st.caption(
        "Jede Aktie die der Bot analysiert hat – egal ob gekauft, gehalten oder übersprungen. "
        "Hier siehst du das vollständige Vorgehen und die Begründung."
    )

    cur_stats  = _alog.get_current_stats()   # neueste Analyse pro Ticker
    hist_stats = _alog.get_stats()           # alle Einträge (inkl. Duplikate)
    last_cycle = set(_alog.get_last_cycle_tickers())

    if cur_stats.get("total", 0) > 0:
        st.caption("**Aktueller Stand** – neueste Analyse pro Aktie (keine Duplikate)")
        sc1, sc2, sc3, sc4, sc5 = st.columns(5)
        sc1.metric("Aktien beobachtet", cur_stats.get("total", 0))
        sc2.metric("🟢 Aktuell BUY",    cur_stats.get("buys", 0))
        sc3.metric("⏭ Aktuell SKIP",    cur_stats.get("skips", 0))
        sc4.metric("⏸ Aktuell HOLD",    cur_stats.get("holds", 0))
        sc5.metric("Ø Sentiment",       f"{cur_stats.get('avg_score', 0):.2f}")
        with st.expander(f"📊 Gesamthistorie ({hist_stats.get('total', 0)} Analyse-Einträge)", expanded=False):
            hc1, hc2, hc3, hc4 = st.columns(4)
            hc1.metric("BUY gesamt",  hist_stats.get("buys", 0))
            hc2.metric("SKIP gesamt", hist_stats.get("skips", 0))
            hc3.metric("HOLD gesamt", hist_stats.get("holds", 0))
            hc4.metric("Ø Sentiment", f"{hist_stats.get('avg_score', 0):.2f}")

        # ── Quellen-Health-Ampel (Roadmap 1.4e) ─────────────────────────────
        # Nutzt die bestehende source_health-Mechanik: welche Collectors
        # liefern real Beiträge, welche sind schwach oder tot?
        try:
            _sh = _alog.source_health(days=30)
        except Exception:
            _sh = None
        if _sh and (_sh["healthy"] or _sh["weak"] or _sh["dead"]):
            _n_dead = len(_sh["dead"])
            _sh_icon = "🔴" if _n_dead else ("🟡" if _sh["weak"] else "🟢")
            with st.expander(
                f"{_sh_icon} Quellen-Health — {len(_sh['healthy'])} gesund · "
                f"{len(_sh['weak'])} schwach · {_n_dead} tot "
                f"(letzte {_sh['days']} Tage)", expanded=False,
            ):
                if not _sh["reliable"]:
                    st.caption(
                        f"⚠ Nur {_sh['n_analyses']} Analysen im Zeitraum — "
                        "Aussage statistisch dünn (Bot pausiert?)."
                    )
                def _src_names(keys):
                    return ", ".join(_SOURCE_NAMES.get(k, k) for k in keys)
                if _sh["healthy"]:
                    st.markdown(f"🟢 **Gesund:** {_src_names(_sh['healthy'])}")
                if _sh["weak"]:
                    st.markdown(f"🟡 **Schwach** (<10 % der Analysen): "
                                f"{_src_names(_sh['weak'])}")
                if _sh["dead"]:
                    st.markdown(f"🔴 **Tot** (0 Treffer): {_src_names(_sh['dead'])}")
                    st.caption("Tote Quellen: API-Key fehlt, Quelle defekt — "
                               "oder Abschalt-Kandidat (Roadmap 2.4).")
        st.divider()

    # Alle bisher analysierten Ticker laden (für Queue-Logik)
    _all_log_tickers = sorted({e["ticker"] for e in _alog.get_recent(limit=2000)})
    _analyzed_set = set(_all_log_tickers)

    with st.form("log_filter_form"):
        fa, fb = st.columns([4, 2])
        with fa:
            filter_rec = st.multiselect(
                "Empfehlung filtern",
                ["BUY", "SKIP", "HOLD", "SELL"],
                default=["BUY", "SKIP", "HOLD", "SELL"],
            )
        with fb:
            log_limit = st.selectbox("Anzahl anzeigen", [50, 100, 200, 500], index=0)
            show_all_history = st.checkbox("Alle Einträge (inkl. Duplikate)", value=False,
                                           help="Zeigt jeden Analyse-Lauf einzeln, auch wenn eine Aktie mehrfach analysiert wurde.")

        ticker_search = st.text_input(
            "Aktie suchen oder zur Analyse vormerken",
            placeholder="Ticker oder Name, z.B. BYD, NVDA, Rheinmetall …",
            help="Sucht in Ticker-Symbol und Aktienname. Unbekannte Ticker werden beim nächsten Zyklus analysiert.",
        )
        _sc1, _sc2 = st.columns(2)
        _searched = _sc1.form_submit_button("🔍 Suchen / Anfragen", width="stretch")
        _reset = _sc2.form_submit_button("✖ Filter zurücksetzen", width="stretch")

    # ── Auswertung ───────────────────────────────────────────────────────────
    from analyzers.user_request_queue import add_ticker as _req_ticker, peek as _peek_requests

    _search_filter = "" if _reset else ticker_search.strip().upper()

    # Resolve to exact ticker if input matches a log ticker directly
    _active_ticker: str | None = None
    if _search_filter:
        if _search_filter in _analyzed_set:
            _active_ticker = _search_filter
        else:
            _exact = [t for t in _all_log_tickers if t.upper() == _search_filter]
            if _exact:
                _active_ticker = _exact[0]

    if _searched and _search_filter:
        # Check how many log entries match the search term (ticker or name substring)
        _log_matches = [
            t for t in _all_log_tickers
            if _search_filter in t.upper()
            or _search_filter in _ALL_NAMES.get(t.upper(), "").upper()
        ]
        if _log_matches:
            if len(_log_matches) == 1:
                st.info(f"**{ticker_label(_log_matches[0])}** — Ergebnis unten.")
            else:
                st.info(f"{len(_log_matches)} Aktien gefunden — Ergebnisse unten.")
        else:
            # No log match → queue the input as a ticker for analysis
            if _search_filter in _peek_requests():
                st.success(f"**{_search_filter}** ist bereits für den nächsten Zyklus vorgemerkt.")
            else:
                _req_ticker(_search_filter)
                st.success(
                    f"✅ **{_search_filter}** wurde zur Analyse-Queue hinzugefügt.  \n"
                    f"Der Bot analysiert ihn beim nächsten Zyklus (15:00 Uhr oder beim nächsten Start)."
                )

    # Pending-Queue anzeigen
    from analyzers.user_request_queue import peek as _peek_queue
    _pending = [e if isinstance(e, str) else e.get("ticker", str(e)) for e in _peek_queue()]
    if _pending:
        st.info(f"⏳ Warteschlange: **{', '.join(_pending)}** — werden beim nächsten Zyklus analysiert.")

    # Latest news for selected ticker
    if _active_ticker:
        _news = _get_ticker_news(_active_ticker)
        if _news:
            with st.expander(f"📰 Aktuelle News — {ticker_label(_active_ticker)}", expanded=True):
                for _n in _news:
                    _title     = _n.get("title", "")
                    _publisher = _n.get("publisher", "")
                    _pub_ts    = _n.get("providerPublishTime") or _n.get("pubTime") or 0
                    _pub_str   = datetime.utcfromtimestamp(_pub_ts).strftime("%d.%m.%Y") if _pub_ts else ""
                    _sentiment = _n.get("overallSentiment", "")
                    _s_icon    = {"POSITIVE": "🟢", "NEGATIVE": "🔴", "NEUTRAL": "🟡"}.get(_sentiment, "")
                    st.markdown(f"{_s_icon} **{_title}**  \n_{_publisher}_ · {_pub_str}")

    # Dedupliziert (Standard) oder volle Historie
    if _active_ticker or show_all_history:
        entries = _alog.get_recent(limit=log_limit, ticker=_active_ticker)
    else:
        entries = _alog.get_latest_per_ticker(limit=log_limit)
    if filter_rec:
        entries = [e for e in entries if e["recommendation"] in filter_rec]
    # Substring-Filter: Ticker oder Name enthält Suchbegriff
    if _search_filter and not _active_ticker:
        entries = [
            e for e in entries
            if _search_filter in e["ticker"].upper()
            or _search_filter in _ALL_NAMES.get(e["ticker"].upper(), "").upper()
        ]

    # Vorherige Empfehlung für Trend-Pfeil vorabladen (nur wenn dedupliziert)
    _prev_rec: dict = {}
    if not show_all_history and not _active_ticker:
        for e in entries:
            t = e["ticker"]
            if t not in _prev_rec:
                _prev_rec[t] = _alog.get_prev_recommendation(t)

    if not entries:
        if _active_ticker:
            st.info(f"Noch keine Analyse für **{ticker_label(_active_ticker)}** vorhanden.")
        elif _search_filter:
            st.info(f"Keine Analyse-Einträge für **{_search_filter}** gefunden.")
        else:
            st.info("Noch keine Analysen gespeichert. Der Bot beginnt beim nächsten Zyklus.")
    else:
        _REC_ICON = {"BUY": "🟢", "SKIP": "⏭", "HOLD": "⏸", "SELL": "🔴"}
        _DIR_ICON = {"BULLISH": "📈", "NEUTRAL": "➡️", "BEARISH": "📉"}
        _today_str = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        for entry in entries:
            rec  = entry["recommendation"]
            icon = _REC_ICON.get(rec, "•")
            dir_icon = _DIR_ICON.get(entry["direction"], "")
            score = entry["sentiment_score"]
            conf  = entry["confidence"]
            ts_full = entry["analyzed_at"]
            ts    = ts_full[:16]

            # Alters-Badge: zeigt wie frisch die Analyse ist
            _entry_date = ts_full[:10]
            if entry["ticker"] in last_cycle:
                _age_badge = "🔵 Letzter Zyklus"
            elif _entry_date == _today_str:
                _age_badge = "🟢 Heute"
            elif _entry_date >= (datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0,minute=0,second=0) -
                                  __import__("datetime").timedelta(days=7)).strftime("%Y-%m-%d"):
                _age_badge = "🟡 Diese Woche"
            else:
                _age_badge = "⚪ Älter"

            # Trend-Pfeil: hat sich die Empfehlung geändert?
            _prev = _prev_rec.get(entry["ticker"])
            if _prev and _prev != rec:
                _trend = f" ↑ war {_prev}" if rec == "BUY" else f" ↓ war {_prev}" if rec == "SELL" else f" ↔ war {_prev}"
            else:
                _trend = ""

            name_suffix = f" ({_ALL_NAMES[entry['ticker'].upper()]})" if entry['ticker'].upper() in _ALL_NAMES else ""
            label = (
                f"{icon} **{entry['ticker']}{name_suffix}** · {dir_icon} {entry['direction']} "
                f"· Score {score:.2f} · {conf} · {ts}{_trend} · {_age_badge}"
            )
            with st.expander(label):
                col_l, col_r = st.columns([3, 2])
                with col_l:
                    st.markdown("**Begründung:**")
                    st.info(entry.get("entry_rationale") or "–")

                    if entry.get("bull_case"):
                        st.markdown(f"🟢 **Bull-Case:** {entry['bull_case']}")
                    if entry.get("bear_case"):
                        st.markdown(f"🔴 **Bear-Case:** {entry['bear_case']}")
                    if entry.get("debate_winner"):
                        winner = entry["debate_winner"]
                        w_icon = "🟢" if winner == "BULL" else ("🔴" if winner == "BEAR" else "🟡")
                        st.markdown(f"**Debatte-Gewinner:** {w_icon} {winner}")

                with col_r:
                    st.metric("Empfehlung",    f"{icon} {rec}")
                    st.metric("Sentiment",     f"{score:.2f}")
                    st.metric("Konfidenz",     conf)
                    if entry.get("target_price"):
                        st.metric("Kursziel",  f"${entry['target_price']:.2f}")
                    if entry.get("suggested_hold"):
                        st.metric("Haltedauer", f"{entry['suggested_hold']} Tage")

                catalysts = entry.get("key_catalysts", [])
                risks     = entry.get("risk_factors", [])
                if catalysts:
                    st.markdown("**⚡ Kaufkatalysatoren:** " + " · ".join(catalysts[:4]))
                if risks:
                    st.markdown("**⚠️ Risiken:** " + " · ".join(risks[:3]))
                # Quellen-Provenienz (Roadmap 1.4a): was floss in diese Analyse ein?
                render_sources_breakdown(entry.get("sources_breakdown"),
                                         total=entry.get("sources_used"))


# ══════════════════════════════════════════════════════════
# TAB 10 – EINSTELLUNGEN
# ══════════════════════════════════════════════════════════
with tab_settings:
    import subprocess
    import re as _re

    _ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")

    def _read_env() -> dict:
        """Liest .env als dict."""
        result = {}
        try:
            with open(_ENV_PATH) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        result[k.strip()] = v.strip()
        except Exception:
            pass
        return result

    def _write_env(updates: dict) -> None:
        """Schreibt einzelne Keys in die .env, fügt fehlende am Ende ein."""
        try:
            with open(_ENV_PATH) as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []

        written = set()
        new_lines = []
        for line in lines:
            if line.strip().startswith("#") or "=" not in line:
                new_lines.append(line)
                continue
            key = line.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                written.add(key)
            else:
                new_lines.append(line)

        for key, val in updates.items():
            if key not in written:
                new_lines.append(f"{key}={val}\n")

        with open(_ENV_PATH, "w") as f:
            f.writelines(new_lines)

    _env = _read_env()

    st.subheader("⚙️ Bot-Einstellungen")
    st.caption(
        "Änderungen werden sofort in die `.env` geschrieben. "
        "Klicke danach **Bot neu starten** damit sie aktiv werden."
    )

    # ── Fokus-Modus AUSSERHALB des Formulars (sofortige Reaktion) ────────────
    _focus_opts   = ["WEALTH_BUILDING", "INCOME", "TARGET_GOAL"]
    _focus_labels = {
        "WEALTH_BUILDING": "🚀 Vermögensaufbau – maximales Wachstum",
        "INCOME":          "💸 Ausschüttung – monatliche Erträge",
        "TARGET_GOAL":     "🏁 Ziel-Modus – Ziel-Betrag bis Datum",
    }
    _cur_focus = _env.get("FOCUS_MODE", config.focus_mode)
    _live_focus = st.selectbox(
        "Fokus-Modus",
        _focus_opts,
        index=_focus_opts.index(_cur_focus) if _cur_focus in _focus_opts else 0,
        format_func=lambda x: _focus_labels[x],
        key="settings_focus_live",
    )

    with st.form("settings_form"):
        col_a, col_b = st.columns(2)
        _sel_focus = st.session_state.get("settings_focus_live", _cur_focus)

        with col_a:
            st.markdown("#### 🎯 Ziel-Einstellungen")

            new_monthly_eur = float(_env.get("MONTHLY_DISTRIBUTION_EUR", config.monthly_distribution_eur))
            new_buffer = int(_env.get("DISTRIBUTION_BUFFER_MONTHS", config.distribution_buffer_months))

            _cap = config.initial_capital  # aktuelles Startkapital

            if _sel_focus == "TARGET_GOAL":
                new_goal_amount = st.number_input(
                    "Ziel-Betrag ($)",
                    min_value=0.0, step=1000.0,
                    value=float(_env.get("TARGET_GOAL_AMOUNT", config.target_goal_amount or 0)),
                )
                new_goal_date = st.text_input(
                    "Ziel-Datum (YYYY-MM-DD)",
                    value=_env.get("TARGET_GOAL_DATE", config.target_goal_date or ""),
                    placeholder="2027-12-31",
                )
                new_growth = float(_env.get("GROWTH_TARGET_MULTIPLE", config.growth_target_multiple))
                # Kapitalempfehlung Ziel-Modus
                if new_goal_amount > 0 and new_goal_date:
                    try:
                        from datetime import date as _date
                        _days = (_date.fromisoformat(new_goal_date) - _date.today()).days
                        _years = max(_days / 365, 0.1)
                        _needed_return = (new_goal_amount / _cap) ** (1 / _years) - 1
                        _color = "green" if _needed_return < 0.20 else ("orange" if _needed_return < 0.40 else "red")
                        _verdict = "realistisch" if _needed_return < 0.20 else ("ambitioniert" if _needed_return < 0.40 else "sehr aggressiv")
                        st.markdown(
                            f"**Kapitalempfehlung:** Bei **${_cap:,.0f}** Startkapital und "
                            f"**{_years:.1f} Jahren** bis zum Zieldatum benötigst du "
                            f"**:{_color}[{_needed_return*100:.1f}% p.a.]** — _{_verdict}_  \n"
                            f"Empfohlenes Mindestkapital für dieses Ziel: **${new_goal_amount * 0.3:,.0f}**+ "
                            f"(30% des Zielbetrags als Start).",
                            unsafe_allow_html=False,
                        )
                    except Exception:
                        pass

            elif _sel_focus == "WEALTH_BUILDING":
                new_growth = st.slider(
                    "Wachstumsziel (× Startkapital)",
                    min_value=1.5, max_value=10.0, step=0.5,
                    value=float(_env.get("GROWTH_TARGET_MULTIPLE", config.growth_target_multiple)),
                    help="Ab diesem Vielfachen des Startkapitals wechselt der Bot in die Ausschüttungsphase",
                )
                new_goal_amount = float(_env.get("TARGET_GOAL_AMOUNT", 0) or 0)
                new_goal_date   = _env.get("TARGET_GOAL_DATE", "")
                _target_val = _cap * new_growth
                # Jahre bis Ziel bei verschiedenen Renditen
                import math as _math
                _y15 = _math.log(new_growth) / _math.log(1.15)
                _y20 = _math.log(new_growth) / _math.log(1.20)
                st.info(
                    f"Ausschüttungsphase ab: **${_target_val:,.0f}** ({new_growth:.1f}× Startkapital)  \n"
                    f"Zeitrahmen bei 15% p.a.: **{_y15:.1f} Jahre** · bei 20% p.a.: **{_y20:.1f} Jahre**  \n"
                    f"Empfohlenes Mindestkapital: **$10.000+** (mehr Kapital = besser diversifiziert)"
                )

            else:  # INCOME
                new_growth = float(_env.get("GROWTH_TARGET_MULTIPLE", config.growth_target_multiple))
                new_goal_amount = float(_env.get("TARGET_GOAL_AMOUNT", 0) or 0)
                new_goal_date   = _env.get("TARGET_GOAL_DATE", "")
                new_monthly_eur = st.number_input(
                    "Gewünschte monatliche Ausschüttung (€)",
                    min_value=100.0, max_value=50000.0, step=100.0,
                    value=float(_env.get("MONTHLY_DISTRIBUTION_EUR", config.monthly_distribution_eur)),
                    help="Der Bot handelt konservativ und versucht diesen Betrag monatlich aus Gewinnen zu erwirtschaften.",
                )
                new_buffer = st.slider(
                    "Sicherheitspuffer (Monate)",
                    min_value=1, max_value=12, step=1,
                    value=int(_env.get("DISTRIBUTION_BUFFER_MONTHS", config.distribution_buffer_months)),
                    help="Wie viele Monatsbeträge als Reserve gehalten werden bevor ausgeschüttet wird.",
                )
                # Kapitalempfehlung Ausschüttungs-Modus
                _annual_eur = new_monthly_eur * 12
                _rec_cap_15 = _annual_eur / 0.15  # benötigtes Kapital bei 15% Rendite
                _rec_cap_20 = _annual_eur / 0.20  # bei 20% Rendite
                _current_usd = _cap
                _ok = _current_usd >= _rec_cap_20
                _msg_color = "green" if _ok else ("orange" if _current_usd >= _rec_cap_15 * 0.6 else "red")
                _verdict2 = "erreichbar" if _ok else ("knapp" if _current_usd >= _rec_cap_20 * 0.6 else "Kapital zu gering")
                st.markdown(
                    f"**Kapitalempfehlung für {new_monthly_eur:,.0f} €/Monat:**  \n"
                    f"• Bei 15% Jahresrendite: **${_rec_cap_15:,.0f}** Mindestkapital  \n"
                    f"• Bei 20% Jahresrendite: **${_rec_cap_20:,.0f}** Mindestkapital  \n"
                    f"• Dein Kapital: **${_current_usd:,.0f}** — _{_verdict2}_"
                )

        with col_b:
            st.markdown("#### 🛡 Risikomanagement")
            new_sl = st.slider(
                "Stop-Loss %",
                min_value=3, max_value=20, step=1,
                value=int(round(float(_env.get("STOP_LOSS_PCT", config.stop_loss_pct)) * 100)),
                help="Position wird automatisch verkauft wenn Verlust diesen Wert erreicht",
            )
            new_tp = st.slider(
                "Take-Profit %",
                min_value=10, max_value=60, step=5,
                value=int(round(float(_env.get("TAKE_PROFIT_PCT", config.take_profit_pct)) * 100)),
                help="Position wird automatisch verkauft wenn Gewinn diesen Wert erreicht",
            )
            new_maxpos_pct = st.slider(
                "Max. Positionsgröße % des Portfolios",
                min_value=5, max_value=30, step=1,
                value=int(round(float(_env.get("MAX_POSITION_PCT", config.max_position_pct)) * 100)),
            )
            new_buy_thr = st.slider(
                "Kauf-Schwelle (Sentiment-Score)",
                min_value=0.50, max_value=0.95, step=0.05,
                value=float(_env.get("BUY_THRESHOLD", config.buy_threshold)),
                help="Nur Aktien mit Sentiment-Score ≥ diesem Wert werden gekauft",
            )
            new_sell_thr = st.slider(
                "Verkauf-Schwelle (Sentiment-Score)",
                min_value=0.10, max_value=0.50, step=0.05,
                value=float(_env.get("SELL_THRESHOLD", config.sell_threshold)),
                help="Positionen werden verkauft wenn der Score unter diesen Wert fällt",
            )

        st.markdown("#### 📋 Watchlist & Scanning")
        w1, w2 = st.columns(2)
        with w1:
            cur_wl = ",".join(config.watchlist)
            new_wl_raw = st.text_area(
                "Watchlist (Komma-getrennt)",
                value=_env.get("WATCHLIST", cur_wl),
                height=80,
                help="Ticker die immer analysiert werden, z.B. AAPL,MSFT,NVDA",
            )
            new_auto_scan = st.toggle(
                "Dynamische Watchlist (AUTO_SCAN)",
                value=_env.get("AUTO_SCAN_WATCHLIST", "false").lower() in ("1","true","yes"),
                help="Bot wählt täglich automatisch die vielversprechendsten Aktien",
            )
            new_scan_picks = st.slider(
                "Max. Auto-Scan Picks",
                min_value=3, max_value=20, step=1,
                value=int(_env.get("SCAN_MAX_PICKS", config.scan_max_picks or 3)),
            )
        with w2:
            new_eu = st.toggle(
                "EU-Aktien aktivieren",
                value=_env.get("EU_STOCKS_ENABLED", "false").lower() in ("1","true","yes"),
            )
            cur_eu_wl = ",".join(config.eu_watchlist) if config.eu_watchlist else ""
            new_eu_wl = st.text_area(
                "EU-Watchlist (leer = Auto-Scan)",
                value=_env.get("EU_WATCHLIST", cur_eu_wl),
                height=80,
                placeholder="SAP.DE,ASML.AS,MC.PA",
                disabled=not new_eu,
            )

        st.markdown("#### ⚡ Spezial-Modi")
        st.caption("Diese Modi überschreiben die Risiko-Einstellungen oben.")
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            new_turbo = st.toggle(
                "🚀 Turbo-Modus",
                value=_env.get("TURBO_MODE", "false").lower() in ("1","true","yes"),
                help="Aggressiv: SL 12%, TP 40%, Positionsgröße 40%, Kauf-Schwelle 0.45. Nur für Paper-Trading empfohlen.",
            )
            if new_turbo:
                st.warning("Turbo: SL 12% · TP 40% · Max-Position 40% · Kauf-Score 0.45")
        with m2:
            new_expl = st.toggle(
                "🔬 Explorations-Modus",
                value=_env.get("EXPLORATION_MODE", "false").lower() in ("1","true","yes"),
                help="Testet schwächere Signale (Score ab 0.55) mit kleineren Positionen um neue Muster zu entdecken.",
            )
            if new_expl:
                st.info("Exploration: Kauf-Score 0.55 · Min-Quellen 1 · Max-Position 25%")
        with m3:
            new_intraday = st.toggle(
                "🕐 Intraday-Scan (3. Analyse)",
                value=_env.get("INTRADAY_SCAN_ENABLED", "false").lower() in ("1","true","yes"),
                help="Führt täglich einen dritten Analyse-Zyklus durch – ideal während der US-Session.",
            )
            new_intraday_time = st.text_input(
                "Uhrzeit (UTC)",
                value=_env.get("INTRADAY_SCAN_TIME", "17:30"),
                placeholder="17:30",
                disabled=not new_intraday,
                help="17:30 UTC = 19:30 MESZ (mitten in der US-Session)",
            )
            if new_intraday:
                st.info(f"3. Analyse täglich um {new_intraday_time} UTC")
        with m4:
            new_frugal = st.toggle(
                "🤖 Ollama-Vollanalyse",
                value=_env.get("FRUGAL_MODE", "false").lower() in ("1","true","yes"),
                help="Ollama übernimmt die komplette Analyse für normale Ticker. Claude nur noch für offene Positionen, SEC/Earnings und manuelle Anfragen. Spart ~85% Claude-Kosten.",
            )
            if new_frugal:
                st.success("Frugal: Ollama analysiert alles · Claude nur für Positionen & SEC")

        st.divider()
        save_btn = st.form_submit_button("💾 Einstellungen speichern", width="stretch", type="primary")

    if save_btn:
        updates = {
            "FOCUS_MODE":             _sel_focus,
            "TARGET_GOAL_AMOUNT":     str(new_goal_amount),
            "TARGET_GOAL_DATE":       new_goal_date,
            "GROWTH_TARGET_MULTIPLE": str(new_growth),
            "STOP_LOSS_PCT":          str(new_sl / 100),
            "TAKE_PROFIT_PCT":        str(new_tp / 100),
            "MAX_POSITION_PCT":       str(new_maxpos_pct / 100),
            "BUY_THRESHOLD":          str(new_buy_thr),
            "SELL_THRESHOLD":         str(new_sell_thr),
            "WATCHLIST":              ",".join(t.strip().upper() for t in new_wl_raw.split(",") if t.strip()),
            "AUTO_SCAN_WATCHLIST":    "true" if new_auto_scan else "false",
            "SCAN_MAX_PICKS":         str(new_scan_picks),
            "EU_STOCKS_ENABLED":      "true" if new_eu else "false",
            "EU_WATCHLIST":           ",".join(t.strip().upper() for t in new_eu_wl.split(",") if t.strip()),
            "ENABLE_SOCIAL_SCAN":     "false",
            "TURBO_MODE":                "true" if new_turbo else "false",
            "EXPLORATION_MODE":          "true" if new_expl else "false",
            "INTRADAY_SCAN_ENABLED":     "true" if new_intraday else "false",
            "INTRADAY_SCAN_TIME":        new_intraday_time.strip() or "17:30",
            "FRUGAL_MODE":               "true" if new_frugal else "false",
            "MONTHLY_DISTRIBUTION_EUR":  str(new_monthly_eur),
            "DISTRIBUTION_BUFFER_MONTHS": str(new_buffer),
        }
        try:
            _write_env(updates)
            try:
                subprocess.run(["systemctl", "restart", "aktien_bot"], check=True, timeout=15)
                st.success("✅ Einstellungen gespeichert und Bot neu gestartet.")
            except Exception as _re:
                st.success("✅ Einstellungen gespeichert.")
                st.warning(f"Bot-Neustart fehlgeschlagen (manuell starten): {_re}")
        except Exception as e:
            st.error(f"Fehler beim Speichern: {e}")

    st.divider()
    st.markdown("### 🔄 Bot-Dienste & Zurücksetzen")
    r1, r2, r3 = st.columns(3)
    with r1:
        if st.button("▶️ Bot neu starten", width="stretch", type="primary"):
            try:
                subprocess.run(["systemctl", "restart", "aktien_bot"], check=True, timeout=10)
                st.success("Bot wurde neu gestartet.")
            except Exception as e:
                st.error(f"Fehler: {e}")
    with r2:
        if st.button("▶️ Dashboard neu starten", width="stretch"):
            try:
                subprocess.run(["systemctl", "restart", "aktien_dashboard"], check=True, timeout=10)
                st.info("Dashboard-Dienst neu gestartet.")
            except Exception as e:
                st.error(f"Fehler: {e}")
    with r3:
        if st.button("🔄 Cache leeren & neu laden", width="stretch"):
            st.cache_resource.clear()
            st.rerun()

    st.divider()
    st.markdown("### 📋 Aktuelle .env Werte (Übersicht)")
    st.caption("Nur zur Ansicht – Änderungen oben im Formular vornehmen.")
    _display_keys = [
        "FOCUS_MODE","TARGET_GOAL_AMOUNT","TARGET_GOAL_DATE","GROWTH_TARGET_MULTIPLE",
        "STOP_LOSS_PCT","TAKE_PROFIT_PCT","MAX_POSITION_PCT","BUY_THRESHOLD","SELL_THRESHOLD",
        "TURBO_MODE","EXPLORATION_MODE","INTRADAY_SCAN_ENABLED","INTRADAY_SCAN_TIME",
        "WATCHLIST","AUTO_SCAN_WATCHLIST","SCAN_MAX_PICKS","EU_STOCKS_ENABLED","EU_WATCHLIST",
        "ENABLE_SOCIAL_SCAN","INITIAL_CAPITAL","BROKER_MODE",
    ]
    env_rows = [{"Einstellung": k, "Wert": _env.get(k, "–")} for k in _display_keys]
    st.dataframe(pd.DataFrame(env_rows), width="stretch", hide_index=True)


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
