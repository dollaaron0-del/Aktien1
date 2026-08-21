"""Live-Aktivität-Panel — bis 18.7.2026 eigener Tab, seit dem Karten-
Umbau (Vision W7) Teil des Werksuhr-Detailpanels (dashboard/tabs/factory.py)
— "was macht der Bot gerade" gehört zur Uhr (Zustand/Phase/nächster Lauf)."""
import html
import json
from datetime import datetime

import streamlit as st

from dashboard import theme as _theme

_EV_ICON = {
    "cycle_start":   "🔄",
    "cycle_end":     "🏁",
    "analysis_done": "🔍",
    "trade":         "💼",
    "gate_blocked":  "⛔",
}
# Design D3.1: Farbcodierung je Event-Typ im Terminal-Log (VAR statt Hex,
# damit ein Palette-Wechsel automatisch durchschlägt).
_EV_COLOR_VAR = {
    "trade":         "--px-neon-green",
    "gate_blocked":  "--px-copper",
    "cycle_start":   "--px-cobalt",
    "cycle_end":     "--px-cobalt",
    "analysis_done": "--px-text",
}


def _render_activity_feed_pixel(rows, ctx) -> None:
    lines = []
    for _ev in rows:
        ic = _EV_ICON.get(_ev.get("event"), "•")
        ts = html.escape((_ev.get("ts") or "")[:16].replace("T", " "))
        var = _EV_COLOR_VAR.get(_ev.get("event"), "--px-text")
        tk = f" <b>{html.escape(ctx.ticker_label(_ev['ticker']))}</b>" if _ev.get("ticker") else ""
        dt = f" — {html.escape(str(_ev['detail']))}" if _ev.get("detail") else ""
        lines.append(f'<div style="color:var({var});">{ic} {ts}{tk}{dt}</div>')
    st.markdown(f'<div class="px-terminal">{"".join(lines)}</div>', unsafe_allow_html=True)


def _render_activity_feed_plain(rows, ctx) -> None:
    for _ev in rows:
        _ic = _EV_ICON.get(_ev.get("event"), "•")
        _ts = (_ev.get("ts") or "")[:16].replace("T", " ")
        _tk = f" **{ctx.ticker_label(_ev['ticker'])}**" if _ev.get("ticker") else ""
        _dt = f" — {_ev['detail']}" if _ev.get("detail") else ""
        st.markdown(f"{_ic} `{_ts}`{_tk}{_dt}")


# Design D3.2: Fertigungsstraße — feste Reihenfolge der Zyklus-Phasen.
_PHASE_ORDER = ["Start", "Exits prüfen", "Vorladen", "Analyse"]


def _render_phase_timeline_pixel(phases) -> None:
    by_name = {p["phase"]: p for p in phases}
    stations = []
    for name in _PHASE_ORDER:
        p = by_name.get(name)
        if p is None:
            stations.append((name, "border", "–", False))
            continue
        running = p.get("ended_at") is None
        mins = p["duration_seconds"] / 60
        dur = f"{mins:.1f} min" if mins >= 1 else f"{p['duration_seconds']:.0f} s"
        var = "--px-cobalt" if running else "--px-neon-green"
        stations.append((name, var, dur, running))
    # Phasen außerhalb der bekannten Reihenfolge (z.B. künftige Erweiterungen)
    # hinten anhängen statt stillschweigend zu verschlucken.
    for p in phases:
        if p["phase"] not in _PHASE_ORDER:
            mins = p["duration_seconds"] / 60
            dur = f"{mins:.1f} min" if mins >= 1 else f"{p['duration_seconds']:.0f} s"
            var = "--px-cobalt" if p.get("ended_at") is None else "--px-neon-green"
            stations.append((p["phase"], var, dur, p.get("ended_at") is None))

    dots = []
    for name, var, dur, running in stations:
        pulse = " px-blink" if running else ""
        label = html.escape(name)
        dur_safe = html.escape(dur)
        dots.append(
            f'<div style="display:flex; flex-direction:column; align-items:center; flex:1;">'
            f'<div style="width:14px; height:14px; border-radius:50%; '
            f'background:var({var}); margin-bottom:4px;" class="{pulse.strip()}"></div>'
            f'<div style="font-size:0.85rem; color:var(--px-text-muted);">{label}</div>'
            f'<div style="font-family:\'VT323\',\'Courier New\',monospace; '
            f'font-size:1.05rem; color:var(--px-text);">{dur_safe}</div>'
            f'</div>'
        )
    line = (
        '<div style="display:flex; align-items:center; position:relative;">'
        + f'<div style="position:absolute; top:7px; left:5%; right:5%; height:2px; '
        + 'background:var(--px-border); z-index:0;"></div>'
        + "".join(dots)
        + "</div>"
    )
    st.markdown(_theme.panel(line), unsafe_allow_html=True)


def _render_phase_timeline_plain(phases) -> None:
    for _p in phases:
        _mins = _p["duration_seconds"] / 60
        _dur_str = f"{_mins:.1f} min" if _mins >= 1 else f"{_p['duration_seconds']:.0f} s"
        _running = " ⏳ läuft noch" if _p.get("ended_at") is None else ""
        st.markdown(f"- **{_p['phase']}**: {_dur_str}{_running}")


_ORDER_ACTION_ICON = {"BUY": "🟢", "SELL": "🔴"}
_ORDER_STATUS_ICON = {"filled": "✅", "error": "⚠️", "cancelled": "🚫"}
_ORDER_STATUS_LED = {"filled": "ok", "error": "err", "cancelled": "off"}


