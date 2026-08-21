#!/usr/bin/env python3
"""
Embedding-Index-CLI — Roadmap 6.9b (Präzedenzfall-Abruf).

Baut einen Embedding-Index über data/analysis_log.db (entry_rationale +
bull_case/bear_case + key_catalysts/risk_factors, kombiniert zu einem
Text je Analyse) und sucht darin per Kosinus-Ähnlichkeit nach "ähnlichen
historischen Situationen". Siehe analyzers/embedding_index.py-Docstring
für die Modell-/Architektur-Entscheidung (transformers statt Ollama-
Embeddings, kein Vektor-DB-Unterbau bei dieser Datengröße).

Persistiert den Index nach data/embedding_index.* (--save, Default an) —
ein voller Rebuild embeddet jede Analyse neu (Sekunden bis wenige Minuten
bei ~1600 Einträgen auf CPU), --load spart das für reine Suchläufe.

Usage:
  python -m scripts.build_embedding_index --limit 500
  python -m scripts.build_embedding_index --load --query "AAPL FDA approval delay"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.analysis_log import AnalysisLog  # noqa: E402
from analyzers.embedding_index import EmbeddingIndex  # noqa: E402

_INDEX_PATH = str(Path(__file__).parent.parent / "data" / "embedding_index")


def _row_text(row: dict) -> str:
    parts = [
        row.get("entry_rationale") or "",
        row.get("bull_case") or "",
        row.get("bear_case") or "",
        " ".join(row.get("key_catalysts") or []),
        " ".join(row.get("risk_factors") or []),
    ]
    return " ".join(p for p in parts if p)


def _p(s: str = "") -> None:
    print(s)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=1000,
                     help="max. Analysen für den Index (Default 1000, jüngste zuerst)")
    ap.add_argument("--load", action="store_true",
                     help="bestehenden Index laden statt neu zu bauen")
    ap.add_argument("--no-save", action="store_true", help="Index nicht persistieren")
    ap.add_argument("--query", default=None, help="Suchtext; ohne = nur Index bauen")
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()

    idx = EmbeddingIndex()

    if args.load:
        _p(f"Lade Index von {_INDEX_PATH} …")
        idx.load(_INDEX_PATH)
    else:
        log = AnalysisLog()
        rows = log.get_recent(limit=args.limit)
        _p(f"{len(rows)} Analysen geladen — embedde …")
        idx.build(rows, text_fn=_row_text)
        _p(f"{len(idx)} indexiert ({len(rows) - len(idx)} ohne Text übersprungen).")
        if not args.no_save:
            idx.save(_INDEX_PATH)
            _p(f"Index gespeichert nach {_INDEX_PATH}.*")

    if not args.query:
        return

    _p(f"\n── Suche: \"{args.query}\" {'─' * 20}")
    results = idx.search(args.query, top_k=args.top_k)
    if not results:
        _p("Keine Treffer (leerer Index oder leere Anfrage).")
        return
    for r in results:
        m = r.meta
        _p(f"\n{r.similarity:.3f}  {m.get('ticker', '?'):<8} {m.get('analyzed_at', '')[:10]} "
           f"{m.get('recommendation', '')}")
        rationale = (m.get("entry_rationale") or "")[:160]
        if rationale:
            _p(f"       {rationale}")


if __name__ == "__main__":
    main()
