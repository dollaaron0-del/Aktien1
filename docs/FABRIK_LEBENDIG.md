# Fabrik Lebendig — die gesammelten Daten fangen an zu spielen

Stand: 16.7.2026 (abends überarbeitet für autonome Abarbeitung).
Ausbaustufe nach `DESIGN_ROADMAP.md` (D0–D8) und `DASHBOARD_HORIZONT.md`
(H1–H7, komplett). Sechs Blöcke L1–L6 — jeder spielt mit Daten, die das
Programm über Monate gesammelt, analysiert und zugeordnet hat.

Ampel: 🟢 günstiges Modell reicht · 🟡 günstig mit EINER markierten
Urteils-Stelle (die Entscheidung ist vorformuliert, nur die Ausführung
braucht einen Blick) · 🔴 NICHT anfassen (siehe Freigaben).

## ⚙️ Arbeitsanweisung (für das abarbeitende Modell — komplett, ohne
## Rückfragen an den User)

**User-Freigaben vom 16.7.2026 (abends, per Auswahl bestätigt — es gibt
KEINE offenen Genehmigungen mehr, NICHT nachfragen):**
1. **Umfang:** alle 🟢- und 🟡-Punkte in der Reihenfolge-Empfehlung
   unten. Alle 🔴-Punkte (L2.4, L3.5, L5.2) sind AUSGESCHLOSSEN —
   überspringen, nicht „nur mal anfangen".
2. **L5.2 (Kamera-Flug/JS)** ist ausdrücklich gesperrt, auch als
   Experiment.
3. **Blocker-Regel:** lässt sich ein Punkt nicht sauber fertigstellen
   (unerwartete Datenlage, Test nicht grün zu kriegen, Annahme im
   Dokument falsch) → Punkt im Dokument als `⏸ BLOCKIERT (Datum,
   1-Satz-Grund)` markieren, NICHT halb einbauen, mit dem NÄCHSTEN
   Punkt weitermachen. Niemals auf den User warten.
4. **Commits:** jeder fertige Punkt wird einzeln committet (Pre-Commit
   erzwingt die volle Suite, ~8 Min — via `nohup git commit … &` und
   auf den PID warten, sonst Timeout).

**Pfadgrenzen (hart):** Neues nur unter `dashboard/`, `tests/`,
`docs/`. In Bestandsdateien nur: `dashboard/**` (inkl. theme.py,
app.py, tabs/, factory/), `tests/conftest.py` (nur ADDITIV: neue
Fixtures), dieses Dokument. NIEMALS anfassen: `bot/`, `strategy/`,
`broker/`, `portfolio/`, `analyzers/`, `system/`, `.env`, crontab,
systemd — alle Bot-Daten nur READ-ONLY konsumieren.

**Muster-Dateien (Vorbild kopieren statt neu erfinden):**

| Aufgabe | Vorbild |
|---|---|
| Read-only-Datensammler, fail-open je Quelle | `dashboard/dossier.py` |
| Eigene read-only sqlite3-Verbindung auf Bot-DB | `dashboard/genealogy.py` |
| SVG-Panel mit PALETTE + Escaping | `dashboard/instruments.py`, `dashboard/power_meter.py` |
| Neue Maschine in der Szene | `dashboard/factory/state.py` `_read_*()` + `machines.py` + `test_dashboard_factory.py` |
| Tab-Fragment isoliert testen | `tests/test_dashboard_dossier_tab.py` (AppTest.from_string-Mini-Skript) |
| Injizierbarer Store für Tests | `dashboard/calibration_curve.py` (`store=None`-Parameter) |
| Ereignis-Fenster aus dem Feed lesen | `read_feed_events_until` (H2.3, `dashboard/factory/state.py`) |

**Standard-Abschluss „→ SA" nach JEDEM Punkt:**
1. Neue Unit-Tests des Punkts grün.
2. `venv/bin/python3 -m pytest tests/test_dashboard_*.py -q` grün.
3. Voll-Render: `AppTest.from_file("dashboard/app.py")` für
   DASHBOARD_THEME=pixel/plain/blueprint × {}, {kiosk:1}, {mobile:1} —
   alle 9 ohne Exception. Dabei `dashboard.departures.earnings_rows`
   auf `lambda *a, **k: []` stubben (sonst 12 yfinance-Netzabrufe).
4. Checkbox hier im Dokument auf `[x]` + kurze „Umgesetzt …"-Notiz
   (inkl. Abweichungen von der Vorgabe — ehrlich, nicht beschönigen).
5. Einzel-Commit (siehe Freigabe 4), Stil: `feat(dashboard): L<x.y> …`
   mit `Co-Authored-By`-Zeile wie in der Git-Historie.

**Eiserne Regeln:**
- Kein Panel/Maschine ohne ECHTE Datenquelle; leere Quelle → ehrlicher
  Leerzustand, NIE Platzhalter-Zahlen.
