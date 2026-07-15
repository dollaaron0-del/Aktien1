"""Tab "🏭 Fabrik" — interaktives Wimmelbild (Vision W1, docs/DESIGN_FABRIK.md).

Jede Maschine spiegelt ein echtes Subsystem; die Szene ist eine dritte
Darstellungsform neben Tabellen und Charts, kein Deko-Bild. Rendert in
BEIDEN Theme-Modi (hängt nur an PALETTE-Konstanten, nicht an
theme.is_enabled() — die Fabrik IST das Pixel-Theme, kein optionaler Zusatz
darauf)."""
import streamlit as st

from dashboard.factory.scene import build_scene_svg
from dashboard.factory.state import MACHINE_IDS, MachineState, read_state
from dashboard.theme import PALETTE

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
