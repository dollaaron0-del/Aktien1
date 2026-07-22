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

## 0. Was reist wie mit? (Überblick, Roadmap 6.1)

Drei unterschiedliche Mechanismen transportieren drei unterschiedliche Dinge —
das ist die häufigste Fehlerquelle bei einem Umzug ("hab ich das vergessen?").

| Was | Mechanismus | Deckt ab |
|---|---|---|
| **Code** | `git clone`/`pull` (Schritt 2) | Gesamtes Repo, versioniert |
| **Trading-/Lern-Daten** | `backup.sh` → `restore.sh` (Schritt 3) | `.env`, `data/*.db`/`.json` (Portfolio, Trades, Lern-DBs — siehe `scripts/backup.sh` CRITICAL/IMPORTANT/LEARNING-Arrays) |
| **IB-Gateway-Zugangsdaten + -Installation** | **manuell**, NICHT Git, NICHT Backup | `/opt/ibgateway/` komplett (inkl. `autologin.sh` mit den Login-Daten) — direkt scp/verschlüsselt übertragen (Schritt 5) |

Check vor dem eigentlichen Umzugstag: `bash scripts/backup.sh --verify` zeigt,
was das Backup abdeckt — alles, was dort NICHT auftaucht und trotzdem
gebraucht wird, muss manuell mitgenommen werden.

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

**Vor Bot-Start prüfen**: Ist `data/` echter Live-Stand oder eine
Präsentations-Demokopie? Im Zweifel User fragen, bevor der Bot mit falschen
Daten startet (Roadmap 0.3).

## 4. systemd-Units einrichten

Alle Units liegen versioniert unter `scripts/`:

```bash
cp scripts/aktien_bot.service scripts/aktien_dashboard.service \
   scripts/aktien_backup.service scripts/aktien_backup.timer \
   scripts/aktien_premarket_ibkr.service scripts/aktien_premarket_ibkr.timer \
   scripts/aktien_source_health.service scripts/aktien_source_health.timer \
   scripts/aktien_nightly_research.service scripts/aktien_nightly_research.timer \
   /etc/systemd/system/
systemctl daemon-reload
```

Danach **gezielt** aktivieren, nicht pauschal — der Bot ist ggf. bewusst
pausiert (siehe CLAUDE.md-Status). Beispiel für Vollbetrieb:

```bash
systemctl enable --now aktien_dashboard.service   # Dashboard, 127.0.0.1:8503
systemctl enable --now aktien_backup.timer        # tägliches Backup 03:00
systemctl enable --now aktien_nightly_research.timer  # Lab-Nachtläufe 01:00 (Roadmap 6.7)
systemctl enable --now aktien_bot.service \
  aktien_premarket_ibkr.timer aktien_source_health.timer
```

`aktien_monday_check.service/.timer` ist **kein** wiederkehrender Regelbetrieb
(war ein einmaliger Gegencheck auf ein festes Datum nach einem Bugfix,
15.6.2026) — nicht Teil dieses Repos, bei Bedarf neu mit aktuellem Datum
anlegen (Vorlage: `OnCalendar=<datum> <zeit>`, `Type=oneshot`).

## 5. IB Gateway

IB Gateway ist eine separate Java-GUI-Anwendung unter `/opt/ibgateway/`,
**nicht** Teil dieses Repos und **nicht** durch Backup/Restore abgedeckt.
Das ist historisch der fragilste Teil eines Neuaufsetzens — hier detailliert,
damit ein Umzug nicht wieder bei null anfängt.

**⚠ Zugangsdaten-Hinweis:** Die eigentlichen IB-Gateway-GUI-Login-Daten
(Paper-Konto DUQ463153) stecken **fest einprogrammiert in
`/opt/ibgateway/autologin.sh`** (als einzelne `xdotool`-Tastendrücke, nicht
in `.env`!). Diese Datei beim Umzug **direkt** (scp/verschlüsselter Transfer)
mit auf den neuen Server nehmen — niemals in dieses Repo, eine Doku oder
einen Chat kopieren. `.env` enthält nur die BOT-seitige Verbindungskonfiguration
(`IBKR_HOST`, `IBKR_PORT`, `IBKR_MARKET_DATA_TYPE` u.ä.), nicht das
Gateway-Login selbst.

