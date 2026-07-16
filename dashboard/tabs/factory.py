"""Tab "🏭 Fabrik" — interaktives Wimmelbild (Vision W1, docs/DESIGN_FABRIK.md).

Jede Maschine spiegelt ein echtes Subsystem; die Szene ist eine dritte
Darstellungsform neben Tabellen und Charts, kein Deko-Bild. Rendert in
BEIDEN Theme-Modi (hängt nur an PALETTE-Konstanten, nicht an
theme.is_enabled() — die Fabrik IST das Pixel-Theme, kein optionaler Zusatz
darauf)."""
import html
import time
from datetime import date

import streamlit as st

from dashboard import theme as _theme
from dashboard.factory.scene import build_scene_svg
from dashboard.factory.state import (
    MACHINE_IDS,
    MachineState,
    read_feed_events_until,
    read_history,
    read_state,
    reconstruct_from_snapshot,
    snapshot,
)
from dashboard.theme import PALETTE

# H2.3: gleiche Icons/Farben wie das Live-Terminal (tabs/live.py) — ein
# Ereignis sieht dort wie hier gleich aus, keine zweite Farbsprache.
_REPLAY_EV_ICON = {
    "cycle_start":   "🔄",
    "cycle_end":     "🏁",
    "analysis_done": "🔍",
    "trade":         "💼",
    "gate_blocked":  "⛔",
}
_REPLAY_EV_COLOR_VAR = {
    "trade":         "--px-neon-green",
    "gate_blocked":  "--px-copper",
    "cycle_start":   "--px-cobalt",
    "cycle_end":     "--px-cobalt",
    "analysis_done": "--px-text",
}

# H2.1: Grundlage für Zeitreise/Replay — Schnappschuss max. 1×/10 Min,
# sonst würde der 60s-Auto-Refresh die Historie-Datei vollschreiben.
# Modul-Variable (kein st.session_state): der Fragment-Rerun läuft
# serverseitig, ein Prozess-globaler Takt ist hier das Richtige.
_SNAPSHOT_INTERVAL_S = 600
_last_snapshot_ts = 0.0


def _maybe_snapshot(state) -> bool:
    """Schreibt einen Schnappschuss nur, wenn seit dem letzten
    mindestens `_SNAPSHOT_INTERVAL_S` vergangen ist. Eigene Funktion
    (statt Inline-Code im Fragment), damit die Drossel ohne
    Streamlit-Fragment-Mechanik testbar ist. Gibt zurück, ob
    geschrieben wurde."""
    global _last_snapshot_ts
    now = time.time()
    if now - _last_snapshot_ts < _SNAPSHOT_INTERVAL_S:
        return False
    snapshot(state)
    _last_snapshot_ts = now
    return True

_LEGEND = (
    ("neon_green", "aktiv/gesund"),
    ("cobalt", "läuft gerade"),
    ("amber", "Warnung"),
    ("red", "Fehler/ausgelöst"),
    ("border", "aus/keine Daten"),
)

_STATUS_LABEL = {
    "ok": "OK", "warn": "Warnung", "err": "Fehler", "off": "Aus", "active": "Aktiv",
}


def _detail_conveyor(m: MachineState) -> None:
    funnel = m.payload or {}
    c1, c2, c3 = st.columns(3)
    c1.metric("Analysiert heute", funnel.get("total", 0))
    c2.metric("Käufe", (funnel.get("actions") or {}).get("BUY", 0))
    c3.metric("Übersprungen", (funnel.get("actions") or {}).get("SKIP", 0))
    skip_reasons = funnel.get("skip_reasons") or {}
    if skip_reasons:
        st.markdown("**Warum übersprungen?**")
        for reason, n in skip_reasons.items():
            st.caption(f"- {reason}: {n}×")


