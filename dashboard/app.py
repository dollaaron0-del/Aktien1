"""
Streamlit Dashboard für den Stock Sentiment Trading Bot.
Starten: python main.py --dashboard
     oder: streamlit run dashboard/app.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import pandas as pd
from datetime import datetime

from config import config
from broker.paper_broker import PaperBroker
from portfolio.portfolio import Portfolio
from portfolio.performance_tracker import PerformanceTracker
from portfolio.phase_controller import PhaseController

st.set_page_config(
    page_title="Stock Sentiment Bot",
    page_icon="📈",
    layout="wide",
)

st.title("📈 Stock Sentiment Trading Bot")
st.caption(f"Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')} | Broker: {config.broker_mode.upper()}")


@st.cache_resource
def load_resources():
    broker = PaperBroker()
    portfolio = Portfolio(config.initial_capital)
    tracker = PerformanceTracker()
    phase_ctrl = PhaseController(
        initial_capital=config.initial_capital,
        growth_target_multiple=config.growth_target_multiple,
        monthly_target_eur=config.monthly_distribution_eur,
        buffer_months=config.distribution_buffer_months,
    )
    return broker, portfolio, tracker, phase_ctrl


broker, portfolio, tracker, phase_ctrl = load_resources()

prices = broker.get_prices(list(portfolio.all_positions().keys()))
total_value = portfolio.total_value(prices)
phase_info = phase_ctrl.get_info(total_value)
acc = tracker.get_accuracy_report()

# ─── KPI Row ────────────────────────────────────────────────────────────────
col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    delta_abs = total_value - config.initial_capital
    delta_pct = delta_abs / config.initial_capital * 100
    st.metric("Gesamtwert", f"${total_value:,.2f}", f"{delta_pct:+.1f}%")

with col2:
    st.metric("Cash", f"${portfolio.cash:,.2f}")

with col3:
    st.metric("Offene Positionen", len(portfolio.all_positions()))

with col4:
    phase_label = "🌱 Wachstum" if phase_info["phase"] == "GROWTH" else "💸 Ausschüttung"
    st.metric("Phase", phase_label, f"{phase_info['progress_pct']:.1f}% zum Ziel")

with col5:
    if acc.get("total_closed", 0) > 0:
        st.metric("Win-Rate", f"{acc['win_rate_pct']}%", f"{acc['total_closed']} Trades")
    else:
        st.metric("Win-Rate", "–", "noch keine Trades")

st.divider()

# ─── Phase progress ──────────────────────────────────────────────────────────
st.subheader("Portfolio-Phase & Wachstumsziel")
left, right = st.columns([2, 1])

with left:
    progress = phase_info["progress_pct"] / 100
    st.progress(progress, text=f"{phase_info['progress_pct']:.1f}% — ${total_value:,.2f} von ${phase_info['growth_target']:,.0f}")

    if phase_info["phase"] == "GROWTH":
        st.info(
            f"🌱 **Wachstumsphase** – Noch **${phase_info['remaining_to_goal']:,.2f}** bis zur monatlichen Ausschüttung.\n\n"
            f"Ziel: ${phase_info['growth_target']:,.0f} ({config.growth_target_multiple:.1f}× Startkapital)"
        )
    else:
        dist = phase_info.get("monthly_distribution", 0)
        st.success(
            f"💸 **Ausschüttungsphase erreicht!**\n\n"
            f"Monatliche Auszahlung: **${dist:,.2f}** (Ziel: ${phase_info['monthly_target']:,.2f})\n"
            f"Sicherheitspuffer: ${phase_info.get('buffer_reserve', 0):,.2f} ({config.distribution_buffer_months} Monate)"
        )

with right:
    st.metric("Startkapital", f"${config.initial_capital:,.2f}")
    st.metric("Wachstumsziel", f"${phase_info['growth_target']:,.0f}")
    if phase_info["phase"] == "DISTRIBUTION":
        st.metric("Monatl. Ausschüttung", f"${phase_info.get('monthly_distribution', 0):,.2f}")

st.divider()

# ─── Open Positions ──────────────────────────────────────────────────────────
st.subheader("Offene Positionen")
positions = portfolio.all_positions()
if positions:
    rows = []
    for ticker, pos in positions.items():
        price = prices.get(ticker, pos.entry_price)
        pnl = (price - pos.entry_price) * pos.shares
        pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
        days = (datetime.utcnow() - datetime.fromisoformat(pos.entry_date)).days
        rows.append({
            "Ticker": ticker,
            "Stück": pos.shares,
            "Einstieg $": pos.entry_price,
            "Aktuell $": price,
            "P&L $": round(pnl, 2),
            "P&L %": round(pnl_pct, 2),
            "SL $": pos.stop_loss,
            "TP $": pos.take_profit,
            "Tage": days,
            "Halteziel": pos.target_hold_days,
        })
    df = pd.DataFrame(rows)

    def color_pnl(val):
        return "color: green" if val >= 0 else "color: red"

    st.dataframe(
        df.style.applymap(color_pnl, subset=["P&L $", "P&L %"]),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("Keine offenen Positionen.")

st.divider()

# ─── Portfolio History Chart ─────────────────────────────────────────────────
st.subheader("Portfoliowert über Zeit")
history = tracker.get_value_history(180)
if len(history) >= 2:
    df_hist = pd.DataFrame(history[::-1])
    df_hist["snapshot_date"] = pd.to_datetime(df_hist["snapshot_date"])
    df_hist = df_hist.set_index("snapshot_date")
    st.line_chart(df_hist["total_value"], use_container_width=True)
else:
    st.info("Noch zu wenige Datenpunkte für einen Chart (mindestens 2 Analysezyklen nötig).")

st.divider()

# ─── Learning / Accuracy ─────────────────────────────────────────────────────
st.subheader("Selbstlernbericht – Vorhersage-Genauigkeit")
if acc.get("total_closed", 0) == 0:
    st.info("Noch keine abgeschlossenen Trades. Der Bot lernt nach dem ersten Verkauf.")
else:
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Win-Rate", f"{acc['win_rate_pct']}%")
    mc2.metric("Richtungs-Genauigkeit", f"{acc['direction_accuracy_pct']}%")
    mc3.metric("Zielkurs-Trefferquote", f"{acc['target_hit_pct']}%")
    mc4.metric("Ø Rendite / Trade", f"{acc['avg_return_pct']:+.2f}%")

    adaptive_threshold = tracker.get_adaptive_threshold(config.buy_threshold)
    threshold_delta = adaptive_threshold - config.buy_threshold
    st.metric(
        "Adaptiver Kauf-Threshold",
        f"{adaptive_threshold:.2f}",
        f"{threshold_delta:+.2f} (Basis: {config.buy_threshold:.2f})",
        delta_color="inverse",
    )
    if threshold_delta > 0:
        st.warning("⚠️ Der Bot wurde konservativer: Win-Rate unter 50 %. Strengere Kaufbedingungen aktiv.")
    elif threshold_delta < 0:
        st.success("✅ Der Bot hat gute Trefferquote: Kaufbedingungen leicht gelockert.")

    st.subheader("Letzte abgeschlossene Trades")
    recent = tracker.get_recent_trades(15)
    if recent:
        df_trades = pd.DataFrame(recent)
        df_trades = df_trades.rename(columns={
            "ticker": "Ticker",
            "entry_price": "Einstieg $",
            "sell_price": "Verkauf $",
            "actual_return_pct": "Rendite %",
            "actual_hold_days": "Tage (Ist)",
            "predicted_hold_days": "Tage (Plan)",
            "predicted_target_price": "Zielkurs $",
            "direction_correct": "Richtung ✓",
            "target_hit": "Zielkurs ✓",
            "sell_reason": "Grund",
        })

        def bool_icon(val):
            return "✓" if val == 1 else "✗"

        df_trades["Richtung ✓"] = df_trades["Richtung ✓"].apply(bool_icon)
        df_trades["Zielkurs ✓"] = df_trades["Zielkurs ✓"].apply(bool_icon)

        def color_return(val):
            try:
                return "color: green" if float(val) >= 0 else "color: red"
            except Exception:
                return ""

        display_cols = ["Ticker", "Einstieg $", "Verkauf $", "Rendite %", "Tage (Ist)",
                        "Tage (Plan)", "Zielkurs $", "Richtung ✓", "Zielkurs ✓", "Grund"]
        existing_cols = [c for c in display_cols if c in df_trades.columns]
        st.dataframe(
            df_trades[existing_cols].style.applymap(color_return, subset=["Rendite %"]),
            use_container_width=True,
            hide_index=True,
        )

st.divider()

# ─── Trade History ───────────────────────────────────────────────────────────
st.subheader("Alle Transaktionen")
trades = portfolio.trade_history()
if trades:
    trade_rows = [
        {
            "Datum": t.timestamp[:10],
            "Ticker": t.ticker,
            "Aktion": t.action,
            "Stück": t.shares,
            "Kurs $": t.price,
            "P&L $": round(t.pnl, 2) if t.pnl else 0,
            "Grund": (t.reason or "")[:50],
        }
        for t in reversed(trades)
    ]
    df_all = pd.DataFrame(trade_rows)
    st.dataframe(df_all, use_container_width=True, hide_index=True)
else:
    st.info("Noch keine Transaktionen.")

# ─── Config sidebar ──────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Konfiguration")
    st.write(f"**Modell:** {config.claude_model}")
    st.write(f"**Watchlist:** {', '.join(config.watchlist)}")
    st.write(f"**Stop-Loss:** {config.stop_loss_pct*100:.0f}%")
    st.write(f"**Take-Profit:** {config.take_profit_pct*100:.0f}%")
    st.write(f"**Max. Position:** {config.max_position_pct*100:.0f}% des Portfolios")
    st.write(f"**Kauf-Threshold:** {config.buy_threshold:.2f}")
    st.write(f"**Min. Quellen:** {config.min_sources}")
    st.divider()
    st.write(f"**Wachstumsziel:** {config.growth_target_multiple:.1f}× Startkapital")
    st.write(f"**Monatl. Zielausschüttung:** ${config.monthly_distribution_eur:,.2f}")
    st.write(f"**Puffermonate:** {config.distribution_buffer_months}")
    if st.button("🔄 Daten neu laden"):
        st.cache_resource.clear()
        st.rerun()
