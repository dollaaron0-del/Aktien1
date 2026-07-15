# Design-Roadmap — Dashboard im 16-Bit-Industrieautomations-Stil

Stand: 15.7.2026 (v2, feingranular). Zielbild (User, 13.7.): "Industrial
automation pixel art" — 16-Bit, isometrisch, Förderbänder mit Datenwürfeln,
Sortier-Roboterarme, Terminal mit blinkenden Lichtern; Kobaltblau/Kupfer/
Stahlgrau/Neon. **Deadline: fertig zur Programm-Vorstellung.**

Dieses Dokument ist als Arbeitsvorrat für ein günstigeres Modell geschnitten:
jeder Task ist in einer Sitzung schaffbar, trifft keine offenen
Design-Entscheidungen (alle Werte stehen unten fest) und endet mit
Verifikation + Commit. Legende: `[x]` fertig · `[ ]` offen.

## Arbeitsprotokoll für das ausführende Modell (vor JEDEM Task lesen)

1. **Ein Task pro Durchgang, Reihenfolge einhalten** (D0.1 → D0.2 → …).
   Innerhalb eines Blocks nicht springen; Blöcke D2/D3/D4 sind untereinander
   unabhängig, brauchen aber alle D0+D1.
2. **Nur diese Pfade anfassen:** `dashboard/`, `.streamlit/`,
   `tests/test_dashboard_*`, dieses Dokument. NIEMALS Bot-/Trading-Code
   (`bot/`, `strategy/`, `broker/`, `analyzers/`, …), nie `.env`, nie
   systemd/crontab. Der Bot ist bewusst pausiert — nichts starten/enablen.
3. **Verifikation nach jedem Task** (Pflicht, beide Läufe müssen `OK` drucken):
   ```bash
   timeout 180 venv/bin/python - <<'EOF'
   from streamlit.testing.v1 import AppTest
   at = AppTest.from_file("dashboard/app.py", default_timeout=120)
   at.run()
   assert not at.exception, list(at.exception)
   print("OK pixel")
   EOF
   DASHBOARD_THEME=plain timeout 180 venv/bin/python - <<'EOF'
   from streamlit.testing.v1 import AppTest
   at = AppTest.from_file("dashboard/app.py", default_timeout=120)
   at.run()
   assert not at.exception, list(at.exception)
   print("OK plain")
   EOF
   ```
   Zusätzlich die Task-eigenen Tests: `venv/bin/python -m pytest
   tests/test_dashboard_theme.py -q` (sobald die Datei existiert).
4. **Commit pro Task**: nur die eigenen Dateien stagen, Message-Format
   `feat(dashboard): <was> (Design D0.3)`. Der pre-commit-Hook fährt die
   volle Suite (~5–8 min) — abwarten; bei Rot Fehler beheben, nie
   `--no-verify`.
5. **Nach Erledigung hier abhaken** (`[x]` + Einzeiler, was gemacht wurde).
6. **Bei Unklarheit nicht raten:** Task offen lassen, direkt unter dem Task
   eine `> OFFEN:`-Zeile mit der konkreten Frage hinterlassen, mit dem
   nächsten unabhängigen Task weitermachen.
7. Das Live-Dashboard (systemd `aktien_dashboard`, Port 8503, runOnSave)
   lädt Code-Änderungen sofort — kaputte Zwischenstände nicht liegen lassen.
8. **HTML-Sicherheit:** Jeder dynamische Wert (Ticker, Gründe, Log-Texte),
   der in ein `unsafe_allow_html`/SVG-Snippet wandert, läuft vorher durch
   `html.escape()`. Keine Ausnahmen.

## Festgelegte Design-Werte (nicht neu entscheiden — nur verwenden)

**ENV-Schalter:** `DASHBOARD_THEME` — `pixel` (Default) | `plain` (alles aus,
Verhalten wie heute). Auswertung ausschließlich in `dashboard/theme.py`.

**Palette** (Konstante `PALETTE` in `dashboard/theme.py`):

