"""Tab "Einstellungen" — ausgelagert aus dashboard/app.py (Roadmap 4.4a)."""
import os
import subprocess

import pandas as pd
import streamlit as st

_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".env")


def _read_env() -> dict:
    """Liest .env als dict."""
    result = {}
    try:
        with open(_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    result[k.strip()] = v.strip()
    except Exception:
        pass
    return result


def _write_env(updates: dict) -> None:
    """Schreibt einzelne Keys in die .env, fügt fehlende am Ende ein."""
    try:
        with open(_ENV_PATH) as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    written = set()
    new_lines = []
    for line in lines:
        if line.strip().startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            written.add(key)
        else:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in written:
            new_lines.append(f"{key}={val}\n")

    with open(_ENV_PATH, "w") as f:
        f.writelines(new_lines)


def render(ctx) -> None:
    config = ctx.config
    _env = _read_env()

    st.subheader("⚙️ Bot-Einstellungen")
    st.caption(
        "Änderungen werden sofort in die `.env` geschrieben. "
        "Klicke danach **Bot neu starten** damit sie aktiv werden."
    )

    # ── Fokus-Modus AUSSERHALB des Formulars (sofortige Reaktion) ────────────
    _focus_opts   = ["WEALTH_BUILDING", "INCOME", "TARGET_GOAL"]
    _focus_labels = {
        "WEALTH_BUILDING": "🚀 Vermögensaufbau – maximales Wachstum",
        "INCOME":          "💸 Ausschüttung – monatliche Erträge",
        "TARGET_GOAL":     "🏁 Ziel-Modus – Ziel-Betrag bis Datum",
    }
    _cur_focus = _env.get("FOCUS_MODE", config.focus_mode)
    _live_focus = st.selectbox(
        "Fokus-Modus",
        _focus_opts,
        index=_focus_opts.index(_cur_focus) if _cur_focus in _focus_opts else 0,
        format_func=lambda x: _focus_labels[x],
        key="settings_focus_live",
    )

    with st.form("settings_form"):
        col_a, col_b = st.columns(2)
        _sel_focus = st.session_state.get("settings_focus_live", _cur_focus)

        with col_a:
            st.markdown("#### 🎯 Ziel-Einstellungen")

            new_monthly_eur = float(_env.get("MONTHLY_DISTRIBUTION_EUR", config.monthly_distribution_eur))
            new_buffer = int(_env.get("DISTRIBUTION_BUFFER_MONTHS", config.distribution_buffer_months))

            _cap = config.initial_capital  # aktuelles Startkapital

            if _sel_focus == "TARGET_GOAL":
                new_goal_amount = st.number_input(
                    "Ziel-Betrag ($)",
                    min_value=0.0, step=1000.0,
                    value=float(_env.get("TARGET_GOAL_AMOUNT", config.target_goal_amount or 0)),
                )
                new_goal_date = st.text_input(
                    "Ziel-Datum (YYYY-MM-DD)",
                    value=_env.get("TARGET_GOAL_DATE", config.target_goal_date or ""),
                    placeholder="2027-12-31",
                )
                new_growth = float(_env.get("GROWTH_TARGET_MULTIPLE", config.growth_target_multiple))
                # Kapitalempfehlung Ziel-Modus
                if new_goal_amount > 0 and new_goal_date:
                    try:
                        from datetime import date as _date
                        _days = (_date.fromisoformat(new_goal_date) - _date.today()).days
                        _years = max(_days / 365, 0.1)
                        _needed_return = (new_goal_amount / _cap) ** (1 / _years) - 1
                        _color = "green" if _needed_return < 0.20 else ("orange" if _needed_return < 0.40 else "red")
                        _verdict = "realistisch" if _needed_return < 0.20 else ("ambitioniert" if _needed_return < 0.40 else "sehr aggressiv")
                        st.markdown(
                            f"**Kapitalempfehlung:** Bei **${_cap:,.0f}** Startkapital und "
                            f"**{_years:.1f} Jahren** bis zum Zieldatum benötigst du "
                            f"**:{_color}[{_needed_return*100:.1f}% p.a.]** — _{_verdict}_  \n"
                            f"Empfohlenes Mindestkapital für dieses Ziel: **${new_goal_amount * 0.3:,.0f}**+ "
                            f"(30% des Zielbetrags als Start).",
                            unsafe_allow_html=False,
                        )
                    except Exception:
                        pass

            elif _sel_focus == "WEALTH_BUILDING":
                new_growth = st.slider(
                    "Wachstumsziel (× Startkapital)",
                    min_value=1.5, max_value=10.0, step=0.5,
                    value=float(_env.get("GROWTH_TARGET_MULTIPLE", config.growth_target_multiple)),
                    help="Ab diesem Vielfachen des Startkapitals wechselt der Bot in die Ausschüttungsphase",
                )
                new_goal_amount = float(_env.get("TARGET_GOAL_AMOUNT", 0) or 0)
                new_goal_date   = _env.get("TARGET_GOAL_DATE", "")
                _target_val = _cap * new_growth
                # Jahre bis Ziel bei verschiedenen Renditen
                import math as _math
                _y15 = _math.log(new_growth) / _math.log(1.15)
                _y20 = _math.log(new_growth) / _math.log(1.20)
                st.info(
                    f"Ausschüttungsphase ab: **${_target_val:,.0f}** ({new_growth:.1f}× Startkapital)  \n"
                    f"Zeitrahmen bei 15% p.a.: **{_y15:.1f} Jahre** · bei 20% p.a.: **{_y20:.1f} Jahre**  \n"
                    f"Empfohlenes Mindestkapital: **$10.000+** (mehr Kapital = besser diversifiziert)"
                )

            else:  # INCOME
                new_growth = float(_env.get("GROWTH_TARGET_MULTIPLE", config.growth_target_multiple))
                new_goal_amount = float(_env.get("TARGET_GOAL_AMOUNT", 0) or 0)
                new_goal_date   = _env.get("TARGET_GOAL_DATE", "")
                new_monthly_eur = st.number_input(
                    "Gewünschte monatliche Ausschüttung (€)",
                    min_value=100.0, max_value=50000.0, step=100.0,
                    value=float(_env.get("MONTHLY_DISTRIBUTION_EUR", config.monthly_distribution_eur)),
                    help="Der Bot handelt konservativ und versucht diesen Betrag monatlich aus Gewinnen zu erwirtschaften.",
                )
                new_buffer = st.slider(
                    "Sicherheitspuffer (Monate)",
                    min_value=1, max_value=12, step=1,
                    value=int(_env.get("DISTRIBUTION_BUFFER_MONTHS", config.distribution_buffer_months)),
                    help="Wie viele Monatsbeträge als Reserve gehalten werden bevor ausgeschüttet wird.",
                )
                # Kapitalempfehlung Ausschüttungs-Modus
                _annual_eur = new_monthly_eur * 12
                _rec_cap_15 = _annual_eur / 0.15  # benötigtes Kapital bei 15% Rendite
                _rec_cap_20 = _annual_eur / 0.20  # bei 20% Rendite
                _current_usd = _cap
                _ok = _current_usd >= _rec_cap_20
                _msg_color = "green" if _ok else ("orange" if _current_usd >= _rec_cap_15 * 0.6 else "red")
                _verdict2 = "erreichbar" if _ok else ("knapp" if _current_usd >= _rec_cap_20 * 0.6 else "Kapital zu gering")
                st.markdown(
                    f"**Kapitalempfehlung für {new_monthly_eur:,.0f} €/Monat:**  \n"
                    f"• Bei 15% Jahresrendite: **${_rec_cap_15:,.0f}** Mindestkapital  \n"
                    f"• Bei 20% Jahresrendite: **${_rec_cap_20:,.0f}** Mindestkapital  \n"
                    f"• Dein Kapital: **${_current_usd:,.0f}** — _{_verdict2}_"
                )

        with col_b:
            st.markdown("#### 🛡 Risikomanagement")
            new_sl = st.slider(
                "Stop-Loss %",
                min_value=3, max_value=20, step=1,
                value=int(round(float(_env.get("STOP_LOSS_PCT", config.stop_loss_pct)) * 100)),
                help="Position wird automatisch verkauft wenn Verlust diesen Wert erreicht",
            )
            new_tp = st.slider(
                "Take-Profit %",
                min_value=10, max_value=60, step=5,
                value=int(round(float(_env.get("TAKE_PROFIT_PCT", config.take_profit_pct)) * 100)),
                help="Position wird automatisch verkauft wenn Gewinn diesen Wert erreicht",
            )
            new_maxpos_pct = st.slider(
                "Max. Positionsgröße % des Portfolios",
                min_value=5, max_value=30, step=1,
                value=int(round(float(_env.get("MAX_POSITION_PCT", config.max_position_pct)) * 100)),
            )
            new_buy_thr = st.slider(
                "Kauf-Schwelle (Sentiment-Score)",
                min_value=0.50, max_value=0.95, step=0.05,
                value=float(_env.get("BUY_THRESHOLD", config.buy_threshold)),
                help="Nur Aktien mit Sentiment-Score ≥ diesem Wert werden gekauft",
            )
            new_sell_thr = st.slider(
                "Verkauf-Schwelle (Sentiment-Score)",
                min_value=0.10, max_value=0.50, step=0.05,
                value=float(_env.get("SELL_THRESHOLD", config.sell_threshold)),
                help="Positionen werden verkauft wenn der Score unter diesen Wert fällt",
            )

        st.markdown("#### 📋 Watchlist & Scanning")
        w1, w2 = st.columns(2)
        with w1:
            cur_wl = ",".join(config.watchlist)
            new_wl_raw = st.text_area(
                "Watchlist (Komma-getrennt)",
                value=_env.get("WATCHLIST", cur_wl),
                height=80,
                help="Ticker die immer analysiert werden, z.B. AAPL,MSFT,NVDA",
            )
            new_auto_scan = st.toggle(
                "Dynamische Watchlist (AUTO_SCAN)",
                value=_env.get("AUTO_SCAN_WATCHLIST", "false").lower() in ("1","true","yes"),
                help="Bot wählt täglich automatisch die vielversprechendsten Aktien",
            )
            new_scan_picks = st.slider(
                "Max. Auto-Scan Picks",
                min_value=3, max_value=20, step=1,
                value=int(_env.get("SCAN_MAX_PICKS", config.scan_max_picks or 3)),
            )
        with w2:
            new_eu = st.toggle(
                "EU-Aktien aktivieren",
                value=_env.get("EU_STOCKS_ENABLED", "false").lower() in ("1","true","yes"),
            )
            cur_eu_wl = ",".join(config.eu_watchlist) if config.eu_watchlist else ""
            new_eu_wl = st.text_area(
                "EU-Watchlist (leer = Auto-Scan)",
                value=_env.get("EU_WATCHLIST", cur_eu_wl),
                height=80,
                placeholder="SAP.DE,ASML.AS,MC.PA",
                disabled=not new_eu,
            )

        st.markdown("#### ⚡ Spezial-Modi")
        st.caption("Diese Modi überschreiben die Risiko-Einstellungen oben.")
        m3, m4 = st.columns(2)
        with m3:
            new_intraday = st.toggle(
                "🕐 Intraday-Scan (3. Analyse)",
                value=_env.get("INTRADAY_SCAN_ENABLED", "false").lower() in ("1","true","yes"),
                help="Führt täglich einen dritten Analyse-Zyklus durch – ideal während der US-Session.",
            )
            new_intraday_time = st.text_input(
                "Uhrzeit (UTC)",
                value=_env.get("INTRADAY_SCAN_TIME", "17:30"),
                placeholder="17:30",
                disabled=not new_intraday,
                help="17:30 UTC = 19:30 MESZ (mitten in der US-Session)",
            )
            if new_intraday:
                st.info(f"3. Analyse täglich um {new_intraday_time} UTC")
        with m4:
            new_frugal = st.toggle(
                "🤖 Ollama-Vollanalyse",
                value=_env.get("FRUGAL_MODE", "false").lower() in ("1","true","yes"),
                help="Ollama übernimmt die komplette Analyse für normale Ticker. Claude nur noch für offene Positionen, SEC/Earnings und manuelle Anfragen. Spart ~85% Claude-Kosten.",
            )
            if new_frugal:
                st.success("Frugal: Ollama analysiert alles · Claude nur für Positionen & SEC")

        st.divider()
        save_btn = st.form_submit_button("💾 Einstellungen speichern", width="stretch", type="primary")

    if save_btn:
        updates = {
            "FOCUS_MODE":             _sel_focus,
            "TARGET_GOAL_AMOUNT":     str(new_goal_amount),
            "TARGET_GOAL_DATE":       new_goal_date,
            "GROWTH_TARGET_MULTIPLE": str(new_growth),
            "STOP_LOSS_PCT":          str(new_sl / 100),
            "TAKE_PROFIT_PCT":        str(new_tp / 100),
            "MAX_POSITION_PCT":       str(new_maxpos_pct / 100),
            "BUY_THRESHOLD":          str(new_buy_thr),
            "SELL_THRESHOLD":         str(new_sell_thr),
            "WATCHLIST":              ",".join(t.strip().upper() for t in new_wl_raw.split(",") if t.strip()),
            "AUTO_SCAN_WATCHLIST":    "true" if new_auto_scan else "false",
            "SCAN_MAX_PICKS":         str(new_scan_picks),
            "EU_STOCKS_ENABLED":      "true" if new_eu else "false",
            "EU_WATCHLIST":           ",".join(t.strip().upper() for t in new_eu_wl.split(",") if t.strip()),
            "ENABLE_SOCIAL_SCAN":     "false",
            "INTRADAY_SCAN_ENABLED":     "true" if new_intraday else "false",
            "INTRADAY_SCAN_TIME":        new_intraday_time.strip() or "17:30",
            "FRUGAL_MODE":               "true" if new_frugal else "false",
            "MONTHLY_DISTRIBUTION_EUR":  str(new_monthly_eur),
            "DISTRIBUTION_BUFFER_MONTHS": str(new_buffer),
        }
        try:
            _write_env(updates)
            try:
                subprocess.run(["systemctl", "restart", "aktien_bot"], check=True, timeout=15)
                st.success("✅ Einstellungen gespeichert und Bot neu gestartet.")
            except Exception as _re:
                st.success("✅ Einstellungen gespeichert.")
                st.warning(f"Bot-Neustart fehlgeschlagen (manuell starten): {_re}")
        except Exception as e:
            st.error(f"Fehler beim Speichern: {e}")

    st.divider()
    st.markdown("### 🔄 Bot-Dienste & Zurücksetzen")
    r1, r2, r3 = st.columns(3)
    with r1:
        if st.button("▶️ Bot neu starten", width="stretch", type="primary"):
            try:
                subprocess.run(["systemctl", "restart", "aktien_bot"], check=True, timeout=10)
                st.success("Bot wurde neu gestartet.")
            except Exception as e:
                st.error(f"Fehler: {e}")
    with r2:
        if st.button("▶️ Dashboard neu starten", width="stretch"):
            try:
                subprocess.run(["systemctl", "restart", "aktien_dashboard"], check=True, timeout=10)
                st.info("Dashboard-Dienst neu gestartet.")
            except Exception as e:
                st.error(f"Fehler: {e}")
    with r3:
        if st.button("🔄 Cache leeren & neu laden", width="stretch"):
            st.cache_resource.clear()
            st.rerun()

    st.divider()
    st.markdown("### 📋 Aktuelle .env Werte (Übersicht)")
    st.caption("Nur zur Ansicht – Änderungen oben im Formular vornehmen.")
    _display_keys = [
        "FOCUS_MODE","TARGET_GOAL_AMOUNT","TARGET_GOAL_DATE","GROWTH_TARGET_MULTIPLE",
        "STOP_LOSS_PCT","TAKE_PROFIT_PCT","MAX_POSITION_PCT","BUY_THRESHOLD","SELL_THRESHOLD",
        "INTRADAY_SCAN_ENABLED","INTRADAY_SCAN_TIME",
        "WATCHLIST","AUTO_SCAN_WATCHLIST","SCAN_MAX_PICKS","EU_STOCKS_ENABLED","EU_WATCHLIST",
        "ENABLE_SOCIAL_SCAN","INITIAL_CAPITAL","BROKER_MODE",
    ]
    env_rows = [{"Einstellung": k, "Wert": _env.get(k, "–")} for k in _display_keys]
    st.dataframe(pd.DataFrame(env_rows), width="stretch", hide_index=True)
