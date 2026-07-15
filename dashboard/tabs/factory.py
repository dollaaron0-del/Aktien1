"""Tab "🏭 Fabrik" — interaktives Wimmelbild (Vision W1, docs/DESIGN_FABRIK.md).

Jede Maschine spiegelt ein echtes Subsystem; die Szene ist eine dritte
Darstellungsform neben Tabellen und Charts, kein Deko-Bild. Rendert in
BEIDEN Theme-Modi (hängt nur an PALETTE-Konstanten, nicht an
theme.is_enabled() — die Fabrik IST das Pixel-Theme, kein optionaler Zusatz
darauf)."""
import streamlit as st

from dashboard.factory.scene import build_scene_svg
from dashboard.factory.state import read_state
from dashboard.theme import PALETTE

_LEGEND = (
    ("neon_green", "aktiv/gesund"),
    ("cobalt", "läuft gerade"),
    ("amber", "Warnung"),
    ("red", "Fehler/ausgelöst"),
    ("border", "aus/keine Daten"),
)


def render(ctx) -> None:
    st.subheader("🏭 Fabrik")
    st.caption(
        "Jede Maschine ist ein echtes Subsystem des Bots — ihr Zustand kommt "
        "aus echten Daten, nicht aus Deko. Aktualisiert sich alle 60 Sekunden."
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

    _scene()
