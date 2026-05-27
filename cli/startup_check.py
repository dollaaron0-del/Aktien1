"""
cli/startup_check.py – Vollständiger Vorab-Check vor dem ersten Server-Start.

Prüft: Python-Version, .env-Vollständigkeit, API-Keys, Broker-Verbindung,
       Datenbank-Schreibrechte, Netzwerk, Paket-Imports, Festplattenplatz.

Verwendung:
    python main.py --check
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

_OK   = "[bold green]✓[/bold green]"
_WARN = "[bold yellow]⚠[/bold yellow]"
_FAIL = "[bold red]✗[/bold red]"

CheckResult = Tuple[str, str, str]   # (status, name, detail)


def _ok(name: str, detail: str = "") -> CheckResult:
    return (_OK, name, detail)


def _warn(name: str, detail: str) -> CheckResult:
    return (_WARN, name, detail)


def _fail(name: str, detail: str) -> CheckResult:
    return (_FAIL, name, detail)


# ── Einzelne Check-Gruppen ────────────────────────────────────────────────────

def check_python() -> List[CheckResult]:
    results = []
    major, minor = sys.version_info.major, sys.version_info.minor
    ver = f"{major}.{minor}.{sys.version_info.micro}"
    if (major, minor) >= (3, 10):
        results.append(_ok("Python-Version", ver))
    else:
        results.append(_fail("Python-Version", f"{ver} – mindestens 3.10 erforderlich"))
    return results


def check_packages() -> List[CheckResult]:
    required = [
        ("anthropic",        "Claude API"),
        ("yfinance",         "Marktdaten"),
        ("praw",             "Reddit"),
        ("newsapi",          "NewsAPI"),
        ("pandas",           "Datenanalyse"),
        ("numpy",            "Numerik"),
        ("requests",         "HTTP"),
        ("rich",             "Terminal-UI"),
        ("schedule",         "Scheduler"),
        ("dotenv",           "python-dotenv"),
        ("dateutil",         "python-dateutil"),
        ("streamlit",        "Dashboard (optional)"),
        ("reportlab",        "PDF-Export (optional)"),
        ("flask",            "TradingView-Webhook (optional)"),
        ("prometheus_client","Prometheus (optional)"),
        ("ib_insync",        "IBKR-Broker (optional)"),
    ]
    optional = {"streamlit", "reportlab", "flask", "prometheus_client", "ib_insync"}
    results = []
    for pkg, label in required:
        try:
            importlib.import_module(pkg)
            results.append(_ok(f"  {label}", f"[dim]{pkg}[/dim]"))
        except ImportError:
            if pkg in optional:
                results.append(_warn(f"  {label}", f"{pkg} fehlt – Feature deaktiviert"))
            else:
                results.append(_fail(f"  {label}", f"pip install {pkg}"))
    return results


def check_env() -> List[CheckResult]:
    from config import config
    results = []

    def _check_key(label: str, value: str, required: bool = True) -> CheckResult:
        if value:
            masked = value[:4] + "…" + value[-4:] if len(value) > 12 else "***"
            return _ok(f"  {label}", masked)
        elif required:
            return _fail(f"  {label}", "fehlt in .env")
        else:
            return _warn(f"  {label}", "nicht gesetzt – Feature deaktiviert")

    results.append(_check_key("ANTHROPIC_API_KEY",   config.anthropic_api_key,   required=True))
    results.append(_check_key("TELEGRAM_BOT_TOKEN",  config.telegram_bot_token,  required=True))
    results.append(_check_key("TELEGRAM_CHAT_ID",    config.telegram_chat_id,    required=True))
    results.append(_check_key("NEWSAPI_KEY",         config.newsapi_key,         required=False))
    results.append(_check_key("REDDIT_CLIENT_ID",    config.reddit_client_id,    required=False))
    results.append(_check_key("REDDIT_CLIENT_SECRET",config.reddit_client_secret,required=False))
    # Broker-Mode
    if config.broker_mode == "ibkr":
        results.append(_ok("  Broker-Mode", f"ibkr @ {config.ibkr_host}:{config.ibkr_port}"))
    else:
        results.append(_ok("  Broker-Mode", "paper (kein echtes Kapital)"))

    # Watchlist
    wl_len = len(config.watchlist)
    if wl_len > 0:
        results.append(_ok("  Watchlist", f"{wl_len} Ticker konfiguriert"))
    else:
        results.append(_warn("  Watchlist", "leer – auto-scan wird verwendet"))

    # Kapital
    if config.initial_capital >= 100:
        results.append(_ok("  Startkapital", f"${config.initial_capital:,.2f}"))
    else:
        results.append(_fail("  Startkapital", f"${config.initial_capital} – zu gering"))

    return results


def check_anthropic() -> List[CheckResult]:
    results = []
    try:
        from config import config
        if not config.anthropic_api_key:
            return [_fail("Anthropic API", "Kein API-Key – Test übersprungen")]
        import anthropic
        client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        resp = client.messages.create(
            model=config.claude_model,
            max_tokens=10,
            messages=[{"role": "user", "content": "Reply: OK"}],
        )
        reply = resp.content[0].text.strip() if resp.content else "?"
        results.append(_ok("  Claude API", f"Antwort: '{reply}' (Modell: {config.claude_model})"))
    except Exception as e:
        results.append(_fail("  Claude API", str(e)[:80]))
    return results


def check_network() -> List[CheckResult]:
    results = []
    checks = [
        ("finance.yahoo.com", "yfinance / Yahoo Finance"),
        ("api.telegram.org",  "Telegram API"),
        ("www.reddit.com",    "Reddit"),
    ]
    for host, label in checks:
        try:
            import socket
            socket.setdefaulttimeout(5)
            socket.gethostbyname(host)
            results.append(_ok(f"  {label}", host))
        except Exception:
            results.append(_fail(f"  {label}", f"Kein Zugriff auf {host}"))
    return results


def check_databases() -> List[CheckResult]:
    results = []
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    test_db = data_dir / "_startup_test.db"
    try:
        con = sqlite3.connect(str(test_db))
        con.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
        con.execute("INSERT INTO t VALUES (1)")
        con.commit()
        con.close()
        test_db.unlink()
        results.append(_ok("  Schreibrechte data/", str(data_dir.resolve())))
    except Exception as e:
        results.append(_fail("  Schreibrechte data/", str(e)))

    existing_dbs = [
        "portfolio.db", "trade_journal.db", "performance.db",
        "signal_queue.db", "tax.db", "dividends.db",
    ]
    for db in existing_dbs:
        p = data_dir / db
        if p.exists():
            size_kb = p.stat().st_size // 1024
            results.append(_ok(f"  {db}", f"{size_kb} KB"))
        else:
            results.append(_warn(f"  {db}", "noch nicht vorhanden – wird beim ersten Start angelegt"))
    return results


def check_disk_space() -> List[CheckResult]:
    results = []
    try:
        import shutil
        total, used, free = shutil.disk_usage(".")
        free_gb = free / (1024 ** 3)
        total_gb = total / (1024 ** 3)
        if free_gb >= 2.0:
            results.append(_ok("  Festplatte", f"{free_gb:.1f} GB frei von {total_gb:.1f} GB"))
        elif free_gb >= 0.5:
            results.append(_warn("  Festplatte", f"Nur {free_gb:.1f} GB frei – Log-Rotation prüfen"))
        else:
            results.append(_fail("  Festplatte", f"Kritisch: nur {free_gb:.2f} GB frei"))
    except Exception as e:
        results.append(_warn("  Festplatte", str(e)))
    return results


def check_logs_dir() -> List[CheckResult]:
    results = []
    logs_dir = Path("logs")
    try:
        logs_dir.mkdir(exist_ok=True)
        test_file = logs_dir / "_test.tmp"
        test_file.write_text("ok")
        test_file.unlink()
        results.append(_ok("  logs/-Verzeichnis", str(logs_dir.resolve())))
    except Exception as e:
        results.append(_fail("  logs/-Verzeichnis", str(e)))
    return results


def check_scripts() -> List[CheckResult]:
    results = []
    for script in ["scripts/watchdog.sh", "scripts/backup.sh", "scripts/aktien_bot.service"]:
        p = Path(script)
        if p.exists():
            executable = os.access(p, os.X_OK)
            if executable or script.endswith(".service"):
                results.append(_ok(f"  {script}", "vorhanden"))
            else:
                results.append(_warn(f"  {script}", "nicht ausführbar – chmod +x scripts/*.sh"))
        else:
            results.append(_warn(f"  {script}", "fehlt"))
    return results


# ── Haupt-Funktion ────────────────────────────────────────────────────────────

def run_startup_check() -> None:
    console.rule("[bold blue]Aktien-Bot – Startup-Check")
    console.print(f"[dim]{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}  ·  Python {sys.version.split()[0]}[/dim]\n")

    sections = [
        ("Python-Version",      check_python),
        ("Pakete (pip)",        check_packages),
        ("Konfiguration (.env)",check_env),
        ("Netzwerk",            check_network),
        ("Claude API",          check_anthropic),
        ("Datenbanken",         check_databases),
        ("Festplatte",          check_disk_space),
        ("Log-Verzeichnis",     check_logs_dir),
        ("Deploy-Skripte",      check_scripts),
    ]

    all_results: List[CheckResult] = []
    for section_name, fn in sections:
        console.print(f"[bold]{section_name}[/bold]")
        try:
            rows = fn()
        except Exception as e:
            rows = [_fail(f"  {section_name}", f"Unerwarteter Fehler: {e}")]
        for r in rows:
            status, name, detail = r
            console.print(f"  {status} {name}  [dim]{detail}[/dim]")
            all_results.append(r)
        console.print()

    # Zusammenfassung
    fails  = [r for r in all_results if r[0] == _FAIL]
    warns  = [r for r in all_results if r[0] == _WARN]
    oks    = [r for r in all_results if r[0] == _OK]

    console.rule()
    console.print(f"  {_OK} OK: {len(oks)}   {_WARN} Warnungen: {len(warns)}   {_FAIL} Fehler: {len(fails)}")
    console.print()

    if fails:
        console.print("[bold red]✗ Nicht startbereit – folgende Probleme beheben:[/bold red]")
        for _, name, detail in fails:
            console.print(f"  → {name.strip()}: {detail}")
        console.print()
        sys.exit(1)
    elif warns:
        console.print("[bold yellow]⚠ Startbereit mit Einschränkungen[/bold yellow]")
        console.print("[dim]Warnungen betreffen optionale Features. Bot kann gestartet werden.[/dim]")
        console.print()
        console.print("[dim]Nächster Schritt:[/dim]  [bold]python main.py[/bold]  (einmaliger Test)")
        console.print("[dim]Dauerbetrieb:  [/dim]  [bold]bash scripts/watchdog.sh[/bold]  oder  [bold]systemctl start aktien_bot[/bold]")
    else:
        console.print("[bold green]✓ Alles bereit – Bot kann gestartet werden![/bold green]")
        console.print()
        console.print("[dim]Nächster Schritt:[/dim]  [bold]python main.py[/bold]  (einmaliger Test)")
        console.print("[dim]Dauerbetrieb:  [/dim]  [bold]bash scripts/watchdog.sh[/bold]  oder  [bold]systemctl start aktien_bot[/bold]")
    console.print()
