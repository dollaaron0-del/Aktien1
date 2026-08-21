"""
SemanticDeduplicator – findet inhaltlich gleiche Nachrichten via Embeddings.

Verwendet nomic-embed-text (274 MB, via Ollama) um semantische Ähnlichkeit
zwischen News-Artikeln zu messen. Paraphrasierte Versionen derselben Meldung
werden auf den besten Artikel reduziert.

Fallback: Bei Ollama-Ausfall exaktes Titel-Matching (bisheriges Verhalten).

Konfiguration via Env-Variablen:
  SEMANTIC_DEDUP_ENABLED      (Standard: true wenn OLLAMA_ENABLED=true)
  SEMANTIC_DEDUP_MODEL        (Standard: nomic-embed-text)
  SEMANTIC_DEDUP_THRESHOLD    (Standard: 0.88, Range 0–1)
  OLLAMA_URL                  (wird geteilt mit OllamaPrescreener)
"""
from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Tuple

import requests

from logger import get_logger

log = get_logger(__name__)

_MODEL     = os.getenv("SEMANTIC_DEDUP_MODEL",     "nomic-embed-text")
_THRESHOLD = float(os.getenv("SEMANTIC_DEDUP_THRESHOLD", "0.88"))
_OLLAMA    = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
_TIMEOUT   = 8   # Sekunden pro Embedding-Aufruf – schnelles Modell


def _cosine(a: List[float], b: List[float]) -> float:
    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a))
    nb   = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _embed(text: str) -> Optional[List[float]]:
    """Ruft Ollama-Embedding für einen Text ab."""
    try:
        resp = requests.post(
            f"{_OLLAMA}/api/embeddings",
            json={"model": _MODEL, "prompt": text[:512]},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json().get("embedding")
    except Exception as e:
        log.debug("Embedding-Fehler: %s", e)
    return None


def _article_text(item: Dict) -> str:
    """Kombiniert Titel + Anfang des Textes für Embedding."""
    title = (item.get("title") or "")[:120]
    body  = (item.get("text") or "")[:200]
    return f"{title} {body}".strip()


def _article_quality(item: Dict) -> float:
    """Gibt eine Qualitätspunktzahl für einen Artikel zurück (höher = besser)."""
    score = 0.0
    if item.get("text"):
        score += min(len(item["text"]) / 500, 2.0)   # längere Texte besser
    prio = {"HIGH": 2.0, "MEDIUM": 1.0}.get(item.get("priority", ""), 0.0)
    score += prio
    # Bekannte Premium-Quellen höher gewichten
    source = (item.get("source") or "").lower()
    if any(s in source for s in ("reuters", "bloomberg", "sec", "8-k", "analyst", "earnings")):
        score += 1.5
    return score


class SemanticDeduplicator:
    """
    Reduziert eine Liste von News-Artikeln auf thematisch einzigartige Meldungen.
    Instanz einmal pro Analyse-Zyklus erstellen und für alle Ticker wiederverwenden.
    """

    def __init__(self):
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            r = requests.get(f"{_OLLAMA}/api/tags", timeout=3)
            if r.status_code == 200:
                models = [m.get("name", "") for m in r.json().get("models", [])]
                self._available = any(_MODEL.split(":")[0] in m for m in models)
                if not self._available:
                    log.info("nomic-embed-text nicht gefunden – Semantic Dedup deaktiviert. "
                             "Aktivieren mit: ollama pull nomic-embed-text")
            else:
                self._available = False
        except Exception:
            self._available = False
        return self._available

    def deduplicate(self, items: List[Dict]) -> List[Dict]:
        """
        Gibt eine Liste ohne semantische Duplikate zurück.
        Artikel die inhaltlich identisch sind werden auf den qualitativ besten reduziert.
        """
        if len(items) <= 1:
            return items

        if not self.is_available():
            return self._title_dedup(items)

        # Embeddings für alle Artikel holen
        texts      = [_article_text(item) for item in items]
        embeddings = [_embed(t) for t in texts]

        # Artikel ohne Embedding direkt behalten (Fehler → sicher durchlassen)
        indexed: List[Tuple[int, List[float]]] = [
            (i, emb) for i, emb in enumerate(embeddings) if emb is not None
        ]
        no_emb_idx = {i for i, emb in enumerate(embeddings) if emb is None}

        # Cluster bilden: greedy – jeder Artikel gehört zum ersten Cluster
        # dessen Zentroid ähnlich genug ist
        clusters: List[List[int]] = []   # Liste von Gruppen (Indizes)

        for idx, emb in indexed:
            placed = False
            for cluster in clusters:
                # Vergleich mit erstem Artikel des Clusters (Repräsentant)
                rep_emb = embeddings[cluster[0]]
                if rep_emb and _cosine(emb, rep_emb) >= _THRESHOLD:
                    cluster.append(idx)
                    placed = True
                    break
            if not placed:
                clusters.append([idx])

        # Pro Cluster: besten Artikel behalten
        kept_indices: List[int] = []
        for cluster in clusters:
            best = max(cluster, key=lambda i: _article_quality(items[i]))
            kept_indices.append(best)
            if len(cluster) > 1:
                dropped = [items[i].get("title", "?")[:60] for i in cluster if i != best]
                log.debug(
                    "Semantic Dedup: Cluster %d Artikel – behalte '%s', entferne: %s",
                    len(cluster), items[best].get("title", "?")[:60], dropped,
                )

        # Artikel ohne Embedding ans Ende hängen (nach Titel-Dedup)
        result = [items[i] for i in sorted(kept_indices)]
        for i in sorted(no_emb_idx):
            result.append(items[i])

        removed = len(items) - len(result)
        if removed > 0:
            log.info("Semantic Dedup: %d/%d Artikel entfernt (Threshold=%.2f)",
                     removed, len(items), _THRESHOLD)

        return result

    @staticmethod
    def _title_dedup(items: List[Dict]) -> List[Dict]:
        """Fallback: exaktes Titel-Matching (bisheriges Verhalten)."""
        seen: set = set()
        unique: List[Dict] = []
        for item in items:
            key = (item.get("title") or "").lower()[:80]
            if key and key not in seen:
                seen.add(key)
                unique.append(item)
            elif not key:
                unique.append(item)
        return unique
