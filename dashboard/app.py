"""
Streamlit Dashboard – Stock Sentiment Trading Bot
Starten: streamlit run dashboard/app.py
     oder: python main.py --dashboard
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import html
import json
import streamlit as st
import pandas as pd
from datetime import datetime, timezone

from config import config
from broker.factory import get_readonly_broker
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
    # Dieselbe Preis-/Positionsquelle wie der Live-Bot (config.broker_mode) –
    # sonst zeigt das Dashboard eine andere Wirklichkeit als die, auf der der
    # Bot tatsächlich handelt (25.7.2026, SAP-Vorfall: Dashboard hätte per
    # PaperBroker/yfinance eine gesunde Position gezeigt, während IBKR intern
    # einen fehlerhaften Stop-Loss-Bruch sah). Read-only, eigene Client-ID –
    # kann nie eine Order platzieren und stört die Bot-Session nicht.
    broker       = get_readonly_broker()
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
# W8.1 (18.7.2026): der ganze Kopf (Logo/Titel/Status/Ampel) wird in eine
# schwebende HUD-Leiste gepackt — sitzt beim Scrollen oben, statt die Szene
# als eigener Block nach unten zu drücken (User-Vorgabe: "die Fabrik soll
# das Einzigste sein, Zusatzinfos am Rand wie bei einem Handy-Base-Bau-
# Spiel"). Öffnender Div hier, schließender direkt vor dem Kiosk-Zweig
# unten — nur bei aktivem Pixel-Theme (Plain-Notausstieg bleibt unangetastet).
if _theme.is_enabled():
    st.markdown('<div class="px-hud-bar">', unsafe_allow_html=True)

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

# Tab-Umbau 18.7.2026: LED-Laufband (D7.3) aus dem Kopf entfernt — dieselben
# Feed-Ereignisse stehen im Live-Tab-Terminal und im Fabrik-Logbuch; der Kopf
# bleibt Logo/Titel/Status/Ampel.

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
        # Ausbau H7.1: Werksleiter-Stimmung neben der Ampel — ein Blick
        # aufs Gesicht statt drei Panels lesen zu müssen. Eigener
        # try/except je Schritt (Score-Abruf, Render), damit ein
        # Fehler hier nie die Ampel selbst verschluckt.
        _ampel_col, _face_col = st.columns([5, 1])
        with _ampel_col:
            st.markdown(_theme.panel(_ampel_line), unsafe_allow_html=True)
        with _face_col:
            try:
                from analyzers.bot_scorer import BotScorer
                _face_score = BotScorer().get().current
            except Exception:
                _face_score = None
            try:
                from dashboard import instruments as _face_instr
                st.markdown(
                    _face_instr.face_svg(_face_score, "Werksleiter"),
                    unsafe_allow_html=True,
                )
            except Exception:
                pass
    else:
        st.caption(_ampel_line)
except Exception:
    pass

if _theme.is_enabled():
    st.markdown('</div>', unsafe_allow_html=True)

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

# ─── Handy-Kompaktansicht (Ausbau-Roadmap H6.2) ──────────────────────────────
# ?mobile=1: die 5 wichtigsten Zahlen + Ampel + Mini-Fabrik + Terminal-Feed
# untereinander — ehrliche Verkleinerung, kein PWA-Vollausbau. Kopfzeile
# (Logo/Titel/Status-Banner/Ampel) ist bereits gerendert; Streamlit-eigene
# Kopfzeile/Toolbar wird wie im Kiosk-Modus per CSS ausgeblendet.
if st.query_params.get("mobile") == "1":
    st.markdown(
        '<style>[data-testid="stHeader"], [data-testid="stToolbar"], '
        '#MainMenu, footer {display:none;}</style>',
        unsafe_allow_html=True,
    )
    try:
        from portfolio.circuit_breaker import CircuitBreaker as _MobileCB
        _mobile_cb = _MobileCB().status(total_value)
        _mobile_daily_pct = _mobile_cb.get("daily_pct") or 0.0
        _mobile_open_value = _mobile_cb.get("open_value") or total_value
        _mobile_daily_pnl = total_value - _mobile_open_value
    except Exception:
        _mobile_daily_pct, _mobile_daily_pnl = 0.0, 0.0
    st.metric(
        "Depotwert", f"${total_value:,.2f}",
        f"{_mobile_daily_pnl:+,.2f} $ ({_mobile_daily_pct:+.2f}%)",
    )

    try:
        from dashboard.factory import render_scene as _mobile_scene
        st.markdown(_mobile_scene(), unsafe_allow_html=True)
    except Exception:
        pass

    try:
        from system.live_status import feed_recent as _mobile_feed_recent
        _mobile_events = _mobile_feed_recent(limit=10)
    except Exception:
        _mobile_events = []
    if _mobile_events:
        _mobile_lines = []
        for _mev in _mobile_events:
            _mts = (_mev.get("ts") or "")[11:16]
            _mparts = [p for p in (_mev.get("ticker"), _mev.get("detail")) if p]
            _mobile_lines.append(
                f"{_mts} {_mev.get('event', '?').upper()}: {' — '.join(_mparts) or '–'}"
            )
        st.markdown(
            '<div class="px-terminal">' + "".join(
                f"<div>{html.escape(line)}</div>" for line in _mobile_lines
            ) + "</div>",
            unsafe_allow_html=True,
        )
    st.stop()

# Tab-Umbau 18.7.2026: Leitstand-Instrumente (D7.1) in den Fabrik-Tab
# verschoben (Leitstand-Optik gehört in die Halle), KPI-Leiste in den
# Portfolio-Tab (dorthin, wo die Detail-Zahlen stehen) — der Kopf bleibt
# Logo/Titel/Status/Ampel. `invested` weiter hier berechnet, weil
# tabs/portfolio.py es über ctx konsumiert.
invested = sum(pos.shares * prices.get(t, pos.entry_price) for t, pos in portfolio.all_positions().items())


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
# KARTEN-UMBAU (Vision W7, 18.7.2026): KEINE TABS MEHR.
# User-Vorgabe wörtlich: "das ganze Programm Dashboard [soll] nur aus
# dieser Fabrik bestehen, also eine interaktive Map [...] wenn man
# [Gegenstände] anklickt, [bekommt man] mehr Infos." Jeder frühere Tab
# lebt jetzt als Detailpanel einer Maschine (siehe docs/DESIGN_FABRIK.md,
# Vision W7-Fortschrittsliste). Übrig bleibt: ein immer sichtbarer HUD
# (Zahlen, die man nicht erst "entdecken" soll) + die Szene selbst.
# ═══════════════════════════════════════════════════════════════════════════════

# Kontext-Bündel für die ausgelagerten Detailpanel-Module (Roadmap 4.4a,
# Monolith-Split): alles, was bis hierher im Modul-Namensraum steht
# (broker/portfolio/config/Helper-Funktionen/…), automatisch statt
# einzeln durchgereicht — siehe dashboard/tabs/__init__.py-Docstring.
import types as _types
_ctx = _types.SimpleNamespace(**locals())

with st.sidebar:
    from dashboard.tabs import sidebar as _sidebar
    _sidebar.render(_ctx)

# ─── Kern-Status-Vollseite (W8.7, 20.7.2026, User-Vorgabe): Übergangs-
# lösung, solange der Fabrik-Umbau noch nicht jede Maschine mit einem
# vollen Detailpanel abdeckt — ?status=1 zeigt statt der Szene eine Seite
# mit Platz für ALLE Bot-Daten (dashboard/tabs/full_status.py). Sidebar
# bleibt erhalten (Pause-Schalter etc. sollen weiter erreichbar sein).
if st.query_params.get("status") == "1":
    from dashboard.tabs import full_status as _tab_full_status
    _tab_full_status.render(_ctx)
else:
    # ─── HUD: KPI-Leiste (Tab-Umbau 18.7.2026, aus dem früheren Portfolio-
    # Tab hierher — Depotwert/Cash/Regime/Win-Rate/Queue soll man nicht
    # erst anklicken müssen, um sie zu sehen). ────────────────────────────
    _hud_delta_pct = (total_value - config.initial_capital) / config.initial_capital * 100
    _hud_cash_pct = portfolio.cash / total_value * 100 if total_value else 0
    _hud_regime_str = (regime_data["regime"] if regime_data else "–")
    _hud_regime_score = (regime_data["recession_score"] if regime_data else None)

    _hk1, _hk2, _hk3, _hk4, _hk5, _hk6 = st.columns(6)
    _hk1.metric("Gesamtwert", f"${total_value:,.2f}", f"{_hud_delta_pct:+.1f}%")
    _hk2.metric("Cash", f"${portfolio.cash:,.2f}", f"{_hud_cash_pct:.0f}% des Portfolios")
    _hk3.metric("Offene Positionen", len(portfolio.all_positions()))
    _hk4.metric(
        "Marktregime", _hud_regime_str,
        f"Score {_hud_regime_score:.2f}" if _hud_regime_score is not None else "–",
        delta_color="inverse",
    )
    if acc.get("total_closed"):
        _hk5.metric("Win-Rate", f"{acc['win_rate_pct']}%", f"{acc['total_closed']} Trades")
    elif _rt_stats:
        _hk5.metric("Win-Rate", f"{_rt_stats['win_rate_pct']}%",
                    f"{_rt_stats['total_closed']} Trades (Portfolio-Historie)")
    else:
        _hk5.metric("Win-Rate", "–", "0 Trades")
    _hk6.metric("Signal-Warteschlange", f"{pending_cnt} ausstehend", delta_color="off")
    st.divider()

    # ─── Die Szene IST das Programm ──────────────────────────────────────
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
