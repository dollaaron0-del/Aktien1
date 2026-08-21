#!/usr/bin/env python3
"""
Entscheidungs-Replay-CLI — Roadmap 4.5.

Spielt archivierte KI-Antworten (analyzers/prompt_archive.py, Roadmap 1.4d)
durch die AKTUELLE Parsing-/Schwellen-Logik und vergleicht das Ergebnis mit
der damals tatsächlich geloggten Empfehlung. Kein API-Call, keine Kosten,
deterministisch bei gleichem Code-Stand — beantwortet "warum hat er das
gekauft?" (Einzel-Replay) bzw. "hätte der heutige Code irgendwo anders
entschieden?" (Batch-Drift-Report nach Code-/Schwellen-Änderungen).

Usage:
  python -m scripts.decision_replay --analysis-id 123
  python -m scripts.decision_replay --recent 500
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402

from analyzers.decision_replay import replay_analysis, replay_recent  # noqa: E402

console = Console()


def _print_single(r) -> None:
    if r is None:
        console.print(
            "[yellow]Kein archivierter Prompt für diese analysis_id[/yellow] "
            "(Ollama-/Frugal-Route archiviert nicht, oder unbekannte ID)."
        )
        return
    tag = "[red]ABWEICHUNG[/red]" if r["changed"] else "[green]identisch[/green]"
    console.print(
        f"#{r['analysis_id']} [bold]{r['ticker']}[/bold] ({r['kind']}, "
        f"archiviert {r['archived_at']}, Modell {r['raw_model']}) — {tag}"
    )
    console.print(f"  damals: {r['original']}")
    replayed_shown = {k: v for k, v in r["replayed"].items() if k != "kind"}
    console.print(f"  heute:  {replayed_shown}")
    if r["changed"]:
        console.print(f"  [dim]abweichende Felder: {', '.join(r['changed_fields'])}[/dim]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Entscheidungs-Replay (Roadmap 4.5)")
    ap.add_argument("--analysis-id", type=int, metavar="ID",
                     help="Einzelne analysis_id replayen")
    ap.add_argument("--recent", type=int, metavar="N", default=None,
                     help="Die letzten N archivierten Prompts replayen (Drift-Report)")
    args = ap.parse_args()

    if args.analysis_id is not None:
        _print_single(replay_analysis(args.analysis_id))
        return

    limit = args.recent or 200
    results = replay_recent(limit=limit)
    changed = [r for r in results if r["changed"]]
    console.print(
        f"{len(results)} archivierte Entscheidungen replayed, "
        f"[bold]{len(changed)}[/bold] weichen vom aktuellen Code ab."
    )
    for r in changed:
        _print_single(r)
    if not changed and results:
        console.print(
            "[green]Keine Abweichung — aktueller Code entscheidet bei allen "
            "archivierten Antworten identisch.[/green]"
        )
    if not results:
        console.print("[dim]Keine archivierten Prompts gefunden.[/dim]")


if __name__ == "__main__":
    main()
