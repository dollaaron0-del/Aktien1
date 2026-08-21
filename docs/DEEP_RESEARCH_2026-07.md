# Deep-Research: Weiterentwicklung Ruflo (Stand 12.7.2026)

> Auftrag des Users vom 12.7.2026: einmal gründlich durchdenken, wie das
> Programm weiterentwickelt werden kann und wie das umzusetzen ist.
> Dieses Dokument ist die Grundlage für künftige Arbeit — es fasst zusammen,
> was BEWIESEN ist, wo die Kante realistisch liegen kann, und in welcher
> Reihenfolge was gebaut wird. Es ergänzt docs/ROADMAP.md (die Checkliste)
> um den roten Faden (das Warum und die Reihenfolge).
>
> Es gelten unverändert: Bot pausiert (CLAUDE.md-Kopf) und die
> 🔒 Freigabe-Regel (ROADMAP Block 6): Hardware-Features nur vorbereiten,
> aktivieren erst auf ausdrückliches User-Signal "läuft auf neuem Server".

## A. Standortbestimmung — was die Befunde hart belegen

Ehrliche Bilanz aus ~6 Wochen Mess- und Lab-Arbeit (Details in ROADMAP-Einträgen):

| Befund | Beleg | Konsequenz |
|---|---|---|
| Mechanik allein hat KEINE Kante | Paper-Forward: Win 39 %, MaxDD −69,5 % vs. B&H +22 % im Bullenjahr; Exit-Variationen (2.1, 2.5) ändern nichts | Keine weitere Energie in Indikator-Tuning auf kleinen Daten |
| Diversifikation wirkt real | Portfolio-Backtest 2.2: MaxDD −14,5 % statt −21…−31 % | Portfolio-Ebene ist die richtige Simulationsebene |
| Gestuftes De-Risking > binär > aus | Stress-Test 2.3: beste MaxDD+Return in 2 von 3 Krisenfenstern | Risiko-Overlay ist ein echtes, schon halb bewiesenes Asset |
| Die KI-Scores DISKRIMINIEREN | Kalibrierung 1.2: AUC 0,61 — aber überkonfident (sagt 46 % Wins, real 34 %) | **Das einzige positive Alpha-Signal im ganzen System** |
| Kosten sind nicht das Problem | 1.3: 0,12 €/Trade API-Kosten; Kante −1,94 %/Trade ist das Problem | Optimierung auf Kosten ist gelöst (Frugal-Mode) |
| Daten sind das Nadelöhr | 10/42 Ticker, Survivorship, 78 Labels, keine News-Historie | Reihenfolge Daten → Protokoll → Compute (Block 6) |
| Anti-Overfit-Fundament steht | 6.3 Parallel-WF, 6.4 Šidák-Gate + Holdout (12.7.) | Große Suchräume sind jetzt diszipliniert fahrbar |

## B. Wo kann die Kante realistisch liegen? Drei Hypothesen

Ein Retail-Bot schlägt Institutionelle nicht bei Geschwindigkeit, Datenzugang
oder Mathematik. Realistische Nischen:

**H1 — Katalysator-Verarbeitung (die Kern-These, einziges positives Signal).**
Die KI-Analyse reagiert auf 8-K/FDA/Earnings-Kontext und erreicht AUC 0,61
auf kleiner Stichprobe. Plausibel, weil: Small-/Mid-Caps mit dünner
Analysten-Abdeckung, wo große Adressen wegen Positionsgrößen nicht spielen,
und wo Text-Verständnis (LLM) tatsächlich Information vor dem Markt-Konsens
extrahieren kann. **Status: ungetestet als Backtest — genau das macht der
EDGAR-Backfill + Event-Study erstmals möglich.**
→ Test: Event-Study (D, Phase 3). Falsifizierbar: wenn Katalysator-Events
mit LLM-Score KEINE Forward-Return-Differenz zeigen, ist H1 tot.

**H2 — Struktur-Prämien über großes Universum.** Momentum, PEAD, 52W-Hoch
sind akademisch dokumentierte Prämien; sie zeigen sich aber erst über
hunderte Titel und lange Historie, nicht über 10 Watchlist-Aktien.
→ Test: 6.2-Daten (PIT!) + Familien 5.1 durch Walk-Forward mit 6.4-Gates.
Ohne Delisting-Daten NICHT seriös testbar (gerade PEAD/Small-Cap-Prämien
sind survivorship-empfindlich).

