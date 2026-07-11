# Server-von-Null-Runbook

Kurzanleitung, um den Trading-Bot auf einem neuen/leeren Server komplett
wiederherzustellen. Entstanden aus der Restore-Probe (Roadmap 0.5, 11.7.2026):
`scripts/restore.sh` wurde dabei erstmals seit Einführung des Backups (0.1)
End-to-End getestet — auf einem frischen `git clone` in einem isolierten
Verzeichnis, mit einem echten, frisch erzeugten Backup-Archiv.

Befund der Probe: Restore-Mechanik (Daten) funktioniert; **ein Bug wurde
dabei gefunden und gefixt** — `restore.sh` installierte Pakete zuvor ins
System-Python statt in eine venv (schlug auf Debian/Ubuntu mit PEP 668,
"externally-managed-environment", fehl). Jetzt legt das Skript bei Bedarf
`venv/` an und installiert/testet konsequent darüber — passend zu den
systemd-Units, die fest auf `venv/bin/...` zeigen.

## 1. Paket-Voraussetzungen (Betriebssystem)

- Python 3.12 (o.ä. 3.11+), `python3-venv`
- `git`
- IB Gateway (für IBKR-Broker-Modus) — separate Installation unter
  `/opt/ibgateway/`, nicht Teil dieses Repos. `autologin.sh` hält Port 4002
  offen (siehe Schritt 5).

## 2. Code holen

```bash
git clone <repo-url> /opt/Aktien
cd /opt/Aktien
```

(Aktuell kein Push zu origin möglich, siehe Roadmap 0.2 — im Ernstfall Code
notfalls vom letzten lokalen Klon/Server kopieren statt `git clone`.)

## 3. Daten wiederherstellen

Neuestes Backup-Archiv besorgen (lokal unter `backups/`, oder vom
Off-Server-Ziel falls `BACKUP_REMOTE` gesetzt war — Stand 11.7. noch nicht
gesetzt, siehe Roadmap 0.1) und:

```bash
bash scripts/restore.sh <pfad-zum-archiv>.tar.gz
```

Das Skript legt bei Bedarf `venv/` an, installiert Pakete gepinnt aus
`requirements.lock` (Fallback `requirements.txt`), entpackt `.env` + `data/`,
und prüft danach Konfiguration + Datenbankzugriff automatisch. Vorhandene
Daten im Zielverzeichnis werden vorher automatisch nach
`data/pre_restore_backup_<timestamp>.tar.gz` gesichert.

**Vor Bot-Start prüfen**: Demo-Daten-Swap aktiv? (siehe Memory
`demo-data-swap-aktiv` / User fragen) — sonst liefe der Bot auf
Präsentations-Demodaten statt echten.

## 4. systemd-Units einrichten

Alle Units liegen versioniert unter `scripts/`:

```bash
cp scripts/aktien_bot.service scripts/aktien_dashboard.service \
   scripts/aktien_backup.service scripts/aktien_backup.timer \
   scripts/aktien_premarket_ibkr.service scripts/aktien_premarket_ibkr.timer \
   scripts/aktien_source_health.service scripts/aktien_source_health.timer \
   /etc/systemd/system/
systemctl daemon-reload
```

Danach **gezielt** aktivieren, nicht pauschal — der Bot ist ggf. bewusst
pausiert (siehe CLAUDE.md-Status). Beispiel für Vollbetrieb:

```bash
systemctl enable --now aktien_dashboard.service   # Dashboard, 127.0.0.1:8503
systemctl enable --now aktien_backup.timer        # tägliches Backup 03:00
systemctl enable --now aktien_bot.service \
  aktien_premarket_ibkr.timer aktien_source_health.timer
```

`aktien_monday_check.service/.timer` ist **kein** wiederkehrender Regelbetrieb
(war ein einmaliger Gegencheck auf ein festes Datum nach einem Bugfix,
15.6.2026) — nicht Teil dieses Repos, bei Bedarf neu mit aktuellem Datum
anlegen (Vorlage: `OnCalendar=<datum> <zeit>`, `Type=oneshot`).

## 5. IB Gateway

- IB Gateway separat installiert unter `/opt/ibgateway/`, nicht durch Backup/
  Restore abgedeckt (kein Bot-Datum, eigene Software-Installation).
- Crontab-Zeile hält Port 4002 am Leben (stündlich):
  ```
  0 * * * * ss -tlnp | grep -q 4002 || /opt/ibgateway/autologin.sh >> /var/log/ibgw-restart.log 2>&1
  ```
  Mit `crontab -e` neu eintragen. Zusätzliche Zeile für automatischen
  Bot-Neustart täglich 06:00 ist aktuell bewusst auskommentiert
  (Bot-Pause, siehe CLAUDE.md) — nur auf Anweisung aktivieren.
- Paper-Konto DUQ463153 (siehe Memory `ibkr-broker-setup`); IBKR-Zugangsdaten
  stecken in der wiederhergestellten `.env`.

## 6. Firewall

`ufw` sollte Port 8503 (Dashboard) **nicht** von außen freigeben — Zugriff
nur per SSH-Tunnel (`ssh -L 8503:localhost:8503 <server>`, Dashboard bindet
an `127.0.0.1`). Kein Login im Dashboard, Settings-Tab kann echte `.env`
lesen/schreiben (Roadmap 0.4).

## 7. Abschluss-Check

```bash
venv/bin/python3 main.py --status        # Portfolio/Config sichtbar?
systemctl status aktien_bot.service      # falls aktiviert
ss -tlnp | grep 4002                     # IB Gateway erreichbar?
```

Vor echter Reaktivierung zusätzlich die vollständige Checkliste aus Roadmap
0.7 (Demo-Swap, backfill_regime, SEC_CONTACT_EMAIL, Registry, Versions-
Stempel, Backup-Timer, erster Zyklus beaufsichtigt) durchgehen.
