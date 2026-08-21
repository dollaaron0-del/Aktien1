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

      **17.7. ERWEITERT** (User-Vision: die Fabrik soll das Hauptding
      werden, Klick auf JEDES Element liefert die Tabellen-Info):
      restliche sechs Maschinen bekommen jetzt ebenfalls eigene
      Detail-Panels statt des generischen JSON-Fallbacks —
      analyzer_claude/analyzer_ollama (genauer model_route-Breakdown
      der letzten 50 Analysen, nicht nur der Präfix-Anteil), breaker
      (Tagesverlust/Drawdown/Reset-Historie aus dem echten
      CircuitBreaker-State), gate (Host:Port + Erreichbarkeit), weather
      (Regime + Energienachfrage-Label aus derselben Quelle wie das
      W4.3-Overlay + Zeitstempel), backup_bot (Liste der letzten Backups
      mit Alter/Größe statt nur einer Zahl). Damit haben jetzt ALLE elf
      Maschinen einen eigenen Block; der generische Fallback bleibt als
      zweite Sicherheitsnetz-Schicht für künftige neue Maschinen. 13 neue
      Tests (route_breakdown-Aggregation, Backup-Liste sortiert/gekappt,
      alle fünf neuen Panels gegen echte isolierte Datenquellen inkl.
      AppTest), Suite grün (121/121 in den Fabrik-Testdateien). Die
      Tab-Reihenfolge (Fabrik als ERSTER Tab statt vorletzter) bleibt
      offen — nächster Schritt derselben Vision.

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

- [x] **W5.1 Asset-Slots** — `machines.py`: liegt
      `dashboard/assets/img/factory_<id>.png` vor (via `theme.image_b64`),
      wird das PNG statt der Skelett-Form gerendert (SVG `<image>`,
      LED/Tooltip/Link bleiben identisch drumherum). Fallback = Skelett.
      Test: mit und ohne Datei. Fertig wenn: Tests + Verifikation OK.

      Umgesetzt 15.7.2026: `machine_box()` prüft vor dem Skelett-Rechteck
      `theme.image_b64(f"factory_{m.id}.png")` — liefert das einen
      Data-URI, wird ein `<image href="..." preserveAspectRatio="xMidYMid
      slice">` an derselben Position/Größe gerendert; ist die Datei
      abwesend (`image_b64` liefert `""`), bleibt exakt das bisherige
      Skelett-Rechteck. LED, Tooltip, Klick-Link (`?factory=<id>`) und
      Activity-Overlay hängen unverändert am `<g>`-Wrapper drumherum, pro
      Maschine unabhängig (ein Asset für z.B. `gate` ersetzt nur dessen
      eigene Box, alle anderen bleiben Skelett bis ihr PNG kommt). 3 neue
      Tests (`test_machine_box_uses_skeleton_rect_without_asset_file`,
      `test_machine_box_uses_image_when_asset_file_present`,
      `test_machine_box_only_uses_asset_for_matching_machine_id`),
      Verifikation pixel+plain OK.
