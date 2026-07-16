# Neustart-Übersicht — Vor / Nach / Server-Update

Stand: 16.7.2026. Konsolidiert offene Punkte aus **allen** Roadmap-Dokumenten
(`ROADMAP.md`, `VISION.md`, `DEEP_RESEARCH_2026-07.md`, `DESIGN_ROADMAP.md`,
`DESIGN_FABRIK.md`, `DASHBOARD_HORIZONT.md`, `REAKTIVIERUNG.md`,
`SERVER_RUNBOOK.md`) unter der Frage: **was ist noch zu tun, und wann?**
`DASHBOARD_HORIZONT.md` hat aktuell 0 offene Punkte (alle 25 erledigt/bewusst
verworfen) — taucht deshalb unten nicht mehr auf.

> Hinweis zur Aktualität: `ROADMAP.md`/`VISION.md` sind vom 11./12.7. und an
> einzelnen Stellen inzwischen **stale** — `REAKTIVIERUNG.md` (14.–16.7.)
> dokumentiert den echteren Ist-Stand. Konkret: ROADMAP 0.3 (Demo-Daten-
> Rücktausch) und VISION V0.1 (Demo-Daten-Teil) zeigen `[ ]`, sind aber laut
> REAKTIVIERUNG-Schritt 1 seit 14.7. erledigt. Unten ist der reale Stand
> maßgeblich, nicht das Kästchen im Ursprungsdokument.

---

## 🟢 Vor Neustart (bis morgen)

Nur zwei echte Punkte übrig — der komplette Rest der Reaktivierungs-
Checkliste (Demo-Daten-Rücktausch, Versions-Stempel, Registry-Neugenerierung,
Backup-Timer) ist laut `REAKTIVIERUNG.md` bereits am 14.7. erledigt.

- **`.env` vervollständigen** (Reaktivierung Schritt 2) — reiner User-Task,
  Datei-Zugriff ist für diese Sitzung gesperrt:
  ```
  SEC_CONTACT_EMAIL=<echte Kontakt-Mail>   # sonst drosselt/blockt EDGAR
  BACKUP_REMOTE=<optional: rsync-Ziel>     # Backup liegt sonst nur lokal
  ```
- **Der Start-Befehl selbst** (Reaktivierung Schritt 6) — bewusst dein
  Vorbehalt, wird nicht eigenmächtig ausgeführt:
  ```bash
  sudo systemctl enable --now aktien_bot.service aktien_dashboard.service \
    aktien_premarket_ibkr.timer aktien_source_health.timer
  # crontab -e: 06:00-Zeile wieder einkommentieren
  ```

---

## 🟡 Nach Neustart (sobald der Bot wieder läuft)

- **Ersten Zyklus beaufsichtigen** (Reaktivierung Schritt 7) — Collectors,
  Telegram, Dashboard-Status-Banner live mitverfolgen.
- **GTC-Schutz-Stops E2E verifizieren** (Reaktivierung Schritt 8) — erst nach
  dem ersten echten Kauf mit Fill möglich; bisher nur gegen Paper-Gateway
  ohne Fill getestet, weil die Börse zu war. Dabei Tagesverlust-Circuit-
  Breaker beobachten (Falschmeldungen bei Partial-TP waren am 11.7. gefixt).
- **Montagslauf-Gegencheck** nach dem ersten Handelstag wiederholen
  (`scripts/monday_cycle_check.py`).
- **ROADMAP 3.2 Skip-Kontrafaktik** — decision_log-SKIPs mit
  `simulate_outcome` nachrechnen, EntryFilter-Schwellen mit Gegenproben
  validieren. Kein Live-Betrieb nötig, aber sinnvoll erst wenn wieder
  Entscheidungen anfallen.
- **ROADMAP 5.3 Slippage-Kalibrierung** — braucht echte IBKR-Paper-Fills,
  die erst mit laufendem Bot entstehen.
- **ROADMAP 6.6 Lern-Loop-Realität** — echtes Weiterlernen (Kalibrierung,
  Meta-Labeling auf LIVE-Ausgängen) braucht laufenden Bot + Zeit, kein
  Hardware-Ersatz möglich.
- **ROADMAP 3.1 Nachbewertung** — Sentiment-Vorwärtsstudie war beim Erstlauf
  ohne Evidenz (IC −0,018, spannt die Null); bleibt `[~]` offen bis genug
  echte Live-Trades (`label_source='live'`) eine belastbare Neubewertung
  erlauben.
- **VISION V1.x „Forschungsfabrik läuft ohne dich"** — Nacht-Experiment-
  Runner, Morgen-Brief, autonomer Forschungszyklus. Unabhängig vom Bot-
  Neustart parallel angehbar, aber V1.1 (H1-Verdikt) braucht zusätzlich das
  EDGAR-8-K-Archiv (V0.2) — noch nicht begonnen, kein Hardware-Bezug.

---

## 🔴 Nach Server-Update (GPU-Server-Umzug, User-Plan ~Ende Juli/Aug.)

**Freigabe-Regel gilt unverändert (User-Anweisung 12.7.):** alles hier wird
nur vorbereitet, nichts eigenmächtig scharfgeschaltet — erst auf dein
ausdrückliches Signal „läuft auf dem neuen Server".

