#!/usr/bin/env python3
"""
scripts/premarket_ibkr_check.py – Pre-Market IBKR-Connectivity-Check.

Läuft werktags ~07:15, also VOR dem 07:30-Analyse-Zyklus. Verifiziert mit einer
EIGENEN clientId (stört die laufende Bot-Verbindung nicht), ob IBKR wirklich
verbunden UND eingeloggt ist – per echter managedAccounts-Abfrage, nicht nur ob
Port 4002 lauscht (fängt damit auch „Zombie"-Gateways).

Bei Problem: Recovery-Versuch über /opt/ibgateway/autologin.sh, dann erneute
Prüfung und lauter Telegram-Alert mit dem Ergebnis. Im Erfolgsfall still
(nur Konsole/journal), um Telegram-Rauschen zu vermeiden.

Aufruf:
  venv/bin/python scripts/premarket_ibkr_check.py            # mit Recovery+Alert
  venv/bin/python scripts/premarket_ibkr_check.py --dry-run  # nur prüfen+Konsole
  venv/bin/python scripts/premarket_ibkr_check.py --notify-ok # auch bei OK pingen
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

AUTOLOGIN = "/opt/ibgateway/autologin.sh"
# Eigene clientId, damit der laufende Bot (clientId=1) nicht getrennt wird.
PROBE_CLIENT_ID = int(os.getenv("IBKR_PREMARKET_CLIENT_ID", "97"))
PROBE_TIMEOUT = int(os.getenv("IBKR_PREMARKET_TIMEOUT", "20"))


def _probe() -> tuple[bool, str]:
    """Echte Verbindungs-/Login-Prüfung mit eigener clientId.
    Returns (ok, detail). ok=True nur wenn verbunden UND ein Konto gemeldet wird."""
    from config import config
    host, port = config.ibkr_host, config.ibkr_port
    try:
        from ib_insync import IB
    except Exception as e:
        return False, f"ib_insync nicht verfügbar: {e}"
    ib = IB()
    try:
        ib.connect(host, port, clientId=PROBE_CLIENT_ID, timeout=PROBE_TIMEOUT)
    except Exception as e:
        return False, f"connect {host}:{port} fehlgeschlagen: {e}"
    try:
        accounts = list(ib.managedAccounts() or [])
        connected = ib.isConnected() and bool(accounts)
        if not connected:
            return False, f"verbunden={ib.isConnected()}, aber keine Konten gemeldet"
        acct = accounts[0]
        is_paper = acct.upper().startswith("DU")
        return True, f"{acct} ({'PAPER' if is_paper else 'LIVE ⚠️'})"
    except Exception as e:
        return False, f"Konten-Abfrage fehlgeschlagen: {e}"
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def _attempt_recovery() -> str:
    if not os.path.exists(AUTOLOGIN):
        return f"Recovery übersprungen – {AUTOLOGIN} fehlt"
    try:
        subprocess.run([AUTOLOGIN], timeout=180, capture_output=True)
    except Exception as e:
        return f"autologin.sh Fehler: {e}"
    # Gateway-Login braucht etwas – kurz warten, dann erneut prüfen.
    time.sleep(60)
    return "autologin.sh ausgeführt"


def _send(msg: str) -> None:
    try:
        from notifier.telegram_notifier import TelegramNotifier
        TelegramNotifier().send(msg)
    except Exception as e:
        print(f"[Telegram fehlgeschlagen: {e}]")


def main() -> int:
    dry = "--dry-run" in sys.argv
    notify_ok = "--notify-ok" in sys.argv

    from config import config
    if config.broker_mode != "ibkr":
        print(f"BROKER_MODE={config.broker_mode} – kein IBKR-Check nötig.")
        return 0

    ok, detail = _probe()
    print(f"Pre-Market IBKR-Check: {'OK' if ok else 'PROBLEM'} – {detail}")

    if ok:
        if notify_ok and not dry:
            _send(f"✅ <b>IBKR bereit vor Börse</b>\nKonto: {detail}")
        return 0

    # Problem → Recovery-Versuch + erneute Prüfung
    print("IBKR nicht bereit – starte Recovery …")
    recovery = _attempt_recovery() if not dry else "Recovery übersprungen (--dry-run)"
    ok2, detail2 = _probe()
    print(f"Nach Recovery: {'OK' if ok2 else 'PROBLEM'} – {detail2}")

    if not dry:
        if ok2:
            _send(
                "⚠️ <b>IBKR war vor Börse nicht bereit – automatisch behoben</b>\n\n"
                f"Erst: {detail}\n{recovery}\nJetzt: ✅ {detail2}"
            )
        else:
            _send(
                "🚨 <b>IBKR vor Börse NICHT erreichbar – Recovery fehlgeschlagen!</b>\n\n"
                f"Erst: {detail}\n{recovery}\nNoch immer: ❌ {detail2}\n\n"
                "Der Bot handelt NICHT (kein Paper-Fallback). IB Gateway manuell prüfen!"
            )
    return 0 if ok2 else 1


if __name__ == "__main__":
    sys.exit(main())
