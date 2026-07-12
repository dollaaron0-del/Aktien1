"""
system/dead_man_switch.py – externer Heartbeat gegen Totalausfall (Roadmap 1.7).

watchdog.sh überwacht den Bot-Prozess, läuft aber auf demselben Server: stirbt
der Server selbst oder das Netz, meldet niemand mehr etwas. Dieses Modul pingt
stattdessen periodisch einen externen Dienst (z.B. healthchecks.io, kostenlos)
aus der laufenden Scheduler-Hauptschleife. Bleibt der Ping aus, alarmiert der
Dienst selbst (Mail/weitere Integrationen) – unabhängig vom eigenen Server.

Fail-open und komplett optional: ohne gesetzte `DEAD_MAN_SWITCH_URL` ist
`ping()` ein No-Op. Netzwerkfehler werden verschluckt (best effort, wie die
Telegram-Sends im Bot) – ein Ausfall des Heartbeat-Diensts darf den Bot nie
beeinträchtigen.
"""
from __future__ import annotations

import threading
import time

from logger import get_logger
from system.http import http_get

log = get_logger(__name__)

_lock = threading.Lock()
_last_ping_ts: float = 0.0


def ping() -> bool:
    """Pingt die konfigurierte Dead-Man-Switch-URL, gedrosselt auf
    config.dead_man_switch_interval_min. Gibt True zurück, wenn tatsächlich
    gesendet wurde (rein informativ, kein Fehlerindikator – fail-open).
    """
    from config import config

    url = getattr(config, "dead_man_switch_url", "") or ""
    if not url:
        return False

    interval_s = max(1, int(getattr(config, "dead_man_switch_interval_min", 5))) * 60
    now = time.monotonic()
    global _last_ping_ts
    with _lock:
        if now - _last_ping_ts < interval_s:
            return False
        _last_ping_ts = now

    try:
        http_get(url, timeout=10)
        log.debug("Dead-Man-Switch gepingt")
        return True
    except Exception as e:
        log.debug("Dead-Man-Switch-Ping fehlgeschlagen (ignoriert): %s", e)
        return False
