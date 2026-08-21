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


def _render_warehouse_movements(m: MachineState) -> None:
    """L3.6: die letzten Bestandsbewegungen — Zu-/Abgänge sichtbar
    machen, nicht nur den Ist-Bestand. Quelle: echte trades-Tabelle
    (state._warehouse_movements). Läuft VOR dem Positions-Check, damit
    die Historie auch bei leerem Lager sichtbar bleibt."""
    moves = (m.payload or {}).get("movements") or {}
    c1, c2 = st.columns(2)
    c1.metric("Wareneingang heute", f"+{moves.get('in_today', 0)}")
    c2.metric("Warenausgang heute", f"−{moves.get('out_today', 0)}")
    recent = moves.get("recent") or []
    if recent:
        st.markdown("**Letzte Bestandsbewegungen**")
        st.table([
            {
                "Zeit": str(r.get("ts") or "")[:16].replace("T", " "),
                "Ticker": r.get("ticker"),
                "Bewegung": ("+" if r.get("action") == "BUY" else "−")
                            + f"{float(r.get('shares') or 0):g} Stk.",
            }
            for r in recent
        ])


def _detail_warehouse(m: MachineState) -> None:
    _render_warehouse_movements(m)
    positions = (m.payload or {}).get("positions") or {}
    if not positions:
        st.caption("Keine offenen Positionen.")
        return
    st.markdown("**Aktueller Bestand**")
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


def _render_warehouse_stock_browser(ctx) -> None:
    """Karten-Umbau 18.7.2026, User-Vorgabe wörtlich: "Wenn man auf das
    Lager klickt öffnet sich ein Tab in der man alle Aktien suchen kann."
    Kartei (früher eigener Tab) + Watchlist/IPO-Pipeline (früher eigener
    Tab) gehören inhaltlich zum Lager — hier ist der Ort, an dem man
    JEDE bekannte Aktie findet, nicht nur die aktuell gekauften. Jede
    Sektion einzeln fail-open."""
    st.divider()
    st.markdown("### 🔎 Alle Aktien durchsuchen")
    st.caption(
        "Jede je analysierte oder beobachtete Aktie — nicht nur die aktuell "
        "gekauften Kisten oben. Wird eine Aktie gekauft, wandert ihre Kiste "
        "über das Förderband (die Analyse-Pipeline) hierher ins Lager."
    )
    try:
        from dashboard import dossier_panel as _dossier_panel
        _dossier_panel.render(ctx)
    except Exception:
        st.caption("Aktien-Suche derzeit nicht verfügbar.")

    st.divider()
    st.markdown("### 🚀 Watchlist & IPO-Pipeline")
    try:
        from dashboard import watchlist_panel as _watchlist_panel
        _watchlist_panel.render(ctx)
    except Exception:
        st.caption("Watchlist derzeit nicht verfügbar.")


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


def _detail_analyzer(m: MachineState) -> None:
    """Vision-Feedback 17.7. (Fabrik als Hauptding): Klartext statt nur
    der Rauch-Intensität aus der Szene — WELCHE Routen genau liefen."""
    s = m.payload or {}
    c1, c2 = st.columns(2)
    c1.metric("Anteil (letzte 50)", f"{s.get('n', 0)}/{s.get('total', 0)}")
    breakdown = s.get("route_breakdown") or {}
    if breakdown:
        st.markdown("**Genauer Modell-Pfad**")
        st.table([
            {"Route": route, "Anzahl": n}
            for route, n in sorted(breakdown.items(), key=lambda kv: -kv[1])
        ])