- [x] **W5.2 Assets je Maschine** — der alte Stil-Prompt vom 13.7.
      (16-Bit-Industrieautomation, Neon) passt nicht mehr zur Vision-W6-
      Kurskorrektur (Ziegelstein/Top-Down/cozy). Neuer Prompt (17.7.,
      auf User-Wunsch geschrieben) unten. Eine Maschine pro Etappe
      generieren/auswählen, Ablage als `factory_<id>.png` in
      `dashboard/assets/img/`.

      **Umgesetzt 18.7.2026** (User stellte Figma-MCP-Zugang mit Dev-Sitz
      bereit, s. Memory `figma-zugang-asset-generierung`): kein KI-Bild-
      generator verfügbar (`generate_figma_design` macht nur Seiten-
      Screenshots, kein gemaltes Pixel-Art) — stattdessen alle 11 Sprites
      als ECHTE Pixel-Raster in Figma gebaut (`use_figma`/
      `createNodeFromSvg`, ein `<rect>` je Pixel, 16px-Zellen). Gemeinsamer
      Ziegel-„Rahmen"-Baustein (Chamfer-Silhouette, 2:1-Ziegelverhältnis auf
      User-Wunsch, Laufverband-Fugen, Richtungslicht NW/SO, weicher
      Schlagschatten) für alle quadratischen/hochformatigen Gebäude geteilt
      — nur `conveyor` bekam ein eigenes Design (offene Rinne statt
      Vollwand, der generische Chamfer bricht bei so extremem Seiten-
      verhältnis). Export via `download_assets`, PNGs liegen mit
      transparentem Hintergrund in `dashboard/assets/img/`. Figma-Datei:
      "Ruflo Pixel-Art Assets" (`ZJ9qr8vXJILOYHukpDzr3u`).

      **Basis-Stil (vor jede Einzelbeschreibung stellen):**
      > Top-down (bird's-eye) view pixel art game asset. Cozy, inviting
      > art style like Stardew Valley, combined with the clear
      > functional silhouette readability of Factorio/Mindustry. Small
      > brick building, part of a whimsical factory complex. Warm
      > earthy palette: terracotta brick red, warm wood brown, soft
      > moss green, cream mortar lines. Soft rounded pixel shading,
      > gentle warm lighting — inviting and charming, NOT grim or
      > sterile industrial neon. Flat rooftop visible directly from
      > above, subtle brick/tile texture, small charming details
      > allowed (a chimney, a flower box, a lantern). Transparent
      > background, single isolated building sprite, no ground/other
      > buildings around it, no text/UI in the image.

      **Je Maschine anhängen:**
      | Datei | Maschine | Beschreibung |
      |---|---|---|
      | `factory_clock.png` | Werksuhr | small charming clocktower rooftop, round clock face visible from above, brick base, small pointed roof |
      | `factory_weather.png` | Wetterstation | tiny weather-station kiosk rooftop, spinning weather vane, small rain gauge, antenna |
      | `factory_docks.png` | Laderampen | long loading-dock building rooftop, several numbered garage-style bay doors along one edge (portrait aspect, ~200×420) |
      | `factory_analyzer_claude.png` | Claude-Analysator | slightly larger, refined brick building, glowing skylight/terminal window visible from above, small satellite dish, subtle warm glow |
      | `factory_analyzer_ollama.png` | Ollama-Werkbank | humbler wooden-roofed workshop shed, simpler/rustic than the Claude building, small chimney with gentle smoke |
      | `factory_conveyor.png` | Förderband | elongated open-topped conveyor structure from above, small crates/parcels on the belt, brick support posts along the sides (wide aspect, ~560×110) |
      | `factory_gate.png` | Verladetor | small brick gatehouse/checkpoint with a striped boom-barrier arm |
      | `factory_warehouse.png` | Hochregallager | large brick warehouse rooftop, small stacked-crates yard visible in one corner |
      | `factory_breaker.png` | Not-Aus | small brick utility shed, red/yellow warning-stripe trim, small emergency light on top |
      | `factory_lab.png` | Qualitätslabor | small brick laboratory building, tiny greenhouse-style glass skylight, thin vent pipe |
      | `factory_backup_bot.png` | Nachtschicht-Roboter | small brick maintenance shed, tiny friendly robot peeking out of the doorway, warmly glowing lantern |

      **Technisch:** Seitenverhältnis grob an die `scene.LAYOUT`-Box der
      jeweiligen Maschine anlehnen (die meisten breit/quadratisch, nur
      `docks` hochformatig ~1:2). PNG mit transparentem Hintergrund
      (liegt dann sauber auf dem bereits code-gezeichneten Ziegel-Canvas).
      Auflösung reicht mit ~512px Kantenlänge, wird im Browser skaliert.
- [x] **W5.3 Einbau je geliefertem Asset** — alle 11 auf einmal (18.7.2026):
      PNGs abgelegt, `test_dashboard_factory.py`/`test_dashboard_factory_tab.py`/
      `test_dashboard_theme.py` grün (162 Tests), Voll-Render pixel+plain
      OK. Zwei Bestandstests (`test_dock_slots_empty_payload_renders_
      nothing_extra`, `test_machine_box_uses_skeleton_rect_without_
      asset_file`) verließen sich auf die zufällige Abwesenheit von
      `factory_docks.png`/`factory_gate.png` im echten Verzeichnis statt
      auf ein isoliertes leeres `_IMG_DIR` (Muster der Nachbartests) — auf
      `tmp_path`-Isolation umgestellt, jetzt asset-unabhängig grün.
      W5.3-clock, W5.3-weather, W5.3-breaker, W5.3-gate, W5.3-lab,
      W5.3-backup_bot, W5.3-analyzer_claude, W5.3-analyzer_ollama,
      W5.3-warehouse, W5.3-docks, W5.3-conveyor.
- [ ] **W5.4 (Nur bei Bedarf) Canvas-Evaluation** — NUR falls die
      SVG-Szene sichtbar ruckelt (Kriterium: flüssiges Scrollen im Tab
      nicht mehr gegeben bei üblicher Maschinen-/Animationszahl). Erst
      messen, dann `> OFFEN:`-Notiz mit Befund — NICHT eigenmächtig eine
      JS-Engine einbauen.

## Vision W6 — Top-Down-Neubau (17.7.2026, User-Entscheidung: voller Umbau)

User-Vorgabe wörtlich: *"ich möchte das das Programm einer Fabrik aus
ziegelstein ähnelt. Man sollte aus der Top-Down ansicht darauf sehen
können. Jedes tool aus dem programm hat in der Fabrik ein eigene
Maschiene die sinvoll miteinander verbinden sein sollten ählich denn
Videospielen Factorio oder Mindustry nur einladender und cozy wie
stardew valley. So das man sich wünscht diese Fabrik als Hintergrund
bild oder Bildschiermschoner zu haben. es passiert immer etwas aber
nicht so viel die maschienen arebeiten einfach denn ganzen tag vor sich
hin"*. Auf Nachfrage: **voller Umbau jetzt**, kein Zwischenschritt.

Damit ist die alte „Festgelegte Bau-Entscheidungen"-Halle (Seitenansicht,
Maschinen in einer Reihe, ganz oben in diesem Dokument) **überholt** —
sie galt bis 17.7., der Kameraperspektive-Teil ist explizit ersetzt.

