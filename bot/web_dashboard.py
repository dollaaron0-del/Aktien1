"""
bot/web_dashboard.py – Lightweight web dashboard served via stdlib http.server.
Accessible at http://161.97.166.88:8080
"""

import json
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from logger import get_logger

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _read_json(filename: str) -> dict | list | None:
    path = os.path.join(_DATA_DIR, filename)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _collect_data() -> dict:
    api_raw = _read_json("api_savings.json") or {}
    macro_raw = _read_json("macro_calendar.json") or {}
    cache_raw = _read_json("analysis_cache.json") or {}
    bot_start_raw = _read_json("bot_start.json") or {}

    # --- API Kosten ---
    today_str = datetime.now().date().isoformat()
    daily = api_raw.get("daily", {})
    today_daily = daily.get(today_str, {})

    api = {
        "claude_calls_today": today_daily.get("claude_calls", 0),
        "ollama_skips_today": today_daily.get("ollama_skips", 0),
        "cost_today": today_daily.get("cost", today_daily.get("cost_usd", 0.0)),
        "saved_today": today_daily.get("saved", today_daily.get("saved_usd", 0.0)),
        "total_analyses": api_raw.get("total_analyses", 0),
        "total_claude_calls": api_raw.get("claude_calls", 0),
        "total_cost": api_raw.get("total_cost_eur", api_raw.get("total_cost_usd", 0.0)),
        "total_saved": api_raw.get("total_saved_eur", api_raw.get("total_saved_usd", 0.0)),
        "currency": "EUR",
    }

    # --- Makro-Termine (next 14 days) ---
    now = datetime.now(timezone.utc)
    events_raw = macro_raw.get("events", [])
    macro_events = []
    for ev in events_raw:
        try:
            ev_date = datetime.fromisoformat(ev.get("date", "").replace("Z", "+00:00"))
            delta = (ev_date.date() - now.date()).days
            if 0 <= delta <= 14:
                macro_events.append({
                    "name": ev.get("name", ""),
                    "date": ev_date.strftime("%d.%m. %H:%M"),
                    "impact": ev.get("impact", "LOW"),
                    "delta_days": delta,
                })
        except (ValueError, TypeError):
            continue
    macro_events.sort(key=lambda e: e["delta_days"])

    # --- Letzte Analysen aus analysis_cache.json ---
    analyses = []
    if isinstance(cache_raw, dict):
        for ticker, data in cache_raw.items():
            if isinstance(data, dict):
                analyses.append({
                    "ticker": ticker,
                    "recommendation": data.get("recommendation", ""),
                    "score": data.get("score"),
                    "confidence": data.get("confidence"),
                    "direction": data.get("direction", ""),
                    "updated_at": data.get("updated_at", ""),
                })
    analyses.sort(key=lambda a: a["updated_at"] or "", reverse=True)
    analyses = analyses[:10]

    # --- System Info ---
    bot_started = bot_start_raw.get("started_at", "")

    return {
        "server_time": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "api": api,
        "macro_events": macro_events,
        "analyses": analyses,
        "bot_started": bot_started,
    }