```python
PALETTE = {
    "bg":          "#14171C",  # Seiten-Hintergrund (Stahl, fast schwarz)
    "bg_panel":    "#1E232B",  # Panel-/Karten-Hintergrund
    "border":      "#3A4250",  # Panel-Rahmen (Stahl)
    "text":        "#E8ECF2",  # Primärtext
    "text_muted":  "#9AA4B2",  # gedämpfter Text
    "cobalt":      "#2E6BE6",  # Primär-Akzent (aktive Tabs, Zyklus-Events)
    "cobalt_hi":   "#4D8DFF",  # Hover/Highlight
    "copper":      "#C87533",  # Kupfer (Gate-Blocks, Warn-Sekundär, Deltas)
    "copper_hi":   "#E09A5A",
    "neon_green":  "#39FF88",  # OK-LED, Gewinne, Trades
    "amber":       "#FFC857",  # Warn-LED
    "red":         "#FF4D4D",  # Fehler-LED, Verluste
    "neon_cyan":   "#33E0FF",  # sparsam: Glow/Scanline/Sonder-Highlights
}
```

**Fonts:** Überschriften/Logo = "Press Start 2P"; Terminal-/Log-Akzente =
"VT323"; **Zahlen, Tabellen, Fließtext bleiben Streamlit-Default** (Lesbarkeit
vor Stil — Pixel-Font dort ist verboten). Fonts als woff2 lokal nach
`dashboard/assets/fonts/` (kein CDN zur Laufzeit); Fallback-Ketten:
`"Press Start 2P", monospace` bzw. `"VT323", "Courier New", monospace`.
Scheitert der Font-Download, Task trotzdem abschließen (Fallback reicht,
im Commit vermerken).

**CSS-Namensraum:** alle eigenen Klassen mit Präfix `px-`:
`.px-head` (Pixel-Überschrift), `.px-panel` (Industriepanel),
`.px-led .px-led--ok/--warn/--err/--off` (Status-LEDs; nur `--warn`/`--err`
pulsieren per CSS-Keyframe `px-blink`, `--ok` leuchtet statisch),
`.px-terminal` (Terminal-Log-Block), `.px-belt` (Förderband, D4).

**API von `dashboard/theme.py`** (Signaturen fix, damit spätere Tasks
dagegen bauen können):

```python
PALETTE: dict[str, str]
def is_enabled() -> bool                    # DASHBOARD_THEME != "plain"
def inject() -> None                        # CSS/Fonts einmalig; no-op bei plain
def led(status: str, label: str) -> str     # status: ok|warn|err|off → HTML-Span
def panel(html_body: str) -> str            # umschließt Body mit .px-panel-Div
def register_chart_themes() -> None         # Altair-Theme + Plotly-Template (D2)
```

Alle Helfer geben bei `plain` schlichtes, ungestyltes HTML bzw. no-op zurück
— Aufrufer brauchen keine eigene Fallunterscheidung.

---

## D0 — Fundament

- [x] **D0.1 `.streamlit/config.toml` anlegen** — Datei mit dem fixen
      dark-Theme-Block angelegt. Beide Verifikations-Läufe OK.
- [x] **D0.2 `dashboard/theme.py` anlegen** — PALETTE, `is_enabled()`,
      `inject()`, `led()`, `panel()`, `image_b64()`, `register_chart_themes()`
      (Stub). 17 Tests in `tests/test_dashboard_theme.py`, alle grün.
- [x] **D0.3 `inject()` verdrahten** — in `app.py` nach `set_page_config()`,
      vor `require_login()`. Alt-CSS-Block bewusst noch nicht angefasst
      (D1.1). Verifikation OK.
- [x] **D0.4 Fonts bundlen** — Press Start 2P + VT323 (latin-Subset, woff2)
      erfolgreich von Google Fonts geladen, OFL.txt mit abgelegt,
      Base64-Einbettung in `theme.py::_font_face_css()` (lru_cache, liest
      lokale Dateien — kein Laufzeit-Netzzugriff). Fallback getestet
      (fehlende Dateien crashen `inject()` nicht).
- [x] **D0.5 CSS-Basisklassen** — direkt in D0.2 mitgebaut (`.px-panel`,
      `.px-led` + `--ok/--warn/--err/--off`, `.px-head`, `.px-terminal`,
      Keyframe `px-blink`). Verifikation OK.

## D1 — Sichtbare Quick-Wins (Header, KPIs, Ampel, Tabs)

- [x] **D1.1 Alt-CSS konsolidieren** — Inline-Block aus `app.py` als
      `theme._legacy_css()` (exakt der alte Text) für plain; `app.py` ruft
      nur noch `inject()`, das intern zwischen `_base_css()`/`_legacy_css()`
      wählt. Optisch bei plain unverändert, Verifikation OK.
- [x] **D1.2 Header** — Titel als `.px-head` in `.px-panel`, Stand/Broker
      in text_muted; Logo zeigt `logo.png` falls via `image_b64()` vorhanden
      (D5.3 vorgezogen), sonst Emoji-Platzhalter. Verifikation OK.
