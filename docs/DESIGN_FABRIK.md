# Fabrik-Wimmelbild — Detail-Tasks (Vision W, NACH der Vorführung)

Stand: 15.7.2026. Feingranulare Zerlegung von Vision W aus
`docs/DESIGN_ROADMAP.md` — geschnitten für die Abarbeitung durch ein
günstigeres Modell. **Es gilt das komplette Arbeitsprotokoll aus
`docs/DESIGN_ROADMAP.md`** (ein Task pro Durchgang, Pfad-Grenzen,
Pflicht-Verifikation pixel+plain, Commit-Regeln, `> OFFEN:` statt raten,
`html.escape()` für alles Dynamische). Zusätzlich für die Fabrik:

- **Fail-open ist Gesetz:** Jeder Daten-Leser fängt JEDE Exception und
  liefert dann Status `off` — die Szene darf nie crashen, egal welche DB
  fehlt. Kein Leser macht Netzwerk-Calls (einzige Ausnahme: der
  Gateway-Socket-Check, 0.4s Timeout, wie die bestehende Ampel).
- **Voraussetzungen:** D0–D2 sind fertig (theme.py existiert). Blöcke
  W1→W2→W3→W4 in Reihenfolge; W5 parallel zu W4 möglich.
- Commit-Format: `feat(dashboard): <was> (Fabrik W1.2)`.

## Festgelegte Bau-Entscheidungen (verwenden, nicht neu entscheiden)

**Struktur** (Paket statt Einzeldatei — 500-Zeilen-Regel):

```
dashboard/factory/__init__.py   # re-exportiert render_scene()
dashboard/factory/state.py      # Daten-Leser → FactoryState (pure, testbar)
dashboard/factory/scene.py      # FactoryState → SVG-String (pure, testbar)
dashboard/factory/machines.py   # je Maschine ein SVG-Baustein (Skelett-Form)
dashboard/tabs/factory.py       # Tab-Renderer (def render(ctx), wie andere Tabs)
tests/test_dashboard_factory.py # Tests für state+scene+machines
```

**Maschinen-IDs** (fix, überall identisch verwenden):
`docks` · `analyzer_claude` · `analyzer_ollama` · `conveyor` · `warehouse` ·
`breaker` · `gate` · `weather` · `lab` · `backup_bot` · `clock`

**Datenmodell** (`state.py`):

```python
@dataclass
class MachineState:
    id: str
    label: str                 # Anzeigename, z.B. "IB-Verladetor"
    status: str                # ok | warn | err | off | active
    tooltip: list[str]         # Zeilen mit ECHTEN Zahlen (W3 nutzt das)
    payload: dict = field(default_factory=dict)  # maschinenspezifisch

@dataclass
class FactoryState:
    machines: dict[str, MachineState]
    paused: bool               # system.bot_control.is_paused()
    generated_at: str          # ISO-Zeitstempel

def read_state() -> FactoryState   # ruft alle Leser, jeder einzeln fail-open
```

**Datenquellen je Maschine** (alle existieren; Leser-Funktion je Maschine
`_read_<id>() -> MachineState`):

| id | Quelle | Status-Logik |
|----|--------|--------------|
| docks | `AnalysisLog().source_health()` | healthy>0→ok, nur weak→warn, alles dead/leer→off; payload: dead/weak/healthy-Listen |
| analyzer_claude / analyzer_ollama | letzte 50 Zeilen `analysis_log` → `provenance.model_route` zählen (`claude*` vs `ollama*`) | Anteil>0 heute→active, sonst off |
| conveyor | `DecisionLog().funnel(<heute>)` | total>0→active, sonst off; payload: das Funnel-Dict (D4-Wiederverwendung) |
| warehouse | `Portfolio().all_positions()` | >0 Positionen→ok, 0→off; payload: {ticker: shares}; KEIN Preis-Fetch (P&L-Farbe erst, wenn ohnehin Preise im Kontext) |
| breaker | `portfolio.circuit_breaker.CircuitBreaker().status(total)`, total aus Portfolio ohne Live-Preise (entry-Preise reichen für den Zustand) | triggered→err, sonst ok |
| gate | Socket-Connect `config.ibkr_host:ibkr_port`, timeout 0.4s | erreichbar→ok, sonst err |
| weather | `data/current_regime.json` (json.load) | BULL→ok, NEUTRAL→warn, BEAR/CRISIS→err; payload: regime-String |
| lab | `ExperienceStore().stats()` | labeled>0→ok; payload: labeled/wins/losses |
| backup_bot | neueste Datei in `backups/` | mtime<36h→ok, <8 Tage→warn, sonst err; fehlt Ordner→off |
| clock | `system.live_status.read_status()` | state==cycle→active, idle mit next_run→ok, sonst off; payload: phase/next_run |

