"""
Tests für analyzers/embedding_index.py (Roadmap 6.9b: Embedding-Index /
Präzedenzfall-Abruf). Netzfrei — ein deterministischer Fake-Encoder statt
eines echten HF-Modells (injizierbar, s. EmbeddingIndex(encoder=...)).
"""
import numpy as np
import pytest

from analyzers.embedding_index import EmbeddingIndex

_VOCAB = ["earnings", "beat", "miss", "weather", "acquisition", "fda"]


def _fake_encoder(texts):
    """Bag-of-Words über ein festes Mini-Vokabular, L2-normalisiert —
    kontrollierbare Ähnlichkeit ohne echtes Modell zu laden."""
    vecs = []
    for t in texts:
        words = t.lower().split()
        v = np.array([words.count(w) for w in _VOCAB], dtype=float)
        norm = np.linalg.norm(v)
        vecs.append(v / norm if norm > 0 else v)
    return np.array(vecs)


def _index(rows):
    idx = EmbeddingIndex(encoder=_fake_encoder)
    idx.build(rows, text_fn=lambda r: r["text"])
    return idx


def test_search_ranks_more_similar_higher():
    rows = [
        {"ticker": "AAPL", "text": "earnings beat estimate"},
        {"ticker": "MSFT", "text": "earnings miss estimate"},
        {"ticker": "XYZ", "text": "weather forecast tomorrow"},
    ]
    idx = _index(rows)
    results = idx.search("earnings beat", top_k=3)
    assert [r.meta["ticker"] for r in results][0] == "AAPL"
    assert results[-1].meta["ticker"] == "XYZ"
    # absteigend sortiert
    sims = [r.similarity for r in results]
    assert sims == sorted(sims, reverse=True)


def test_top_k_limits_results():
    rows = [{"ticker": f"T{i}", "text": "earnings beat estimate"} for i in range(10)]
    idx = _index(rows)
    results = idx.search("earnings", top_k=3)
    assert len(results) == 3


def test_empty_text_rows_are_skipped():
    rows = [
        {"ticker": "AAPL", "text": "earnings beat"},
        {"ticker": "EMPTY", "text": ""},
        {"ticker": "NONE", "text": None},
    ]
    idx = _index(rows)
    assert len(idx) == 1


def test_len_reflects_indexed_rows():
    rows = [{"ticker": "AAPL", "text": "earnings beat"},
            {"ticker": "MSFT", "text": "fda approval"}]
    idx = _index(rows)
    assert len(idx) == 2


def test_search_on_empty_index_returns_empty_list():
    idx = EmbeddingIndex(encoder=_fake_encoder)
    idx.build([], text_fn=lambda r: r["text"])
    assert idx.search("earnings") == []


def test_search_with_empty_query_returns_empty_list():
    idx = _index([{"ticker": "AAPL", "text": "earnings beat"}])
    assert idx.search("") == []
    assert idx.search("   ") == []


def test_save_and_load_roundtrip(tmp_path):
    rows = [
        {"ticker": "AAPL", "text": "earnings beat estimate"},
        {"ticker": "XYZ", "text": "weather forecast tomorrow"},
    ]
    idx = _index(rows)
    path = str(tmp_path / "idx")
    idx.save(path)

    idx2 = EmbeddingIndex(encoder=_fake_encoder)
    idx2.load(path)
    assert len(idx2) == 2
    results = idx2.search("earnings beat", top_k=1)
    assert results[0].meta["ticker"] == "AAPL"


def test_build_with_all_empty_texts_yields_empty_index():
    idx = _index([{"ticker": "A", "text": ""}, {"ticker": "B", "text": None}])
    assert len(idx) == 0
    assert idx.search("anything") == []