- Animationen nur, wenn der echte Zustand es hergibt (Vorbild:
  Stromzähler-Scheibe dreht nur bei echtem Tagesverbrauch); immer
  `prefers-reduced-motion`-fest (theme.py-Block erweitern).
- Die Fabrik-Szene (`factory/state.py` read_state-Pfad) bleibt
  NETZFREI — kein Kurs-Abruf, keine HTTP-Calls; P&L nur in Tabs mit
  `ctx.prices`.
- Jede neue Datei, die unter `data/` SCHREIBT, braucht eine
  autouse-Isolations-Fixture in `tests/conftest.py` — im selben
  Commit, nicht später. Read-only-Leser brauchen keine.
- Alle dynamischen Texte in SVG/HTML escapen (`html.escape`).
- Vor dem Bauen echte Datenstruktur ansehen (eine Zeile aus der
  echten Datei lesen), nicht der Beschreibung hier blind glauben —
  Abweichungen als Notiz dokumentieren.

**Bekannte Fallen (alle 15./16.7. real erlebt):**
- `st.form_submit_button` erscheint in AppTest unter `at.get("button")`,
  NICHT unter einem eigenen Typ.
- Es gibt KEINE AppTest-Elementklasse für `st.altair_chart` — Charts
  nur über „kein Exception" testbar.
- `st.cache_data`-Funktionen brauchen hashbare Argumente (Tuple statt
  Liste — siehe `_cached_earnings_rows`).
- Test-Ticker IMMER synthetisch wählen (`ZXX…`), nie AAPL/NVDA/TSLA —
  echte Ticker kollidieren mit Produktions-DBs und Nachbar-Tests
  (Decision-Log-Test-DB ist sessionweit geteilt).
- Default-Parameter binden zur Ladezeit: NIE `def f(path=KONSTANTE)`
  für Pfade — Konstante zur Laufzeit im Funktionskörper auflösen
  (ExperienceStore-Vorfall 16.7., siehe L1.1-Notiz).
- `system.live_status` cacht ein Feed-Singleton — conftest setzt es
  zurück; eigene Module nicht noch einmal cachen lassen.
- `st.markdown` führt KEIN JavaScript aus (innerHTML) — deshalb ist
  L5.2 gesperrt und L5.1 bewusst JS-frei.
- Beim Editieren von `scene.py`/`machines.py`: Klick-Links sind
  `<a href="?factory=…" target="_self">` — Struktur nicht verändern.

## Datenquellen-Inventar (16.7. geprüft, alles echt vorhanden)

| Quelle | Inhalt | Für |
|---|---|---|
| `data/analysis_log.db` | 1620 Analysen, 237 Ticker (Top: GILD 50×), Score/Confidence/Rationale je Lauf | L1 Score-EKG |
| `data/experience.db` (decisions) | Ausgangs-Labels: pnl_pct, outcome, exit_reason, hold_days, regime | L1 Bilanz, L2 Erinnerungen |
| `analyzers/stock_relations.py` | THEMES + `_TICKER_TO_THEMES` (Single Source) | L1 Verwandte |
| `data/news_velocity.json` | News-Puls-Zeitreihe je Ticker | L1 Puls |
| `data/reddit_hype_cache.json`, `data/options_intelligence.json` | Hype/Options-Signale je Ticker | L1 (optional) |
| `data/position_notes.db`, `data/ticker_profiles.json` | eigene Notizen, Sektor/Firma | L1 Kopfzeile |
| `data/factory_history.jsonl` | Szenen-Schnappschüsse alle 10 Min — **erst seit 15.7., wächst** | L2 Traum |
| `data/activity_feed.db` | Ereignis-Archiv (seit 16.7. sauber + testisoliert) | L2 Traum-Untertitel |
| `portfolio.db` (positions) | entry_date, target_hold_days, shares | L3 überall |

---

## L1 — Personalakten-Kartei (das Werk kennt jede Aktie)

Eigener Tab „🗂 Kartei": je Aktie eine Personalakte, die ALLES bündelt,
was das Programm je über sie gesammelt hat. Kern-Ehrlichkeitsregel: jede
Akte zeigt nur, was WIRKLICH da ist — dünne Akte = dünne Anzeige, keine
Platzhalter.

