# Vision Ruflo — Nordstern & abhakbare Meilensteine

> Stand 12.7.2026. Gegenstück zu ROADMAP.md (Checkliste der Arbeit) und
> DEEP_RESEARCH_2026-07.md (roter Faden): Dieses Dokument sagt, WOHIN das
> Programm als großes Ganzes soll und woran jeder Fortschritt gemessen wird.
>
> **Nordstern:** Ein autonomer Ein-Personen-Quant-Fonds — ein System, das
> eigenständig Hypothesen erzeugt, diszipliniert falsifiziert, bewiesene
> Kanten in ein Portfolio aufnimmt, Kapital zuteilt, überwacht und sterbende
> Kanten wieder entfernt. Der User ist Aufsichtsrat, nicht Operator.
>
> Es gelten unverändert: Bot pausiert (CLAUDE.md), 🔒 Freigabe-Regel für
> Hardware-Features (ROADMAP Block 6).

## Spielregeln für dieses Dokument

- Jedes Ziel hat eine **Definition of Done (DoD)** — abgehakt wird nur, was
  messbar erfüllt ist, nicht was "im Prinzip fertig" ist.
- Stufen bauen aufeinander auf; innerhalb einer Stufe ist die Reihenfolge
  Empfehlung, keine Pflicht.
- **V1.1 (H1-Verdikt) ist die zentrale Weiche:** fällt es negativ aus, wird
  Stufe 4 ersatzlos gestrichen und Stufe 3 zum Nordstern. Das ist kein
  Scheitern, sondern der Zweck des Experiments.
- Anti-Sunk-Cost: Ein verworfenes Ziel wird hier durchgestrichen und mit
  Datum + Grund versehen, nicht gelöscht.

---

## Stufe 0 — Fundament sichern (Voraussetzungen; alter Server + Umzug)

Ohne diese Punkte ist alles Weitere Hasard (ein Plattendefekt oder
Survivorship-Bias entwertet Monate an Arbeit).

- [ ] **V0.1 Risiko-Trio erledigt** — Push-Token aktiv (Code off-server),
      Backup-Timer läuft mit Remote-Ziel inkl. Lern-DBs, Demo-Daten
      zurückgetauscht. *DoD: `git push` funktioniert; ein Restore-Test aus
      dem Remote-Backup wurde einmal real durchgespielt; data/ ist echt.*
- [ ] **V0.2 EDGAR-8-K-Archiv steht** — eigenes, freies Ereignis-Roharchiv.
      *DoD: ≥10 Jahre 8-K-Filings für das Zieluniversum lokal, Manifest-
      Parquet vollständig, Download-Skript idempotent wiederaufsetzbar.*
- [ ] **V0.3 Lab-Validierung komplett** — PIT-Universum + CPCV als zweite
      Achse. *DoD: Walk-Forward läuft mit `--pit-universe`; CPCV-Lauf auf
      einer Bestands-Strategie durchgelaufen; Tests grün.*
- [ ] **V0.4 Survivorship-freie Daten angebunden** (EODHD o. ä., User-Kauf).
      *DoD: Backtest über delistete Ticker läuft nachweislich (mindestens
      ein bekannter Delisting-Fall taucht in den Daten auf).*
- [ ] **V0.5 Server-Umzug abgeschlossen, GPU freigegeben** (6.1-Checkliste).
      *DoD: User-Signal "läuft auf neuem Server"; Bot-Zyklus + Lab laufen
      dort; altes System als Fallback dokumentiert.*

## Stufe 1 — Die Forschungsfabrik läuft ohne dich (Horizont ~1 Jahr)

Vom "User stößt jedes Experiment an" zum geschlossenen Kreislauf
Idee → Test → Verdikt → Archiv.

- [ ] **V1.1 ⭐ H1-VERDIKT LIEGT VOR** — die Event-Study über das annotierte
      8-K-Archiv ist gelaufen (Annotations-Gate → Backfill → Event-Study,
      mit Šidák + Holdout). *DoD: schriftliches Verdikt-Dokument mit
      Bootstrap-CIs; Entscheidung "H1 lebt / H1 tot" ist gefallen und in
      diesem Dokument vermerkt. **Wichtigstes einzelnes Häkchen der
      gesamten Vision.***
