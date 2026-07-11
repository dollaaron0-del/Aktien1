# Ausbau-Roadmap

Stand: 11.7.2026. Konsolidierte Liste offener und erledigter Ausbau-Punkte für
den Trading-Bot. Bot ist bewusst pausiert (siehe CLAUDE.md) — diese Roadmap
wird Punkt für Punkt durchgegangen, wenn Zeit dafür ist, nicht auf Zuruf
komplett abgearbeitet.

Legende: `[x]` fertig · `[~]` teilweise erledigt · `[ ]` offen

## Block 0 — Akut/Fundament (unabhängig vom Bot, zuerst)

- [x] **0.1 Backup-Lücke schließen** — Code fertig & committet 7.7. (786888a):
      backup.sh LEARNING-Array (experience/decision_log/calibration/
      paper_forward/strategy_registry/analysis_log/rl_weights), Rotation
      (BACKUP_KEEP), Off-Server-Naht (BACKUP_REMOTE), `--verify`;
      aktien_backup.timer+.service (täglich 03:00). E2E getestet (34 MB,
      gültig). Offen: Timer nicht enabled (nur committet), BACKUP_REMOTE noch
      nicht gesetzt (Backup vorerst nur lokal). Enablen später:
      ```bash
      cp scripts/aktien_backup.{service,timer} /etc/systemd/system/
      systemctl daemon-reload
      systemctl enable --now aktien_backup.timer
      ```
- [ ] **0.2 Push-Frage klären** (User-Entscheidung) — origin/main hängt beim
      21.5. hinterher; Token bräuchte Contents:write. Code+Daten liegen sonst
      auf einer Platte.
- [ ] **0.3 Demo-Daten-Rücktausch** — Pflicht vor Bot-Reaktivierung; echte
      `data/` liegt im Backup, aktuell Demo-Kopie für Präsentation aktiv.
      Danach `python -m scripts.backfill_regime` auf der echten DB.
- [~] **0.4 Dashboard/Ports absichern** (11.7. erledigt, Netzwerk-Teil fertig).
      Befund: manuell gestartete Streamlit-Instanz auf :8503 (0.0.0.0, XSRF
      aus), ufw ließ 8503 weltweit rein, kein Login, Settings-Tab kann echte
      `.env` (Anthropic/Telegram/IBKR-Keys) lesen+schreiben. 8501/8888/8082 =
      Demo-Server aus `/root/showcase` (anderes Projekt, nicht angefasst).
      Erledigt: ufw-Regel 8503 entfernt (v4+v6, extern nicht mehr erreichbar);
      `scripts/aktien_dashboard.service` korrigiert (Pfade `/opt/Aktien`, Port
      vereinheitlicht auf 8503, `--server.address=127.0.0.1`, SSH-Tunnel-Doku),
      nach `/etc/systemd/system/` gespiegelt + `daemon-reload`; manueller
      Alt-Prozess (PID 3057754, seit 27.6. auf 0.0.0.0) beendet, Service
      `enable --now` gesetzt (reboot-fest, aktiv, lauscht jetzt nur auf
      127.0.0.1:8503, verifiziert per `ss`). Zugriff per
      `ssh -L 8503:localhost:8503 <server>`. Noch nicht behoben: Settings-Tab
      ohne In-App-Login (Netzwerk-Absicherung war Kernfix, Login/Reverse-Proxy
      wäre Zusatzhärtung — bleibt offen, kein akuter Netzwerk-Zugriff mehr).
- [x] **0.5 Restore-Probe + Server-Runbook** — fertig 11.7. Echtes Backup
      erzeugt (34 MB, `backups/`), auf frischem `git clone` in isoliertem
      Verzeichnis komplett zurückgespielt (nicht gegen `/opt/Aktien` — dort
      ist `data/` aktuell Demo/Lern-Mix, siehe 0.3). Dabei **Bug gefunden &
      gefixt**: `restore.sh` installierte Pakete zuvor ins System-Python
      statt in eine venv — schlägt auf diesem Debian/Ubuntu mit PEP 668
      (`externally-managed-environment`) fehl; unbeaufsichtigt zurückgespielt
      liefe der Bot ganz ohne Pakete, der alte Schnelltest hätte das nicht
      gemerkt (nutzte ebenfalls System-Python, das zufällig bereits ähnliche
      Pakete hatte). Jetzt legt `restore.sh` bei Bedarf `venv/` an und
      installiert/testet konsequent darüber (101/101 Pakete exakt wie
      `requirements.lock`, Konfiguration + DB-Zugriff grün). Zweiter Fund:
      `aktien_premarket_ibkr.*` und `aktien_source_health.*` (wiederkehrende
      Timer) lagen nur in `/etc/systemd/system/`, nicht im Repo — bei
      Server-Neuaufbau verloren gewesen; jetzt nach `scripts/` nachgezogen
      und committet. Ergebnis: `docs/SERVER_RUNBOOK.md` (Pakete, Code,
      Restore, systemd-Units, IB Gateway, Firewall, Abschluss-Check).