**H3 — Kein Alpha, aber besseres Risiko (die ehrliche Rückfalllinie).**
Schon halb bewiesen (2.2 + 2.3): Diversifikation + gestuftes De-Risking
liefern B&H-nahe Rendite mit deutlich weniger Drawdown. Das ist auch OHNE
Kante ein legitimes Endprodukt: "B&H + Risiko-Overlay statt Stock-Picking".
→ Kein neuer Test nötig, nur längere OOS-Bestätigung. Wichtig als Anker
gegen Sunk-Cost: Wenn H1+H2 fallen, ist H3 das Produkt.

## C. Der rote Faden: Testbarkeit zuerst

Die wichtigste Einzel-Erkenntnis dieser Analyse: **Der Engpass ist nicht
Ideenmangel, sondern dass die Kern-These H1 heute nicht falsifizierbar ist.**
Alles Weitere (Meta-Labeling, Radar, Ensembles, mehr Familien) baut auf einer
These, die noch nie gegen Historie getestet wurde. Deshalb ist das eine
Experiment mit dem höchsten Hebel:

```
EDGAR-8-K-Archiv (frei, Jahrzehnte)
   → lokales LLM annotiert (nach bestandenem Qualitäts-Gate)
   → Katalysator-Zeitreihe je Ticker (Punkt-in-Zeit: Filing-Datum)
   → EVENT-STUDY: Forward-Returns nach Event-Typ × LLM-Score × Marktkapitalisierung
   → Verdikt über H1 (mit 6.4-Disziplin: Šidák, Holdout)
```

Erst wenn H1 dort Signal zeigt, lohnen Meta-Labeling und die tiefe
Live-Analyse (6.11b) — sonst veredeln sie Rauschen.

## D. Umsetzungsplan in Phasen

### Phase 0 — Risiko-Trio (SOFORT sinnvoll, unabhängig von allem; User-Entscheide)
1. **Push-Token (0.2)**: Code liegt >6 Wochen nur auf einer Platte.
2. **Backup-Timer enablen + BACKUP_REMOTE (0.1-Rest)**: Lern-DBs off-server.
3. **Demo-Daten-Rücktausch (0.3)**: divergiert seit 2.7., wird wöchentlich teurer.

### Phase 1 — alter Server, jetzt baubar (kein GPU nötig)
1. **EDGAR-Rohdaten-Download** (`scripts/edgar_download.py`, neu):
   NUR 8-K zuerst (ereignisnah, kompakt; 10-K später). Quellen: EDGAR
   Full-Text-Search-API (efts.sec.gov, paginierbar) + tägliche Index-Dateien.
   Grenzen: max. 10 req/s, User-Agent mit Kontakt-Mail (**braucht
   SEC_CONTACT_EMAIL in .env — offener User-Schritt**), IP-Block bei Verstoß.
   Ablage: `data/edgar/{cik}/{accession}.txt` + Manifest-Parquet
   (ticker, cik, form, filing_date, url). I/O-lastig → läuft nachts auf dem
   alten Server. Kein Bot-Wiring, reines Sammel-Skript.
2. **Historische S&P-500-Zusammensetzung** (6.2b, gratis): fertige Datensätze
   existieren (GitHub fja05680/sp500 bzw. hanshof/sp500_constituents,
   ab 1996, CSV je Datum). Naht: `strategy_lab/universe.py` bekommt
   `constituents_at(date)` + Walk-Forward-Option `--pit-universe`:
   jedes Train/Test-Fenster nutzt die DAMALIGE Mitgliederliste (Kurse
   delisteter Titel fehlen bei yfinance weiter → Teilfix, ehrlich labeln).
3. **CPCV** (6.4c-Rest): `strategy_lab/cpcv.py` — purged/embargoed
   Combinatorial CV als zweite Validierungs-Achse neben Walk-Forward;
   rechenintensiv, aber mit 6.3-Workern schon auf 6 Kernen fahrbar.
4. **4.1-Nachtlauf-Befund** einarbeiten (läuft beim User über Nacht).

### Phase 2 — Umzug (wenn Server da; 6.1-Checkliste)
- Härtung + Backup-Dimensionierung (6.1a/b), .env-Transfer, IB-Gateway neu.
- **Datenkauf-Entscheid** (User) mit recherchierten Fakten:
  - **EODHD All-World ~20 €/Monat: Delistings sind in JEDEM Paket enthalten**
    (11.000+ delistete US-Ticker ab ~2000) — pragmatischster Einstieg.
  - **Norgate Platinum ~630 $/Jahr**: sauberste Lösung (Delistings + fertige
    historische Index-Mitgliedschaft, Standard bei systematischen Tradern) —
    teurer als ursprünglich angenommen (~52 $/Monat, nicht 30–40 $).
  - Sharadar (Nasdaq Data Link): Preishistorie erst ab ~2014 → zu kurz.
  - Empfehlung: EODHD zum Start (Preis eines Streaming-Abos), Norgate nur
    falls H2 ernsthaft verfolgt wird.

