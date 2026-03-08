# Location: scripts/embed_claims_parallel.py
# Purpose: Parallel embedding of unembedded claims via OpenRouter (10 agents)
# Functions: embed_worker, main
# Calls: ara/central_db.py, openai (OpenRouter)
# Imports: threading, time, json, logging

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_log = logging.getLogger("embed_claims")

NUM_AGENTS = 10
BATCH_SIZE = 50
MODEL = "google/gemini-embedding-001"

lock = threading.Lock()
done = 0
failed = 0
start_time = 0.0


def get_client():
    from openai import OpenAI
    creds = json.load(open(Path.home() / ".ara" / "credentials.json"))
    key = creds.get("openrouter_api_key", "")
    if not key:
        raise RuntimeError("No OpenRouter API key")
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)


def embed_worker(agent_id: int, batches: list[list[dict]]):
    global done, failed
    client = get_client()
    db = CentralDB()

    for batch in batches:
        texts = [r["claim_text"][:2000] for r in batch]
        try:
            result = client.embeddings.create(model=MODEL, input=texts)
            for r, emb in zip(batch, result.data):
                blob = json.dumps(emb.embedding)
                db._conn.execute(
                    "UPDATE claims SET embedding = ? WHERE claim_id = ?",
                    (blob, r["claim_id"]),
                )
            db._conn.commit()
            with lock:
                done += len(batch)
        except Exception as exc:
            with lock:
                failed += len(batch)
            if "429" in str(exc) or "rate" in str(exc).lower():
                time.sleep(15)
            else:
                _log.warning("Agent %d error: %s", agent_id, exc)
                time.sleep(3)


def progress_reporter(total: int):
    while True:
        time.sleep(60)
        elapsed = time.time() - start_time
        mins = elapsed / 60
        with lock:
            d, f = done, failed
        rate = d / mins if mins > 0 else 0
        remaining = total - d - f
        eta = remaining / rate if rate > 0 else 0
        _log.info(
            "PROGRESS [%.0fm] | Claims: %d/%d (%.0f/min) | Failed: %d | ETA: %.0fm",
            mins, d, total, rate, f, eta,
        )
        if d + f >= total:
            break


def main():
    global start_time
    db = CentralDB()

    rows = db._conn.execute(
        "SELECT claim_id, claim_text FROM claims WHERE embedding IS NULL"
    ).fetchall()
    rows = [dict(r) for r in rows]
    _log.info("Claims to embed: %d", len(rows))

    if not rows:
        _log.info("All claims embedded!")
        return

    batches = [rows[i:i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
    agent_work = [[] for _ in range(NUM_AGENTS)]
    for i, b in enumerate(batches):
        agent_work[i % NUM_AGENTS].append(b)

    _log.info("Launching %d agents (%d batches)", NUM_AGENTS, len(batches))

    start_time = time.time()
    threads = []

    t = threading.Thread(target=progress_reporter, args=(len(rows),), daemon=True)
    t.start()

    for i in range(NUM_AGENTS):
        if agent_work[i]:
            t = threading.Thread(target=embed_worker, args=(i, agent_work[i]), name=f"emb-claim-{i}")
            t.start()
            threads.append(t)

    for t in threads:
        t.join()

    elapsed = time.time() - start_time
    _log.info("=" * 60)
    _log.info("DONE in %.1f min | Claims: %d/%d | Failed: %d", elapsed / 60, done, len(rows), failed)


if __name__ == "__main__":
    main()
