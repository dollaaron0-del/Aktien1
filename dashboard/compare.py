"""
dashboard/compare.py — Zeitraum-Vergleich (Ausbau-Roadmap H2.4).

Reine Aggregations-Funktion, read-only über bestehende Logs. Kein neuer
Datenspeicher, keine Schreiboperation.

Hinweis zur Abweichung vom ursprünglichen Roadmap-Entwurf: `AnalysisLog`
hat KEINE datumsbezogene Aggregation (`get_stats()` läuft über die
gesamte Tabelle, nicht über einen Zeitraum) — eine Erweiterung dort läge
außerhalb des für diese Ausbau-Session erlaubten Pfads
(`analyzers/` ist tabu). Stattdessen wird `get_recent(limit=...)`
read-only gezogen und clientseitig nach `analyzed_at` gefiltert.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List


def _daterange(start_day: str, end_day: str) -> List[str]:
    """Alle Tage zwischen start_day/end_day (inklusive, YYYY-MM-DD),
    Reihenfolge der Argumente egal."""
    start = date.fromisoformat(start_day)
    end = date.fromisoformat(end_day)
    if end < start:
        start, end = end, start
    days = []
    d = start
    while d <= end:
        days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def week_stats(start_day: str, end_day: str, analysis_limit: int = 5000) -> Dict:
    """Aggregiert read-only über [start_day, end_day] (inklusive).
    Fail-open: fehlende/kaputte Logs liefern 0 statt zu crashen.

    Rückgabe: {"days": [...], "total", "buy", "skip", "hold",
    "n_analyses", "avg_sentiment"}.
    """
    days = _daterange(start_day, end_day)

    total = buy = skip = hold = 0
    try:
        from analyzers.decision_log import DecisionLog
        dlog = DecisionLog()
        for day in days:
            try:
                funnel = dlog.funnel(day)
            except Exception:
                continue
            total += funnel.get("total", 0) or 0
            actions = funnel.get("actions") or {}
            buy += actions.get("BUY", 0) or 0
            skip += actions.get("SKIP", 0) or 0
            hold += actions.get("HOLD", 0) or 0
    except Exception:
        pass

    n_analyses = 0
    avg_sentiment = 0.0
    try:
        from analyzers.analysis_log import AnalysisLog
        alog = AnalysisLog()
        day_set = set(days)
        scores = []
        for row in alog.get_recent(limit=analysis_limit):
            ts = str(row.get("analyzed_at") or "")
            if ts[:10] in day_set:
                n_analyses += 1
                score = row.get("sentiment_score")
                if isinstance(score, (int, float)):
                    scores.append(score)
        if scores:
            avg_sentiment = sum(scores) / len(scores)
    except Exception:
        pass

    return {
        "days": days,
        "total": total,
        "buy": buy,
        "skip": skip,
        "hold": hold,
        "n_analyses": n_analyses,
        "avg_sentiment": round(avg_sentiment, 3),
    }
