"""Tab "Entscheidungs-Transparenz" — ausgelagert aus dashboard/app.py (Roadmap 4.4a)."""
import streamlit as st

from analyzers.analysis_log import AnalysisLog


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
                        ctx.render_sources_breakdown(_lk.get("sources_breakdown"),
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