**Setup-Reihenfolge auf dem neuen Server:**
1. IB Gateway installieren (Java-Runtime + Gateway-Installer von IBKR).
2. `Xvfb :99` als virtuelles Display einrichten (Gateway läuft headless,
   ohne echten Monitor) — **konsequent `:99` verwenden**, nicht `:1` (frühere
   Verwirrung durch zwei parallele Display-Configs in unterschiedlichen
   Skripten).
3. `autologin.sh` (aus dem alten Server übertragen, s.o.) startet Gateway,
   wartet 45s, sucht dann per `xdotool` das Login-Fenster über
   Fenstergrößen-Heuristik (100.000–700.000 px² Fläche) und **scannt per
   Pixel-Helligkeit** nach den Eingabefeldern (kein festes Koordinaten-Layout
   — das Fenster kann sich leicht verschieben). Tippt Username/Passwort
   zeichenweise über `xdotool key`, drückt Enter, prüft nach ~55s ob Port
   4002 offen ist.
4. **Bekannte Fragilität**: Die GUI-Automatisierung ist inhärent brüchig
   („FEHLER: Kein Fenster" ist ein reales, schon aufgetretenes Fehlerbild).
   Bei Problemen: `scrot`-Screenshots liegen unter `/tmp/ibgw_*.png`
   (init/before_type/after_type/after_login) — das Skript loggt ausführlich,
   welche Pixel-Cluster es gefunden hat und ob Klick/Tippen überhaupt eine
   sichtbare Wirkung hatten. Erwarte beim ERSTEN Durchlauf auf neuer
   Hardware, dass das nicht sofort klappt (andere Fenstergröße/-position
   als beim letzten Mal) — Zeit dafür einplanen, das ist NICHT der
   1-Stunden-Standardfall.
5. Drei Schutzschichten für den Dauerbetrieb (auf dem alten Server bewährt,
   auf dem neuen identisch nachbauen):
   - systemd `ibgateway.service` (falls vorhanden) mit `Restart=always`.
   - Stündlicher Cron-Watchdog — hält Port 4002 am Leben:
     ```
     0 * * * * ss -tlnp | grep -q 4002 || /opt/ibgateway/autologin.sh >> /var/log/ibgw-restart.log 2>&1
     ```
     Mit `crontab -e` neu eintragen.
   - `aktien_premarket_ibkr.timer` (Mo–Fr 07:15, im Repo) prüft proaktiv
     VOR dem 07:30-Zyklus mit eigener `clientId=97`, ob eine echte
     `managedAccounts`-Abfrage funktioniert — nicht nur ob der Port offen ist.
   - Automatischer Bot-Neustart täglich 06:00 nach Gateway-Restart ist
     aktuell bewusst auskommentiert (Bot-Pause-Historie, siehe CLAUDE.md) —
     nur auf Anweisung aktivieren.
6. `broker/ibkr_broker.py` setzt `reqMarketDataType(3)` (Delayed, abo-frei)
   automatisch nach Connect — kein manueller Marktdaten-Abo-Schritt nötig,
   solange `IBKR_MARKET_DATA_TYPE` nicht explizit auf einen anderen Wert
   gesetzt ist.
7. **KEIN stiller Paper-Fallback**: Ist IBKR beim Bot-Start nicht erreichbar,
   handelt der Bot laut Policy gar nicht (Order-Fehler statt Phantom-Paper-
   Buchung) — Gateway muss vor dem Bot-Start laufen und Port 4002 offen sein.

## 6. Firewall

`ufw` sollte Port 8503 (Dashboard) **nicht** von außen freigeben — Zugriff
nur per SSH-Tunnel (`ssh -L 8503:localhost:8503 <server>`, Dashboard bindet
an `127.0.0.1`). Optionales Passwort-Gate via `DASHBOARD_PASSWORD` in `.env`
(`dashboard/auth.py`, Default AUS); Settings-Tab kann in jedem Fall die echte
`.env` lesen/schreiben (Roadmap 0.4) — die Netzwerk-Absicherung bleibt die
eigentliche Schranke, das Passwort ist nur eine Zusatzhürde.

## 7. Abschluss-Check

```bash
venv/bin/python3 main.py --status        # Portfolio/Config sichtbar?
systemctl status aktien_bot.service      # falls aktiviert
ss -tlnp | grep 4002                     # IB Gateway erreichbar?
```

Vor echter Reaktivierung zusätzlich die vollständige Checkliste aus Roadmap
0.7 (Demo-Swap, backfill_regime, SEC_CONTACT_EMAIL, Registry, Versions-
Stempel, Backup-Timer, erster Zyklus beaufsichtigt) durchgehen.
