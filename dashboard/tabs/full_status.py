"""Volle Status-Seite (W8.7, 20.7.2026, User-Vorgabe): der Kern-Status-
Knopf in der Sidebar soll nicht in einen engen Popover führen, sondern auf
eine eigene Seite mit genug Platz für ALLE Bot-Daten — als Übergangslösung,
solange der Fabrik-Umbau (Vision W7) noch nicht jede Maschine mit einem
vollen Detailpanel abdeckt. Bündelt nur bereits vorhandene Panel-Module
(gleiche, die sonst über Maschinen-Klicks in der Fabrik erreichbar sind),
keine eigene Datenlogik. Jeder Abschnitt einzeln fail-open: ein kaputtes
Panel darf die anderen nie mit runterreißen."""
import streamlit as st


def _render_kern_zahlen(ctx) -> None:
    st.subheader("📋 Kern-Zahlen")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bot-Zustand", "⏸ pausiert" if ctx._hdr_paused else "▶️ läuft")
    try:
        c2.metric("Depotwert", f"${ctx.total_value:,.2f}")
    except Exception:
        pass
    try:
        c3.metric("Offene Positionen", len(ctx.portfolio.all_positions()))
    except Exception:
        pass
    try:
        c4.metric("Signal-Queue", f"{ctx.pending_cnt} ausstehend")
    except Exception:
        pass

    c5, c6, c7, c8 = st.columns(4)
    try:
        if ctx.regime_data:
            c5.metric("Marktregime", ctx.regime_data["regime"],
                      f"Score {ctx.regime_data['recession_score']:.2f}")
    except Exception:
        pass
    try:
        from portfolio.circuit_breaker import CircuitBreaker
        _cb = CircuitBreaker().status(ctx.total_value)
        c6.metric("Circuit-Breaker", "🔴 ausgelöst" if _cb.get("triggered") else "🟢 normal")
    except Exception:
        pass
    try:
        import socket
        with socket.create_connection((ctx.config.ibkr_host, ctx.config.ibkr_port), timeout=0.4):
            c7.metric("IB-Gateway", "🟢 erreichbar")
    except Exception:
        c7.metric("IB-Gateway", "🔴 nicht erreichbar")

    # Effektive Kauf-Schwelle: Basis-Schwelle + Kapitalknappheits-Aufschlag
    # (strategy/swing_strategy.py _capital_scarcity_adjustment), damit man
    # nicht nur die statische Basis (Kontrollraum-Slider), sondern die
    # tatsächlich gerade wirksame Schwelle sieht.
    try:
        if getattr(ctx.config, "capital_scarcity_threshold_enabled", True):
            from strategy.swing_strategy import _capital_scarcity_adjustment
            equity = ctx.portfolio.total_value({})
            cash_pct = (ctx.portfolio.cash / equity) if equity > 0 else 0.0
            pivot = float(getattr(ctx.config, "capital_scarcity_pivot_pct", 0.10))
            max_adj = float(getattr(ctx.config, "capital_scarcity_max_adj", 0.15))
            adj = _capital_scarcity_adjustment(cash_pct, max_adj, pivot)
            eff_threshold = ctx.config.buy_threshold + adj
            c8.metric(
                "Effektive Kauf-Schwelle",
                f"{eff_threshold:.2f}",
                f"+{adj:.3f} (Cash {cash_pct * 100:.0f}%)" if adj >= 0.005 else "≈ Basis (Cash reichlich)",
            )
        else:
            c8.metric("Effektive Kauf-Schwelle", f"{ctx.config.buy_threshold:.2f}", "Kapital-Aufschlag deaktiviert")
    except Exception:
        pass

    try:
        from analyzers.api_cost_tracker import APICostTracker
        _cost = APICostTracker().summary()
        st.caption(
            f"Claude-Kosten heute: {_cost['today_cost_eur']:.2f}€ / "
            f"{_cost.get('daily_limit_eur', 1.0):.2f}€ Limit"
        )
    except Exception:
        pass

    try:
        from system import live_status as _ls_mod
        _ls = getattr(ctx, "_ls", None) or _ls_mod.read_status()
        if _ls and _ls.get("state") == "cycle":
            _age = _ls_mod.status_age_seconds(_ls)
            if _age is not None and _age < 1800:
                _phase_txt = _ls.get("phase") or "Zyklus"
                if _ls.get("ticker"):
                    _phase_txt += f" · {_ls['ticker']}"
                st.info(f"🔄 Aktuell: {_phase_txt}")
        elif _ls and _ls.get("next_run"):
            st.caption(f"Nächster Lauf: {_ls['next_run'][:16].replace('T', ' ')} Uhr")
    except Exception:
        pass


# (Titel, Modulname) — dieselben Panel-Module, die sonst nur über einen
# Maschinen-Klick in der Fabrik erreichbar sind (siehe dashboard/tabs/
# factory.py::_render_detail_panel). Reihenfolge = Warenfluss.
_SECTIONS = [
    ("💰 Portfolio", "portfolio_panel"),
    ("🧠 Entscheidungen & Signal-Queue", "decisions_panel"),
    ("📡 Live-Aktivität", "live_panel"),
    ("🔍 Analyse-Log", "analysis_log_panel"),
    ("🌦 Markt-Regime", "regime_panel"),
    ("🧪 Trades & Lernen", "trades_panel"),
    ("🚀 Watchlist & IPO-Pipeline", "watchlist_panel"),
    ("🔎 Aktien-Kartei", "dossier_panel"),
    ("🎛 Einstellungen & Kontrollraum", "settings_panel"),
]


def render(ctx) -> None:
    st.markdown("[← Zurück zur Fabrik](?)")
    st.title("📋 Kern-Status — volle Übersicht")
    st.caption(
        "Übergangsseite mit allen Bot-Daten auf einen Blick, solange die "
        "Fabrik-Szene noch nicht jede Maschine mit einem vollen Detailpanel "
        "abdeckt. Bleibt bestehen, bis der Umbau fertig ist."
    )
    st.divider()

    _render_kern_zahlen(ctx)

    import importlib
    for title, modname in _SECTIONS:
        st.divider()
        st.subheader(title)
        try:
            mod = importlib.import_module(f"dashboard.{modname}")
            mod.render(ctx)
        except Exception:
            st.caption("Dieser Abschnitt ist derzeit nicht verfügbar.")
