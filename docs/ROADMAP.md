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
- [x] **0.7 Reaktivierungs-Runbook** — fertig 11.7.: `docs/REAKTIVIERUNG.md`.
      Geordnete 8-Schritte-Checkliste: Demo-Swap zurück (0.3, selektiver
      Merge statt rm -rf — data/ ist seit 2.7. divergiert!) → backfill_regime
      → SEC_CONTACT_EMAIL/BACKUP_REMOTE in .env → Versions-Stempel (1.6,
      Gate: erst einbauen, dann starten) → Registry schlank neu
      (walk_forward --total 12 --max-combos 24) → Backup-Timer enablen (0.1)
      → Services/Crontab enablen → erster Zyklus beaufsichtigt +
      Montagslauf-Gegencheck → 1.9-Rest E2E (GTC-Stop nach erstem echten
      Kauf im Gateway sichtbar?). Inkl. Rückabwicklungs-Abschnitt. Das
      Dokument ist die Checkliste — ausgeführt wird sie erst bei der
      tatsächlichen Reaktivierung (User-Entscheidung).
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
- [~] **1.4 Transparenz: Quellen-Provenienz & Pipeline-Trace im Dashboard** —
      (a)+(b)+(e) fertig 11.7.: (a) sources_breakdown wird im Analyse-Log-Tab
      pro Eintrag gerendert (sprechende Collector-Namen, Treffer absteigend,
      leere Quellen als Caption); (b) analysis_log.store() liefert die
      Zeilen-ID, Runner reicht sie als analysis_id ans decision_log durch
      (Spalte + idempotente Migration), Entscheidungen-Tab zeigt die
      verkettete Analyse samt Quellen auf; (e) Quellen-Health-Ampel im
      Analyse-Log-Tab aus der bestehenden source_health-Mechanik
      (gesund/schwach/tot, Warnung bei dünner Datenlage). 8 Tests
      (test_provenance_link.py), Suite 408 grün, Dashboard headless
      durchgerendert (AppTest, keine Exceptions). Offen: (c)
      Verarbeitungs-Trace (Modell-Route, Makro-Brief-Bausteine, Gates) als
      provenance-JSON, (d) KI-Prompt-Archiv — beide wirken erst voll bei
      laufendem Bot. Queue-Drain-Entscheidungen tragen bewusst keine
      analysis_id (Signal-Analyse lag zeitlich früher).
- [~] **1.5 Live-Sichtbarkeit: "Was macht der Bot gerade?"** — (a)+(b)+(c)
      fertig 11.7.: system/live_status.py (fail-open, wirft nie).
      (a) Runner meldet Phasen (Start/Exits/Vorladen/Analyse je Ticker
      i/n/Abschluss) → data/bot_status.json (atomar); Scheduler schreibt
      zwischen Jobs Idle + nächsten geplanten Lauf (heilt Crash-Reste);
      Dashboard-Header rendert Live-Zeile mit ETA, Staleness-Check >30 min.
      (b) Aktivitätsfeed data/activity_feed.db (SQLite/WAL, Auto-Pruning
      ~2000): cycle_start/analysis_done/trade/cycle_end; neuer Dashboard-Tab
      "Live" zeigt die letzten 50. (c) Nächste-Aktionen-Panel im Live-Tab:
      nächster Scheduler-Lauf + systemd-Timer (list-timers, JSON) mit
      letztem/nächstem Lauf. 10 Tests, Suite 418 grün, Dashboard headless
      gerendert. Wirkt live erst bei laufendem Bot. Offen: (d) Gesundheits-
      Ampelleiste im Header, (e) Zyklus-Zeitleiste, (f) Order-Lifecycle-
      Ansicht, (g) Telegram /status-Befehl.
- [x] **1.6 Versions-Stempel in Entscheidungslogs** — fertig 11.7.
      `analyzers/version_stamp.py`: Git-Hash (kurz) + kuratierter
      Config-Schnappschuss (Whitelist entscheidungsrelevanter Werte +
      ENV-Flags, bewusst NIE die ganze Config — enthält Keys), einmal pro
      Prozess gecacht (laufender Prozess führt den Start-Code aus, frisch
      gelesener Hash wäre bei Änderungen im Working Tree falscher).
      decision_log + analysis_log stempeln jeden neuen Eintrag automatisch
      (Spalten git_hash/config_json, idempotente Migration wie cost_eur;
      analysis_log-Migration dabei auf PRAGMA-Muster umgestellt). Fail-open
      an jeder Naht. 9 Tests (test_version_stamp.py), Suite 400 grün;
      Migration gegen Kopien der echten DBs verifiziert (1620 Analyse-
      Zeilen erhalten, Alt-Zeilen NULL — rückwirkend nie).
