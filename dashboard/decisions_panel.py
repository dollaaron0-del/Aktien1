"""Entscheidungs-Transparenz-Panel — bis 18.7.2026 eigener Tab, seit dem
Karten-Umbau (Vision W7) Teil des Förderband-Detailpanels (die kurze
Funnel-Zusammenfassung dort bleibt, das hier ist die volle Tiefe:
Tag-Auswahl, Skip-Gründe, Zeitraum-Vergleich, Signal-Queue)."""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from analyzers.analysis_log import AnalysisLog
from dashboard import theme as _theme
from dashboard.compare import week_stats


def _render_period_compare() -> None:
    """H2.4: Zeitraum-Vergleich — zwei Datums-Paare, aggregiert über
    dashboard.compare.week_stats() (read-only). Fail-open: ein Aggregat-
    Fehler zeigt nur eine leere Tabelle statt das Dashboard zu crashen."""
    with st.expander("📊 Zeitraum-Vergleich"):
        today = date.today()
        col_a, col_b = st.columns(2)
        with col_a:
            st.caption("Zeitraum A")
            a_start = st.date_input("Von (A)", value=today - timedelta(days=7),
                                    key="cmp_a_start")
            a_end = st.date_input("Bis (A)", value=today - timedelta(days=1),
                                  key="cmp_a_end")
        with col_b:
            st.caption("Zeitraum B")
            b_start = st.date_input("Von (B)", value=today - timedelta(days=14),
                                    key="cmp_b_start")
            b_end = st.date_input("Bis (B)", value=today - timedelta(days=8),
                                  key="cmp_b_end")

        try:
            stats_a = week_stats(a_start.isoformat(), a_end.isoformat())
            stats_b = week_stats(b_start.isoformat(), b_end.isoformat())
        except Exception:
            st.caption("Vergleich derzeit nicht verfügbar.")
            return

        _ROWS = [
            ("Entscheidungen gesamt", "total"),
            ("Käufe (BUY)", "buy"),
            ("Übersprungen (SKIP)", "skip"),
            ("Gehalten (HOLD)", "hold"),
            ("Analysen", "n_analyses"),
            ("Ø Sentiment", "avg_sentiment"),
        ]
        df = pd.DataFrame([
            {
                "Kennzahl": label,
                "A": stats_a.get(key, 0),
                "B": stats_b.get(key, 0),
                "Δ (A−B)": round(stats_a.get(key, 0) - stats_b.get(key, 0), 3),
            }
            for label, key in _ROWS
        ])
        st.dataframe(df, width="stretch", hide_index=True)


