"""
Gemeinsame Broker-Auswahl für alle NICHT-handelnden Konsumenten (Dashboard,
Telegram-Status, Dry-Run-Vergleich).

Vorher hingen dashboard/app.py, system/telegram_commands.py und
dashboard/dry_run.py hart an PaperBroker() – unabhängig von config.broker_mode.
Lief der Bot live über IBKR (BROKER_MODE=ibkr), zeigten Dashboard und Telegram
dadurch eine ANDERE Preisquelle (yfinance-Cache über PaperBroker) als die, auf
der der Bot tatsächlich Kauf-/Verkaufsentscheidungen traf (IBKR-Live-/Delayed-
Daten). Konkret sichtbar geworden am 25.7.2026 (SAP-Vorfall): Dashboard/
Telegram hätten eine gesunde Position gezeigt, während der Bot intern längst
einen (fehlerhaften) Stop-Loss-Bruch registrierte und zu verkaufen versuchte.

get_readonly_broker() liefert denselben Broker-TYP wie main.py (config.
broker_mode entscheidet), aber im IBKR-Fall über eine EIGENE Client-ID und
readonly=True: eine zweite Verbindung zum selben Gateway, die die laufende
Handels-Session des Bots (eigene Client-ID) nicht stört und selbst bei einem
Programmierfehler nie eine Order platzieren kann (IBKR lehnt das auf einer
readonly-Session serverseitig ab).
"""
import os
import threading
from typing import Optional

from config import config
from logger import get_logger

log = get_logger(__name__)

_READONLY_CLIENT_ID = int(os.getenv("IBKR_READONLY_CLIENT_ID", "9"))

_lock = threading.Lock()
_instance: Optional[object] = None


def get_readonly_broker():
    """Prozessweiter Singleton – ein Dashboard-/Telegram-Prozess braucht nur
    eine einzige Lese-Verbindung, nicht eine pro Aufruf (IBKR-Connects kosten
    ~1-2s). Fällt bei nicht erreichbarem Gateway automatisch auf den
    yfinance-Preis-Fallback von IBKRBroker zurück (siehe get_price/get_prices)
    statt zu crashen."""
    global _instance
    if _instance is not None:
        return _instance
    with _lock:
        if _instance is not None:
            return _instance
        if config.broker_mode == "ibkr":
            from broker.ibkr_broker import IBKRBroker
            log.info("broker.factory: IBKR read-only (clientId=%d)", _READONLY_CLIENT_ID)
            _instance = IBKRBroker(client_id=_READONLY_CLIENT_ID, readonly=True)
        else:
            from broker.paper_broker import PaperBroker
            _instance = PaperBroker()
        return _instance
