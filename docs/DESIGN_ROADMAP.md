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

- [x] **D2.1 Altair-Theme** — `theme._altair_theme()` + Registrierung/Enable
      in `register_chart_themes()`. Transparenter Hintergrund, Achsen/Grid
      in border-Farbe, Labels text_muted, kategoriale Range aus der Palette.
      Betrifft `tabs/regime.py`/`tabs/portfolio.py` (Linienfarben dort bleiben
      bewusst hartkodiert — nur Hintergrund/Achsen/Labels ändern sich, kein
      Tab-Code angefasst). Tests: Theme wird registriert+aktiviert, plain
      lässt "default" aktiv, Kategorie-Range nutzt PALETTE.
- [x] **D2.2 Plotly-Template** — `pio.templates["pixel"]` + `default=
      "pixel"`. Ehrlicher Befund: der bestehende `tabs/network.py`-Graph
      setzt `paper_bgcolor`/`plot_bgcolor`/`font` bereits explizit im
      `go.Layout(...)` selbst (Zeile ~532) — diese Werte übersteuern jedes
      Template. Das Template ist damit korrekt registriert und für
      KÜNFTIGE Plotly-Charts ohne eigene Farbwerte wirksam, verändert den
      bestehenden Netzwerk-Graphen aber optisch nicht. Kein Tab-Code
      angefasst (wie gefordert). Test: `pio.templates.default == "pixel"`.
- [x] **D2.3 Drift-Schutz** — Kommentarblock in `theme.py` + Idempotenz-Test
      (zweifacher Aufruf wirft nicht). Verifikation (pixel+plain) OK.

## D3 — Live-Tab als „Leitstand" (nach D0+D1)

- [x] **D3.1 Aktivitätsfeed als Terminal-Log** — Event-Zeilen als EIN
      `.px-terminal`-Block, Farbcodierung per CSS-Var (trade neon_green,
      gate_blocked copper, cycle_start/end cobalt, analysis_done text-Farbe),
      alles `html.escape()`d. Plain nutzt weiter die alte Zeilen-für-Zeile-
      Darstellung. 5 neue Tests (`test_dashboard_live_tab.py`, geseedete
      Temp-`ActivityFeed`), Verifikation OK.
- [x] **D3.2 Zyklus-Zeitleiste als Fertigungsstraße** — feste Stationen-
      Reihenfolge (Start/Exits prüfen/Vorladen/Analyse), Punkte + Verbindungs-
      linie, laufende Station pulsiert (cobalt, `px-blink`-Klasse
      wiederverwendet), abgeschlossen neon_green, Dauer in VT323. Unbekannte
      Phasennamen werden hinten angehängt statt verschluckt. Verifikation OK.
- [x] **D3.3 Order-Historie** — `theme.led()` statt Emoji-Icon fürs
      Fill-/Fehler-/Storno-Symbol, Teilausführung als copper-Badge, Rest
      escaped. Verifikation OK.
- [x] **D3.4 Nächste-Aktionen/Timer-Panel** — Timer-Zeilen in `.px-panel` +
      VT323. Verifikation OK.

## D4 — Entscheidungs-Funnel als Förderband (Vorzeige-Stück; nach D0+D1)

- [x] **D4.1 SVG-Baustein** — `dashboard/conveyor.py::build_conveyor_svg()`.
      Einlauf mit Gesamtzahl, Band, pro Top-5-SKIP-Grund ein Sortier-Arm in
      einen beschrifteten copper-Behälter (Rest als "…"), Auslauf BUY
      (neon_green) + HOLD/SELL-Kasten. Farben aus `theme.PALETTE`, VT323,
      alles `html.escape()`d. 9 Tests (`test_dashboard_conveyor.py`).
- [x] **D4.2 Einbindung** — `tabs/decisions.py` rendert das SVG oberhalb der
      Fortschrittsbalken, nur bei `theme.is_enabled()`; die alten
      `st.progress`-Balken bleiben (Zahlen-Detail + plain-Fallback). 2 neue
      Tests gegen den echten `DecisionLog`-Singleton-Pfad.
- [x] **D4.3 Band-Animation** — Band bekommt eine zweite, gemusterte Fläche
      (`px-belt-pattern`, diagonale Streifen) mit CSS-Keyframe
      `px-belt-scroll`; `@media (prefers-reduced-motion: reduce)` schaltet
      sie UND die LED-Blink-Keyframes ab. Alles im Browser, keine
      Streamlit-Rerun-Kosten. Verifikation (pixel+plain) OK.

## D5 — Echte Pixel-Art-Assets (parallel möglich; D5.2 braucht den User)