- [x] **D1.3 Gesundheits-Ampel → LEDs** — alle drei Dot-Funktionen nutzen
      `theme.led(status,label)`; Zeile als `.px-panel`. Plain liefert
      weiter den alten Emoji-Text (macht `led()` selbst). Verifikation OK.
- [x] **D1.4 KPI-Leiste** — `[data-testid="stMetric"]` auf Panel-Optik,
      Label in VT323, Delta-Farben neon_green/red über die SVG-Fill-Farbe
      der Streamlit-Pfeile. In `theme._base_css()` mitgebaut (D1.1-Aufhänger,
      gleiche Selektoren). Verifikation OK.
- [x] **D1.5 Tab-Leiste** — aktive Tab cobalt, inaktive text_muted, Hover
      cobalt_hi, Tab-Unterstreichung (`[data-baseweb="tab-highlight"]`)
      cobalt. In `theme._base_css()` mitgebaut. Verifikation OK.
- [x] **D1.6 Login-Seite** — `st.title()` bleibt ECHTES st.title (Test-
      Vertrag erhalten), globale `h1`-Pixel-Font-Regel in `theme.py` greift
      dadurch praktisch nur hier (st.title wird sonst nirgends verwendet,
      geprüft). Formular in `st.container(border=True)`, Splash-Bild-Slot
      via `image_b64("splash.png")` (D5.3 vorgezogen). Passwort-Logik
      unverändert. `tests/test_dashboard_auth.py` weiter grün (22 Tests
      gesamt mit theme), Verifikation OK.

## D2 — Chart-Theming (nach D0, unabhängig von D1.2–D1.6)

- [ ] **D2.1 Altair-Theme** — in `theme.py::register_chart_themes()`:
      `alt.themes.register("pixel", …)` + enable (nur wenn `is_enabled()`);
      Werte: Hintergrund transparent, Achsen/Grid border-Farbe, Labels
      text_muted, kategoriale Range `[cobalt, copper, neon_green, amber,
      red, neon_cyan]`. Aufruf in `app.py` nach `inject()`. Kein Chart-Code
      in den Tabs anfassen (Theme wirkt global). Fertig wenn: Verifikation
      OK + Mini-Test (Theme registriert, plain lässt Default aktiv).
- [ ] **D2.2 Plotly-Template** — dito: `plotly.io.templates["pixel"]`,
      `templates.default = "pixel"` nur bei `is_enabled()`; gleiche Farben,
      `paper_bgcolor`/`plot_bgcolor` transparent. Betroffen ist nur
      `tabs/network.py` — prüfen, dass der dortige Graph das Template erbt
      (kein explizites `template=`-Argument nötig). Fertig wenn: Verifikation.
