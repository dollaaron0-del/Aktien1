# Reaktivierungs-Runbook

Geordnete Checkliste, um den bewusst pausierten Bot (seit 21.6.2026, siehe
CLAUDE.md) wieder in Betrieb zu nehmen. Die Schritte bauen aufeinander auf —
**Reihenfolge einhalten**. Nichts hiervon läuft automatisch; jeder Schritt ist
eine bewusste Entscheidung. Stand: 11.7.2026 (Roadmap 0.7).

Abgrenzung: `docs/SERVER_RUNBOOK.md` = Server von Null wiederherstellen.
Dieses Dokument = vorhandenen, pausierten Bot auf diesem Server reaktivieren.

✅ Ist-Stand 14.7.2026 Nachmittag: Schritte 1, 3, 4, 5 sind erledigt (Demo-
Daten selektiv zurückgemerged, Registry frisch, Backup-Timer installiert +
aktiv). **Nur noch Schritt 2 (.env) ist offen** — dafür fehlt dieser Sitzung
der Datei-Zugriff (Bash auf `.env` ist hart gesperrt), das muss der User selbst
ergänzen:

```
SEC_CONTACT_EMAIL=<echte Kontakt-Mail>
BACKUP_REMOTE=<optional: rsync-Ziel, z.B. user@host:/pfad>
```

Danach ist **Schritt 6 der einzige verbleibende Schritt** — bewusst NICHT
ausgeführt, das ist der "Startbefehl", den sich der User vorbehält.

## Vorab-Check: Was gerade Sache ist

```bash
systemctl is-active aktien_bot.service aktien_dashboard.service \
  aktien_premarket_ibkr.timer aktien_source_health.timer aktien_backup.timer
crontab -l | grep aktien_bot        # 06:00-Zeile sollte auskommentiert sein
ss -tlnp | grep 4002                # IB Gateway muss laufen
```

## Schritt 1 — Demo-Daten-Rücktausch (Roadmap 0.3) ✅ ERLEDIGT (14.7.)

Vorher komplettes Backup gezogen (`backups/aktien_backup_20260714_130525.tar.gz`).
Content-Vergleich (nicht nur mtime) ergab: nur `portfolio.db`, `trade_journal.db`,
`performance.db`, `bot_score.json` trugen Demo-Werte (4 Fake-Positionen
NVDA/MSFT/ASML/LLY, aufgeblähte Trade-/Snapshot-/Prediction-Zahlen) — alle
Lern-DBs (`experience.db`, `decision_log.db`, `analysis_log.db`,
`calibration.json`, …) waren vom Swap unberührt. Die 4 betroffenen Dateien
gezielt aus `data_REAL_BACKUP_demo/` zurückkopiert (keine Komplett-Aktion).
Verifiziert: Portfolio zeigt jetzt 0 Positionen, 24 Trades, echtes Cash.
`backfill_regime --dry-run` meldete "nichts zu tun" (Regime-Labels bereits
aktuell). `data_REAL_BACKUP_demo/` bewusst nicht gelöscht.

## Schritt 2 — .env vervollständigen

- `SEC_CONTACT_EMAIL` setzen (seit Härtung Juni 2026 offen; SEC-EDGAR-Requests
  brauchen einen identifizierenden User-Agent, sonst drosselt/blockt EDGAR).
- Optional, empfohlen: `BACKUP_REMOTE` (rsync-Ziel) setzen, damit Backups
  nicht nur lokal liegen (Roadmap 0.1, Off-Server-Lücke).

## Schritt 3 — Versions-Stempel (Roadmap 1.6) ✅ ERLEDIGT (11.7.)

`decision_log`/`analysis_log` stempeln jeden neuen Eintrag automatisch mit
Git-Hash + Config-Schnappschuss (`analyzers/version_stamp.py`). Nichts mehr
zu tun hier — Schritt bleibt nur als Dokumentation der Reihenfolge stehen.

## Schritt 4 — Strategy-Registry neu generieren (schlank) ✅ ERLEDIGT (14.7.)

```bash
venv/bin/python -m scripts.walk_forward --total 12 --max-combos 24 --workers 0
```

Neu erzeugt (6 Kerne, `--workers 0`). Ergebnis: 0 ACTIVE, alle Familien
WATCH/FRAGILE/REJECTED — deckt sich mit dem bekannten Meta-Backtest-Befund,
keine Überraschung. baseline_swing hat laut Paper-Forward weiterhin keine
Kante gegen Buy&Hold; die Registry steuert nur das mechanische Gerüst.

## Schritt 5 — Backup-Timer enablen (Roadmap 0.1) ✅ ERLEDIGT (14.7.)

Installiert + aktiv, nächster Lauf: siehe `systemctl list-timers aktien_backup.timer`.

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