- [x] **D5.1 Asset-Infrastruktur** — `dashboard/assets/img/` angelegt
      (`.gitkeep`); `theme.image_b64(name)` bereits in D0.2 gebaut + getestet.
- [x] **D5.2 Bilder generieren + auswählen** — Umgesetzt 18.7.2026 mit
      Figma-MCP (Dev-Sitz, s. `figma-zugang-asset-generierung`), gleiche
      Pixel-Raster-Technik wie W5.2: (a) `logo.png` — kompaktes
      quadratisches Emblem (256×256, Candlestick-Chart-Terminal mit
      Stahl-Bezel + Kupfer-Nieten), tatsächlich nur `height:2.2em` im
      Header gerendert (`app.py:182`) — daher bewusst quadratisch statt
      der ursprünglich geschätzten 600×120-Banner-Form. (b) `splash.png`
      — 800×400-Szene (Terminal mit blinkenden Lichtern, Förderband mit
      3 leuchtenden Datenwürfeln, Sortier-Roboterarm mit Greifer),
      Industrie-Neon-Palette aus `theme.py::_PALETTE_PIXEL`. (c) Tab-Icons
      NICHT gebaut: `st.tabs()` nimmt nur reine Text-Labels, kein Bild-
      Einbaupfad vorhanden (geprüft) — D5.4 dadurch gegenstandslos.
- [x] **D5.3 Logo + Splash einbinden** — bereits in D1.2/D1.6 verdrahtet
      (Header + Login-Seite); mit echten Dateien (18.7.) verifiziert
      (pixel+plain OK, `test_dashboard_theme.py`+`test_dashboard_auth.py`
      41/41 grün).
- [x] **D5.4 (Optional) Tab-Icons** — entfällt (s. D5.2c): `st.tabs()`
      unterstützt keine Bild-Labels, kein Code-Anknüpfungspunkt vorhanden.

## D6 — Konsistenz-Pass + Generalprobe (zuletzt, vor der Vorstellung)

- [x] **D6.1 Tab-für-Tab-Restesuche** — Voll-Render (alle 12 Tabs, `st.tabs()`
      führt jeden Tab-Body unabhängig vom Klick-Zustand aus, damit sind ALLE
      bei jedem Verifikations-Lauf mitgeprüft): 0 Exceptions, 525
      Markdown-Elemente, 277 Metrics, 3 `st.error`-Banner — alle drei sind
      die erwarteten „Bot pausiert"-Hinweise, keine echten Fehler.
      Grundfarben/Fonts/Kontrast wirken bereits dashboard-weit über
      `.streamlit/config.toml` (Streamlit-natives Theming, nicht
      Tab-spezifisch) — auch unberührte Tabs sehen dadurch stimmig dunkel/
      kobalt aus. **Echter Rest-Befund:** `tabs/log.py`, `tabs/portfolio.py`,
      `tabs/watchlist.py`, `tabs/queue.py`, `tabs/trades.py` nutzen weiterhin
      rohe Status-Emoji (🟢/🟡/🔴/⚪) statt `theme.led()` — funktioniert
      technisch einwandfrei, ist aber optisch nicht auf dem LED-Stil der
      migrierten Tabs (app.py/live.py/decisions.py). Bewusst NICHT in diesem
      Durchgang mitgezogen (5 weitere Dateien, kein eigener D-Task dafür
      vorgesehen) — sauberer Startpunkt für einen Folge-Task
      „D6.1-Nachzug: LED-Migration Restliche Tabs", falls Kapazität bleibt.
      Kein Blocker für die Vorstellung (D0+D1+D2+D4 tragen das Zielbild
      bereits durchgängig).

      **Nachzug 15.7.2026 (nicht beauftragt, auf Nachfrage bestätigt):**
      Durchsuche aller 5 Dateien ergab, dass die meisten der ~25
      Emoji-Stellen sich NICHT sauber auf `theme.led()` ummünzen lassen —
      Streamlit rendert in `st.metric()`-Labels, `st.expander()`-Titeln,
      `st.dataframe()`-Zellen und `st.text()` grundsätzlich kein HTML
      (`led()` liefert im pixel-Modus einen `<span>`). Ein Zwang dorthin
      hätte entweder kaputtes rohes Markup gezeigt oder eine Layout-Änderung
      erfordert (Badge aus dem Titel raus in eine eigene Zeile) — beides
      über den Rahmen einer reinen Stil-Angleichung hinaus. Echte
      LED-Kandidaten waren nur Stellen mit einem plain `st.markdown()`/
      Spalten-`.markdown()`-Aufruf: `tabs/log.py` (Quellen-Health
      Gesund/Schwach/Tot, News-Sentiment-Icon, Bull-/Bear-Case,
      Debatte-Gewinner — 7 Stellen) und `tabs/watchlist.py` (Bench-Score in
      der Warteliste-Tabelle — 1 Stelle). Umgesetzt als `led(status, "")`
      (nur der farbige Punkt per CSS-`::before`, bestehender Text/Label
      bleibt unverändert) statt `led(status, label)`, um die sichtbare
      Information 1:1 zu erhalten. `portfolio.py`, `queue.py`, `trades.py`
      bleiben unverändert — dort stecken alle Emoji in genau den oben
      genannten nicht-HTML-Kontexten. 3 neue Tests
      (`tests/test_dashboard_led_migration.py`), Verifikation pixel+plain OK.
