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
from datetime import datetime

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
from collectors.social_scan import SocialPulseDB
from analyzers.weekend_prep import WeekendPrep
from portfolio.goal_risk_assessor import GoalRiskAssessor, OK, CAUTION, DANGER, UNREACHABLE

# ─── Ticker → Firmenname ──────────────────────────────────────────────────────
_US_NAMES = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "TSLA": "Tesla",
    "AMZN": "Amazon", "GOOGL": "Alphabet", "GOOG": "Alphabet", "META": "Meta",
    "NFLX": "Netflix", "AMD": "AMD", "INTC": "Intel", "QCOM": "Qualcomm",
    "TSM": "TSMC", "ORCL": "Oracle", "CRM": "Salesforce", "ADBE": "Adobe",
    "SHOP": "Shopify", "SNOW": "Snowflake", "PLTR": "Palantir",
    "JPM": "JPMorgan", "BAC": "Bank of America", "GS": "Goldman Sachs",
    "V": "Visa", "MA": "Mastercard", "PYPL": "PayPal",
    "JNJ": "J&J", "PFE": "Pfizer", "MRK": "Merck", "ABBV": "AbbVie",
    "UNH": "UnitedHealth", "LLY": "Eli Lilly",
    "XOM": "ExxonMobil", "CVX": "Chevron",
    "SPY": "S&P 500 ETF", "QQQ": "Nasdaq ETF", "VTI": "Total Market ETF",
    "SH": "S&P 500 Inv.", "PSQ": "Nasdaq Inv.", "SQQQ": "Nasdaq 3× Inv.",
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana",
}
_EU_NAMES = {ticker: name for ticker, (name, *_) in EU_UNIVERSE.items()}
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
    social_db    = SocialPulseDB()
    weekend_prep = WeekendPrep(
        anthropic_api_key=config.anthropic_api_key,
        watchlist=config.watchlist,
    )
    return (broker, portfolio, tracker, phase_ctrl, focus_ctrl,
            journal, reflection, sig_queue, detector, social_db, weekend_prep)


(broker, portfolio, tracker, phase_ctrl, focus_ctrl,
 journal, reflection, sig_queue, detector, social_db, weekend_prep) = load_resources()

# ─── Live data ────────────────────────────────────────────────────────────────
prices      = broker.get_prices(list(portfolio.all_positions().keys()))
total_value = portfolio.total_value(prices)
phase_info  = phase_ctrl.get_info(total_value)
acc         = tracker.get_accuracy_report()
regime_data = detector.get_latest()
pending_cnt = sig_queue.count_pending()

# ─── Helper: regime color / label ────────────────────────────────────────────
_REGIME_COLOR = {BULL: "#00e676", NEUTRAL: "#ffd740", BEAR: "#ff7043", CRISIS: "#f44336"}
_REGIME_ICON  = {BULL: "🟢", NEUTRAL: "🟡", BEAR: "🟠", CRISIS: "🔴"}
_REGIME_CSS   = {BULL: "regime-bull", NEUTRAL: "regime-neutral", BEAR: "regime-bear", CRISIS: "regime-crisis"}


