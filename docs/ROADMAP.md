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
- [x] **0.4 Dashboard/Ports absichern** (11.7. Netzwerk-Teil, 12.7. Login-Rest).
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
      `ssh -L 8503:localhost:8503 <server>`.
      ✅ REST GEBAUT 12.7.: dashboard/auth.py — Passwort-Gate vor dem
      gesamten Rendering (require_login(), st.stop() bis Login), optional
      per DASHBOARD_PASSWORD in .env (Default AUS = exakt altes Verhalten,
      kein Breaking Change). Konstante-Zeit-Vergleich (secrets.compare_digest),
      Zustand in session_state (kein erneutes Passwort bei jedem Rerun). In
      app.py direkt nach st.set_page_config() verdrahtet. 5 Tests
      (test_dashboard_auth.py, headless via streamlit.testing.v1 AppTest auf
      einem isolierten Mini-Skript statt des vollen app.py), Suite grün.
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
- [x] **1.4 Transparenz: Quellen-Provenienz & Pipeline-Trace im Dashboard** —
      (a)+(b)+(e) fertig 11.7.: (a) sources_breakdown wird im Analyse-Log-Tab
      pro Eintrag gerendert (sprechende Collector-Namen, Treffer absteigend,
      leere Quellen als Caption); (b) analysis_log.store() liefert die
      Zeilen-ID, Runner reicht sie als analysis_id ans decision_log durch
      (Spalte + idempotente Migration), Entscheidungen-Tab zeigt die
      verkettete Analyse samt Quellen auf; (e) Quellen-Health-Ampel im
      Analyse-Log-Tab aus der bestehenden source_health-Mechanik
      (gesund/schwach/tot, Warnung bei dünner Datenlage). 8 Tests
      (test_provenance_link.py), Suite 408 grün, Dashboard headless
      durchgerendert (AppTest, keine Exceptions). (c) FERTIG 14.7.:
      Verarbeitungs-Trace als provenance_json-Spalte in analysis_log
      (idempotente Migration). model_route/frugal_reason werden zentral in
      ClaudeAnalyzer.analyze() gestempelt (_stamp_route(), ein einziger
      Stempel-Punkt direkt vor jedem return statt in den einzelnen
      Bau-Methoden) — deckt alle Pfade ab (empty/ollama_frugal_full/
      ollama_frugal_thesis/ollama_legacy/ollama_fallback/claude/
      claude_dedup_cache) samt Klartext-Begründung. macro_context.
      summarize_sources() leitet pro Makro-Quelle True/False rein additiv
      aus vorhandenen Snapshot-Keys ab (kein neuer Collector-Code).
      cycle_analysis.py führt beides + das 1.8-Daten-Gate (ok/reason/
      sanitized_fields) einmal pro Ticker zu einem provenance-dict
      zusammen und reicht es an analysis_log.store() durch. Alt-Zeilen
      ohne provenance_json liefern {} statt JSON-Fehler. 9 neue Tests
      (test_analysis_provenance.py) + Ergänzungen in test_analysis_log_
      sources.py/test_macro_context.py/test_run_analysis_cycle.py, volle
      Suite grün. (d) FERTIG 14.7.: KI-Prompt-Archiv — neues
      analyzers/prompt_archive.py (eigene data/prompt_archive.db, Tabelle
      prompts, analysis_id/ticker/model/system_prompt/user_prompt/
      response_text), verkettet über dieselbe analysis_id wie 1.4b.
      AnalysisResult trägt jetzt raw_model/raw_system_prompt/raw_user_prompt/
      raw_response, gesetzt an GENAU den zwei Stellen mit einem echten
      Claude-Aufruf (_claude_analysis, _thesis_check) — bewusst NUR Claude
      (teuerste Stufe), Ollama-/Frugal-Routen bleiben leer. Cache-Hits
      (claude_dedup_cache) dürfen NICHT erneut archivieren: der Dedup-Cache
      (data/claude_result_cache.json) entfernt die raw_*-Felder vor dem
      Schreiben, ein Cache-Hit liefert sie deshalb leer zurück. Verkabelung:
      runner.py instanziiert _prompt_archive einmal (wie _analysis_log),
      cycle_analysis.py archiviert direkt nach dem analysis_log.store()-
      Aufruf, nur wenn analysis_id vorhanden UND raw_response gesetzt ist.
      15 neue Tests (test_prompt_archive.py + Ergänzungen in
      test_analysis_provenance.py/test_run_analysis_cycle.py), volle
      Suite 848 grün. Basis für Entscheidungs-Replay (Roadmap 4.5). Queue-
      Drain-Entscheidungen tragen bewusst keine analysis_id (Signal-Analyse
      lag zeitlich früher) — damit auch kein Prompt-Archiv-Eintrag.
- [x] **1.5 Live-Sichtbarkeit: "Was macht der Bot gerade?"** — (a)+(b)+(c)+(d)+(e)+(f)+(g)
      fertig: system/live_status.py (fail-open, wirft nie).
      (a) Runner meldet Phasen (Start/Exits/Vorladen/Analyse je Ticker
      i/n/Abschluss) → data/bot_status.json (atomar); Scheduler schreibt
      zwischen Jobs Idle + nächsten geplanten Lauf (heilt Crash-Reste);
      Dashboard-Header rendert Live-Zeile mit ETA, Staleness-Check >30 min.
      (b) Aktivitätsfeed data/activity_feed.db (SQLite/WAL, Auto-Pruning
      ~2000): cycle_start/analysis_done/trade/cycle_end; neuer Dashboard-Tab
      "Live" zeigt die letzten 50. (c) Nächste-Aktionen-Panel im Live-Tab:
      nächster Scheduler-Lauf + systemd-Timer (list-timers, JSON) mit
      letztem/nächstem Lauf. 10 Tests, Suite 418 grün, Dashboard headless
      gerendert. Wirkt live erst bei laufendem Bot. (d) FERTIG 13.7.
      (3f2fc86): Gesundheits-Ampelleiste im Header — IB-Gateway (TCP-Connect,
      0.4s Timeout), Claude-Tageskosten vs. Limit, Circuit-Breaker-Status;
      fail-open pro Check, bewusst unabhängig vom Bot-Pause-Zustand gerendert
      (autologin.sh hält Port 4002 auch pausiert offen). (e) FERTIG 14.7.:
      Zyklus-Zeitleiste — set_phase() führt jetzt phase_history in
      bot_status.json, ein Eintrag PRO PHASENNAME (Start/Exits prüfen/
      Vorladen/Analyse) statt pro Aufruf (sonst würde jeder Ticker innerhalb
      "Analyse" die Zeitleiste fluten — nur Namenswechsel schließt die
      vorherige Phase und öffnet die nächste). set_idle() schließt die letzte
      Phase, behält die Historie aber bis zum nächsten set_phase()-Aufruf
      (Zeitleiste bleibt auch im Idle-Zustand sichtbar). Neue Funktion
      phase_durations() rechnet Dauer je Phase in Sekunden (offene letzte
      Phase eines laufenden Zyklus: bis jetzt). Dashboard-Tab "Live" zeigt
      sie als kleine Liste unter dem Aktivitätsfeed. 7 neue Tests
      (test_live_status.py), Suite 855 grün, Dashboard headless
      durchgerendert (AppTest, keine Exceptions, Zeitleiste im Baum
      gefunden). Wirkt live erst bei laufendem Bot. (f) FERTIG 14.7.:
      Order-Lifecycle-Ansicht — broker/order_log.py (data/order_log.db):
      log_order()-Decorator liegt EINMAL außen um buy()/sell()/buy_crypto()/
      sell_crypto() in PaperBroker UND IBKRBroker, statt an jedem Aufrufer
      (TradeExecutor, HedgeStrategy, ShortStrategy, EarningsStrategy) oder
      jedem einzelnen internen return-Pfad (IBKR hat mehrere Fehler-Returns
      pro Methode) anzusetzen — sieht dadurch jedes Ergebnis unabhängig vom
      intern gewählten Pfad, ohne die Methodenkörper selbst anzufassen
      (geringeres Risiko in echtem Handelscode). Fail-open: ein Logging-Fehler
      darf eine Order nie verhindern oder verändern (Decorator fängt
      Log-Exceptions separat vom eigentlichen Order-Ergebnis ab). Dashboard-
      Tab "Live" zeigt die letzten 30 Orders (Aktion/Status-Icons, Titel,
      Modus, Fill-Menge/-Preis bzw. Fehlergrund, Teilausführungs-Hinweis).
      11 Tests (test_order_log.py, u.a. Decorator bewahrt Signatur-
      Introspektion für executor._broker_accepts_stop() UND ist fail-open bei
      kaputtem Log-Backend; PaperBroker-Verkabelung real getestet), Suite
      weiterhin grün. Dashboard headless verifiziert (AppTest gegen isolierte
      Temp-DB: gefüllte + Fehler-/Teilausführungs-Fälle rendern ohne
      Exception). (g) FERTIG 14.7.: Telegram /status-Befehl —
      system/telegram_commands.py. Kein Webhook (kein öffentlicher HTTPS-
      Endpunkt vorhanden), stattdessen getUpdates-Short-Polling
      (`timeout=0`, kein blockierendes Long-Polling) an derselben Stelle der
      Scheduler-Hauptschleife wie der Dead-Man-Switch-Ping (1.7) — die
      Schleife tickt ohnehin ~1×/Minute, ein zusätzlicher schneller HTTP-Call
      fällt nicht ins Gewicht. Nur Nachrichten aus dem konfigurierten
      TELEGRAM_CHAT_ID werden akzeptiert (kein offener Befehlskanal); der
      zuletzt verarbeitete update_id wird in data/telegram_offset.json
      gemerkt (kein Reprocessing alter Befehle nach Neustart).
      build_status_text() liefert dieselben Signale wie die Dashboard-
      Gesundheits-Ampelleiste (1.5d: Pause-Zustand, Zyklus-Phase/nächster
      Lauf, IB-Gateway, Claude-Tageskosten, Circuit-Breaker) + Portfolio-Wert/
      Cash/Positionen — jeder Baustein einzeln fail-open, ein kaputter Check
      verschluckt nie die übrigen Zeilen. Neue Wichtigkeitsstufe "command" in
      notifier/telegram_notifier.py (_IMPORTANT_LEVELS): eine direkte Antwort
      auf einen Nutzer-Befehl darf im TELEGRAM_MODE=important nie unterdrückt
      werden, anders als automatisierte info-Meldungen. 24 neue Tests
      (test_telegram_commands.py + 1 Ergänzung in test_telegram_levels.py),
      Suite weiterhin grün. Bewusst nur /status (wie in der Roadmap benannt) —
      kein generisches Befehls-Framework, kein Trade-Eingriff per Telegram
      (Human-in-the-Loop wurde 11.7. bereits verworfen).
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
- [x] **1.7 Externer Dead-Man-Switch** — fertig 12.7.
      system/dead_man_switch.py: ping() an eine konfigurierbare
      DEAD_MAN_SWITCH_URL (z.B. healthchecks.io, kostenlos), aus der
      Scheduler-Hauptschleife heraus alle DEAD_MAN_SWITCH_INTERVAL_MIN
      (Default 5) Minuten. No-Op ohne gesetzte URL, fail-open bei
      Netzwerkfehlern. Ergänzt watchdog.sh um den Fall, dass Server/Netz
      selbst ausfällt (watchdog.sh kann dann nicht mehr alarmieren) — bleibt
      der Ping aus, meldet der externe Dienst selbst. Setup: Account bei
      healthchecks.io (o.ä.) anlegen, Check mit Periode ≥ Intervall +
      Karenzzeit anlegen, Ping-URL in DEAD_MAN_SWITCH_URL (.env) eintragen.
      4 Tests (test_dead_man_switch.py), Suite 467 grün.
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
- [~] **1.10 API-Kosten-Hebel** — Roadmap-NACHTRAG 12.7.: (a)+(c) waren
      bereits seit 18.6. fertig & aktiv (5521e37), hier nur bisher nicht
      abgehakt: (a) Prompt-Caching AKTIV (claude_analyzer._system_blocks/
      _cache_control, CLAUDE_CACHE_TTL Default "1h") — System-Prompt + der
      pro-Zyklus-konstante Makro/Geo-Kontext sind gecachte Blöcke, einmal
      statt pro Ticker bezahlt, Beta-Header hält den Cache über den ganzen
      (langsamen CPU-)Zyklus warm. (c) Modell-Tiering AKTIV
      (_light_model(), CLAUDE_MODEL_LIGHT Default Haiku 4.5) — Thesis-/
      Exit-Checks offener Positionen laufen auf Haiku (~1/3 Kosten von
      Sonnet), die finale Katalysator-Analyse bleibt auf dem Hauptmodell;
      Cost-Tracker rechnet modellspezifisch ab. (b) Batch-API BEWUSST NICHT
      gebaut: kein Batch-shaped Aufrufer vorhanden — der Live-Zyklus braucht
      synchrone Ergebnisse (Trading-Entscheidung), keine 24h-Latenz;
      unbenutzter Client wäre totes Gerüst. Natürlicher künftiger Aufrufer:
      6.8a-Annotations-Gate (~200 Filings doppelt labeln) — dort erst bauen,
      wenn dieser Schritt ansteht.