- [x] **D6.2 Plain-Generalprobe** — Voll-Render mit `DASHBOARD_THEME=plain`:
      0 Exceptions, 574 Markdown-Elemente (mehr als pixel, da dort mehrere
      Einzeiler statt EINEM Panel-Block gerendert werden — strukturell
      erwartet), gleiche 277 Metrics, gleiche 3 Pause-Banner. **Ehrlicher
      Befund:** `DASHBOARD_THEME=plain` schaltet den kompletten CSS-/Markup-
      Layer (D1–D4) ab, aber NICHT `.streamlit/config.toml` (D0.1) — die
      Streamlit-Server-Theme-Datei wird vom Framework beim Start gelesen,
      nicht pro Request, und lässt sich zur Laufzeit nicht per ENV-Check
      umgehen. Der Notausstieg liefert also "altes Markup, neue
      Grundfarben", nicht pixelgenau das Aussehen von vor D0. Für den
      eigentlichen Zweck (Vorstellung geht schief → schnell auf neutral
      zurück) reicht das; für 100%ige Rückkehr zum Vor-D0-Zustand müsste
      zusätzlich `.streamlit/config.toml` gelöscht/umbenannt werden
      (Handgriff, kein Code).
- [ ] **D6.3 Screenshot-Foliensatz** — pro Tab ein Screenshot (SSH-Tunnel,
      Browser) nach `scratchpad/screenshots_design/` als Präsentations-
      Fallback, falls live etwas klemmt.
      > OFFEN: braucht echten Browser-Zugriff (z.B. claude-in-chrome-Skill)
      auf den per SSH-Tunnel erreichbaren Server — in dieser Sitzung nicht
      verfügbar/geprüft. Headless-Rendering (AppTest) ersetzt das NICHT,
      da es keine echten Bildschirmfotos liefert. Nachholen, sobald
      Browser-Zugriff besteht.
- [ ] **D6.4 Generalprobe** — Vorstellung einmal komplett durchspielen
      (Vollbild, Demo-Reihenfolge: Header/KPIs → Live-Leitstand →
      Förderband-Funnel → Portfolio); Stolperer als Task notieren.
      > OFFEN: dasselbe Browser-Zugriffs-Problem wie D6.3 — der headless
      Vollrender (D6.1/D6.2, 0 Exceptions in beiden Modi) ist die technische
      Generalprobe; die VISUELLE Generalprobe (sieht es wirklich gut aus?)
      braucht echtes Anschauen im Browser durch den User oder ein Modell
      mit Browser-Zugriff.

## D7 — Charakter-Ausbau (15.7.2026, User-Wunsch: „mehr Charakter,
## schön anzusehen, Infos intelligent im Schema eingebunden")

Vier Richtungen, alle vier vom User per Auswahl bestätigt. Prinzip
unverändert: **kein Element ohne echte Datenquelle** — einzige, bewusst
benannte Ausnahme ist D7.4 (reine Atmosphäre-Optik).

- [x] **D7.1 Leitstand-Instrumente** — neues Modul
      `dashboard/instruments.py` (reine SVG-String-Funktionen, Muster
      `conveyor.py`): (a) **Manometer „Kesseldruck"** = heutiger
      Tagesverlust relativ zum Circuit-Breaker-Limit
      (`CircuitBreaker.status().daily_pct` vs. `MAX_DAILY_LOSS_PCT`),
      (b) **Treibstofftank** = Claude-Tagesbudget
      (`APICostTracker.summary()` today/limit, Füllstand = Rest),
      (c) **7-Segment-Anzeige** für den Depotwert. Einbau in `app.py`
      unter der Gesundheits-Ampel (nur pixel; plain unverändert),
      fail-open. Fertig wenn: Tests + Verifikation pixel+plain OK.

      Umgesetzt 15.7.2026: Manometer mit grün/amber/rot-Zonen (Nabe
      blinkt >85 % via fx-blink → respektiert reduced-motion), Tank mit
      Schwellenfarben (>40 grün, 15–40 amber, <15 rot), 7-Segment mit
      Geister-Segmenten (klassischer LED-Look; Punkt/Komma als Dot,
      unbekannte Zeichen wie € werden übersprungen statt zu crashen).
      24 Tests (`tests/test_dashboard_instruments.py`), Voll-Render
      pixel: 3× px-instrument vorhanden, plain: 0 — beide 0 Exceptions.