**Szene** (`scene.py`): `build_scene_svg(state: FactoryState) -> str`,
`viewBox="0 0 1200 675"`. Layout-Konstante (Maschine → x,y,w,h) — Werte fix:

```python
LAYOUT = {
    "clock":           (500,  20, 200,  70),
    "weather":         (950,  20, 220,  90),
    "docks":           ( 20, 130, 180, 420),
    "analyzer_claude": (280, 150, 200, 130),
    "analyzer_ollama": (520, 150, 160, 110),
    "conveyor":        (240, 340, 620, 120),
    "warehouse":       (900, 250, 270, 220),
    "gate":            (930, 500, 240, 120),
    "lab":             (620, 500, 260, 120),
    "breaker":         (380, 510, 180, 100),
    "backup_bot":      ( 60, 580, 200,  80),
}
```

Status→Farbe aus `theme.PALETTE`: ok→neon_green, warn→amber, err→red,
off→border, active→cobalt. Skelett-Form je Maschine (machines.py): Rechteck
in bg_panel mit border, Label in VT323, Status-LED-Kreis oben rechts —
bewusst schlicht, W5 ersetzt die Formen später durch Pixel-Art.

**Tab:** neuer Tab „🏭 Fabrik" ans ENDE der `st.tabs([...])`-Liste
(`app.py` ~Zeile 424 — Variable `tab_factory` ergänzen). Szene in einem
`@st.fragment(run_every="60s")`-Block (Muster existiert: `app.py:556`).
Fokus-Navigation über `st.query_params["factory"]`.

---

## W1 — Hallen-Skelett

- [x] **W1.1 Paket + Datenmodell** — `dashboard/factory/state.py`:
      `MachineState`/`FactoryState`, `read_state()` mit allen elf Lesern
      (jeder einzeln fail-open zu `status="off"`, plus ein äußeres try/except
      in `read_state()` selbst als zweite Sicherheitsnetz-Schicht),
      `paused` aus `system.bot_control.is_paused()`. Detail-Leser gegen
      echte (isolierte) Datenquellen getestet: `conveyor` über den echten
      `DecisionLog()`-Pfad, `warehouse` über die bestehende
      `fresh_portfolio`-Fixture, `weather` über einen patchbaren
      `_REGIME_FILE`-Modulkonstante (Analog zu `_BACKUPS_DIR`), `gate` gegen
      einen echten unerreichbaren Port.
- [x] **W1.2 SVG-Bausteine** — `machines.py::machine_box()` (Skelett-Box,
      Status-LED, `<title>`-Tooltip aus `MachineState.tooltip`, alles
      escaped) + `scene.py::build_scene_svg()` (festes `LAYOUT`-Dict,
      Hallen-Rahmen + Boden). `dashboard/factory/__init__.py` exportiert
      `render_scene()` als Bequemlichkeits-Helfer.
- [x] **W1.3 Tab einbauen** — `dashboard/tabs/factory.py` (Tab „🏭 Fabrik"
      ans Ende von `app.py`s `st.tabs()`-Liste), Szene in einem
      `@st.fragment(run_every="60s")`-Block. Rendert bewusst in BEIDEN
      Theme-Modi (hängt nur an `theme.PALETTE`, nicht an `is_enabled()` —
      die Fabrik ist Teil des Pixel-Zielbilds selbst, kein optionaler
      Zusatz). Verifikation pixel+plain OK.
- [x] **W1.4 Legende + Pausiert-Banner** — Farb-Legende als Caption unter
      der Szene, `.px-panel`-Banner bei `state.paused` (Bot ist aktuell
      pausiert → im Test deterministisch sichtbar UND live im Dashboard
      sichtbar). 25 neue Tests (`test_dashboard_factory.py` +
      `test_dashboard_factory_tab.py`), Verifikation pixel+plain OK.

## W2 — Leben (zustandsgetriebene Animation)

- [x] **W2.1 Keyframes + Aktiv-Klassen** — `fx-belt-run` (Förderband,
      teilt sich das Keyframe mit D4.3s `px-belt-anim`), `fx-blink`
      (LED-Puls, teilt sich `px-blink` mit den bestehenden LED-Punkten),
      `fx-smoke` (drei versetzte Rauch-Kreise über den Analysatoren, neues
      Keyframe `fx-smoke-rise`). Alle drei nur angehängt, wenn Maschinen-Typ
      + Status passen (`_activity_overlay()` in machines.py, getrennt von
      der generischen `machine_box()`); `@media (prefers-reduced-motion:
      reduce)` deaktiviert alle drei zusammen mit den bestehenden.