def _detail_breaker(m: MachineState) -> None:
    s = m.payload or {}
    c1, c2 = st.columns(2)
    c1.metric("Tagesverlust", f"{s.get('daily_pct', 0):+.1f}%")
    c2.metric("Drawdown vom Hoch", f"{s.get('drawdown_pct', 0):.1f}%")
    st.markdown(f"**Ausgelöst:** {'🔴 Ja' if s.get('triggered') else '🟢 Nein'}")
    if s.get("open_value") is not None:
        st.caption(f"Tagesöffnungswert: ${s['open_value']:,.0f} · "
                  f"Allzeithoch: ${s.get('peak_value', 0):,.0f}")
    last_reset = s.get("last_reset")
    if last_reset:
        st.caption(f"Letzter Reset: {str(last_reset.get('at') or '')[:16].replace('T', ' ')} "
                  f"durch {last_reset.get('by') or '?'}")


def _detail_gate(m: MachineState) -> None:
    s = m.payload or {}
    st.markdown(f"**IB-Gateway:** {s.get('host') or '–'}:{s.get('port') or '–'}")
    st.markdown(f"**Erreichbar:** {'🟢 Ja' if m.status == 'ok' else '🔴 Nein'}")


def _detail_weather(m: MachineState) -> None:
    s = m.payload or {}
    st.markdown(f"**Marktregime:** {s.get('regime') or 'unbekannt'}")
    if s.get("demand_label"):
        st.markdown(f"**Energienachfrage:** {s['demand_label']}")
    if s.get("timestamp"):
        st.caption(f"Stand: {str(s['timestamp'])[:16].replace('T', ' ')} Uhr")


def _detail_backup_bot(m: MachineState) -> None:
    s = m.payload or {}
    st.markdown(f"**Letztes Backup:** vor {s.get('age_hours', 0):.0f}h")
    recent = s.get("recent") or []
    if recent:
        st.markdown("**Letzte Backups**")
        st.table([
            {"Datei": r["name"], "Alter": f"{r['age_hours']:.0f}h",
            "Größe": f"{r['size_mb']:.0f} MB"}
            for r in recent
        ])


def _detail_control_room(m: MachineState) -> None:
    s = m.payload or {}
    c1, c2, c3 = st.columns(3)
    c1.metric("Dashboard-Passwort", "gesetzt" if s.get("password_set") else "AUS")
    c2.metric("Anthropic-Key", "gesetzt" if s.get("api_key_set") else "fehlt")
    c3.metric("Broker-Modus", (s.get("broker_mode") or "–").upper())
    if not s.get("password_set"):
        st.caption("⚠️ Kein Dashboard-Passwort gesetzt — dokumentierte Härtungslücke (dashboard/auth.py).")


_DETAIL_RENDERERS = {
    "conveyor": _detail_conveyor,
    "warehouse": _detail_warehouse,
    "docks": _detail_docks,
    "lab": _detail_lab,
    "clock": _detail_clock,
    "analyzer_claude": _detail_analyzer,
    "analyzer_ollama": _detail_analyzer,
    "breaker": _detail_breaker,
    "gate": _detail_gate,
    "weather": _detail_weather,
    "backup_bot": _detail_backup_bot,
    "control_room": _detail_control_room,
}


