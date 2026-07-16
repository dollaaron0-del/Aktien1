"""Tab "Portfolio" — ausgelagert aus dashboard/app.py (Roadmap 4.4a)."""
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from analyzers.bot_scorer import MILESTONES, BotScorer, get_modifiers
from portfolio.goal_risk_assessor import CAUTION, DANGER, OK, UNREACHABLE, GoalRiskAssessor


def _render_position_notes(ticker_label, tickers) -> None:
    """H1.4: freies Notizfeld je offener Position — reine Gedächtnisstütze,
    der Bot liest sie nicht (keine Entscheidung hängt daran). Eigene
    Funktion (statt Inline-Code in render()), damit sie isoliert testbar
    ist, ohne die schweren render(ctx)-Abhängigkeiten zu brauchen.
    Fail-open: ein DB-Fehler blendet den Block einfach aus."""
    try:
        from dashboard.position_notes import PositionNotes
        notes = PositionNotes()
        for ticker in tickers:
            with st.expander(f"📝 Notiz — {ticker_label(ticker)}"):
                st.caption("Nur für dich sichtbar — der Bot liest diese Notiz nicht.")
                current = notes.get(ticker)
                new_text = st.text_area(
                    "Notiz", value=current, key=f"note_text_{ticker}",
                    label_visibility="collapsed",
                )
                if st.button("Speichern", key=f"note_save_{ticker}"):
                    notes.set(ticker, new_text)
                    st.success("Gespeichert.")
    except Exception:
        pass


def _render_shelf(positions, prices) -> None:
    """D8.3: Lager-Detailregal — Positionen als Kisten nach Sektor,
    nur im Pixel-Theme (plain behält die nüchterne Tabelle als einzige
    Darstellung). Fail-open: ein Fehler blendet das Regal einfach aus."""
    try:
        from dashboard import theme
        if not theme.is_enabled():
            return
        from dashboard.warehouse_shelf import shelf_data, shelf_svg
        groups = shelf_data(positions, prices)
        st.markdown(shelf_svg(groups), unsafe_allow_html=True)
    except Exception:
        pass


def _render_weekly_report_button() -> None:
    """H5.1: Wochen-Report als eigenständige HTML-Datei — archivierbar/
    teilbar auch ohne laufendes Dashboard. Fail-open: ein Baufehler
    blendet den Knopf einfach aus."""
    try:
        from dashboard.report import build_weekly_html
        from datetime import date
        today = date.today()
        html_report = build_weekly_html(today.isoformat())
        st.download_button(
            "📄 Wochen-Report (HTML)",
            data=html_report.encode("utf-8"),
            file_name=f"wochen_report_{today.isoformat()}.html",
            mime="text/html",
        )
    except Exception:
        pass


def render(ctx) -> None:
    phase_info = ctx.phase_info
    config = ctx.config
    total_value = ctx.total_value
    acc = ctx.acc
    portfolio = ctx.portfolio
    prices = ctx.prices

    _render_weekly_report_button()

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
        st.metric("Investiert",      f"${ctx.invested:,.2f}")

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
    _render_shelf(positions, prices)
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
                    f"⚠️ **{ctx.ticker_label(ticker)}** seit {days}d ohne Gewinn "
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

        _render_position_notes(ctx.ticker_label, positions.keys())
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
    history = ctx.tracker.get_value_history(_port_days)
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
        _spy_df = ctx._get_spy_benchmark(_port_days, _start_val)
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
