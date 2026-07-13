"""Tab "Live-Aktivität" — ausgelagert aus dashboard/app.py (Roadmap 4.4a)."""
import json
from datetime import datetime

import streamlit as st


def render(ctx) -> None:
    st.subheader("📡 Live-Aktivität")
    st.caption(
        "Strukturierte Bot-Events (Zyklen, Analysen, Trades) statt Log-Dateien. "
        "Füllt sich, sobald der Bot läuft."
    )

    _EV_ICON = {
        "cycle_start":   "🔄",
        "cycle_end":     "🏁",
        "analysis_done": "🔍",
        "trade":         "💼",
        "gate_blocked":  "⛔",
    }
    try:
        from system.live_status import feed_recent as _feed_recent
        _ev_rows = _feed_recent(limit=50)
    except Exception:
        _ev_rows = []

    if _ev_rows:
        for _ev in _ev_rows:
            _ic = _EV_ICON.get(_ev.get("event"), "•")
            _ts = (_ev.get("ts") or "")[:16].replace("T", " ")
            _tk = f" **{ctx.ticker_label(_ev['ticker'])}**" if _ev.get("ticker") else ""
            _dt = f" — {_ev['detail']}" if _ev.get("detail") else ""
            st.markdown(f"{_ic} `{_ts}`{_tk}{_dt}")
    else:
        st.info(
            "Noch keine Events aufgezeichnet — der Aktivitätsfeed füllt sich "
            "ab dem nächsten Bot-Lauf (Bot ist aktuell pausiert)."
            if ctx._hdr_paused else
            "Noch keine Events aufgezeichnet — der Feed füllt sich ab dem "
            "nächsten Analyse-Zyklus."
        )

    st.divider()

    # ── Nächste Aktionen (Roadmap 1.5c) ─────────────────────────────────────
    st.subheader("⏭ Nächste Aktionen")
    _next_bits = []
    if ctx._ls and ctx._ls.get("state") == "idle" and ctx._ls.get("next_run"):
        _next_bits.append("**Nächster Scheduler-Lauf:** "
                          + ctx._ls["next_run"][:16].replace("T", " ") + " Uhr")
    if ctx._hdr_paused:
        _next_bits.append("⏸ Bot pausiert — es sind keine Zyklen geplant, "
                          "bis er wieder gestartet wird.")
    for _nb in _next_bits:
        st.markdown(_nb)

    # systemd-Timer des Projekts (Backup, Pre-Market-Check, Quellen-Report):
    # letzter/nächster Lauf, ohne SSH + systemctl-Kommandos.
    try:
        import subprocess as _sp
        _lt = _sp.run(
            ["systemctl", "list-timers", "aktien_*", "--all", "--no-pager",
             "--output=json"],
            capture_output=True, text=True, timeout=5,
        )
        _timers = json.loads(_lt.stdout) if _lt.returncode == 0 and _lt.stdout else []
    except Exception:
        _timers = []
    if _timers:
        st.markdown("**systemd-Timer:**")
        for _t in _timers:
            _unit = _t.get("unit") or "?"
            _nxt = _t.get("next")
            _lst = _t.get("last")

            def _fmt_us(v):
                # systemd liefert µs-Epoch (int) oder Klartext, je nach Version
                try:
                    return datetime.fromtimestamp(int(v) / 1_000_000).strftime("%d.%m. %H:%M")
                except (TypeError, ValueError, OSError):
                    return str(v) if v else "–"
            st.markdown(f"- `{_unit}` · nächster Lauf: {_fmt_us(_nxt)} · "
                        f"letzter: {_fmt_us(_lst)}")
    else:
        st.caption("Keine aktiven systemd-Timer gefunden (oder Abfrage nicht "
                   "möglich) — Timer sind bei pausiertem Bot disabled.")
