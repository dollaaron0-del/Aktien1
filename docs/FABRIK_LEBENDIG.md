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

- [ ] 🟢 **L1.1 Datensammler `dashboard/dossier.py`** —
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
- [ ] 🟢 **L1.2 Score-EKG** — `dossier_ekg(history)` : Altair-Linie
      sentiment_score über analyzed_at, Punkte eingefärbt nach
      recommendation (BUY grün/SKIP grau/SELL rot), Confidence als
      Punktgröße. AppTest-Falle bleibt: es gibt KEINE
      altair_chart-Elementklasse — nur „kein Exception" testbar.
- [ ] 🟡 **L1.3 Akten-Blatt (UI)** — neuer Tab „🗂 Kartei" in app.py
      (Tab-Liste + Modul `dashboard/tabs/dossier.py`): Selectbox über
      `all_known_tickers()` (Format „NVDA — 47 Analysen"), darunter
      Aktenkopf (Firma/Sektor/Themen), Score-EKG, Bilanz-Zeile
      (n Trades, Gewinne/Verluste, Ø pnl_pct — NUR aus gelabelten
      Zeilen), letzte 5 Entscheidungen mit Begründung, News-Puls-
      Sparkline, Notiz-Feld (bestehende PositionNotes-Mechanik
      wiederverwenden, KEIN zweiter Speicher). [URTEIL] Anordnung/
      Gewichtung des Blatts — was oben steht, muss das Wichtigste sein
      (Bilanz vor Puls). plain-Theme: gleiche Daten als Tabellen.
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

## Reihenfolge-Empfehlung

L1.1 → L1.2 → L1.3 (die Kartei trägt sofort sichtbar) → L3.1 + L3.3
(kleine Schritte, großer Lebendigkeits-Gewinn, nutzen D8-Infrastruktur)
→ L2.1 + L2.2 (Erinnerungen) → L3.2 (Loren) → L2.3–L2.5 (Traum; profitiert
davon, dass factory_history bis dahin mehr Tage hat) → L1.4/L1.5 → L3.4 →
L3.5 zuletzt (Datenlage klären).

Hinweis Zeitpunkt: nichts hiervon blockiert den Bot-Neustart morgen —
alles ist reine Dashboard-/Lese-Arbeit. Umgekehrt profitieren L2 (Feed-
Untertitel, Traummaterial) und L3 (echte Positionen) stark davon, dass
der Bot wieder läuft und Daten produziert.