- [x] **W2.2 Rampen-Aktivität** — `_dock_slots()`: ein Slot je Collector-
      Quelle aus `source_health()` (healthy/weak/dead → neon_green/amber/
      border), auf 10 gekappt, Rest als „+n weitere". Nur bei `docks`
      gerendert (Payload anderer Maschinen wird ignoriert, auch bei
      zufällig ähnlichen Keys).
- [x] **W2.3 Nachtmodus bei Pause** — `machine_box(..., animate=bool)`
      unterdrückt Blink/Band/Rauch komplett, unabhängig vom Einzelstatus
      (verhindert z.B. Rauch aus VERALTETEN "active"-Analyzer-Zeilen aus
      der Zeit vor der Pause). `scene.py` legt bei `state.paused` zusätzlich
      ein `fx-night-overlay`-Rechteck über die ganze Halle — nur die
      Werksuhr wird NACH dem Overlay gezeichnet und bleibt normal sichtbar
      ("nur die Werksuhr leuchtet"). Aktuell live sichtbar, da der Bot
      pausiert ist.
- [x] **W2.4 Performance-Check** — gemessen (15.7., drei echte Läufe):
      0.18/0.07/0.06ms pro `build_scene_svg()`-Aufruf, SVG ~5,4KB — weit
      unter dem 50ms-Ziel, als Kommentar in `scene.py` festgehalten.
      Regressions-Test (10×-Mittel < 50ms) ergänzt.

      11 neue Tests (Animations-Bedingungen, Slot-Kappung/Escaping,
      Nachtmodus-Overlay, Performance-Guard), Verifikation pixel+plain OK.

## W3 — Interaktivität

- [x] **W3.1 Tooltips** — Tooltips waren technisch schon seit W1.1/W1.2
      mit echten Zahlen gefüllt (`<title>`-Element in `machine_box()`).
      Feinschliff jetzt: jede Tooltip-Zeile wird EINZELN escaped und dann
      erst mit dem literalen `&#10;`-Entity gejoint (statt `html.escape()`
      über den ganzen gejointen String laufen zu lassen — das hätte das
      `&` der Entity selbst zu `&amp;#10;` verstümmelt).
- [x] **W3.2 Klick-Fokus per Query-Param** — jede Maschinen-Box steckt in
      `<a href="?factory=<id>" target="_self">`; `tabs/factory.py` liest
      `st.query_params.get("factory")`, ignoriert unbekannte/fehlende IDs
      still. `_render_detail_panel()` als gemeinsamer Einstieg für W3.2+W3.3.
- [x] **W3.3 Detail-Panels ausgebaut** — fünf Maschinen mit eigenem Block:
      conveyor (Funnel-Metriken + SKIP-Gründe), warehouse (Positions-Tabelle),
      docks (Gesund/Schwach/Tot-Listen), lab (Labeled/Gewinne/Verluste/
      Win-Rate), clock (Zustand/Phase/nächster Lauf). Alle anderen sechs
      Maschinen behalten den generischen Fallback (Label/Status/Tooltip-
      Zeilen/Rohdaten als `st.json`) — bewusst kein Task pro restlicher
      Maschine, der generische Pfad ist bereits vollständig nützlich.
      Detail-Renderer sind fail-open (ein kaputter Spezial-Block fällt auf
      den generischen zurück statt die Seite zu crashen).

      9 neue Tests (Tooltip-Join/-Escaping, Klick-Fokus mit bekannter/
      unbekannter ID, zwei Detail-Panels gegen echte isolierte
      Datenquellen), Verifikation pixel+plain OK.

## W4 — Entdeckungs-Ebene

