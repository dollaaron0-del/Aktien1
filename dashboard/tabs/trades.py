"""Tab "Trades & Lernen" — ausgelagert aus dashboard/app.py (Roadmap 4.4a)."""
from datetime import datetime

import pandas as pd
import streamlit as st

from dashboard import theme as _theme

_THESIS_STATUS_LED = {"PROVEN": "ok", "ABANDONED": "err", "PENDING": "warn"}
_THESIS_STATUS_LABEL = {"PROVEN": "Bewiesen", "ABANDONED": "Verworfen", "PENDING": "Läuft"}


def _render_thesis_board() -> None:
    """H4.1: Thesen-Board — verbindet das Dashboard mit dem Nordstern
    (docs/VISION.md: Kante beweisen statt Rendite jagen). Fail-open: ein
    Lesefehler zeigt nur den Leerzustand statt zu crashen."""
    st.subheader("🎯 Thesen-Board")
    st.caption(
        "Erfolgs-/Abbruchkriterien je Strategie-These (Roadmap 6.10) — "
        "beweist die Kante, statt Rendite zu jagen."
    )
    from dashboard.thesis_board import default_criteria, thesis_rows
    try:
        rows = thesis_rows()
    except Exception:
        rows = []

    if not rows:
        crit = default_criteria()
        st.info(
            f"Noch keine These aktiv — Kriterien: {crit['n_min']} Trades / "
            f"{crit['time_budget_months']} Monate."
        )
        return

    for row in rows:
        status_label = _THESIS_STATUS_LABEL.get(row["status"], row["status"])
        if _theme.is_enabled():
            led = _theme.led(_THESIS_STATUS_LED.get(row["status"], "off"), status_label)
            st.markdown(f"**{row['name']}** — {led}", unsafe_allow_html=True)
        else:
            st.markdown(f"**{row['name']}** — {status_label}")
        if row["description"]:
            st.caption(row["description"])
        st.progress(
            row["time_progress"],
            text=(
                f"Zeit-Fortschritt: {row['months_elapsed']:.1f}/"
                f"{row['time_budget_months']} Monate (Ziel: {row['n_min']} Trades)"
            ),
        )
        if row["verdict_reason"]:
            st.caption(f"Verdikt: {row['verdict_reason']}")


def _render_paper_forward_curve() -> None:
    """H4.3: Paper-Forward-Fieberkurve — kumulierte Rendite über die
    echten abgeschlossenen Positionen. BEWUSST ohne Benchmark-Linie
    (siehe Moduldoc dashboard/paper_forward_curve.py: Buy&Hold-Vergleich
    bräuchte einen Live-Preis-Abruf, außerhalb des Dashboard-Scopes).
    Fail-open: ohne Daten nur ein Hinweis, nie eine Exception."""
    st.subheader("🌡️ Paper-Forward-Fieberkurve")
    st.caption(
        "Kumulierte Rendite der Paper-Forward-Strategie über die Zeit "
        "(ehrlich: ohne Buy&Hold-Vergleich — der bräuchte einen "
        "Live-Kursabruf, den dieses Dashboard-Modul bewusst nicht macht)."
    )
    from dashboard.paper_forward_curve import equity_curve
    try:
        rows = equity_curve()
    except Exception:
        rows = []

    if not rows:
        st.caption("Noch keine abgeschlossenen Paper-Forward-Positionen.")
        return

    if len(rows) < 30:
        st.caption(f"⚠️ Bilanz statistisch dünn (n={len(rows)}).")

    try:
        import altair as alt
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        chart = alt.Chart(df).mark_line(point=True).encode(
            x=alt.X("date:T", title="Exit-Datum"),
            y=alt.Y("cum_return:Q", title="Kumulierte Rendite"),
            tooltip=["date", "ticker", "strategy", "return_pct", "cum_return"],
        )
        if len(rows) < 30:
            # Halbtransparentes Warnband über die ganze Chart-Breite —
            # die dünne Stichprobe bleibt auch optisch sichtbar, nicht
            # nur im Caption-Text.
            band = alt.Chart(df).mark_rect(opacity=0.08).encode(
                x=alt.X("min(date):T"), x2=alt.X2("max(date):T"),
                y=alt.value(0), y2=alt.value(300),
            )
            chart = band + chart
        st.altair_chart(chart, width="stretch")
    except Exception:
        pass


