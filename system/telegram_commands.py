"""
system/telegram_commands.py – eingehender Telegram-/status-Befehl (Roadmap 1.5g).

notifier/telegram_notifier.py sendet bisher nur ausgehend. Für einen Befehl
muss der Bot eingehende Nachrichten abholen – kein Webhook (kein öffentlicher
HTTPS-Endpunkt vorhanden), sondern getUpdates-Short-Polling aus der laufenden
Scheduler-Hauptschleife heraus (dieselbe Stelle wie der Dead-Man-Switch-Ping,
Roadmap 1.7 – die Schleife tickt ohnehin ~1×/Minute).

Nur Nachrichten aus dem konfigurierten TELEGRAM_CHAT_ID werden akzeptiert
(kein offener Befehlskanal für Dritte). Der zuletzt verarbeitete update_id
wird in data/telegram_offset.json gemerkt, damit ein Neustart keine alten
Befehle erneut abarbeitet (Telegram liefert sonst dieselben Updates wieder).

Fail-open: jeder Fehler (Netzwerk, Parsing, fehlender Token) wird verschluckt
– ein kaputter Befehlskanal darf den Handelszyklus nie beeinträchtigen.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from logger import get_logger
from system.http import http_get

log = get_logger(__name__)

_OFFSET_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "telegram_offset.json")
_API_BASE = "https://api.telegram.org/bot{token}/getUpdates"


def _load_offset(offset_file: str = _OFFSET_FILE) -> int:
    try:
        with open(offset_file) as f:
            return int(json.load(f).get("last_update_id", 0))
    except Exception:
        return 0


def _save_offset(update_id: int, offset_file: str = _OFFSET_FILE) -> None:
    try:
        os.makedirs(os.path.dirname(offset_file), exist_ok=True)
        with open(offset_file, "w") as f:
            json.dump({"last_update_id": update_id}, f)
    except Exception:
        pass


def build_status_text() -> str:
    """Klartext-Status für Telegram – dieselben Signale wie die Dashboard-
    Gesundheits-Ampelleiste (Roadmap 1.5d), aber unabhängig vom Dashboard
    aufrufbar. Jeder Baustein ist einzeln fail-open, damit ein kaputter Check
    (z.B. Broker nicht erreichbar) die übrigen Zeilen nicht verschluckt."""
    from config import config

    lines = ["📡 <b>Bot-Status</b>"]

    try:
        from system import bot_control
        lines.append("⏸ Pausiert (Dashboard)" if bot_control.is_paused() else "▶️ Aktiv")
    except Exception:
        pass

    try:
        from system import live_status
        ls = live_status.read_status()
        if ls:
            if ls.get("state") == "idle" and ls.get("next_run"):
                lines.append(f"💤 Idle · nächster Lauf: {ls['next_run'][:16].replace('T', ' ')} Uhr")
            elif ls.get("phase"):
                lines.append(f"🔄 Phase: {ls['phase']}")
    except Exception:
        pass

    try:
        import socket
        with socket.create_connection((config.ibkr_host, config.ibkr_port), timeout=0.4):
            lines.append("🟢 IB-Gateway erreichbar")
    except Exception:
        lines.append("🔴 IB-Gateway nicht erreichbar")

    try:
        from analyzers.api_cost_tracker import APICostTracker
        s = APICostTracker().summary()
        today = float(s.get("today_cost_eur") or 0.0)
        limit = float(s.get("daily_limit_eur") or 0.0)
        pct = (today / limit * 100) if limit > 0 else 0.0
        dot = "🔴" if pct >= 100 else "🟡" if pct >= 80 else "🟢"
        lines.append(f"{dot} Claude-Kosten: {today:.2f}€/{limit:.2f}€")
    except Exception:
        pass

    try:
        from portfolio.portfolio import Portfolio
        from portfolio.circuit_breaker import CircuitBreaker
        from broker.paper_broker import PaperBroker

        port = Portfolio()
        positions = port.all_positions()
        prices = PaperBroker().get_prices(list(positions.keys())) if positions else {}
        total_value = port.total_value(prices)
        cb = CircuitBreaker().status(total_value)
        lines.append("🔴 Circuit-Breaker AUSGELÖST" if cb.get("triggered") else "🟢 Circuit-Breaker")
        lines.append(
            f"💼 Wert: ${total_value:,.2f} · Cash: ${port.cash:,.2f} · "
            f"Positionen: {len(positions)}"
        )
    except Exception:
        pass

    return "\n".join(lines)


def poll(offset_file: str = _OFFSET_FILE) -> None:
    """Holt neue Telegram-Nachrichten (getUpdates, kein Long-Poll-Warten –
    die Scheduler-Schleife tickt ohnehin ~1×/Minute) und beantwortet /status.
    Fail-open, No-Op ohne konfigurierten Token/Chat."""
    from config import config

    token = getattr(config, "telegram_bot_token", "") or ""
    chat_id = str(getattr(config, "telegram_chat_id", "") or "")
    if not token or not chat_id:
        return

    offset = _load_offset(offset_file)
    try:
        resp = http_get(
            _API_BASE.format(token=token),
            params={"offset": offset + 1, "timeout": 0},
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        log.debug("Telegram getUpdates fehlgeschlagen (ignoriert): %s", e)
        return

    if not data.get("ok"):
        return

    for update in data.get("result", []):
        update_id = update.get("update_id")
        if update_id is not None:
            _save_offset(update_id, offset_file)
        msg = update.get("message") or {}
        text = (msg.get("text") or "").strip()
        from_chat = str((msg.get("chat") or {}).get("id") or "")
        if from_chat != chat_id:
            continue  # Nur der konfigurierte Chat darf Befehle geben
        if text.split("@")[0].lower() == "/status":
            try:
                from notifier.telegram_notifier import TelegramNotifier
                TelegramNotifier().send(build_status_text(), level="command")
            except Exception:
                pass