def _render_detail_panel(m: MachineState, ctx=None) -> None:
    """Detail-Block unter der Szene (Vision W3.2/W3.3, erweitert 17.7.
    nach User-Feedback "Fabrik soll das Hauptding werden"): JEDE der elf
    Maschinen hat einen eigenen Block mit denselben Daten, die sonst nur
    in den Tabellen-Tabs stünden. Generischer Fallback bleibt als
    zweite Sicherheitsnetz-Schicht (kaputter Spezial-Block → Rohdaten
    statt Crash). Seit dem Tab-Umbau (18.7.2026) trägt die Wetterstation
    zusätzlich das volle Markt-Regime-Panel (früher eigener Tab) —
    nur mit echtem ctx, der Kiosk-Modus (ctx=None) bleibt schlank."""
    st.divider()
    st.markdown(f"### {m.label}")
    st.caption(f"Status: {_STATUS_LABEL.get(m.status, m.status)}")

    rendered = False
    renderer = _DETAIL_RENDERERS.get(m.id)
    if renderer is not None:
        try:
            renderer(m)
            rendered = True
        except Exception:
            pass  # Fail-open: generischer Fallback greift trotzdem

    if not rendered:
        for line in m.tooltip:
            st.markdown(f"- {line}")
        if m.payload:
            st.json(m.payload)

    if m.id == "weather" and ctx is not None:
        try:
            from dashboard import regime_panel as _regime_panel
            st.divider()
            _regime_panel.render(ctx)
        except Exception:
            pass  # Fail-open: Wetter-Kurzinfo oben steht bereits

    # Analysator-Klick trägt das volle Analyse-Log (früher eigener Tab) —
    # gleiche ctx-Regel wie beim Regime-Panel.
    if m.id in ("analyzer_claude", "analyzer_ollama") and ctx is not None:
        try:
            from dashboard import analysis_log_panel as _alog_panel
            st.divider()
            _alog_panel.render(ctx)
        except Exception:
            pass  # Fail-open: model_route-Breakdown oben steht bereits

    # Qualitätslabor trägt das volle Trades-&-Lernen-Panel (früher eigener
    # Tab: Kalibrierung, Lernkurve, Genealogie, Paper-Forward, Thesis-Board)
    # — passt inhaltlich zum "was hat das Werk gelernt" der Lab-Maschine.
    if m.id == "lab" and ctx is not None:
        try:
            from dashboard import trades_panel as _trades_panel
            st.divider()
            _trades_panel.render(ctx)
        except Exception:
            pass  # Fail-open: Gelabelt/Gewinne/Verluste-Kacheln oben stehen bereits

    # Kontrollraum trägt das volle Einstellungen-Formular (früher eigener
    # Tab) — gleiche ctx-Regel: Kiosk-Modus bleibt bei der Kurzinfo oben.
    if m.id == "control_room" and ctx is not None:
        try:
            from dashboard import settings_panel as _settings_panel
            st.divider()
            _settings_panel.render(ctx)
        except Exception:
            pass  # Fail-open: Status-Kacheln oben stehen bereits

    # Lager trägt das volle Portfolio-Panel (Wachstumsphase, Ziel-Risiko,
    # Positionstabelle, Bot-Score, Wertverlauf, Transaktionen — früher
    # eigener Tab) sowie Kartei-Suche + Watchlist/IPO-Pipeline (ebenfalls
    # früher eigene Tabs) — User-Vorgabe 18.7.: "alle Aktien suchen"
    # gehört ans Lager, nicht in einen separaten Tab.
    if m.id == "warehouse" and ctx is not None:
        try:
            from dashboard import portfolio_panel as _portfolio_panel
            st.divider()
            _portfolio_panel.render(ctx)
        except Exception:
            pass  # Fail-open: Bestand/Bewegungen oben stehen bereits
        _render_warehouse_stock_browser(ctx)

    # Werksuhr trägt das volle Live-Aktivität-Panel (früher eigener Tab)
    # — "was macht der Bot gerade" ist Uhr-Domäne (Zustand/Phase/nächster
    # Lauf), gleiche ctx-Regel wie die anderen Panel-Merges.
    if m.id == "clock" and ctx is not None:
        try:
            from dashboard import live_panel as _live_panel
            st.divider()
            _live_panel.render(ctx)
        except Exception:
            pass  # Fail-open: Zustand/Phase/nächster Lauf oben stehen bereits

    # Förderband trägt die volle Entscheidungs-Transparenz (früher eigener
    # Tab, inkl. Signal-Queue) — die kurze Funnel-Zusammenfassung oben
    # bleibt, das hier ist die Tiefe (Tag-Auswahl, Skip-Gründe, Vergleich).
    if m.id == "conveyor" and ctx is not None:
        try:
            from dashboard import decisions_panel as _decisions_panel
            st.divider()
            _decisions_panel.render(ctx)
        except Exception:
            pass  # Fail-open: Funnel-Metriken oben stehen bereits


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
def _cached_earnings_rows(tickers: tuple, held: tuple = ()) -> list:
    """Earnings-Abfrage (yfinance → Netz) höchstens alle 6h; fail-open
    leer. Tuples statt Listen, damit st.cache_data hashen kann.
    `held` (L3.1) markiert Earnings gehaltener Titel als Frachtrisiko —
    liegt bewusst INNERHALB des Caches, damit die Depot-Lage denselben
    6h-Abruf nutzt statt einen zweiten auszulösen."""
    from dashboard import departures
    try:
        return departures.earnings_rows(list(tickers), held=list(held))
    except Exception:
        return []