def _detail_warehouse(m: MachineState) -> None:
    positions = (m.payload or {}).get("positions") or {}
    if not positions:
        st.caption("Keine offenen Positionen.")
        return
    st.table([
        {
            "Ticker": t,
            "Anteile": (info or {}).get("shares"),
            "Haltedauer": (
                f"{(info or {}).get('age_ratio'):.0%} des Ziels"
                if (info or {}).get("age_ratio") is not None else "–"
            ),
        }
        for t, info in positions.items()
    ])

    # H1.4: vorhandene Positions-Notizen read-only mit anzeigen (Pflege
    # bleibt im Portfolio-Tab) — st.caption escaped automatisch, kein
    # unsafe_allow_html nötig/verwendet.
    try:
        from dashboard.position_notes import PositionNotes
        _notes = PositionNotes()
        _has_notes = False
        for t in positions:
            _text = _notes.get(t)
            if _text:
                if not _has_notes:
                    st.markdown("**Notizen:**")
                    _has_notes = True
                st.caption(f"**{t}:** {_text}")
    except Exception:
        pass


def _detail_docks(m: MachineState) -> None:
    health = m.payload or {}
    for label, key in (("🟢 Gesund", "healthy"), ("🟡 Schwach", "weak"), ("🔴 Tot", "dead")):
        names = health.get(key) or []
        st.markdown(f"**{label} ({len(names)}):** {', '.join(names) if names else '–'}")


def _detail_lab(m: MachineState) -> None:
    s = m.payload or {}
    c1, c2, c3 = st.columns(3)
    c1.metric("Gelabelt", s.get("labeled") or 0)
    c2.metric("Gewinne", s.get("wins") or 0)
    c3.metric("Verluste", s.get("losses") or 0)
    if s.get("win_rate") is not None:
        st.caption(f"Win-Rate: {s['win_rate'] * 100:.1f}%")


def _detail_clock(m: MachineState) -> None:
    s = m.payload or {}
    st.markdown(f"**Zustand:** {s.get('state') or '–'}")
    st.markdown(f"**Phase:** {s.get('phase') or '–'}")
    if s.get("next_run"):
        st.markdown(f"**Nächster Lauf:** {s['next_run'][:16].replace('T', ' ')} Uhr")


_DETAIL_RENDERERS = {
    "conveyor": _detail_conveyor,
    "warehouse": _detail_warehouse,
    "docks": _detail_docks,
    "lab": _detail_lab,
    "clock": _detail_clock,
}


def _render_detail_panel(m: MachineState) -> None:
    """Detail-Block unter der Szene (Vision W3.2/W3.3): die fünf
    wichtigsten Maschinen bekommen einen sinnvollen eigenen Block, alle
    anderen den generischen Fallback (Label/Status/Tooltip/Rohdaten)."""
    st.divider()
    st.markdown(f"### {m.label}")
    st.caption(f"Status: {_STATUS_LABEL.get(m.status, m.status)}")

    renderer = _DETAIL_RENDERERS.get(m.id)
    if renderer is not None:
        try:
            renderer(m)
            return
        except Exception:
            pass  # Fail-open: generischer Fallback greift trotzdem

    for line in m.tooltip:
        st.markdown(f"- {line}")
    if m.payload:
        st.json(m.payload)


def _render_replay_terminal(day: str, until_ts: str) -> None:
    """H2.3: Feed-Ereignisse des gewählten Tages bis zum Regler-
    Zeitpunkt — das ist der "Replay"-Teil (kein Echtzeit-Rerun-Trick,
    siehe Modul-Doku der Roadmap: robuster Regler statt Streamlit-
    Frickelei). Fail-open: Lesefehler zeigen nur einen Hinweis."""
    try:
        events = read_feed_events_until(day, until_ts)
    except Exception:
        events = []
    if not events:
        st.caption("Keine Ereignisse bis zu diesem Zeitpunkt.")
        return
    lines = []
    for ev in events:
        icon = _REPLAY_EV_ICON.get(ev.get("event"), "•")
        ts = html.escape((ev.get("ts") or "")[11:16])
        var = _REPLAY_EV_COLOR_VAR.get(ev.get("event"), "--px-text")
        tk = f" <b>{html.escape(str(ev['ticker']))}</b>" if ev.get("ticker") else ""
        dt = f" — {html.escape(str(ev['detail']))}" if ev.get("detail") else ""
        lines.append(f'<div style="color:var({var});">{icon} {ts}{tk}{dt}</div>')
    st.markdown(f'<div class="px-terminal">{"".join(lines)}</div>', unsafe_allow_html=True)