- [x] **1.11 Trade-Pfad-Fixes (Audit 11.7.)** — fertig & committet 11.7.
      (92938c8, 7463913, bc7a8f6). Hauptpfad via TradeExecutor war sauber,
      Nebenpfade (EarningsStrategy, HedgeStrategy, Rebalancer) buchten ohne
      Broker-Order/Fill-Check — alles gefixt: Order zuerst, Buch nur bei
      bestätigtem Fill, Fehlschlag → Position bleibt offen + Alarm. Dazu
      Rebalancer-Crash (get_cash()), IBKR-Fill-Buchung, Partial-TP-
      Falschmeldung behoben sowie Tagesverlust-Circuit-Breaker verkabelt
      (griff vorher nie). 9 Regressionstests, Suite 391 grün.
- [x] **1.12 Broker-seitige Trailing-Stops** — fertig 12.7. Schließt die
      1.9-Grenze, aber via Backstop-Sync statt IBKR-TRAIL-Ordertyp: Bot-
      Trailing bleibt führend (3 Stufen, Soft-Stop-Extension), jede Ratchet-
      Anhebung wird per bestehendem update_stop() sofort an den GTC-Backstop
      durchgereicht (SwingStrategy._sync_broker_stop()). Kein Doppel-
      Management, kein neuer Ordertyp nötig. Dabei einen scharfen
      Altbug gefunden: Portfolio.update_position_state() existierte gar
      nicht — jede Trailing-Ratchet-Anhebung crashte check_exits() für den
      GANZEN Zyklus (AttributeError, kein try/except). Methode ergänzt
      (portfolio/portfolio.py). 3 neue Tests, Suite 470 grün.
- [x] **1.13 IBKR-Kursdaten via reqHistoricalData** — fertig 12.7., erster
      Schritt (Live-Pfad-Kurse, Backtests bewusst noch unangetastet).
      IBKRBroker.get_history() (reqHistoricalData, Delayed-Bars reichen dank
      IBKR_MARKET_DATA_TYPE) liefert ein DataFrame im yfinance-Format
      (Open/High/Low/Close/Volume, DatetimeIndex) → TechnicalIndicators
      nimmt jetzt optional einen broker-Parameter und bevorzugt IBKR, fällt
      aber bei Flag aus/Verbindungsfehler/leerer Antwort/zu kurzer Historie
      auf yfinance zurück (mehrstufig fail-open). Einziger Live-Aufrufer
      (SwingStrategy._atr_vol_multiplier, ATR-Sizing) reicht jetzt den
      Broker durch. Neues Flag IBKR_HISTORICAL_DATA (Default an). 8 neue
      Tests, Suite 478 grün. Backtests/Dashboard bewusst unverändert auf
      yfinance (kein Broker im Kontext).
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

- [x] **2.1 Exit-Lab** — fertig 12.7. Exits parametrisierbar gemacht (ATR-
      Trailing-SL, regime-abhängige SL/TP-Skalierung, Soft-Time-Stop) und
      durch den bestehenden Walk-Forward gejagt. Neue BacktestConfig-Felder
      (sl_mode/atr_mult/atr_period, regime_lookback/regime_vol_threshold/
      regime_sl_mult_*/regime_tp_mult_*, time_stop_mode/soft_time_stop_*),
      alle Defaults reproduzieren exakt das alte feste %-Verhalten (Golden-
      Test vor dem Umbau aufgenommen, 0 Drift). Gemeinsamer Exit-Resolver
      backtesting.engine._run_exit_loop ersetzt zwei unabhängig kopierte
      Implementierungen (engine._simulate UND paper_forward._resolve hatten
      dieselbe TP1/TP2/SL/Time-Logik dupliziert – ein echtes Bug-Risiko, da
      neue Exit-Logik sonst nur in einem Pfad gewirkt hätte). EXIT_PARAM_SPACE
      (sl_mode/atr_mult/time_stop_mode) in alle 4 Familien gemischt; die
      übrigen neuen Felder bewusst NICHT gesucht (Anti-Data-Dredging, inkl.
      der regime_*_mult-Felder – dazu unten mehr). WalkForwardReport/
      WindowEval bekamen avg_test_max_drawdown/worst_test_max_drawdown (war
      vorher berechnet, aber verworfen). 22 neue Tests (test_engine_exits.py,
      Erweiterungen in test_paper_forward.py/test_strategy_lab.py/
      test_walkforward.py, inkl. Cross-Check engine._simulate ≡
      paper_forward._resolve für alle Exit-Stil-Kombinationen), Suite 494
      grün.
      EHRLICHER BEFUND (Walk-Forward baseline_swing, 10-Ticker-Watchlist,
      12J/4×2J-Fenster, survivorship-verzerrt): MaxDD hat sich in diesem
      kleinen Lauf NICHT verbessert (Ø −21,1%→−22,6%, Worst −24,6%→−31,0%),
      Return leicht schlechter (Ø +48%→+38%). Modale Wahl über die Fenster:
      2× sl_mode=fixed+soft, 1× fixed+hard, 1× regime+hard — atr_trail wurde
      NIE gewählt. Zwei Gründe, sauber getrennt von der Code-Qualität: (1)
      Walk-Forward wählt Trainings-Parameter nach total_return, nicht
      MaxDD-adjustiert — ein Exit-Stil, der Drawdown senkt, wird nur
      gewählt, wenn er ZUGLEICH den Trainings-Return schlägt; (2) die
      regime_sl_mult_*/regime_tp_mult_*-Multiplikatoren bleiben in diesem
      Lauf alle auf neutralem 1.0 (bewusst nicht gesucht) → sl_mode=regime
      unterscheidet sich in der Praxis kaum von fixed. Die ATR-Trailing-
      Mechanik selbst ist isoliert unit-getestet korrekt (schützt in einem
      Rallye-dann-Crash-Szenario deutlich mehr Gewinn als der feste %-SL) —
      sie hat auf diesem kleinen, überlebensverzerrten Sample nur nicht den
      Trainings-Return-Wettbewerb gewonnen. Folge-Idee (nicht Teil von 2.1):
      risiko-adjustierte Trainings-Selektion (z.B. Sharpe/Calmar statt
      total_return) wäre nötig, damit der Walk-Forward drawdown-schonende
      Exits überhaupt bevorzugen KANN.
