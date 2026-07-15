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
    _render_archive()
    _render_logbook()