def _render_calibration_curve() -> None:
    """H3.3: Kalibrier-Kurve live — Trefferquote je Konfidenz-Stufe aus
    den echten gelabelten Trades. Nutzt das registrierte Altair-Theme
    "pixel" (D2.3: keine eigenen Farben hardcoden). Fail-open: ohne
    Daten nur ein Hinweis, nie eine Exception."""
    st.subheader("📈 Kalibrier-Kurve")
    st.caption(
        "Trefferquote je Konfidenz-Stufe — zeigt auch, wo der Bot sich "
        "überschätzt (siehe Selbstlern-Fundament: Sentiment ist nicht "
        "monoton kalibriert)."
    )
    from dashboard.calibration_curve import confidence_win_rates
    try:
        rows = confidence_win_rates()
    except Exception:
        rows = []

    if not rows or all(r["n"] == 0 for r in rows):
        st.caption("Noch keine gelabelten Trades vorhanden.")
        return

    chart_rows = [r for r in rows if r["win_rate"] is not None]
    if chart_rows:
        try:
            import altair as alt
            df = pd.DataFrame(chart_rows)
            chart = alt.Chart(df).mark_bar().encode(
                x=alt.X("confidence", sort=["HIGH", "MEDIUM", "LOW"], title="Konfidenz"),
                y=alt.Y("win_rate", title="Trefferquote", scale=alt.Scale(domain=[0, 1])),
                tooltip=["confidence", "win_rate", "n"],
            )
            st.altair_chart(chart, width="stretch")
        except Exception:
            pass

    for row in rows:
        if row["n"] and row["n"] < 20:
            st.caption(f"⚠️ {row['confidence']}: Stichprobe dünn (n={row['n']}).")


def _render_learning_curve() -> None:
    """L6.1: Lernkurven-Wand — Entwicklung statt Ist-Stand: wird die
    Selbsteinschätzung über die Zeit besser, und wie wächst der
    Erfahrungsschatz? Fail-open: ohne Daten nur ein Hinweis."""
    st.subheader("🧱 Lernkurven-Wand")
    st.caption(
        "Was das Werk über die Zeit gelernt hat — Güte der "
        "Selbsteinschätzung (Kalibrierungs-Monitor) und Wachstum des "
        "Erfahrungsschatzes."
    )
    from dashboard.learning_curve import (
        MIN_POINTS_FOR_CURVE, calibration_history, experience_growth,
    )

    try:
        cal = calibration_history()
    except Exception:
        cal = []
    if not cal:
        st.caption("Noch keine Messpunkte des Kalibrierungs-Monitors.")
    elif len(cal) < MIN_POINTS_FOR_CURVE:
        # Zwei Punkte sind kein Trend — eine Linie dazwischen würde eine
        # Entwicklung suggerieren, die nicht belegt ist.
        st.caption(
            f"Erst {len(cal)} Messpunkt{'e' if len(cal) != 1 else ''} — die "
            f"Kurve entsteht, sobald der Monitor öfter gelaufen ist "
            f"(ab {MIN_POINTS_FOR_CURVE})."
        )
        st.table([
            {"Lauf": r["run_at"][:16].replace("T", " "), "n": r["n"],
             "Brier": r["brier"], "BSS": r["bss"], "AUC": r["auc"]}
            for r in cal
        ])
    else:
        try:
            import altair as alt
            df = pd.DataFrame([
                {"run_at": r["run_at"], "Wert": r[k], "Maß": k.upper()}
                for r in cal for k in ("brier", "bss", "auc")
                if r.get(k) is not None
            ])
            df["run_at"] = pd.to_datetime(df["run_at"])
            chart = alt.Chart(df).mark_line(point=True).encode(
                x=alt.X("run_at:T", title="Monitor-Lauf"),
                y=alt.Y("Wert:Q"),
                color=alt.Color("Maß:N"),
                tooltip=["run_at", "Maß", "Wert"],
            )
            st.altair_chart(chart, width="stretch")
            st.caption(
                "Brier niedriger = besser · BSS über 0 = besser als der "
                "naive Mittelwert · AUC über 0,5 = erkennt Muster."
            )
        except Exception:
            pass

    try:
        growth = experience_growth()
    except Exception:
        growth = []
    if not growth:
        st.caption("Noch keine gelabelten Erfahrungen.")
        return
    st.markdown("**Erfahrungsschatz**")
    try:
        import altair as alt
        gdf = pd.DataFrame(growth)
        gdf["date"] = pd.to_datetime(gdf["date"])
        gchart = alt.Chart(gdf).mark_area(opacity=0.6).encode(
            x=alt.X("date:T", title="Entscheidungstag"),
            y=alt.Y("total:Q", title="gelabelte Erfahrungen (kumuliert)"),
            tooltip=["date", "new", "total"],
        )
        st.altair_chart(gchart, width="stretch")
    except Exception:
        pass
    st.caption(
        f"{growth[-1]['total']} gelabelte Entscheidungen von "
        f"{growth[0]['date']} bis {growth[-1]['date']}. Zeitachse ist der "
        f"Entscheidungstag, nicht der Etikettier-Tag — alle Labels stammen "
        f"aus einem Backfill-Lauf und trügen sonst alle dasselbe Datum."
    )


