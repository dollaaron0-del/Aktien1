"""
Embedding-Index über archivierte Analysen — Roadmap 6.9b.

"Ähnliche historische Situationen" als Analyse-Kontext (Präzedenzfall-Abruf
statt nur aktueller Daten): archivierte Analyse-Texte (analysis_log,
prompt_archive, News-Snapshots) werden mit einem lokalen Embedding-Modell
vektorisiert; eine neue Analyse-Anfrage findet per Kosinus-Ähnlichkeit die
nächsten Präzedenzfälle.

Kein Ollama-Embeddings-Endpoint (der lokale Server läuft ohne
`--embeddings`-Flag, s. Befund 9.8.2026 — Neustart mit Flag bräuchte sudo).
Stattdessen sentence-transformers/all-MiniLM-L6-v2 direkt über
transformers.AutoModel + Mean-Pooling (torch/transformers sind seit der
FinBERT-Anbindung 7.8.2026 bereits gepinnt, keine neue Abhängigkeit) —
384-dim, ~90MB, läuft CPU-tauglich schnell genug für Batch-Indexierung.

Kein Vektor-DB-Unterbau nötig: bei der aktuellen Größenordnung
(analysis_log ~1620 Einträge) ist die volle Kosinus-Matrix trivial
(1620×384 Floats ≈ 2,5MB) — reines numpy statt FAISS/Chroma.

Bewusst NUR Retrieval-Baustein — kein Wiring in den Live-Analyse-Prompt
(das wäre ein eigener, separater Schritt nach belegtem Effekt, Muster wie
2.1/2.5/4.3/6.9e/6.9f).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_encoder_singleton: Optional[Callable[[List[str]], np.ndarray]] = None


def _load_default_encoder(model_name: str = _DEFAULT_MODEL):
    """Lädt Tokenizer+Modell EINMAL pro Prozess (Modul-Singleton, wie
    system.resource_manager es für TIER_MODELS macht) – Encoder-Aufruf
    selbst bleibt eine reine Funktion (Text -> normalisierte Vektoren)."""
    import torch
    from transformers import AutoModel, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name)
    model.eval()

    def encode(texts: List[str]) -> np.ndarray:
        enc = tok(texts, padding=True, truncation=True, return_tensors="pt")
        with torch.no_grad():
            out = model(**enc)
        mask = enc["attention_mask"].unsqueeze(-1).float()
        summed = (out.last_hidden_state * mask).sum(1)
        counts = mask.sum(1).clamp(min=1e-9)
        mean = summed / counts
        return torch.nn.functional.normalize(mean, p=2, dim=1).numpy()

    return encode


def get_default_encoder() -> Callable[[List[str]], np.ndarray]:
    """Modul-weiter Singleton – ein Modell-Download/-Load pro Prozess,
    nicht pro EmbeddingIndex-Instanz."""
    global _encoder_singleton
    if _encoder_singleton is None:
        _encoder_singleton = _load_default_encoder()
    return _encoder_singleton


@dataclass
class SearchResult:
    similarity: float
    meta: Dict = field(default_factory=dict)


class EmbeddingIndex:
    """Encoder ist injizierbar (Tests/Batch-Aufrufer teilen sich einen
    Fake statt ein echtes Modell zu laden – Muster wie ClaudeAnalyzer in
    decision_replay.py)."""

    def __init__(self, encoder: Optional[Callable[[List[str]], np.ndarray]] = None):
        self._encoder = encoder or get_default_encoder()
        self._vectors: Optional[np.ndarray] = None   # (n, dim), L2-normalisiert
        self._meta: List[Dict] = []

    def build(self, rows: List[Dict], text_fn: Callable[[Dict], str]) -> None:
        """Vektorisiert `text_fn(row)` je Zeile; leere/None-Texte werden
        übersprungen (kein Vektor aus leerem String)."""
        texts, kept = [], []
        for row in rows:
            t = (text_fn(row) or "").strip()
            if t:
                texts.append(t)
                kept.append(row)
        if not texts:
            self._vectors = np.zeros((0, 0))
            self._meta = []
            return
        self._vectors = self._encoder(texts)
        self._meta = kept

    def __len__(self) -> int:
        return len(self._meta)

    def search(self, query: str, top_k: int = 5) -> List[SearchResult]:
        if not query or not query.strip() or self._vectors is None or len(self._meta) == 0:
            return []
        qvec = self._encoder([query])[0]
        sims = self._vectors @ qvec
        order = np.argsort(-sims)[:top_k]
        return [SearchResult(similarity=float(sims[i]), meta=self._meta[i]) for i in order]

    # ── Persistenz ────────────────────────────────────────────────────────────
    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        np.save(path + ".vectors.npy", self._vectors if self._vectors is not None
                else np.zeros((0, 0)))
        with open(path + ".meta.json", "w", encoding="utf-8") as f:
            json.dump(self._meta, f)

    def load(self, path: str) -> None:
        self._vectors = np.load(path + ".vectors.npy")
        with open(path + ".meta.json", encoding="utf-8") as f:
            self._meta = json.load(f)
