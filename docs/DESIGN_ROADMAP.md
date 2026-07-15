# Design-Roadmap — Dashboard im 16-Bit-Industrieautomations-Stil

Stand: 15.7.2026. Zielbild ist der vom User am 13.7. benannte Stil
("Industrial automation pixel art", 16-Bit, isometrisch: Förderbänder mit
Datenwürfeln, Sortier-Roboterarme, Terminal mit blinkenden Lichtern;
Game-Dev-Asset-Kit-Optik). Umsetzung **schrittweise, wenn Kapazität frei
ist** — jeder Block ist einzeln shippbar und lässt das Dashboard jederzeit in
einem vorzeigbaren Zustand. **Deadline: fertig zur Programm-Vorstellung.**

Legende: `[x]` fertig · `[~]` teilweise · `[ ]` offen

## Leitplanken (gelten für JEDEN Block)

- **Lesbarkeit vor Stil.** Das ist ein datendichtes Finanz-Werkzeug: Zahlen,
  Tabellen und Log-Texte bleiben in gut lesbarer Schrift mit hohem Kontrast.
  Pixel-Fonts (z.B. "Press Start 2P") NUR für Überschriften/Akzente — nie
  für Kennzahlen, Tabellen oder Fließtext (dort allenfalls ein gut lesbarer
  Terminal-Mono wie VT323/IBM Plex Mono).
- **Reine Präsentationsschicht.** Kein Trading-/Daten-Code wird angefasst;
  alle Änderungen leben in `dashboard/` + `.streamlit/`. Kein Risiko für
  Bot-Logik oder Testsuite.
- **Eine Quelle für den Stil.** Farben/Fonts/CSS zentral in einem neuen
  `dashboard/theme.py` (+ `.streamlit/config.toml`) — Tabs importieren von
  dort, nichts wird pro Tab hart kodiert (dieselbe Disziplin wie
  [[stock-relations-single-source]]).
- **Abschaltbar.** Ein zentraler Schalter (ENV `DASHBOARD_THEME=pixel|plain`,
  Default pixel nach D0) — falls etwas bei der Vorstellung klemmt, ist das
  alte neutrale Aussehen einen Neustart entfernt.
- **Performance.** Nur CSS/Statik — keine Animationen, die pro
  Streamlit-Rerun neu rechnen; blinkende LEDs &c. laufen als reine
  CSS-Keyframes im Browser.

## D0 — Design-Fundament (Voraussetzung für alles Weitere)

- [ ] **D0.1 Farbpalette fixieren** — exakte Hex-Werte für Kobaltblau
      (Primär/Akzente), Kupfer (Warn-/Sekundärakzent), Stahlgrau-Stufen
      (Hintergründe, dunkles Theme) und Neon (Erfolg/Alarm, sparsam) als
      Konstanten in `dashboard/theme.py`. Ampel-Semantik bleibt erhalten:
      grün/gelb/rot müssen auch im neuen Schema eindeutig unterscheidbar
      sein (Farbenblind-Check: nicht nur über Farbe codieren, Icons behalten).
- [ ] **D0.2 `.streamlit/config.toml`** — Streamlit-Theme (base dark,
      primaryColor, backgroundColor, secondaryBackgroundColor, textColor,
      font) aus D0.1 ableiten. Datei existiert noch nicht — Greenfield.
- [ ] **D0.3 Zentrale CSS-Injektion** — `theme.py::inject()` einmal in
      `app.py` direkt nach `st.set_page_config()` aufrufen; lädt Fonts
      (lokal gebundelt, KEIN CDN — Dashboard läuft auch ohne Internet),
      definiert CSS-Klassen für Panels/LEDs/Terminal-Look, die spätere
      Blocks nutzen.
- [ ] **D0.4 Verifikation** — headless AppTest-Durchlauf aller Tabs (keine
      Exceptions), Vorher/Nachher-Screenshot als Abnahme-Basis.

## D1 — Sichtbare Quick-Wins (Header, KPIs, Ampel)

Wirkt auf jeder Seite, kleinster Aufwand pro Sichtbarkeit:

- [ ] **D1.1 Header/Titelzeile** — Pixel-Font-Titel + kleines Logo-Placeholder
      (bis D5 echte Pixel-Art liefert), Stahlgrau-Panel-Optik.
- [ ] **D1.2 Gesundheits-Ampel → Terminal-LEDs** — die bestehende
      Ampelleiste (IB-Gateway/Claude-Kosten/Circuit-Breaker, 1.5d) als
      blinkende Status-LEDs im Leitstand-Look (CSS-Keyframe-Puls nur bei
      Warn-/Fehlzustand; grün leuchtet statisch — Dauerblinken nervt).
- [ ] **D1.3 KPI-Leiste** — `st.metric`-Karten als Industriepanel gestylt
      (Rahmen, Nieten-/Schrauben-Ecken per CSS, Kupfer-Akzent bei Deltas).
- [ ] **D1.4 Tab-Leiste** — Icons/Beschriftung konsistent, aktive
      Tab-Markierung in Kobaltblau.

## D2 — Chart-Theming (einheitliche Datenvisualisierung)

- [ ] **D2.1 Altair-Theme** — zentrales Theme (dunkler Hintergrund,
      Palette aus D0.1, Mono-Achsenbeschriftung) via
      `alt.themes.register()` in `theme.py`; betrifft `tabs/regime.py`,
      `tabs/portfolio.py`.
