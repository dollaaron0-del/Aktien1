"""Tab "Analyse-Log" — ausgelagert aus dashboard/app.py (Roadmap 4.4a)."""
from datetime import datetime, timezone

import streamlit as st


def render(ctx) -> None:
    _ALL_NAMES = ctx._ALL_NAMES
    _SOURCE_NAMES = ctx._SOURCE_NAMES
    ticker_label = ctx.ticker_label

    from analyzers.analysis_log import AnalysisLog as _AnalysisLog
    _alog = _AnalysisLog()

    st.subheader("🔍 Analyse-Log – alle betrachteten Aktien")
    st.caption(
        "Jede Aktie die der Bot analysiert hat – egal ob gekauft, gehalten oder übersprungen. "
        "Hier siehst du das vollständige Vorgehen und die Begründung."
    )

    cur_stats  = _alog.get_current_stats()   # neueste Analyse pro Ticker
    hist_stats = _alog.get_stats()           # alle Einträge (inkl. Duplikate)
    last_cycle = set(_alog.get_last_cycle_tickers())

    if cur_stats.get("total", 0) > 0:
        st.caption("**Aktueller Stand** – neueste Analyse pro Aktie (keine Duplikate)")
        sc1, sc2, sc3, sc4, sc5 = st.columns(5)
        sc1.metric("Aktien beobachtet", cur_stats.get("total", 0))
        sc2.metric("🟢 Aktuell BUY",    cur_stats.get("buys", 0))
        sc3.metric("⏭ Aktuell SKIP",    cur_stats.get("skips", 0))
        sc4.metric("⏸ Aktuell HOLD",    cur_stats.get("holds", 0))
        sc5.metric("Ø Sentiment",       f"{cur_stats.get('avg_score', 0):.2f}")
        with st.expander(f"📊 Gesamthistorie ({hist_stats.get('total', 0)} Analyse-Einträge)", expanded=False):
            hc1, hc2, hc3, hc4 = st.columns(4)
            hc1.metric("BUY gesamt",  hist_stats.get("buys", 0))
            hc2.metric("SKIP gesamt", hist_stats.get("skips", 0))
            hc3.metric("HOLD gesamt", hist_stats.get("holds", 0))
            hc4.metric("Ø Sentiment", f"{hist_stats.get('avg_score', 0):.2f}")

        # ── Quellen-Health-Ampel (Roadmap 1.4e) ─────────────────────────────
        # Nutzt die bestehende source_health-Mechanik: welche Collectors
        # liefern real Beiträge, welche sind schwach oder tot?
        try:
            _sh = _alog.source_health(days=30)
        except Exception:
            _sh = None
        if _sh and (_sh["healthy"] or _sh["weak"] or _sh["dead"]):
            _n_dead = len(_sh["dead"])
            _sh_icon = "🔴" if _n_dead else ("🟡" if _sh["weak"] else "🟢")
            with st.expander(
                f"{_sh_icon} Quellen-Health — {len(_sh['healthy'])} gesund · "
                f"{len(_sh['weak'])} schwach · {_n_dead} tot "
                f"(letzte {_sh['days']} Tage)", expanded=False,
            ):
                if not _sh["reliable"]:
                    st.caption(
                        f"⚠ Nur {_sh['n_analyses']} Analysen im Zeitraum — "
                        "Aussage statistisch dünn (Bot pausiert?)."
                    )

                def _src_names(keys):
                    return ", ".join(_SOURCE_NAMES.get(k, k) for k in keys)
                if _sh["healthy"]:
                    st.markdown(f"🟢 **Gesund:** {_src_names(_sh['healthy'])}")
                if _sh["weak"]:
                    st.markdown(f"🟡 **Schwach** (<10 % der Analysen): "
                                f"{_src_names(_sh['weak'])}")
                if _sh["dead"]:
                    st.markdown(f"🔴 **Tot** (0 Treffer): {_src_names(_sh['dead'])}")
                    st.caption("Tote Quellen: API-Key fehlt, Quelle defekt — "
                               "oder Abschalt-Kandidat (Roadmap 2.4).")
        st.divider()

    # Alle bisher analysierten Ticker laden (für Queue-Logik)
    _all_log_tickers = sorted({e["ticker"] for e in _alog.get_recent(limit=2000)})
    _analyzed_set = set(_all_log_tickers)

    with st.form("log_filter_form"):
        fa, fb = st.columns([4, 2])
        with fa:
            filter_rec = st.multiselect(
                "Empfehlung filtern",
                ["BUY", "SKIP", "HOLD", "SELL"],
                default=["BUY", "SKIP", "HOLD", "SELL"],
            )
        with fb:
            log_limit = st.selectbox("Anzahl anzeigen", [50, 100, 200, 500], index=0)
            show_all_history = st.checkbox("Alle Einträge (inkl. Duplikate)", value=False,
                                           help="Zeigt jeden Analyse-Lauf einzeln, auch wenn eine Aktie mehrfach analysiert wurde.")

        ticker_search = st.text_input(
            "Aktie suchen oder zur Analyse vormerken",
            placeholder="Ticker oder Name, z.B. BYD, NVDA, Rheinmetall …",
            help="Sucht in Ticker-Symbol und Aktienname. Unbekannte Ticker werden beim nächsten Zyklus analysiert.",
        )
        _sc1, _sc2 = st.columns(2)
        _searched = _sc1.form_submit_button("🔍 Suchen / Anfragen", width="stretch")
        _reset = _sc2.form_submit_button("✖ Filter zurücksetzen", width="stretch")

    # ── Auswertung ───────────────────────────────────────────────────────────
    from analyzers.user_request_queue import add_ticker as _req_ticker, peek as _peek_requests

    _search_filter = "" if _reset else ticker_search.strip().upper()

    # Resolve to exact ticker if input matches a log ticker directly
    _active_ticker: str | None = None
    if _search_filter:
        if _search_filter in _analyzed_set:
            _active_ticker = _search_filter
        else:
            _exact = [t for t in _all_log_tickers if t.upper() == _search_filter]
            if _exact:
                _active_ticker = _exact[0]

    if _searched and _search_filter:
        # Check how many log entries match the search term (ticker or name substring)
        _log_matches = [
            t for t in _all_log_tickers
            if _search_filter in t.upper()
            or _search_filter in _ALL_NAMES.get(t.upper(), "").upper()
        ]
        if _log_matches:
            if len(_log_matches) == 1:
                st.info(f"**{ticker_label(_log_matches[0])}** — Ergebnis unten.")
            else:
                st.info(f"{len(_log_matches)} Aktien gefunden — Ergebnisse unten.")
        else:
            # No log match → queue the input as a ticker for analysis
            if _search_filter in _peek_requests():
                st.success(f"**{_search_filter}** ist bereits für den nächsten Zyklus vorgemerkt.")
            else:
                _req_ticker(_search_filter)
                st.success(
                    f"✅ **{_search_filter}** wurde zur Analyse-Queue hinzugefügt.  \n"
                    f"Der Bot analysiert ihn beim nächsten Zyklus (15:00 Uhr oder beim nächsten Start)."
                )

    # Pending-Queue anzeigen
    from analyzers.user_request_queue import peek as _peek_queue
    _pending = [e if isinstance(e, str) else e.get("ticker", str(e)) for e in _peek_queue()]
    if _pending:
        st.info(f"⏳ Warteschlange: **{', '.join(_pending)}** — werden beim nächsten Zyklus analysiert.")

    # Latest news for selected ticker
    if _active_ticker:
        _news = ctx._get_ticker_news(_active_ticker)
        if _news:
            with st.expander(f"📰 Aktuelle News — {ticker_label(_active_ticker)}", expanded=True):
                for _n in _news:
                    _title     = _n.get("title", "")
                    _publisher = _n.get("publisher", "")
                    _pub_ts    = _n.get("providerPublishTime") or _n.get("pubTime") or 0
                    _pub_str   = datetime.utcfromtimestamp(_pub_ts).strftime("%d.%m.%Y") if _pub_ts else ""
                    _sentiment = _n.get("overallSentiment", "")
                    _s_icon    = {"POSITIVE": "🟢", "NEGATIVE": "🔴", "NEUTRAL": "🟡"}.get(_sentiment, "")
                    st.markdown(f"{_s_icon} **{_title}**  \n_{_publisher}_ · {_pub_str}")

    # Dedupliziert (Standard) oder volle Historie
    if _active_ticker or show_all_history:
        entries = _alog.get_recent(limit=log_limit, ticker=_active_ticker)
    else:
        entries = _alog.get_latest_per_ticker(limit=log_limit)
    if filter_rec:
        entries = [e for e in entries if e["recommendation"] in filter_rec]
    # Substring-Filter: Ticker oder Name enthält Suchbegriff
    if _search_filter and not _active_ticker:
        entries = [
            e for e in entries
            if _search_filter in e["ticker"].upper()
            or _search_filter in _ALL_NAMES.get(e["ticker"].upper(), "").upper()
        ]

    # Vorherige Empfehlung für Trend-Pfeil vorabladen (nur wenn dedupliziert)
    _prev_rec: dict = {}
    if not show_all_history and not _active_ticker:
        for e in entries:
            t = e["ticker"]
            if t not in _prev_rec:
                _prev_rec[t] = _alog.get_prev_recommendation(t)

    if not entries:
        if _active_ticker:
            st.info(f"Noch keine Analyse für **{ticker_label(_active_ticker)}** vorhanden.")
        elif _search_filter:
            st.info(f"Keine Analyse-Einträge für **{_search_filter}** gefunden.")
        else:
            st.info("Noch keine Analysen gespeichert. Der Bot beginnt beim nächsten Zyklus.")
    else:
        _REC_ICON = {"BUY": "🟢", "SKIP": "⏭", "HOLD": "⏸", "SELL": "🔴"}
        _DIR_ICON = {"BULLISH": "📈", "NEUTRAL": "➡️", "BEARISH": "📉"}
        _today_str = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        for entry in entries:
            rec  = entry["recommendation"]
            icon = _REC_ICON.get(rec, "•")
            dir_icon = _DIR_ICON.get(entry["direction"], "")
            score = entry["sentiment_score"]
            conf  = entry["confidence"]
            ts_full = entry["analyzed_at"]
            ts    = ts_full[:16]

            # Alters-Badge: zeigt wie frisch die Analyse ist
            _entry_date = ts_full[:10]
            if entry["ticker"] in last_cycle:
                _age_badge = "🔵 Letzter Zyklus"
            elif _entry_date == _today_str:
                _age_badge = "🟢 Heute"
            elif _entry_date >= (datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0,minute=0,second=0) -
                                  __import__("datetime").timedelta(days=7)).strftime("%Y-%m-%d"):
                _age_badge = "🟡 Diese Woche"
            else:
                _age_badge = "⚪ Älter"

            # Trend-Pfeil: hat sich die Empfehlung geändert?
            _prev = _prev_rec.get(entry["ticker"])
            if _prev and _prev != rec:
                _trend = f" ↑ war {_prev}" if rec == "BUY" else f" ↓ war {_prev}" if rec == "SELL" else f" ↔ war {_prev}"
            else:
                _trend = ""

            name_suffix = f" ({_ALL_NAMES[entry['ticker'].upper()]})" if entry['ticker'].upper() in _ALL_NAMES else ""
            label = (
                f"{icon} **{entry['ticker']}{name_suffix}** · {dir_icon} {entry['direction']} "
                f"· Score {score:.2f} · {conf} · {ts}{_trend} · {_age_badge}"
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
                # Quellen-Provenienz (Roadmap 1.4a): was floss in diese Analyse ein?
                ctx.render_sources_breakdown(entry.get("sources_breakdown"),
                                             total=entry.get("sources_used"))