- [x] **D7.2 Fabrik-Detailtiefe** — `state.py`/`machines.py`:
      (a) Hochregallager: **eine Kiste je offener Position**, Farbe nach
      Haltedauer-Ratio (grün <80 %, amber <100 %, rot ≥100 % — gleiche
      Logik wie Portfolio-Tab; braucht entry_date/target_hold_days im
      payload), (b) **mechanischer Durchsatz-Zähler** am Förderband
      (funnel total), (c) **Rauch-Intensität** der Analysatoren nach
      echtem Routing-Anteil (payload share), (d) **Batterie-Balken** am
      Nachtschicht-Roboter (Backup-Alter → Ladestand). Tests je Detail.

      Umgesetzt 15.7.2026: neuer `_machine_extras()`-Dispatcher in
      machines.py (Kisten max. 12 + „+n weitere", Zählwerk 3-stellig
      gekappt bei 999, Batterie leer nach 48h, Rauch 1–4 Wolken nach
      Anteil). Warehouse-payload umgestellt auf
      `{"positions": {ticker: {shares, age_ratio}}}` — bewusst
      Haltedauer statt P&L als Kisten-Farbe (read_state() bleibt
      netzwerkfrei, kein Live-Kurs-Abruf); Detail-Panel im Fabrik-Tab
      zeigt die Haltedauer jetzt mit an. 9 neue Tests, Suite
      factory+tab 88 grün.
- [x] **D7.3 Laufband-Anzeigetafel** — LED-Ticker im Kopfbereich
      (`app.py`): letzte echte Ereignisse aus `ActivityFeed.recent()` +
      nächster geplanter Lauf; CSS-Marquee (`.px-ticker`),
      `prefers-reduced-motion`: statisch; plain: entfällt. Alle Inhalte
      escaped.

      Umgesetzt 15.7.2026: `theme.ticker(items)` (Escaping zentral,
      Inhalt verdoppelt für die nahtlose -50%-Schleife, 30s Umlauf),
      Einbau in app.py direkt unter der Kopfzeile via
      `feed_recent(limit=5)` + `read_status().next_run`, fail-open.
      3 neue Tests; Voll-Render zeigt echte Feed-Einträge im Ticker.
- [x] **D7.4 CRT-Atmosphäre** — (a) sehr dezente Scanlines als fixes
      Overlay (pointer-events:none, opacity ≤0.06, per
      `DASHBOARD_CRT=0` abschaltbar), (b) kurze Boot-Sequenz auf der
      Login-Seite (CSS-Typing, `prefers-reduced-motion`: aus). Einzige
      reine Optik im ganzen Design — bewusst und dokumentiert.

      Umgesetzt 15.7.2026: Scanlines+Vignette als `body::after`
      (statisch → kein Motion-Problem; Abschalt-Env geprüft), Boot-
      Sequenz als 3 gestaffelte `.px-boot-line`-Zeilen im `.px-terminal`
      (bewusst statische Texte, keine vorgetäuschten Systemwerte;
      `animation-fill-mode: both` + Basis-opacity 1 → unter
      reduced-motion sofort sichtbar statt unsichtbar). 3 neue Tests;
      Login-AppTest zeigt 3 Boot-Zeilen + intakten Titel-Vertrag.

## D8 — Informations-Ausbau (16.7.2026, User-Wunsch: „Design-Idee erweitern,
## mehr Informationen sinnvoll integrieren")

