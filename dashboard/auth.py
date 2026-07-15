"""
Passwort-Gate fürs Dashboard — Zusatzhärtung zu 0.4 (Roadmap Block 0).

Der Netzwerk-Teil war der Kernfix (11.7.: Dashboard nur noch 127.0.0.1 +
SSH-Tunnel statt 0.0.0.0 im Netz). Aber der Settings-Tab kann echte
.env-Keys (Anthropic/Telegram/IBKR) lesen UND schreiben, und es gab bisher
kein In-App-Login — wer den Tunnel erreicht (z.B. ein anderer lokaler
Prozess/User auf demselben Host), sieht/ändert sofort alles.

Optional per DASHBOARD_PASSWORD in .env, Default AUS: ungesetzt verhält
sich das Dashboard exakt wie vorher (kein Gate) — kein Breaking Change für
den aktuellen Betrieb, nur eine Härtungs-Option.
"""
from __future__ import annotations

import os
import secrets

import streamlit as st

from dashboard import theme as _theme

_SESSION_KEY = "_dashboard_authed"
_INPUT_KEY = "_dashboard_pw_input"


def require_login() -> None:
    """Zeigt ein Login-Formular und stoppt das Rendering, bis das Passwort
    stimmt. No-Op, wenn DASHBOARD_PASSWORD nicht gesetzt ist (Default)."""
    password = os.getenv("DASHBOARD_PASSWORD", "").strip()
    if not password:
        return
    if st.session_state.get(_SESSION_KEY):
        return

    # Nur Markup/Klassen (D1.6) — die Login-Logik unten bleibt unverändert.
    # st.title() bleibt ein echtes st.title (Test-Vertrag: at.title[0].value
    # enthält "Login"); die Pixel-Font dafür kommt aus einer h1-Regel in
    # theme.py, die NUR hier greift (st.title wird sonst nirgends verwendet).
    if _theme.is_enabled():
        _splash_uri = _theme.image_b64("splash.png")  # D5.3, Fallback = kein Bild
        if _splash_uri:
            st.markdown(f'<img src="{_splash_uri}" style="max-width:100%;">',
                       unsafe_allow_html=True)
    st.title("🔒 Dashboard-Login")
    with st.container(border=True):
        entered = st.text_input("Passwort", type="password", key=_INPUT_KEY)
        if entered:
            if secrets.compare_digest(entered, password):
                st.session_state[_SESSION_KEY] = True
                st.rerun()
            else:
                st.error("Falsches Passwort.")
    st.stop()
