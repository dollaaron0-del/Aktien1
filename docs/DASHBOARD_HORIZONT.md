# Dashboard-Horizont — Ausbau-Roadmap über den Tellerrand

> Erstellt 15.7.2026 auf User-Wunsch: Erweiterungen **unabhängig von der
> Programm-Vorstellung**, für freie Kapazitäten. Kein Zeitplan, keine
> Reihenfolge-Pflicht — jeder Punkt ist einzeln nehmbar. Ergänzt
> `DESIGN_ROADMAP.md` (D0–D7, Vorstellung) und `DESIGN_FABRIK.md` (W1–W5,
> Wimmelbild); ersetzt keins von beiden.
>
> **15.7. unterteilt in Einzelschritte**, damit auch ein günstigeres
> Modell (z.B. Haiku) Punkte sicher abarbeiten kann. Jeder Punkt trägt
> eine Modell-Ampel:
>
> - 🟢 **günstiges Modell geeignet** — mechanisch, Muster existiert,
>   Schritte sind vollständig vorgegeben
> - 🟡 **günstiges Modell mit Vorsicht** — Schritte vorgegeben, aber an
>   markierten Stellen ist Urteilsvermögen nötig; im Zweifel abbrechen
>   und Notiz hinterlassen
> - 🔴 **stärkeres Modell nötig** — Sicherheit/Bot-Nähe/Architektur;
>   NICHT mit günstigem Modell beginnen

---

## Arbeitsanweisung für das abarbeitende Modell (IMMER lesen)

**Erlaubte Pfade:** NUR `dashboard/`, `tests/test_dashboard_*`,
`docs/DASHBOARD_HORIZONT.md`. Alles andere ist tabu — insbesondere
`bot/`, `analyzers/`, `broker/`, `portfolio/` (lesen ja, ändern NIE),
`.env`, systemd, crontab. **Der Bot ist bewusst pausiert — niemals
Services starten/enablen.**

**Muster kopieren statt erfinden:**

| Aufgabe | Muster-Datei |
|---|---|
| SVG-Baustein (reine Funktion) | `dashboard/conveyor.py`, `dashboard/instruments.py` |
| Fabrik-Maschine/Extra | `dashboard/factory/machines.py` (`_machine_extras`) |
| Daten-Leser (fail-open) | `dashboard/factory/state.py` (`_read_*`) |
| Tab-Modul | `dashboard/tabs/factory.py` |
| CSS/Theme-Erweiterung | `dashboard/theme.py` (Namespace `px-`) |
| Modul-Test | `tests/test_dashboard_instruments.py` |
| AppTest mit Fake-Datenquelle | `tests/test_dashboard_led_migration.py` |
| AppTest mit echter isolierter DB | `tests/test_dashboard_factory_tab.py` |

**Eiserne Regeln:**
1. Jeder dynamische Wert in `unsafe_allow_html`/SVG läuft durch
   `html.escape()` — keine Ausnahme.