- [x] **2.2 Portfolio-Level-Backtest** — fertig 12.7. Neues Modul
      strategy_lab/portfolio_backtest.py: verschmilzt die (unveränderten)
      Trade-Listen aller (Strategie, Ticker)-Kombinationen eines weight_plan()-
      förmigen Plans zu Open/Close-Events, simuliert event-getrieben EIN
      Portfolio mit gemeinsamem Cash-Pool statt der bisherigen Per-Ticker-
      Durchschnittsbildung (backtesting.metrics.aggregate()). Cash-Constraint
      (Dollar-Sizing = plan_weight × aktuelle Equity × Vol-Multiplikator,
      gecappt auf verfügbares Cash), Max-Positionen, Themen-Kappung als
      Korrelations-Proxy (analyzers/stock_relations.py::StockRelations.
      get_themes() — bewusst NICHT der live-netzwerk-abhängige, kaputte
      CorrelationChecker), Vol-Targeting (spiegelt swing_strategy.py's
      _atr_vol_multiplier-Formel/-Clamps, kausal aus backtesting.engine._atr()
      am Entry-Balken). Gleicher Tag: Close vor Open (Kapital wird frei, bevor
      es ein neuer Entry beansprucht). Sharpe jetzt echt datumsbasiert
      (Tages-Resampling der Dollar-Equity-Kurve), nicht mehr trade-count-
      basiert wie TickerMetrics.sharpe. Kein Engine-/Trade-Umbau nötig — reine
      Downstream-Simulation über bestehende strategy.runner()-Ergebnisse.
      13 neue Tests (Cash/Max-Pos/Themen-Kappung/Vol-Targeting/Equity-Kurve-
      Golden/Plan-Vergleich), Suite 507 grün. Neues CLI scripts/
      portfolio_backtest.py vergleicht weight_plan() (Allokator) gegen
      Gleichgewichtung.
      REALER LAUF (12J, 10-Ticker-Watchlist): Registry hat aktuell 0 ACTIVE
      Strategien (baseline_swing WATCH/FRAGILE, s. [[exit-lab-befund]]) →
      weight_plan() ist leer, Allokator-Zeile zeigt ehrlich Flat/0 Trades statt
      eines erfundenen Vergleichs. Gleichgewichtung über alle 4 Familien:
      +829 % Return, MaxDD NUR −14,5 %, Sharpe 1,45 — deutlich besser als die
      per-Ticker-gemittelten MaxDD-Werte aus dem Walk-Forward (−21…−31 %,
      [[exit-lab-befund]]), weil Diversifikation über mehrere gleichzeitig
      offene, unkorrelierte Positionen den gemittelten Einzel-Ticker-Drawdown
      tatsächlich abfedert — genau der Effekt, den die reine Per-Ticker-
      Mittelung bisher unsichtbar gemacht hat. 962 von 1306 Signalen wurden
      NICHT genommen (519 Cash, 395 Max-Positionen, 48 Themen-Kappung) — zeigt,
      wie stark reales Kapital das unbeschränkte Signal-Universum tatsächlich
      einschränkt. Die Allokator-Frage selbst ("hilft weight_plan gegenüber
      Gleichgewichtung?") bleibt unbeantwortet, bis eine Strategie ACTIVE wird.
- [x] **2.3 Stress-Test-Harness** — fertig 12.7. 2008/2020/2022 durch die
      Entscheidungs-Pipeline; gestuftes De-Risking statt binärem
      CircuitBreaker.
      Teil A (dad8fec): strategy_lab/stress_test.py — benannte Krisenfenster
      (GFC_2008, COVID_2020, BEAR_2022) via Loader-Wrapper auf die bestehende
      run_portfolio_backtest() (2.2) zugeschnitten, kein neuer Simulations-
      code. Ehrlicher Survivorship-Hinweis: yfinance purgt 2008-Pleiten
      (Lehman, WaMu, Bear Stearns) — ein GFC-Lauf über das heutige Universum
      läuft nur auf Überlebenden. 6 neue Tests, Suite 513 grün.
      Teil B: gestuftes De-Risking in strategy_lab/portfolio_backtest.py
      (DeriskConfig, standardmäßig AUS/opt-in) — Größen-Multiplikator für
      NEUE Entries stufenweise nach laufendem Drawdown vom Portfolio-Equity-
      Peak (< 5 % voll, 5–10 % halb, 10–15 % viertel, ≥ 15 % kein Entry mehr
      = spiegelt den bestehenden binären CircuitBreaker als harte Unter-
      grenze statt ihn zu ersetzen). Bewusst nur neue Käufe gebremst, offene
      Positionen unangetastet (kein aktives Exposure-Abbauen) — einfachster
      Fail-Safe-Fall, keine Rücknahme-Logik nötig, da der Multiplikator bei
      jedem neuen Entry-Event live aus dem aktuellen Drawdown neu berechnet
      wird. 7 neue Tests, Suite 520 grün.
      REALER VERGLEICH 12.7. (scripts/stress_test.py, neues CLI: aus/binär/
      gestuft je Krisenfenster über die 10-Ticker-Watchlist, Gleichgewichtung
      da Registry 0 ACTIVE): GFC_2008 (nur Überlebende, s.o.) — aus MaxDD
      −34,9 %/Return −25,0 %; binär (1 Tier bei 15 %, spiegelt den Live-
      CircuitBreaker) MaxDD −19,1 %/Return −19,1 %; gestuft MaxDD −15,7 %/
      Return −15,7 % (beste MaxDD UND beste Return). BEAR_2022 — aus MaxDD
      −20,6 %/Return −14,0 %; binär MaxDD −16,5 %/Return −11,0 %; gestuft
      MaxDD −13,4 %/Return −10,2 % (wieder beste MaxDD UND beste Return).
      COVID_2020 (Erholung zu schnell/Fenster zu kurz für 5 %-Portfolio-DD)
      — alle drei identisch (Derisk greift nie, 0 Sperren). Damit: gestuft
      schlägt binär, binär schlägt aus, in beiden Krisen mit nennenswertem
      Drawdown. AUFFÄLLIGKEIT: bei binär/gestuft in GFC_2008 ist Return ≈
      MaxDD (Portfolio bleibt bis Fensterende praktisch flach auf dem
      Tiefpunkt liegen, da nach der Sperre kaum noch neue Trades laufen und
      keine Erholung mehr eingefangen wird) — Kehrseite des Schutzes:
      verpasste Erholung, wenn das Fenster kurz nach dem Tief endet.
      Sharpe verschlechtert sich unter Derisking in GFC_2008 (aus −1,47 →
      binär −2,49 → gestuft −3,03) trotz besserem MaxDD/Return — Artefakt
      der Formel (bestraft niedrige Vola bei negativem Mittelwert), NICHT
      als Gegenargument zu werten; MaxDD/Return sind hier die relevanten
      Metriken, nicht Sharpe. Fazit: Stufung hilft in dieser Stichprobe
      (2 von 3 Fenstern mit Wirkung) klar gegenüber binär und aus — aber nur
      2 Krisenfenster mit echtem Ausschlag, keine große Stichprobe.
- [x] **2.4 Quellen-Ablation / Collector-Pruning** — Werkzeug fertig 12.7.
      Neu: scripts/source_ablation.py (+ 10 Tests, tests/test_source_ablation.py).
      Unterschied zu source_health() (1.4e, PRÄSENZ — feuert die Quelle?):
      hier geht es um WIRKUNG — verknüpft analysis_log.sources_breakdown mit
      dem gelabelten Trade-Ausgang aus experience.db (Join über
      (ticker, decided_at==analyzed_at), da experience.db rückwirkend aus
      analysis_log gelabelt wird) und vergleicht je Quelle Ø-Rendite mit vs.
      ohne Treffer (Zweigruppen-Bootstrap-CI auf die Differenz, analog
      track_record.py). Kodiertes Mindest-n je Gruppe (Default 10) als
      Ehrlichkeits-Gate statt erfundener Aussagen bei zu wenig Daten.
      REALER LAUF 12.7.: nur 29 gelabelte Entscheidungen haben überhaupt
      einen Quellen-Breakdown (von 347 in experience.db, größtenteils
      backfill_hypo/kontrafaktisch) — bestätigt die Roadmap-Annahme
      "braucht Datenhistorie". 16 von 31 vorkommenden Quellen haben in
      dieser Stichprobe GAR KEINE Varianz (11 feuern nie: aaii_sentiment,
      earn_transcripts, econ_calendar, eu_regulation, job_listings, patents,
      quiver, reddit, short_volume, twitter, web_traffic; 5 feuern immer:
      chinese_media, crypto_news, german_media, short_interest, yahoo) —
      strukturell nicht ablierbar. Cross-Check gegen source_health() (1.4e,
      109 Analysen mit Breakdown, PRÄSENZ-Sicht): 9 von 10 dort als "tot"
      geführten Quellen sind auch hier ohne Varianz — konsistent. Von den
      restlichen 15 erreichen nur 5 (sec_8k, analyst_ratings, wire, newsapi,
      estimate_revisions) das Mindest-n — und bei ALLEN spannt das 95%-CI
      der Diff die Null (keine Quelle statistisch von 0 unterscheidbar,
      P(≤0) 32–84 %). Ehrliches Verdikt: bei aktueller Datenmenge NICHT
      genug Evidenz für ODER gegen irgendeine Quelle — Abschaltungen wären
      hier reine Vermutung. Werkzeug ist einsatzbereit, braucht aber echte
      Live-Historie (Bot pausiert, kaum 'live'-gelabelte Entscheidungen) für
      ein belastbares Verdikt.
- [x] **2.5 Ziel-Nachführung aus Neuanalysen (TP-Update)** — Backtest-Seite
      FERTIG 12.7. (32d6fa4): wie in der Leitplanke gefordert ZUERST im Exit-Lab
      (2.1) walk-forward gegengetestet. backtesting/engine tp_mode="reanchor"
      (mechanischer Stand-in: TP2 periodisch auf dieselbe %-Formel vom heutigen
      Kurs re-verankert; min_dev-Sperre, Anhebung großzügig, Absenkung gedämpft +
      nie unter Kurs/SL). Default "fixed" = No-Op; 4. Achse in EXIT_PARAM_SPACE.
      7 Tests, Suite 534 grün. A/B (46 Ticker, 4659 Trades): reanchor ändert nur
      3,6 % der Trades, Ø-Kante +1,88 %→+1,90 %, MaxDD minimal schlechter — nahezu
      neutral, keine belegte Kante. Live-Wiring bewusst NICHT gebaut (Backtest
      rechtfertigt die Komplexität nicht).

## Block 3 — Alpha-These prüfen (braucht laufenden Bot + Zeit)

- [~] **3.1 Sentiment-Forward-Study** — Werkzeug fertig + Erstlauf 14.7.:
      analyzers/sentiment_forward_study.py (+ CLI scripts/sentiment_forward_
      study.py). Feste, an buy_threshold orientierte Score-Buckets (kein
      datenabhängiges Quantil — Anti-Data-Dredging wie beim w52_high-Fenster,
      5.1); Edge je Bucket per Bootstrap-CI (reuse
      scripts.track_record._bootstrap_mean_ci statt Kopie); Information
      Coefficient (Spearman sentiment_score↔pnl_pct, Paar-Bootstrap-CI,
      eigene vektorisierte Rang-Funktion statt scipy-Abhängigkeit — dieselbe
      Begründung wie in track_record.py). label_source getrennt ausgewiesen
      (backfill_hypo/backfill/live) — nicht dieselbe Evidenzqualität, exakt
      wie track_record.py es bereits für seine Meilensteine hält. 21 Tests
      (test_sentiment_forward_study.py, u.a. Kunst-Daten mit erzwungener
      perfekter bzw. Null-Korrelation als Positiv-/Negativkontrolle), netzfrei.
      EHRLICHER ERSTLAUF (347 gelabelte Entscheidungen, 291 backfill_hypo +
      56 backfill, **0 live**): Information Coefficient −0,018, 90%-CI
      [−0,123, +0,091] (spannt die Null — kein belegtes Signal). Auffällig:
      der 0,65–0,80-Bucket zeigt sogar eine LEICHT NEGATIVE Kante
      (−1,37 %, P(≤0)=99 %) statt der erwarteten positiven Beziehung —
      deckt sich mit dem bereits bekannten Kalibrierungs-Befund (1.2: AUC
      0,61 aber überkonfident, BSS −0,02) und der Beobachtung "Sentiment
      nicht monoton kalibriert" aus dem Selbstlern-Fundament. Ehrliches
      Verdikt: bei aktueller Datenlage KEINE Evidenz für sentiment_score als
      eigenständige Advisory-Quelle — Verdrahtung in live_bridge bewusst
      NICHT gebaut (würde eine nicht belegte Kante verdrahten). Bleibt [~]
      offen, bis echte Live-Trades (label_source='live') eine belastbare
      Neubewertung erlauben (Block-3-Voraussetzung: laufender Bot + Zeit).
- [ ] **3.2 Skip-Kontrafaktik** — decision_log-SKIPs mit simulate_outcome
      nachrechnen → EntryFilter-Schwellen mit Gegenproben validieren.

## Block 4 — Meta-Ebene & Robustheit

- [x] **4.1 Meta-Backtest des Allokators** — weight_plan + Regime-Faktor
      selbst validieren. CODE FERTIG 12.7. (5990c3e): komplette
      Selektions-Pipeline (Walk-Forward je Familie → build_registry →
      weight_plan) an rollierenden Stichtagen nur mit Vergangenheitsdaten
      nachgestellt, drei Arme OOS durch den Portfolio-Backtest (Allokator /
      Allokator+Regime / Gleichgewichtung), Paired-Differenzen mit
      Bootstrap-90%-CI; CLI scripts/meta_backtest.py, 10 Tests, Suite 548
      grün.
      ✅ ERGEBNIS-LAUF 12.7. Abend (10-Ticker-Watchlist, 20J, 6 rollierende
      Meta-Fenster à 8J-Selektion/2J-OOS, --workers 3, ~50 Min Laufzeit,
      parallel zur Code-Arbeit im Hintergrund): EINDEUTIGER, ETWAS
      ERNÜCHTERNDER BEFUND — Allokator Ø +0,25% (5/6 Fenster FLAT, da
      Registry meist 0 ACTIVE), Gleichgewichtung Ø +61,66%. Paired-Diff
      Allokator−Gleichgewichtung: −61,42%, Bootstrap-90%-CI
      [−79,18%,−40,71%], P(≤0)=100% — der Allokator ist auf dieser
      Stichprobe SIGNIFIKANT SCHLECHTER als simples Gleichgewichten, nicht
      nur „nicht besser". Regime-Faktor vs. Allokator: exakt ±0,00%
      (nur 1 von 6 Fenstern hatte überhaupt eine aktive Strategie, dort
      griff der Regime-Faktor gar nicht). Ursache: das 6.4-ROBUST-Gate ist
      auf der kleinen 10-Ticker-Stichprobe zu streng — die Registry bleibt
      fast immer leer, während der Markt in 5 von 6 Fenstern deutlich
      steigt; „lieber gar nicht handeln" kostet hier real Rendite.
      Beantwortet die seit 2.2 offene Allokator-Frage EINDEUTIG: NEIN, der
      robustheits-gefilterte Allokator hilft (bislang, auf dieser
      Stichprobe) nicht gegenüber Gleichgewichtung — im Gegenteil. Log:
      reports/meta_backtest_2026-07-12.log.
      ✅ USER-ENTSCHEIDUNG 12.7. Abend: Gate bleibt STRIKT (ACTIVE-only,
      kein WATCH-Teilgewicht, keine gesenkte Signifikanz-Schwelle). Die
      Leere der Registry gilt als ehrliches "wir wissen es nicht", nicht
      als Fehler des Gates — der Fix für die −61%-Unterperformance ist
      NICHT ein laxeres Gate (das würde genau das Anti-Overfit-Protokoll
      unterlaufen, das 6.4 aufgebaut hat), sondern mehr echte Evidenz
      (größeres Universum 5.2, mehr Historie 6.2, echte Live-Trades). Damit
      bleibt 4.1 als Design-Frage GEKLÄRT — die Konsequenz ist kein
      Code-Fix, sondern Geduld + Datenausbau.
- [x] **4.2 Bootstrap/Monte-Carlo in der Promotion** — FERTIG 12.7. (4d28454).
      _bootstrap_ci() (numpy, fester Seed) zieht ein 90%-CI über die wenigen
      OOS-Fenster; Verdikt konfidenzbewusst: ROBUST verlangt zusätzlich
      CI-Untergrenze > 0 → verrauschte, die-Null-berührende Strategien fallen
      auf FRAGILE. Report um test_return_ci_lo/hi/p_le0 + max_drawdown_ci
      ergänzt, CLI zeigt OOS-CI-Spalte. 4 Tests, Suite 538 grün.
- [x] **4.3 Regime-Übergangsmodell** — GEBAUT 12.7.: strategy_lab/regime.py
      um eine rollierende Tages-Regime-Zeitreihe erweitert (track_regime(),
      im Vorwärts-Schritt-Stil von paper_forward.replay() — Truncating-
      Loader über einen Tagesbereich, Punkt-in-Zeit, kein Look-Ahead) +
      Hysterese/Debounce (apply_hysteresis(): ein neues Label muss
      min_confirm-mal in Folge auftreten, bevor es übernommen wird) +
      Churn-Messung (count_transitions()). CLI scripts/regime_track.py.
      17 neue Tests (u.a. Look-Ahead-Freiheit per Zukunfts-Manipulation
      verifiziert, Trailing-Fenster-Grenzfall Bär→Bulle-Wende, Hysterese
      erhöht Übergänge nie). REALER LAUF (7 Mega-Caps, 15J, wöchentliche
      Kadenz): Hysterese senkt das Schwellen-Flackern deutlich — 14
      Übergänge/Jahr roh → 5/Jahr bei min_confirm=5 (−64%). Bewusst NICHT
      live verdrahtet (weder analyzers/recession_detector.py noch die
      regime_*_mult-Exit-Multiplikatoren aus 2.1) — nur gebaut + gemessen,
      dieselbe Zurückhaltung wie 2.5 (reanchor): Wiring erst, wenn ein
      Effekt auf echte Entscheidungen belegt ist, nicht nur auf die
      Signal-Stabilität selbst.
- [~] **4.4 Code-Gesundheit + lokale CI** — (b) lokale CI GEBAUT 12.7.:
      scripts/git-hooks/pre-commit (Testsuite vor jedem Commit, bricht bei
      Rot ab) + scripts/install_git_hooks.sh (verlinkt nach .git/hooks/,
      das Verzeichnis ist nie Git-getrackt); installiert und live mit dem
      eigenen Commit verifiziert (583 Tests, 83s). Notausgang bewusst nur
      manuell (`--no-verify`), nicht automatisiert.
      (a) 500-Zeilen-Regel (CLAUDE.md), Modul-Split — Stand 13.7.:
      dashboard/app.py FERTIG (3414→516 Zeilen, 11 Tab-Module + ticker_names.py,
      Golden-Master-AppTest-Diffing).
      bot/runner.py FERTIG (1745→889 Zeilen, −49 %): 5 Extraktionen —
      cycle_close.py (Zyklus-Abschluss), cycle_checks.py (Pre-Analyse-
      Marktkontext), cycle_exits.py (SL/TP + TradingView-SELL),
      cycle_prefetch.py (paralleles News/Preis-Vorladen +
      Analyse-Vorberechnung), cycle_analysis.py (die serielle Analyse-
      Kern-Schleife selbst — größter/riskantester Schnitt, erst nach
      6 Charakterisierungstests für zuvor ungepinnte Zweige: RL-Veto,
      BUY-related_tickers, SKIP-Conditional-Entry, Earnings-Strategy,
      TradingView-SELL, Multi-Agent-Konsens). Funktionskörper bei der
      Kern-Schleife wortwörtlich verschoben (diff-verifiziert) statt von
      Hand transkribiert. cycle_analysis.py selbst (661 Zeilen) bleibt
      ehrlich über der 500-Zeilen-Regel — weitere Aufteilung wäre ein
      eigener Folge-Task. Suite durchgehend grün (725→740).
      bot/scheduler.py FERTIG (2419→1008 Zeilen, −58 %): 6 Nähte, alle
      37 Job-Closures aus run_bot_loop ausgelagert — scheduler_maintenance.py
      (3 unabhängige Jobs), scheduler_risk.py (5 Jobs, davon 2 gekoppelt
      über Dependency-Injection statt fester Verdrahtung),
      scheduler_macro.py (7 Jobs), scheduler_scanners.py + 
      scheduler_scanners2.py (komplette Scanner-Gruppe, 11 Jobs +
      geteilter escalate_ticker-Helfer), scheduler_analysis.py (die
      komplexeste Gruppe — Jobs rufen sich GEGENSEITIG auf, gelöst durch
      Durchreichen der eigenen gleichnamigen Wrapper-Closures als
      Parameter statt Neu-Referenzierung, damit `schedule`s Namens-
      Introspektion und der Pre-Market-vor-Analyse-Reihenfolgevertrag
      erhalten bleiben). Alle Körper diff-verifiziert wortwörtlich
      übernommen, kein Verhalten geändert. Verbleibend in scheduler.py:
      3 State-schwere Einzeljobs (Circuit-Breaker-Monitor, Resource-Check,
      Regime-Check) + reiner Setup-Code — bewusst nicht mehr angefasst,
      kein akuter Bedarf. Suite durchgehend grün (740→822).
- [x] **4.5 Entscheidungs-Replay** — FERTIG 14.7. `analyzers/decision_replay.py`.
      Bewusst NICHT der Claude-Aufruf selbst (nicht deterministisch, würde
      erneut kosten), sondern der archivierte ECHTE Antworttext (1.4d) durch
      die AKTUELLE Parsing-/Schwellen-Logik (ClaudeAnalyzer._parse_response/
      _enforce_buy_floor) gejagt und mit der damals tatsächlich geloggten
      Empfehlung verglichen — beantwortet "würde der heutige Code aus
      derselben KI-Antwort dieselbe Entscheidung ableiten?", z.B. um eine
      buy_threshold- oder Buy-Boden-Änderung gegen echte historische
      KI-Antworten zu prüfen, ohne die API erneut zu bezahlen. Zwei archivierte
      Antwort-Schemata (Standard-Analyse vs. These-Check offener Positionen)
      werden am charakteristischen Prompt-Text erkannt statt am Modellnamen
      (robuster). `replay_analysis(analysis_id)` für einen Einzelfall,
      `replay_recent(limit)` für einen Batch-Drift-Report; beide injizierbar
      (Tests/Batch-Aufrufer teilen sich eine DB-Verbindung). CLI
      `scripts/decision_replay.py --analysis-id/--recent`. Dashboard-Tab
      "Entscheidungen" zeigt bei verketteter Analyse jetzt live, ob ein Replay
      mit aktuellem Code abweicht (Warnung) oder identisch bleibt (Caption) —
      headless per AppTest gegen isolierte Temp-DBs verifiziert (Drift-Fall
      + identischer Fall, keine Exceptions). `PromptArchive.recent()` neu
      ergänzt als Basis fürs Batch-Replay. 16 neue Tests
      (test_decision_replay.py), Suite 882 grün. Bewusst nicht angegangen:
      Replay der VOLLEN Strategie-Entscheidung (swing_strategy.evaluate())
      bräuchte zusätzlich den live-Marktpreis/Portfolio-Zustand zum
      Entscheidungszeitpunkt, der nicht archiviert ist — Scope bleibt ehrlich
      auf der KI-Analyse-Schicht, die vollständig archiviert und damit
      wirklich deterministisch reproduzierbar ist.

## Block 5 — Breite (erst nach 2–4)

- [~] **5.1 Mehr Familien** — 12.7. (parallel zum 4.1-Nachtlauf): 2 neue
      Familien in strategy_lab/families.py, reine Preis-Signale (kein
      neuer Datenbedarf): **w52_high** (Nähe zum 52-Wochen-Hoch,
      George/Hwang-Effekt; Fenster bewusst FEST 252 Tage, kein Suchparameter
      — das WÄRE Data-Dredging, ist die Definition des Effekts) und
      **gap_meanrev** (starker Overnight-Gap-down im Aufwärtstrend, anders
      als rsi_meanrev kein Flanken-Trigger — jeder Gap-Tag ein eigenes
      Ereignis). 20 neue/erweiterte Tests (test_families_new.py +
      Familien-Listen in test_strategy_lab.py ergänzt), CLI-Smoke-Test
      gegen echte Cache-Daten (ehrlich OVERFIT/FRAGILE auf kleiner
      Stichprobe, kein Overselling). PEAD BEWUSST NICHT gebaut:
      pead_tracker.py ist ein Live-Event-Tracker ohne punkt-in-Zeit-
      EPS-Überraschungs-Historie über 20 Jahre — nicht ehrlich backtestbar.
      Saisonalität BEWUSST zurückgestellt: Kalendereffekte laden zum
      Data-Dredging geradezu ein (großer, verlockender Parameterraum).
- [x] **5.2 Universum erweitern** — Generalitäts-Check 12.7. (parallel zum
      4.1-Nachtlauf): backtesting/data_loader.load() ist bereits
      universum-agnostisch (reicht den Ticker-String unverändert an
      yfinance durch) — SAP.DE/SIE.DE/BAS.DE (EU) und BTC-USD/ETH-USD/
      SOL-USD (Krypto) laden und cachen ohne Codeänderung; Walk-Forward
      über alle Familien läuft auf beiden Universen crashfrei
      (ehrlich: kein ROBUST-Verdikt auf der kleinen Stichprobe, erwartet).
      EINE ECHTE ERKENNTNIS dabei: Krypto handelt 7 Tage/Woche — im
      10-Jahres-Fenster BTC-USD 3680 Bars vs. SAP.DE 2561 Bars (Faktor
      ~1,44×). SMA200/RSI14 &c. sind Bar-Zählungen, keine
      Kalenderzeiträume — dieselbe Parameterzahl deckt bei Krypto einen
      KÜRZEREN Kalenderzeitraum ab als bei Aktien. Kein Bug, aber beim
      Parameter-Vergleich zwischen Aktien- und Krypto-Läufen zu beachten.
      Befund als Regression verankert: 6 neue Tests
      (test_each_family_runs_on_dense_7day_calendar, freq='D' statt 'B'
      synthetisch) verhindern künftig, dass Family-Code je einen
      Business-Day-Kalender voraussetzt.
- [ ] **5.3 Slippage-Kalibrierung** aus echten IBKR-Paper-Fills.

## Block 6 — Server-Umzug + Intensiv-Lab (GPU-Server, geplant ~Ende Juli/Aug. 2026)

> User-Plan 12.7.: In einigen Wochen Umzug auf eigenen Server (bessere CPU,
> GPU, mehr RAM). Ziel: das Walk-Forward-/Meta-Backtest-Verfahren deutlich
> genauer und intensiver fahren.
>
> **Ehrliche Leitplanke zuerst**: Das Nadelöhr ist DATEN, nicht Rechenleistung.
> Aktuell: ~10er-Watchlist bzw. 42-Ticker-Cache, survivorship-verzerrte
> yfinance-Kurse, 78 gelabelte echte Trades. Mehr Compute auf denselben Daten
> macht Ergebnisse nur PRÄZISER FALSCH — und ein 100× größerer Suchraum findet
> GARANTIERT Scheinkanten, wenn das Anti-Overfit-Protokoll nicht vorher steht.
> Deshalb Reihenfolge: Daten → Protokoll → Compute. GPU-Punkt mit dem größten
> sicheren Nutzen ist NICHT der Backtest, sondern Ollama (6.5a).
>
> **🔒 FREIGABE-REGEL (User-Anweisung 12.7.):** Alle Features in diesem Block,
> die auf der besseren Hardware aufbauen, werden nur VORBEREITET — Code, Tests,
> Nähte, Flags (Default AUS, exakt altes Verhalten). AKTIVIERT wird nichts
> davon eigenmächtig: erst wenn der User ausdrücklich mitteilt, dass das
> Programm auf dem neuen Server läuft, werden die vorbereiteten Features
> freigegeben. Analog zur Bot-Pausierung: Vorbereitung ja, Scharfschalten nur
> auf Anweisung.

- [ ] **6.1 Umzugs-Fundament** (Großteil existiert schon): restore.sh +
      docs/SERVER_RUNBOOK.md sind erprobt (0.5); Push-Frage 0.2 wird damit
      PFLICHT (Code muss versioniert auf den neuen Server, nicht per scp);
      Demo-Daten-Swap-Rücktausch VOR dem Umzug klären (sonst zieht die
      Demo-data/ mit um); .env-Secrets-Transfer manuell (nie ins Repo);
      IB-Gateway + autologin auf neuem Server neu aufsetzen.
      ERGÄNZT 12.7.: (a) SICHERHEITS-CHECKLISTE als fester Umzugsschritt
      (Lehre aus 0.4: Dashboard stand offen im Netz, Settings-Tab konnte
      .env lesen+schreiben): ufw default-deny, Dashboard nur 127.0.0.1 +
      SSH-Tunnel, kein Dienst auf 0.0.0.0 ohne Grund, ss-Audit nach Setup.
      (b) SPEICHER/BACKUP-DIMENSIONIERUNG: PIT-Archiv (6.8b) + EDGAR-Rohdaten
      (6.8a) wachsen auf zig–hunderte GB; Backup-Strategie muss das UND die
      Lern-DBs off-server abdecken (heute: Backup nur lokal, Timer nicht
      enabled — 0.1-Rest wird mit dem Umzug PFLICHT statt optional).
- [ ] **6.2 Daten-Ausbau** (Voraussetzung, dass mehr Compute überhaupt lohnt).
      Das Nadelöhr konkret: (1) nur 10/42 Ticker = zu wenige unabhängige
      Stichproben, (2) Survivorship-Bias — yfinance kennt nur heutige
      Überlebende, alle Delistings/Pleiten fehlen → Backtests systematisch
      geschönt, (3) nur 78 gelabelte echte Trades, (4) keine historischen
      News-/Sentiment-Daten (halber Bot nicht backtestbar). Maßnahmen nach
      Aufwand:
      (a) GRATIS/SOFORT gegen (1): Universum auf mehrere hundert Ticker,
          Parquet-Cache vorab befüllen; EU-Universum durchs selbe Lab
          (zieht 5.2 vor).
      (b) ✅ GRATIS/TEILFIX gegen (2): HISTORISCHE Index-Zusammensetzungen
          statt heutiger Liste — GEBAUT 12.7. (Vision V0.3):
          scripts/sp500_membership_download.py lädt github.com/fja05680/sp500
          (ab 1996, CSV je Änderungsdatum) → data/sp500_membership.csv;
          strategy_lab.universe.constituents_at(datum) liefert die Punkt-
          in-Zeit-Liste (kein Look-Ahead); CLI-Flag --pit-universe in
          scripts/walk_forward.py filtert JEDES Teilfenster auf die
          DAMALIGE Mitgliedschaft. Sanity-Check gegen echte Daten bestätigt
          den Fix: TSLA fehlt 2010 (trat erst Ende 2020 bei), NVDA war
          schon dabei — ohne PIT hätte 2010 heimlich mit der heutigen
          Liste gerechnet. 11 Tests (test_pit_universe.py, netzfrei),
          Suite 578 grün. Bias wird kleiner, nicht null (Kurse delisteter
          Werte fehlen bei yfinance trotzdem — Rest bleibt (c)).
      (c) USER-ENTSCHEID (einziger echter Fix für (2)): Point-in-Time-Daten
          inkl. Delistings. RECHERCHIERT 12.7. (docs/DEEP_RESEARCH_2026-07.md):
          EODHD All-World ~20 €/Mon. — Delistings in JEDEM Paket (11.000+
          delistete US-Ticker ab ~2000) = pragmatischster Einstieg;
          Norgate Platinum ~630 $/JAHR (nicht 30–40 $/Mon. wie zunächst
          angenommen) = sauberste Lösung inkl. fertiger historischer
          Index-Mitgliedschaft; Sharadar erst ab ~2014 → zu kurz.
          Wird mit mehr Compute WICHTIGER, nicht optionaler —
          Survivorship-Bias wächst mit dem Suchraum mit.
      (d) ZEIT STATT GELD gegen (3): Bot-Paper-Betrieb über Monate (Block
          3/6.6) — kein Hardware-Ersatz möglich.
      (e) AB-JETZT-ARCHIVIEREN gegen (4): alles selbst wegschreiben
          (Prompt-Archiv 1.4d zahlt darauf ein) → in einem Jahr rückwirkend
          testbar; historische News-Archive kaufen ist institutionell teuer
          (RavenPack & Co.) → verworfen.
- [x] **6.3 Parallel-Walk-Forward** — FERTIG 12.7. (4cce16b), vorgezogen auf
      dem alten Server. ProcessPoolExecutor über die Grid-Search-Kombos je
      Fenster (unabhängig), deterministisch BIT-IDENTISCH zur seriellen
      Schleife (pool.map erhält Reihenfolge, Auswahl per striktem >).
      run_walk_forward(workers=…) + run_meta_backtest(workers=…), CLI-Flag
      --workers (walk_forward + meta_backtest), ENV STRATEGY_LAB_WORKERS;
      Default 1 = seriell (exakt altes Verhalten), 0 = Kerne−1. Fail-open:
      jeder Pool-Fehler degradiert auf seriell. Voraussetzung gelöst:
      Strategy-Objekte picklebar (families._fires_today-Closure →
      _FiresToday-Klasse). Benchmark echte Cache-Daten: 3,0× mit 4 Workern
      (52,6s → 17,3s), identisches Ergebnis. 5 Tests, Suite 552 grün.
      Auf dem 6-Kern-Server ~4–5× drin, auf dem neuen Server ~Kernzahl.
- [x] **6.4 Anti-Overfit-Protokoll für große Suchräume** (PFLICHT vor
      "intensiver") — FERTIG 12.7. (9372f14 + CPCV-Ergänzung):
      (a) Multiple-Testing-Korrektur AKTIV: strategy_lab/anti_overfit.py,
          Šidák-korrigierte Signifikanzschwelle — n getesteter Kombos fließt
          ins Promotion-Verdikt ein (ROBUST verlangt p_le0 ≤ šidák(n); n=1 ≙
          altes 4.2-Verhalten, 60 Kombos → p ≤ 0.00086, 10.000 → p ≤ 5e-6).
          Bewusst Šidák statt echtem Deflated Sharpe: DSR braucht
          Skew/Kurtosis-Schätzungen, die auf 3–8 OOS-Fenstern nicht tragen.
          Report zeigt n_combos_tested + alpha_adjusted.
      (b) Holdout AKTIV: run_walk_forward(holdout_years=…) spart den
          jüngsten Schwanz KOMPLETT von der Suche aus (CLI --holdout);
          run_holdout() bewertet feste (modale) Parameter darauf und
          protokolliert JEDEN Zugriff (data/holdout_access.json) —
          Disziplin-Ziel ≤1×/Quartal bleibt Prozess, kein Schloss.
          7 Tests, Suite 559 grün, CLI-E2E verifiziert.
      (c) ✅ CPCV GEBAUT 12.7. (Vision, Roadmap-Abarbeitung): strategy_lab/
          cpcv.py + CLI scripts/cpcv.py. Zweite Validierungs-Achse: statt
          nur EINER vorwärtslaufenden Fenster-Abfolge (Walk-Forward) prüft
          CPCV viele verschiedene Kombinationen, welcher Zeitblock als Test
          dient (C(n_blocks,test_blocks), gedeckelt via --max-paths), mit
          Purging (Trainingsende vor einem Testblock um purge_days
          verkürzt — ein Signal dort könnte sonst einen Trade eröffnen, der
          in den Test hineinläuft) + Embargo (Trainingsstart nach einem
          Testblock um embargo_days verzögert, gegen serielle Korrelation).
          Bewusste Vereinfachung ggü. dem Originalpapier (wie Šidák statt
          DSR): keine Pfad-Rekonstruktion, jede Testblock-Kombination zählt
          als eigenes unabhängiges OOS-Ergebnis — statistisch konservativer,
          nicht großzügiger. Nutzt dieselbe Grid-Search-/Aggregations-/
          6.3-Worker-Maschinerie wie walkforward.py (_aggregate_report
          wiederverwendet → identische Šidák/Bootstrap-Gates auf beiden
          Achsen). 18 Tests (test_cpcv.py, u.a. Purge/Embargo-Grenzfälle:
          mittig/Rand/mehrere/angrenzende Testblöcke, Parallel==Seriell),
          netzfrei; CLI-Smoke-Test gegen echte Cache-Daten verifiziert.
          Holdout-in-6.7-Verdrahtung bleibt Teil der 6.7-Routine (dort, wo
          6.7 selbst noch offen ist).
- [~] **6.5 GPU-Nutzen realistisch einordnen**:
      (a) ✅ GRÖSSTER SICHERER GEWINN bereits VORBEREITET (Fund 12.7. beim
          Sichten, kein neuer Code nötig): system/resource_manager.py::
          _has_inference_gpu() erkennt automatisch Apple Silicon (Darwin),
          eine per nvidia-smi sichtbare NVIDIA-GPU oder OLLAMA_FORCE_GPU und
          schaltet TIER_MODELS dann selbständig von den kleinen CPU-Defaults
          (llama3.2:3b) auf die großen GPU-Defaults (qwen2.5:32b/14b) um —
          ohne Codeänderung beim Umzug, genau im Sinne der Freigabe-Regel
          (vorbereitet, aktiviert sich am Hardware-Signal selbst). War bisher
          UNGETESTET trotz zentraler Rolle; 6 Tests ergänzt
          (test_resource_manager_gpu.py: Darwin/Force-Override/nvidia-smi-
          Erfolg/-Timeout/-Fehlen/Falsy-Override), netzfrei (subprocess
          gemockt). Direkt messbarer Euro-Effekt sobald GPU da ist, null
          Overfit-Risiko — reine Infrastruktur.
      (b) ✅ ML-Meta-Labeling GEBAUT 12.7.: strategy_lab/meta_label.py + CLI
          scripts/meta_label.py. Modell lernt NICHT Kurse vorherzusagen,
          sondern WELCHE der mechanischen Signale (Donchian/RSI/…) im
          Nachhinein Gewinner waren — Features Regime/Trailing-Vola/
          Trailing-Rendite/Breadth zum Signalzeitpunkt (streng vor dem
          Stichtag, kein Look-Ahead), Label = Backtest-Trade-Ausgang (NICHT
          die 78 echten Trades — die bleiben Validierung, 6.8c).
          HistGradientBoostingClassifier (CPU reicht, sklearn bereits
          gepinnt) — bewusst kein NN/GPU, da noch keine Kante gezeigt.
          VALIDIERUNG NACH 6.4-PROTOKOLL: expandierendes Fenster über
          Zeitblöcke (dieselbe make_blocks()-Mechanik wie CPCV) statt
          Zufalls-Split; mehrere P(Win)-Schwellen als kleine Multiple-
          Testing-Situation mit Šidák-Korrektur + Bootstrap-CI (dieselbe
          Maschinerie wie Walk-Forward/CPCV); Holdout-Schwanz ausgespart +
          protokolliert (anti_overfit.log_holdout_access). Feature-
          Importance bewusst NICHT Teil davon — bleibt eigener 6.9(g)-Punkt
          (Permutation-Importance, rechenintensiver). 15 Tests
          (test_meta_label.py, u.a. Look-Ahead-Freiheit per Zukunfts-
          Manipulation verifiziert + Positivkontrolle: ein künstlich
          eingebautes Signal wird auch wirklich erkannt, nicht nur immer
          NO_SIGNAL zurückgegeben), netzfrei. CLI-Smoke-Test gegen echte
          Cache-Daten (5 Ticker/15J) verifiziert — ehrlicher Befund dort:
          NO_SIGNAL bei nur 1 Auswertungs-Block (zu kleine Stichprobe),
          erwartungsgemäß, kein Overselling.
      (c) OFFEN: TimesFM-Experiment (bereits evaluiert: NICHT für Kurse,
          evtl. für Alt-Data-Reihen) — auf GPU-Server günstig nachholbar,
          zero-shot vs. naive Baseline. Braucht die Hardware selbst, bleibt
          Block-6-Hardware-gated.
      (d) BEWUSST NICHT: Deep Learning / RL direkt auf Kursen — 78 gelabelte
          Trades und Random-Walk-Preise; das wäre Overfitting mit Ansage.
- [ ] **6.6 Lern-Loop-Realität**: Echtes Weiterlernen (Kalibrierung,
      Meta-Labeling auf LIVE-Ausgängen) braucht laufenden Bot + Zeit —
      der neue Server beschleunigt die Forschung, ersetzt aber nicht die
      Live-Historie (Block 3 bleibt eigener Engpass).
- [ ] **6.7 Intensiv-Fahrplan (Lab als Dauerbetrieb)** — rechenintensive
      Läufe vom Hand-Anstoß in feste Routine überführen (systemd-Timer
      analog der bestehenden Bot-Timer). Skizze:
      (a) NÄCHTLICH: Walk-Forward über alle Familien (--workers 0) →
          Registry-Refresh; solange der Bot pausiert ist rein advisory,
          data/strategy_registry.json bleibt die einzige Schnittstelle.
      (b) WÖCHENTLICH: Meta-Backtest des Allokators (4.1-Lauf) +
          Paper-Forward-Abgleich (Soll vs. Ist).
      (c) MONATLICH: Quellen-Ablation (2.4) + Stress-Test-Vergleich (2.3);
          QUARTALSWEISE das 6.4-Holdout-Fenster (bewusst selten — Holdout
          nutzt sich durch Anfassen ab).
      (d) OUTPUT: Reports als Dateien (reports/ o.ä.) + kurze
          Telegram-Zusammenfassung (TELEGRAM_MODE=important); ALARM nur
          bei Verdikt-Wechsel (z.B. ROBUST→FRAGILE) oder Lauf-Fehler,
          kein Zahlenspam (Lehre aus dem Watchdog-Spam).
      (e) VORAUSSETZUNG: 6.4-Gates zuerst — ein nächtlicher Suchlauf ohne
          Multiple-Testing-Korrektur automatisiert nur die Selbsttäuschung;
          Läufe versionieren (Config-Hash + Datenstand in den Report).
- [ ] **6.8 Datenlücke mit Compute schließen** — was der GPU-Server GEGEN
      das Daten-Nadelöhr (6.2) tun kann, statt nur schneller zu rechnen.
      Kernidee: Compute erzeugt keine neuen Kurs-Informationen, aber es
      kann FREIE ROH-ARCHIVE in nutzbare Zeitreihen verwandeln, für die
      bisher die Annotations-Kosten prohibitiv waren (Claude-API), mit
      lokalem GPU-LLM aber ~0 € kosten:
      (a) LLM-BACKFILL AUS FREIEN ARCHIVEN (größter Hebel gegen 6.2-(4)):
          SEC EDGAR Volltext (8-K/10-K/10-Q, frei, Jahrzehnte zurück) per
          Ollama massen-annotieren → historische Event-/Katalysator-
          Zeitreihe je Ticker (Earnings-Überraschung, Guidance, FDA, M&A).
          Ergänzend GDELT (News-Events frei ab 2015). Damit wird die
          KI-/Katalysator-Hälfte des Bots erstmals backtestbar — mit
          Punkt-in-Zeit-Disziplin (nur Filing-Datum, kein Lookahead).
          GATE DAVOR (12.7. ergänzt): Annotationsqualität erst BEWEISEN —
          ~200 Filings doppelt labeln (lokales LLM vs. Claude vs. echter
          Kursausgang), Übereinstimmung messen; Massen-Backfill nur wenn
          das lokale Modell trägt, sonst lernen wir Rauschen.
          VORZIEHBAR: der EDGAR-Download ist I/O- nicht CPU-lastig → kann
          schon auf dem ALTEN Server laufen (SEC-Fair-Access beachten:
          max. 10 req/s, User-Agent mit Kontakt-Mail — braucht die offene
          SEC_CONTACT_EMAIL in .env), Rohmaterial liegt dann beim
          GPU-Start bereit.
          ✅ SKRIPT GEBAUT (12.7., Vision V0.2): scripts/edgar_download.py —
          Submissions-API inkl. Archiv-Pagination, 5 req/s-Drossel,
          Manifest-Parquet (data/edgar/), idempotent wiederaufsetzbar,
          Abbruch ohne SEC_CONTACT_EMAIL; netzfreie Tests
          (test_edgar_download.py, Suite 567). OFFEN: .env-Eintrag (User)
          + erster Nachtlauf über die Watchlist.
      (b) EIGENES PIT-ARCHIV VORWÄRTS: Dauer-Collector auf dem neuen Server
          schreibt ab Tag 1 alles versioniert weg (Quotes, News-Snapshots,
          Alt-Data, Prompts) → selbstgebautes Point-in-Time-Archiv, das
          mit jedem Monat wertvoller wird (Verzahnung 6.7d-Reports).
      (c) LABEL-VERVIELFACHUNG gegen 6.2-(3): Meta-Labeling-Trainingsdaten
          nicht nur aus 78 echten Trades, sondern aus zehntausenden
          BACKTEST-Signalausgängen über das große Universum (Features:
          Regime/Vola/Breadth zum Signalzeitpunkt; Label: simulierter
          Ausgang). Echte Trades bleiben Validierung, nicht Training.
      (d) RESAMPLING STATT SYNTHETIK: Block-Bootstrap/Monte-Carlo-Pfade
          machen die VALIDIERUNG härter (Verteilungen statt Punktwerte,
          verzahnt mit 6.4) — ehrlich bleiben: synthetische Kurse enthalten
          keine neue Information, sie finden keine Kante, sie zerstören
          nur Scheinkanten. Genau dafür einsetzen.
      (e) GRENZE KLAR BENENNEN: Kurse delisteter Aktien kann kein Compute
          rekonstruieren — Survivorship-Fix bleibt Kauf-Entscheid 6.2-(c).
- [ ] **6.9 Weitere Compute-Hebel** (gesammelt 12.7., User wählt bei Umzug aus) —
      Aufgaben, die heute zu rechenintensiv sind, auf dem GPU-Server aber
      einmalig oder als Routine laufen können.
      EINMALIG (Backfill):
      (a) WHISPER-TRANSKRIPTION: Earnings-Calls/IR-Webcasts (Audio frei
          zugänglich) auf der GPU transkribieren → Ton-/Sentiment-Zeitreihe
          je Ticker; der tote earn_transcripts-Collector (2.4-Befund) bekäme
          damit erstmals eine echte Quelle.
      (b) EMBEDDING-INDEX über alles Archivierte (analysis_log ~1620
          Einträge, News-Snapshots, 6.8a-Filings): lokales Embedding-Modell
          + Vektorsuche → "ähnliche historische Situationen" als
          Analyse-Kontext (Präzedenzfall-Abruf statt nur aktueller Daten).
      (c) RE-ANALYSE-STUDIE: alle archivierten Analysen per lokalem LLM
          gegen die echten Ausgänge nachbewerten (LLM-as-Judge) →
          systematische Analysefehler finden, Kalibrierung (1.2) schärfen;
          via Claude-API unbezahlbar, lokal ~0 €.
      (d) SKIP-KONTRAFAKTIK XXL: 3.2 hochskaliert — ALLE historischen SKIPs
          + großes Universum durchsimulieren → EntryFilter-Schwellen mit
          echten Gegenproben statt kleiner Stichprobe validieren.
      REGELMÄSSIG (im Zyklus / nächtlich):
      (e) LLM-ENSEMBLE/SELBST-KONSISTENZ: n Samples je Analyse statt 1,
          Streuung der Antworten = ehrliches Unsicherheitsmaß → speist die
          Kalibrierung; lokal ~kostenlos, via API n-facher Preis.
      (f) HEADLINE-MASSEN-TRIAGE: jede Schlagzeile lokal scoren statt
          Keyword-Filter (bei 1,7 tok/s unmöglich) → bessere
          Eskalations-Qualität + weniger Claude-Calls (verzahnt
          Frugal-/Quiet-Mode).
      (g) TEURE STATISTIK ALS STANDARD: purged CPCV, Deflated Sharpe,
          Permutation-Importance der Meta-Labeling-Features — heute
          Sonderläufe, künftig fester Teil jedes 6.7-Nachtlaufs (macht
          6.4 vom Einmal-Protokoll zum Dauer-Gate).
      LEITPLANKE: LLM-Annotationen sind verrauscht und alles daraus
      Gelernte läuft durch die 6.4-Gates; (e)/(f) verbessern KALIBRIERUNG
      und KOSTEN, nicht automatisch die Kante.
- [x] **6.10 Erfolgs-/Abbruchkriterien definieren** — GEBAUT 12.7. Abend.
      ✅ USER-ENTSCHEIDUNG: n_min=150 Live-Trades ODER 24 Monate Zeit-Budget
      (was zuerst eintritt, bewusst am oberen Ende der besprochenen
      18–24-Monats-Spanne). Kodiert in analyzers/thesis_verdict.py: pro
      benannter These (Thesis-Dataclass in data/thesis_registry.json,
      gitignored) ein automatisches Verdikt PENDING/PROVEN/ABANDONED —
      PROVEN nur bei erreichter Stichprobe UND beiden Bootstrap-CI-
      Untergrenzen > 0 (Kante UND schlägt Buy&Hold, reuse
      track_record._bootstrap_mean_ci); ABANDONED entweder bei erreichter
      Stichprobe ohne erfülltes Kriterium ODER bei abgelaufenem Zeit-Budget
      vor erreichter Stichprobe. „NICHT WIEDERBELEBEN" ist hart kodiert:
      ein einmal gefälltes Verdikt wird von evaluate() nie neu berechnet,
      selbst mit überzeugenden neuen Daten (Test dafür). CLI
      scripts/thesis_verdict.py (--register/--evaluate/--list). Erste
      These **mechanical_baseline** registriert (seit 12.7.2026,
      n_min=150, 24 Monate) — CLI-Smoke-Test gegen echte Trade-Historie:
      56/150 Trades, PENDING (56 deckt sich mit dem bekannten 1.1-Befund).
      12 Tests (test_thesis_verdict.py, netzfrei), Suite grün. Ohne das
      hätte das Lab unbegrenzt weiterlaufen können, ohne dass je ein
      Verdikt fällt — jetzt ist "wann geben wir eine These auf" eine
      kodierte Entscheidung, kein Versehen.
- [ ] **6.11 Breite Tages-Analyse + Analyse-Tiefe als A/B** (12.7. ergänzt,
      User-Frage "Schwellen senken und viel mehr Aktien analysieren?").
      KERN-TRENNUNG: Analyse breit, Funnel streng — Entscheidungs-/
      Eskalations-Schwellen werden NICHT gesenkt, eher verschärft (Multiple-
      Testing im Live-Funnel: wer täglich 500 statt 10 Aktien prüft, findet
      garantiert zufällig "starke" — 6.4-Logik gilt auch hier).
      (a) BEOBACHTUNGS-RADAR: täglich hunderte Aktien LOKAL analysieren
          (GPU-Ollama, Grenzkosten ~0). Wert: Score-ZEITREIHE je Aktie
          (Signal-Halbwertszeit, Kalibrierung je Titel/Sektor messbar) +
          jede Analyse+Ausgang = Trainingsbeispiel (löst das 78-Label-
          Problem über Zeit; speist 1.2/6.5b). Gehandelt wird weiter nur,
          was den strengen Funnel übersteht — Radar ≠ Trade-Kandidat.
          Praktische Grenze sind Datenquellen-Limits, nicht Compute:
          braucht Parquet-Cache 6.2(a) + gestaffelte Abrufe.
      (b) ANALYSE-TIEFE ALS A/B-EXPERIMENT: mehrstufige Analyse (Technik/
          News/Fundamental getrennt + expliziter CONTRA-Pass gegen die
          bekannte Überkonfidenz aus 1.2, dann Synthese), mehr Kontext
          (10-K/Transkripte 6.9a, Präzedenzfälle 6.9b), Ensemble 6.9e,
          Ticker-Dossier (Analyse baut auf Vorwissen je Titel auf).
          MESSPFLICHT: dieselben Aktien parallel schlank vs. tief
          analysieren, Kalibrierungs-Monitor (1.2) entscheidet per
          Brier/AUC — tiefe Analyse wird nur Standard, wenn sie messbar
          besser ist, nicht weil sie fundierter KLINGT.

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
- [ ] GPU-Server-Sizing VOR dem Kauf gemeinsam durchgehen (→ Block 6):
      entscheidende Größe ist VRAM, nicht CPU/RAM — bestimmt, welches
      lokale Modell läuft (8B ≈ 8–12 GB, 32B ≈ ~24 GB, 70B ≈ 48 GB+/
      quantisiert) und ob Whisper (6.9a) parallel passt. Dazu grobe
      Strom-vs-API-Kosten-Rechnung (6.5a nicht blind glauben).
- [x] Erfolgs-/Abbruchkriterien je These festlegen (→ 6.10) — 150 Trades /
      24 Monate, 12.7. Abend entschieden & kodiert.

**Vor Live-Relevanz außerdem**: Registry neu generieren (aktuell
Spielzeug-Lauf; schlank fahren: `--total 12 --max-combos 24`, sonst
>18 min CPU).
