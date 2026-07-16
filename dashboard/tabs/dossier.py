"""Tab "🗂 Kartei" — Personalakten je Aktie (Design-Roadmap L1.1–L1.3,
docs/FABRIK_LEBENDIG.md).

Bündelt alles, was das Programm je über eine Aktie gesammelt hat, auf
einem Blatt. Kern-Ehrlichkeitsregel: eine dünne Akte bleibt dünn sichtbar
— keine Platzhalter, kein erfundenes Füllmaterial. Reiner Leser
(dashboard/dossier.py), schreibt nirgends außer der bestehenden
Notiz-Mechanik (PositionNotes, wiederverwendet — kein zweiter Speicher).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import dossier as _dossier


def _ekg_chart(history: list) -> None:
    """L1.2: Score-EKG — Sentiment-Score über die Zeit, Punkte nach
    Empfehlung eingefärbt, Größe nach Confidence. Nutzt das registrierte
    "pixel"-Altair-Theme (D2.3), keine eigenen Farben hardcoden.
    Fail-open: ein Baufehler zeigt nur keinen Chart (die Tabelle
    darunter bleibt die verlässliche Quelle)."""
    rows = [r for r in history if r.get("sentiment_score") is not None]
    if not rows:
        return
    try:
        import altair as alt
        df = pd.DataFrame([{
            "analyzed_at": r["analyzed_at"],
            "sentiment_score": r["sentiment_score"],
            "recommendation": r.get("recommendation") or "?",
            "confidence": r.get("confidence") or "?",
        } for r in rows])
        df["analyzed_at"] = pd.to_datetime(df["analyzed_at"])
        chart = alt.Chart(df).mark_line(point=True).encode(
            x=alt.X("analyzed_at:T", title="Analysiert am"),
            y=alt.Y("sentiment_score:Q", title="Sentiment-Score",
                    scale=alt.Scale(domain=[0, 1])),
            color=alt.Color("recommendation:N", title="Empfehlung"),
            size=alt.Size("confidence:N", title="Konfidenz"),
            tooltip=["analyzed_at", "sentiment_score", "recommendation", "confidence"],
        )
        st.altair_chart(chart, width="stretch")
    except Exception:
        pass


def _render_akte(ticker: str) -> None:
    d = _dossier.dossier(ticker)
    profile = d["profile"]
    header = profile.get("company") or ticker
    st.subheader(f"🗂 {header} ({ticker})")
    if profile.get("sector"):
        st.caption(f"{profile['sector']} · {profile.get('industry', '–')}")

    trades = d["trades"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Analysen", len(d["history"]))
    c2.metric("Gelabelte Trades", trades["n_trades"])
    c3.metric("Gewinne/Verluste", f"{trades['wins']}/{trades['losses']}")
    c4.metric(
        "Ø Ergebnis",
        f"{trades['avg_pnl_pct']:+.1f}%" if trades["avg_pnl_pct"] is not None else "–",
    )

    if d["history"]:
        st.markdown("**Score-Verlauf**")
        _ekg_chart(d["history"])
    else:
        st.caption("Noch keine Analyse-Historie für diesen Ticker.")

    if d["themes"] or d["related"]:
        st.markdown("**Themen & Verwandte**")
        if d["themes"]:
            st.caption("Themen: " + ", ".join(d["themes"]))
        if d["related"]:
            links = " · ".join(
                f"[{t}](?dossier={t})" for t in d["related"]
            )
            st.markdown(f"Verwandte: {links}")

    if trades["rows"]:
        st.markdown("**Letzte Entscheidungen (gelabelt)**")
        st.dataframe(
            pd.DataFrame(trades["rows"][:10]),
            width="stretch", hide_index=True,
        )

    pulse = [p for p in d["news_pulse"] if p["count"] > 0]
    if pulse:
        st.markdown("**News-Puls (letzte 14 Tage mit Daten)**")
        st.bar_chart(pd.DataFrame(d["news_pulse"]).set_index("date")["count"])

    with st.expander("📝 Notiz"):
        st.caption("Nur für dich sichtbar — der Bot liest diese Notiz nicht.")
        try:
            from dashboard.position_notes import PositionNotes
            notes = PositionNotes()
            new_text = st.text_area(
                "Notiz", value=d["note"], key=f"dossier_note_{ticker}",
                label_visibility="collapsed",
            )
            if st.button("Speichern", key=f"dossier_note_save_{ticker}"):
                notes.set(ticker, new_text)
                st.success("Gespeichert.")
        except Exception:
            pass


def render(ctx) -> None:
    st.caption(
        "Was das Werk über jede Aktie weiß — Score-Verlauf, Bilanz, "
        "Themen-Verwandte, News-Puls. Eine dünne Akte bleibt dünn "
        "sichtbar, es wird nichts erfunden."
    )
    known = _dossier.all_known_tickers()
    if not known:
        st.info("Noch keine Analysen vorhanden — die Kartei füllt sich, sobald der Bot läuft.")
        return

    options = [f"{r['ticker']} — {r['n_analyses']} Analysen" for r in known]
    ticker_by_option = {opt: r["ticker"] for opt, r in zip(options, known)}

    default_idx = 0
    focused = st.query_params.get("dossier")
    if focused:
        for i, r in enumerate(known):
            if r["ticker"] == focused.upper():
                default_idx = i
                break

    choice = st.selectbox("Aktie wählen", options, index=default_idx, key="dossier_select")
    _render_akte(ticker_by_option[choice])