- [ ] **D2.3 Drift-Schutz** — Kommentarblock in `theme.py` ("neue Charts:
      kein eigenes Farb-Hardcoding, Theme kommt von hier") + Test, dass
      `register_chart_themes()` idempotent ist (zweifacher Aufruf wirft
      nicht — Streamlit reruns!). Fertig wenn: Tests grün.

## D3 — Live-Tab als „Leitstand" (nach D0+D1)

- [ ] **D3.1 Aktivitätsfeed als Terminal-Log** — `tabs/live.py`: die
      Event-Zeilen (bisher `st.markdown` je Event) als EIN
      `.px-terminal`-Block; Farbcodierung per span: trade→neon_green,
      gate_blocked→copper, cycle_start/end→cobalt, analysis_done→text.
      Alle Feld-Inhalte durch `html.escape()`. Plain: alter Pfad bleibt
      (if not theme.is_enabled(): bisheriger Code). Fertig wenn:
      Verifikation OK + gezielter AppTest mit geseedeter Temp-Feed-DB
      (Muster: bestehende live-Tab-Verifikation in der Git-Historie,
      Commit 8fb561b).
- [ ] **D3.2 Zyklus-Zeitleiste als Fertigungsstraße** — `tabs/live.py`:
      Phasen (Start→Exits→Vorladen→Analyse) als horizontale Stationen-Leiste
      (HTML/CSS: Punkte + Verbindungslinie, abgeschlossene Station
      neon_green, laufende pulsierend cobalt, ausstehende border-Farbe),
      Dauer-Angaben in VT323 darunter. Datenquelle unverändert
      `phase_durations()`. Fertig wenn: Verifikation OK.
- [ ] **D3.3 Order-Historie** — `tabs/live.py`: Order-Zeilen mit
      `theme.led()` statt Emoji (filled→ok, error→err, cancelled→off),
      Teilausführung als copper-Badge. Fertig wenn: Verifikation OK.
- [ ] **D3.4 Nächste-Aktionen/Timer-Panel** — Restliche Abschnitte des
      Live-Tabs in `.px-panel`-Optik, systemd-Timer-Zeilen in VT323.
      Fertig wenn: Verifikation OK.

## D4 — Entscheidungs-Funnel als Förderband (Vorzeige-Stück; nach D0+D1)

- [ ] **D4.1 SVG-Baustein (reine Funktion + Tests)** — NEU
      `dashboard/conveyor.py`: `build_conveyor_svg(funnel: dict, width:
      int = 900) -> str`. Input ist exakt das Dict von
      `DecisionLog.funnel(day)` (`{"total": n, "actions": {...},
      "skip_reasons": {...}}`). Darstellung: links Einlauf mit `total`
      Datenwürfel-Symbol + Zahl; Band nach rechts; pro skip_reason-Kategorie
      (Top 5, Rest als "…") ein Sortier-Arm, der in einen beschrifteten
      Behälter mit Anzahl wirft (copper); rechts Auslauf "BUY" (neon_green,
      Anzahl) und "SELL/HOLD"-Kästen. Farben aus `theme.PALETTE`, Beschriftung
      VT323, alle Labels `html.escape()`d. KEINE Animation in diesem Task.
      NEU `tests/test_dashboard_conveyor.py`: Zahlen/Labels erscheinen im
      SVG, leerer Funnel ({}, total 0) rendert ohne Fehler, Escaping-Test
      (`<script>` im Reason-Label kommt escaped raus), Top-5-Kappung.
      Fertig wenn: Tests grün (Verifikation hier optional — noch nicht
      eingebunden).
- [ ] **D4.2 Einbindung in den Entscheidungen-Tab** — `tabs/decisions.py`:
      oberhalb der bestehenden Fortschrittsbalken das SVG rendern
      (`st.markdown(svg, unsafe_allow_html=True)` bzw. `st.html`); die
      alten `st.progress`-Balken BEHALTEN (Zahlen-Detail + plain-Fallback).
      Nur bei `theme.is_enabled()`. Fertig wenn: Verifikation OK + AppTest
      mit geseedeter decision_log-Temp-DB (Muster in Git-Historie,
      Commit 7bdd413) zeigt das SVG im Baum.
- [ ] **D4.3 (Optional) Band-Animation** — CSS-Keyframe (laufende
      Band-Streifen), nur wenn D4.1/D4.2 abgenommen sind; eigener
      ENV-Unterschalter nicht nötig, aber Animation muss bei
      `prefers-reduced-motion` aus sein. Fertig wenn: Verifikation OK.

## D5 — Echte Pixel-Art-Assets (parallel möglich; D5.2 braucht den User)

- [x] **D5.1 Asset-Infrastruktur** — `dashboard/assets/img/` angelegt
      (`.gitkeep`); `theme.image_b64(name)` bereits in D0.2 gebaut + getestet.
- [ ] **D5.2 [USER] Bilder generieren + auswählen** — mit dem Prompt vom
      13.7.: (a) Header-Logo/Banner ~600×120, (b) Login-Splash ~800×400,
      (c) optional 12 Tab-Icons 32×32. Ablage als PNG in
      `dashboard/assets/img/` (`logo.png`, `splash.png`, `tab_<name>.png`).
      > Dieser Task kann NICHT vom Modell erledigt werden — User-Auswahl.
- [x] **D5.3 Logo + Splash einbinden** — bereits in D1.2/D1.6 verdrahtet
      (Header + Login-Seite), Fallback-Pfad (keine Datei vorhanden) ist der
      aktuell aktive, verifizierte Zustand.
- [ ] **D5.4 (Optional) Tab-Icons** — nur falls D5.2 Icons liefert.

## D6 — Konsistenz-Pass + Generalprobe (zuletzt, vor der Vorstellung)

- [ ] **D6.1 Tab-für-Tab-Restesuche** — alle 13 Tabs headless rendern und
      eine Checkliste hier eintragen (pro Tab: ungestylte Panels?
      Kontrast? Chart im Theme? Emoji-Reste, die durch LEDs ersetzt
      gehören?). Funde als Mini-Tasks D6.1a, D6.1b, … direkt hier anfügen.
- [ ] **D6.2 Plain-Generalprobe** — kompletter Klick-Durchlauf mit
      `DASHBOARD_THEME=plain` (Notausstieg funktioniert wirklich, altes
      Aussehen intakt).
- [ ] **D6.3 Screenshot-Foliensatz** — pro Tab ein Screenshot (SSH-Tunnel,
      Browser) nach `scratchpad/screenshots_design/` als Präsentations-
      Fallback, falls live etwas klemmt.
- [ ] **D6.4 Generalprobe** — Vorstellung einmal komplett durchspielen
      (Vollbild, Demo-Reihenfolge: Header/KPIs → Live-Leitstand →
      Förderband-Funnel → Portfolio); Stolperer als Task notieren.

## Vision W — Interaktives Fabrik-Wimmelbild (NACH der Vorführung)

User-Idee 15.7.: Das Dashboard soll langfristig wie ein interaktives
Wimmelbild wirken — eine lebende Fabrikhalle, in der die Maschinen arbeiten
und man immer Neues entdeckt. **Kernprinzip (macht es zu mehr als Deko):
jede Maschine ist ein echtes Subsystem, ihr Zustand kommt aus echten
Daten** — die Szene ist eine dritte Darstellungsform neben Tabellen und
Charts, kein Hintergrundbild. Eigener Tab („Fabrik"), ersetzt keine
Daten-Tabs. Gated: erst nach D0–D6 + Vorführung; lebt erst richtig, wenn
der Bot wieder läuft.

Maschinen-Mapping (Datenquellen existieren alle schon): Laderampen =
Collectors (`source_health`; tote Quelle = dunkle Rampe) · großer
Analysator vs. kleine Werkbank = Claude vs. Ollama (Frugal-Routing sichtbar)
· Förderband+Sortier-Arme = Entscheidungs-Funnel (D4 ist der Keim) ·
Hochregallager = Portfolio (Kisten = Positionen, Farbe = P&L) · Not-Aus-Pilz
= Circuit-Breaker · Verladetor = IB Gateway (Orders verlassen die Halle) ·
Wetterstation = Regime (`current_regime.json`) + echter Wetter-Collector ·
Qualitätslabor = Lern-Stack/Experience Store · Nachtschicht-Roboter =
Backup-Timer 03:00 · Werksuhr/Schichtplan = Scheduler (`bot_status.json`).
Entdeckungs-Ebene: seltene ECHTE Zustände = seltene Szenen-Ereignisse
(Earnings-Sperre, SL-Cooldown, EONET-Hazards, erster Live-Trade, These
PROVEN), Tag/Nacht nach echter Uhrzeit. Kein Zufalls-Deko-Generator.

Etappen W1 (Hallen-Skelett) → W2 (zustandsgetriebenes Leben) → W3
(Tooltips + Klick-Fokus) → W4 (Entdeckungs-Ebene/Easter Eggs) → W5
(Pixel-Art-Ausbau je Maschine). **Die feingranularen Einzel-Tasks samt
festgelegter Bau-Entscheidungen (Paketstruktur, Maschinen-IDs, Datenmodell,
LAYOUT, Datenquellen-Tabelle) stehen in `docs/DESIGN_FABRIK.md`** — dort
gilt dasselbe Arbeitsprotokoll wie hier. Ehrliche Grenzen: größter Aufwand
ist Asset-Arbeit, nicht Code; Performance-Regel: Animationen rein im
Browser (CSS/SMIL), nie pro Rerun rechnen.

## Bewusst NICHT geplant

- ✗ Streamlit ersetzen (eigenes Frontend): unverhältnismäßig für ein
  internes Betriebs-Werkzeug.
- ✗ Spielszene als ERSATZ der Daten-Tabs: zerstört die Datendichte. Die
  Fabrik-Szene (Vision W) ist bewusst ein ZUSÄTZLICHER Tab; in den
  Daten-Tabs wird das Zielbild über Palette, Panels, LEDs, Terminal-Look
  und die zwei Motiv-Visuals (D3/D4) transportiert.
- ✗ Sound-Effekte.

## Reihenfolge & Minimal-Paket

D0 → D1 → dann D2/D3/D4 in beliebiger Reihenfolge (D4 fest vor der
Vorstellung einplanen), D5 parallel sobald User Bilder liefert, D6 zuletzt.
**Minimal-Paket bei Zeitdruck: D0 + D1 + D2** — ergibt bereits ein
durchgängig kohärentes Industrie-Theme.