### Phase 3 — GPU-Server (NACH User-Freigabe; alles vorher nur vorbereitet)
1. **Ollama-Modellwahl + Annotations-Gate** (6.8a-Gate): ~200 8-Ks doppelt
   labeln (lokal vs. Claude vs. echter Kursausgang) → Übereinstimmung messen.
2. **Massen-Backfill** der 8-K-Historie (nur wenn Gate bestanden).
3. **⭐ EVENT-STUDY** (`strategy_lab/event_study.py`, neu — wichtigster
   Baustein des ganzen Plans): Events × Forward-Returns (1/5/20/60 Tage),
   gruppiert nach Event-Typ, LLM-Score-Bucket, Market-Cap; Bootstrap-CIs,
   Šidák über die Zahl der getesteten Gruppen, Holdout-Jahre ausgespart.
   Loader-injizierbar, netzfreie Tests (Muster: test_anti_overfit.py).
   → liefert das H1-Verdikt.
4. **Radar** (6.11a): score-Zeitreihen-DB (`data/radar.db`), täglicher
   Lauf über großes Universum, Flag default AUS, kein Funnel-Eingriff.

### Phase 4 — nur wenn H1-Signal positiv
- **Meta-Labeling** (`learning/meta_label.py`): sklearn ist schon gepinnt
  (1.9.0) → HistGradientBoosting; Features Regime/Vola/Breadth/Event-Typ
  zum Signalzeitpunkt, Label = Ausgang; Training auf Backtest-Ausgängen
  (6.8c), echte Trades NUR Validierung; Auswertung walk-forward + 6.4.
  Naht in den Live-Pfad ausschließlich advisory (Muster live_bridge).
- **Analyse-Tiefe-A/B** (6.11b) + Ensembles (6.9e), Schiedsrichter ist der
  Kalibrierungs-Monitor (Brier/AUC), nicht der Eindruck.
- **Kalibrierungs-Deckel**: die bekannte Überkonfidenz (46 %→34 %) per
  Isotonic/Platt-Nachkalibrierung auf den Radar-Zeitreihen fixen.

### Phase 5 — Verdikt (6.10)
Erfolgs-/Abbruchkriterien je These VOR den Läufen festschreiben (User),
z. B.: "H1: Event-Study-Kante mit CI-Untergrenze > 0 nach Šidák; danach
n≥100 Paper-Trades mit positiver Netto-Kante — sonst H1 verwerfen, H3 als
Produkt festschreiben." Zeit-Budget dazu.

## E. Was bewusst NICHT gebaut wird (Anti-Empfehlungen)

- **RL/Deep Learning direkt auf Kursen** (6.5d): 78 Labels + Random-Walk.
- **Intraday/HFT-Ambitionen**: falscher Wettbewerb, falsche Infrastruktur.
- **TimesFM & Co. für Kursprognosen**: evaluiert, random-walk (nur evtl.
  Alt-Data-Reihen).
- **Mehr Indikator-Familien VOR dem Daten-Fix**: vergrößert nur den
  Suchraum für Scheinkanten (jetzt zwar Šidák-gebremst, aber sinnlos).
- **Bezahl-News-Archive** (RavenPack etc.): institutionelle Preisklasse;
  stattdessen EDGAR-Backfill + eigenes Vorwärts-Archiv.
- **Schwellen senken, weil Analyse billig wird** (6.11): Multiple-Testing
  im Live-Funnel — Analyse breit, Funnel streng.

## F. Prioritäten-Kurzliste

1. (User, 30 Min) Risiko-Trio: Push-Token, Backup-Timer, Demo-Rücktausch-Termin.
2. (User, 2 Min) SEC_CONTACT_EMAIL in .env → schaltet EDGAR-Download frei.
3. (Bau, alter Server) `scripts/edgar_download.py` — 8-K-Archiv aufbauen.
4. (Bau, alter Server) PIT-Universum-Naht + historische S&P-Listen.
5. (Bau, alter Server) CPCV als 6.4-Abschluss.
6. (Entscheid beim Umzug) EODHD ~20 €/Mon. als Survivorship-Einstieg.
7. (GPU, nach Freigabe) Annotations-Gate → Backfill → **Event-Study = H1-Verdikt**.

Quellen der Recherche (12.7.2026): EDGAR-Zugriffsregeln sec.gov ("Accessing
EDGAR Data", 10 req/s, User-Agent-Pflicht); freie S&P-Konstituenten:
github.com/fja05680/sp500, github.com/hanshof/sp500_constituents;
Norgate-Preise norgatedata.com/prices.php (Platinum inkl. Delisted);
EODHD-Preise/Delisted eodhd.com/pricing + financial-apis/delisted-stock-companies-data.