def _render_archive() -> None:
    """H2.2/H2.3: Zeitreise-Regler + Tages-Replay — Grundlage H2.1
    (read_history). Bewusst AUSSERHALB des 60s-@st.fragment: der
    Regler-Zustand darf nicht vom unabhängigen Live-Refresh der Szene
    mitgerissen/zurückgesetzt werden. Fail-open: kaputte/fehlende
    Historie zeigt nur einen Hinweis, nie eine Exception."""
    with st.expander("🕰 Archiv & Replay"):
        day = st.date_input("Datum", value=date.today(), key="factory_archive_day")
        try:
            rows = read_history(day.isoformat())
        except Exception:
            rows = []

        if not rows:
            st.caption("Keine Aufzeichnung für diesen Tag.")
            return

        options = [r.get("ts", "") for r in rows]
        chosen_ts = st.select_slider(
            "Uhrzeit", options=options,
            value=options[-1],
            format_func=lambda ts: ts[11:16] if len(ts) >= 16 else ts,
            key="factory_archive_slider",
        )
        row = next((r for r in rows if r.get("ts") == chosen_ts), rows[-1])

        st.warning("ARCHIV-ANSICHT — nicht der Live-Zustand")
        archived_state = reconstruct_from_snapshot(row)
        st.markdown(build_scene_svg(archived_state), unsafe_allow_html=True)

        st.markdown("**Ereignisse bis zu diesem Zeitpunkt:**")
        _render_replay_terminal(day.isoformat(), chosen_ts)


def _render_logbook() -> None:
    """H7.3: Schichtbuch — auf Wunsch (Button-Klick, kein automatisches
    Schreiben beim bloßen Rendern) fasst `dashboard.logbook.write_entry()`
    die echten Feed-Ereignisse eines Tages zusammen. Fail-open: ein
    Lesefehler zeigt nur den Leerzustand statt zu crashen."""
    with st.expander("📖 Schichtbuch"):
        from dashboard.logbook import read_entry, write_entry
        day = st.date_input("Tag", value=date.today(), key="logbook_day")
        day_str = day.isoformat()
        try:
            entry = read_entry(day_str)
        except Exception:
            entry = None

        if entry is not None:
            st.markdown(entry.get("text") or "")
        else:
            st.caption("Noch kein Schichtbuch-Eintrag für diesen Tag.")
            if st.button("Eintrag erzeugen", key="logbook_generate"):
                try:
                    write_entry(day_str)
                except Exception:
                    pass
                st.rerun()


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _cached_earnings_rows(tickers: tuple) -> list:
    """Earnings-Abfrage (yfinance → Netz) höchstens alle 6h; fail-open
    leer. Tuple statt Liste, damit st.cache_data hashen kann."""
    from dashboard import departures
    try:
        return departures.earnings_rows(list(tickers))
    except Exception:
        return []


def _render_departures() -> None:
    """D8.1: Werksbahnhof-Abfahrtstafel — was kommt auf die Fabrik zu?
    Makro-Termine, Watchlist-Earnings, nächster Zyklus, nächstes Backup.
    Fail-open: jede Quelle darf einzeln ausfallen."""
    from dashboard import departures
    try:
        extra = _cached_earnings_rows(tuple(departures.watchlist_tickers()))
        rows = departures.upcoming_events(extra_rows=extra)
    except Exception:
        return
    if _theme.is_enabled():
        st.markdown(departures.board_html(rows), unsafe_allow_html=True)
    elif rows:
        st.markdown("**🚉 Anstehende Termine**")
        st.table([{"Datum": r["date"], "Termin": r["label"],
                   "Einstufung": r.get("impact") or r.get("kind", "")}
                  for r in rows])


