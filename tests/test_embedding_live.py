# Location: tests/test_embedding_live.py
# Purpose: Live integration test for Gemini embedding pipeline
# Functions: test_embed, test_cosine, test_batch
# Calls: ara/tools/pipeline.py, ara/tools/papers.py, ara/db.py
# Imports: os, json, tempfile

"""
Run with: GOOGLE_API_KEY=<your-key> python -m pytest tests/test_embedding_live.py -v -s
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from ara.db import ARADB
from ara.tools.pipeline import embed_text, batch_embed_papers
from ara.tools.papers import search_similar, _cosine_similarity

SKIP_REASON = "GOOGLE_API_KEY not set"
has_key = bool(os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY"))


def test_cosine_similarity_math():
    """Cosine similarity works correctly (no API needed)."""
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    assert _cosine_similarity(a, b) == pytest.approx(1.0)

    c = [0.0, 1.0, 0.0]
    assert _cosine_similarity(a, c) == pytest.approx(0.0)

    d = [1.0, 1.0, 0.0]
    assert _cosine_similarity(a, d) == pytest.approx(0.7071, abs=0.001)

    assert _cosine_similarity([], []) == 0.0
    assert _cosine_similarity([0, 0, 0], [1, 1, 1]) == 0.0


def test_db_embedding_roundtrip():
    """Store and retrieve embeddings from DB (no API needed)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = ARADB(Path(tmpdir) / "test.db")
        sid = db.create_session("test topic")

        db.store_papers(sid, [
            {"title": "Paper about AI", "abstract": "Deep learning methods", "authors": ["Smith"], "source": "test"},
            {"title": "Paper about climate", "abstract": "Global warming effects", "authors": ["Jones"], "source": "test"},
        ])

        unembedded = db.get_unembedded_papers(sid)
        assert len(unembedded) == 2

        # Store fake embeddings
        db.store_embedding(unembedded[0]["paper_id"], [0.1, 0.2, 0.3])
        db.store_embedding(unembedded[1]["paper_id"], [0.4, 0.5, 0.6])

        # Verify
        still_unembedded = db.get_unembedded_papers(sid)
        assert len(still_unembedded) == 0

        with_emb = db.get_papers_with_embeddings(sid)
        assert len(with_emb) == 2
        assert with_emb[0]["embedding"] == [0.1, 0.2, 0.3]
        db.close()


def test_search_similar_with_fake_embeddings():
    """search_similar uses cosine similarity when embeddings exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = ARADB(Path(tmpdir) / "test.db")
        sid = db.create_session("test topic")

        db.store_papers(sid, [
            {"title": "Neural networks for NLP", "abstract": "Transformers and attention", "authors": ["Smith"], "source": "test"},
            {"title": "Climate change impacts", "abstract": "Rising temperatures in arctic", "authors": ["Jones"], "source": "test"},
        ])

        papers = db.get_unembedded_papers(sid)
        # AI paper gets embedding close to query, climate paper gets orthogonal
        db.store_embedding(papers[0]["paper_id"], [0.9, 0.1, 0.0])
        db.store_embedding(papers[1]["paper_id"], [0.0, 0.1, 0.9])

        ctx = {"db": db, "session_id": sid}

        # Without API key, it will try embeddings but fail on query embedding,
        # then fall back to keyword
        result = json.loads(search_similar({"text": "deep learning"}, ctx))
        # Should fall back to keyword since no API key for query embedding
        assert result["method"] in ("keyword_fallback", "embedding_cosine")
        db.close()


@pytest.mark.skipif(not has_key, reason=SKIP_REASON)
def test_embed_text_live():
    """Live test: embed a text string via Gemini API."""
    result = json.loads(embed_text({"text": "machine learning algorithms"}, {}))
    assert "embedding" in result
    assert result["dimensions"] == 768
    assert len(result["embedding"]) == 768
    print(f"Embedding dimensions: {result['dimensions']}")
    print(f"First 5 values: {result['embedding'][:5]}")


@pytest.mark.skipif(not has_key, reason=SKIP_REASON)
def test_batch_embed_live():
    """Live test: batch embed papers via Gemini API."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = ARADB(Path(tmpdir) / "test.db")
        sid = db.create_session("test topic")

        db.store_papers(sid, [
            {"title": "Machine learning in healthcare", "abstract": "AI diagnostics for radiology", "authors": ["Chen", "Lee"], "source": "test"},
            {"title": "Quantum computing basics", "abstract": "Qubits and entanglement", "authors": ["Patel"], "source": "test"},
            {"title": "Urban planning with GIS", "abstract": "Geographic information systems for city design", "authors": ["Garcia"], "source": "test"},
        ])

        ctx = {"db": db, "session_id": sid}
        result = json.loads(batch_embed_papers({}, ctx))
        print(f"Batch result: {result}")

        assert result["embedded"] == 3
        assert result["failed"] == 0

        # Verify embeddings stored
        with_emb = db.get_papers_with_embeddings(sid)
        assert len(with_emb) == 3
        assert len(with_emb[0]["embedding"]) == 768

        # Now test semantic search
        result = json.loads(search_similar({"text": "artificial intelligence medicine", "limit": 3}, ctx))
        print(f"Search result: {json.dumps(result, indent=2, default=str)[:500]}")
        assert result["method"] == "embedding_cosine"
        # AI/healthcare paper should rank first
        assert "healthcare" in result["papers"][0]["title"].lower() or "machine" in result["papers"][0]["title"].lower()

        db.close()
