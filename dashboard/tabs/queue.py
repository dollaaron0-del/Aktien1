"""Tab "Signal-Queue" — ausgelagert aus dashboard/app.py (Roadmap 4.4a)."""
import pandas as pd
import streamlit as st


def render(ctx) -> None:
    # ── Analyse-Warteschlange (user_request_queue) ────────────────────────────
    st.subheader("🔍 Nächste Analyse-Runde")
    st.caption(
        "Ticker die vom Headline-Scanner, Geopolitik-Radar oder manuell vorgemerkt wurden "
        "und beim nächsten Analyse-Zyklus zusätzlich untersucht werden."
    )
    if ctx._analysis_queue:
        _aq_cols = st.columns(min(len(ctx._analysis_queue), 6))
        for _i, _aq_t in enumerate(ctx._analysis_queue):
            _aq_cols[_i % 6].info(f"🔍 **{_aq_t}**")
        st.caption(f"{len(ctx._analysis_queue)} Ticker warten auf Analyse – Queue wird beim nächsten Zyklus geleert.")
    else:
        st.success("Keine Ticker in der Analyse-Warteschlange.")

    from analyzers.user_request_queue import add_ticker as _urq_add
    _manual_ticker = st.text_input(
        "Ticker manuell zur Analyse vormerken",
        placeholder="z.B. CRM oder SAP.DE",
        key="manual_analysis_ticker",
    ).strip().upper()
    if st.button("➕ Zur nächsten Analyse hinzufügen", key="btn_add_analysis"):
        if _manual_ticker:
            _urq_add(_manual_ticker)
            st.success(f"{_manual_ticker} wird beim nächsten Zyklus analysiert.")
            st.rerun()
        else:
            st.warning("Bitte einen Ticker eingeben.")

    st.divider()

    # ── Bedingte Einstiege (Conditional Entries) ──────────────────────────────
    st.subheader("📌 Bedingte Einstiege")
    st.caption(
        "Aktien die Claude als SKIP bewertet hat, aber bei einem günstigeren "
        "Kurs automatisch gekauft werden. Der Bot prüft alle 15 Min ob der "
        "Trigger-Kurs erreicht wurde."
    )
    try:
        from analyzers.conditional_entry import ConditionalEntryWatcher
        _ce_watcher = ConditionalEntryWatcher()
        _ce_active = _ce_watcher.get_active()
        if _ce_active:
            _ce_prices = {
                e.ticker: ctx.broker.get_price(e.ticker) or e.price_at_creation
                for e in _ce_active
            }
            for ce in _ce_active:
                _cur = _ce_prices.get(ce.ticker, ce.price_at_creation)
                # Watcher löst auf Ausbruch nach oben aus (Kurs >= Trigger).
                # _pct_away = noch fehlende Distanz nach oben bis zum Trigger.
                _pct_away = (ce.trigger_price - _cur) / _cur * 100 if _cur else 0
                _triggered = _cur >= ce.trigger_price if _cur else False
                _icon = "🟢" if _triggered else ("🟡" if _pct_away < 3 else "⚪")
                with st.expander(
                    f"{_icon} **{ce.ticker}** – Trigger ${ce.trigger_price:.2f} "
                    f"· Aktuell ${_cur:.2f} "
                    f"· {'🔥 AUSGELÖST' if _triggered else f'{_pct_away:.1f}% entfernt'}"
                    f" · läuft ab {ce.expires_at[:10]}",
                    expanded=_triggered,
                ):
                    _cc1, _cc2, _cc3 = st.columns(3)
                    _cc1.metric("Trigger-Kurs",    f"${ce.trigger_price:.2f}",
                                f"{ce.pct_to_trigger:+.1f}% vs. Analyse-Kurs")
                    _cc2.metric("Aktueller Kurs",  f"${_cur:.2f}",
                                f"{'🔥 Trigger erreicht!' if _triggered else f'noch {_pct_away:.1f}% bis Trigger'}")
                    _cc3.metric("Kursziel (Claude)", f"${ce.target_price:.2f}" if ce.target_price else "–")
                    st.markdown(f"**Bull-Case:** {ce.bull_case}")
                    st.markdown(f"**Bear-Case:** {ce.bear_case}")
                    st.markdown(f"**Begründung:** {ce.entry_rationale}")
                    if ce.key_catalysts:
                        st.markdown("**Katalysatoren:** " + " · ".join(ce.key_catalysts))
                    if st.button(f"🗑 {ce.ticker} entfernen", key=f"rm_ce_{ce.ticker}"):
                        _ce_watcher.remove(ce.ticker)
                        st.rerun()
        else:
            st.info("Keine aktiven bedingten Einstiege.")
    except Exception as _ce_dash_err:
        st.caption(f"Conditional Entries nicht verfügbar: {_ce_dash_err}")

    st.divider()
    st.subheader("Ausstehende BUY-Signale")
    st.caption(
        "Wenn ein starkes BUY-Signal eintrifft aber kein Kapital frei ist, "
        "landet es hier. Sobald ein Trade geschlossen wird, wird das Signal automatisch ausgeführt."
    )

    pending = ctx.sig_queue.get_pending()
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
    history_q = ctx.sig_queue.get_history(20)
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
        st.dataframe(df_q, width="stretch", hide_index=True)
    else:
        st.info("Noch keine Signal-Historie.")