- [x] 🟢 **L1.1 Datensammler `dashboard/dossier.py`** —
      `dossier(ticker) -> Dict` bündelt read-only, jede Quelle einzeln
      fail-open: Profil (ticker_profiles), Analyse-Historie
      (`AnalysisLog.get_recent(ticker=…, limit=100)`, chronologisch),
      Trade-Bilanz (experience.db: eigene read-only sqlite3-Verbindung,
      Muster `genealogy.py`; Zeilen mit outcome je Ticker),
      Themen-Verwandte (`stock_relations._TICKER_TO_THEMES` — Achtung:
      privater Name; wenn möglich über THEMES selbst ableiten),
      News-Puls (news_velocity.json, letzte 14 Tage), eigene Notiz
      (PositionNotes.get). Plus `all_known_tickers()` (distinct aus
      analysis_log, sortiert nach Analysen-Anzahl). Tests je Quelle
      inkl. „Quelle fehlt → Feld leer, kein Crash".

      Umgesetzt 16.7.2026: `themes_and_related()` nutzt die ÖFFENTLICHEN
      `StockRelations.get_themes()`/`get_related()` statt des privaten
      `_TICKER_TO_THEMES` (existierte schon, bessere Wahl als vom
      Roadmap-Entwurf angenommen). `trade_bilanz(ticker, store=None)`
      nimmt einen injizierbaren Store (Muster
      `calibration_curve.confidence_win_rates`) — nötig, weil
      `ExperienceStore.__init__` seinerzeit `db_path` als
      Default-Parameter zur Modul-Ladezeit band (siehe Sicherheitsfund
      unten). 17 Tests (test_dashboard_dossier.py), jede Quelle einzeln
      fail-open getestet.

      **🔒 Sicherheitsfund beim Bauen (schwerwiegender als geplant):**
      `tests/test_dashboard_factory.py` rief `ExperienceStore()`
      UNGESCHÜTZT auf (kein `db_path`) und schrieb dabei bei JEDEM
      Suite-Lauf eine synthetische AAPL-„Live"-Zeile (WIN, +5 %,
      `decided_at` 1.1.2026 — vor jedem echten Bot-Betrieb) in die
      ECHTE `data/experience.db`. Grund: `ExperienceStore.__init__`
      hatte `db_path: str = DB_PATH` als Default-Parameter — zur
      Modul-Ladezeit gebunden, ein DB_PATH-Monkeypatch griff darum bei
      ungeschützten Aufrufern (auch `dashboard/achievements.py`) nicht.
      Diese Fake-Zeile hatte bereits real die „Erster Live-Trade"-
      Plakette in der ECHTEN `data/achievements.json` ausgelöst
      (unlocked_at 15.7.) — der Bot hat bis heute NIE real gehandelt.
      Behoben: `ExperienceStore.__init__` auf Laufzeit-Lookup
      umgestellt (Muster `dashboard/dry_run.py`), neue autouse-Fixture
      `_isolate_experience_store` in `conftest.py`, Fake-Zeile aus der
      echten DB gelöscht (347 statt 348 Zeilen — die anderen 347 bleiben
      unangetastet, echt), falsche Plakette aus `achievements.json`
      entfernt. Volle Suite (1351 Tests) + MD5-Check von 10 echten
      Dateien vor/nach bestätigen: kein weiterer Leck mehr.
- [x] 🟢 **L1.2 Score-EKG** — `dossier_ekg(history)` : Altair-Linie
      sentiment_score über analyzed_at, Punkte eingefärbt nach
      recommendation (BUY grün/SKIP grau/SELL rot), Confidence als
      Punktgröße. AppTest-Falle bleibt: es gibt KEINE
      altair_chart-Elementklasse — nur „kein Exception" testbar.

      Umgesetzt 16.7.2026: als `_ekg_chart()` direkt in
      `dashboard/tabs/dossier.py` (Muster `tabs/trades.py` — Chart-Code
      lebt dort inline, kein separates Chart-Modul; Abweichung vom
      Roadmap-Entwurf, konsistenter mit dem Rest des Codebase).