def _render_power_meter() -> None:
    """D8.2: E-Werk-Stromzähler — echte KI-Kosten (Split, Ersparnis,
    14-Tage-Trend) aus api_savings.json. Fail-open."""
    from dashboard import power_meter
    try:
        energy = power_meter.read_energy()
    except Exception:
        return
    if _theme.is_enabled():
        st.markdown(power_meter.meter_svg(energy), unsafe_allow_html=True)
    else:
        st.caption(
            f"⚡ KI-Kosten: heute {energy['today_cost']:.2f}€ "
            f"(Claude {energy['today_claude']} / Ollama {energy['today_ollama']}) · "
            f"gesamt {energy['total_cost']:.2f}€ · "
            f"gespart {energy['total_saved']:.2f}€"
        )


def _render_ticker_form() -> None:
    """H1.2: Werksauftrag an den Docks — Ticker-Schnellanalyse. Ruft
    exakt dieselbe Queue-Logik wie tabs/log.py auf (analyzers.
    user_request_queue), keine eigene Warteschlangen-Mechanik. Fail-open:
    ein Fehler beim Einreihen zeigt nur keine Erfolgsmeldung."""
    with st.form("factory_ticker_form"):
        ticker_input = st.text_input(
            "Werksauftrag: Ticker zur Analyse einwerfen",
            placeholder="z.B. NVDA, BYD, Rheinmetall …",
        )
        submitted = st.form_submit_button("📥 Einwerfen")

    if submitted and ticker_input.strip():
        from analyzers.user_request_queue import add_ticker, peek
        ticker = ticker_input.strip().upper()
        try:
            if ticker in peek():
                st.success(f"**{ticker}** ist bereits für den nächsten Zyklus vorgemerkt.")
            else:
                add_ticker(ticker)
                st.success(
                    f"✅ **{ticker}** wurde zur Analyse-Queue hinzugefügt.  \n"
                    f"Der Bot analysiert ihn beim nächsten Zyklus (15:00 Uhr oder beim nächsten Start)."
                )
        except Exception:
            pass