- [x] **W4.1 Ereignis-Framework** — `scene.py::scene_events(state)`.
      Drei Requisiten: (a) `breaker.status=="err"` → blinkende Rundumleuchte
      + „NOT-AUS"-Schild (direkt aus dem Maschinen-Status, keine eigene
      Datenquelle nötig), (b) `data/eonet_hazards.json` `hazard_label==
      "ELEVATED"` → dunkle Wolke überm Dach, (c) `StopLossCooldown.
      all_blocked()` nicht leer → „SPERRZONE"-Absperrband am Förderband.
      Neue Felder `FactoryState.events: Dict[str,bool]` +
      `weather_demand_label: str`, befüllt über `state._read_events()`
      (fail-open pro Flag + äußeres try/except).
      **Echter Nebenbefund** beim End-to-End-Check gegen echte Daten: (c)
      zunächst naiv über die Rohdatei geprüft ("nicht leer" = aktiv) — ein
      über einen Monat alter GILD-Cooldown-Eintrag wäre damit fälschlich
      als aktiv gemeldet worden. Auf `StopLossCooldown.all_blocked()`
      umgestellt (respektiert den echten Ablauf). Dabei einen bestehenden,
      NICHT gefixten Bug in `analyzers/sl_cooldown.py` gefunden: die
      Selbstbereinigung dort vergleicht `len(active)<len(data)`, NACHDEM
      abgelaufene Einträge schon aus `data` gepoppt wurden — der Vergleich
      ist danach immer `False`, die Datei wird nie tatsächlich bereinigt
      (nur das Rückgabe-Ergebnis von `all_blocked()` ist jederzeit korrekt).
      Bewusst NICHT gefixt — außerhalb der `dashboard/`-Pfadgrenze dieser
      Design-Session (Arbeitsprotokoll, docs/DESIGN_ROADMAP.md).
- [x] **W4.2 Tag/Nacht** — dünner Himmelsstreifen oben in der Szene,
      Farbe nach `datetime.now().hour` (06–20 Uhr hell, sonst dunkel; `now`
      injizierbar für Tests, kein Realismus-Anspruch/Übergang).
- [x] **W4.3 Echtes Wetter** — `state.weather_demand_label` aus
      `data/weather_macro.json` (`demand_label`, fail-open): ELEVATED →
      Regen-Striche über der Wetterstation, SUBDUED → Sonne, NORMAL → kein
      Overlay.
- [x] **W4.4 Easter Eggs** — (a) `experience_store.stats()['live'] > 0` →
      goldener Wimpel überm Verladetor, (b) eine These in
      `thesis_registry.json` auf `status=="PROVEN"` → goldene Statue vor
      der Halle, (c) jüngstes Backup < 15h alt → Nachtschicht-Roboter-Detail
      am Backup-Roboter. Alle drei an echte Daten gebunden, je eigener Test.
- [x] **W4.5 Wachstums-Regel** — Kommentarblock in `machines.py` (schon
      seit W1.2 vorhanden) um den zweiten Erweiterungspfad ergänzt: neue
      MASCHINE (Leser+LAYOUT+Tooltip+Test, Box automatisch generisch) vs.
      neues EREIGNIS (Flag in `_read_events()` + Bedingung in
      `scene_events()`, kein LAYOUT-Platz nötig). Querverweis in
      `scene.py`s Moduldoc ergänzt.

      21 neue Tests (Ereignis-Flags einzeln + Kombination, Tag/Nacht mit
      zwei Uhrzeiten, Wetter-Overlay je Label, drei Easter Eggs, echter
      Ablauf-Fall für den SL-Cooldown-Bugfund), Verifikation pixel+plain OK.

## W5 — Pixel-Art-Ausbau (parallel zu W4 möglich)

- [ ] **W5.1 Asset-Slots** — `machines.py`: liegt
      `dashboard/assets/img/factory_<id>.png` vor (via `theme.image_b64`),
      wird das PNG statt der Skelett-Form gerendert (SVG `<image>`,
      LED/Tooltip/Link bleiben identisch drumherum). Fallback = Skelett.
      Test: mit und ohne Datei. Fertig wenn: Tests + Verifikation OK.
- [ ] **W5.2 [USER] Assets je Maschine** — mit dem Stil-Prompt vom 13.7.,
      eine Maschine pro Etappe generieren/auswählen, Ablage als
      `factory_<id>.png`. > Kann NICHT vom Modell erledigt werden.
- [ ] **W5.3 Einbau je geliefertem Asset** — wiederholbarer Mini-Task:
      PNG ablegen, headless prüfen, Screenshot, abhaken (hier je Maschine
      eine Zeile ergänzen: W5.3-conveyor, W5.3-gate, …).
- [ ] **W5.4 (Nur bei Bedarf) Canvas-Evaluation** — NUR falls die
      SVG-Szene sichtbar ruckelt (Kriterium: flüssiges Scrollen im Tab
      nicht mehr gegeben bei üblicher Maschinen-/Animationszahl). Erst
      messen, dann `> OFFEN:`-Notiz mit Befund — NICHT eigenmächtig eine
      JS-Engine einbauen.

## Bewusst NICHT (auch hier)

- ✗ Live-Preis-/Netzwerk-Calls in state.py (einzige Ausnahme: der
  0.4s-Gateway-Socket) — die Szene liest nur, was lokal schon da ist.
- ✗ Eigener Refresh-Mechanismus neben `st.fragment(run_every="60s")`.
- ✗ Die Fabrik als Startseite/Ersatz der Daten-Tabs.