- [x] **0.6 Dependency-Pinning** — fertig 11.7. `requirements.lock` per
      `pip freeze` aus der laufenden venv erzeugt (101 Pakete, exakt gepinnt,
      keine lokalen/editable Pfade). `requirements.txt` bleibt die
      Quelle mit Untergrenzen für bewusste Upgrades, Kommentar-Block verweist
      auf den Workflow (`pip install --upgrade -r requirements.txt && pip
      freeze > requirements.lock`, danach Testsuite). `scripts/restore.sh`
      nutzt jetzt bevorzugt `requirements.lock` (Fallback auf
      `requirements.txt`, falls kein Lockfile im Archiv) — direkt relevant
      für die geplante Restore-Probe (0.5). `setup_mac_mini.sh` bewusst
      unverändert (anderes Ziel-System, andere Wheels).
- [ ] **0.7 Reaktivierungs-Runbook** — verstreute Vorbedingungen als eine
      geordnete Checkliste: Demo-Swap zurück (0.3) → backfill_regime →
      SEC_CONTACT_EMAIL in .env → Registry neu (schlank!) → Versions-Stempel
      (1.6) drin? → Backup-Timer enablen (0.1) → Services/Crontab enablen
      (CLAUDE.md) → erster Zyklus beaufsichtigt + Montagslauf gegenchecken;
      dabei 1.9 E2E prüfen: GTC-Schutz-Stops nach erstem Kauf im IB Gateway
      sichtbar?
- [x] **0.8 Roadmap ins Repo spiegeln** — dieses Dokument; lebte vorher nur im
      Assistenten-Memory, nicht in Git/Backup erfasst. Bei Änderungen beide
      Stellen pflegen (Memory bleibt Arbeitskopie).

## Block 1 — Mess-Fundament (vor Bot-Reaktivierung sinnvoll)

- [x] **1.1 Evidenz-Gates operationalisieren** — fertig & committet 7.7.
      (dff7d1d): scripts/track_record.py (+ 11 Tests). Bootstrap-95%-CI auf
      Ø-Trade-Rendite (numpy, kein scipy) + P(Edge≤0); paired vs-B&H über
      regionalen Index je Haltefenster; Edge-CI je Regime; kodierte Gates
      (n Live-Trades≥100, Edge>0, schlägt B&H, Edge je Regime) → PASS/FAIL.
      Befund: 56 echte Backfill-BUYs = −1,94 %/Trade, verliert vs B&H
      (P≤0 98 %), alle 4 Gates FAIL. Regime-Achse aktuell trivial (nur BULL).
- [x] **1.2 Kalibrierungs-Monitoring** — fertig & committet 7.7. (07c9d22):
      scripts/calibration_monitor.py (+ 16 Tests). Walk-forward Brier +
      Brier-Skill-Score, Reliability-Bänder/ECE/MCE, AUC, Drift-Erkennung +
      Snapshot-Verlauf. Kodierte Stufenpfad-Gates Advisory→Sizing. Befund:
      AUC 0,61 (diskriminiert) aber überkonfident (ECE 0,126, BSS −0,02) →
      Verdikt nur ADVISORY, greift nicht ins Live-Sizing ein.
- [x] **1.3 Kosten-Attribution pro Entscheidung** — fertig & committet 7.7.
      (1fb44e5): decision_log um cost_eur-Spalte + add_cost()/cost_stats();
      scripts/cost_attribution.py = Brutto-Kante − Kosten → Netto-Kante +
      Break-even-Positionsgröße + Selbsttragend-Gate. Befund: API-Kosten
      0,12 €/Trade vernachlässigbar — das Problem ist die Kante, nicht die
      Kosten.
