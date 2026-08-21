"""
dashboard/report.py — Wochen-Report-Export (Ausbau-Roadmap H5.1).

build_weekly_html() baut ein in sich geschlossenes HTML-Dokument (Inline-
CSS aus der PALETTE, KEINE externen Ressourcen — kein CDN, keine
Web-Fonts, keine externen Bilder) für den Download: KPI-Zahlen,
Wochen-Funnel (dashboard.compare.week_stats, H2.4), aktuelle Fabrik-Szene
(dashboard.factory.render_scene) und die letzten 10 Entscheidungen.
Read-only, fail-open je Abschnitt, alle dynamischen Texte escaped.
"""
from __future__ import annotations

import html
from datetime import date, timedelta
from typing import Optional

from dashboard.compare import week_stats
from dashboard.theme import PALETTE


def _decisions_table_html() -> str:
    try:
        from analyzers.decision_log import DecisionLog
        entries = DecisionLog().get_recent(limit=10)
    except Exception:
        entries = []
    if not entries:
        return "<p>Keine Entscheidungen protokolliert.</p>"
    rows = "".join(
        f"<tr><td>{html.escape(str(e.get('decided_at', ''))[:16])}</td>"
        f"<td>{html.escape(str(e.get('ticker') or '?'))}</td>"
        f"<td>{html.escape(str(e.get('action') or '?'))}</td>"
        f"<td>{html.escape(str(e.get('reason') or ''))}</td></tr>"
        for e in entries
    )
    return (
        "<table><thead><tr><th>Zeit</th><th>Ticker</th><th>Aktion</th>"
        f"<th>Grund</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _scene_svg() -> str:
    try:
        from dashboard.factory import render_scene
        return render_scene()
    except Exception:
        return "<p>Fabrik-Szene nicht verfügbar.</p>"


def build_weekly_html(end_day: Optional[str] = None) -> str:
    """Baut den Wochen-Report als eigenständiges HTML-Dokument — direkt
    herunterladbar/archivierbar auch ohne laufendes Dashboard, keine
    externen Ressourcen im Dokument. `end_day` (YYYY-MM-DD) = Ende des
    betrachteten 7-Tage-Fensters, Default = heute. Fail-open je Abschnitt:
    ein kaputter Teil zeigt nur einen Hinweis statt das Ganze zu killen."""
    end = date.fromisoformat(end_day) if end_day else date.today()
    start = end - timedelta(days=6)
    try:
        stats = week_stats(start.isoformat(), end.isoformat())
    except Exception:
        stats = {"total": 0, "buy": 0, "skip": 0, "hold": 0,
                 "n_analyses": 0, "avg_sentiment": 0.0}

    p = PALETTE
    title = f"Wochen-Report {start.isoformat()} – {end.isoformat()}"
    esc_title = html.escape(title)

    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>{esc_title}</title>
<style>
body {{ background:{p['bg']}; color:{p['text']}; font-family: monospace; padding: 24px; }}
h1, h2 {{ color:{p['cobalt_hi']}; }}
.kpi {{ display:inline-block; background:{p['bg_panel']}; border:1px solid {p['border']};
        border-radius:4px; padding:10px 16px; margin:6px; }}
table {{ border-collapse: collapse; width:100%; margin-top:8px; }}
th, td {{ border:1px solid {p['border']}; padding:6px 10px; text-align:left; }}
th {{ background:{p['bg_panel']}; }}
</style>
</head>
<body>
<h1>{esc_title}</h1>
<h2>Kennzahlen</h2>
<div class="kpi">Entscheidungen: {int(stats.get('total', 0))}</div>
<div class="kpi">Käufe: {int(stats.get('buy', 0))}</div>
<div class="kpi">Übersprungen: {int(stats.get('skip', 0))}</div>
<div class="kpi">Gehalten: {int(stats.get('hold', 0))}</div>
<div class="kpi">Analysen: {int(stats.get('n_analyses', 0))}</div>
<div class="kpi">Ø Sentiment: {stats.get('avg_sentiment', 0.0)}</div>
<h2>Fabrik-Szene (Momentaufnahme)</h2>
{_scene_svg()}
<h2>Letzte Entscheidungen</h2>
{_decisions_table_html()}
</body>
</html>"""