def _render_control_panel(total_value: float = 0.0) -> None:
    """H1.1/H1.3: Steuerpult am Werk — Pause-Hebel und Not-Aus-Reset.
    Beide Aktionen mit Bestätigung und Feed-Protokoll (H1-Kopfregel).
    Fail-open: ein Lesefehler zeigt nur einen Hinweis statt zu crashen."""
    from dashboard.controls import (
        pause_status,
        reset_circuit_breaker,
        service_state,
        set_bot_paused,
    )
    with st.expander("🎛 Steuerpult"):
        try:
            status = pause_status()
            svc = service_state()
        except Exception:
            st.caption("Steuerpult derzeit nicht verfügbar.")
            return
        paused = bool(status.get("paused"))

        # ── Ehrlichkeit zuerst: der Flag ist NICHT der Dienst ────────────
        # Ein "Weiter" hebt nur die Pause auf. Läuft der systemd-Dienst
        # nicht, passiert danach trotzdem nichts — das muss hier stehen,
        # sonst täuscht der Schalter Wirkung vor.
        if svc == "active":
            st.caption("Dienst `aktien_bot.service`: läuft.")
        elif svc == "unknown":
            st.caption("Dienst `aktien_bot.service`: Zustand nicht ermittelbar.")
        else:
            st.warning(
                "Dienst `aktien_bot.service` läuft **nicht** (`" + html.escape(svc) + "`). "
                "Der Hebel hier schaltet nur den Pause-Flag — der Bot nimmt die "
                "Arbeit erst wieder auf, wenn du den Dienst zusätzlich selbst "
                "startest (`systemctl start aktien_bot.service`)."
            )

        if paused:
            since = status.get("since") or "?"
            st.markdown(f"**Zustand:** ⏸ pausiert seit {html.escape(str(since)[:16])}")
        else:
            st.markdown("**Zustand:** ▶ nicht pausiert")

        # ── H1.1: Pause-Hebel ───────────────────────────────────────────
        with st.form("factory_pause_form"):
            if paused:
                st.caption("Pause aufheben — der Bot arbeitet dann wieder (sofern der Dienst läuft).")
                confirm = st.checkbox("Ja, Pause aufheben", key="pause_confirm")
                submitted = st.form_submit_button("▶ Weiter")
                new_state, reason = False, ""
            else:
                st.caption("Bot pausieren — laufende Jobs werden nicht mehr ausgeführt.")
                reason = st.text_input("Grund (optional)", key="pause_reason")
                confirm = st.checkbox("Ja, Bot pausieren", key="pause_confirm")
                submitted = st.form_submit_button("⏸ Pausieren")
                new_state = True
        if submitted:
            if not confirm:
                st.warning("Bitte zuerst bestätigen.")
            else:
                try:
                    set_bot_paused(new_state, reason=reason or "", by="dashboard")
                    st.success("Zustand geändert.")
                    st.rerun()
                except Exception:
                    st.error("Umschalten fehlgeschlagen.")

        # ── H1.3: Not-Aus-Reset (zwei Schritte) ─────────────────────────
        st.divider()
        st.markdown("**Not-Aus zurücksetzen**")
        st.caption(
            "Setzt Tagesöffnungs- und Allzeithoch-Referenz auf den aktuellen "
            "Depotwert. Das ist eine bewusste Risiko-Übersteuerung: der heutige "
            "Verlust zählt danach als 0, und das bisherige Allzeithoch wird "
            "verworfen (der Drawdown-Schutz schlägt danach später an)."
        )
        with st.form("factory_breaker_reset_form"):
            ack = st.checkbox("Mir ist klar, dass das eine Risiko-Sperre aufhebt",
                              key="breaker_ack")
            typed = st.text_input('Zum Bestätigen "RESET" eintippen', key="breaker_typed")
            reset_submitted = st.form_submit_button("🔴 Not-Aus zurücksetzen")
        if reset_submitted:
            if not ack or typed.strip().upper() != "RESET":
                st.warning('Beide Schritte nötig: Haken setzen UND "RESET" eintippen.')
            else:
                result = reset_circuit_breaker(total_value, by="dashboard")
                if result is None:
                    st.error("Reset fehlgeschlagen.")
                else:
                    st.success("Not-Aus zurückgesetzt.")
                    st.rerun()

        # ── H1.5: Trockenlauf ───────────────────────────────────────────
        st.divider()
        st.markdown("**Was würde der Bot jetzt tun?**")
        st.caption(
            "Spielt den echten Entscheidungspfad für einen Ticker durch — "
            "gegen eine Kopie des echten Portfolios, mit der letzten "
            "gespeicherten Analyse. Ändert nichts: keine Order, kein "
            "Log-Eintrag, keine Kosten."
        )
        with st.form("factory_dry_run_form"):
            dr_ticker = st.text_input("Ticker", key="dry_run_ticker")
            dr_submitted = st.form_submit_button("🔬 Trockenlauf")
        if dr_submitted and dr_ticker.strip():
            from dashboard.dry_run import dry_run
            try:
                res = dry_run(dr_ticker)
            except Exception:
                res = {"ok": False, "error": "Trockenlauf fehlgeschlagen."}
            if not res.get("ok"):
                st.info(res.get("error") or "Trockenlauf nicht möglich.")
            else:
                _act = res.get("action") or "?"
                _icon = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⏸", "SKIP": "⏭"}.get(_act, "•")
                st.markdown(f"### {_icon} {html.escape(_act)}")
                if res.get("reason"):
                    st.info(html.escape(str(res["reason"])))
                _an = res.get("analysis") or {}
                dc1, dc2, dc3 = st.columns(3)
                dc1.metric("Sentiment", f"{_an.get('sentiment_score') or 0:.2f}")
                dc2.metric("Konfidenz", str(_an.get("confidence") or "–"))
                dc3.metric("Regime", str(res.get("regime") or "–"))
                st.caption(
                    f"Grundlage: Analyse vom "
                    f"{html.escape(str(_an.get('analyzed_at') or '?')[:16])} · "
                    f"Kurs ${res.get('price') or 0:,.2f}"
                )


