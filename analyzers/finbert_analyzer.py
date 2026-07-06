"""
FinBERT — finanzspezifisches BERT-Sentiment-Modell (lokal, kein API-Key).

ProsusAI/finbert wurde auf Financial PhraseBank + FiQA-SA trainiert.
Deutlich besser als generische Modelle für Finanznachrichten:
  - Versteht Begriffe wie "beat estimates", "guidance raised", "downgrade"
  - Unterscheidet positives Wachstum von "positiven" Verlusten ("only lost $2M")
  - ~438MB Download, einmalig gecacht in ~/.cache/huggingface/

Setup (automatisch beim ersten Aufruf):
    pip3 install transformers torch --break-system-packages

Geschwindigkeit CPU: ~50–100ms für 10 Headlines im Batch.
"""
from __future__ import annotations

from typing import List, Dict, Optional
from logger import get_logger

log = get_logger(__name__)

_MODEL_NAME = "ProsusAI/finbert"
_BATCH_SIZE  = 16
_MAX_LEN     = 512

_pipeline_instance = None
_available: Optional[bool] = None


def _get_pipeline():
    global _pipeline_instance, _available
    if _available is not None:
        return _pipeline_instance if _available else None
    try:
        from transformers import pipeline as hf_pipeline
        _pipeline_instance = hf_pipeline(
            "text-classification",
            model=_MODEL_NAME,
            device=-1,          # CPU
            top_k=None,         # return all three labels per item
            batch_size=_BATCH_SIZE,
            truncation=True,
            max_length=_MAX_LEN,
        )
        _available = True
        log.info("FinBERT geladen: %s (CPU)", _MODEL_NAME)
    except Exception as e:
        _available = False
        log.debug("FinBERT nicht verfügbar (pip install transformers torch): %s", e)
    return _pipeline_instance if _available else None


class FinBERTAnalyzer:
    """
    Finanzspezifisches Sentiment als lokaler Pre-Filter vor Ollama/Claude.

    Hauptnutzen:
      - Billige erste Einschätzung (<100ms, kein API-Cost)
      - Erkennt klar negative Nachrichtenlage → spart Ollama- und Claude-Calls
      - Erkennt positive Überraschungen früh → hebt Claude-Analyseprioritäten
      - FinBERT-Score wird als Synthese-Signal in Claude-Prompt injiziert
    """

    def is_available(self) -> bool:
        return _get_pipeline() is not None

    def analyze_headlines(self, headlines: List[str]) -> Dict:
        """
        Analysiert bis zu 20 Headlines und gibt aggregiertes Sentiment zurück.

        Returns:
            label:      "POSITIVE" | "NEGATIVE" | "NEUTRAL"
            score:      float  0.0 (rein bärisch) … 1.0 (rein bullisch)
            confidence: "HIGH" | "MEDIUM" | "LOW"
            pos_pct:    % der Headlines die positiv klassifiziert wurden
            neg_pct:    % der Headlines die negativ klassifiziert wurden
        """
        pipe = _get_pipeline()
        if pipe is None or not headlines:
            return {"label": "NEUTRAL", "score": 0.5, "confidence": "LOW",
                    "pos_pct": 0, "neg_pct": 0}

        texts = [h[:_MAX_LEN] for h in headlines[:20]]
        try:
            results = pipe(texts)
        except Exception as e:
            log.debug("FinBERT Inference fehlgeschlagen: %s", e)
            return {"label": "NEUTRAL", "score": 0.5, "confidence": "LOW",
                    "pos_pct": 0, "neg_pct": 0}

        pos_scores, neg_scores = [], []
        for item_results in results:
            d = {r["label"].lower(): r["score"] for r in item_results}
            pos_scores.append(d.get("positive", 0.0))
            neg_scores.append(d.get("negative", 0.0))

        avg_pos = sum(pos_scores) / len(pos_scores)
        avg_neg = sum(neg_scores) / len(neg_scores)

        # 0.5 = neutral, range [0, 1]
        score = max(0.0, min(1.0, 0.5 + (avg_pos - avg_neg) / 2.0))

        if avg_pos > avg_neg and avg_pos > (1 - avg_pos - avg_neg):
            label = "POSITIVE"
        elif avg_neg > avg_pos and avg_neg > (1 - avg_pos - avg_neg):
            label = "NEGATIVE"
        else:
            label = "NEUTRAL"

        margin = abs(avg_pos - avg_neg)
        if margin > 0.30:
            confidence = "HIGH"
        elif margin > 0.12:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        n = len(texts)
        pos_pct = round(sum(1 for p, ng in zip(pos_scores, neg_scores) if p > ng) / n * 100)
        neg_pct = round(sum(1 for p, ng in zip(pos_scores, neg_scores) if ng > p) / n * 100)

        return {
            "label":      label,
            "score":      round(score, 3),
            "confidence": confidence,
            "pos_pct":    pos_pct,
            "neg_pct":    neg_pct,
        }

    def build_signal_item(self, ticker: str, result: Dict) -> Optional[Dict]:
        """
        Erstellt ein synthetisches News-Item das den FinBERT-Score in den
        Claude-Prompt injiziert. Claude gewichtet es als unabhängige Quelle.
        Nur wenn FinBERT ein klares Signal hat (nicht LOW confidence).
        """
        if result["confidence"] == "LOW":
            return None
        label    = result["label"]
        score    = result["score"]
        conf     = result["confidence"]
        pos_pct  = result["pos_pct"]
        neg_pct  = result["neg_pct"]

        label_de = {"POSITIVE": "POSITIV", "NEGATIVE": "NEGATIV", "NEUTRAL": "NEUTRAL"}[label]
        signal   = "BULLISCH" if label == "POSITIVE" else ("BÄRISCH" if label == "NEGATIVE" else "NEUTRAL")
        from datetime import datetime, timezone
        return {
            "source":    "FinBERT-KI-Sentiment",
            "title":     (
                f"FinBERT-Analyse {ticker}: {label_de} "
                f"({pos_pct}% positiv / {neg_pct}% negativ, Score {score:.2f})"
            ),
            "text": (
                f"Automatische ML-Sentimentanalyse durch FinBERT "
                f"(finanzspezifisches BERT, trainiert auf Financial PhraseBank). "
                f"Ergebnis: {label_de} mit {conf}-Konfidenz. "
                f"{pos_pct}% der aktuellen Headlines klassifiziert als positiv, "
                f"{neg_pct}% als negativ. "
                f"Aggregierter Score: {score:.3f} (0=bärisch, 1=bullisch). "
                f"Dieses Modell versteht Finanzsprache zuverlässiger als generische Sentiment-Tools. "
                f"Signal: {signal}."
            ),
            "published": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "signal":    signal,
            "finbert_score": score,
        }