2. Jede Datenquelle fail-open lesen (`try/except` → leerer/„off"-Zustand).
3. `theme._legacy_css()` und der plain-Modus werden NIE verändert.
4. Neue Animationen brauchen einen `prefers-reduced-motion`-Eintrag.
5. Dateien unter 500 Zeilen halten.

**Standard-Abschluss (gilt für JEDEN Punkt, Kürzel „→ SA"):**
```bash
# 1. Punkt-Tests + betroffene Suiten:
venv/bin/python -m pytest tests/test_dashboard_*.py -q
# 2. Voll-Render beide Modi (je 0 Exceptions Pflicht):
DASHBOARD_THEME=pixel venv/bin/python -c "
from streamlit.testing.v1 import AppTest
at = AppTest.from_file('dashboard/app.py'); at.run(timeout=60)
assert not len(at.exception), [str(e.value) for e in at.exception]; print('OK')"
DASHBOARD_THEME=plain venv/bin/python -c "
from streamlit.testing.v1 import AppTest
at = AppTest.from_file('dashboard/app.py'); at.run(timeout=60)
assert not len(at.exception), [str(e.value) for e in at.exception]; print('OK')"
# 3. Haken in diesem Dokument setzen + kurze Umsetzungs-Notiz.
# 4. Commit (pre-commit-Hook läuft die volle Suite, ~7 Min — im
#    Hintergrund starten und blockierend warten, Muster:
#    nohup git commit -m "..." > /tmp/c.log 2>&1 &  → until-Schleife auf PID).
```

**Abbruch-Regel:** Schlägt derselbe Test nach 2 Fix-Versuchen weiter
fehl, oder verlangt ein Schritt eine Änderung außerhalb der erlaubten
Pfade: STOPPEN, Zwischenstand NICHT committen, unter dem Punkt eine
`> BLOCKIERT:`-Notiz mit dem genauen Fehler hinterlassen.

**Bekannte Fallen:**
- `DecisionLog`/einige Klassen binden ihren DB-Pfad beim ersten Import —
  in Tests Pfade VOR dem Import setzen oder die conftest-Fixtures nutzen
  (`fresh_portfolio` existiert bereits).
- `st.metric()`-Labels, `st.expander()`-Titel, `st.dataframe()`-Zellen
  und `st.text()` rendern KEIN HTML — dort niemals `theme.led()`/HTML
  einsetzen.
- `.streamlit/config.toml` wird nur beim Server-Start gelesen — nichts
  hineinbauen, was zur Laufzeit umschaltbar sein soll.

---

## H1 — Vom Anzeigen zum Bedienen (Leitstand → Steuerpult)

Alle Aktionen: mit Bestätigungs-Dialog, Protokoll in den Activity-Feed,
klarer Anzeige, dass es eine manuelle Dashboard-Aktion war.

- [ ] **H1.1 Pause-Schalter im Dashboard** (M) 🔴
      *Grund für 🔴: schreibt in `system.bot_control` (außerhalb
      dashboard/) bzw. braucht eine saubere Erweiterung dort — Scope-
      Entscheidung + Bot-Nähe. Erst mit starkem Modell die Schnittstelle
      bauen; danach kann die reine UI 🟢 nachgezogen werden.*

- [ ] **H1.2 Ticker-Schnellanalyse an den Docks** (S) 🟢
      Ziel: Ticker-Eingabe im Fabrik-Tab → `user_request_queue` (wie im
      Log-Tab bereits vorhanden — Logik NICHT neu erfinden, aufrufen).
      1. [ ] In `dashboard/tabs/factory.py` unter dem Detail-Panel-Bereich
         ein `st.form("factory_ticker_form")` mit `st.text_input`
         (Label „Werksauftrag: Ticker zur Analyse einwerfen") + Submit.
      2. [ ] Bei Submit: `from analyzers.user_request_queue import
         add_ticker, peek` — exakt wie `dashboard/tabs/log.py` (Zeilen um
         `_req_ticker`) es tut, inkl. „bereits vorgemerkt"-Fall
         (`st.success`-Meldungen übernehmen).
      3. [ ] Test in `tests/test_dashboard_factory_tab.py`: Form
         absenden (AppTest `.text_input[...].set_value("NVDA")` +
         `.run()`), danach `peek()` enthält „NVDA" (Queue-Datei vorher
         per monkeypatch auf tmp_path umbiegen:
         `monkeypatch.setattr(urq_mod, "_FILE", str(tmp_path/"q.json"))`).
      4. [ ] → SA

- [ ] **H1.3 Not-Aus-Reset mit Zwei-Schritt-Bestätigung** (M) 🔴
      *Grund für 🔴: setzt Circuit-Breaker-State zurück (Risiko-Mechanik,
      Datei liegt außerhalb dashboard/). Schnittstellen-Design und
      Sicherheits-Abwägung zuerst mit starkem Modell.*

- [x] **H1.4 Positions-Notizen** (S) 🟡
      Ziel: freies Notizfeld je offener Position, NUR fürs Auge (der Bot
      liest es nicht — das auch in den UI-Text schreiben).
      1. [x] Neues Modul `dashboard/position_notes.py`:
         `class PositionNotes` mit `get(ticker) -> str` und
         `set(ticker, text) -> None`, SQLite unter
         `data/position_notes.db` (Tabelle `notes(ticker TEXT PRIMARY
         KEY, text TEXT, updated TEXT)`). Konstruktor
         `__init__(self, db_path: str | None = None)` — Pfad NICHT als
         Default-Parameter-Konstante binden (bekannte Falle!), sondern
         im Body auflösen.
      2. [x] In `dashboard/tabs/portfolio.py` je Position ein Expander
         „Notiz" mit `st.text_area` + Speichern-Knopf (🟡: vorhandenes
         Positions-Rendering nicht umbauen, nur ergänzen).
      3. [x] Im Lager-Detail-Panel (`dashboard/tabs/factory.py`,
         `_detail_warehouse`) vorhandene Notiz read-only anzeigen.
      4. [x] Tests `tests/test_dashboard_position_notes.py`: get/set
         Roundtrip auf tmp-DB; leere Notiz = „"; HTML in Notiz wird beim
         Anzeigen escaped (Anzeige läuft über `st.text_area`/`st.caption`
         — kein unsafe_allow_html verwenden!).
      5. [x] → SA

      Umgesetzt 15.7.2026: `_render_position_notes()` als eigene,
      isoliert testbare Funktion in `tabs/portfolio.py` (ergänzt die
      bestehende Positionstabelle unverändert), je Position ein
      Expander mit Text-Area + Speichern-Knopf. `_detail_warehouse()`
      im Fabrik-Tab zeigt vorhandene Notizen zusätzlich read-only (nur
      falls eine existiert, kein leerer „Notizen:"-Abschnitt).
      **Nachzug:** `PositionNotes()` legt schon beim Anzeigen (Import +
      Konstruktor) die DB-Datei an — das hätte jeden End-to-End-Test von
      `tabs/portfolio.py`/`tabs/factory.py` in die echte
      `data/position_notes.db` schreiben lassen (gefunden über den
      vollen App-Vollrender-Test in `test_dashboard_kiosk.py`); neue
      autouse-Fixture `_isolate_position_notes` in `conftest.py`
      (gleiches Muster wie `_isolate_factory_history`). 15 neue Tests;
      Verifikation normal/kiosk × pixel/plain je 0 Exceptions.

- [ ] **H1.5 „Was würde der Bot jetzt tun?"-Trockenlauf** (L) 🔴
      *Grund für 🔴: ruft die echte Analyse-Pipeline auf (Kosten-Routing,
      Seiteneffekt-Freiheit muss GARANTIERT sein). Nur stark.*

## H2 — Zeitreise & Replay

- [x] **H2.1 Zustands-Schnappschüsse** (M) 🟢
      Ziel: `read_state()` regelmäßig als JSON-Zeile sichern — Grundlage
      für H2.2/H2.3.
      1. [x] In `dashboard/factory/state.py`:
         `HISTORY_FILE = os.path.join(_DATA_DIR, "factory_history.jsonl")`
         (Modul-Konstante, damit Tests sie monkeypatchen können — Muster
         `_REGIME_FILE`) und
         `def snapshot(state: FactoryState, path: str | None = None) ->
         None`: hängt eine Zeile `{"ts": state.generated_at, "paused":
         state.paused, "machines": {id: {"status": m.status, "tooltip":
         m.tooltip}}}` an (OHNE payload — klein halten). Fail-open.
      2. [x] Deckelung in `snapshot()`: überschreitet die Datei 5 MB,
         älteste Hälfte der Zeilen verwerfen (einfach: Zeilen lesen,
         hintere Hälfte zurückschreiben).
      3. [x] `def read_history(day: str, path: str | None = None) ->
         list[dict]`: alle Zeilen deren `ts` mit `day` (YYYY-MM-DD)
         beginnt; kaputte Zeilen überspringen.
      4. [x] Aufruf: im Fabrik-Tab-Fragment (`tabs/factory.py`) nach
         `read_state()` — aber MAX 1×/10 Min (Modul-Variable
         `_last_snapshot_ts` vergleichen), sonst schreibt der
         60s-Auto-Refresh die Datei voll.
      5. [x] Tests: snapshot+read Roundtrip auf tmp-Datei; Deckelung
         (Datei künstlich >5 MB → schrumpft); kaputte Zeile wird
         übersprungen; Drossel (zwei snapshot-Aufrufe direkt
         nacheinander → nur 1 Zeile).
      6. [x] → SA

      Umgesetzt 15.7.2026: Drossel-Logik als eigene, testbare Funktion
      `_maybe_snapshot(state)` in `tabs/factory.py` (statt Inline-Code im
      `@st.fragment`) — so ist die 10-Minuten-Sperre ohne
      Streamlit-Fragment-Mechanik direkt testbar. `read_history()` gibt
      älteste zuerst zurück. **Wichtiger Nachzug:** neue autouse-Fixture
      `_isolate_factory_history` in `tests/conftest.py` (Muster
      `_isolate_sl_cooldown`/`_isolate_order_log`) — ohne sie hätte JEDER
      Test, der `factory.render()`/`read_state()` end-to-end aufruft
      (z.B. die bestehenden W1.3-Tab-Tests), in die echte
      `data/factory_history.jsonl` geschrieben; beim ersten Suite-Lauf
      genau so aufgefallen und sofort isoliert. 7 neue Tests
      (`tests/test_dashboard_history.py`); Verifikation in allen vier
      Kombinationen (normal/kiosk × pixel/plain) je 0 Exceptions.

- [x] **H2.2 Zeitreise-Regler im Fabrik-Tab** (M, braucht H2.1) 🟡
      1. [x] In `tabs/factory.py` Expander „🕰 Archiv": `st.date_input` +
         `st.select_slider` über die Zeitstempel aus
         `read_history(day)` (leer → Hinweis „keine Aufzeichnung").
      2. [x] Gewählter Eintrag → `FactoryState` rekonstruieren (Status +
         Tooltip reichen; payload-lose Maschinen rendern ohne Extras —
         genau dafür sind die Extras fail-open). `build_scene_svg()`
         damit rendern, DARÜBER unübersehbar
         `st.warning("ARCHIV-ANSICHT — nicht der Live-Zustand")`.
      3. [x] 🟡-Stelle: Live-Szene und Archiv-Szene sauber trennen
         (Archiv NICHT ins 60s-Fragment legen, sonst springt der Regler).
         Im Zweifel: Archiv außerhalb `_scene()` rendern.
      4. [x] Tests: Archiv-Expander rendert mit präparierter
         History-Datei; Warning erscheint; unbekannter Tag → Hinweis.
      5. [x] → SA

      Umgesetzt 15.7.2026: `_render_archive()` in `tabs/factory.py`,
      direkt nach `_scene()` in `render()` aufgerufen (also außerhalb
      des `@st.fragment(run_every="60s")` — der Regler bleibt beim
      Live-Refresh stabil). Neue `state.py`-Bausteine:
      `MACHINE_LABELS` (Zuordnung id→Label, da die history-Zeile
      bewusst kein Label speichert) und
      `reconstruct_from_snapshot(row) -> FactoryState`. 5 neue Tests
      (2 in `test_dashboard_factory_tab.py`, 2 in
      `test_dashboard_history.py` für die Rekonstruktion selbst);
      Verifikation normal/kiosk × pixel/plain je 0 Exceptions.

- [x] **H2.3 Tages-Replay** (M, braucht H2.1) 🟡
      *Hinweis: KEINE Echtzeit-Animation über st.rerun-Schleifen bauen
      (Streamlit-Frickelei) — stattdessen Schieberegler „Uhrzeit" über
      die Snapshots des Tages + Feed-Events bis zu diesem Zeitpunkt im
      Terminal-Stil darunter. Das ist robust und fühlt sich trotzdem wie
      Replay an.*
      1. [x] Zeit-Slider (Werte = Snapshot-Zeitstempel des Tages).
      2. [x] Szene zum gewählten Zeitpunkt (wie H2.2).
      3. [x] Darunter `.px-terminal`-Block mit den Feed-Ereignissen des
         Tages BIS zum Slider-Zeitpunkt (`feed_recent` liefert nur die
         letzten 50 — stattdessen direkt `ActivityFeed`-DB per SQL
         `WHERE ts LIKE 'YYYY-MM-DD%' AND ts <= ?` lesen; read-only).
      4. [x] Tests mit präparierter Feed-DB (Muster
         `test_dashboard_live_tab.py`: `ActivityFeed(db_path=tmp)`).
      5. [x] → SA

      Umgesetzt 15.7.2026: Kein zweiter Slider — der bereits in H2.2
      gebaute Zeitreise-Regler treibt jetzt auch das Replay (genau wie
      im Roadmap-Hinweis „Szene zum gewählten Zeitpunkt (wie H2.2)"
      vorgeschlagen). Neue `state.py`-Funktion
      `read_feed_events_until(day, ts, db_path=None)` liest read-only
      direkt aus der Activity-Feed-DB (eigene sqlite3-Connection, keine
      Änderung an `system/live_status.py` — außerhalb des erlaubten
      Pfads). `tabs/factory.py`s `_render_archive()` heißt jetzt
      „🕰 Archiv & Replay" und zeigt das Terminal (gleiche Icons/Farben
      wie `tabs/live.py`, damit keine zweite Farbsprache entsteht)
      direkt unter der archivierten Szene. 5 neue Tests (3 für die
      Lese-Funktion, 2 AppTest-Integrationstests); Verifikation
      normal/kiosk × pixel/plain je 0 Exceptions.

- [x] **H2.4 Wochen-Vergleich** (S) 🟢
      1. [x] Neues Modul `dashboard/compare.py`:
         `def week_stats(start_day: str, end_day: str) -> dict` —
         aggregiert read-only aus `DecisionLog.funnel(day)` je Tag
         (Summen: total/BUY/SKIP) und `AnalysisLog.get_stats()`-Feldern,
         fail-open (fehlende Tage = 0).
      2. [x] Im Entscheidungen-Tab (`tabs/decisions.py`) Expander
         „Zeitraum-Vergleich": zwei Datums-Paare wählen,
         `st.dataframe` mit Spalten A/B/Δ.
      3. [x] Tests: week_stats gegen präparierte DecisionLog-Einträge
         (bare `DecisionLog()` — conftest bindet die Test-DB).
      4. [x] → SA

      Umgesetzt 15.7.2026 mit einer dokumentierten Abweichung: Punkt 1
      wollte `AnalysisLog.get_stats()`-Felder verwenden, aber diese
      Methode aggregiert über die GESAMTE Tabelle (kein Datumsbereich)
      — eine Erweiterung dort hätte `analyzers/analysis_log.py`
      angefasst, außerhalb des erlaubten Pfads dieser Ausbau-Session.
      Stattdessen liest `week_stats()` `AnalysisLog.get_recent(limit=
      5000)` read-only und filtert clientseitig nach `analyzed_at` —
      liefert Analysen-Anzahl + Ø-Sentiment im Zeitraum, ohne
      `analyzers/` zu verändern. `tabs/decisions.py` zeigt jetzt einen
      „📊 Zeitraum-Vergleich"-Expander mit zwei Datumspaaren (Default:
      letzte 7 Tage vs. die 7 Tage davor) und einer A/B/Δ-Tabelle.
      12 neue Tests (8 in `test_dashboard_compare.py`, 4 in
      `test_dashboard_decisions_conveyor.py`); Verifikation normal/
      kiosk × pixel/plain je 0 Exceptions.

## H3 — Erklärbarkeit

- [x] **H3.1 „Warum nicht?"-Explorer** (M) 🟡
      Ziel: Ticker wählen → Gate-Strecke grün/rot mit echten Gründen.
      1. [x] Daten-Funktion in `dashboard/why_not.py`:
         `def gate_trail(ticker: str, day: str | None = None) ->
         list[dict]` — liest die DecisionLog-Einträge des Tickers
         (neuester Tag), extrahiert `skip_reasons`/`action`/`reason` und
         mappt auf eine feste Gate-Reihenfolge
         (`_GATES = ("Liquidität", "Breadth", "SL-Cooldown",
         "Korrelation", "Kapital", "Signal")` — 🟡: die echten
         reason-Strings im decision_log VORHER per SQL anschauen und das
         Mapping daran ausrichten, nicht raten; unbekannte Gründe in
         einen „Sonstiges"-Eintrag).
      2. [x] SVG-Weichenstrecke (Muster conveyor.py): je Gate ein Kasten
         grün (passiert) / rot (geblockt, mit Grund-Text) / grau (nicht
         erreicht).
      3. [x] Einbau als Expander im Entscheidungen-Tab mit
         Ticker-Selectbox (Werte = heutige Funnel-Ticker).
      4. [x] Tests: gate_trail mit präparierten Log-Einträgen (geblockt
         auf Stufe 2 → Stufe 3+ grau); SVG escaped Grund-Texte.
      5. [x] → SA

      Umgesetzt 15.7.2026, Investigations-Schritt bestätigt eine andere
      Realität als der ursprüngliche Entwurf: `_GATES` existiert nicht —
      die reale, bereits bestehende Kategorisierung ist
      `analyzers.decision_log._REASON_BUCKETS` (12 Buckets: daten_gate,
      kein_kaufsignal, unter_schwelle, zu_wenige_quellen,
      max_positionen, earnings_sperre, korrelation, liquiditaet,
      lernfilter_avoid, positionsgroesse, tagesverlust, kein_kurs) —
      exakt dieselbe, die der Förderband-Funnel (D4) schon nutzt.
      `gate_trail()` importiert diese Bucket-Reihenfolge und die Labels
      aus `dashboard.conveyor._reason_label` statt sie zu duplizieren.
      Dokumentierte Einschränkung: die Bucket-Reihenfolge ist die
      Regex-Prüfreihenfolge, nicht zwingend die exakte Gate-Ausführungs-
      Reihenfolge der echten Strategie (liegt in `bot/`, außerhalb des
      Dashboard-Scopes) — für BUY/HOLD/SELL gilt die komplette Strecke
      als "passiert". Einbau im Entscheidungen-Tab direkt unter den
      Skip-Grund-Balken. **Nachzug beim Testen:** Test-Ticker AAPL/
      MSFT/NVDA/TSLA kollidierten mit `_seed()`-Daten aus
      `test_dashboard_decisions_conveyor.py` (geteilte Test-Decision-Log-
      DB über die ganze Session) — `gate_trail()` nimmt den NEUESTEN
      Eintrag, ein gleichnamiger Ticker aus einer anderen Testdatei ohne
      explizites `decided_at` (= "jetzt") gewann daher gegen den eigens
      gesetzten Test-Zeitstempel; behoben durch eigene Test-Ticker
      (ZWN1–5). 8 Modul-Tests + 3 Tab-Tests; Verifikation normal/kiosk ×
      pixel/plain je 0 Exceptions (Explorer selbst rendert in der echten
      Produktions-DB aktuell nicht, weil `decision_log.db` leer ist —
      Bot pausiert, fail-open korrekt).

- [x] **H3.2 Entscheidungs-Genealogie** (M) 🟡
      1. [x] Daten-Funktion `dashboard/genealogy.py`:
         `def order_lineage(order_id) -> dict` — Order (order_log) →
         zeitlich passender analysis_log-Eintrag desselben Tickers →
         dessen `sources_breakdown`/`provenance`. Alles read-only,
         fail-open; fehlende Stufe = None (🟡: Zuordnung Order→Analyse
         über Ticker + nächstliegender Zeitstempel davor — dokumentieren,
         dass das eine Heuristik ist).
      2. [x] Darstellung: dreistufiger Stammbaum als SVG (Order-Kasten →
         Analyse-Kasten → Quellen-Kästen), Klick nicht nötig — Tooltips
         (`<title>`, Muster machines.py) reichen in v1.
      3. [x] Einbau im Trades-Tab als Expander je Order (nur pixel).
      4. [x] Tests: lineage mit präparierten DBs; None-Stufen rendern
         als „(keine Analyse gefunden)".
      5. [x] → SA

      Umgesetzt 15.7.2026: `order_lineage()`/`lineage_svg()` in
      `dashboard/genealogy.py`, mit eigenen read-only sqlite3-Connections
      auf `broker.order_log.DB_PATH`/`analyzers.analysis_log.DB_PATH`
      (kein Eingriff in die beiden Module). Einbau als
      „🧬 Entscheidungs-Genealogie" im Tab „Trades & Lernen" (nicht in
      `tabs/live.py`, wo Orders technisch auch schon sichtbar sind —
      der Roadmap-Wortlaut wollte es explizit im Trades-Tab, thematisch
      näher an „verstehen" statt „live überwachen"). **Zwei Nachzüge
      beim Testen:** (a) `_render_genealogy()` musste denselben
      DB-Pfad wie der bereits geladene `OrderLog` an `order_lineage()`
      weiterreichen (`_order_log._db_path`) — sonst hätte die
      Genealogie-Funktion in Tests gegen die ISOLIERTE Order-Test-DB,
      aber trotzdem gegen die ECHTE `analysis_log.db` verglichen (Fund
      beim ersten Testlauf, nicht vorher offensichtlich). (b) ein
      Plain-Modus-Test mit Ticker "NVDA" fand tatsächlich eine reale
      Analyse in der echten Produktions-DB (bewusst NICHT
      pfad-isoliert, da die Funktion genau das in Produktion tun soll)
      — Test auf einen garantiert synthetischen Ticker umgestellt.
      10 Modul-Tests + 3 Tab-Tests; Verifikation normal/kiosk ×
      pixel/plain je 0 Exceptions.

- [ ] **H3.3 Kalibrier-Kurve live** (S) 🟢
      1. [ ] Daten: `ExperienceStore` (Muster `_read_lab()` in
         factory/state.py) — je Konfidenz-Stufe (HIGH/MEDIUM/LOW)
         Trefferquote der gelabelten Trades ziehen. Wenn der Store dafür
         schon eine Methode hat, benutzen; sonst read-only SQL auf
         `data/experience.db` (Schema vorher mit `.schema` ansehen).
      2. [ ] Altair-Balken/Punkt-Chart (Theme „pixel" ist registriert,
         KEINE eigenen Farben hardcoden — D2.3-Regel) im Lern-Tab bzw.
         Qualitätslabor-Detail-Panel; n<20 → `st.caption`-Warnband
         „Stichprobe dünn".
      3. [ ] Tests: Aggregation mit präparierter tmp-DB; Warnband-Logik.
      4. [ ] → SA

## H4 — Lern-Fortschritt sichtbar

- [x] **H4.1 Thesen-Board** (M) 🟢
      1. [x] Datenquelle ansehen: `analyzers/thesis_verdict.py` bzw. die
         Registry-Datei, die es liest (read-only!). Je These:
         Name, Status, Trades gesammelt / nötig.
      2. [x] Neues Panel im Lern-/Strategie-Tab: je These eine Zeile —
         `st.progress(min(1.0, n/nötig))` + Status-Plakette
         (`theme.led`: PROVEN=ok, PENDING=warn, FALSIFIED=err).
      3. [x] Leerzustand ehrlich: „Noch keine These aktiv — Kriterien:
         150 Trades / 24 Monate" (Zahlen aus dem Modul ziehen, nicht
         hartkodieren).
      4. [x] Tests: präparierte Registry-Datei → Balken/Plaketten;
         fehlende Datei → Leerzustand ohne Exception.
      5. [x] → SA

      Umgesetzt 15.7.2026 mit zwei dokumentierten Korrekturen: (a) die
      Registry kennt nur PENDING/PROVEN/ABANDONED — kein „FALSIFIED",
      wie der ursprüngliche Entwurf annahm; (b) die Registry speichert
      KEINEN laufenden Trade-Zähler — `n_trades` entsteht erst
      transient in `evaluate()` (braucht echte ExperienceStore-Trades +
      Bootstrap-Statistik aus scripts/track_record.py, außerhalb des
      Dashboard-Scopes). Neues Modul `dashboard/thesis_board.py` zeigt
      darum bis zum ersten Verdikt den ZEIT-Fortschritt
      (`started_at` vs. `time_budget_months`, allein aus der Registry
      berechenbar) statt eines erfundenen Trade-Fortschritts; sobald
      ein Verdikt gefallen ist, erscheint die reale Trade-Zahl aus dem
      persistierten `verdict_reason`-Text. Einbau im Tab „Trades &
      Lernen" (`tabs/trades.py`), direkt nach den Performance-
      Kennzahlen. **Echter Fund beim Verifizieren:** die Produktions-
      Registry enthält bereits eine reale These
      (`mechanical_baseline`, seit 12.7.2026, PENDING) — das Board
      zeigt sie im echten Voll-Render sofort live an. 10 neue Tests (7
      Modul-Tests, 3 Tab-Tests); Verifikation normal/kiosk × pixel/
      plain je 0 Exceptions.

- [x] **H4.2 Regime-Landkarte** (M) 🟡 — **BLOCKIERT, siehe Befund**
      1. [x] Datenquelle: per-Regime-Kalibrierung (Lern-Stack 2.7.) —
         zuerst die Speicherform finden (`grep -rn "regime"
         analyzers/calibration*` / `data/calibration.json` ansehen).
         🟡: Wenn die Struktur nicht eindeutig je (Regime × Stufe) eine
         Trefferquote hergibt, BLOCKIERT-Notiz statt Bastelei.
      2. [ ] Matrix als Altair-Heatmap (Regime-Zeilen × Konfidenz-
         Spalten, Zellwert = Trefferquote, n klein → Zelle grau).
      3. [ ] Tests: Aggregat aus präpariertem JSON; Grau-Regel.
      4. [ ] → SA

      > BLOCKIERT 15.7.2026: `analyzers/calibration.py` speichert
      > Regime und Konfidenz als ZWEI GETRENNTE, unabhängige
      > Dimensionen (`data/calibration.json`: `tables.regime`,
      > `tables.confidence` — je ein flaches Dict, KEINE gemeinsame
      > (Regime × Konfidenz)-Kreuztabelle existiert). Geprüft an der
      > echten Produktionsdatei: `tables.regime` hat sogar nur EINEN
      > Eintrag (`BULL`, n=347) — alle 347 gelabelten Trades stammen
      > aus dem einen Bullenjahr im Datensatz (deckt sich mit dem
      > bereits bekannten Befund „mechanischer baseline_swing schlägt
      > Buy&Hold NICHT" aus dem Paper-Forward-Erstbefund). Eine
      > Regime×Konfidenz-Matrix zu bauen hieße, zwei unabhängig
      > berechnete Verteilungen als gemeinsame vorzutäuschen —
      > statistisch irreführend, kein Software-Bug zum Beheben.
      > Eine ECHTE gemeinsame Kalibrierung bräuchte eine neue,
      > zweidimensionale Bucket-Dimension in `analyzers/calibration.py`
      > selbst (`_DIMENSIONS` um z.B. `"regime_confidence"` erweitern)
      > — das ist ein Analyzer-Change, außerhalb des für diese
      > Ausbau-Session erlaubten Pfads (nur `dashboard/`). Alternative,
      > ehrliche Mini-Version wäre zwei GETRENNTE Balkendiagramme
      > (Regime-Trefferquote, Konfidenz-Trefferquote nebeneinander,
      > nicht verschränkt) — das wäre kein „H4.2" mehr, sondern ein
      > neuer, kleinerer Punkt. Nicht umgesetzt; auf Rückmeldung
      > wartend, ob (a) die echte Erweiterung in `analyzers/`
      > gewünscht ist, oder (b) die ehrliche Zwei-Balken-Mini-Version
      > reicht.

- [ ] **H4.3 Paper-Forward-Fieberkurve** (S) 🟢
      1. [ ] Datenquelle: `data/paper_forward.json` (existiert; Struktur
         vorher ansehen). Zeitreihe Strategie vs. Benchmark extrahieren.
      2. [ ] Altair-Liniendiagramm im Strategie-Tab; solange n Trades
         < 30: halbtransparentes Warnband + Caption „Bilanz statistisch
         dünn (n=…)" — die ehrliche Darstellung ist der Punkt.
      3. [ ] Tests: präparierte JSON → Chart-Daten korrekt; Warnband-
         Schwelle.
      4. [ ] → SA

## H5 — Fernblick & Weitergabe

- [x] **H5.1 Wochen-Report-Export** (M) 🟡
      1. [x] Neues Modul `dashboard/report.py`:
         `def build_weekly_html(end_day: str | None = None) -> str` —
         in sich geschlossenes HTML (Inline-CSS aus PALETTE, KEINE
         externen Ressourcen): KPI-Zahlen, Funnel-Summen der Woche
         (H2.4-Funktion wiederverwenden, falls schon gebaut — sonst
         Mini-Aggregat inline), Fabrik-SVG (`render_scene()`), letzte
         10 Entscheidungen. ALLES escaped.
      2. [x] Einbau: `st.download_button("Wochen-Report (HTML)",
         data=..., file_name=f"report_{end_day}.html",
         mime="text/html")` im Portfolio-Tab.
      3. [x] Tests: build_weekly_html liefert `<html`-Dokument ohne
         `http://`/`https://`-Referenzen (Selbstständigkeits-Check per
         assert), escaped Beispiel-Injection.
      4. [x] → SA

      Umgesetzt 15.7.2026: `build_weekly_html()` nutzt `week_stats()`
      (H2.4, wie vorgeschlagen wiederverwendet statt neu gebaut),
      `dashboard.factory.render_scene()` für die Fabrik-Momentaufnahme
      und die letzten 10 Entscheidungen aus `DecisionLog.get_recent()`
      — alles in einem einzigen, eigenständigen HTML-String mit
      Inline-CSS aus der PALETTE. **Test-Nachzug:** der ursprüngliche
      Selbstständigkeits-Check „kein `http://` im Dokument" war zu
      strikt — die SVG-`xmlns="http://www.w3.org/2000/svg"`-Deklaration
      enthält legitim „http://", löst aber nie einen Netzwerk-Request
      aus (reiner XML-Namensraum-Bezeichner). Test umgestellt auf einen
      gezielten Check (kein `<link>`/`<script src>`/`@import`/`url(http`,
      keine `src=`/`href=`-Attribute mit `http(s)://`). Download-Knopf
      „📄 Wochen-Report (HTML)" im Portfolio-Tab ganz oben. 9 neue
      Tests; Verifikation normal/kiosk × pixel/plain je 0 Exceptions,
      Knopf im echten Voll-Render bestätigt sichtbar.

- [ ] **H5.2 [USER] Zuschauer-Modus** (M) 🔴
      *Grund für 🔴: Auth/Sicherheit (Settings-Tab kann .env-Keys lesen
      und schreiben). Fehler hier = echtes Leck. Erst User-Entscheid, ob
      Fremd-Einblick überhaupt gewollt ist, dann starkes Modell.*

- [ ] **H5.3 Telegram-Rückverweis** (S) 🔴
      *Grund für 🔴: ändert den Telegram-Versand (außerhalb dashboard/).
      Kleiner Eingriff, aber falscher Ort für ein günstiges Modell mit
      dieser Pfad-Beschränkung.*

## H6 — Plattform

- [x] **H6.1 Kiosk-Modus** (S) 🟢 ← **empfohlener Einstiegspunkt**
      1. [x] In `dashboard/app.py` GANZ OBEN nach `require_login()`:
         `if st.query_params.get("kiosk") == "1":` → nur
         `dashboard.tabs.factory.render(_ctx)` + minimale Kopfzeile
         (Uhrzeit, `theme.led`-Ampel), dann `st.stop()`. KEINE Tabs,
         keine KPI-Leiste. (Das Fragment im Fabrik-Tab refresht schon
         alle 60s — nichts extra bauen.)
      2. [x] CSS-Feinschliff in theme.py: unter `?kiosk=1` Streamlit-
         Header/Toolbar ausblenden — eigene Klasse `.px-kiosk` am
         Wrapper-Div + `[data-testid="stHeader"] {display:none}` NUR in
         einem zusätzlichen Style-Block, den app.py im Kiosk-Zweig
         injiziert (NICHT global in _base_css()).
      3. [x] Tests `tests/test_dashboard_kiosk.py`: AppTest mit
         `at.query_params["kiosk"]="1"` → Fabrik-SVG da, `len(at.tabs)
         == 0`, 0 Exceptions; ohne Param → Tabs wie bisher (Anzahl >10).
      4. [x] → SA

      Umgesetzt 15.7.2026: Kiosk-Check sitzt bewusst NICHT ganz oben,
      sondern direkt nach dem bereits vorhandenen Ampel-Block (Zeile
      ~367) — Logo/Titel/Pause-Banner/Live-Status/Ampel sind zu diesem
      Zeitpunkt schon gerendert (kein Doppel-Code für die Ampel-Logik
      nötig, `_ibkr_gateway_dot()`/`_claude_cost_dot()`/
      `_circuit_breaker_dot()` existieren erst ab dort). Direkt danach
      `st.stop()` — KPI-Leiste, Instrumente, Ticker und alle Tabs werden
      nie erreicht. `factory.render(None)` funktioniert, weil
      `tabs/factory.py`s `render(ctx)` den `ctx`-Parameter gar nicht
      liest. Streamlit-Chrome-CSS (`stHeader`/`stToolbar`/`MainMenu`/
      `footer`) wird unabhängig vom Theme-Modus ausgeblendet (Kiosk ist
      kein Theme-Zustand). 3 neue Tests; Verifikation lief in allen
      vier Kombinationen (normal/kiosk × pixel/plain) — je 0 Exceptions.

- [ ] **H6.2 Handy-Kompaktansicht** (M) 🟢
      Wie H6.1, zweiter Zweig `?mobile=1`: Depotwert + Tages-P&L
      (`st.metric`), Ampel-Zeile, Fabrik-SVG (skaliert eh auf 100%),
      Terminal-Feed (letzte 10). Gleiche Testform wie H6.1.

- [ ] **H6.3 [USER] Canvas/WebGL-Fabrik** (L) 🔴
      *Nur falls SVG nach W5-Assets + Replay messbar ruckelt (Kriterium
      in DESIGN_FABRIK W5.4). Evaluation + Architektur = stark.*

- [x] **H6.4 Zweit-Theme „Blaupause"** (S) 🟡
      1. [x] `theme.py`: `PALETTE_BLUEPRINT` (weiß/hellblau auf
         Blaupausen-Blau, z.B. bg #0B2A4A, Linien #E8F1FF) und
         `DASHBOARD_THEME=blueprint` als dritter Modus:
         `is_enabled()` bleibt True, aber `PALETTE`-Auflösung wird eine
         Funktion `palette()` (🟡: ALLE `PALETTE[`-Zugriffe der
         dashboard/-Module auf `palette()[` umstellen — mechanisch, aber
         viele Stellen; grep-Liste zuerst erstellen und im Commit-Text
         dokumentieren).
      2. [x] Tests: blueprint aktiv → andere Hex-Werte im CSS; pixel
         unverändert (Regression: bestehende Tests bleiben grün).
      3. [x] → SA

      **Bewusste Abweichung vom Entwurf, dokumentiert statt blind
      umgesetzt:** Schritt 1 wollte `PALETTE` durch eine `palette()`-
      Funktion ersetzen und ALLE Zugriffsstellen im dashboard/-Baum
      mechanisch umschreiben. Grep-Liste zuerst erstellt (wie
      vorgeschrieben): 8 Dateien importieren `PALETTE`
      (`conveyor.py`, `genealogy.py`, `instruments.py`, `report.py`,
      `why_not.py`, `tabs/factory.py`, `factory/scene.py`,
      `factory/machines.py`), 35+ `PALETTE[...]`-Indexierungen. Statt
      diese alle umzuschreiben (hohes Risiko, eine Stelle zu übersehen):
      `PALETTE` bleibt ein Name, ist aber jetzt eine
      `_PaletteProxy(Mapping)`-Instanz, die bei JEDEM Zugriff
      (`__getitem__`) live gegen `DASHBOARD_THEME` auflöst — `PALETTE[
      "bg"]`, `p = PALETTE; p["bg"]`, `.items()` funktionieren an ALLEN
      bestehenden Stellen unverändert, keine einzige musste angefasst
      werden. Per AST-Analyse (nicht nur grep, um Mehrzeiler sicher zu
      erfassen) EINE echte Ausnahme gefunden: `factory/machines.py`s
      `_STATUS_COLOR` fror PALETTE-Werte in einem Modul-level-Dict beim
      Import ein — zu einer Funktion `_status_color()` gemacht, die bei
      jedem Aufruf neu auflöst. **Bekannte, nicht behobene
      Einschränkung:** Plotly-Chart-Templates werden weiterhin einmalig
      beim ersten `register_chart_themes()`-Aufruf eingefroren (anders
      als Altair, das eine Funktion statt eines fertigen Objekts
      registriert) — in der Praxis unkritisch, da `DASHBOARD_THEME`
      eine Server-ENV-Variable ohne Laufzeit-Umschalter ist, aber im
      Code dokumentiert. 8 neue Tests; Verifikation in ALLEN VIER
      Kombinationen (pixel/plain/blueprint × normal/kiosk) je
      0 Exceptions — Blueprint-Farbe `#0B2A4A` im echten Voll-Render
      bestätigt durchgängig sichtbar, keine Pixel-Farbreste.

## H7 — Charakter weiter

- [x] **H7.1 Werksleiter-Stimmung** (S) 🟢
      1. [x] `dashboard/instruments.py`: `def face_svg(score: float,
         label: str = "") -> str` — Pixel-Gesicht 3 Zustände (>75
         Lächeln + grüne LED-Augen, 40–75 neutral, <40 Sorgenfalte +
         amber), Score None → graues Schlaf-Gesicht („Zzz").
      2. [x] Einbau Header-Zeile app.py (neben Ampel): Score aus
         `BotScorer().get()` (Muster: `tabs/portfolio.py` um Zeile 147),
         fail-open.
      3. [x] Tests: 4 Zustände liefern unterscheidbares SVG; Label
         escaped.
      4. [x] → SA

      Umgesetzt 15.7.2026: `face_svg()` unterscheidet die vier Zustände
      über Mund-Form (Linie neutral, `<path>` gebogen für
      Lächeln/Sorge — Kontrollpunkt unter bzw. über den Endpunkten) und
      Augenfarbe (neon_green/text/amber/text_muted); `score=None` UND
      nicht-numerische Eingaben (fail-open) zeigen das Schlaf-Gesicht
      mit „Zzz". Einbau in `app.py` direkt neben der Gesundheits-Ampel
      (eigene Spalte, `st.columns([5, 1])`), Score-Abruf und Render
      jeweils in eigenem try/except, damit ein Fehler hier nie die
      Ampel selbst verschluckt — nur pixel, plain unverändert. 8 neue
      Tests (`tests/test_dashboard_instruments.py`); Verifikation
      normal/kiosk × pixel/plain je 0 Exceptions, Gesicht im echten
      Voll-Render bestätigt sichtbar.

- [ ] **H7.2 Plaketten-Wand** (M) 🟢
      1. [ ] `dashboard/achievements.py`: `CATALOG` fester Plaketten
         (id, Titel, Prüf-Funktion) — alle Prüfungen read-only auf
         bestehende Daten: „Erster Live-Trade" (`ExperienceStore.stats()
         ['live'] > 0`), „100 gelabelte Trades" (labeled ≥ 100),
         „Erste PROVEN-These" (Registry), „30 Tage ohne Not-Aus"
         (CircuitBreaker-State), „1 Jahr Betrieb" (ältester
         analysis_log-Eintrag).
      2. [ ] `def unlocked() -> list[dict]`: prüft alle, MERKT sich
         einmal Erreichtes in `data/achievements.json` (einmal erreicht
         = bleibt, auch wenn die Bedingung später wieder kippt — das ist
         der Sinn einer Plakette).
      3. [ ] Anzeige: Expander „🏅 Plaketten-Wand" im Fabrik-Tab —
         erreichte als `theme.panel` mit Datum, offene ausgegraut mit
         Bedingung.
      4. [ ] Tests: Prüf-Funktionen mit präparierten Quellen; Merk-Logik
         (Bedingung kippt zurück → Plakette bleibt); fehlende Quellen →
         fail-open offen statt Crash.
      5. [ ] → SA

- [ ] **H7.3 Schichtbuch** (M) 🟡
      1. [ ] `dashboard/logbook.py`: `def write_entry(day: str) -> str` —
         Feed-Events des Tages zusammenfassen. Erst der ehrliche
         Fallback OHNE LLM: aus den Events einen 3-Satz-Text nach festen
         Regeln bauen („{n} Analysen, {m} Trades. {Besonderheit}.").
         Ablage `data/logbook.jsonl` (einmal je Tag).
      2. [ ] OPTIONAL dahinter (🟡): wenn der lokale Ollama erreichbar
         ist (bestehenden Client aus dem Projekt verwenden, NIEMALS
         Claude-API — Kostenregel!), den Regel-Text durch eine schönere
         3-Satz-Prosa ersetzen; Ollama down → Regel-Text bleibt. Timeout
         kurz (5s), fail-open.
      3. [ ] Anzeige: „📖 Schichtbuch" als blätterbarer Expander
         (Datums-Selectbox) im Fabrik- oder Live-Tab.
      4. [ ] Tests: Regel-Text aus präparierten Events; Einmal-je-Tag-
         Logik; Ollama-Zweig gemockt (down → Fallback).
      5. [ ] → SA

---

## Priorisierungs-Hilfe (wenn Kapazität da ist)

| Rang | Punkt | Ampel | Warum zuerst |
|------|-------|-------|--------------|
| 1 | H6.1 Kiosk-Modus | 🟢 | S-Aufwand, sofort sichtbar, perfekter Test für das günstige Modell |
| 2 | H7.1 Werksleiter-Stimmung | 🟢 | S, reines SVG-Muster-Kopieren |
| 3 | H2.1 Schnappschüsse | 🟢 | Grundlage, macht H2.2/H2.3 billig |
| 4 | H4.1 Thesen-Board | 🟢 | verbindet Dashboard mit dem Nordstern |
| 5 | H3.1 „Warum nicht?" | 🟡 | häufigste echte Frage; Mapping-Schritt braucht Sorgfalt |

**Empfehlung zur Arbeitsteilung:** 🟢-Punkte einzeln an das günstige
Modell geben (ein Punkt = eine Sitzung, Arbeitsanweisung oben gilt);
🟡 nur mit wachem Blick aufs Ergebnis; 🔴 (H1.1, H1.3, H1.5, H5.2,
H5.3, H6.3) für eine Sitzung mit starkem Modell aufheben.