def _render_achievements() -> None:
    """H7.2: Plaketten-Wand — echte Meilensteine, einmal erreicht bleiben
    sie erreicht (dashboard.achievements.unlocked() merkt das dauerhaft).
    Fail-open: ein Lesefehler zeigt den Katalog einfach leer statt zu
    crashen."""
    with st.expander("🏅 Plaketten-Wand"):
        from dashboard.achievements import unlocked
        try:
            rows = unlocked()
        except Exception:
            rows = []
        for row in rows:
            if row["unlocked"]:
                body = (
                    f"🏅 <b>{html.escape(row['title'])}</b> — erreicht am "
                    f"{html.escape(str(row['unlocked_at']))}"
                )
                if _theme.is_enabled():
                    st.markdown(_theme.panel(body), unsafe_allow_html=True)
                else:
                    st.success(f"🏅 {row['title']} — erreicht am {row['unlocked_at']}")
            else:
                st.caption(f"🔒 {row['title']} — {row['condition_text']}")


def render(ctx) -> None:
    st.subheader("🏭 Fabrik")
    st.caption(
        "Jede Maschine ist ein echtes Subsystem des Bots — ihr Zustand kommt "
        "aus echten Daten, nicht aus Deko. Klick auf eine Maschine für Details. "
        "Aktualisiert sich alle 60 Sekunden."
    )

    @st.fragment(run_every="60s")
    def _scene() -> None:
        state = read_state()
        st.markdown(build_scene_svg(state), unsafe_allow_html=True)
        _maybe_snapshot(state)

        if state.paused:
            st.markdown(
                '<div class="px-panel">⏸ <b>Werk pausiert</b> — Anzeige zeigt '
                'den letzten bekannten Zustand, es laufen keine neuen Zyklen.</div>',
                unsafe_allow_html=True,
            )

        legend_html = " &nbsp;·&nbsp; ".join(
            f'<span style="color:{PALETTE[color]};">●</span> {label}'
            for color, label in _LEGEND
        )
        st.caption(legend_html, unsafe_allow_html=True)

        # W3.2: Klick-Fokus per Query-Param — unbekannte/fehlende IDs
        # werden stillschweigend ignoriert (kein Fehler bei Tippfehlern
        # in der URL).
        focused_id = st.query_params.get("factory")
        if focused_id in MACHINE_IDS:
            machine = state.machines.get(focused_id)
            if machine is not None:
                _render_detail_panel(machine)

    _scene()
    _render_departures()
    _render_power_meter()
    _render_ticker_form()
    # H1.1/H1.3: Bedien-Schalter NUR mit echtem ctx — der Kiosk-Modus
    # (H6.1) ruft render(None) für ein Dauer-Wandbild auf; ein Not-Aus-
    # Reset-Knopf gehört dort nicht hin, und ohne ctx gäbe es auch keinen
    # echten Depotwert als Reset-Referenz (0.0 wäre gefährlich falsch).
    if ctx is not None and hasattr(ctx, "total_value"):
        _render_control_panel(getattr(ctx, "total_value", 0.0))
    _render_archive()
    _render_logbook()
    _render_achievements()
