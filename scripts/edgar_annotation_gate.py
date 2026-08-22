#!/usr/bin/env python3
"""
EDGAR-Annotations-Gate — Roadmap 6.8a.

Vor dem Massen-Backfill (lokales LLM annotiert alle 1600+ 8-K-Filings aus
data/edgar/) muss die Annotationsqualität erst bewiesen werden: eine
Zufalls-Stichprobe von Filings wird DOPPELT gelabelt (lokales Ollama-Modell
UND Claude), beide Label werden a) untereinander (Übereinstimmung) und
b) gegen den echten Kursausgang (Forward-Return N Handelstage nach
filing_date) verglichen. Massen-Annotation nur, wenn das LOKALE Modell
(der eigentliche Massen-Annotator) eine Trennung liefert, die statistisch
von 0 verschieden ist — sonst würde der Massen-Backfill nur Rauschen
lernen (Roadmap-Leitplanke, exakt wie beim Anti-Overfit-Protokoll 6.4).

Bewusst KEIN Live-Wiring: reines Offline-Validierungs-Werkzeug, betrifft
weder Bot- noch Backtest-Pfad.

Kosten: Claude-Aufrufe respektieren das bestehende Tagesbudget
(APICostTracker.check_daily_limit(), Roadmap 1.10) — bei Erreichen bricht
der Lauf ehrlich ab statt das Limit zu überziehen, Zwischenstand bleibt
in --state erhalten (--resume setzt fort, ohne bereits gelabelte Filings
erneut zu bezahlen).

Usage:
  python -m scripts.edgar_annotation_gate --n 200
  python -m scripts.edgar_annotation_gate --n 20 --skip-claude   # kostenlos, nur lokal
  python -m scripts.edgar_annotation_gate --resume                # Zwischenstand fortsetzen
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich import box  # noqa: E402

from scripts.source_ablation import _bootstrap_diff_ci  # noqa: E402
from scripts.backfill_outcomes import _load_bars  # noqa: E402

console = Console()

REPO_ROOT = Path(__file__).parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "data" / "edgar" / "manifest.parquet"
DEFAULT_STATE = REPO_ROOT / "data" / "edgar_annotation_gate.json"

N_MIN = 10               # Mindest-n je Richtungs-Gruppe, sonst "unzureichend" (wie 2.4/3.2)
CI_LEVEL = 0.95
BOOTSTRAP_ITERS = 20000
SEED = 20260811
MAX_FILING_CHARS = 6000  # Prompt-/Kosten-Deckel je Filing (Roh-8-K sind oft viele KB)
DIRECTIONS = ("BULLISH", "BEARISH", "NEUTRAL")

# Backoff bei Ollama-Ausfall MITTEN im Lauf (Absturz/Neustart des Dienstes,
# nicht nur Timeout — dafür sorgt bereits der prescreener-eigene Circuit-
# Breaker). Ohne das würde der Loop Dutzende Zeilen pro Sekunde gegen eine
# tote Verbindung rennen, wie im ersten echten Lauf 11.8.2026 beobachtet.
OLLAMA_FAIL_BACKOFF_S = 10
OLLAMA_MAX_BACKOFF_ROUNDS = 6  # ~ (10+20+…+60)s ≈ 3.5 Min, danach aufgeben

_PROMPT_TEMPLATE = """Du bewertest eine SEC-8-K-Meldung von {ticker} \
(eingereicht am {filing_date}). Antworte NUR mit einem JSON-Objekt, keine \
weitere Erklärung:
{{"direction": "BULLISH"|"BEARISH"|"NEUTRAL", "confidence": "HIGH"|"MEDIUM"|"LOW", \
"reason": "<max. 20 Worte>"}}

