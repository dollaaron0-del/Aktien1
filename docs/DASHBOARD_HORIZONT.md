# Dashboard-Horizont — Ausbau-Roadmap über den Tellerrand

> Erstellt 15.7.2026 auf User-Wunsch: Erweiterungen **unabhängig von der
> Programm-Vorstellung**, für freie Kapazitäten. Kein Zeitplan, keine
> Reihenfolge-Pflicht — jeder Punkt ist einzeln nehmbar. Ergänzt
> `DESIGN_ROADMAP.md` (D0–D7, Vorstellung) und `DESIGN_FABRIK.md` (W1–W5,
> Wimmelbild); ersetzt keins von beiden.

## Spielregeln (gelten für JEDEN Punkt)

1. **Echte Daten, keine Deko** — das Kernprinzip des ganzen Designs gilt
   weiter. Jedes neue Element hängt an einer echten Datenquelle.
2. **Bot-Pause bleibt gesperrt** — kein Punkt hier startet/enabled den Bot.
   Bedien-Elemente (H1) führen nur Aktionen aus, die der User **selbst und
   bewusst im Dashboard klickt**.
3. **Fail-open** — kein neues Feature darf das Dashboard crashen können.
4. **Keine Bezahl-Dienste ohne User-Entscheid**, kein CDN, keine
   Laufzeit-Netzabhängigkeit im Render-Pfad.
5. **Streamlit zuerst** — Plattform-Sprünge (H6) nur nach Messung/Begründung.
6. Arbeitsprotokoll wie gehabt: nur `dashboard/`, `tests/test_dashboard_*`,
   Design-Docs; Verifikation pixel+plain vor jedem Commit.

Aufwand: **S** ≈ Stunden · **M** ≈ ein Abend · **L** ≈ mehrere Sitzungen.
`[USER]` = braucht Entscheidung oder Zutun des Users.

---

## H1 — Vom Anzeigen zum Bedienen (Leitstand → Steuerpult)

Das Dashboard zeigt heute nur. Ein Leitstand hat aber Schalter. Alle
Aktionen: mit Bestätigungs-Dialog, Protokoll ins decision_log/Feed, und
klarer Anzeige, WER es war (Dashboard-Login).

- [ ] **H1.1 Pause-Schalter im Dashboard** (M) — der große Hebel an der
      Werksuhr: `bot_control.pause()/resume()` per Klick + Bestätigung.
      Ersetzt kein systemd (Service muss laufen), aber macht die bewusste
      Pause/Weiter-Entscheidung sichtbar und bedienbar. DoD: Klick →
      Zustand kippt, Feed-Eintrag, Fabrik zeigt Nachtmodus sofort.
      *Hinweis: Das ist die USER-Handlung — die Sperre „nicht eigenmächtig
      reaktivieren" gilt für das Modell, nicht für diesen Knopf.*
- [ ] **H1.2 Ticker-Schnellanalyse** (S) — im Fabrik-Tab: Ticker eintippen
      → landet in der user_request_queue (existiert), mit Rückmeldung wann
      der nächste Zyklus ihn nimmt. Heute geht das nur im Log-Tab; im
      Leitstand wäre es eine „Werksauftrag einwerfen"-Klappe an den Docks.
- [ ] **H1.3 Not-Aus-Reset mit Zwei-Schritt-Bestätigung** (M) — wenn der
      Circuit-Breaker ausgelöst hat, zeigt der Not-Aus-Pilz einen
      Reset-Hebel (CircuitBreaker-State zurücksetzen). Zweistufig
      (Schieber + Tippen von „RESET"), Protokoll-Pflicht.
- [ ] **H1.4 Positions-Notizen** (S) — pro offener Position ein freies
      Notizfeld (eigene kleine Tabelle `data/position_notes.db`), im
      Portfolio-Tab und im Lager-Detail-Panel sichtbar. Der Bot liest sie
      NICHT (reine Gedächtnisstütze) — ehrlich dokumentieren.
- [ ] **H1.5 „Was würde der Bot jetzt tun?"-Trockenlauf** (L) — Knopf, der
      für EINEN Ticker die komplette Analyse-Pipeline read-only durchspielt
      (Frugal-Routing respektieren! Standard: nur Ollama/mechanisch, Claude
      nur mit explizitem Haken) und den Entscheidungsweg anzeigt, ohne
      Order/Log-Schreibung. DoD: Ergebnis-Panel mit Funnel-Weg + Begründung.

## H2 — Zeitreise & Replay (die Daten liegen schon da)

analysis_log, decision_log, experience.db, order_log, activity_feed —
alles hat Zeitstempel. Niemand schaut sie als *Zeit* an.

- [ ] **H2.1 Zustands-Schnappschüsse** (M) — `read_state()` (Fabrik) 1×/Zyklus
      als JSON-Zeile in eine kleine Historie (`data/factory_history.jsonl`,
      rotierend). Ohne das bleibt Zeitreise teuer; mit ihm wird H2.2/H2.3
      billig. DoD: Schreiber + Leser + Test, Datei wächst gedeckelt.
- [ ] **H2.2 Zeitreise-Regler im Fabrik-Tab** (M, braucht H2.1) — Slider
      „Datum/Uhrzeit" → Fabrik rendert den historischen Zustand (die Szene
      ist eine reine Funktion State→SVG, genau dafür gebaut). Banner
      „ARCHIV-ANSICHT" unübersehbar.