def _render_html(data: dict) -> str:
    api = data["api"]
    macro = data["macro_events"]
    analyses = data["analyses"]

    def _rec_color(rec: str) -> str:
        r = rec.upper()
        if r == "BUY":
            return "#4caf50"
        if r == "SELL":
            return "#f44336"
        if r == "HOLD":
            return "#ff9800"
        return "#9e9e9e"

    def _impact_color(impact: str) -> str:
        i = impact.upper()
        if i == "HIGH":
            return "#f44336"
        if i == "MEDIUM":
            return "#ff9800"
        return "#9e9e9e"

    macro_rows = ""
    if macro:
        for ev in macro:
            ic = _impact_color(ev["impact"])
            macro_rows += (
                f'<tr><td>{ev["date"]}</td>'
                f'<td>{ev["name"]}</td>'
                f'<td style="color:{ic};font-weight:bold">{ev["impact"]}</td></tr>\n'
            )
    else:
        macro_rows = '<tr><td colspan="3" style="color:#666">Keine Termine in den nächsten 14 Tagen</td></tr>'

    analysis_rows = ""
    if analyses:
        for a in analyses:
            rc = _rec_color(a["recommendation"])
            score_str = f'{a["score"]:.2f}' if a["score"] is not None else "–"
            conf_str = f'{a["confidence"]:.0%}' if a["confidence"] is not None else "–"
            ts = (a["updated_at"] or "")[:16].replace("T", " ")
            analysis_rows += (
                f'<tr>'
                f'<td style="font-weight:bold">{a["ticker"]}</td>'
                f'<td style="color:{rc};font-weight:bold">{a["recommendation"]}</td>'
                f'<td>{a["direction"]}</td>'
                f'<td>{score_str}</td>'
                f'<td>{conf_str}</td>'
                f'<td style="color:#888;font-size:0.85em">{ts}</td>'
                f'</tr>\n'
            )
    else:
        analysis_rows = '<tr><td colspan="6" style="color:#666">Keine Analysen vorhanden</td></tr>'

    bot_started_str = data["bot_started"] or "–"

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>Aktien Bot Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d0d0d;color:#e0e0e0;font-family:'Courier New',monospace;font-size:14px;padding:12px}}
h1{{font-size:1.3em;color:#4caf50;letter-spacing:2px;margin-bottom:4px}}
.subtitle{{color:#666;font-size:0.8em;margin-bottom:16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}}
.card{{background:#1a1a1a;border:1px solid #333;border-radius:6px;padding:14px}}
.card h2{{font-size:0.95em;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;border-bottom:1px solid #333;padding-bottom:6px}}
.stat-row{{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #222}}
.stat-row:last-child{{border-bottom:none}}
.stat-label{{color:#999}}
.stat-val{{color:#e0e0e0;font-weight:bold}}
table{{width:100%;border-collapse:collapse;margin-top:4px}}
th{{color:#666;font-size:0.8em;text-align:left;padding:4px 6px;border-bottom:1px solid #333}}
td{{padding:5px 6px;border-bottom:1px solid #1f1f1f;font-size:0.88em}}
tr:last-child td{{border-bottom:none}}
.full-width{{grid-column:1/-1}}
.green{{color:#4caf50}}.red{{color:#f44336}}.yellow{{color:#ff9800}}.gray{{color:#9e9e9e}}
</style>
</head>
<body>
<h1>AKTIEN BOT DASHBOARD</h1>
<p class="subtitle">Letztes Update: {data["server_time"]} &nbsp;|&nbsp; Auto-Refresh: 60s</p>
<div class="grid">

<div class="card">
<h2>API-Kosten</h2>
<div class="stat-row"><span class="stat-label">Heute Claude-Calls</span><span class="stat-val">{api["claude_calls_today"]}</span></div>
<div class="stat-row"><span class="stat-label">Heute Ollama-Skips</span><span class="stat-val">{api["ollama_skips_today"]}</span></div>
<div class="stat-row"><span class="stat-label">Heute Kosten</span><span class="stat-val">${api["cost_today"]:.4f}</span></div>
<div class="stat-row"><span class="stat-label">Heute Ersparnis</span><span class="stat-val green">${api["saved_today"]:.4f}</span></div>
<div class="stat-row"><span class="stat-label">Gesamt Analysen</span><span class="stat-val">{api["total_analyses"]}</span></div>
<div class="stat-row"><span class="stat-label">Gesamt Claude-Calls</span><span class="stat-val">{api["total_claude_calls"]}</span></div>
<div class="stat-row"><span class="stat-label">Gesamtkosten</span><span class="stat-val">${api["total_cost"]:.4f}</span></div>
<div class="stat-row"><span class="stat-label">Gesamt Ersparnis</span><span class="stat-val green">${api["total_saved"]:.4f}</span></div>
</div>

<div class="card">
<h2>System Info</h2>
<div class="stat-row"><span class="stat-label">Bot gestartet</span><span class="stat-val">{bot_started_str}</span></div>
<div class="stat-row"><span class="stat-label">Serverzeit</span><span class="stat-val">{data["server_time"]}</span></div>
</div>

<div class="card full-width">
<h2>Makro-Termine (naechste 14 Tage)</h2>
<table>
<tr><th>Datum</th><th>Ereignis</th><th>Relevanz</th></tr>
{macro_rows}
</table>
</div>

<div class="card full-width">
<h2>Letzte Analysen</h2>
<table>
<tr><th>Ticker</th><th>Empfehlung</th><th>Richtung</th><th>Score</th><th>Konfidenz</th><th>Aktualisiert</th></tr>
{analysis_rows}
</table>
</div>

</div>
</body>
</html>"""


class _DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress default access log
        pass

    def do_GET(self):
        if self.path == "/api/data":
            self._serve_json()
        elif self.path in ("/", "/index.html"):
            self._serve_html()
        else:
            self.send_error(404)

    def _serve_html(self):
        try:
            data = _collect_data()
            body = _render_html(data).encode("utf-8")
        except Exception as exc:
            get_logger(__name__).warning("Dashboard render error: %s", exc)
            body = b"<pre>Dashboard error: " + str(exc).encode() + b"</pre>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self):
        try:
            data = _collect_data()
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ReuseHTTPServer(HTTPServer):
    allow_reuse_address = True  # avoids "Address already in use" on restart


def start_dashboard(port: int = 8080) -> None:
    """Startet den Dashboard-Server als Hintergrund-Thread.
    Bindet den Port synchron, um Konflikte (z.B. nginx/Streamlit auf demselben
    Port) sofort zu erkennen – dann wird der interne Server sauber übersprungen
    statt eine irreführende Erfolgsmeldung zu loggen."""
    log = get_logger(__name__)
    try:
        server = _ReuseHTTPServer(("0.0.0.0", port), _DashboardHandler)
    except OSError as e:
        log.info(
            "Web-Dashboard übersprungen – Port %d belegt (%s). "
            "Vermutlich nginx/Streamlit; interner Server nicht erforderlich.",
            port, e,
        )
        return
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Web-Dashboard gestartet auf Port %d", port)
