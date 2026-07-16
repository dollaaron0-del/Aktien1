# Fabrik Lebendig — die gesammelten Daten fangen an zu spielen

Stand: 16.7.2026. Dritte Ausbaustufe nach `DESIGN_ROADMAP.md` (D0–D8) und
`DASHBOARD_HORIZONT.md` (H1–H7, komplett). Drei vom User gewählte
Großprojekte, deutlich mehr Arbeit als bisherige Einzelpunkte — jedes
spielt mit Daten, die das Programm über Monate gesammelt, analysiert und
zugeordnet hat, und macht das Werk lebendiger.

**Es gelten unverändert die Arbeitsregeln aus `DASHBOARD_HORIZONT.md`**
(Pfadgrenzen, Standard-Abschluss „→ SA" mit Tests + Voll-Render
pixel/plain/blueprint × normal/kiosk/mobile, Abbruchregel, bekannte
Fallen). Zusätzliche Falle seit 16.7.: `system.live_status` ist in Tests
per `_isolate_live_status` isoliert (conftest.py) — neue Module, die in
`data/` schreiben, brauchen weiterhin ihre eigene autouse-Fixture.

Ampel wie gehabt: 🟢 günstiges Modell reicht · 🟡 günstig mit markierter
Urteils-Stelle · 🔴 starkes Modell.

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
      Stempel): „BEWÄHRT" (≥3 gelabelte Trades, Ø pnl > 0), „GEMIEDEN"
      (Lern-Filter-AVOID aktiv — aus `rl_weights.json` prüfen ob je
      Ticker abfragbar; [URTEIL] falls nicht sauber abfragbar: Stempel
      weglassen statt raten), „STAMMGAST" (≥30 Analysen), „FRISCH"
      (erste Analyse <14 Tage her).

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
      Traum. [URTEIL] Mindest-Schwelle ggf. anpassen, wenn reale
      Daten-Dichte bekannt ist.
- [ ] 🔴 **L2.4 Traum-Wiedergabe** — nur wenn `state.paused` ODER
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

- [ ] 🟢 **L3.1 Abfahrtsplan der Positionen** — die D8.1-Tafel bekommt
      je offener Position eine Zeile: „TSM — planmäßige Abfahrt"
      am Tag entry_date + target_hold_days (kind="position", eigene
      Farbe). Dazu Earnings-Termine GEHALTENER Ticker als „⚠ Fracht-
      risiko" (EarningsFilter, läuft durch denselben 6h-Cache).
      Abfahrt in der Vergangenheit (Ziel überschritten) → „überfällig"
      statt stillschweigend weg.
- [ ] 🟡 **L3.2 Loren-Umlauf in der Szene** — Schienenkreis durch die
      Halle (scene.py); je Position eine Lore, Position auf der
      Schiene = Haltedauer-Fortschritt (0 % am Wareneingang, 100 % am
      Verladetor), Farbe nach age_ratio (exakt die D7.2-Logik,
      netzfrei), Tooltip Ticker/Anteile/Tage. Max. 8 Loren + „+n"
      (Muster D7.2-Kisten). [URTEIL] Schienenführung im 24×14-Grid so
      legen, dass keine Maschine verdeckt wird — vorher Layout in
      scene.py WIRKLICH ansehen, nicht raten.
- [ ] 🟢 **L3.3 Ticker-Laufband kennt die Fracht** — das D7.3-Band
      mischt Positions-Meldungen ein, zur Renderzeit berechnet (KEINE
      Feed-Schreibungen): „TSM Tag 12/15", „QCOM überfällig seit 3
      Tagen". Escaping wie gehabt.
- [ ] 🟡 **L3.4 Werksleiter schaut aufs Lager** — das H7.1-Gesicht
      bezieht die Positions-Lage ehrlich mit ein, WENN Kurse da sind
      (app.py hat ctx.prices): bestehende Stimmungs-Formel um einen
      dokumentierten Positions-Term ergänzen (z. B. Anteil Positionen
      im Minus). [URTEIL] Gewichtung — das Gesicht darf nicht von
      einem einzigen Ausreißer kippen; Formel im Docstring festhalten
      und testen (Grenzfälle 0 Positionen / alle im Plus / alle im
      Minus).
- [ ] 🔴 **L3.5 Positions-Lebenslauf in der Akte** — Brücke zu L1: die
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
      [URTEIL] Zeitfenster (10 Min?) an den 60s-Szenen-Refresh und
      die reale Zyklus-Frequenz anpassen. Verzahnt mit W4-Ereignis-
      Ebene (goldener Wimpel beim ersten Live-Trade bleibt separat).

## L4 — Fehlende Maschinen (User-Frage 16.7.: „hat jedes Feature seine
## eigene Maschine?")

Befund: 11 Maschinen decken die Kern-Subsysteme ab, aber vier ECHTE
Subsysteme fehlen in der Halle. Regel bleibt: eine Maschine pro
Subsystem (nicht pro Dashboard-Feature — Panels wie Stromzähler/Tafel
sind bewusst Anbauten, keine Maschinen). Für jede neue Maschine gilt
das W2-Muster: `_read_<id>()` in state.py (fail-open, netzfrei),
Eintrag in MACHINE_IDS + MACHINE_LABELS, Zeichnung in machines.py,
Tooltip + Klick-Fokus, Tests wie test_dashboard_factory.py.
ACHTUNG Layout: 24×14-Grid in scene.py ist voll — L4 braucht entweder
L5 (größeres Gelände) zuerst ODER eine bewusste Verdichtung; [URTEIL]
vor dem ersten L4-Punkt entscheiden, nicht nebenbei quetschen.

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
- [ ] 🔴 **L5.2 Echter Kamera-Flug (Drag-Pan + Rad-Zoom, MIT JS)** —
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

## Reihenfolge-Empfehlung

L1.1 → L1.2 → L1.3 ✅ (die Kartei trägt sofort sichtbar) → L3.1 + L3.3 +
L3.6 (kleine Schritte, großer Lebendigkeits-Gewinn, nutzen D8-/D7.2-
Infrastruktur) → L2.1 + L2.2 (Erinnerungen) → **L5.1 (größeres Gelände —
VOR L4, sonst ist kein Platz für neue Maschinen)** → L4.2 + L4.4 (die
zwei 🟢-Maschinen) → L3.2 (Loren — profitiert vom größeren Gelände) →
L4.1 + L4.3 → L2.3–L2.5 (Traum; profitiert davon, dass factory_history
bis dahin mehr Tage hat) → L1.4/L1.5 → L3.4 → L3.5 und L5.2 zuletzt
(Datenlage klären bzw. nur bei echtem Bedarf).

Hinweis Zeitpunkt: nichts hiervon blockiert den Bot-Neustart morgen —
alles ist reine Dashboard-/Lese-Arbeit. Umgekehrt profitieren L2 (Feed-
Untertitel, Traummaterial) und L3 (echte Positionen) stark davon, dass
der Bot wieder läuft und Daten produziert.
