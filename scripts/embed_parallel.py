# Location: scripts/embed_parallel.py
# Purpose: Chunk full texts + parallel embedding via OpenRouter API (10 agents)
# Functions: chunk_papers, embed_papers_worker, embed_chunks_worker, main
# Calls: ara/central_db.py, ara/tools/fulltext.py, openai (OpenRouter)
# Imports: threading, time, json, logging

"""
Parallel Chunking + Embedding Script — 10 agents via OpenRouter
================================================================
Phase 1: Chunk all papers with full text but no chunks
Phase 2: Embed unembedded papers (3 agents) + chunks (7 agents) in parallel

Usage:
    python scripts/embed_parallel.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ara.central_db import CentralDB
from ara.tools.fulltext import _chunk_text as chunk_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_log = logging.getLogger("embed_parallel")

# ── Config ──
NUM_PAPER_AGENTS = 3
NUM_CHUNK_AGENTS = 7
BATCH_SIZE = 50
MODEL = "google/gemini-embedding-001"

# ── Shared counters ──
lock = threading.Lock()
papers_done = 0
chunks_done = 0
papers_failed = 0
chunks_failed = 0
start_time = 0.0


def get_client():
    from openai import OpenAI
    creds = json.load(open(Path.home() / ".ara" / "credentials.json"))
    key = creds.get("openrouter_api_key", "")
    if not key:
        key = os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError("No OpenRouter API key found")
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)


# ── Phase 1: Chunking ──

def chunk_papers(db: CentralDB) -> int:
    """Chunk all papers with full text but no chunks."""
    rows = db._conn.execute(
        "SELECT p.paper_id, p.title, p.full_text FROM papers p "
        "WHERE p.full_text IS NOT NULL AND p.full_text != '' "
        "AND p.paper_id NOT IN (SELECT DISTINCT paper_id FROM paper_chunks) "
        "ORDER BY p.paper_id"
    ).fetchall()

    if not rows:
        _log.info("CHUNK: All papers already chunked")
        return 0

    _log.info("CHUNK: %d papers need chunking", len(rows))
    total_chunks = 0
    for i, row in enumerate(rows):
        chunks = chunk_text(row["full_text"])
        if chunks:
            stored = db.store_chunks(row["paper_id"], chunks)
            total_chunks += stored
        if (i + 1) % 50 == 0:
            _log.info("CHUNK: %d/%d papers chunked (%d chunks so far)", i + 1, len(rows), total_chunks)

    _log.info("CHUNK: Done — %d chunks from %d papers", total_chunks, len(rows))
    return total_chunks


# ── Phase 2: Embedding ──

def embed_papers_worker(agent_id: int, paper_batches: list[list[dict]]):
    global papers_done, papers_failed
    client = get_client()
    db = CentralDB()

    for batch in paper_batches:
        texts = []
        for p in batch:
            text = (p.get("title") or "") + ". " + (p.get("abstract") or "")
            texts.append(text[:2000])

        try:
            result = client.embeddings.create(model=MODEL, input=texts)
            for p, emb in zip(batch, result.data):
                blob = json.dumps(emb.embedding)
                db._conn.execute(
                    "UPDATE papers SET embedding = ? WHERE paper_id = ?",
                    (blob, p["paper_id"]),
                )
            db._conn.commit()
            with lock:
                papers_done += len(batch)
        except Exception as exc:
            with lock:
                papers_failed += len(batch)
            if "429" in str(exc) or "rate" in str(exc).lower():
                time.sleep(15)
            else:
                _log.warning("Paper agent %d error: %s", agent_id, exc)
                time.sleep(3)


def embed_chunks_worker(agent_id: int, chunk_batches: list[list[dict]]):
    global chunks_done, chunks_failed
    client = get_client()
    db = CentralDB()

    for batch in chunk_batches:
        texts = [r["chunk_text"][:2000] for r in batch]

        try:
            result = client.embeddings.create(model=MODEL, input=texts)
            for r, emb in zip(batch, result.data):
                blob = json.dumps(emb.embedding)
                db._conn.execute(
                    "UPDATE paper_chunks SET embedding = ? WHERE chunk_id = ?",
                    (blob, r["chunk_id"]),
                )
            db._conn.commit()
            with lock:
                chunks_done += len(batch)
        except Exception as exc:
            with lock:
                chunks_failed += len(batch)
            if "429" in str(exc) or "rate" in str(exc).lower():
                time.sleep(15)
            else:
                _log.warning("Chunk agent %d error: %s", agent_id, exc)
                time.sleep(3)


def progress_reporter(total_papers: int, total_chunks: int):
    while True:
        time.sleep(60)
        elapsed = time.time() - start_time
        mins = elapsed / 60
        with lock:
            pd, cd = papers_done, chunks_done
            pf, cf = papers_failed, chunks_failed
        rate_p = pd / mins if mins > 0 else 0
        rate_c = cd / mins if mins > 0 else 0
        _log.info(
            "PROGRESS [%.0fm] | Papers: %d/%d (%.0f/min) | Chunks: %d/%d (%.0f/min) | Failed: %d+%d",
            mins, pd, total_papers, rate_p, cd, total_chunks, rate_c, pf, cf,
        )
        if pd + pf >= total_papers and cd + cf >= total_chunks:
            break


def main():
    global start_time

    db = CentralDB()

    # ── Phase 1: Chunk ──
    _log.info("=" * 60)
    _log.info("PHASE 1: Chunking papers with full text")
    _log.info("=" * 60)
    new_chunks = chunk_papers(db)

    # ── Phase 2: Embed ──
    _log.info("=" * 60)
    _log.info("PHASE 2: Parallel embedding (10 agents)")
    _log.info("=" * 60)

    # Get unembedded papers
    paper_rows = db._conn.execute(
        "SELECT paper_id, title, abstract FROM papers WHERE embedding IS NULL"
    ).fetchall()
    paper_rows = [dict(r) for r in paper_rows]
    _log.info("Papers to embed: %d", len(paper_rows))

    # Get unembedded chunks
    chunk_rows = db._conn.execute(
        "SELECT chunk_id, chunk_text FROM paper_chunks WHERE embedding IS NULL"
    ).fetchall()
    chunk_rows = [dict(r) for r in chunk_rows]
    _log.info("Chunks to embed: %d", len(chunk_rows))

    if not paper_rows and not chunk_rows:
        _log.info("Nothing to embed — all done!")
        return

    # Batch and distribute
    def make_batches(rows, batch_size):
        return [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)]

    def distribute(batches, n_agents):
        agents = [[] for _ in range(n_agents)]
        for i, b in enumerate(batches):
            agents[i % n_agents].append(b)
        return agents

    paper_batches = make_batches(paper_rows, BATCH_SIZE)
    chunk_batches = make_batches(chunk_rows, BATCH_SIZE)

    paper_agent_work = distribute(paper_batches, NUM_PAPER_AGENTS)
    chunk_agent_work = distribute(chunk_batches, NUM_CHUNK_AGENTS)

    _log.info(
        "Launching %d paper agents (%d batches) + %d chunk agents (%d batches)",
        NUM_PAPER_AGENTS, len(paper_batches),
        NUM_CHUNK_AGENTS, len(chunk_batches),
    )

    start_time = time.time()
    threads = []

    # Progress reporter
    t = threading.Thread(
        target=progress_reporter, args=(len(paper_rows), len(chunk_rows)),
        daemon=True, name="progress",
    )
    t.start()

    # Paper agents
    for i in range(NUM_PAPER_AGENTS):
        if paper_agent_work[i]:
            t = threading.Thread(
                target=embed_papers_worker, args=(i, paper_agent_work[i]),
                name=f"paper-agent-{i}",
            )
            t.start()
            threads.append(t)

    # Chunk agents
    for i in range(NUM_CHUNK_AGENTS):
        if chunk_agent_work[i]:
            t = threading.Thread(
                target=embed_chunks_worker, args=(i, chunk_agent_work[i]),
                name=f"chunk-agent-{i}",
            )
            t.start()
            threads.append(t)

    for t in threads:
        t.join()

    elapsed = time.time() - start_time
    _log.info("=" * 60)
    _log.info(
        "DONE in %.1f min | Papers: %d/%d | Chunks: %d/%d | Failed: %d+%d",
        elapsed / 60, papers_done, len(paper_rows), chunks_done, len(chunk_rows),
        papers_failed, chunks_failed,
    )


if __name__ == "__main__":
    main()