def regime_badge(regime: str) -> str:
    icon  = _REGIME_ICON.get(regime, "⚪")
    css   = _REGIME_CSS.get(regime, "")
    return f'<span class="{css}">{icon} {regime}</span>'


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
    if st.button("🔄 Aktualisieren", use_container_width=True):
        st.cache_resource.clear()
        st.rerun()

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
k5.metric(
    "Win-Rate",
    f"{acc['win_rate_pct']}%" if acc.get("total_closed") else "–",
    f"{acc.get('total_closed', 0)} Trades",
)
k6.metric(
    "Signal-Warteschlange",
    f"{pending_cnt} ausstehend",
    delta_color="off",
)

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════════════════════════════════════
tab_portfolio, tab_regime, tab_queue, tab_social, tab_briefing, tab_trades, tab_tech, tab_watchlist, tab_log = st.tabs([
    "📊 Portfolio",
    "🛡 Markt-Regime",
    f"📋 Signal-Queue ({pending_cnt})",
    "📡 Social Pulse",
    "📰 Wochenbriefing",
    "📈 Trades & Lernen",
    "📉 Technicals",
    "🔭 Watchlist",
    "🔍 Analyse-Log",
])


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
        for ticker, pos in positions.items():
            price   = prices.get(ticker, pos.entry_price)
            pnl     = (price - pos.entry_price) * pos.shares
            pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
            days    = (datetime.utcnow() - datetime.fromisoformat(pos.entry_date)).days
            is_hedge = pos.rationale and pos.rationale.startswith("[HEDGE]")
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
                "Tage":       days,
                "Katalysatoren": ", ".join(pos.entry_catalysts[:2]) if pos.entry_catalysts else "–",
            })
        df = pd.DataFrame(rows)

        def _color_pnl(val):
            if isinstance(val, (int, float)):
                return "color: #00e676" if val >= 0 else "color: #f44336"
            return ""

        st.dataframe(
            df.style.map(_color_pnl, subset=["P&L $", "P&L %"]),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("Keine offenen Positionen.")

    st.divider()

    # Portfolio value chart
    st.subheader("Portfoliowert – Verlauf")
    _port_range = st.radio(
        "Zeitraum", ["1 Tag", "1 Woche", "1 Monat", "Alles"],
        horizontal=True, key="port_range", index=3,
    )
    _port_days = {"1 Tag": 1, "1 Woche": 7, "1 Monat": 30, "Alles": 180}[_port_range]
    history = tracker.get_value_history(_port_days)
    if len(history) >= 2:
        df_hist = pd.DataFrame(history[::-1])
        df_hist["snapshot_date"] = pd.to_datetime(df_hist["snapshot_date"])
        df_hist = df_hist.set_index("snapshot_date")
        st.line_chart(df_hist["total_value"], use_container_width=True)
    else:
        st.info("Mindestens 2 Analysezyklen nötig.")

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
            st.dataframe(pd.DataFrame(trade_rows), use_container_width=True, hide_index=True)
        else:
            st.info("Noch keine Transaktionen.")


# ══════════════════════════════════════════════════════════
# TAB 2 – MARKT-REGIME
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

        st.divider()

        # Score gauge (progress bar)
        st.subheader("Rezessions-Score-Gauge")
        score_pct = score
        bar_color = color
        st.markdown(
            f"""<div style='background:#1e2130;border-radius:8px;padding:6px;'>
            <div style='background:{bar_color};width:{score_pct*100:.1f}%;height:22px;
                        border-radius:6px;transition:width 0.4s;'></div>
            </div>
            <div style='display:flex;justify-content:space-between;font-size:0.75rem;color:#888;margin-top:2px;'>
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
            "vix":            ("VIX – Angst-Index",         "30%"),
            "yield_curve":    ("Zinskurve (2y vs 10y)",      "25%"),
            "sp500_ma200":    ("S&P 500 vs 200-Tage-MA",     "20%"),
            "sector_breadth": ("Marktbreite (Sektor-Trends)","15%"),
            "credit_spread":  ("Credit Spread (HYG/IEI)",    "10%"),
            "claude_macro":   ("Claude Makro-Analyse",        "20%*"),
        }
        for key, (name, weight) in labels.items():
            comp = components.get(key, {})
            if not comp:
                continue
            score_c = comp.get("score", 0)
            label_c = comp.get("label", "")
            # Build detail string
            detail = label_c
            if key == "vix" and comp.get("value"):
                detail = f"VIX={comp['value']:.1f} · {label_c}"
            elif key == "yield_curve" and comp.get("spread_pct") is not None:
                detail = f"Spread={comp['spread_pct']:.2f}% · {label_c}"
            elif key == "sp500_ma200" and comp.get("gap_pct") is not None:
                detail = f"Gap={comp['gap_pct']:+.1f}% · {label_c}"
            elif key == "sector_breadth":
                detail = comp.get("label", "")
            elif key == "claude_macro":
                detail = comp.get("summary", "")[:80]
            comp_rows.append({
                "Signal":   name,
                "Gewicht":  weight,
                "Score":    round(score_c, 3),
                "Detail":   detail,
            })

        if comp_rows:
            df_comp = pd.DataFrame(comp_rows)
            st.dataframe(
                df_comp.style.background_gradient(subset=["Score"], cmap="RdYlGn_r", vmin=0, vmax=1),
                use_container_width=True, hide_index=True,
            )

        # Macro summary from Claude
        macro_sum = regime_data.get("macro_summary", "")
        if macro_sum:
            st.info(f"**Claude Makro-Einschätzung:** {macro_sum}")

        st.divider()

        # Regime history chart
        st.subheader("Regime-Verlauf")
        _reg_range = st.radio(
            "Zeitraum", ["1 Woche", "2 Wochen", "1 Monat"],
            horizontal=True, key="reg_range", index=2,
        )
        _reg_days = {"1 Woche": 7, "2 Wochen": 14, "1 Monat": 30}[_reg_range]
        history_r = detector.get_history(_reg_days)
        if len(history_r) >= 2:
            df_r = pd.DataFrame(history_r[::-1])
            df_r["recorded_at"] = pd.to_datetime(df_r["recorded_at"])
            df_r = df_r.set_index("recorded_at")
            st.line_chart(df_r["recession_score"], use_container_width=True)
            # Regime breakdown table
            with st.expander("Datentabelle"):
                st.dataframe(
                    df_r[["recession_score", "regime", "vix", "yield_spread"]].rename(columns={
                        "recession_score": "Score", "regime": "Regime",
                        "vix": "VIX", "yield_spread": "Zinskurve",
                    }),
                    use_container_width=True,
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
                days    = (datetime.utcnow() - datetime.fromisoformat(pos.entry_date)).days
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
            st.dataframe(pd.DataFrame(hrows), use_container_width=True, hide_index=True)
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
        st.dataframe(df_q, use_container_width=True, hide_index=True)
    else:
        st.info("Noch keine Signal-Historie.")


# ══════════════════════════════════════════════════════════
# TAB 4 – SOCIAL PULSE
# ══════════════════════════════════════════════════════════
with tab_social:
    st.subheader("Social-Media Sentiment (letzte 6 Stunden)")
    st.caption("Stündlicher Scan von Reddit & StockTwits – keyword-basiertes Sentiment ohne Claude-Kosten.")

    # Time window selector
    tw_col, _ = st.columns([2, 6])
    with tw_col:
        hours_sel = st.selectbox("Zeitraum", [2, 6, 12, 24], index=1,
                                  format_func=lambda h: f"Letzte {h}h")

    # Spikes
    spikes = social_db.get_spikes(hours=2, min_mentions=3)
    if spikes:
        st.markdown("### 🚨 Aktivitäts-Spikes (2h vs. 12h Baseline)")
        spike_cols = st.columns(min(len(spikes), 4))
        for i, spike in enumerate(spikes[:4]):
            pulse  = spike["avg_score"]
            p_icon = "📈" if pulse > 0.1 else ("📉" if pulse < -0.1 else "➡️")
            spike_cols[i].metric(
                label=spike["ticker"],
                value=f"{spike['spike_ratio']:.1f}× Spike",
                delta=f"{p_icon} Pulse {pulse:+.2f}",
                delta_color="normal" if pulse >= 0 else "inverse",
            )
        st.divider()

    # Full pulse table
    pulse_data = social_db.get_pulse_summary(hours=int(hours_sel))
    if pulse_data:
        st.subheader(f"Alle gescannten Ticker (letzte {hours_sel}h)")
        p_rows = []
        for p in pulse_data:
            pulse  = p["avg_score"]
            total  = p["total_mentions"]
            bull   = p.get("bull", 0)
            bear   = p.get("bear", 0)
            p_rows.append({
                "Ticker":        p["ticker"],
                "Erwähnungen":   total,
                "🟢 Bullish":    bull,
                "🔴 Bearish":    bear,
                "Pulse-Score":   round(pulse, 3),
                "Letzte Messung": p["latest"][:16] if p.get("latest") else "–",
            })
        df_pulse = pd.DataFrame(p_rows)
        st.dataframe(
            df_pulse.style.background_gradient(
                subset=["Pulse-Score"], cmap="RdYlGn", vmin=-1, vmax=1
            ),
            use_container_width=True, hide_index=True,
        )

        # Per-ticker detail
        st.divider()
        st.subheader("Ticker-Detail")
        selected_ticker = st.selectbox("Ticker auswählen", [p["ticker"] for p in pulse_data])
        if selected_ticker:
            recent_scans = social_db.get_recent(selected_ticker, hours=24)
            if recent_scans:
                scan_rows = []
                for r in recent_scans:
                    top = json.loads(r.get("top_mentions") or "[]") if isinstance(r.get("top_mentions"), str) else (r.get("top_mentions") or [])
                    scan_rows.append({
                        "Zeit":       r["scanned_at"][:16],
                        "Quelle":     r["source"],
                        "Erwähnungen": r["mention_count"],
                        "Bullish":    r["bullish_count"],
                        "Bearish":    r["bearish_count"],
                        "Pulse":      r["pulse_score"],
                        "Top-Posts":  " | ".join(top[:2]),
                    })
                st.dataframe(pd.DataFrame(scan_rows), use_container_width=True, hide_index=True)
            else:
                st.info("Keine Scan-Daten für diesen Ticker im Zeitraum.")
    else:
        st.info(
            "Noch keine Social-Pulse-Daten. "
            "Der stündliche Scan schreibt Daten sobald der Bot läuft "
            "(ENABLE_SOCIAL_SCAN=true in .env)."
        )


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
        if st.button("🔄 Neues Briefing generieren", use_container_width=True):
            with st.spinner("Claude bereitet Wochenbriefing vor…"):
                result = weekend_prep.generate_briefing(newsapi_key=config.newsapi_key)
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
                    use_container_width=True, hide_index=True,
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
                st.dataframe(df_bkt, use_container_width=True, hide_index=True)

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
                use_container_width=True, hide_index=True,
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
            for col in ["Richtung ✓", "Zielkurs ✓"]:
                if col in df_tr.columns:
                    df_tr[col] = df_tr[col].apply(lambda v: "✓" if v == 1 else "✗")
            desired = ["Ticker", "Einstieg $", "Verkauf $", "Rendite %", "Tage (Ist)",
                       "Tage (Plan)", "Zielkurs $", "Richtung ✓", "Zielkurs ✓", "Exit-Typ", "Grund"]
            existing = [c for c in desired if c in df_tr.columns]
            st.dataframe(
                df_tr[existing].style.map(
                    lambda v: ("color: #00e676" if isinstance(v, (int, float)) and v >= 0
                               else ("color: #f44336" if isinstance(v, (int, float)) and v < 0 else "")),
                    subset=["Rendite %"],
                ),
                use_container_width=True, hide_index=True,
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
        if st.button("🔄 Jetzt generieren", use_container_width=True):
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
            st.dataframe(df_tech, use_container_width=True)


# ══════════════════════════════════════════════════════════
# TAB 8 – DYNAMISCHE WATCHLIST
# ══════════════════════════════════════════════════════════
with tab_watchlist:
    st.subheader("🔭 Dynamische Watchlist")
    st.caption(
        "Der Bot scannt täglich ~80 Aktien und wählt automatisch die vielversprechendsten aus. "
        "Scoring: Volumen (30%) + Momentum (25%) + RSI (25%) + MACD (20%)"
    )

    _dw = DynamicWatchlist(max_picks=config.scan_max_picks or 12)

    wl_col1, wl_col2 = st.columns([4, 1])
    with wl_col2:
        if st.button("🔄 Jetzt neu scannen", use_container_width=True):
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
            use_container_width=True,
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
        "Regierungsaufträge aufgefallen sind. Werden temporär (7 Tage) beobachtet."
    )
    _expander = SignalDrivenExpander()
    sig_entries = _expander.get_all_entries()
    if sig_entries:
        df_sig = pd.DataFrame(sig_entries).rename(columns={
            "ticker":     "Ticker",
            "reason":     "Signal-Grund",
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
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info(
            "Noch keine Signal-Ticker entdeckt.  \n"
            "Der Bot erkennt automatisch unbekannte Aktien aus Insider-Trades, "
            "Social-Spikes und Options-Flow während des Betriebs."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## ⚙️ Bot-Status")
    st.caption(f"Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

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
            st.metric("Heute Claude-Kosten",   f"${cost_summary['today_cost_usd']:.2f}")
            st.metric("Gesamt Claude-Kosten",  f"${cost_summary['total_cost_usd']:.2f}")
        with c2:
            st.metric("Ollama-Skips gesamt",   cost_summary["ollama_skips"],
                      help="Analysen die Claude nicht gerufen haben")
            st.metric("Heute gespart",         f"${cost_summary['today_saved_usd']:.2f}")
            st.metric("Gesamt gespart",        f"${cost_summary['total_saved_usd']:.2f}")

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
    st.write(f"**Social Scan:** {'✓' if config.enable_social_scan else '✗'}")
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
        "Twitter/X Sentiment",
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

    st.divider()
    if st.button("🔄 Cache leeren & neu laden", use_container_width=True):
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

    stats = _alog.get_stats()
    if stats.get("total", 0) > 0:
        sc1, sc2, sc3, sc4, sc5 = st.columns(5)
        sc1.metric("Analysen gesamt",  stats.get("total", 0))
        sc2.metric("🟢 BUY",           stats.get("buys", 0))
        sc3.metric("⏭ SKIP",           stats.get("skips", 0))
        sc4.metric("⏸ HOLD",           stats.get("holds", 0))
        sc5.metric("Ø Sentiment",      f"{stats.get('avg_score', 0):.2f}")
        st.divider()

    # Autocomplete: alle bisher analysierten Ticker laden
    _all_log_tickers = sorted({e["ticker"] for e in _alog.get_recent(limit=2000)})

    with st.form("log_filter_form"):
        f1, f2, f3 = st.columns([3, 3, 2])
        with f1:
            filter_rec = st.multiselect(
                "Empfehlung",
                ["BUY", "SKIP", "HOLD", "SELL"],
                default=["BUY", "SKIP", "HOLD", "SELL"],
            )
        with f2:
            _ticker_opts = ["Alle"] + _all_log_tickers
            filter_ticker_sel = st.selectbox(
                "Ticker",
                _ticker_opts,
                format_func=lambda t: ticker_label(t) if t != "Alle" else "— Alle —",
            )
        with f3:
            log_limit = st.selectbox("Anzahl", [50, 100, 200, 500], index=0)
        _searched = st.form_submit_button("🔍 Suchen", use_container_width=True)

    _filter_ticker = None if filter_ticker_sel == "Alle" else filter_ticker_sel
    entries = _alog.get_recent(limit=log_limit, ticker=_filter_ticker)
    if filter_rec:
        entries = [e for e in entries if e["recommendation"] in filter_rec]

    if not entries:
        st.info("Noch keine Analysen gespeichert. Morgen früh ab 07:30 Uhr beginnt der Bot.")
    else:
        _REC_ICON = {"BUY": "🟢", "SKIP": "⏭", "HOLD": "⏸", "SELL": "🔴"}
        _DIR_ICON = {"BULLISH": "📈", "NEUTRAL": "➡️", "BEARISH": "📉"}

        for entry in entries:
            rec  = entry["recommendation"]
            icon = _REC_ICON.get(rec, "•")
            dir_icon = _DIR_ICON.get(entry["direction"], "")
            score = entry["sentiment_score"]
            conf  = entry["confidence"]
            ts    = entry["analyzed_at"][:16]

            name_suffix = f" ({_ALL_NAMES[entry['ticker'].upper()]})" if entry['ticker'].upper() in _ALL_NAMES else ""
            label = (
                f"{icon} **{entry['ticker']}{name_suffix}** · {dir_icon} {entry['direction']} "
                f"· Score {score:.2f} · {conf} · {ts}"
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