- [ ] **H2.3 Tages-Replay** (M) — einen gewählten Handelstag als Ablauf:
      Feed-Events + Entscheidungen in Echtzeit-Raffung (z.B. 1 Min = 1 Sek)
      durchspielen, Fabrik animiert mit. Der „Wimmelbild wird Film"-Moment.
- [ ] **H2.4 Wochen-Vergleich** (S) — zwei Zeiträume nebeneinander:
      Funnel-Zahlen, Win-Rate, Kosten, Quellen-Health als Delta-Tabelle.
      Reine Aggregation vorhandener Logs.

## H3 — Erklärbarkeit („Warum?" als eigene Ansicht)

Die Provenienz-Daten existieren (sources_breakdown, model_route,
skip_reasons) — sie verdienen mehr als Expander-Text.

- [ ] **H3.1 „Warum nicht?"-Explorer** (M) — Ticker wählen → alle Gates,
      die er durchlief, als Weichenstrecke (Liquiditäts-Gate, Breadth,
      SL-Cooldown, Korrelation, Kapital, …) mit grün/rot je Weiche und dem
      echten Blockier-Grund aus dem decision_log. Beantwortet DIE
      Standardfrage („warum hat er X nicht gekauft?") in einer Grafik.
- [ ] **H3.2 Entscheidungs-Genealogie** (M) — von einer Order rückwärts:
      Order → Signal → Analyse → Quellen (provenance). Als Stammbaum-
      Diagramm; Klick auf jede Stufe zeigt den Roh-Eintrag.
- [ ] **H3.3 Kalibrier-Kurve live** (S) — Experience-Store: gesagte
      Konfidenz vs. eingetretene Trefferquote als Verlässlichkeits-Diagramm
      (die Daten aus dem Selbstlern-Fundament, 78+ gelabelte Trades).
      Ehrlichkeits-Feature: zeigt auch, WO der Bot sich überschätzt.

## H4 — Lern-Fortschritt sichtbar (Anschluss an Vision V1/V2)

Die Nordstern-Frage ist „Kante beweisen". Das Dashboard sollte den
Beweis-Fortschritt zeigen, nicht nur Tageszahlen.

- [ ] **H4.1 Thesen-Board** (M) — die kodierten Erfolgskriterien
      (`thesis_verdict.py`, 150 Trades/24 Monate) als Fortschritts-Tafel:
      je These ein Balken (Trades gesammelt / nötig), Status
      PROVEN/PENDING/FALSIFIED als Plakette. Die goldene Statue (W4.4)
      bekommt damit ihren Kontext.
- [ ] **H4.2 Regime-Landkarte** (M) — per-Regime-Kalibrierung
      (Lern-Stack-Erweiterung 2.7.) als Matrix: Regime × Konfidenz-Stufe →
      echte Trefferquote, Zellenfarbe = Verlässlichkeit. Zeigt, in welchem
      Wetter der Bot fahren kann.
- [ ] **H4.3 Paper-Forward-Fieberkurve** (S) — die ehrliche Bilanz
      (Live-Bridge-Fix 24.6.) laufend als Chart: Strategie vs. Buy&Hold,
      mit Stichproben-Warnband solange n klein ist.

## H5 — Fernblick & Weitergabe

- [ ] **H5.1 Wochen-Report-Export** (M) — ein Klick → in sich
      geschlossene HTML-Datei (Inline-CSS, keine externen Abhängigkeiten)
      mit KPIs, Funnel, Fabrik-SVG-Momentaufnahme, Top-Entscheidungen der
      Woche. Teilbar/archivierbar ohne laufendes Dashboard.
- [ ] **H5.2 [USER] Zuschauer-Modus** (M) — zweites, read-only-Passwort:
      Settings-Tab (schreibt .env!) und alle H1-Schalter ausgeblendet.
      Nützlich, wenn nach der Vorstellung jemand „mal reinschauen" will.
      *User-Entscheid: soll es überhaupt Fremd-Einblick geben?*
- [ ] **H5.3 Telegram-Rückverweis** (S) — wichtige Telegram-Nachrichten
      (TELEGRAM_MODE=important existiert) bekommen einen Deep-Link
      `?factory=<maschine>` bzw. Tab-Anker in den Text. Vom Handy-Alarm
      direkt zur richtigen Maschine im Leitstand.

## H6 — Plattform & über Streamlit hinaus (nur nach Messung)

- [ ] **H6.1 Kiosk-Modus** (S) — URL-Param `?kiosk=1`: nur die Fabrik,
      ohne Chrome/Tabs, Auto-Refresh — als Dauer-Wandbild auf einem
      Zweitmonitor. Der billigste „Wow"-Punkt dieser Liste.
- [ ] **H6.2 Handy-Kompaktansicht** (M) — `?mobile=1` oder
      Viewport-Erkennung: die 5 wichtigsten Zahlen + Ampel + Mini-Fabrik
      untereinander. Kein PWA-Vollausbau, nur ehrliche Verkleinerung.
- [ ] **H6.3 [USER] Canvas/WebGL-Fabrik** (L) — NUR falls die SVG-Szene
      nach W5-Assets + H2.3-Replay sichtbar ruckelt (Messkriterium steht
      in DESIGN_FABRIK W5.4). Dann: Evaluation Pixi.js o.ä. als
      Static-Bundle (kein CDN), Ergebnis erst als Notiz, nicht als Umbau.
- [ ] **H6.4 Zweit-Theme „Blaupause"** (S) — die Theme-Architektur kann
      mehr als pixel/plain: ein drittes Schema (technische Zeichnung,
      weiß auf blau) als Beweis der Sauberkeit UND als seriöse Ansicht
      für Zahlen-Puristen. Rein `theme.py`, kein Tab-Code.

## H7 — Charakter weiter (der Tamagotchi-Faktor)

- [ ] **H7.1 Werksleiter-Stimmung** (S) — kleines Gesicht im Header,
      Ausdruck = echter Bot-Score (BotScorer existiert): >75 zufrieden,
      40–75 neutral, <40 besorgt. Ein Blick ersetzt drei Panels.
- [ ] **H7.2 Plaketten-Wand** (M) — Errungenschaften aus echten
      Meilensteinen: erster Live-Trade, 100. gelabelter Trade, 30 Tage
      ohne Not-Aus, erste PROVEN-These, 1 Jahr Laufzeit. Einmal erreicht =
      bleibt (eigene kleine Datei); Fabrik-Wand zeigt die Plaketten.
- [ ] **H7.3 Schichtbuch** (M) — automatisch geführtes Werkstagebuch:
      1×/Tag fasst der LOKALE Ollama (kein Claude-Budget!) die
      Feed-Ereignisse zu 3 Sätzen Prosa zusammen („Ruhige Schicht,
      14 Analysen, keine Trades. GILD-Sperre ausgelaufen. Backup 02:00
      OK."), abrufbar als blätterbares Buch. Charme + echtes Protokoll.

---

## Priorisierungs-Hilfe (wenn Kapazität da ist, in dieser Reihenfolge)

| Rang | Punkt | Warum zuerst |
|------|-------|--------------|
| 1 | H6.1 Kiosk-Modus | S-Aufwand, sofort sichtbarer Nutzen |
| 2 | H3.1 „Warum nicht?"-Explorer | beantwortet die häufigste echte Frage |
| 3 | H2.1 + H2.2 Zeitreise | einmalige Grundlage, dann vieles billig |
| 4 | H4.1 Thesen-Board | verbindet Dashboard mit dem Nordstern |
| 5 | H1.1 Pause-Schalter | erster echter Steuerpult-Schritt |

Alles andere nach Lust — die Punkte sind unabhängig, sofern nicht
anders vermerkt (H2.2/H2.3 brauchen H2.1).