- [ ] **D2.2 Plotly-Template** — dito als `plotly.io.templates`-Eintrag;
      betrifft `tabs/network.py`.
- [ ] **D2.3 Konsistenz-Regel** — neue Charts müssen das zentrale Theme
      nutzen (kurzer Hinweis in `theme.py`-Doku), damit nichts
      zurückdriftet.

## D3 — Live-Tab als „Leitstand" (thematisches Herzstück Nr. 1)

Der Tab, der das Terminal-Motiv am natürlichsten trägt:

- [ ] **D3.1 Aktivitätsfeed als Terminal-Log** — Mono-Font, dunkles
      Panel, dezenter CRT-Look (Scanline/Glow per CSS, subtil), Events
      farbcodiert (Trade neon, Gate-Block kupfer, Zyklus kobalt).
- [ ] **D3.2 Zyklus-Zeitleiste als Fertigungsstraße** — die Phasen
      (Start → Exits → Vorladen → Analyse) als Stationen einer Linie mit
      Fortschritts-Markern statt nackter Liste.
- [ ] **D3.3 Order-Historie als Ausgabeschacht** — gefüllte Orders als
      „gestanzte" Einträge, Fehler-Orders mit Warn-LED.

## D4 — Entscheidungs-Funnel als Förderband (Vorzeige-Stück Nr. 2)

Das Motiv „Förderband mit Datenwürfeln + Sortier-Arme" passt EXAKT auf den
bestehenden Entscheidungs-Funnel (analysiert → Gates → Kauf/Skip):

- [ ] **D4.1 Statisches Förderband-SVG** — Eigenbau-Komponente
      (`st.html`/SVG, kein externes JS): Datenwürfel = analysierte Titel
      laufen über ein Band, Sortier-Arme = die Gate-Kategorien
      (Schwelle/Quellen/Korrelation/…) werfen SKIPs in beschriftete
      Behälter, durchgelaufene Würfel = Käufe. Zahlen aus dem echten
      `decision_log`-Funnel des gewählten Tages — das Visual IST die
      Funnel-Statistik, kein Deko-Bild daneben.
- [ ] **D4.2 (optional, nur wenn D4.1 trägt)** — dezente CSS-Animation
      (laufendes Band). Abschalten, falls es vom Inhalt ablenkt.

## D5 — Echte Pixel-Art-Assets (braucht Bild-Generierung)

- [ ] **D5.1 Asset-Liste + Generierung** — Logo/Banner (Header), Splash
      fürs Login-Gate (`dashboard/auth.py` — großer Effekt, null Risiko),
      evtl. 12 Tab-Icons. Generierung mit dem User-Prompt vom 13.7. als
      Basis (isometrisch, Kobalt/Kupfer/Stahl/Neon); **User wählt die
      finalen Bilder aus** (Geschmacksfrage).
- [ ] **D5.2 Einbindung** — Assets nach `dashboard/assets/` (Repo, damit
      Restore/Umzug sie mitnimmt), Base64-eingebettet oder via
      `st.image` — kein externer Host.

## D6 — Konsistenz-Pass + Generalprobe (vor der Vorstellung)

- [ ] **D6.1 Alle 13 Tabs durchgehen** — Reste-Suche: ungestylte Panels,
      Kontrast-Probleme, überlange Texte, Chart-Ausreißer.
- [ ] **D6.2 Präsentations-Durchlauf** — Dashboard im Vollbild wie bei der
      Vorstellung durchklicken (per SSH-Tunnel), Screenshot-Satz als
      Fallback-Foliensatz exportieren.
- [ ] **D6.3 Plain-Fallback testen** — `DASHBOARD_THEME=plain` einmal
      durchrendern (Notausstieg funktioniert wirklich).

## Bewusst NICHT geplant

- ✗ **Streamlit ersetzen** (eigenes Frontend/React): unverhältnismäßig —
  das Dashboard ist ein internes Betriebs-Werkzeug, kein Produkt-Frontend.
- ✗ **Isometrische 3D-Spielszene als UI**: Deko-Vollbild würde die
  Datendichte zerstören; das Zielbild wird über Palette, Panels, LEDs,
  Terminal-Look und die zwei Motiv-Visuals (D3/D4) transportiert.
- ✗ **Sound-Effekte**: bei einer Live-Vorstellung eher peinlich als cool.

## Reihenfolge & Aufwand (grob)

| Block | Aufwand | Sichtbarkeit | Wann |
|-------|---------|--------------|------|
| D0    | klein   | indirekt     | zuerst (Fundament) |
| D1    | klein   | hoch         | direkt nach D0 |
| D2    | klein   | mittel       | nach D1, unabhängig von D3/D4 |
| D3    | mittel  | hoch         | wenn Kapazität frei |
| D4    | mittel–groß | sehr hoch (Vorzeige-Stück) | vor der Vorstellung fest einplanen |
| D5    | klein (Arbeit) + User-Auswahl | hoch | parallel möglich, braucht User |
| D6    | klein   | —            | letzter Schritt vor der Vorstellung |

Minimal-Paket, falls die Vorstellung früher kommt als gedacht:
**D0 + D1 + D2** ergibt bereits ein durchgängig kohärentes Industrie-Theme;
D4 ist das Sahnestück, D5/D6 der Feinschliff.
