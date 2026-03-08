# Location: scripts/chunk_and_embed.py
# Purpose: Chunk full-text papers into ~500-token segments and embed each chunk
# Functions: chunk_text, main
# Calls: ara/central_db.py, google.genai
# Imports: json, time, logging, sys

"""
Chunked Full-Text Embedding Pipeline
=====================================
Splits each paper's full text into ~500-token overlapping chunks,
stores them in paper_chunks table, then embeds each chunk.

Usage:
    python scripts/chunk_and_embed.py [slice_n] [total_slices]

    # Single process (all papers):
    python scripts/chunk_and_embed.py

    # Parallel (e.g., slice 0 of 8):
    python scripts/chunk_and_embed.py 0 8
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ara.central_db import CentralDB
from ara.credentials import load_api_key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_log = logging.getLogger("chunk_embed")

CHUNK_SIZE = 500  # ~500 tokens worth of chars (~2000 chars)
CHUNK_CHARS = 2000
CHUNK_OVERLAP = 200  # 200 char overlap between chunks


def chunk_text(text: str, chunk_chars: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks of ~chunk_chars characters."""
    if not text or len(text) < 200:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_chars

        # Try to break at sentence boundary
        if end < len(text):
            # Look for sentence end in last 20% of chunk
            search_start = end - int(chunk_chars * 0.2)
            best_break = -1
            for sep in ['. ', '.\n', '? ', '! ', '\n\n']:
                pos = text.rfind(sep, search_start, end)
                if pos > best_break:
                    best_break = pos + len(sep)
            if best_break > search_start:
                end = best_break

        chunk = text[start:end].strip()
        if len(chunk) >= 100:  # Skip tiny chunks
            chunks.append(chunk)

        start = end - overlap
        if start >= len(text):
            break

    return chunks


def main():
    slice_n = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    total_slices = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    use_or = os.getenv("USE_OPENROUTER", "").lower() in ("1", "true", "yes")

    _log.info("Slice %d/%d, OpenRouter=%s", slice_n, total_slices, use_or)

    db = CentralDB()

    # Phase 1: Chunk papers that haven't been chunked yet
    rows = db._conn.execute(
        "SELECT p.paper_id, p.title, p.full_text FROM papers p "
        "WHERE p.full_text IS NOT NULL AND p.full_text != '' "
        "AND p.paper_id NOT IN (SELECT DISTINCT paper_id FROM paper_chunks) "
        "ORDER BY p.paper_id"
    ).fetchall()

    # Slice
    my_papers = [dict(r) for i, r in enumerate(rows) if i % total_slices == slice_n]
    _log.info("Chunking %d papers (of %d total unchunked)", len(my_papers), len(rows))

    total_chunks = 0
    for paper in my_papers:
        chunks = chunk_text(paper["full_text"])
        if chunks:
            stored = db.store_chunks(paper["paper_id"], chunks)
            total_chunks += stored

    _log.info("Stored %d chunks from %d papers", total_chunks, len(my_papers))

    # Phase 2: Embed chunks that don't have embeddings
    unembedded = db._conn.execute(
        "SELECT chunk_id, chunk_text FROM paper_chunks WHERE embedding IS NULL ORDER BY chunk_id"
    ).fetchall()

    my_chunks = [dict(r) for i, r in enumerate(unembedded) if i % total_slices == slice_n]
    _log.info("Embedding %d chunks (of %d total unembedded)", len(my_chunks), len(unembedded))

    if not my_chunks:
        _log.info("Nothing to embed")
        return

    if use_or:
        from openai import OpenAI
        or_key = os.getenv("OPENROUTER_API_KEY", "")
        if not or_key:
            _log.error("Set OPENROUTER_API_KEY")
            sys.exit(1)
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=or_key)
        embedded = 0
        for j in range(0, len(my_chunks), 50):
            batch = my_chunks[j:j + 50]
            texts = [r["chunk_text"][:500] for r in batch]
            try:
                result = client.embeddings.create(model="google/gemini-embedding-001", input=texts)
                for row, emb in zip(batch, result.data):
                    db.store_chunk_embedding(row["chunk_id"], emb.embedding)
                    embedded += 1
            except Exception as exc:
                _log.warning("Embed error: %s", exc)
                if "429" in str(exc) or "rate" in str(exc).lower():
                    time.sleep(10)
                else:
                    time.sleep(2)
            if (j + 50) % 500 == 0:
                _log.info("  Embedded %d/%d", embedded, len(my_chunks))
        _log.info("Embedded %d chunks via OpenRouter", embedded)
    else:
        gkey = load_api_key()
        if not gkey:
            _log.error("No Google API key")
            sys.exit(1)
        from google import genai
        client = genai.Client(api_key=gkey)
        embedded = 0
        for j in range(0, len(my_chunks), 50):
            batch = my_chunks[j:j + 50]
            texts = [r["chunk_text"][:500] for r in batch]
            try:
                result = client.models.embed_content(model="gemini-embedding-001", contents=texts)
                if result.embeddings:
                    for row, emb_obj in zip(batch, result.embeddings):
                        db.store_chunk_embedding(row["chunk_id"], emb_obj.values)
                        embedded += 1
            except Exception as exc:
                _log.warning("Embed error: %s", exc)
                if "429" in str(exc) or "rate" in str(exc).lower():
                    time.sleep(30)
                else:
                    time.sleep(2)
            if (j + 50) % 500 == 0:
                _log.info("  Embedded %d/%d", embedded, len(my_chunks))
        _log.info("Embedded %d chunks via Google direct", embedded)

    # Final stats
    total = db._conn.execute("SELECT COUNT(*) FROM paper_chunks").fetchone()[0]
    with_emb = db._conn.execute("SELECT COUNT(*) FROM paper_chunks WHERE embedding IS NOT NULL").fetchone()[0]
    _log.info("Final: %d total chunks, %d embedded", total, with_emb)


if __name__ == "__main__":
    main()