Meldungstext (ggf. gekürzt):
{text}
"""


# ── Reine Logik (netzfrei, testbar) ────────────────────────────────────────

def _parse_json_label(raw: Optional[str]) -> Optional[Dict]:
    """JSON-Label aus einer LLM-Antwort ziehen — robust gegen Text-Präfix/
    -Suffix ums eigentliche Objekt (Modelle antworten selten strikt roh-JSON)."""
    if not raw:
        return None
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end])
    except json.JSONDecodeError:
        return None
    direction = str(data.get("direction", "")).strip().upper()
    if direction not in DIRECTIONS:
        return None
    confidence = str(data.get("confidence", "MEDIUM")).strip().upper()
    if confidence not in ("HIGH", "MEDIUM", "LOW"):
        confidence = "MEDIUM"
    return {
        "direction": direction,
        "confidence": confidence,
        "reason": str(data.get("reason", ""))[:200],
    }


def _sample_filings(manifest: pd.DataFrame, n: int, seed: int,
                    repo_root: Path = REPO_ROOT) -> pd.DataFrame:
    """Zufalls-Stichprobe über Filings mit tatsächlich vorhandener Textdatei.
    Deterministisch (fester seed) und über Ticker gestreut (kein Ticker darf
    die Stichprobe dominieren, sonst testet das Gate faktisch nur AAPL)."""
    has_file = manifest["path"].apply(
        lambda p: bool(p) and (repo_root / p).exists()
    )
    pool = manifest[has_file]
    if pool.empty or n <= 0:
        return pool.iloc[0:0]
    per_ticker = max(1, n // max(1, pool["ticker"].nunique()))
    parts = [
        grp.sample(n=min(per_ticker, len(grp)), random_state=seed)
        for _, grp in pool.groupby("ticker")
    ]
    sample = pd.concat(parts) if parts else pool.iloc[0:0]
    if len(sample) > n:
        sample = sample.sample(n=n, random_state=seed)
    return sample.reset_index(drop=True)


def _filing_text_excerpt(path: Path, max_chars: int = MAX_FILING_CHARS) -> str:
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return ""
    return text[:max_chars]


def _forward_return(bars: List[Dict], filing_date: str, hold_days: int) -> Optional[float]:
    """Forward-Return in % über hold_days Handelstage ab der ersten Bar
    AM/NACH filing_date (Filing kann nachbörslich sein — der Markt reagiert
    frühestens am nächsten Handelstag). None bei zu wenig Zukunftsdaten."""
    entry_idx = next((i for i, b in enumerate(bars) if b.get("date", "") >= filing_date), None)
    if entry_idx is None:
        return None
    exit_idx = entry_idx + hold_days
    if exit_idx >= len(bars):
        return None
    entry, exit_ = bars[entry_idx].get("close"), bars[exit_idx].get("close")
    if not entry or not exit_:
        return None
    return (exit_ - entry) / entry * 100.0


def _group_by_direction(rows: List[Dict], label_key: str) -> Tuple[List[float], List[float]]:
    """Liefert (bullish_returns, bearish_returns) für Filings mit gültigem
    Label UND gültigem Forward-Return unter label_key ('ollama'/'claude')."""
    bull, bear = [], []
    for r in rows:
        label = r.get(label_key)
        ret = r.get("forward_return_pct")
        if label is None or ret is None:
            continue
        if label["direction"] == "BULLISH":
            bull.append(ret)
        elif label["direction"] == "BEARISH":
            bear.append(ret)
    return bull, bear


def _evaluate_gate(bull: List[float], bear: List[float], rng: np.random.Generator,
                   min_n: int = N_MIN) -> Dict:
    """Bootstrap-Diff (BULLISH-Ø − BEARISH-Ø Forward-Return) + Mindest-n-Gate.
    status: 'unzureichend' (n zu klein) | 'signal' (CI-Untergrenze > 0, Label
    trennt echte Kursausgänge) | 'kein_signal' (CI spannt die Null)."""
    if len(bull) < min_n or len(bear) < min_n:
        return {"status": "unzureichend", "n_bullish": len(bull), "n_bearish": len(bear)}
    ci = _bootstrap_diff_ci(bull, bear, rng, iters=BOOTSTRAP_ITERS, ci=CI_LEVEL)
    ci["status"] = "signal" if ci["lo"] > 0 else "kein_signal"
    return ci


def _agreement_rate(rows: List[Dict]) -> Dict:
    """Anteil exakter direction-Übereinstimmung zwischen Ollama- und
    Claude-Label, nur über Filings mit BEIDEN Labels."""
    both = [r for r in rows if r.get("ollama") and r.get("claude")]
    if not both:
        return {"n": 0, "rate": None}
    matches = sum(1 for r in both if r["ollama"]["direction"] == r["claude"]["direction"])
    return {"n": len(both), "rate": matches / len(both)}


# ── I/O-Schicht (Netzwerk/Dateien — nicht netzfrei, injizierbar für Tests) ──

def label_with_ollama(prescreener, prompt: str) -> Optional[Dict]:
    raw = prescreener._call_ollama(prompt, max_tokens=150)
    if raw is None:
        # Gleiches Muster wie prescreen(): ein fehlgeschlagener Aufruf (Timeout,
        # Connection-Refused nach Absturz/Neustart des Ollama-Dienstes, …) darf
        # den gecachten is_available()=True-Zustand nicht überleben, sonst rennt
        # der Aufrufer stumm gegen eine tote Verbindung weiter.
        prescreener.reset_availability_cache()
    return _parse_json_label(raw)


def label_with_claude(call_fn: Callable[[str], Optional[str]], prompt: str) -> Optional[Dict]:
    raw = call_fn(prompt)
    return _parse_json_label(raw)


def make_claude_caller(model: str, cost_tracker) -> Callable[[str], Optional[str]]:
    """Baut den echten Claude-Aufrufer (anthropic SDK) inkl. Kosten-Buchung.
    Ruft NICHT selbst check_daily_limit() — das entscheidet main() vor jedem
    Aufruf, damit ein erschöpftes Budget den Lauf sauber abbricht statt
    stumm weiterzuzählen."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

    def _call(prompt: str) -> Optional[str]:
        try:
            resp = client.messages.create(
                model=model, max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # pragma: no cover - Netzwerk/API-Fehler
            console.print(f"  [dim]Claude-Fehler: {exc}[/dim]")
            return None
        if cost_tracker is not None:
            in_tok, out_tok, cache_tok = cost_tracker.usage_from_response(resp)
            cost_tracker.record_claude_usage(model, in_tok, out_tok, cache_tok)
        return resp.content[0].text

    return _call


# ── State (Resumable) ───────────────────────────────────────────────────────

def load_state(path: Path) -> List[Dict]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return []
    return []


def save_state(rows: List[Dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2))
    tmp.replace(path)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="EDGAR-Annotations-Gate (Roadmap 6.8a)")
    ap.add_argument("--n", type=int, default=200, help="Stichprobengröße")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--hold-days", type=int, default=5, help="Forward-Return-Fenster (Handelstage)")
    ap.add_argument("--min-n", type=int, default=N_MIN)
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    ap.add_argument("--skip-claude", action="store_true", help="nur lokales Modell, kostenlos")
    ap.add_argument("--skip-ollama", action="store_true")
    ap.add_argument("--resume", action="store_true", help="Zwischenstand aus --state fortsetzen")
    ap.add_argument("--ollama-model", default=None,
                    help="Default: BALANCED-Tier (schneller/kleiner als PERFORMANCE — "
                         "Massen-Lauf über lange Filing-Texte braucht Durchsatz, nicht "
                         "Spitzenqualität; PERFORMANCE=qwen2.5:14b lief auf der 8GB-Karte "
                         "bei 6000-Zeichen-Prompts wiederholt in 30s-Timeouts)")
    ap.add_argument("--ollama-timeout", type=int, default=45)
    ap.add_argument("--max-chars", type=int, default=MAX_FILING_CHARS,
                    help="Prompt-Deckel je Filing-Text (Latenz-/Kosten-Bremse)")
    args = ap.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        console.print(f"[red]Kein Manifest unter {manifest_path} — erst scripts/edgar_download.py laufen lassen.[/red]")
        sys.exit(1)
    manifest = pd.read_parquet(manifest_path)
    sample = _sample_filings(manifest, args.n, args.seed)
    console.print(f"Stichprobe: {len(sample)} Filings über {sample['ticker'].nunique()} Ticker "
                  f"(seed={args.seed})")

    state_path = Path(args.state)
    rows: Dict[str, Dict] = {r["accession"]: r for r in (load_state(state_path) if args.resume else [])}

    prescreener = None
    if not args.skip_ollama:
        from analyzers.ollama_prescreener import OllamaPrescreener
        from system.resource_manager import TIER_MODELS, ResourceTier
        model = args.ollama_model or TIER_MODELS[ResourceTier.BALANCED]
        prescreener = OllamaPrescreener(model=model, timeout=args.ollama_timeout)
        if not prescreener.is_available():
            console.print("[yellow]Ollama nicht erreichbar — lokale Labels werden übersprungen.[/yellow]")
            prescreener = None
        else:
            console.print(f"Lokales Modell: {model} (Timeout {args.ollama_timeout}s)")

    claude_call, cost_tracker = None, None
    if not args.skip_claude:
        if not os.getenv("ANTHROPIC_API_KEY", ""):
            console.print("[yellow]Kein ANTHROPIC_API_KEY — Claude-Labels werden übersprungen.[/yellow]")
        else:
            from analyzers.api_cost_tracker import APICostTracker
            from config import config as _cfg
            cost_tracker = APICostTracker()
            claude_call = make_claude_caller(_cfg.claude_model_light, cost_tracker)

    budget_exhausted = False
    ollama_fail_streak = 0
    for _, row in sample.iterrows():
        acc = row["accession"]
        entry = rows.setdefault(acc, {
            "accession": acc, "ticker": row["ticker"], "form": row["form"],
            "filing_date": row["filing_date"], "ollama": None, "claude": None,
            "forward_return_pct": None,
        })

        if prescreener is not None and getattr(prescreener, "_circuit_open", False):
            console.print("[yellow]Ollama-Circuit-Breaker ausgelöst — lokale Labels werden "
                          "für den Rest dieses Laufs übersprungen (--resume später erneut "
                          "versuchen, wenn die Engine wieder schnell antwortet).[/yellow]")
            prescreener = None

        text = None
        if (prescreener is not None and entry["ollama"] is None) or \
           (claude_call is not None and entry["claude"] is None and not budget_exhausted):
            text = _filing_text_excerpt(REPO_ROOT / row["path"], max_chars=args.max_chars)
        prompt = _PROMPT_TEMPLATE.format(ticker=row["ticker"], filing_date=row["filing_date"],
                                         text=text or "") if text else None

        if prescreener is not None and entry["ollama"] is None and prompt:
            entry["ollama"] = label_with_ollama(prescreener, prompt)
            if entry["ollama"] is None:
                # Kann Timeout, Parse-Fehler ODER ein mitten im Lauf gecrashter/
                # neugestarteter Ollama-Dienst sein (Connection-Refused fällt
                # NICHT unter den prescreener-eigenen Timeout-Circuit-Breaker).
                # Statt weiter im Sekundentakt gegen eine tote Verbindung zu
                # rennen: pausieren, Verfügbarkeit neu prüfen, mit Backoff
                # wiederholen — inkl. erneutem Versuch DIESER Zeile nach
                # Wiederherstellung, sonst zählt eine reine Verbindungslücke
                # fälschlich als inhaltlicher Fehlschlag. Nach
                # OLLAMA_MAX_BACKOFF_ROUNDS aufgeben.
                ollama_fail_streak += 1
                for round_i in range(1, OLLAMA_MAX_BACKOFF_ROUNDS + 1):
                    wait_s = OLLAMA_FAIL_BACKOFF_S * round_i
                    console.print(f"[yellow]Ollama-Aufruf fehlgeschlagen (Streak "
                                  f"{ollama_fail_streak}) — warte {wait_s}s und prüfe "
                                  f"erneut ({round_i}/{OLLAMA_MAX_BACKOFF_ROUNDS}).[/yellow]")
                    time.sleep(wait_s)
                    prescreener.reset_availability_cache()
                    if prescreener.is_available():
                        entry["ollama"] = label_with_ollama(prescreener, prompt)
                        if entry["ollama"] is not None:
                            break
                else:
                    console.print("[red]Ollama bleibt nicht erreichbar — lokale Labels werden "
                                  "für den Rest dieses Laufs übersprungen (--resume später "
                                  "erneut versuchen).[/red]")
                    prescreener = None
            if entry["ollama"] is not None:
                ollama_fail_streak = 0

        if claude_call is not None and entry["claude"] is None and prompt and not budget_exhausted:
            allowed, reason = cost_tracker.check_daily_limit()
            if not allowed:
                console.print(f"[yellow]{reason}[/yellow]")
                budget_exhausted = True
            else:
                entry["claude"] = label_with_claude(claude_call, prompt)

        if entry["forward_return_pct"] is None:
            bars = _load_bars(row["ticker"], row["filing_date"])
            entry["forward_return_pct"] = _forward_return(bars, row["filing_date"], args.hold_days)

        if len(rows) % 10 == 0:
            save_state(list(rows.values()), state_path)  # Zwischenstand — Abbruch kostet max. 10

    ordered = list(rows.values())
    save_state(ordered, state_path)

    rng = np.random.default_rng(args.seed)
    for labeler in ("ollama", "claude"):
        bull, bear = _group_by_direction(ordered, labeler)
        verdict = _evaluate_gate(bull, bear, rng, args.min_n)
        table = Table(title=f"Annotations-Gate — {labeler}", box=box.SIMPLE)
        for col in verdict:
            table.add_column(col)
        table.add_row(*(f"{v:.3f}" if isinstance(v, float) else str(v) for v in verdict.values()))
        console.print(table)
        if verdict["status"] == "unzureichend":
            console.print(f"  [dim]Unzureichend Daten (< {args.min_n} je Richtungs-Gruppe) — "
                          f"kein Verdikt möglich.[/dim]")
        elif verdict["status"] == "signal":
            console.print(f"  [green]SIGNAL — {labeler}-Label trennt echte Kursausgänge "
                          f"(CI-Untergrenze {verdict['lo']:.2f}% > 0).[/green]")
        else:
            console.print(f"  [red]KEIN SIGNAL — {labeler}-Label trennt Kursausgänge nicht "
                          f"messbar von Zufall.[/red]")

    agree = _agreement_rate(ordered)
    if agree["n"] > 0:
        console.print(f"\nÜbereinstimmung Ollama↔Claude: {agree['rate']*100:.1f}% (n={agree['n']})")
    else:
        console.print("\nÜbereinstimmung Ollama↔Claude: keine Filings mit beiden Labels.")

    ollama_status = _evaluate_gate(*_group_by_direction(ordered, "ollama"), rng, args.min_n)["status"]
    if ollama_status == "signal":
        console.print("\n[bold green]GATE BESTANDEN — lokales Modell trägt, Massen-Backfill "
                      "kann folgen.[/bold green]")
    elif ollama_status == "unzureichend":
        console.print("\n[bold yellow]GATE OFFEN — noch zu wenig gelabelte Filings für ein "
                      "Verdikt (--resume mit größerem --n erneut laufen lassen).[/bold yellow]")
    else:
        console.print("\n[bold red]GATE NICHT BESTANDEN — Massen-Backfill mit dem lokalen "
                      "Modell wird NICHT empfohlen (würde Rauschen lernen).[/bold red]")


if __name__ == "__main__":
    main()