- [ ] **1.4 Transparenz: Quellen-Provenienz & Pipeline-Trace im Dashboard** —
      pro Entscheidung sichtbar machen, welche Quellen einflossen und wie sie
      verarbeitet wurden. Gestaffelt: (a) vorhandenes sources_breakdown im
      Dashboard anzeigen, (b) decision_log↔analysis_log verknüpfen, (c)
      Verarbeitungs-Trace (Modell-Route, Makro-Brief-Bausteine, Gates) als
      provenance-JSON, (d) KI-Prompt-Archiv (voller Prompt + Antwort je
      Analyse), (e) Quellen-Health-Ampel im Dashboard. (a)-(b) sofort
      möglich, (c)-(d) wirken erst voll bei laufendem Bot.
- [ ] **1.5 Live-Sichtbarkeit: "Was macht der Bot gerade?"** — Kernpaket
      (a) Live-Status-Zeile im Dashboard-Header (data/bot_status.json je
      Phasenwechsel), (b) Live-Aktivitätsfeed (JSONL/SQLite statt Textlog),
      dazu optional (c) Nächste-Aktionen-Panel, (d) Gesundheits-Ampelleiste,
      (e) Zyklus-Zeitleiste, (f) Order-Lifecycle-Ansicht, (g) Telegram
      /status-Befehl. (a)-(c) bot-unabhängig vorbereitbar.
- [ ] **1.6 Versions-Stempel in Entscheidungslogs** (vor Reaktivierung!) —
      decision_log/analysis_log speichern weder Git-Hash noch
      Config-Schnappschuss → Evidenz-Gates (1.1) messen sonst ein
      bewegliches Ziel. Billige Spalten (git_hash, config_json), idempotente
      Migration wie cost_eur — wirkt nur ab Einbau, rückwirkend nie.
- [ ] **1.7 Externer Dead-Man-Switch** — watchdog.sh läuft auf demselben
      Server; externer Dienst (z.B. healthchecks.io) soll Ausbleiben von
      Zyklus-Pings alarmieren.
- [ ] **1.8 Zentrales Daten-Qualitäts-Gate** — NaN/inf, veraltete Kurse,
      unplausible Sprünge vor der Analyse erkennen → Ticker überspringen +
      Event loggen statt mit Müll zu rechnen.
- [x] **1.9 Broker-seitige Stop-Loss-Orders (IBKR)** — fertig & committet
      11.7. (b03f967). Jede Position hat einen ruhenden GTC-Stop bei IBKR
      (Notfallnetz bei Bot-Ausfall; Bot-Exits bleiben führend, bewusst kein
      TP-Limit beim Broker). buy() platziert Stop nach Fill, sell() räumt
      Stops vor dem Verkauf, update_stop() nach Partial-TP,
      sync_protective_stops() heilt Alt-Positionen. Flag IBKR_SERVER_STOPS.
      11 Tests, Suite 385 grün. E2E gegen Paper-Gateway bestätigt (11.7.).
      Bewusst offen: Trailing-Stop-Anhebungen werden nicht live zum Broker
      gesynct.
- [ ] **1.10 API-Kosten-Hebel** — (a) Prompt-Caching für Makro-Brief-Präfix,
      (b) Batch-API für Routineanalysen (50% Rabatt), (c) Modell-Tiering
      (Haiku für Routine-Scans).
- [x] **1.11 Trade-Pfad-Fixes (Audit 11.7.)** — fertig & committet 11.7.
      (92938c8, 7463913, bc7a8f6). Hauptpfad via TradeExecutor war sauber,
      Nebenpfade (EarningsStrategy, HedgeStrategy, Rebalancer) buchten ohne
      Broker-Order/Fill-Check — alles gefixt: Order zuerst, Buch nur bei
      bestätigtem Fill, Fehlschlag → Position bleibt offen + Alarm. Dazu
      Rebalancer-Crash (get_cash()), IBKR-Fill-Buchung, Partial-TP-
      Falschmeldung behoben sowie Tagesverlust-Circuit-Breaker verkabelt
      (griff vorher nie). 9 Regressionstests, Suite 391 grün.
- [ ] **1.12 Broker-seitige Trailing-Stops (IBKR TRAIL)** — schließt die
      1.9-Grenze: GTC-Backstop bleibt aktuell auf Einstands-SL, während
      Bot-Trailing das Buch-SL anhebt. IBKR-TRAIL würde den Broker selbst
      nachziehen lassen.
- [ ] **1.13 IBKR-Kursdaten via reqHistoricalData** — Kurse/Historie direkt
      vom Broker statt/neben yfinance, entschärft die yfinance-Abhängigkeit.
- [ ] **1.14 whatIf-Margin-Check vor Orders** — ib_insync whatIfOrder() vor
      jeder echten Order als billige Plausibilitätsprüfung.