- [x] **W6.1 Top-Down-Grundriss** — `scene.py::LAYOUT` komplett neu als
      Grid statt Reihe, `viewBox` `1200x675`→`1200x820`. Reihenfolge
      oben→unten spiegelt den echten Datenfluss (Zulauf `docks`/`weather`/
      `clock` → Analyse `analyzer_claude`/`analyzer_ollama` → Entscheidung
      `conveyor`/`gate` → Lager/Sicherheit `warehouse`/`breaker`/`lab` →
      Backoffice `backup_bot`) — wie in Factorio/Mindustry: Rohstoffe oben
      rein, Ergebnis unten raus. Überlappungsfreiheit aller elf Boxen
      geprüft + als Test kodiert (`test_layout_boxes_do_not_overlap`).
- [x] **W6.2 Maschinen-Verbindungen** — neue `_CONNECTIONS`-Liste (14
      Paare `(von, nach, art)`, `art ∈ {main, feedback, utility}`) +
      `_connection_paths()`, gerendert VOR den Maschinen-Boxen (Leitung
      läuft optisch unter den Gebäuden). Jede Verbindung spiegelt eine
      ECHTE Abhängigkeit im Bot-Code (z.B. `docks`/`weather` → Analysatoren
      → `conveyor` → `warehouse`/`gate`; `lab` → Analysatoren als
      gestrichelte Lern-Rückkopplung; `warehouse` → `backup_bot` als
      Wartungs-Linie) — keine erfundene Deko. Eine `main`-Leitung
      "fließt" (`fx-pipe-flow`, neues CSS-Keyframe in `theme.py`) NUR,
      wenn beide Enden gerade `status in (ok, active)` sind — dieselbe
      Nur-echte-Daten-Regel wie `_activity_overlay` (W2.1).
      `feedback`/`utility` bleiben immer gestrichelt/statisch.
- [x] **W6.3 Ziegel/Cozy-Optik (prozedural, kein Bild-Asset nötig)** —
      zwei neue additive Palette-Keys `"brick"`/`"grass"` in BEIDEN
      `_PALETTE_PIXEL` UND `PALETTE_BLUEPRINT` (Pflicht, `test_dashboard_
      theme.py:262` erzwingt identische Keys). Canvas-Hintergrund wird
      `grass` (Werksgelände), darauf ein eingerückter Ziegel-Baukörper
      (neues `fx-brick-pattern`, Running-Bond-Optik, analog dem
      bestehenden `fx-belt-pattern`-Trick). Skelett-Fallback in
      `machine_box()` bekommt eine dünne Ziegel-Dachkante (nur additive
      zweite `<rect>`, bestehende Asset-Slot-Tests bleiben unverändert
      grün, da sie nur `"<rect" in box` prüfen). Restliche Dashboard-Tabs
      bleiben beim bestätigten Industrie-Neon-Pixel-Look — die zwei neuen
      Keys werden NUR von der Fabrik-Szene referenziert.
      **Grenze:** echte Top-Down-Pixel-Art je Maschine bleibt W5.2
      (User-Task, kann nicht generiert werden) — dieser Umbau liefert die
      Struktur (Grundriss, Verbindungen, Ziegel-/Rasen-Optik), keine
      illustrierten Sprites. Docken später über den bestehenden W5.1-
      Asset-Slot-Mechanismus an, ohne dass W6 nochmal angefasst wird.
      4 neue Tests (`test_all_connections_reference_known_machine_ids_
      and_kinds`, `test_connection_paths_include_known_main_connection`,
      `test_connection_only_animates_when_both_endpoints_active`,
      `test_feedback_connection_always_dashed_regardless_of_status`) +
      `test_layout_boxes_do_not_overlap` (W6.1).
      **Offen aus derselben Vision (User-Entscheidung 17.7.: erst alle
      Maschinen-Detail-Panels — s. W3.3-Ergänzung oben —, DANACH dies):**
      W6.4 Fabrik als ersten/Standard-Tab (widerspricht der alten
      „Bewusst NICHT"-Zeile unten — die gilt nur noch bis zu dieser
      User-Entscheidung, nicht mehr unverändert).