- [x] 🟡 **L1.3 Akten-Blatt (UI)** — neuer Tab „🗂 Kartei" in app.py
      (Tab-Liste + Modul `dashboard/tabs/dossier.py`): Selectbox über
      `all_known_tickers()` (Format „NVDA — 47 Analysen"), darunter
      Aktenkopf (Firma/Sektor/Themen), Score-EKG, Bilanz-Zeile
      (n Trades, Gewinne/Verluste, Ø pnl_pct — NUR aus gelabelten
      Zeilen), letzte 5 Entscheidungen mit Begründung, News-Puls-
      Sparkline, Notiz-Feld (bestehende PositionNotes-Mechanik
      wiederverwenden, KEIN zweiter Speicher). [URTEIL] Anordnung/
      Gewichtung des Blatts — was oben steht, muss das Wichtigste sein
      (Bilanz vor Puls). plain-Theme: gleiche Daten als Tabellen.

      Umgesetzt 16.7.2026: [URTEIL] Reihenfolge Kopf → KPI-Zeile
      (Analysen/Trades/Gewinne-Verluste/Ø-Ergebnis) → Score-EKG →
      Themen/Verwandte (als `?dossier=TICKER`-Links, W3.2-Muster) →
      letzte 10 gelabelte Entscheidungen → News-Puls-Balken → Notiz.
      Deep-Link vorselektiert die Akte aus der URL (Kleinschreibung
      normalisiert). Leerzustand („noch keine Analysen") statt
      Platzhalter-Akte. plain-Theme bekommt dieselben Daten (Chart +
      Tabellen sind bereits theme-neutral, keine gesonderte Fallback-
      Ansicht nötig). 5 AppTest-Tests
      (test_dashboard_dossier_tab.py). Voll-Render-Verifikation gegen
      ECHTE Produktionsdaten: GILD erscheint korrekt als
      meist-analysierter Ticker (50 Analysen) an erster Stelle der
      Auswahl.
- [ ] 🟢 **L1.4 Querverweise** — in der Akte: Verwandte als klickbare
      Links (`?dossier=TSM`-Query-Param, Muster `?factory=`-Fokus aus
      W3.2); im Lager-Regal (D8.3) und im Trades-Tab je Ticker ein
      „→ Akte"-Link. Unbekannte Query-Werte stillschweigend ignorieren.
- [ ] 🟡 **L1.5 Akten-Deckblatt-Stempel** — kleine ehrliche Stempel auf
      der Akte, nur wenn die Bedingung WIRKLICH zutrifft (sonst kein
      Stempel): „BEWÄHRT" (≥3 gelabelte Trades, Ø pnl > 0),
      „STAMMGAST" (≥30 Analysen), „FRISCH" (erste Analyse <14 Tage
      her), „GEMIEDEN" — ENTSCHIEDEN 16.7. nach Prüfung:
      `rl_weights.json` ist GLOBAL (6 Feature-Gewichte), NICHT je
      Ticker abfragbar. Stattdessen: `analyzers.entry_filter.
      EntryFilter().evaluate(features)` mit den Features der LETZTEN
      analysis_log-Zeile des Tickers aufrufen (exakt der Weg, den
      `dashboard/dry_run.py` fürs „Lern-Filter AVOID" nutzt — vorher
      dort ansehen, welche Feature-Keys evaluate() erwartet [URTEIL:
      nur dieses Mapping]). Verdict AVOID → Stempel; NEUTRAL/PROCEED/
      CAUTION/Exception → KEIN Stempel. Tests je Stempel-Bedingung
      (positiv + negativ + Exception-Fall).

## L2 — Traummodus & Erinnerungen (das Werk erinnert sich)

Lebendigkeit aus der EIGENEN Geschichte — nichts wird erfunden, jede
Traumszene ist ein echter archivierter Zustand, jede Erinnerung ein
echtes Ereignis mit Datum.

- [ ] 🟢 **L2.1 Erinnerungs-Rechner `dashboard/memories.py`** —
      `memories_for(day) -> List[Dict]`: durchsucht experience.db +
      portfolio.db (trades) + achievements.json + thesis_registry nach
      Jahrestagen relativ zu `day`: „Heute vor N Wochen: erster
      Live-Trade", „…größter Gewinn (TSM +9,1 %)", „…Regime kippte auf
      BEAR", „…These mechanical_baseline registriert". Nur echte
      Treffer, max. 3, mit exaktem Datum. Read-only, fail-open, Tests
      mit synthetischer DB (Muster test_dashboard_genealogy).
- [ ] 🟢 **L2.2 Erinnerungs-Plakette** — Fabrik-Tab, unter der Szene:
      „📅 Heute vor …"-Zeilen aus L2.1. Plain: st.caption. Nichts
      anzeigen, wenn keine Erinnerung — kein „noch nichts passiert"-
      Gefüll.
- [ ] 🟡 **L2.3 Traum-Datenlage** — `dream_material() -> Optional[str]`:
      wählt aus factory_history.jsonl einen vergangenen Tag mit ≥10
      Schnappschüssen (deterministisch: Tag mit den meisten
      Schnappschüssen, bei Gleichstand der jüngste). Stand 16.7. gibt
      es erst 2 Tage Material — die Funktion muss mit „noch nichts
      Träumbares" (None) leben und die UI zeigt dann schlicht keinen
      Traum. ENTSCHIEDEN 16.7.: Schwelle fest ≥10 Schnappschüsse; als
      Modul-Konstante `_MIN_SNAPSHOTS = 10` anlegen (leicht
      nachjustierbar), NICHT dynamisch raten.
- [ ] 🔴 **L2.4 Traum-Wiedergabe** (GESPERRT für günstiges Modell,
      User-Entscheid 16.7.) — nur wenn `state.paused` ODER
      lokale Nachtzeit (22–06 Uhr): eigenes
      `@st.fragment(run_every="3s")`, das pro Tick den NÄCHSTEN
      Schnappschuss des Traum-Tages rendert (reconstruct_from_snapshot
      + build_scene_svg existieren, H2.2), mit Sepia-Filter
      (CSS-Klasse `px-dream`, neu in theme.py, inkl.
      prefers-reduced-motion: dann statisches Standbild) und klarer
      Kennzeichnung „🌙 Traum: Wiederholung vom 15.07., 8× Zeitraffer".
      Der Traum ersetzt die Live-Szene NICHT (die zeigt weiter den
      echten Pause-Zustand) — er läuft als eigener Block darunter,
      einklappbar. HEIKEL (darum 🔴): Fragment-Takt vs. 60s-Szene-
      Fragment, Zustands-Index über Reruns (st.session_state), und der
      Traum darf NIE in factory_history schreiben (kein _maybe_snapshot
      im Traum-Pfad!).
- [ ] 🟢 **L2.5 Traum-Untertitel** — unter der Traumszene die 2–3
      Feed-Ereignisse des nachgespielten Zeitfensters
      (read_feed_events_until existiert, H2.3) als leise Untertitel.
      Hinweis: activity_feed.db wurde 16.7. geleert (war reines
      Testrauschen) — Untertitel gibt es also erst für Tage nach dem
      Neustart; leer = einfach keine Untertitel.

## L3 — Positionen leben im ganzen Werk (User-Idee 16.7.)

Offene Positionen sind heute EIN Bereich (Lager). Künftig ziehen sie
sich als Bewohner durchs ganze Werk — jede Erscheinung speist sich aus
denselben echten Feldern (entry_date, target_hold_days, shares), es
gibt KEINE zweite Datenhaltung. Harte Grenze bleibt: die Fabrik-Szene
ist netzfrei — P&L (braucht Live-Kurs) erscheint nur in Tabs, die
`ctx.prices` haben; in der Szene sprechen wir über ZEIT (Haltedauer),
nicht über Geld.

- [x] 🟢 **L3.1 Abfahrtsplan der Positionen** — die D8.1-Tafel bekommt
      je offener Position eine Zeile: „TSM — planmäßige Abfahrt"
      am Tag entry_date + target_hold_days (kind="position", eigene
      Farbe). Dazu Earnings-Termine GEHALTENER Ticker als „⚠ Fracht-
      risiko" (EarningsFilter, läuft durch denselben 6h-Cache).
      Abfahrt in der Vergangenheit (Ziel überschritten) → „überfällig"
      statt stillschweigend weg.

      Umgesetzt 16.7.2026: `_position_rows()` als weitere NETZFREIE
      Quelle direkt in `upcoming_events()` (Portfolio ist lokal —
      gehört zu den internen Quellen, nicht in die gecachten
      `extra_rows`, die nur für den Netz-Abruf Earnings gedacht sind).
      Farbsprache: ABFAHRT=cobalt („läuft", wie Szenen-Legende),
      ÜBERFÄLLIG/FRACHTRISIKO=rot. `board_html` sagt jetzt
      „überfällig (3 Tage)" statt „in -3 Tagen" (Singular/Plural
      korrekt) — der Horizont-Filter schneidet nur nach vorne ab, also
      bleiben überfällige Zeilen stehen und stehen durch die
      Datums-Sortierung ganz oben. `earnings_rows(held=…)` markiert
      Earnings gehaltener Titel; der Tab speist gehaltene Ticker
      ZUSÄTZLICH zur Watchlist in denselben 6h-Cache ein (sonst fehlte
      der Termin genau im riskantesten Fall — ein Titel, den wir halten,
      der aber nicht mehr auf der Watchlist steht). ABWEICHUNGEN:
      (a) `limit` von 10 auf 14 erhöht, sonst hätten Positions-Zeilen
      bei vollem Depot die Makro-Termine verdrängt; (b) Test-Helfer
      `_no_system` → `_only_macro` umbenannt, legt jetzt auch die
      Positions-Quelle stumm (sonst hingen Makro-Tests an der echten
      Depot-Lage). 11 neue Tests (test_dashboard_departures.py, 24
      gesamt), netzfrei via Fake-Portfolio. Aktuell zeigt die Tafel
      keine Abfahrten — das Depot ist bis zum Neustart leer; ab dem
      ersten Kauf erscheinen sie automatisch.
- [ ] 🟡 **L3.2 Loren-Umlauf in der Szene** — Schienenkreis durch die
      Halle (scene.py); je Position eine Lore, Position auf der
      Schiene = Haltedauer-Fortschritt (0 % am Wareneingang, 100 % am
      Verladetor), Farbe nach age_ratio (exakt die D7.2-Logik,
      netzfrei), Tooltip Ticker/Anteile/Tage. Max. 8 Loren + „+n"
      (Muster D7.2-Kisten). VORGABE 16.7.: Schiene als flacher Bogen
      im unteren Randstreifen der Szene (unterhalb der Maschinen-
      Zeile); [URTEIL: nur die exakte Trasse] vorher die Maschinen-
      Koordinaten in scene.py/machines.py WIRKLICH ansehen und per
      Test absichern, dass kein Loren-/Schienen-Rect ein Maschinen-
      Rect schneidet (Koordinaten-Assertion, nicht Augenmaß).
      ERST NACH L5.1 bauen (mehr Platz).
- [x] 🟢 **L3.3 Ticker-Laufband kennt die Fracht** — das D7.3-Band
      mischt Positions-Meldungen ein, zur Renderzeit berechnet (KEINE
      Feed-Schreibungen): „TSM Tag 12/15", „QCOM überfällig seit 3
      Tagen". Escaping wie gehabt.

      Umgesetzt 16.7.2026: `freight_ticker_items()` in departures.py.
      Dabei die Positions-Rechnung aus L3.1 zu einer gemeinsamen
      `position_progress()` herausgezogen (ticker/due_date/days_held/
      hold_days/overdue_days) — Tafel UND Laufband rechnen jetzt aus
      derselben Quelle, sonst wären sie unweigerlich auseinander
      gedriftet; ein Test hält das fest. Einbau in app.py direkt am
      bestehenden D7.3-Block, fail-open; Escaping macht wie gehabt
      `theme.ticker()` zentral. Singular/Plural bei „ÜBERFÄLLIG SEIT
      1 TAG/3 TAGEN" korrekt. 6 neue Tests (30 in der Datei).
- [ ] 🟡 **L3.4 Werksleiter schaut aufs Lager** — das H7.1-Gesicht
      bezieht die Positions-Lage ehrlich mit ein, WENN Kurse da sind
      (app.py hat ctx.prices). FORMEL ENTSCHIEDEN 16.7.:
      `angepasst = basis − 0.15 × verlust_anteil`, geklemmt auf [0, 1];
      `verlust_anteil` = Anteil offener Positionen mit negativem P&L
      (0 Positionen → Term 0, Basis unverändert). Damit kippt EIN
      Ausreißer das Gesicht nie um mehr als 0.15. [URTEIL: nur wo die
      Basis herkommt] vorher in app.py ansehen, welcher Score heute in
      `face_svg()` fließt, und die Formel dort im Docstring
      festschreiben. Tests: 0 Positionen / alle im Plus / alle im
      Minus / genau eine von vier im Minus.
- [ ] 🔴 **L3.5 Positions-Lebenslauf in der Akte** (GESPERRT für
      günstiges Modell, User-Entscheid 16.7.) — Brücke zu L1: die
      Personalakte einer GEHALTENEN Aktie zeigt oben eine Lebenslauf-
      Leiste: Einstieg (Datum/Kurs aus positions), bisherige Feed-/
      Order-Ereignisse zu diesem Halt (order_log + activity_feed,
      read-only), geplante Abfahrt, SL/TP-Marken (aus der
      GTC-Stop-Logik, sofern als Daten greifbar — erst prüfen WO
      Stop-Preise persistiert sind: broker/order_log? conditional_
      entries.json? NICHT raten). 🔴 weil Datenlage unklar ist und
      erst sauber ermittelt werden muss.
- [ ] 🟢 **L3.6 Lager-Zählwerk: Zu- und Abgänge** (User-Frage 16.7.:
      „sieht man auch Bestandsveränderungen?" — Antwort war: nur
      indirekt über Feed/Replay, LÜCKE) — am Hochregallager ein
      Wareneingangs-/Warenausgangs-Zählwerk: „heute +2 / −1" aus der
      echten `trades`-Tabelle (portfolio.db, read-only eigene
      sqlite3-Verbindung, Muster genealogy.py; Käufe/Verkäufe des
      Tages zählen). Im Lager-Detailpanel (Klick-Fokus) zusätzlich die
      letzten 5 Bestandsbewegungen als Liste (Datum, Ticker, ±Stück).
      Mechanische Zählwerk-Optik wie der D7.2-Durchsatz-Zähler am
      Förderband (gleiches Muster wiederverwenden). 0 Bewegungen =
      Zählwerk zeigt 0, kein Verstecken.

      Umgesetzt 16.7.2026: `state._warehouse_movements()` mit eigener
      read-only sqlite3-Verbindung auf `portfolio.PORTFOLIO_DB`
      (Muster genealogy.py — die Portfolio-Klasse hat keine
      Trade-Historie-Schnittstelle). Echte Spalten geprüft:
      `trades(ticker, action BUY/SELL, shares, price, timestamp, pnl,
      …)`, timestamps sind naive lokale ISO-Strings → Tages-Schnitt per
      `substr(timestamp,1,10)`. Zählwerk zweistellig (nicht dreistellig
      wie am Förderband: >99 Bewegungen/Tag gibt der Funnel nicht her),
      grün für Zugang / kupfer für Abgang, 0 wird angezeigt.
      Zusätzlich: Tooltip-Zeile „heute: +N rein / -N raus" am Lager und
      im Detail-Panel zwei Metriken + Tabelle der letzten 5 Bewegungen
      — die läuft VOR dem Positions-Check, damit die Historie auch bei
      LEEREM Lager sichtbar bleibt (aktueller Normalfall). 10 neue
      Tests (test_dashboard_factory.py, 89 gesamt).
- [ ] 🟡 **L3.7 Anlieferung & Versand — Trade als sichtbare Lieferung**
      (User-Wunsch 16.7.: „sieht man im Dashboard, wie eine Lieferung
      fertig gemacht wird?" — Befund: man sieht nur das ERGEBNIS,
      Kiste da/weg, nicht den Vorgang). Ereignis-getriebene Animation
      in der Szene: liegt im Activity-Feed ein `trade`-Event der
      letzten ~10 Minuten (read_feed_events_until existiert, H2.3),
      zeigt die Szene den Vorgang — Kauf = Paket rollt vom Förderband
      ins Lager (Anlieferung), Verkauf = Kiste rollt vom Lager zum
      Verladetor (Versand); Stop-Loss-Verkauf mit rotem
      „EXPRESS"-Aufkleber. CSS/SMIL-Animation (Performance-Regel:
      nie pro Rerun rechnen), reduced-motion-fest, danach Ruhe.
      **Ehrlichkeits-Regel wie Stromzähler-Scheibe: Animation NUR bei
      echtem, frischem Trade-Event — kein Dauergewusel als Deko.**
      ENTSCHIEDEN 16.7.: Zeitfenster fest 10 Minuten, als Konstante
      `_DELIVERY_WINDOW_MIN = 10`. Verzahnt mit W4-Ereignis-Ebene
      (goldener Wimpel beim ersten Live-Trade bleibt separat).

## L4 — Fehlende Maschinen (User-Frage 16.7.: „hat jedes Feature seine
## eigene Maschine?")

Befund: 11 Maschinen decken die Kern-Subsysteme ab, aber vier ECHTE
Subsysteme fehlen in der Halle. Regel bleibt: eine Maschine pro
Subsystem (nicht pro Dashboard-Feature — Panels wie Stromzähler/Tafel
sind bewusst Anbauten, keine Maschinen). Für jede neue Maschine gilt
das W2-Muster: `_read_<id>()` in state.py (fail-open, netzfrei),
Eintrag in MACHINE_IDS + MACHINE_LABELS, Zeichnung in machines.py,
Tooltip + Klick-Fokus, Tests wie test_dashboard_factory.py.
ENTSCHIEDEN 16.7.: **L5.1 (größeres Gelände) ist harte Voraussetzung —
kein L4-Punkt darf vor abgeschlossenem L5.1 begonnen werden** (das
aktuelle Grid ist voll, Quetschen ist keine Option).

- [ ] 🟡 **L4.1 Wartehalle (Signal-Queue)** — `data/signal_queue.db`
      (PENDING-Signale bei vollen Slots): Bank mit wartenden Paketen,
      Anzahl = echte PENDING-Zeilen; Tooltip zeigt Ticker + seit wann.
      Status: off (leer) / ok (1–2) / warn (≥3, Stau).
- [ ] 🟢 **L4.2 Auftragsbriefkasten (Werksaufträge)** —
      `analyzers/user_request_queue.py` (peek() existiert): Briefkasten
      am Werkstor, Fähnchen oben wenn Aufträge warten; Tooltip = die
      eingeworfenen Ticker. Verzahnt mit dem H1.2-Formular (das wirft
      hier ein).
- [ ] 🟡 **L4.3 Konstruktionsbüro (Strategie-Labor)** —
      `data/strategy_registry.json` + `data/thesis_registry.json`:
      Zeichenbrett-Häuschen; Status nach Registry-Inhalt (0 ACTIVE =
      gedimmt mit ehrlichem Tooltip „keine Strategie mit bewiesener
      Kante" — DER bekannte Befund, nicht beschönigen), Thesen mit
      Zeit-Budget-Fortschritt im Detail-Panel.
- [ ] 🟢 **L4.4 Funkturm (Telegram)** — Sendemast am Dach;
      Status aus `TELEGRAM_MODE`-Config + `data/notify_throttle.json`
      (letzte Sendung, gedrosselt?). Funkwellen-Animation NUR wenn
      heute wirklich gesendet wurde (Ehrlichkeits-Regel wie die
      Stromzähler-Scheibe); reduced-motion-fest.

## L5 — Werksgelände & Kamera (User-Idee 16.7.: „mit dem Mauszeiger
## über die Fabrik fliegen — dann muss sie nicht auf eine
## Bildschirmgröße begrenzt sein")

Technische Ausgangslage (16.7. geprüft, nicht geraten): die Szene ist
EIN SVG (viewBox 0 0 1200 675, `style="width:100%"`), gerendert über
`st.markdown(unsafe_allow_html=True)`; Maschinen sind
`<a href="?factory=…" target="_self">`-Links (Klick-Fokus W3.2).
**Harte Falle:** `st.markdown` führt KEIN JavaScript aus (innerHTML —
Scripts werden nicht ausgeführt). Echtes Drag/Zoom braucht darum
`st.components.v1.html` (iframe) — und DORT müssen die Maschinen-Links
auf `target="_parent"` umgestellt werden, sonst navigiert der Klick
das iframe statt des Dashboards und der Fokus bricht.

- [ ] 🟢 **L5.1 Größeres Gelände + Scroll-Flug (die 80%-Lösung,
      OHNE JS)** — viewBox/Grid wachsen lassen (z. B. 2000×675,
      Platz für L4-Maschinen), das SVG mit fester Pixel-Breite in
      einen `overflow-x:auto`-Container (Muster D8.3-Regal). „Fliegen"
      = scrollen/wischen; funktioniert in st.markdown, Links + Tooltips
      + Animationen bleiben unverändert, mobil = natives Touch-Wischen.
      Kiosk-Modus (H6.1): dort NICHT scrollen lassen, sondern die
      Gesamtansicht skalieren (ein Wandbild scrollt niemand).
- [ ] 🔴 **L5.2 Echter Kamera-Flug (Drag-Pan + Rad-Zoom, MIT JS)**
      (GESPERRT — User-Entscheid 16.7.: erst L5.1 im Alltag testen;
      dieser Punkt wird, falls überhaupt, später mit starkem Modell
      gebaut. Auch nicht „experimentell anfangen".) —
      nur wenn L5.1 sich zu klein anfühlt: Szene in
      `st.components.v1.html` einbetten, Vanilla-JS (kein CDN, alles
      inline): Drag verschiebt / Mausrad zoomt die viewBox,
      Doppelklick = Reset, Touch-Pinch für mobil. Maschinen-Links auf
      `target="_parent"` (siehe Falle oben) — Klick-Fokus MUSS danach
      nachweislich weiter funktionieren (Test + Hand-Verifikation).
      Zoomgrenzen festlegen (min = Gesamtansicht, max ≈ 3×), damit man
      sich nicht „verfliegt". iframe-Höhe fest → `height`-Parameter
      sauber berechnen. 🔴 wegen JS-im-iframe-Komplexität und weil
      AppTest iframes nur als Block sieht (Verifikation dünner als
      gewohnt — ehrlich dokumentieren).

## L6 — Wissens-Sichtbarkeit (User-Freigabe 16.7.: „wird das Wissen
## auch visuell erfasst?")

Das gesammelte Wissen WIRKT (Lern-Filter, Kalibrierung, Lessons-Memo),
ist aber teils unsichtbar. Zwei Panels im Tab „Trades & Lernen", direkt
bei der bestehenden Kalibrier-Kurve (H3.3 — gleiche Chart-Muster).

- [ ] 🟢 **L6.1 Lernkurven-Wand** — Entwicklung über die Zeit statt nur
      Ist-Stand: (a) `data/calibration_monitor.json` → `history[]`
      (run_at, brier, bss, ece, auc — Stand 16.7.: genau 1 Messpunkt
      vom 7.7.) als Linien-Chart, ABER erst ab ≥3 Messpunkten — bei
      1–2 stattdessen ehrliche Tabelle + Caption „Kurve entsteht,
      sobald der Monitor öfter gelaufen ist"; (b) kumulierte Anzahl
      gelabelter Erfahrungen über die Zeit aus experience.db
      (`labeled_at` je Zeile; injizierbarer Store wie
      `calibration_curve.py`). Neues Modul `dashboard/learning_curve.py`
      (read-only), Render in tabs/trades.py neben der Kalibrier-Kurve.
- [ ] 🟡 **L6.2 Lern-Filter-Röntgenblick** — was der Filter gelernt
      hat, sichtbar: `data/rl_weights.json` enthält (16.7. geprüft)
      `weights` + `feature_names` (sentiment_score, vix_level,
      momentum_5d, news_velocity, confidence_encoded, regime_encoded)
      + `trade_count` (aktuell 6!) + `reward_history`. Horizontale
      Balken je benanntem Feature-Gewicht, PFLICHT-Caption mit
      `trade_count`: „gelernt aus erst N Trades — mit Vorsicht lesen".
      [URTEIL: nur die deutschen Feature-Labels] nüchtern übersetzen
      (z. B. „News-Tempo"), NICHT interpretieren („achtet auf X" wäre
      Überverkauf bei n=6). Fail-open: Datei fehlt → Panel fehlt.

## Reihenfolge (verbindlich für die autonome Abarbeitung)

1. **L3.1** Abfahrtsplan der Positionen
2. **L3.3** Laufband kennt die Fracht
3. **L3.6** Lager-Zählwerk
4. **L2.1 + L2.2** Erinnerungs-Rechner + Plakette
5. **L6.1** Lernkurven-Wand
6. **L6.2** Lern-Filter-Röntgenblick
7. **L1.4** Querverweise → Akte
8. **L1.5** Akten-Stempel
9. **L5.1** Größeres Gelände + Scroll-Flug
10. **L4.2** Auftragsbriefkasten (erst nach L5.1!)
11. **L4.4** Funkturm
12. **L4.1** Wartehalle
13. **L4.3** Konstruktionsbüro
14. **L3.2** Loren-Umlauf (nach L5.1, letzter Szenen-Punkt)
15. **L2.3 + L2.5** Traum-Datenlage + Untertitel (L2.4 selbst bleibt
    gesperrt — L2.3/L2.5 sind reine Daten-/Anzeige-Bausteine dafür)
16. **L3.4** Werksleiter-Formel (zuletzt: berührt app.py-Kopfbereich)

ÜBERSPRUNGEN wird (gesperrt): L2.4, L3.5, L5.2.

Hinweis Zeitpunkt: nichts hiervon blockiert den Bot-Neustart —
alles ist reine Dashboard-/Lese-Arbeit. Umgekehrt profitieren L2 (Feed-
Untertitel, Traummaterial) und L3 (echte Positionen) stark davon, dass
der Bot wieder läuft und Daten produziert.
