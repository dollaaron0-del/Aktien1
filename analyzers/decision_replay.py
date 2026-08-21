"""
DecisionReplay – Entscheidungs-Replay (Roadmap 4.5).

Baut auf dem KI-Prompt-Archiv (1.4d): repliziert NICHT den Claude-Aufruf selbst
(das wäre nicht deterministisch und würde erneut Geld kosten) – sondern spielt
den archivierten ECHTEN Antworttext durch die AKTUELLE Parsing-/Schwellen-Logik
(ClaudeAnalyzer._parse_response/_enforce_buy_floor) und vergleicht das Ergebnis
mit der damals tatsächlich geloggten Empfehlung. Beantwortet: "würde der
heutige Code aus derselben KI-Antwort dieselbe Entscheidung ableiten?" –
nützlich um Code-/Schwellen-Änderungen (z.B. buy_threshold, _enforce_buy_floor)
gegen echte historische KI-Antworten zu prüfen, ohne die API erneut zu bezahlen.

Zwei archivierte Antwort-Schemata (beide über dieselbe analysis_id verkettet):
- Standard-Analyse (_claude_analysis): recommendation BUY/HOLD/SKIP + direction/
  confidence, geparst über ClaudeAnalyzer._parse_response.
- These-Check offener Positionen (_thesis_check): thesis_valid + recommendation
  HOLD/SELL, eigenes schlankeres Schema. Am charakteristischen Prompt-Text
  erkannt (is_thesis_check_prompt) statt an Modellnamen (fragiler).
"""
from __future__ import annotations

from typing import Dict, List, Optional

_THESIS_CHECK_MARKER = "Ist die Kaufthese noch intakt"

_ANALYSIS_FIELDS = ("recommendation", "direction", "confidence", "sentiment_score")


def is_thesis_check_prompt(user_prompt: str) -> bool:
    """Erkennt einen archivierten These-Check-Prompt am charakteristischen Text
    aus _USER_TEMPLATE_THESIS_CHECK – robuster als eine Modellnamen-Heuristik."""
    return _THESIS_CHECK_MARKER in (user_prompt or "")


def replay_response(ticker: str, user_prompt: str, response_text: str,
                    analyzer=None) -> Dict:
    """Parst einen archivierten Antworttext mit der AKTUELLEN Parsing-Logik.

    Rein funktional (kein API-Call, kein DB-Zugriff) – deterministisch bei
    gleichem Code-Stand. `analyzer` ist injizierbar (Batch-Aufrufer wie
    replay_recent teilen sich EINE ClaudeAnalyzer-Instanz statt pro Zeile
    neu zu konstruieren – der Konstruktor liest u.a. den API-Kosten-Stand
    von der Platte)."""
    if analyzer is None:
        from analyzers.claude_analyzer import ClaudeAnalyzer
        analyzer = ClaudeAnalyzer()
    if is_thesis_check_prompt(user_prompt):
        data = analyzer._safe_json(response_text)
        return {
            "kind": "thesis_check",
            "thesis_valid": data.get("thesis_valid", True),
            "thesis_break_reason": data.get("thesis_break_reason", ""),
            "sentiment_score": float(data.get("sentiment_score", 0.5)),
            "recommendation": data.get("recommendation", "HOLD"),
        }
    result = analyzer._parse_response(ticker, response_text)
    return {
        "kind": "analysis",
        "recommendation": result.recommendation,
        "direction": result.direction,
        "confidence": result.confidence,
        "sentiment_score": result.sentiment_score,
    }


def diff_fields(original: Dict, replayed: Dict) -> List[str]:
    """Vergleicht nur die Felder, die das jeweilige Schema tatsächlich führt –
    ein These-Check kennt kein direction/confidence."""
    fields = ("recommendation",) if replayed.get("kind") == "thesis_check" else _ANALYSIS_FIELDS
    return [f for f in fields if (original or {}).get(f) != replayed.get(f)]


def _resolve_prompt_archive(prompt_archive):
    if prompt_archive is None:
        from analyzers.prompt_archive import PromptArchive
        prompt_archive = PromptArchive()
    return prompt_archive


def _resolve_analysis_log(analysis_log):
    if analysis_log is None:
        from analyzers.analysis_log import AnalysisLog
        analysis_log = AnalysisLog()
    return analysis_log


def replay_analysis(
    analysis_id: int, analysis_log=None, prompt_archive=None,
    analyzer=None, _entry: Optional[Dict] = None, _original: Optional[Dict] = None,
) -> Optional[Dict]:
    """Repliziert EINE archivierte Analyse gegen die damals geloggte Empfehlung.

    Liefert None, wenn kein Prompt archiviert ist (Ollama-/Frugal-Route – 1.4d
    archiviert bewusst nur echte Claude-Aufrufe) oder die analysis_id im
    Analyse-Log nicht (mehr) existiert. `analysis_log`/`prompt_archive`/
    `analyzer` sind injizierbar (Tests, Batch-Aufrufer teilen sich eine
    Instanz statt pro Zeile neu zu verbinden/konstruieren). `_entry`/
    `_original`: interne Parameter für Aufrufer, die den Archiv- bzw.
    Analyse-Log-Eintrag bereits gelesen haben (replay_recent über
    prompt_archive.recent(); das Dashboard über AnalysisLog().get_by_id() für
    die "Zugehörige Analyse"-Anzeige) und ihn nicht doppelt nachschlagen
    müssen.
    """
    if _entry is not None:
        entry = _entry
    else:
        prompt_archive = _resolve_prompt_archive(prompt_archive)
        entry = prompt_archive.get_by_analysis_id(analysis_id)
    if not entry or not entry.get("response_text"):
        return None

    if _original is not None:
        original = _original
    else:
        original = _resolve_analysis_log(analysis_log).get_by_id(analysis_id)
    if not original:
        return None

    replayed = replay_response(
        entry.get("ticker", ""), entry.get("user_prompt", ""), entry["response_text"],
        analyzer=analyzer,
    )
    changed = diff_fields(original, replayed)
    return {
        "analysis_id": analysis_id,
        "ticker": entry.get("ticker"),
        "archived_at": entry.get("created_at"),
        "raw_model": entry.get("model"),
        "kind": replayed["kind"],
        "original": {f: original.get(f) for f in _ANALYSIS_FIELDS},
        "replayed": replayed,
        "changed_fields": changed,
        "changed": bool(changed),
    }


def replay_recent(
    limit: int = 200, analysis_log=None, prompt_archive=None
) -> List[Dict]:
    """Batch-Audit über die letzten `limit` archivierten Prompts – Basis für
    einen Drift-Report nach Code-/Schwellen-Änderungen (z.B. buy_threshold).
    Teilt sich EINE ClaudeAnalyzer-Instanz über den ganzen Batch (statt pro
    Zeile neu zu konstruieren) und reicht den bereits gelesenen Archiv-Eintrag
    direkt durch (kein zweites get_by_analysis_id() je Zeile)."""
    prompt_archive = _resolve_prompt_archive(prompt_archive)
    analysis_log = _resolve_analysis_log(analysis_log)
    from analyzers.claude_analyzer import ClaudeAnalyzer
    analyzer = ClaudeAnalyzer()

    out = []
    for entry in prompt_archive.recent(limit=limit):
        aid = entry.get("analysis_id")
        if aid is None:
            continue
        r = replay_analysis(aid, analysis_log=analysis_log, prompt_archive=prompt_archive,
                            analyzer=analyzer, _entry=entry)
        if r is not None:
            out.append(r)
    return out
