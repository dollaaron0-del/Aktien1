"""Tab "Technische Indikatoren" — ausgelagert aus dashboard/app.py (Roadmap 4.4a)."""
import pandas as pd
import streamlit as st

from analyzers.technical_indicators import TechnicalIndicators


def render(ctx) -> None:
    st.subheader("Technische Indikatoren – Watchlist")
    st.caption("RSI, MACD, Bollinger Bands, EMAs und ATR für alle beobachteten Aktien.")

    _ti = TechnicalIndicators()

    selected_ticker = st.selectbox(
        "Ticker auswählen",
        options=ctx.config.watchlist,
        format_func=ctx.ticker_label,
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
        for t in ctx.config.watchlist:
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
