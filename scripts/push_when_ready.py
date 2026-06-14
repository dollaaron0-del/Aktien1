#!/usr/bin/env python3
"""
scripts/push_when_ready.py – Versucht den Push des Bot-Branches und meldet
Erfolg per Telegram. Gedacht für einen wiederkehrenden systemd-Timer, der so
lange retried, bis der Git-Token Schreibrechte hat. Bei Erfolg stoppt sich der
Timer selbst.

Erfolgs-Erkennung: git-push rc==0 ODER "up-to-date" (nichts zu pushen).
Bei 403/Fehler: still beenden (Timer versucht es beim nächsten Intervall erneut).
"""
from __future__ import annotations

import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRANCH = "claude/bot-improvements-duWvU"
TIMER_UNIT = "aktien_push_retry.timer"


def _git_push() -> tuple[bool, str]:
    res = subprocess.run(
        ["git", "-C", PROJECT_ROOT, "push", "origin", BRANCH],
        capture_output=True, text=True,
    )
    out = (res.stdout or "") + (res.stderr or "")
    ok = res.returncode == 0 or "up-to-date" in out.lower()
    return ok, out.strip()


def _notify(msg: str) -> None:
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from notifier.telegram_notifier import TelegramNotifier
        TelegramNotifier().send(msg)
    except Exception as e:
        print(f"[Telegram fehlgeschlagen: {e}]")


def _stop_timer() -> None:
    """Stoppt den wiederkehrenden Timer nach erfolgreichem Push."""
    try:
        subprocess.run(["systemctl", "stop", TIMER_UNIT],
                       capture_output=True, text=True)
    except Exception as e:
        print(f"[Timer-Stop fehlgeschlagen: {e}]")


def main() -> int:
    ok, out = _git_push()
    print(out)
    if ok:
        # Anzahl gepushter Commits ist jetzt 0 vor origin – nur informativ melden.
        _notify(
            "✅ <b>Git-Push erfolgreich</b>\n"
            f"Branch <code>{BRANCH}</code> ist jetzt auf origin.\n"
            "Auto-Retry beendet."
        )
        _stop_timer()
        return 0
    # Stiller Fehlschlag (z.B. weiterhin 403) → Timer retried später erneut.
    print("[Push noch nicht möglich – Retry beim nächsten Intervall]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
