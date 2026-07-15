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

# ─── Ticker → Firmenname (Roadmap 4.4a: dashboard/ticker_names.py) ─────────
from dashboard.ticker_names import (US_NAMES as _US_NAMES, EU_NAMES as _EU_NAMES,
                                    ALL_NAMES as _ALL_NAMES, ticker_label)


# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Stock Sentiment Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="auto",
)

from dashboard import theme as _theme  # noqa: E402
_theme.inject()
_theme.register_chart_themes()

from dashboard.auth import require_login  # noqa: E402
require_login()

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
    _logo_uri = _theme.image_b64("logo.png")  # D5.3: echtes Logo, sonst Emoji-Platzhalter
    if _theme.is_enabled() and _logo_uri:
        st.markdown(f'<img src="{_logo_uri}" style="height:2.2em;">', unsafe_allow_html=True)
    else:
        st.markdown("## 📈")
with c_title:
    if _theme.is_enabled():
        st.markdown(
            _theme.panel(
                '<div class="px-head" style="font-size:1.1rem;">Stock Sentiment Trading Bot</div>'
                f'<small style="color:var(--px-text-muted);">Stand: '
                f'{datetime.now().strftime("%d.%m.%Y %H:%M")} · Broker: '
                f'{config.broker_mode.upper()}</small>'
            ),
            unsafe_allow_html=True,
        )
    else:
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

# ─── Laufband-Anzeigetafel (Design D7.3) ─────────────────────────────────────
# LED-Ticker wie in einer Werkshalle: die letzten echten Ereignisse aus dem
# Activity-Feed + nächster geplanter Lauf. Nur pixel, fail-open.
if _theme.is_enabled():
    try:
        from system.live_status import feed_recent as _tick_recent, read_status as _tick_ls
        _tick_items = []
        for _ev in _tick_recent(limit=5):
            _t = (_ev.get("ts") or "")[11:16]
            _parts = [p for p in (_ev.get("ticker"), _ev.get("detail")) if p]
            _tick_items.append(f"{_t} {_ev.get('event', '?').upper()}: {' — '.join(_parts) or '–'}")
        _tick_next = (_tick_ls() or {}).get("next_run")
        if _tick_next:
            _tick_items.append(f"NÄCHSTER LAUF: {str(_tick_next)[:16].replace('T', ' ')} UHR")
        _ticker_html = _theme.ticker(_tick_items)
        if _ticker_html:
            st.markdown(_ticker_html, unsafe_allow_html=True)
    except Exception:
        pass

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

# ─── Gesundheits-Ampelleiste (Roadmap 1.5d): IB-Gateway/Claude-Kosten/CB ─────
# Unabhängig vom Bot-Pause-Zustand (autologin.sh hält Port 4002 stündlich
# offen, auch während der Bot pausiert ist) – reine Infrastruktur-Signale,
# fail-open: ein einzelner fehlgeschlagener Check darf die anderen nie
# verschlucken oder das Dashboard blockieren (Gateway-Check mit kurzem Timeout).
def _ibkr_gateway_dot() -> str:
    import socket
    try:
        with socket.create_connection((config.ibkr_host, config.ibkr_port), timeout=0.4):
            return _theme.led("ok", "IB-Gateway")
    except Exception:
        return _theme.led("err", "IB-Gateway")


def _claude_cost_dot() -> str:
    try:
        from analyzers.api_cost_tracker import APICostTracker
        s = APICostTracker().summary()
        today = float(s.get("today_cost_eur") or 0.0)
        limit = float(s.get("daily_limit_eur") or 0.0)
        pct = (today / limit * 100) if limit > 0 else 0.0
        status = "err" if pct >= 100 else "warn" if pct >= 80 else "ok"
        return _theme.led(status, f"Claude-Kosten {today:.2f}€/{limit:.2f}€")
    except Exception:
        return _theme.led("off", "Claude-Kosten n/a")


def _circuit_breaker_dot(current_value: float) -> str:
    try:
        from portfolio.circuit_breaker import CircuitBreaker
        st_cb = CircuitBreaker().status(current_value)
        triggered = bool(st_cb.get("triggered"))
        return _theme.led("err" if triggered else "ok",
                          "Circuit-Breaker AUSGELÖST" if triggered else "Circuit-Breaker")
    except Exception:
        return _theme.led("off", "Circuit-Breaker n/a")