Vier Richtungen vorgeschlagen, drei vom User bestätigt (Unfalltafel
„X Tage ohne Störfall" bewusst abgewählt). Prinzip unverändert: **kein
Element ohne echte, bisher UNGENUTZTE Datenquelle** — jedes Panel
beantwortet eine Frage, die das Dashboard bisher nicht beantwortet.

- [x] **D8.1 Werksbahnhof-Abfahrtstafel** — neues Modul
      `dashboard/departures.py`: kommende Termine als Bahnhofs-
      Anzeigetafel im Fabrik-Tab. Datenquellen (alle echt, alle bisher
      in keinem Panel): (a) `data/macro_calendar.json` (FOMC/CPI/NFP
      mit Impact-Stufe), (b) Earnings-Termine der aktuellen Watchlist
      (`data/dynamic_watchlist.json` → `EarningsFilter.next_earnings()`,
      yfinance — im Tab per `st.cache_data` gedeckelt, fail-open ohne
      Netz), (c) nächster Bot-Zyklus (`live_status.read_status().next_run`),
      (d) nächstes Backup (`systemctl show aktien_backup.timer`,
      read-only, 2s-Timeout — Muster `controls.service_state()`).
      Beantwortet: „Was kommt auf die Fabrik zu?"

      Umgesetzt 16.7.2026: `upcoming_events()` ruft selbst NIE ins Netz —
      Earnings kommen nur als fertige `extra_rows` rein (der Tab holt sie
      über `_cached_earnings_rows`, `st.cache_data` ttl=6h, Tuple-Argument
      wegen Hashbarkeit). Backup-Termin via `systemctl show
      --property=NextElapseUSecRealtime --value` (Datum wird aus dem
      formatierten String feld-weise geparst; ""/n-a/0/infinity → Zeile
      entfällt). Vergangene/kaputte/>60-Tage-Termine gefiltert; Tafel
      zeigt relative Zeit (heute/morgen/in N Tagen); plain-Theme bekommt
      dieselben Daten als nüchterne `st.table`. 13 Tests
      (test_dashboard_departures.py, netzfrei via injiziertem
      Fake-Filter + gemocktem subprocess). Voll-Render-Verifikation:
      Tafel erscheint mit den ECHTEN FOMC/CPI/NFP-Terminen aus der
      Produktions-macro_calendar.json.
- [x] **D8.2 E-Werk-Stromzähler** — neues Modul
      `dashboard/power_meter.py`: die echten KI-Kosten aus
      `data/api_savings.json` (Pfad via `api_cost_tracker._FILE`,
      read-only) als Drehstromzähler: Tagesverbrauch, Claude-vs-Ollama-
      Anteil, gesparte Beträge (Ollama-Vorprüfung + Cache), 14-Tage-
      Verlaufsbalken. Abgrenzung zu D7.1-Tank (nur heute vs. Limit):
      der Zähler zeigt SPLIT, ERSPARNIS und TREND — der Frugal-Mode
      wird damit erstmals im Dashboard sichtbar. Einbau Fabrik-Tab.

      Umgesetzt 16.7.2026: Zählerscheibe dreht (`fx-spin`, neu in
      theme.py inkl. prefers-reduced-motion-Ausnahme) NUR wenn heute
      wirklich Kosten anfielen — eine stehende Scheibe ist die ehrliche
      Anzeige des pausierten Werks. Verlaufsreihe füllt fehlende Tage
      mit 0 auf (Lücken bleiben sichtbar statt zusammengeschoben);
      Ersparnis = saved + cache_saved. Read-only-Vertrag ist selbst
      getestet (Datei-Bytes vor/nach identisch). plain-Theme: eine
      Caption mit denselben Zahlen. 7 Tests
      (test_dashboard_power_meter.py).
- [x] **D8.3 Lager-Detailregal** — neues Modul
      `dashboard/warehouse_shelf.py`: Positionen als Kisten im
      Hochregal, gruppiert nach Sektor (`data/ticker_profiles.json`,
      read-only): Füllstand = Positionsgröße relativ, Aufkleber =
      P&L (aus `ctx.prices`, kein eigener Netz-Abruf), Etikett =
      Haltedauer + Ziel-Countdown (`entry_date`/`target_hold_days`).
      Einbau Portfolio-Tab über der Positionstabelle (nur pixel,
      plain unverändert); ehrlicher Leer-Zustand („Lager leer") bei
      0 Positionen — aktuell der Normalfall bis zum Neustart.

      Umgesetzt 16.7.2026: Füllstand relativ zur GRÖSSTEN Position
      (min. 8 % damit jede Kiste sichtbar bleibt); ohne Kurs in
      ctx.prices fällt der Wert ehrlich auf den Einstand zurück und
      P&L zeigt „–" statt einer erfundenen Zahl; unbekannte Ticker
      landen im Sektor „Sonstige"; kaputte Positions-Objekte werden
      übersprungen statt das Regal zu reißen. Breites Regal scrollt
      horizontal in eigener Box (overflow-x). 9 Tests
      (test_dashboard_warehouse_shelf.py). Verifiziert: Leerzustand
      gegen das echte (leere) Portfolio + Kisten-Render mit
      synthetischen Positionen (+10 %/−10 % farbrichtig).

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
