"""Sidebar — ausgelagert aus dashboard/app.py (Roadmap 4.4a)."""
from datetime import datetime

import streamlit as st

from portfolio.focus_mode import FocusMode


def render(ctx) -> None:
    config = ctx.config
    portfolio = ctx.portfolio
    regime_data = ctx.regime_data
    ticker_label = ctx.ticker_label

    st.markdown("## ⚙️ Bot-Status")
    st.caption(f"Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    # Nächste Analyse: echter Marktkalender (Börsen + Vorlauf) statt festem
    # 07:30-Countdown; bei pausiertem Bot gibt es schlicht keine nächste Analyse.
    if ctx._hdr_paused:
        st.error("⏸ Bot pausiert – keine geplanten Analysen.")
    else:
        try:
            from analyzers.market_schedule import MarketSchedule as _MS
            _nxt = _MS(config.market_exchanges, config.market_lead_minutes).next_window()
            if _nxt:
                st.info(f"⏰ Nächste Analyse: **{_nxt['analysis_local']}**")
            else:
                st.info("⏰ Kein Handelstag – nächste Analyse am nächsten Börsentag.")
        except Exception:
            st.caption("Analyse-Zeitplan nicht verfügbar.")

    # Auto-Refresh: läuft als Fragment-Timer, stößt regelmäßig einen kompletten
    # Seiten-Rerun an (Streamlit lädt sonst nur bei Interaktion neu).
    st.toggle("🔄 Auto-Refresh (60 s)", value=False, key="auto_refresh")

    # Live regime badge
    if regime_data:
        r   = regime_data["regime"]
        sc  = regime_data["recession_score"]
        col = ctx._REGIME_COLOR.get(r, "#888")
        st.markdown(
            f"**Regime:** <span style='color:{col};font-weight:700;'>"
            f"{ctx._REGIME_ICON[r]} {r}</span> (Score: {sc:.2f})",
            unsafe_allow_html=True,
        )
    else:
        st.markdown("**Regime:** – (noch kein Snapshot)")

    st.markdown(f"**Offene Positions:** {len(portfolio.all_positions())}")
    st.markdown(f"**Signal-Queue:** {ctx.pending_cnt} ausstehend")
    st.divider()

    # ── Pause-Schalter ──────────────────────────────────────────────────────────
    # Hält den Bot vorübergehend KOMPLETT an (alle Jobs inkl. SL/TP-Überwachung).
    # Der systemd-Service läuft weiter; nach dem Deaktivieren nimmt der Bot die
    # Arbeit beim nächsten Schleifendurchlauf (max. 60 Sek.) automatisch wieder auf.
    from system import bot_control as _bot_control
    _pause_status = _bot_control.get_status()
    _is_paused = _pause_status["paused"]

    if _is_paused:
        _since_txt = ""
        if _pause_status.get("since"):
            try:
                _since_dt = datetime.fromisoformat(_pause_status["since"])
                _since_txt = f" seit {_since_dt.strftime('%d.%m. %H:%M')}"
            except Exception:
                pass
        st.error(f"⏸ **Bot ist pausiert**{_since_txt} – es laufen keine Jobs (auch keine SL/TP-Überwachung).")
    else:
        st.success("▶️ Bot läuft – alle Jobs aktiv.")

    _new_paused = st.toggle(
        "⏸ Bot pausieren (kompletter Stopp)",
        value=_is_paused,
        key="bot_pause_toggle",
        help="Hält ALLE Bot-Aktivitäten an, inkl. Stop-Loss/Take-Profit. "
             "Offene Positionen werden während der Pause NICHT automatisch abgesichert. "
             "Greift innerhalb von max. 60 Sekunden.",
    )
    if _new_paused != _is_paused:
        _bot_control.set_paused(_new_paused, by="dashboard")
        if _new_paused:
            st.warning("Bot wird angehalten … (wirkt in max. 60 Sek.)")
        else:
            st.info("Bot wird fortgesetzt … (wirkt in max. 60 Sek.)")
        st.rerun()
    st.divider()

    # Focus mode
    st.markdown("### 🎯 Fokus-Modus")
    fm_info   = ctx.focus_ctrl.get_info(ctx.total_value)
    scale_info = ctx.focus_ctrl.scaling_info(ctx.total_value)
    st.markdown(f"**{fm_info['label']}**")
    st.caption(fm_info["description"])
    st.write(f"SL: **{fm_info['stop_loss_pct']*100:.0f}%** · TP: **{fm_info['take_profit_pct']*100:.0f}%**")
    st.write(f"Max Position: **{scale_info['max_position_pct']*100:.0f}%** (${scale_info['max_position_usd']:,.0f}) · Halt: **{fm_info['preferred_hold_days']}d**")
    st.write(f"Max Positionen: **{scale_info['max_positions']}** · Min Sentiment: **{fm_info['min_sentiment']:.2f}**")

    open_count = len(portfolio.all_positions())
    slots_free = scale_info["max_positions"] - open_count
    st.progress(
        min(open_count / scale_info["max_positions"], 1.0),
        text=f"Positionen: {open_count}/{scale_info['max_positions']} ({slots_free} frei)"
    )

    if fm_info["mode"] == FocusMode.TARGET_GOAL and fm_info.get("target_amount"):
        st.progress(
            fm_info["progress_pct"] / 100,
            text=f"{fm_info['progress_pct']:.1f}% · noch {fm_info['days_remaining']} Tage",
        )
    st.divider()

    # ── Ollama / API-Kosten ────────────────────────────────────────────────────
    st.markdown("### 🤖 KI-Backend & Kosten")
    try:
        from analyzers.api_cost_tracker import APICostTracker
        cost_summary = APICostTracker().summary()
        ollama_active = config.ollama_enabled

        if ollama_active:
            st.success(f"Ollama aktiv: `{config.ollama_model}`")
        else:
            st.info("Nur Claude API (Ollama deaktiviert)")

        c1, c2 = st.columns(2)
        with c1:
            st.metric("Claude-Aufrufe gesamt", cost_summary["claude_calls"])
            st.metric(f"Heute Claude-Kosten (Limit {cost_summary.get('daily_limit_eur', 1.0):.2f}€)",
                      f"{cost_summary['today_cost_eur']:.2f}€")
            st.metric("Gesamt Claude-Kosten",  f"{cost_summary['total_cost_eur']:.2f}€")
        with c2:
            st.metric("Ollama-Skips gesamt",   cost_summary["ollama_skips"],
                      help="Analysen die Claude nicht gerufen haben")
            st.metric("Heute gespart",         f"{cost_summary['today_saved_eur']:.2f}€")
            st.metric("Gesamt gespart",        f"{cost_summary['total_saved_eur']:.2f}€")

        skip_pct = cost_summary["skip_rate_pct"]
        if cost_summary["total_analyses"] > 0:
            st.progress(skip_pct / 100,
                        text=f"Ollama-Einsparrate: {skip_pct:.1f}% der Analysen ohne Claude")
    except Exception:
        st.caption("Kosten-Tracking noch nicht verfügbar")
    st.divider()

    # Config summary
    st.markdown("### ⚙️ Konfiguration")
    st.write(f"**Claude Modell:** {config.claude_model}")
    st.write(f"**Ollama Modell:** {config.ollama_model if config.ollama_enabled else '–'}")
    st.write(f"**Broker:** {config.broker_mode.upper()}")
    st.write(f"**Watchlist:** {', '.join(ticker_label(t) for t in config.watchlist)}")
    st.write(f"**Kauf-Schwelle:** {config.buy_threshold:.2f}")
    st.write(f"**Hedge ab:** {config.hedge_from_regime}")
    st.write(f"**Max Hedge:** {config.max_hedge_pct*100:.0f}%")
    st.write(f"**Börsen:** {', '.join(config.market_exchanges)}")
    st.write(f"**Social Scan:** ✗ (deaktiviert)")
    st.write(f"**SL/TP-Check:** alle 30 Min")
    st.write(f"**Aging-Check:** alle 4h")
    st.write(f"**Kelly Sizing:** {'✓' if config.use_kelly_sizing else '✗'}")
    st.divider()

    st.markdown("### 🔧 Analyse-Features")
    features = [
        "30-Tage Nachrichtenarchiv",
        "Thesis-Überprüfung",
        "Congressional Insider-Trades",
        "SEC Form 4 Insidermeldungen",
        "US-Bundesaufträge (usaspending)",
        "SEC EDGAR 8-K Meldungen",
        "StockTwits Sentiment",
        "PRNewswire / BusinessWire",
        "EU-Nachrichten (Google RSS)",
        "Options-Flow (C/P-Ratio)",
        "Earnings-Filter",
        "Sektor-Korrelationscheck",
        "Kelly-Criterion Sizing",
        "Rezessions-Detektor",
        "Inverse ETF Hedging",
        "Signal-Warteschlange",
        "Wochenbriefing (KI)",
        "Technische Indikatoren (RSI, MACD, BB, EMA, ATR)",
    ]
    for f in features:
        st.write(f"✓ {f}")

    # Ehrlicher Quellen-Status statt statischer ✓-Liste: was ist bewusst
    # abgeschaltet (tote Endpoints), was scheitert an fehlenden API-Keys?
    _dead_srcs = sorted(set(getattr(config, "collectors_disabled", [])))
    if _dead_srcs:
        st.write("✗ **Deaktivierte Quellen** (Endpoint tot, `COLLECTORS_DISABLED`): "
                 + ", ".join(_dead_srcs))
    _keyless = [
        _n for _n, _k in (
            ("Twitter/X", "twitter_bearer_token"),
            ("Quiver (Congress-Trades)", "quiver_api_key"),
            ("NewsAPI", "newsapi_key"),
        ) if not getattr(config, _k, "")
    ]
    if _keyless:
        st.write("✗ **Ohne API-Key inaktiv:** " + ", ".join(_keyless))

    st.divider()
    if st.button("🔄 Cache leeren & neu laden", width="stretch"):
        st.cache_resource.clear()
        st.rerun()