try:
    _ampel_line = " · ".join([
        _ibkr_gateway_dot(),
        _claude_cost_dot(),
        _circuit_breaker_dot(total_value),
    ])
    if _theme.is_enabled():
        st.markdown(_theme.panel(_ampel_line), unsafe_allow_html=True)
    else:
        st.caption(_ampel_line)
except Exception:
    pass

# ─── Kiosk-Modus (Ausbau-Roadmap H6.1) ───────────────────────────────────────
# ?kiosk=1: nur die Fabrik als Dauer-Wandbild auf einem Zweitmonitor — keine
# KPI-Leiste, keine Instrumente/Ticker, keine Tabs. Kopfzeile bleibt minimal
# (Logo/Titel/Status-Ampel oben sind bereits gerendert); Streamlit-eigene
# Kopfzeile/Toolbar wird per CSS ausgeblendet (nur hier, nicht global in
# theme.py — Kiosk ist ein eigener Anzeigemodus, kein Theme-Zustand).
if st.query_params.get("kiosk") == "1":
    st.markdown(
        '<style>[data-testid="stHeader"], [data-testid="stToolbar"], '
        '#MainMenu, footer {display:none;}</style>'
        '<div class="px-kiosk"></div>',
        unsafe_allow_html=True,
    )
    from dashboard.tabs import factory as _kiosk_factory
    _kiosk_factory.render(None)
    st.stop()

# ─── Leitstand-Instrumente (Design D7.1) ─────────────────────────────────────
# Dieselben Risiko-/Kostenzahlen wie in der Ampel-Zeile, aber als ablesbare
# Industrie-Instrumente: Manometer (Tagesverlust vs. Circuit-Breaker-Limit),
# Tank (Rest des Claude-Tagesbudgets), 7-Segment (Depotwert). Nur pixel;
# fail-open — ein Instrument-Fehler darf das Dashboard nie blockieren.
if _theme.is_enabled():
    try:
        from dashboard import instruments as _instr
        from portfolio.circuit_breaker import CircuitBreaker as _InstrCB
        from portfolio.circuit_breaker import _MAX_DAILY_LOSS as _CB_LIMIT

        _cb_st = _InstrCB().status(total_value)
        _loss_pct = max(0.0, -float(_cb_st.get("daily_pct") or 0.0))
        _pressure = (_loss_pct / (_CB_LIMIT * 100) * 100) if _CB_LIMIT else 0.0

        from analyzers.api_cost_tracker import APICostTracker as _InstrCost
        _cost_s = _InstrCost().summary()
        _cost_today = float(_cost_s.get("today_cost_eur") or 0.0)
        _cost_limit = float(_cost_s.get("daily_limit_eur") or 0.0)
        _fuel = (max(0.0, 100.0 - _cost_today / _cost_limit * 100)
                 if _cost_limit > 0 else 100.0)

        _ic1, _ic2, _ic3 = st.columns([2, 1.3, 2.7])
        _ic1.markdown(
            _instr.gauge_svg(
                _pressure, "KESSELDRUCK",
                f"Tagesverlust {_loss_pct:.1f}% / Limit {_CB_LIMIT * 100:.0f}%",
            ),
            unsafe_allow_html=True,
        )
        _ic2.markdown(
            _instr.tank_svg(
                _fuel, "TREIBSTOFF",
                f"Claude {_cost_today:.2f}/{_cost_limit:.2f}€",
            ),
            unsafe_allow_html=True,
        )
        _ic3.markdown(
            _instr.seven_segment_svg(f"{total_value:.0f}", "DEPOTWERT USD"),
            unsafe_allow_html=True,
        )
    except Exception:
        pass

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

tab_portfolio, tab_live, tab_decisions, tab_regime, tab_queue, tab_network, tab_briefing, tab_trades, tab_tech, tab_watchlist, tab_log, tab_settings, tab_factory = st.tabs([
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
    "🏭 Fabrik",
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
    from dashboard.tabs import sidebar as _sidebar
    _sidebar.render(_ctx)


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


# ══════════════════════════════════════════════════════════
# TAB "FABRIK" – interaktives Wimmelbild (Vision W1)
# ══════════════════════════════════════════════════════════
with tab_factory:
    from dashboard.tabs import factory as _tab_factory
    _tab_factory.render(_ctx)


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
