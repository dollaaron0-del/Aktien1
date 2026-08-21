"""Dynamische-Watchlist-Panel — bis 18.7.2026 eigener Tab, seit dem
Karten-Umbau Teil des Hochregallager-Detailpanels (dashboard/tabs/factory.py),
neben der durchsuchbaren Kartei — gehört inhaltlich zusammen: welche
Aktien beobachtet/vorgemerkt sind."""
import os

import pandas as pd
import streamlit as st

from analyzers.dynamic_watchlist import DynamicWatchlist
from analyzers.signal_expander import SignalDrivenExpander
from dashboard import theme as _theme


def render(ctx) -> None:
    config = ctx.config
    portfolio = ctx.portfolio
    ticker_label = ctx.ticker_label
    _ALL_NAMES = ctx._ALL_NAMES

    # ── IPO-Pipeline ─────────────────────────────────────────────────────────
    st.subheader("🚀 IPO-Pipeline – Demnächst an der Börse")
    st.caption(
        "Nur Unternehmen mit Bewertung ≥ $10 Mrd. werden verfolgt. "
        "Ab $25 Mrd. werden sie bei IPO-Erkennung automatisch zur Analyse-Queue hinzugefügt."
    )

    try:
        from analyzers.ipo_tracker import IPOTracker as _IPOTracker, CANDIDATES as _IPO_CANDS
        _ipo = _IPOTracker()
        _pipeline = _ipo.get_pipeline()
        if _pipeline:
            _ipo_rows = []
            for _c in _pipeline:
                _hype = _c["hype_score"]
                _hype_str = f"{_hype:.0%}" if _hype is not None else "–"
                _hype_icon = (
                    "🟢" if (_hype or 0) >= 0.6
                    else "🟡" if (_hype or 0) >= 0.4
                    else "🔴" if _hype is not None
                    else "⚪"
                )
                _status = (
                    f"✅ LIVE ({_c['live_ticker']})"
                    if _c["is_live"]
                    else "⏳ Pre-IPO"
                )
                _ipo_rows.append({
                    "Status":         _status,
                    "Unternehmen":    _c["name"],
                    "Sektor":         _c["sector"],
                    "Bew. ($Mrd.)":   _c["valuation_b"],
                    "Hype-Score":     f"{_hype_icon} {_hype_str}",
                    "Artikel/Woche":  _c["articles_7d"],
                    "Auto-Watchlist": "✅" if _c["auto_eligible"] else "❌",
                    "Zuletzt geprüft": _c["last_checked"],
                    "Info":           _c["notes"],
                })
            st.dataframe(pd.DataFrame(_ipo_rows), width="stretch", hide_index=True)

            # Detail-Expander mit Headlines
            _live_cands  = [c for c in _pipeline if c["is_live"]]
            _pre_cands   = [c for c in _pipeline if not c["is_live"] and c["articles_7d"] > 0]
            if _live_cands:
                st.success(
                    "🎉 **Neue Börsengänge erkannt:** "
                    + ", ".join(f"{c['name']} ({c['live_ticker']})" for c in _live_cands)
                )
            for _c in _pre_cands[:4]:
                with st.expander(f"📰 Headlines – {_c['name']} (letzte 7 Tage)"):
                    for _h in _c["headlines"]:
                        st.markdown(f"• {_h}")
                    if not _c["headlines"]:
                        st.caption("Noch keine Artikel gefunden.")
        else:
            st.info("Noch keine IPO-Daten. Wird täglich um 06:00 UTC aktualisiert.")
    except Exception as _ipo_err:
        st.caption(f"IPO-Tracker nicht verfügbar: {_ipo_err}")

    st.divider()

    st.subheader("🔭 Dynamische Watchlist")
    st.caption(
        "Der Bot scannt täglich ~80 Aktien und wählt automatisch die vielversprechendsten aus. "
        "Scoring: Volumen (30%) + Momentum (25%) + RSI (25%) + MACD (20%)"
    )

    _dw = DynamicWatchlist(max_picks=config.scan_max_picks or 12)

    wl_col1, wl_col2 = st.columns([4, 1])
    with wl_col2:
        if st.button("🔄 Jetzt neu scannen", width="stretch"):
            with st.spinner("Scanne Markt-Universum…"):
                active = list(portfolio.all_positions().keys())
                new_wl = _dw.force_refresh(active_tickers=active)
            st.success(f"Neue Watchlist: {', '.join(new_wl)}")
            st.rerun()

    with wl_col1:
        if not config.auto_scan_watchlist:
            st.warning(
                "Dynamische Watchlist ist deaktiviert. "
                "Setze `AUTO_SCAN_WATCHLIST=true` in der `.env` Datei."
            )
        else:
            cached = _dw._load_cache()
            if cached:
                age_h = _dw._cache_age_hours(cached)
                updated = cached.get("updated_at", "–")[:16]
                st.info(
                    f"Letzte Aktualisierung: **{updated} UTC** "
                    f"(vor {age_h:.1f}h) · Nächste in {max(0, 24 - age_h):.1f}h"
                )
                current_wl = cached["tickers"]
                st.markdown("**Aktuelle Watchlist:**")
                wl_badges = "  ".join(
                    f"`{ticker_label(t)}`" for t in current_wl
                )
                st.markdown(wl_badges)
            else:
                st.info("Noch kein Scan durchgeführt. Klicke 'Jetzt neu scannen'.")

    st.divider()

    # Scored candidates table
    st.subheader("Alle bewerteten Kandidaten")
    st.caption("Vollständige Rangliste aller gescannten Aktien mit Einzelscores.")
    with st.spinner("Lade Kandidaten-Scores…"):
        candidates = _dw.get_scored_candidates()

    if candidates:
        df_wl = pd.DataFrame(candidates)
        df_wl.insert(1, "Name", df_wl["ticker"].apply(lambda t: _ALL_NAMES.get(t.upper(), "")))
        df_wl = df_wl.rename(columns={
            "ticker":       "Ticker",
            "total_score":  "Gesamt-Score",
            "price":        "Kurs $",
            "vol_ratio":    "Vol-Ratio",
            "momentum_20d": "Momentum 20d %",
            "rsi":          "RSI",
            "macd_hist":    "MACD-Hist",
            "vol_score":    "Score Volumen",
            "mom_score":    "Score Momentum",
            "rsi_score":    "Score RSI",
            "macd_score":   "Score MACD",
        })
        # Highlight top 12 (current watchlist)
        top_n = config.scan_max_picks or 12
        st.dataframe(
            df_wl.style.background_gradient(
                subset=["Gesamt-Score"], cmap="RdYlGn", vmin=0, vmax=100
            ).map(
                lambda v: "color: #00e676; font-weight:700" if isinstance(v, (int, float)) and v >= 0 else "",
                subset=["Momentum 20d %"],
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption(f"Top {top_n} werden als Watchlist verwendet.")
    else:
        st.info("Noch keine Scan-Daten. Klicke 'Jetzt neu scannen'.")

    st.divider()

    # Signal-Ticker (Insider, Social, Options, Contracts)
    st.subheader("📡 Signal-Ticker (Small-Cap-Radar)")
    st.caption(
        "Aktien die durch Insider-Käufe, Social-Spikes, Options-Flow oder "
        "Regierungsaufträge aufgefallen sind. Werden **passiv gesammelt** (📥) und "
        "erst bei genug Signal-Gewicht **und Bestätigung aus ≥2 Quellen** zur "
        "Analyse eskaliert (🔬). Max. 3 gleichzeitig in Analyse. Temporär (7 Tage)."
    )
    _expander = SignalDrivenExpander()
    sig_entries = _expander.get_all_entries()
    if sig_entries:
        for _e in sig_entries:
            _e["sources"] = ", ".join(_e.get("sources", [])) or "–"
        df_sig = pd.DataFrame(sig_entries).rename(columns={
            "ticker":     "Ticker",
            "status":     "Status",
            "reason":     "Signal-Grund",
            "weight":     "Gewicht",
            "sources":    "Quellen",
            "n_sources":  "#Q",
            "added_at":   "Entdeckt",
            "expires_at": "Läuft ab",
            "active":     "Aktiv",
            "signals":    "Signale",
        })
        st.dataframe(
            df_sig.style.map(
                lambda v: "color: #00e676; font-weight:700" if v is True else
                          ("color: #888" if v is False else ""),
                subset=["Aktiv"],
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info(
            "Noch keine Signal-Ticker entdeckt.  \n"
            "Der Bot erkennt automatisch unbekannte Aktien aus Insider-Trades, "
            "Social-Spikes und Options-Flow während des Betriebs."
        )

    st.divider()

    # ── Watchlist bearbeiten ─────────────────────────────────────────────────
    st.subheader("✏️ Watchlist bearbeiten")
    st.caption("Änderungen werden sofort in die .env geschrieben. Bot danach neu starten.")

    def _wl_read() -> list:
        try:
            _p = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
            with open(_p) as _f:
                for _l in _f:
                    if _l.strip().startswith("WATCHLIST="):
                        return [t.strip().upper() for t in _l.strip().split("=", 1)[1].split(",") if t.strip()]
        except Exception:
            pass
        return list(config.watchlist)

    def _wl_write(tickers: list) -> None:
        _p = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
        _val = ",".join(tickers)
        try:
            with open(_p) as _f:
                _lines = _f.readlines()
        except FileNotFoundError:
            _lines = []
        _written = False
        _new = []
        for _l in _lines:
            if _l.strip().startswith("WATCHLIST="):
                _new.append(f"WATCHLIST={_val}\n")
                _written = True
            else:
                _new.append(_l)
        if not _written:
            _new.append(f"WATCHLIST={_val}\n")
        with open(_p, "w") as _f:
            _f.writelines(_new)

    _cur_wl = _wl_read()
    _wl_all_opts = sorted(set(list(_ALL_NAMES.keys()) + _cur_wl))

    _wl_col1, _wl_col2 = st.columns([3, 2])
    with _wl_col1:
        _keep = st.multiselect(
            "Aktuelle Watchlist (Häkchen entfernen = löschen)",
            options=_cur_wl,
            default=_cur_wl,
            format_func=ticker_label,
            key="wl_keep_ms",
        )
    with _wl_col2:
        _add_opt = st.selectbox(
            "Ticker hinzufügen",
            options=[""] + _wl_all_opts,
            index=0,
            format_func=lambda x: "— Ticker auswählen —" if x == "" else ticker_label(x),
            key="wl_add_select",
        )
        _add_manual = st.text_input(
            "… oder manuell eingeben",
            placeholder="z.B. TSLA oder BMW.DE",
            key="wl_add_manual",
        ).strip().upper()

    _wl_save_col, _wl_restart_col = st.columns(2)
    with _wl_save_col:
        if st.button("💾 Watchlist speichern", width="stretch", type="primary", key="wl_save_btn"):
            _final_wl = list(_keep)
            for _t in [_add_opt, _add_manual]:
                if _t and _t not in _final_wl:
                    _final_wl.append(_t)
            if _final_wl:
                _wl_write(_final_wl)
                st.success(f"✅ Gespeichert: {', '.join(ticker_label(t) for t in _final_wl)}")
                st.info("Bot neu starten damit die neue Watchlist aktiv wird.")
            else:
                st.error("Watchlist darf nicht leer sein.")
    with _wl_restart_col:
        if st.button("▶️ Bot neu starten", width="stretch", key="wl_restart_btn"):
            try:
                import subprocess as _wl_sp
                _wl_sp.run(["systemctl", "restart", "aktien_bot"], check=True, timeout=10)
                st.success("Bot wurde neu gestartet.")
            except Exception as _wl_e:
                st.error(f"Fehler: {_wl_e}")

    # ── Warteliste (BenchList) ───────────────────────────────────────────────
    st.divider()
    st.subheader("⏳ Warteliste")
    st.caption(
        "Aktien die der Bot aus News, Reddit oder Signal-Scans aufgeschnappt hat. "
        "Bei freien Positions-Slots werden sie automatisch priorisiert analysiert."
    )
    try:
        from analyzers.bench_list import BenchList as _BenchList
        _bench = _BenchList()
        _bench_entries = _bench.get_all()
        if not _bench_entries:
            st.info("Noch keine Kandidaten in der Warteliste. Der Bot füllt sie automatisch beim nächsten Zyklus.")
        else:
            _b_cols = st.columns([1, 3, 1, 1])
            _b_cols[0].markdown("**Ticker**")
            _b_cols[1].markdown("**Grund**")
            _b_cols[2].markdown("**Score**")
            _b_cols[3].markdown("**Signale**")
            for _be in sorted(_bench_entries, key=lambda x: (-x["score"], -x["signal_count"]))[:15]:
                _bc = st.columns([1, 3, 1, 1])
                _bc[0].markdown(f"`{_be['ticker']}`")
                _bc[1].caption(_be["reason"][:60])
                _score_status = "ok" if _be["score"] >= 0.6 else "warn" if _be["score"] >= 0.4 else "err"
                _bc[2].markdown(
                    f"{_theme.led(_score_status, '')} {_be['score']:.2f}",
                    unsafe_allow_html=_theme.is_enabled(),
                )
                _bc[3].markdown(str(_be["signal_count"]))

            # Ticker manuell zur Warteliste hinzufügen
            with st.expander("➕ Ticker manuell zur Warteliste hinzufügen"):
                _manual_bench_col1, _manual_bench_col2 = st.columns([3, 1])
                _manual_bench_ticker = _manual_bench_col1.text_input(
                    "Ticker", placeholder="z.B. PLTR oder SIE.DE", key="bench_add_input"
                ).strip().upper()
                if _manual_bench_col2.button("Hinzufügen", key="bench_add_btn") and _manual_bench_ticker:
                    _bench.add(_manual_bench_ticker, reason="Manuell hinzugefügt", score=0.5)
                    st.success(f"{_manual_bench_ticker} zur Warteliste hinzugefügt.")
                    st.rerun()
    except Exception as _bench_err:
        st.caption(f"Warteliste nicht verfügbar: {_bench_err}")