def _render_instruments(ctx) -> None:
    """Leitstand-Instrumente (Design D7.1) — Tab-Umbau 18.7.2026: aus dem
    App-Kopf hierher verschoben, die Leitstand-Optik gehört in die Halle.
    Manometer (Tagesverlust vs. Circuit-Breaker-Limit), Tank (Rest des
    Claude-Tagesbudgets), 7-Segment (Depotwert). Braucht echten ctx
    (Depotwert); Kiosk-Modus (ctx=None) bleibt reine Szene. Fail-open."""
    if ctx is None or not hasattr(ctx, "total_value"):
        return
    try:
        from dashboard import instruments as _instr
        from portfolio.circuit_breaker import CircuitBreaker as _InstrCB
        from portfolio.circuit_breaker import _MAX_DAILY_LOSS as _CB_LIMIT

        total_value = float(getattr(ctx, "total_value", 0.0))
        _cb_st = _InstrCB().status(total_value)
        _loss_pct = max(0.0, -float(_cb_st.get("daily_pct") or 0.0))
        _pressure = (_loss_pct / (_CB_LIMIT * 100) * 100) if _CB_LIMIT else 0.0

        from analyzers.api_cost_tracker import APICostTracker as _InstrCost
        _cost_s = _InstrCost().summary()
        _cost_today = float(_cost_s.get("today_cost_eur") or 0.0)
        _cost_limit = float(_cost_s.get("daily_limit_eur") or 0.0)
        _fuel = (max(0.0, 100.0 - _cost_today / _cost_limit * 100)
                 if _cost_limit > 0 else 100.0)

        _ic1, _ic2, _ic3 = st.columns([2, 1.3, 2.7])
        _ic1.markdown(
            _instr.gauge_svg(
                _pressure, "KESSELDRUCK",
                f"Tagesverlust {_loss_pct:.1f}% / Limit {_CB_LIMIT * 100:.0f}%",
            ),
            unsafe_allow_html=True,
        )
        _ic2.markdown(
            _instr.tank_svg(
                _fuel, "TREIBSTOFF",
                f"Claude {_cost_today:.2f}/{_cost_limit:.2f}€",
            ),
            unsafe_allow_html=True,
        )
        _ic3.markdown(
            _instr.seven_segment_svg(f"{total_value:.0f}", "DEPOTWERT USD"),
            unsafe_allow_html=True,
        )
    except Exception:
        pass


def _render_memories() -> None:
    """L2.2: „Heute vor …"-Plakette — echte Ereignisse aus der eigenen
    Geschichte (dashboard/memories.py). Gibt es heute nichts zu
    erinnern, erscheint NICHTS (kein „noch nichts passiert"-Gefüll).
    Fail-open."""
    try:
        from dashboard.memories import memories_for
        rows = memories_for()
    except Exception:
        return
    if not rows:
        return
    if _theme.is_enabled():
        items = "".join(
            f'<div style="font-size:15px;padding:1px 0;">'
            f'<span style="color:{PALETTE["amber"]};">{html.escape(r["when"])}:</span> '
            f'{html.escape(r["text"])} '
            f'<span style="color:{PALETTE["text_muted"]};font-size:12px;">'
            f'({html.escape(r["date"])})</span></div>'
            for r in rows
        )
        st.markdown(
            f'<div class="px-panel" style="font-family:VT323,monospace;">'
            f'<div style="color:{PALETTE["amber"]};letter-spacing:1px;">'
            f'📅 HEUTE VOR …</div>{items}</div>',
            unsafe_allow_html=True,
        )
    else:
        for r in rows:
            st.caption(f"📅 {r['when']}: {r['text']} ({r['date']})")