def _render_filter_xray() -> None:
    """L6.2: Röntgenblick in den Lern-Filter — welche Merkmale er wie
    gewichtet. Der Hinweis auf die Stichprobengröße ist Pflicht, nicht
    Zierde: bei 6 Trades sind das Anhaltspunkte, keine Erkenntnisse.
    Fail-open: ohne Datei kein Panel."""
    from dashboard.filter_xray import feature_weights
    try:
        data = feature_weights()
    except Exception:
        return
    if not data["features"]:
        return
    st.subheader("🔬 Röntgenblick: Lern-Filter")
    n = data["trade_count"]
    st.caption(
        f"Gewichte, die der Lern-Filter aus echten Trade-Ausgängen gelernt "
        f"hat — gelernt aus erst **{n} Trade{'s' if n != 1 else ''}**, mit "
        f"Vorsicht zu lesen. Der Filter ist derselbe, der im Trockenlauf "
        f"„Lern-Filter AVOID" + "“ meldet."
    )
    try:
        import altair as alt
        df = pd.DataFrame(data["features"])
        chart = alt.Chart(df).mark_bar().encode(
            x=alt.X("weight:Q", title="Gewicht"),
            y=alt.Y("label:N", sort="-x", title=None),
            tooltip=["label", "weight"],
        )
        st.altair_chart(chart, width="stretch")
    except Exception:
        pass


def _render_genealogy() -> None:
    """H3.2: Entscheidungs-Genealogie — jede Order zurückverfolgt zu
    ihrer Analyse und deren Quellen. Fail-open: ein Lesefehler zeigt nur
    den Leerzustand statt zu crashen."""
    st.subheader("🧬 Entscheidungs-Genealogie")
    st.caption(
        "Jede Order rückwärts verfolgt: Order → Analyse → Quellen. Die "
        "Zuordnung Order→Analyse ist eine Heuristik (zeitlich "
        "nächstliegende Analyse desselben Tickers vor der Order)."
    )
    try:
        from broker.order_log import get_order_log
        _order_log = get_order_log()
        orders = _order_log.recent(limit=10)
    except Exception:
        orders, _order_log = [], None
    if not orders:
        st.caption("Noch keine Orders protokolliert.")
        return

    from dashboard.genealogy import lineage_svg, order_lineage
    for order in orders:
        label = f"{order.get('action', '?')} {order.get('ticker', '?')} · {str(order.get('ts', ''))[:16]}"
        with st.expander(label):
            try:
                # Denselben DB-Pfad wie der gerade genutzte OrderLog
                # verwenden (wichtig in Tests: get_order_log() ist
                # per Fixture auf eine Temp-DB isoliert, order_lineage()
                # muss dieselbe Datei lesen, nicht die echte data/).
                lineage = order_lineage(order["id"], order_db_path=_order_log._db_path)
            except Exception:
                lineage = {"order": None, "analysis": None, "sources": None}
            if _theme.is_enabled():
                st.markdown(lineage_svg(lineage), unsafe_allow_html=True)
            else:
                _analysis = lineage.get("analysis")
                st.caption(
                    f"Analyse: {_analysis.get('recommendation')} "
                    f"(Score {_analysis.get('sentiment_score')})"
                    if _analysis else "Analyse: (keine Analyse gefunden)"
                )
                _sources = lineage.get("sources") or {}
                if _sources:
                    st.caption("Quellen: " + ", ".join(
                        f"{s}×{n}" for s, n in _sources.items()
                    ))
                else:
                    st.caption("Quellen: (kein Breakdown gespeichert)")


def render(ctx) -> None:
    acc = ctx.acc
    portfolio = ctx.portfolio
    tracker = ctx.tracker
    config = ctx.config
    journal = ctx.journal
    reflection = ctx.reflection

    # Learning KPIs
    st.subheader("Performance-Kennzahlen")
    if acc.get("total_closed", 0) == 0:
        if ctx._rt_stats:
            st.caption(
                "ℹ️ Prediction-Tracking ist noch leer (füllt sich ab dem nächsten "
                "Kauf→Verkauf-Paar) — die Kennzahlen stammen direkt aus den echten "
                "Portfolio-Trades."
            )
            fk1, fk2, fk3, fk4 = st.columns(4)
            fk1.metric("Win-Rate",          f"{ctx._rt_stats['win_rate_pct']}%",
                       f"{ctx._rt_stats['total_closed']} Trades")
            fk2.metric("Ø Rendite / Trade", f"{ctx._rt_stats['avg_return_pct']:+.2f}%")
            fk3.metric("Realisiert gesamt", f"${ctx._rt_stats['total_pnl']:+,.2f}")
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
    _render_genealogy()
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
    _render_thesis_board()
    st.divider()
    _render_calibration_curve()
    st.divider()
    _render_learning_curve()
    st.divider()
    _render_filter_xray()
    st.divider()
    _render_paper_forward_curve()
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