def render(ctx) -> None:
    st.subheader("📡 Live-Aktivität")
    st.caption(
        "Strukturierte Bot-Events (Zyklen, Analysen, Trades) statt Log-Dateien. "
        "Füllt sich, sobald der Bot läuft."
    )

    try:
        from system.live_status import feed_recent as _feed_recent
        _ev_rows = _feed_recent(limit=50)
    except Exception:
        _ev_rows = []

    if _ev_rows:
        if _theme.is_enabled():
            _render_activity_feed_pixel(_ev_rows, ctx)
        else:
            _render_activity_feed_plain(_ev_rows, ctx)
    else:
        st.info(
            "Noch keine Events aufgezeichnet — der Aktivitätsfeed füllt sich "
            "ab dem nächsten Bot-Lauf (Bot ist aktuell pausiert)."
            if ctx._hdr_paused else
            "Noch keine Events aufgezeichnet — der Feed füllt sich ab dem "
            "nächsten Analyse-Zyklus."
        )

    st.divider()

    # ── Zyklus-Zeitleiste (Roadmap 1.5e) ────────────────────────────────────
    st.subheader("🕒 Zyklus-Zeitleiste")
    try:
        from system.live_status import phase_durations as _phase_durations
        _phases = _phase_durations(ctx._ls)
    except Exception:
        _phases = []
    if _phases:
        if _theme.is_enabled():
            _render_phase_timeline_pixel(_phases)
        else:
            _render_phase_timeline_plain(_phases)
    else:
        st.caption(
            "Noch keine Zeitleiste — füllt sich mit dem nächsten Analyse-Zyklus."
            if not ctx._hdr_paused else
            "Noch keine Zeitleiste — der Bot ist aktuell pausiert."
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

    def _fmt_us(v):
        # systemd liefert µs-Epoch (int) oder Klartext, je nach Version
        try:
            return datetime.fromtimestamp(int(v) / 1_000_000).strftime("%d.%m. %H:%M")
        except (TypeError, ValueError, OSError):
            return str(v) if v else "–"

    if _timers:
        if _theme.is_enabled():
            _rows = []
            for _t in _timers:
                _unit = html.escape(_t.get("unit") or "?")
                _rows.append(
                    f"{_unit} · nächster Lauf: {html.escape(_fmt_us(_t.get('next')))} · "
                    f"letzter: {html.escape(_fmt_us(_t.get('last')))}"
                )
            st.markdown(
                _theme.panel(
                    '<div style="font-family:&quot;VT323&quot;,&quot;Courier New&quot;,monospace; '
                    'font-size:1.05rem;">' + "<br>".join(_rows) + "</div>"
                ),
                unsafe_allow_html=True,
            )
        else:
            st.markdown("**systemd-Timer:**")
            for _t in _timers:
                _unit = _t.get("unit") or "?"
                st.markdown(f"- `{_unit}` · nächster Lauf: {_fmt_us(_t.get('next'))} · "
                            f"letzter: {_fmt_us(_t.get('last'))}")
    else:
        st.caption("Keine aktiven systemd-Timer gefunden (oder Abfrage nicht "
                   "möglich) — Timer sind bei pausiertem Bot disabled.")

    st.divider()

    # ── Order-Historie (Roadmap 1.5f) ───────────────────────────────────────
    st.subheader("📋 Order-Historie")
    st.caption(
        "Jede BUY/SELL-Order von Paper-/IBKR-Broker, protokolliert direkt am "
        "Rückgabewert (unabhängig vom internen Return-Pfad)."
    )
    try:
        from broker.order_log import get_order_log
        _orders = get_order_log().recent(limit=30)
    except Exception:
        _orders = []
    if _orders:
        for _o in _orders:
            _ts = (_o.get("ts") or "")[:16].replace("T", " ")
            _tk = ctx.ticker_label(_o["ticker"]) if _o.get("ticker") else "?"
            _shares, _price = _o.get("shares"), _o.get("fill_price")
            if _o.get("status") == "filled" and isinstance(_shares, (int, float)) \
                    and isinstance(_price, (int, float)):
                _detail = f"{_shares:g} Stk. @ {_price:.2f}"
                if _o.get("partial"):
                    _detail += " (Teilausführung)"
            else:
                _detail = _o.get("reason") or ""
            if _theme.is_enabled():
                _aicon = _ORDER_ACTION_ICON.get(_o.get("action"), "•")
                _led = _theme.led(_ORDER_STATUS_LED.get(_o.get("status"), "off"),
                                  _o.get("action") or "?")
                _badge = (
                    ' <span style="color:var(--px-copper);">(Teilausführung)</span>'
                    if _o.get("partial") else ""
                )
                st.markdown(
                    _theme.panel(
                        f"{_aicon} {_led} · `{html.escape(_ts)}` "
                        f"<b>{html.escape(_tk)}</b> · {html.escape(_o.get('mode') or '?')} "
                        f"— {html.escape(_detail)}{_badge}"
                    ),
                    unsafe_allow_html=True,
                )
            else:
                _aicon = _ORDER_ACTION_ICON.get(_o.get("action"), "•")
                _sicon = _ORDER_STATUS_ICON.get(_o.get("status"), "")
                st.markdown(
                    f"{_aicon}{_sicon} `{_ts}` **{_tk}** {_o.get('action')} "
                    f"· {_o.get('mode') or '?'} — {_detail}"
                )
    else:
        st.caption(
            "Noch keine Orders protokolliert — füllt sich mit dem nächsten "
            "Trade."
        )