- [x] **W6.4 Fabrik als erster/Standard-Tab — UMGESETZT 18.7.2026** im
      Rahmen des großen Tab-Umbaus (User: „Dashboard aufräumen und
      sortieren als Grundlage fürs neue Dashboard", Wochenend-Projekt):
      **8 Tabs statt 14** — 🏭 Fabrik (zuerst/Standard), 📊 Portfolio,
      📡 Live, 🧠 Entscheidungen, 📈 Trades & Lernen, 🔭 Watchlist,
      🗂 Kartei, ⚙️ Einstellungen. Gelöscht: Aktien-Netzwerk (577 Zeilen
      Plotly-Showcase) + Technicals (Live-yfinance-Anzeige, Indikatoren
      fließen ohnehin in die Analyse). Verschoben statt gelöscht:
      Markt-Regime → Wetterstation-Detailpanel (`dashboard/regime_panel.py`),
      Analyse-Log → Analysator-Detailpanels (`dashboard/analysis_log_panel.py`),
      Signal-Queue → Abschnitt im Entscheidungen-Tab
      (`dashboard/signal_queue_panel.py`, Zähler wandert ins Tab-Label),
      Wochenbriefing → Abschnitt in der Kartei (`dashboard/briefing_panel.py`).
      Kopfbereich entschlackt: KPI-Leiste → Portfolio-Tab, Leitstand-
      Instrumente (D7.1) → Fabrik-Tab (`_render_instruments`), LED-Laufband
      (D7.3) ersatzlos raus (Live-Terminal + Fabrik-Logbuch zeigen dieselben
      Ereignisse); Kopf = Logo/Titel/Status-Banner/Ampel. Panels bleiben
      per ctx-Regel kiosk-sicher (ctx=None → schlanke Kurzinfo). Anti-Drift-
      Test in test_stock_relations.py auf reine Kopie-Verbots-Prüfung
      reduziert (Import-Pflicht war ohne Netzwerk-Tab gegenstandslos).
      Dashboard-Suite 520 Tests grün, Voll-Render pixel+plain OK.

## Vision W7 — Karte statt Dashboard (18.7.2026, User-Entscheidung: volles Aufgehen)

User-Vorgabe wörtlich: *"ich möchte das das ganze Programm Dashboard nur
aus dieser Fabrik besteht also eine Interaktive Map wird in der man die
Informationen heraus bekommt wenn man auf entsprechende Gegenstände die
organisch in der Fabrik verteilt sind drauf schaut und wenn man sie
anklickt mehr infos bekommt."* Damit ist W6.4 (Fabrik als erster Tab)
nur eine Zwischenstufe — das Ziel ist NULL Tabs außer der Szene selbst.

**Zwei Architektur-Entscheidungen (User, 18.7.):**
1. **Kontrollraum als eigenes Gebäude** (nicht ein Fixpunkt außerhalb der
   Karte) — Verwaltung bleibt visuell Teil der Fabrik. Umgesetzt: 12.
   Maschine, `control_room`, Backoffice-Pendant zum Nachtschicht-Roboter.
2. **Aktien als Kisten im Hochregallager, durchsuchbar per Klick aufs
   Lager; Kauf = Kiste wandert übers Förderband durch die Analysatoren
   ins Lager.** Umgesetzt: Kartei (Aktien-Suche) + Watchlist/IPO-Pipeline
   leben jetzt im Lager-Detailpanel (`_render_warehouse_stock_browser`
   in `tabs/factory.py`, Panels in `dossier_panel.py`/`watchlist_panel.py`).
   Die Kisten-Wanderung nutzt die bereits bestehende `conveyor→warehouse`-
   Leitungsanimation (W6.2, fließt bei echter Aktivität) statt einer
   neuen Pro-Kiste-Physik-Animation — bildet dieselbe reale Bewegung
   (Analyse → Kauf → Lager) ab, ohne Zusatzkomplexität.

**Fortschritt:**
- [x] **W7.1 Kontrollraum-Gebäude** — 12. Maschine, Status spiegelt eine
      echte Härtungslücke (kein Dashboard-Passwort). Volles
      Einstellungen-Formular am Detailpanel. 7 Tabs → committet.
- [x] **W7.2 Lager wird Aktien-Hub** — Kartei + Watchlist/IPO-Pipeline ins
      Lager-Detailpanel verschoben (`dossier_panel.py`, `watchlist_panel.py`,
      beide ex-Tabs). `?dossier=`-Links tragen jetzt `factory=warehouse&`
      mit, sonst würde der Link ins Leere zeigen (Kartei-Tab existiert
      nicht mehr). 5 Tabs → committet.
- [x] **W7.3–W7.7 Tab-Leiste komplett entfernen — UMGESETZT 18.7.2026**
      in einem Zug (alle vier Ziele hingen strukturell zusammen, ein
      Halbzustand mit unerreichbarem Code wäre eine Regression gewesen):
      - **W7.3 HUD** — `app.py` hat keine `st.tabs()` mehr. Persistenter,
        immer sichtbarer Streifen bleibt: Kopf (Logo/Titel/Status-Banner),
        Gesundheits-Ampel, Werksleiter-Gesicht, KPI-Leiste (Gesamtwert/
        Cash/Positionen/Regime/Win-Rate/Signal-Queue — aus dem früheren
        Portfolio-Tab hierher, das sind Zahlen zum SEHEN, nicht zum
        Entdecken), Sidebar (Bot-Pause/Fokus-Modus/Kosten/Config, bleibt
        unverändert bestehen). Danach direkt `factory.render(_ctx)` —
        keine Tab-Auswahl mehr nötig.
      - **W7.4 Live** (`tabs/live.py` → `live_panel.py`) → Werksuhr-
        Detailpanel (Zustand/Phase/nächster Lauf ist Uhr-Domäne).
      - **W7.5 Entscheidungen** (`tabs/decisions.py` → `decisions_panel.py`,
        inkl. der bereits vorher gemergten Signal-Queue) → Förderband-
        Detailpanel, unter der bestehenden kurzen Funnel-Zusammenfassung.
      - **W7.6 Trades & Lernen** (`tabs/trades.py` → `trades_panel.py`,
        588 Zeilen: Kalibrierung, Lernkurve, Genealogie, Paper-Forward,
        Thesis-Board, Filter-X-Ray, Why-Not) → Qualitätslabor-Detailpanel.
      - **W7.7 Portfolio-Rest** (`tabs/portfolio.py` → `portfolio_panel.py`:
        Wachstumsphase, Ziel-Risiko, Positionstabelle, Bot-Score,
        Wertverlauf, Transaktionen) → Lager-Detailpanel, oberhalb der
        W7.2-Aktien-Suche.

      Alle vier folgen demselben Muster wie Regime/Analyse-Log/Briefing/
      Einstellungen/Kartei/Watchlist: `render(ctx)` unverändert
      wiederverwendet, am Detailpanel per `st.divider()` angehängt,
      fail-open (`try/except`, Kiosk bleibt schlank). Kiosk-/Mobile-Tests
      auf die neue Realität umgestellt (auch der Vollmodus hat 0 Tabs —
      der Unterschied ist jetzt HUD vs. Kurzform statt Tabs vs. keine
      Tabs). Dashboard-Suite 517 Tests grün, Voll-Render pixel+plain OK.

      **Damit ist Vision W7 (User 18.7.: "das ganze Programm soll nur aus
      der Fabrik bestehen") strukturell komplett** — alle 8 früheren Tabs
      sind Detailpanels von Maschinen oder Teil des HUD.

## Vision W8 — Vollbild statt Fenster (18.7.2026, spät abends)

User-Feedback nach dem Anschauen von W7: *"Die Fabrik ist immer noch nur
ein Fenster. Die Firma sollte aber im Prinzip das Einzigste sein. Wir
machen es so: die Firma ist das Einzigste was man auf dem Dashboard
sieht in voller Größe, Zusatzinformationen werden am Rand eingeblendet
ähnlich wie bei einem Base-Bau-Spiel auf dem Handy."* W7 hatte die
Tab-Leiste entfernt, aber die Szene stand immer noch als ein Block unter
Kopf/KPI-Leiste im normalen Streamlit-Fluss — kein echtes Vollbild.

- [x] **W8.1 Schwebende HUD-Leiste + Vollbild-Szene (18.7.2026)** —
      `theme.py::_base_css()` (NUR Pixel-Theme, Plain bleibt beim
      D6.2-Notausstieg unangetastet):
      - Streamlit-eigene Kopfzeile/Toolbar/Menü/Footer global ausgeblendet
        (bisher nur in Kiosk-/Mobile-Zweigen; jetzt Standard).
      - `.block-container`-Ränder auf ein Minimum, `max-width:100%` — die
        Seite nutzt fast die volle Bildschirmbreite statt eines
        eingerahmten "Fensters".
      - `.px-hud-bar`: Kopf (Logo/Titel/Status-Banner/Live-Status/Ampel+
        Werksleiter-Gesicht) sitzt in einem `position: sticky`-Div mit
        halbtransparentem, geweichtem Hintergrund (`backdrop-filter`,
        inkl. `-webkit-`-Präfix für Safari) — bleibt beim Scrollen oben,
        schiebt die Szene aber nicht als eigener Block nach unten.
        `app.py` öffnet/schließt den Div um den kompletten Kopf-Abschnitt
        (nur bei `is_enabled()`).
      - `.px-scene-wrap`: die Live-Hauptszene (`tabs/factory.py::_scene()`)
        bekommt `min-height: 76vh` + Flexbox-Zentrierung — dominiert den
        Bildschirm, statt ein kleiner Block zu sein. Die SVG selbst bleibt
        unverändert seitenverhältnistreu (`width:100%; height:auto`,
        `preserveAspectRatio="xMidYMid meet"`) — Letterbox statt
        Verzerrung. NUR die Live-Hauptszene bekommt den Rahmen; die
        Zeitreise-Archiv- und Handy-Zweitverwendungen der Szene bleiben
        bewusst kompakt (kein zweites Vollbild verschachtelt in einem
        Detail-Panel).
      - Kiosk-Modus (`?kiosk=1`) profitiert automatisch mit (dieselbe
        `_scene()`-Funktion) — bekommt jetzt zusätzlich die schwebende
        Kopfleiste statt gestapelter Einzelelemente.
      3 neue Tests (`tests/test_dashboard_hud_layout.py`): HUD-Div öffnet
      und schließt korrekt, Szenen-Rahmen nur bei Pixel-Theme, Plain-Modus
      bleibt frei von beiden neuen Klassen. Voll-Render pixel+plain OK,
      532 Dashboard-Tests grün.
      **Ehrlicher Hinweis wie schon bei W7.10:** CSS-Layout (`position:
      sticky`, `backdrop-filter`, `vh`-Einheiten) lässt sich nicht per
      Headless-Test/Figma-Vorschau visuell verifizieren — nur die
      strukturelle Korrektheit (Klassen vorhanden, HTML balanciert, keine
      Exceptions) ist automatisiert geprüft. Das tatsächliche Bildschirm-
      Ergebnis (wirkt es wirklich wie ein Vollbild-Base-Bau-Spiel?) braucht
      den echten Browser-Check durch den User.
- [x] **W8.2 Detail-Panels als echtes Overlay (18.7.2026, gleicher Abend)**
      — einfacher als in W8.1 befürchtet: der `:target`-Trick war nie
      nötig, weil Streamlit den Query-Parameter bereits SERVERSEITIG
      auswertet (`st.query_params.get("factory")`) — der Server weiß
      also schon, OB ein Panel gerendert wird. Es musste nur noch WIE
      (Position) geändert werden: reines CSS-Positionieren des ohnehin
      bedingt gerenderten Inhalts, kein JS nötig.
      `tabs/factory.py`: öffnet vor `_render_detail_panel(machine, ctx)`
      einen Backdrop-Link (`<a href="?" class="px-detail-backdrop">`,
      räumt `factory`+`dossier` gemeinsam auf) + ein Panel-Div mit
      Schließen-Link, schließt das Panel-Div danach wieder — nur bei
      Pixel-Theme (D6.2: Plain bleibt beim alten Inline-Verhalten).
      `theme.py`: `.px-detail-backdrop` (fixed, `inset:0`, dunkel-
      transparent, `z-index:1000`) liegt UNTER `.px-detail-panel` (fixed,
      zentriert, `max-height:88vh` + `overflow-y:auto` für die
      inzwischen sehr inhaltsreichen Panels, `z-index:1001`) — Klicks
      im Panel treffen das Panel zuerst (normale DOM-Stapelreihenfolge),
      Klicks außerhalb schließen über den Backdrop. Eigener
      `@media (max-width:640px)`-Feinschliff für schmale Fenster.
      4 neue Tests (`test_detail_panel_renders_as_overlay_with_backdrop_and_close`,
      `test_detail_panel_overlay_absent_in_plain_theme`), Voll-Render
      pixel+plain OK, zusätzlich gegen das inhaltsreichste Panel (Lager:
      Aktien-Suche+Watchlist+Portfolio) durchprobiert — kein Crash.
      **Damit ist auch der zweite offene Punkt aus W8 erledigt** — CSS-
      Layout selbst bleibt wie bei W8.1 nur strukturell (nicht visuell)
      automatisiert prüfbar, braucht den echten Browser-Check.

      **Sidebar bewusst NICHT ins Kontrollraum-Detail gefaltet** (geprüft
      18.7., nach W7.10): anders als die Tabs ist die Sidebar keine
      konkurrierende "Seite" — sie ist ein Streamlit-natives Seitenpanel,
      das neben der Karte steht, kein Ersatz dafür. Wichtiger: sie trägt
      den **Bot-Pause-Schalter** — die kritischste Einzelaktion im ganzen
      Dashboard (kompletter Stopp inkl. SL/TP-Überwachung). Den hinter
      "Kontrollraum anklicken → runterscrollen" zu verstecken wäre eine
      echte Verschlechterung, kein Aufräumen — ein Not-Halt gehört so
      erreichbar wie möglich, nicht in ein Untermenü. Bleibt wie sie ist.

- [x] **W7.8 Rohr-Optik verstärkt (18.7.2026)** — User lud 25 Referenz-
      bilder hoch (neues Upload-Feld in der Sidebar,
      `dashboard/assets/reference/`, nicht versioniert). Durchsicht ergab
      vier Stil-Cluster (Stardew Valley, echte Factorio-Screenshots,
      fotorealistische 3D-Renders/Satisfactory, zwei Ausreißer ohne
      Fabrik-Bezug) — klare Rücksprache statt Raten, da die Cluster sich
      widersprechen (Factorios Dichte widerspricht der 17.7.-Vorgabe
      "nicht zu viel los"; 3D-Renders sind eine andere Technik als unser
      SVG-Pixel-Raster). **User-Entscheidung:** Pixel-Art-Technik UND
      warme Ziegel-Palette bleiben unverändert (nichts Bestehendes wird
      verworfen); die beiden Ausreißer-Bilder waren nur lose Stimmung,
      keine Vorgabe. Einziges konkretes Ergebnis: die Verbindungs-
      leitungen (`scene.py::_connection_paths()`) bekommen mehr visuelles
      Gewicht (Factorio-Anleihe) OHNE Farb-/Stilwechsel — neue dickere
      Border-Gehäuselinie + periodische Kupfer-Muffen-Striche
      (`_pipe_joints()`) unter der eigentlichen (ggf. fließenden)
      Leitung. 1 neuer Test (`test_connection_paths_have_pipe_casing_and_joints`),
      Voll-Render pixel+plain OK.

- [x] **W7.9 Dichter vernetzt (18.7.2026, gleiche Sitzung)** — User-
      Präzisierung: die Nicht-Pixel-Art-Referenzbilder waren nicht für
      die Optik gedacht, sondern für die VERBINDUNGS-IDEE ("jedes Tool
      bekommt seine eigene Maschine", dicht vernetzt, "chaotisch aber im
      Rhythmus"). Statt Deko zu erfinden: Code-Audit auf bisher fehlende
      ECHTE Abhängigkeiten zwischen den 12 Maschinen, 5 gefunden und
      ergänzt (`_CONNECTIONS` 14→19):
      - `breaker→conveyor` (feedback) — Circuit-Breaker blockiert
        Kaufentscheidungen wirklich (`strategy/swing_strategy.py:224`
        `_circuit_breaker_active()`).
      - `lab→backup_bot`, `conveyor→backup_bot` (utility) — Lern-Daten
        (`experience.db`) UND Entscheidungs-Log (`decision_log.db`)
        werden beim Backup mitgesichert (`scripts/backup.sh`), parallel
        zur bestehenden `warehouse→backup_bot`-Leitung.
      - `control_room→conveyor` (utility) — `config.buy_threshold`
        steuert die Kaufschwelle direkt (`strategy/swing_strategy.py:280`).
      - `control_room→gate` (utility) — `config.broker_mode` bestimmt,
        ob Paper- oder IBKR-Gateway läuft (`main.py:223`).
      Alle fünf einzeln im Code verifiziert (grep+Read), bevor sie
      hinzugefügt wurden — Prinzip "keine erfundene Deko" bleibt
      unangetastet. Visuell per Figma-Vorschau geprüft: die Szene wirkt
      jetzt deutlich dichter vernetzt, ohne unübersichtlich zu werden.
      1 neuer Test (`test_connections_include_w79_dependencies_verified_in_code`),
      Voll-Render pixel+plain OK.

- [x] **W7.10 Echte Kisten-Wanderungs-Animation (18.7.2026)** — löst den
      Platzhalter ein, der bei W7.2 bewusst dokumentiert wurde. User-
      Vorgabe wörtlich (18.7.): "wenn eine Aktie gekauft wird, wandert
      die Kiste über das Förderband ins Lager." Neue Funktion
      `scene.py::_crate_travel_marker()`: eine kleine Kiste (Kupfer-
      Farbton, Muster wie die Lager-Kisten aus W5.2) wandert per reiner
      CSS-Animation (`offset-path`, kein SMIL, kein Rerun-Kostenaufwand)
      entlang der `conveyor→warehouse`-Leitung. Nur sichtbar, wenn diese
      Leitung wirklich fließt (dieselbe Nur-echte-Daten-Regel wie
      `_connection_paths`) UND der Bot nicht pausiert ist (W2.3-
      Nachtmodus-Konvention). `fx-crate-travel`-Keyframe in `theme.py`,
      in derselben `prefers-reduced-motion`-Abschaltliste wie die
      übrigen fx-*-Animationen. Wird VOR den Maschinen-Boxen gezeichnet
      (wie die Leitungen selbst) — verschwindet dadurch optisch "unter"
      dem Lager-Gebäude, sobald sie ankommt. 3 neue Tests
      (`test_crate_travel_marker_*`), Voll-Render pixel+plain OK.
      **Ehrlicher Hinweis:** CSS-`offset-path`-Animationen laufen nur im
      echten Browser, nicht in einem statischen Screenshot/Headless-Test
      — visuell nur live im Dashboard prüfbar, nicht per Figma-Vorschau
      wie die übrigen Design-Iterationen dieser Sitzung.

- [x] **W8.3–W8.5 Sidebar-Ausklapp-Pfeil (18.7.2026, drei Anläufe)** — User
      meldete nach W8.1: "die Sidebar ist nicht mehr da". W8.1 hatte
      `[data-testid="stHeader"]` komplett per `display:none` versteckt
      (für die Vollbild-Fabrik); der Ausklapp-Pfeil einer kollabierten
      Sidebar (`stExpandSidebarButton`, bei `initial_sidebar_state="auto"`
      je nach Fensterbreite) hängt aber als Kind-Element daran und wurde
      mit unsichtbar. W8.3: `stHeader` bleibt im Baum (nur Höhe/Hintergrund
      auf 0), Toolbar/Menu/Footer bleiben `display:none`. User meldete
      erneut "immer noch nicht" → **W8.4**: `height:0` auf dem Elternteil
      nimmt ein normales Flow-Kind im Layout mit auf Höhe 0 (Bounding-Box
      wurde `None`) — nur noch Hintergrund transparent, Höhe bleibt
      natürlich. Wieder nicht ausreichend → **W8.5** (echte Ursache, erst
      per Playwright/echtem Chromium gefunden, AppTest kennt kein
      CSS-Layout): der Pfeil liegt in Wahrheit eine Ebene tiefer direkt in
      `stToolbar`, und GENAU `stToolbar` stand seit dem allerersten
      W8.1-Commit auf `display:none` — unabhängig vom `stHeader`-Fix. Die
      Kind-Struktur hat zwei getrennte Zweige: einen nur mit
      `stExpandSidebarButton`, einen mit den Geschwistern
      `stToolbarActions`/`stAppDeployButton`/`stMainMenu` (Deploy-Button +
      Drei-Punkte-Menü, das eigentliche "Geraffel"). Jetzt werden nur noch
      diese drei einzeln versteckt, `stToolbar` selbst bleibt da. Diesmal
      end-to-end statt nur strukturell verifiziert: lokale
      Streamlit-Instanz + Playwright bei 700px Fensterbreite (Sidebar
      kollabiert), Pfeil tatsächlich angeklickt, Bounding-Box-Wechsel
      (width 0→300) bestätigt, Deploy-Button/Menü per Screenshot als weg
      gegengecheckt. **Lehre für künftige Layout-Fixes:** rein strukturelle
      Prüfung (Klassen vorhanden, kein Crash) reicht bei CSS-Positionierung
      nicht — Playwright gegen die echte Seite ist hier die einzige
      verlässliche Verifikation.

- [x] **W8.6 Förderband-Chevrons statt Kupfer-Muffen (18.7.2026)** — User-
      Vorgabe zu den Referenzbildern vom selben Tag: "orientiere dich sehr
      stark daran und hol dir Inspiration wie Maschinen und Förderbänder
      aussehen könnten." Neue `scene.py::_belt_treads()`: nur `kind="main"`
      -Leitungen (echter Waren-/Datenfluss) bekommen Rollen-Chevrons
      (Polylinien in `copper_hi`, quer zur Leitung, Pfeilspitze in
      Flussrichtung) statt der bisherigen `_pipe_joints()`-Kupfer-Muffen —
      sieht jetzt wie ein echtes Förderband aus. `feedback`/`utility`
      zeigen weiterhin nur eine Beziehung, keinen Warenfluss, und bleiben
      bei den alten Muffen. 2 neue Tests
      (`test_main_connection_gets_belt_tread_chevrons_not_pipe_joints`,
      `test_feedback_and_utility_connections_keep_pipe_muffen_joints`).

- [x] **W8.7 Kern-Status-Vollseite statt Popover (20.7.2026)** — User-
      Vorgabe: ein Ein-Klick-Zugang zu "allen Daten des Bots", erst als
      Popover gebaut, aber zu eng für den vollen Datenstand. Umgebaut auf
      eine eigene Seite (`?status=1`, `dashboard/tabs/full_status.py`):
      `app.py` prüft den Query-Param VOR der KPI-Leiste/Fabrik-Szene und
      rendert bei `status=1` die Vollseite statt der Szene; die Sidebar
      bleibt erhalten (Bot-Pause-Schalter muss immer erreichbar bleiben,
      s. Begründung bei W8.2). Sidebar bekommt einen Link
      (`_render_core_status_link()`, reines `<a href="?status=1"
      target="_self">` statt `st.link_button` — dieselbe
      Query-Param-Navigation wie die Fabrik-Detailpanels). Bewusste
      Übergangslösung, solange der Fabrik-Umbau noch nicht jede Maschine
      mit einem vollen Detailpanel abdeckt.

## Bewusst NICHT (auch hier)

- ✗ Live-Preis-/Netzwerk-Calls in state.py (einzige Ausnahme: der
  0.4s-Gateway-Socket) — die Szene liest nur, was lokal schon da ist.
- ✗ Eigener Refresh-Mechanismus neben `st.fragment(run_every="60s")`.
- ~~✗ Die Fabrik als Startseite/Ersatz der Daten-Tabs.~~ ÜBERHOLT (17.7.,
  Vision W6) und seit 18.7.2026 UMGESETZT: Fabrik ist erster/Standard-Tab
  (s. W6.4 oben), die Daten-Tabs bestehen konsolidiert weiter (8 statt 14).
