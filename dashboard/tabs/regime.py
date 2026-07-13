"""Tab "Markt-Regime" — ausgelagert aus dashboard/app.py (Roadmap 4.4a)."""
import json
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from analyzers.recession_detector import BULL, NEUTRAL


def render(ctx) -> None:
    regime_data = ctx.regime_data
    if not regime_data:
        st.info("Noch kein Regime-Snapshot. Läuft beim nächsten Analyse-Zyklus.")
        return

    regime = regime_data["regime"]
    score = regime_data["recession_score"]
    color = ctx._REGIME_COLOR.get(regime, "#888")

    # Top row
    r1, r2, r3, r4 = st.columns(4)
    r1.markdown(
        f"<div style='text-align:center;padding:10px;background:#1e2130;"
        f"border:2px solid {color};border-radius:12px;'>"
        f"<div style='font-size:2rem;font-weight:700;color:{color};'>{ctx._REGIME_ICON[regime]} {regime}</div>"
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
    _all_history_r = ctx.detector.get_history(365)
    _avail_days = 0
    if _all_history_r:
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

    history_r = ctx.detector.get_history(_reg_days)
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
        (t, p) for t, p in ctx.portfolio.all_positions().items()
        if p.rationale and p.rationale.startswith("[HEDGE]")
    ]
    if hedge_positions:
        hrows = []
        for ticker, pos in hedge_positions:
            price   = ctx.prices.get(ticker, pos.entry_price)
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
        enabled = ctx.config.enable_hedging
        if not enabled:
            st.warning("Hedging ist deaktiviert (ENABLE_HEDGING=false in .env)")
        elif regime in (BULL, NEUTRAL):
            st.success(f"Kein Hedge nötig – Regime ist {regime}.")
        else:
            st.info("Keine Hedge-Positionen offen (noch kein Kapital zugewiesen oder gerade geschlossen).")