## Block 2 — Kein-Kante-Befund angehen (strategy_lab Prio 1)

- [ ] **2.1 Exit-Lab** — Exits parametrisierbar (ATR-Trailing, Regime-Stops,
      Time-Varianten) + Walk-Forward. Adressiert MaxDD −69,5%.
- [ ] **2.2 Portfolio-Level-Backtest** — Cash-Constraint, Max-Positionen,
      Korrelations-Kappung, Vol-Targeting.
- [ ] **2.3 Stress-Test-Harness** — 2008/2020/2022 durch die
      Entscheidungs-Pipeline; gestuftes De-Risking statt binärem
      CircuitBreaker.
- [ ] **2.4 Quellen-Ablation / Collector-Pruning** — 30+ Collectors, bisher
      nur hinzugefügt, nie entfernt. Periodisch messen, welche Quelle
      Entscheidungen messbar verbessert; tote/wertlose Quellen abschalten.
      Braucht Datenhistorie → nach Reaktivierung.
- [ ] **2.5 Ziel-Nachführung aus Neuanalysen (TP-Update)** — target_price aus
      Neuanalysen offener Positionen ins Positions-Buch übernehmen (nur bei
      signifikanter Abweichung + Konfidenz≥MEDIUM). Vorher im Exit-Lab (2.1)
      walk-forward gegentesten, ob TP-Updates überhaupt Kante bringen.

## Block 3 — Alpha-These prüfen (braucht laufenden Bot + Zeit)

- [ ] **3.1 Sentiment-Forward-Study** — Event-Study aus ExperienceStore
      (Edge/IC je Sentiment-Bucket) → zweite Advisory-Quelle in live_bridge.
- [ ] **3.2 Skip-Kontrafaktik** — decision_log-SKIPs mit simulate_outcome
      nachrechnen → EntryFilter-Schwellen mit Gegenproben validieren.

## Block 4 — Meta-Ebene & Robustheit

- [ ] **4.1 Meta-Backtest des Allokators** — weight_plan + Regime-Faktor
      selbst validieren.
- [ ] **4.2 Bootstrap/Monte-Carlo in der Promotion** — DD-Konfidenzintervalle
      statt Punkt-Verdikte.
- [ ] **4.3 Regime-Übergangsmodell** — Hysterese, Regime-Signal selbst per
      Paper-Forward tracken.
- [ ] **4.4 Code-Gesundheit + lokale CI** — 500-Zeilen-Regel (CLAUDE.md)
      verletzt: dashboard/app.py 3215, bot/scheduler.py 2415, bot/runner.py
      1698 Zeilen — genau in diesen Monolithen entstanden stille Bugs
      (Watchdog-Zeitzonen, Headline-Trigger). Schrittweiser Modul-Split;
      dazu lokale CI (Pre-Commit-Hook oder Timer für die Testsuite —
      GitHub Actions geht mangels Push nicht, vgl. 0.2).
- [ ] **4.5 Entscheidungs-Replay** — baut auf 1.4d Prompt-Archiv. Vergangenen
      Zyklus deterministisch aus archivierten Eingangsdaten nachspielen.

## Block 5 — Breite (erst nach 2–4)

- [ ] **5.1 Mehr Familien** — PEAD via pead_tracker-Naht, 52W-Hoch,
      Gap-MeanRev, Saisonalität.
- [ ] **5.2 Universum erweitern** — EU/Krypto durchs selbe Lab,
      Generalitäts-Check.
- [ ] **5.3 Slippage-Kalibrierung** aus echten IBKR-Paper-Fills.

## Verworfen (nicht wieder vorschlagen)

- ✗ **Trade-Freigabe per Telegram (Human-in-the-Loop)** — User-Entscheid
  11.7.2026: nicht nötig, Bot läuft ohnehin erstmal auf Paper-Account,
  Fehler dort egal. Ggf. neu bewerten, falls Umstieg auf echtes Geld
  ansteht.

## User-Entscheidungen (Geld/Zugang, blockieren jeweilige Punkte)

- [ ] Point-in-Time-Daten kaufen? (Norgate/Sharadar/EODHD) → schaltet
      Survivorship-Mechanik scharf.
- [ ] Push-Token mit Contents:write? (→ 0.2)
- [ ] Bot-Reaktivierung wann? → Voraussetzung für Block 3 + Ziel 1/2.

**Vor Live-Relevanz außerdem**: Registry neu generieren (aktuell
Spielzeug-Lauf; schlank fahren: `--total 12 --max-combos 24`, sonst
>18 min CPU).