- [ ] **V1.2 Hypothesen-Backlog existiert als Artefakt** — nicht im Kopf,
      nicht im Chat. *DoD: versionierte Datei/DB mit je These: Idee,
      erwarteter Informationsgewinn, Test-Design, Status; ≥5 Einträge.*
- [ ] **V1.3 Nacht-Experiment-Runner** — ein Experiment läuft unbeaufsichtigt
      durch und schreibt selbst einen Befund-Report. *DoD: ein kompletter
      Lauf (Start abends, Report morgens) ohne manuellen Eingriff; Report
      enthält Setup, Ergebnis, Verdikt-Vorschlag.*
- [ ] **V1.4 Morgen-Brief** — das System fasst Nachtläufe + offene
      Entscheidungen zusammen (Datei oder Telegram). *DoD: 5 Werktage in
      Folge automatisch erschienen, ohne Spam (max. 1/Tag).*
- [ ] **V1.5 Ein voller autonomer Forschungszyklus** — vom Backlog-Eintrag
      bis zum archivierten Verdikt ohne dass jemand Code anfasst.
      *DoD: mindestens 1 These komplett maschinell abgearbeitet; User hat
      nur priorisiert und das Verdikt abgenickt.*
- [ ] **V1.6 Stufen-Abnahme** — *DoD: 4 Wochen Dauerbetrieb der Fabrik,
      ≥8 abgeschlossene Experimente, jedes mit Verdikt-Dokument; kein
      Experiment ohne Šidák/Holdout-Disziplin.*

## Stufe 2 — Portfolio von Mikro-Kanten (Horizont ~2 Jahre)

Nicht DIE eine Strategie, sondern ein Bündel kleiner, einzeln bewiesener,
möglichst unkorrelierter Effekte — verwaltet wie Fonds-Manager.

- [ ] **V2.1 Erste bewiesene Kante** — irgendeine Strategie besteht die
      volle Promotion. *DoD: Bootstrap-CI-Untergrenze > 0 nach Šidák auf
      Train, bestätigt auf unberührtem Holdout; Registry-Status ACTIVE.*
- [ ] **V2.2 Zweite, unkorrelierte Kante** — *DoD: wie V2.1, zusätzlich
      Korrelation der Equity-Kurven zu V2.1 < 0,5.*
- [ ] **V2.3 Allokator arbeitet datenbasiert** — Kapitalgewichte kommen aus
      dem Meta-Backtest, nicht aus Bauchgefühl. *DoD: Meta-Backtest-Verdikt
      (4.1) positiv: Allokator-Mischung schlägt naive Gleichgewichtung
      out-of-sample.*
- [ ] **V2.4 Degradations-Wächter** — sterbende Kanten fliegen automatisch.
      *DoD: definierte Suspend-Regel (z. B. rollierendes CI schneidet 0)
      implementiert + einmal im Replay/Paper nachweislich ausgelöst.*
- [ ] **V2.5 Kalibrierungs-Deckel** — die bekannte Überkonfidenz
      (46 % gesagt, 34 % real) ist behoben. *DoD: Isotonic/Platt-Schicht
      aktiv; auf rollierenden 50 Trades liegt |vorhergesagt − real| ≤ 5 pp.*
- [ ] **V2.6 Stufen-Abnahme** — *DoD: 6 Monate Paper-Forward des
      Kanten-Portfolios schlagen den Benchmark risikoadjustiert
      (Sharpe/MaxDD), dokumentiert im Monats-Report.*

## Stufe 3 — Persönliches Vermögens-Betriebssystem (Horizont ~3 Jahre)

Mission erweitert: nicht "schlage den Markt", sondern "verwalte das
Gesamtrisiko besser als ETF-Sparplan oder Bankberater". Gilt AUCH, wenn
H1/H2 sterben — dann ist diese Stufe der Nordstern (H3-Produkt).

- [ ] **V3.1 Risiko-Overlay eigenständig validiert** — Diversifikation +
      gestuftes De-Risking als Produkt, losgelöst vom Stock-Picking.
      *DoD: 6 Monate out-of-sample-Bestätigung: Drawdown-Reduktion aus 2.2/2.3
      hält auf neuen Daten (MaxDD ≤ 60 % des B&H-Drawdowns bei ≥ 80 % der
      Rendite).*
- [ ] **V3.2 Overlay steuert das Gesamtdepot** — Cash-Quote/De-Risking
      wirken auf das ganze Vermögen, nicht nur auf Bot-Positionen.
      *DoD: Depot-weite Ziel-Allokation wird berechnet + als Empfehlung
      ausgegeben; 3 Monate Track-Record der Empfehlungen.*