def render(ctx) -> None:
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
        "daten_gate":        "Daten-Qualitäts-Gate",
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

        # Förderband-Visual (Design D4): dieselben Funnel-Zahlen als
        # Vorzeige-Stück, oberhalb des Zahlen-Details. Nur bei aktivem
        # Theme; die Fortschrittsbalken darunter bleiben der plain-Fallback
        # UND das genaue Zahlen-Detail.
        if _theme.is_enabled():
            try:
                from dashboard.conveyor import build_conveyor_svg
                st.markdown(build_conveyor_svg(_fn), unsafe_allow_html=True)
            except Exception:
                pass

        if _fn["skip_reasons"]:
            st.markdown("**Warum wurde übersprungen?**")
            _sr_total = sum(_fn["skip_reasons"].values()) or 1
            for _b, _n in _fn["skip_reasons"].items():
                _pct = _n / _sr_total
                st.progress(_pct, text=f"{_BUCKET_LABELS.get(_b, _b)} — {_n}× ({_pct:.0%})")

        # H3.1: "Warum nicht?"-Explorer — beantwortet "warum hat der Bot X
        # nicht gekauft?" als Weichenstrecke statt Prosa-Text. Fail-open:
        # ein Lesefehler zeigt nur den Hinweis, nie eine Exception.
        try:
            _wn_tickers = sorted({e["ticker"] for e in _dlog_dash.get_day(_sel_day)})
        except Exception:
            _wn_tickers = []
        if _wn_tickers:
            with st.expander("🔎 Warum nicht? Explorer"):
                _wn_ticker = st.selectbox("Ticker", _wn_tickers, key="why_not_ticker")
                from dashboard.why_not import gate_trail, gate_trail_svg
                try:
                    _trail = gate_trail(_wn_ticker, _sel_day)
                except Exception:
                    _trail = []
                if not _trail:
                    st.caption("Keine Entscheidung für diesen Ticker an diesem Tag gefunden.")
                elif _theme.is_enabled():
                    st.markdown(gate_trail_svg(_trail), unsafe_allow_html=True)
                else:
                    _wn_icon = {"passed": "🟢", "blocked": "🔴", "unreached": "⚪", "result": "🔵"}
                    for _step in _trail:
                        _line = f"{_wn_icon.get(_step['status'], '•')} {_step['label']}"
                        if _step.get("reason"):
                            _line += f" — {_step['reason']}"
                        st.caption(_line)

        st.divider()

        # ── Einzel-Entscheidungen ───────────────────────────────────────────
        _dec_filter = st.multiselect(
            "Aktionen filtern", ["BUY", "SELL", "SKIP", "HOLD"],
            default=["BUY", "SELL", "SKIP", "HOLD"], key="dec_action_filter",
        )
        _dec_entries = [e for e in _dlog_dash.get_day(_sel_day)
                        if (e.get("action") or "").upper() in _dec_filter]
        st.caption(f"{len(_dec_entries)} Entscheidungen am {_sel_day}")
        # Geteilte Instanzen statt pro Zeile neu zu verbinden/konstruieren —
        # st.expander-Bodies laufen bei JEDEM Rerun, auch eingeklappt.
        _alog_shared = AnalysisLog()
        try:
            from analyzers.prompt_archive import PromptArchive
            from analyzers.claude_analyzer import ClaudeAnalyzer
            _parch_shared = PromptArchive()
            _analyzer_shared = ClaudeAnalyzer()
        except Exception:
            _parch_shared = _analyzer_shared = None
        for _e in _dec_entries[:100]:
            _a = (_e.get("action") or "?").upper()
            _icon = _ACTION_ICON.get(_a, "•")
            _sc = _e.get("sentiment_score")
            _sc_txt = f" · Score {_sc:.2f}" if isinstance(_sc, (int, float)) else ""
            with st.expander(
                f"{_icon} **{ctx.ticker_label(_e['ticker'])}** — {_a}{_sc_txt} "
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
                        _lk = _alog_shared.get_by_id(int(_e["analysis_id"]))
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
                        ctx.render_sources_breakdown(_lk.get("sources_breakdown"),
                                                     total=_lk.get("sources_used"))
                        # Entscheidungs-Replay (Roadmap 4.5): spielt die
                        # archivierte KI-Antwort (falls vorhanden – nur echte
                        # Claude-Aufrufe, 1.4d) durch die AKTUELLE Parsing-/
                        # Schwellen-Logik. Kein API-Call, kein Kostenrisiko.
                        try:
                            from analyzers.decision_replay import replay_analysis
                            _replay = replay_analysis(int(_e["analysis_id"]),
                                                      analysis_log=_alog_shared,
                                                      prompt_archive=_parch_shared,
                                                      analyzer=_analyzer_shared,
                                                      _original=_lk)
                        except Exception:
                            _replay = None
                        if _replay:
                            if _replay["changed"]:
                                st.warning(
                                    f"🔁 Replay mit aktuellem Code weicht ab: "
                                    f"damals **{_replay['original'].get('recommendation')}**, "
                                    f"heute **{_replay['replayed'].get('recommendation')}** "
                                    f"({', '.join(_replay['changed_fields'])})"
                                )
                            else:
                                st.caption(
                                    "🔁 Replay mit aktuellem Code: identisches Ergebnis"
                                )
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
                    "Details pro Aktie im Analysator-Panel der 🏭 Fabrik "
                    "(Klick auf den Claude-Analysator). Sobald der Bot "
                    "wieder läuft, erscheint hier zusätzlich der Strategie-Schritt "
                    "(gekauft / übersprungen + Grund)."
                )
        except Exception:
            pass

    st.divider()
    _render_period_compare()

    # ── Signal-Queue (Tab-Umbau 18.7.2026: früher eigener Tab) ────────────
    # Warteschlangen sind Teil des Entscheidungswegs: Analyse-Vormerkungen,
    # bedingte Einstiege und ausstehende BUY-Signale gehören inhaltlich
    # direkt hinter den Entscheidungs-Funnel.
    st.divider()
    st.header("📋 Signal-Queue")
    try:
        from dashboard import signal_queue_panel as _sq_panel
        _sq_panel.render(ctx)
    except Exception:
        st.caption("Signal-Queue derzeit nicht verfügbar.")
