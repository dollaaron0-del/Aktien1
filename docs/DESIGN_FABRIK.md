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

- [ ] **W3.1 Tooltips** — jede Maschine bekommt `<title>` aus
      `MachineState.tooltip` (Zeilen mit `&#10;` gejoint, escaped) —
      native Browser-Tooltips, kein JS. Tooltips mit ECHTEN Zahlen füllen
      (Leser in state.py ergänzen, z.B. warehouse: „NVDA: 13.5 Stk."). Tests:
      Tooltip-Text im SVG. Fertig wenn: Tests + Verifikation OK.
- [ ] **W3.2 Klick-Fokus per Query-Param** — Maschinen-Boxen in
      `<a href="?factory=<id>" target="_self">` wrappen; `tabs/factory.py`
      liest `st.query_params.get("factory")` und rendert unter der Szene
      ein Detail-Panel der fokussierten Maschine (erstmal: Label, Status,
      Tooltip-Zeilen als Liste, payload als `st.json`). Unbekannte IDs
      ignorieren. Fertig wenn: Verifikation OK + AppTest mit gesetztem
      Query-Param zeigt das Panel.
- [ ] **W3.3 Detail-Panels ausbauen** — je Maschine ein sinnvoller
      Detail-Block statt `st.json`: conveyor→Funnel-Zahlen (reuse
      decisions-Tab-Bausteine), warehouse→Positions-Tabelle,
      docks→source_health-Listen, lab→ExperienceStore-Stats, clock→Phasen.
      Ein Task PRO Maschine ist erlaubt (W3.3a, W3.3b, … hier ergänzen),
      wenn es sonst zu groß wird. Fertig wenn: Verifikation OK.

## W4 — Entdeckungs-Ebene

- [ ] **W4.1 Ereignis-Framework** — `scene.py::scene_events(state) ->
      list[str]` (SVG-Snippets, werden über die Szene gelegt). Erste drei
      Requisiten, alle an ECHTE Zustände gebunden: (a) breaker err →
      rote Rundumleuchte + „NOT-AUS"-Schild, (b) `data/eonet_hazards.json`
      mit aktiven Hazards → dunkle Wolke überm Dach, (c) SL-Cooldown aktiv
      (`data/sl_cooldown.json` nicht leer) → „Sperrzone"-Absperrband am
      Band. Tests je Requisite (mit/ohne Zustand). Fertig wenn: Tests +
      Verifikation OK.
- [ ] **W4.2 Tag/Nacht** — Himmels-/Fensterfarbe nach echter Server-Uhrzeit
      (06–20 Uhr hell, sonst dunkel; Übergang egal, kein Realismus-Anspruch).
      Fertig wenn: Tests (zwei Uhrzeiten gemockt) + Verifikation OK.
- [ ] **W4.3 Echtes Wetter** — `data/weather_macro.json` lesen (fail-open):
      Regen-/Sonnen-Overlay über der Wetterstation passend zum Collector-
      Inhalt. Fertig wenn: Tests + Verifikation OK.
- [ ] **W4.4 Easter Eggs** — (a) erster Trade mit `label_source='live'` in
      experience.db → goldener Wimpel überm Verladetor, (b) eine These in
      `thesis_registry.json` auf PROVEN → goldene Statue vor der Halle,
      (c) Backup heute Nacht gelaufen → zufriedener Nachtschicht-Roboter
      mit Kaffeetasse. Alle an echte Daten gebunden, Tests je Egg.
      Fertig wenn: Tests + Verifikation OK.
- [ ] **W4.5 Wachstums-Regel dokumentieren** — Kommentarblock oben in
      `machines.py`: „Neue Bot-Funktion ⇒ neue Maschine: MachineState-Leser
      in state.py, Platz in LAYOUT, Box in machines.py, Tooltip, Test."
      Fertig wenn: committet.

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