- [ ] **1.7 Externer Dead-Man-Switch** — watchdog.sh läuft auf demselben
      Server; externer Dienst (z.B. healthchecks.io) soll Ausbleiben von
      Zyklus-Pings alarmieren.
- [x] **1.8 Zentrales Daten-Qualitäts-Gate** — fertig 11.7.
      analyzers/data_quality.py: (1) Kurs gültig (None/NaN/inf/≤0 → SKIP),
      (2) Kurs frisch (yahoo_collector liefert jetzt last_bar_date; älter
      als DATA_GATE_MAX_STALE_DAYS=5 → SKIP), (3) Skalenfehler-Detektor
      (Kurs >5× über 52W-Hoch bzw. <1/5 unter 52W-Tief, GBp↔GBP-Fälle;
      bewusst KEINE Volatilitäts-Bremse — echte Ausbrüche passieren),
      (4) nicht-finite Begleitfelder werden in place auf None bereinigt
      (NaN-Falle systematisch statt Punkt-Fix). Verdrahtet an beiden
      Analyse-Pfaden (Prefetch-Worker spart Claude-Call; serielle Schleife
      loggt SKIP ins decision_log mit eigenem Funnel-Bucket "daten_gate" +
      gate_blocked-Event in den Live-Feed). Fail-open: fehlende Felder
      werden nicht geprüft, Gate-Fehler blockt nie (Executor-Pfad behält
      eigene _valid_price-Schranke). 23 Tests, Suite 441 grün; E2E gegen
      echten yfinance-Abruf verifiziert.
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
- [x] **1.14 whatIf-Margin-Check vor Orders** — fertig 11.7. (Code + Tests).
      ibkr_broker._whatif_rejection() vor jedem placeOrder (Markt-Orders
      inkl. Krypto-Pfad): blockt NUR bei klarem Nein von IBKR —
      DBL_MAX-Sentinel in den Margin-Feldern oder Init-Margin nach Order >
      Eigenkapital (equityWithLoanAfter) → typisierter OrderResult.error
      statt Gateway-Ablehnung. Alles andere fail-open (leere Antwort,
      Exception, Parse-Fehler → Order geht raus, Gateway lehnt zur Not
      selbst ab). Flag IBKR_WHATIF_CHECK (Default an). 10 Tests, Suite 451
      grün. E2E gegen Paper-Gateway ausstehend: Gateway-Session Sa-Abend im
      Zombie-Zustand (Port offen, Anfragen timeout — IBKR-Wartungsfenster);
      zusammen mit dem offenen 1.9-Fill-Test Mo ab 15:30 CEST nachholen
      (steht in der 0.7-Checkliste).

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
- ✗ **Trade Republic als Datenquelle** — geprüft 11.7.2026: keine offizielle
  API (nur fragile Reverse-Engineering-Clients gegen die AGB, mit echten
  Login-Daten), Kurse sind nur LS-Exchange-Quotes (redundant zu
  yfinance/IBKR, qualitativ schlechter als 1.13), kein Informationsvorsprung.
  Widerspricht zudem dem Collector-Pruning-Ziel (2.4).

## User-Entscheidungen (Geld/Zugang, blockieren jeweilige Punkte)

- [ ] Point-in-Time-Daten kaufen? (Norgate/Sharadar/EODHD) → schaltet
      Survivorship-Mechanik scharf.
- [ ] Push-Token mit Contents:write? (→ 0.2)
- [ ] Bot-Reaktivierung wann? → Voraussetzung für Block 3 + Ziel 1/2.

**Vor Live-Relevanz außerdem**: Registry neu generieren (aktuell
Spielzeug-Lauf; schlank fahren: `--total 12 --max-combos 24`, sonst
>18 min CPU).