- [ ] **V3.3 Multi-Asset-Risk-Off** — De-Risking kann in mehr als Cash
      (z. B. Anleihen-/Gold-ETF) ausweichen. *DoD: durchs selbe Lab
      backgetestet mit 6.4-Disziplin; nur bei nachgewiesenem Zusatznutzen
      aktiviert, sonst hier als "geprüft, verworfen" vermerkt.*
- [ ] **V3.4 Jahresbilanz-Automatik** — ehrlicher Jahres-Report: System vs.
      ETF-Sparplan, risikoadjustiert, inkl. aller Kosten. *DoD: erster
      automatisch erzeugter Jahresbericht liegt vor.*
- [ ] **V3.5 Stufen-Abnahme** — *DoD: 12 Monate Betrieb (Paper oder klein
      real, User-Entscheid): MaxDD < halber B&H-Drawdown bei ≥ 80 % der
      B&H-Rendite — ODER bewusster, dokumentierter Abbruch dieser Stufe.*

## Stufe 4 — Katalysator-Intelligenz als Burggraben (NUR wenn V1.1 positiv)

Bedingte Stufe. Voraussetzung: Die Event-Study hat gezeigt, dass
LLM-Verständnis von Filings echte Vorlaufinformation liefert. Der Burggraben
ist das eigene, täglich wachsende annotierte Ereignis-Archiv — nicht kaufbar,
nur erlebbar.

- [ ] **V4.1 Tägliche Filing-Pipeline** — das System liest den kompletten
      8-K-Strom des Universums jeden Tag. *DoD: 20 Handelstage in Folge
      lückenlos annotiert (Nachweis im Manifest), ohne manuellen Eingriff.*
- [ ] **V4.2 Vorwärts-Archiv mit Ausgangs-Labels** — jedes Event bekommt
      automatisch seinen späteren Kursausgang angeheftet. *DoD: Backfill-
      Job labelt Events nach 1/5/20/60 Tagen; Archiv wächst ≥ 3 Monate.*
- [ ] **V4.3 Katalysator-Kante im Paper-Handel bestätigt** — *DoD: n ≥ 100
      katalysator-getriebene Paper-Trades mit positiver Netto-Kante
      (nach Kosten + Slippage), CI-Untergrenze > 0.*
- [ ] **V4.4 Meta-Labeling veredelt die Kante** — *DoD: Meta-Modell
      verbessert die Netto-Kante der Katalysator-Trades walk-forward
      nachweislich; sonst hier als "geprüft, verworfen" vermerkt.*
- [ ] **V4.5 Tür-Entscheidung Signal-Produkt** (rein optional, User) —
      bleibt es Eigenkapital, oder wird daraus je ein Produkt für Dritte?
      *DoD: bewusste, dokumentierte Entscheidung — inkl. regulatorischer
      Prüfung (Anlageberatungs-Territorium), falls "ja".*

## Endbild (woran der Nordstern insgesamt gemessen wird)

Alle drei sind Dinge, die man nicht kaufen kann, sondern nur über Zeit
aufbauen:

- [ ] **E1 Eigenes Ereignis-Archiv** ≥ 2 Jahre lückenlos annotierte,
      ausgangs-gelabelte Katalysator-Historie (entfällt, wenn H1 tot).
- [ ] **E2 Kalibrierte Erfolgsbilanz** ≥ 2 Jahre: vorhergesagte
      Wahrscheinlichkeiten ≈ reale Frequenzen, monatlich dokumentiert.
- [ ] **E3 Selbst-ehrliche Pipeline**: jede jemals getestete These hat ein
      archiviertes Verdikt (bewiesen/verworfen), nichts läuft live ohne
      bestandene Promotion — auditierbar über die Verdikt-Dokumente.

## Bewusst NICHT Teil der Vision

HFT/Intraday, Deep Learning direkt auf Kursen, "Renaissance schlagen",
Bezahl-News-Archive institutioneller Preisklasse, Verkauf von Traumrenditen.
Realistische Obergrenze: marktnahe Rendite mit deutlich besserem Risiko plus
eventuell wenige Punkte Nischen-Alpha. Der Burggraben ist Ehrlichkeit als
Systemeigenschaft.