- **ROADMAP 6.1 Umzugs-Fundament** — Code muss versioniert rüber (macht
  0.2/Push-Token PFLICHT statt optional), Sicherheits-Checkliste (ufw
  default-deny, Dashboard nur 127.0.0.1+Tunnel), Speicher-/Backup-
  Dimensionierung für wachsende PIT-/EDGAR-Archive, IB-Gateway neu aufsetzen.
- **ROADMAP 6.2 Daten-Ausbau** — Universum auf mehrere hundert Ticker,
  Parquet-Cache vorab befüllen. Der bezahlte Survivorship-Fix (Norgate/
  EODHD, ~20 €/Mon. bzw. ~630 $/Jahr) bleibt **User-Geldentscheidung**,
  wird mit mehr Compute aber dringlicher, nicht optionaler.
- **ROADMAP 6.5(c) TimesFM-Experiment** — braucht die GPU-Hardware selbst
  (zero-shot vs. naive Baseline auf Alt-Data-Reihen, nicht auf Kursen).
- **ROADMAP 6.7 Intensiv-Fahrplan** — Lab von Hand-Anstoß in feste
  systemd-Timer-Routine überführen (nächtlich Walk-Forward, wöchentlich
  Meta-Backtest, monatlich Ablation/Stress-Test).
- **ROADMAP 6.9/6.11** — weitere Compute-Hebel (wählst du beim Umzug aus)
  und Breite-Analyse-als-A/B (mehr Aktien beobachten, Funnel bleibt streng).
- **VISION V0.4/V0.5** — survivorship-freie Daten angebunden, Umzug
  abgeschlossen + GPU freigegeben (DoD: User-Signal + Bot/Lab laufen dort).

**Offene Geldentscheidungen, die diesen Block blockieren** (aus ROADMAPs
eigener „User-Entscheidungen"-Sektion, unverändert seit 12.7.):
- Point-in-Time-Daten kaufen? (Norgate/Sharadar/EODHD)
- Push-Token mit `Contents:write`? (wird mit dem Umzug zur Pflicht)
- GPU-Server-Sizing vor dem Kauf gemeinsam durchgehen (VRAM entscheidet,
  nicht CPU/RAM — 8B≈8–12GB, 32B≈~24GB, 70B≈48GB+ quantisiert)

---

## ✅ Bereits vorbereitet — aktiviert sich selbst, kein Schalter nötig

Diese Punkte sind fertig gebaut und getestet, brauchen aber **keine manuelle
Freigabe** — sie greifen erst, wenn die Hardware-Bedingung eintritt (Flags
Default aus, exakt altes Verhalten bis dahin):

- **ROADMAP 6.5(a) Ollama-GPU-Autoerkennung** — `resource_manager.py::
  _has_inference_gpu()` erkennt Apple Silicon / nvidia-smi / `OLLAMA_FORCE_GPU`
  automatisch und schaltet die TIER_MODELS selbständig von CPU-Defaults
  (llama3.2:3b) auf GPU-Defaults (qwen2.5:32b/14b) um. Direkt messbarer
  Euro-Effekt sobald GPU da ist, kein Overfit-Risiko (reine Infrastruktur).
- **ROADMAP 6.3 Parallel-Walk-Forward** — `--workers 0` nutzt schon jetzt
  alle Kerne minus 1; skaliert auf dem neuen Server automatisch mit, ohne
  Codeänderung.
- **ROADMAP 6.4 Anti-Overfit-Protokoll** (Šidák-Korrektur, Holdout, CPCV) —
  bereits aktiv, wird mit größerem Suchraum nach dem Umzug wichtiger, aber
  es gibt nichts zusätzlich einzuschalten.
- **H1.1/H1.3 Dashboard-Steuerpult** (Pause-Hebel, Not-Aus-Reset) — bereits
  live nutzbar sobald der Bot wieder läuft, unabhängig vom Server-Umzug.

---

## Unabhängige Nebenstränge (nicht durch Neustart/Umzug blockiert)

Reine Präsentations-Vorbereitung, kann parallel laufen, sobald du Zeit hast:

- **DESIGN_ROADMAP D6.3** Screenshot-Foliensatz, **D6.4** Generalprobe —
  beide brauchen echten Browser-Zugriff (nicht headless machbar).
- **DESIGN_ROADMAP D5.2/D5.4** — Bilder generieren/auswählen (User), Icons
  nur falls D5.2 welche liefert.
- **DESIGN_FABRIK W5.2** — Maschinen-Art-Assets liefern (User, Stil-Prompt
  vom 13.7. liegt bereit), **W5.3** — Einbau je geliefertem Asset.

## Kleiner Fund am Rande (kein Roadmap-Punkt, niedrige Priorität)

`analyzers/sl_cooldown.py` — Selbstbereinigung vergleicht
`len(active)<len(data)` NACHDEM abgelaufene Einträge schon aus `data`
gepoppt wurden, ist daher immer `False` — die Cooldown-Datei wird nie
tatsächlich bereinigt (nur wächst langsam; `all_blocked()`s Rückgabewert
selbst bleibt jederzeit korrekt, kein Funktionsfehler). Gefunden am 13.7.
beim Fabrik-Check, bewusst nicht gefixt (außerhalb des damaligen
Dashboard-Scopes). Kann jederzeit unabhängig vom Neustart behoben werden.
