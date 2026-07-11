# Reaktivierungs-Runbook

Geordnete Checkliste, um den bewusst pausierten Bot (seit 21.6.2026, siehe
CLAUDE.md) wieder in Betrieb zu nehmen. Die Schritte bauen aufeinander auf —
**Reihenfolge einhalten**. Nichts hiervon läuft automatisch; jeder Schritt ist
eine bewusste Entscheidung. Stand: 11.7.2026 (Roadmap 0.7).

Abgrenzung: `docs/SERVER_RUNBOOK.md` = Server von Null wiederherstellen.
Dieses Dokument = vorhandenen, pausierten Bot auf diesem Server reaktivieren.

## Vorab-Check: Was gerade Sache ist

```bash
systemctl is-active aktien_bot.service aktien_dashboard.service \
  aktien_premarket_ibkr.timer aktien_source_health.timer aktien_backup.timer
crontab -l | grep aktien_bot        # 06:00-Zeile sollte auskommentiert sein
ss -tlnp | grep 4002                # IB Gateway muss laufen
```

## Schritt 1 — Demo-Daten-Rücktausch (Roadmap 0.3) ⚠ NICHT trivial

`data/` ist seit 27.6. eine Demo-Kopie für eine Präsentation; die echten Daten
liegen in `data_REAL_BACKUP_demo/`. **Achtung, seit 2.7. divergiert:** die
Lern-Stack-Arbeit hat direkt in `data/` geschrieben (z.B. `experience.db`
neuer als das Backup). Ein naiver Komplett-Rücktausch (`rm -rf data && mv …`)
würde diese neueren Lerndaten **vernichten**.

Stattdessen selektiv mergen — vorher Ist-Stand je Datei klären:

- **Demo (aus `data_REAL_BACKUP_demo/` zurückholen)**: Portfolio/Trades —
  `portfolio.db`, `trade_journal.db`, `performance.db`, `bot_score.json`
  (Demo zeigt 4 Positionen NVDA/MSFT/ASML/LLY, 22 Trades, 68% Win-Rate —
  daran erkennbar).
- **Echt & neuer in `data/` (behalten!)**: Lern-DBs — `experience.db`,
  `decision_log.db`, `calibration.json`, `analysis_log.db`, `rl_weights.json`,
  `paper_forward.json`, `strategy_registry.json`.
- Bei Unklarheit pro Datei: mtime + Inhalt vergleichen (`sqlite3 <db>
  "select count(*) from …"`), im Zweifel beide Stände sichern.

Vorher komplettes Backup ziehen: `bash scripts/backup.sh` (sichert den
Ist-Zustand inkl. der zu überschreibenden Dateien).

Danach Regime-Labels auf der echten DB nachziehen:

```bash
venv/bin/python -m scripts.backfill_regime --dry-run   # erst ansehen
venv/bin/python -m scripts.backfill_regime             # dann schreiben
```

Anschließend Memory `demo-data-swap-aktiv` löschen (erledigt).

## Schritt 2 — .env vervollständigen

- `SEC_CONTACT_EMAIL` setzen (seit Härtung Juni 2026 offen; SEC-EDGAR-Requests
  brauchen einen identifizierenden User-Agent, sonst drosselt/blockt EDGAR).
- Optional, empfohlen: `BACKUP_REMOTE` (rsync-Ziel) setzen, damit Backups
  nicht nur lokal liegen (Roadmap 0.1, Off-Server-Lücke).

## Schritt 3 — Versions-Stempel (Roadmap 1.6) — VOR dem Start einbauen

Noch **offen** (Stand 11.7.): `decision_log`/`analysis_log` speichern weder
Git-Hash noch Config-Schnappschuss. Ohne das messen die Evidenz-Gates (1.1)
ein bewegliches Ziel — Track-Record über still wechselnde Code-Stände beweist
nichts. Wirkt nur ab Einbau, rückwirkend nie → **vor** der Reaktivierung
umsetzen, nicht danach. Erst wenn 1.6 committet ist, hier abhaken.

## Schritt 4 — Strategy-Registry neu generieren (schlank)

Die aktuelle Registry ist ein Spielzeug-Lauf. Neu erzeugen, bewusst klein
(sonst >18 min CPU auf diesem Server):

```bash
venv/bin/python -m scripts.walk_forward --total 12 --max-combos 24
```

Schreibt `data/strategy_registry.json`. Hinweis: baseline_swing hat laut
Paper-Forward keine Kante (verliert gegen Buy&Hold) — die Registry steuert
nur das mechanische Gerüst, Erwartungen entsprechend niedrig halten.

## Schritt 5 — Backup-Timer enablen (Roadmap 0.1)

```bash
cp scripts/aktien_backup.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now aktien_backup.timer
systemctl list-timers | grep aktien_backup   # nächster Lauf 03:00?
```

## Schritt 6 — Bot-Services + Crontab enablen

Nur wenn Schritte 1–5 erledigt sind (Dashboard läuft ggf. schon, schadet nicht):

```bash
sudo systemctl enable --now aktien_bot.service aktien_dashboard.service \
  aktien_premarket_ibkr.timer aktien_source_health.timer
```

Dann `crontab -e`: die auskommentierte 06:00-Zeile wieder aktivieren
(`0 6 * * * /opt/ibgateway/autologin.sh … && systemctl restart aktien_bot`).
Die stündliche autologin-Zeile (hält Port 4002) läuft bereits durch.

Hinweis: `aktien_monday_check.timer` war ein Einmal-Check (15.6.), **nicht**
wieder enablen. Und: nur EIN Bot- + EIN Dashboard-Service — Duplikat-Services
haben früher doppelte Telegram-Nachrichten verursacht.

## Schritt 7 — Ersten Zyklus beaufsichtigen

- `journalctl -fu aktien_bot` bzw. `tail -f logs/bot.log` beim ersten Zyklus
  mitlesen: Collectors liefern? Analyse läuft? Keine Crash-Loops?
- Telegram: kommt der Zyklus-Output an (TELEGRAM_MODE=important)?
- Dashboard (per SSH-Tunnel, `ssh -L 8503:localhost:8503`): Status-Banner
  zeigt „aktiv", Tab „Entscheidungen" füllt sich?
- Nach dem ersten Handelstag: Montagslauf-Gegencheck sinngemäß wiederholen
  (`venv/bin/python scripts/monday_cycle_check.py` prüft den letzten
  Vollzyklus auf Vollständigkeit).

## Schritt 8 — GTC-Schutz-Stops E2E verifizieren (Roadmap 1.9-Rest)

Nach dem **ersten echten Kauf** (US-Handelszeiten, Mo–Fr ab 15:30 CEST):

- Im IB Gateway / TWS prüfen: liegt zur neuen Position ein ruhender GTC-Stop?
- Damit ist der letzte offene 1.9-Punkt (voller Kauf→Stop→Verkauf-Durchlauf
  mit echtem Fill) abgehakt — bisher nur gegen Paper-Gateway ohne Fill
  getestet, weil die Börse zu war.
- Ebenso beobachten: Tagesverlust-Circuit-Breaker-Zustand (`data/daily_loss.json`)
  und dass keine Falschmeldungen bei Partial-TP kommen (Fixes vom 11.7.).

## Rückabwicklung (falls etwas schiefgeht)

```bash
sudo systemctl disable --now aktien_bot.service \
  aktien_premarket_ibkr.timer aktien_source_health.timer
# crontab -e: 06:00-Zeile wieder auskommentieren
```

Datenstand vor der Reaktivierung liegt im Backup aus Schritt 1.