def _render_departures() -> None:
    """D8.1: Werksbahnhof-Abfahrtstafel — was kommt auf die Fabrik zu?
    Makro-Termine, Earnings, nächster Zyklus, nächstes Backup — und
    (L3.1) die planmäßigen Abfahrten der offenen Positionen.
    Fail-open: jede Quelle darf einzeln ausfallen."""
    from dashboard import departures
    try:
        held = departures.held_tickers()
        # Gehaltene Titel MÜSSEN mit abgefragt werden, auch wenn sie
        # (nicht mehr) auf der Watchlist stehen — sonst fehlte gerade
        # beim riskantesten Fall der Earnings-Termin.
        tickers = tuple(sorted(set(departures.watchlist_tickers()) | set(held)))
        extra = _cached_earnings_rows(tickers, tuple(sorted(held)))
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
    exakt dieselbe Queue-Logik wie das Analyse-Log-Panel (dashboard/analysis_log_panel.py) auf (analyzers.
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
        # Vision-Transfer 24.7.2026: das animierte Canvas (factory/canvas.py)
        # ersetzt die statische SVG-Szene im Pixel-Theme. Es läuft in einem
        # components.html-iframe (st.markdown würde die Skripte strippen) und
        # ist an denselben read_state() gebunden. Die SVG-Szene (scene.py)
        # bleibt als Notausstieg: Plain-Theme, Canvas-Fehler, sowie
        # unverändert Archiv-Replay/Report/Mobile (D6.2-Prinzip).
        rendered = False
        if _theme.is_enabled():
            try:
                import streamlit.components.v1 as _components
                from dashboard.factory.canvas import (
                    SCENE_EMBED_HEIGHT as _scene_h,
                )
                from dashboard.factory.canvas import build_scene_html as _scene_html
                _components.html(_scene_html(state), height=_scene_h, scrolling=False)
                rendered = True
            except Exception:
                rendered = False  # Fail-open: SVG-Fallback unten greift
        if not rendered:
            svg = build_scene_svg(state)
            # W8.1: der Vollbild-Rahmen ist eine reine Pixel-Theme-Zutat (die
            # CSS-Klasse existiert nur in _base_css) — Plain bekommt weiterhin
            # exakt das nackte SVG-Markup (D6.2-Prinzip: Notausstieg bleibt
            # wortwörtlich das alte Verhalten).
            if _theme.is_enabled():
                svg = f'<div class="px-scene-wrap">{svg}</div>'
            st.markdown(svg, unsafe_allow_html=True)
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
                # W8.2 (18.7.2026, User-Vorgabe: Detailpanels als echtes
                # Overlay statt inline unter der Szene): der Server weiß
                # bereits (über den Query-Parameter), ob ein Panel offen
                # ist — reines CSS-Positionieren des schon bedingt
                # gerenderten Inhalts reicht, kein JS/:target-Trick nötig.
                # Backdrop-<a> UND Schließen-<a> zeigen auf "?" (räumt
                # factory+dossier gemeinsam auf). Nur bei Pixel-Theme
                # (D6.2-Prinzip: Plain bleibt beim alten Inline-Verhalten).
                if _theme.is_enabled():
                    st.markdown(
                        '<a class="px-detail-backdrop" href="?" target="_self" '
                        'aria-label="Schließen"></a>'
                        '<div class="px-detail-panel">'
                        '<a class="px-detail-close" href="?" target="_self">'
                        '✕ Schließen</a>',
                        unsafe_allow_html=True,
                    )
                _render_detail_panel(machine, ctx)
                if _theme.is_enabled():
                    st.markdown('</div>', unsafe_allow_html=True)

    _scene()
    _render_instruments(ctx)
    _render_memories()
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
