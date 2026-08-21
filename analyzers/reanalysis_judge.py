"""
Re-Analyse-Studie (LLM-as-Judge) — Roadmap 6.9c.

Bewertet archivierte Entscheidungen (analyzers.experience_store.ExperienceStore,
bereits gelabelt mit echtem Outcome) im Nachhinein per lokalem LLM: war die
damalige Begründung angemessen, oder liegt ein systematischer Analysefehler
vor? Via Claude-API unbezahlbar (jede Entscheidung nochmal bezahlen), lokal
~0 € (Ollama).

Feste Taxonomie statt Freitext-Auswertung — macht die Ergebnisse aggregierbar
(Muster wie track_record.py-Buckets), auf Kosten etwas gröberer Kategorien:
  KORREKT             – Begründung war stimmig, Ausgang bestätigt sie
  UEBERKONFIDENT       – hohe Konfidenz geäußert, Ausgang widerspricht ihr
  RISIKO_UEBERSEHEN    – ein Risikofaktor, der den schlechten Ausgang klar
                         erklärt, wurde damals NICHT genannt
  PECH                 – Begründung + Risiken waren stimmig, unvorhersehbares
                         Ereignis hat trotzdem zum schlechten Ausgang geführt
  UNKLAR               – nicht genug Information für ein Urteil

Bewusst NUR Diagnose – kein Wiring in Kalibrierung (1.2) oder Live-Pfad;
LLM-Urteile sind selbst verrauscht (vgl. Roadmap-6.9-Leitplanke), Ergebnis
ist ein Hinweis für Menschen, kein automatischer Regler.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

TAXONOMY = ("KORREKT", "UEBERKONFIDENT", "RISIKO_UEBERSEHEN", "PECH", "UNKLAR")

_JUDGE_PROMPT = """You are auditing a past trading decision with hindsight knowledge \
of the actual outcome. Judge WHY the outcome happened, not whether the decision \
looked reasonable in isolation.

TICKER: {ticker}
ORIGINAL RECOMMENDATION: {recommendation} (direction={direction}, confidence={confidence})
CATALYSTS NOTED AT THE TIME: {key_catalysts}
RISK FACTORS NOTED AT THE TIME: {risk_factors}

ACTUAL OUTCOME: {outcome} ({pnl_pct:+.2f}% over the hold period)

Classify into EXACTLY one category:
- KORREKT: reasoning was sound and the outcome matches it
- UEBERKONFIDENT: high confidence was stated, but the outcome contradicts it
- RISIKO_UEBERSEHEN: a risk factor that clearly explains the bad outcome was \
NOT among the noted risk factors
- PECH: reasoning and noted risks were sound, an unforeseeable event still \
caused a bad outcome
- UNKLAR: not enough information to judge

Return ONLY this JSON (no explanation, no markdown):
{{"category": "<KORREKT|UEBERKONFIDENT|RISIKO_UEBERSEHEN|PECH|UNKLAR>", "reason": "<max 15 words>"}}"""


def build_judge_prompt(
    ticker: str, recommendation: str, direction: str, confidence: str,
    key_catalysts: List[str], risk_factors: List[str],
    outcome: str, pnl_pct: float,
) -> str:
    return _JUDGE_PROMPT.format(
        ticker=ticker,
        recommendation=recommendation or "?",
        direction=direction or "?",
        confidence=confidence or "?",
        key_catalysts="; ".join(key_catalysts or []) or "(keine genannt)",
        risk_factors="; ".join(risk_factors or []) or "(keine genannt)",
        outcome=outcome or "?",
        pnl_pct=pnl_pct if pnl_pct is not None else 0.0,
    )


def _parse_judge_response(raw: str) -> Optional[Dict]:
    """Robust gegen Formatfehler – analog
    OllamaPrescreener._parse_ollama_response, aber schlankeres Schema."""
    candidates = [raw]
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(raw[start:end + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        category = str(data.get("category", "")).strip().upper()
        if category not in TAXONOMY:
            continue
        return {"category": category, "reason": str(data.get("reason", ""))[:200]}
    return None


def judge_decision(
    prescreener, ticker: str, recommendation: str, direction: str,
    confidence: str, key_catalysts: List[str], risk_factors: List[str],
    outcome: str, pnl_pct: float,
) -> Optional[Dict]:
    """Ein Urteil für eine gelabelte Entscheidung. None bei Ollama-Fehler/
    unparsbarer Antwort (fail-open, kein erfundenes Urteil)."""
    prompt = build_judge_prompt(
        ticker, recommendation, direction, confidence,
        key_catalysts, risk_factors, outcome, pnl_pct,
    )
    raw = prescreener.generate(prompt, max_tokens=80)
    if raw is None:
        return None
    return _parse_judge_response(raw)
