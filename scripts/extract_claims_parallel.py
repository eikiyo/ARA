# Location: scripts/extract_claims_parallel.py
# Purpose: Parallel claim extraction from papers with DOI but no claims (15 agents via OpenRouter)
# Functions: extract_worker, main
# Calls: ara/central_db.py, openai (OpenRouter)
# Imports: threading, time, json, logging

"""
Parallel Claim Extraction — 15 Gemini Flash Lite agents via OpenRouter
=======================================================================
Extracts structured claims from papers that have DOI but no claims yet.
Uses abstract + full text (if available) for extraction.

Usage:
    python scripts/extract_claims_parallel.py
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_log = logging.getLogger("claim_extract")

NUM_AGENTS = 15
MODEL = "google/gemini-2.0-flash-lite-001"

EXTRACTION_PROMPT = """You are an academic research claim extractor. Extract 3-8 structured claims from this paper.

For each claim, provide a JSON object with these fields:
- "claim_text": The key finding, argument, or conclusion (1-2 sentences, specific)
- "claim_type": One of: "finding", "argument", "methodology", "limitation", "gap", "theory"
- "confidence": 0.0-1.0 (how confident the paper is about this claim)
- "section": Which section this comes from (e.g., "results", "discussion", "abstract")
- "sample_size": If mentioned (e.g., "N=500 firms")
- "effect_size": If mentioned (e.g., "β=0.23", "r²=0.45", "23% increase")
- "p_value": If mentioned (e.g., "p<0.001")
- "study_design": If identifiable (e.g., "panel regression", "case study", "survey", "conceptual")
- "population": Who/what was studied (e.g., "fintech startups in ASEAN")
- "country": Geographic scope if mentioned
- "year_range": Time period if mentioned

Output ONLY a JSON array of claim objects. No markdown, no explanation.

Paper title: {title}
Authors: {authors}
Year: {year}
DOI: {doi}

{content}"""

# Shared counters
lock = threading.Lock()
papers_done = 0
claims_stored = 0
papers_failed = 0
start_time = 0.0


def get_client():
    from openai import OpenAI
    creds = json.load(open(Path.home() / ".ara" / "credentials.json"))
    key = creds.get("openrouter_api_key", "")
    if not key:
        raise RuntimeError("No OpenRouter API key")
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)


def extract_worker(agent_id: int, papers: list[dict]):
    global papers_done, claims_stored, papers_failed
    client = get_client()
    db = CentralDB()

    for paper in papers:
        title = paper.get("title") or ""
        authors = paper.get("authors") or ""
        year = paper.get("year") or ""
        doi = paper.get("doi") or ""
        abstract = paper.get("abstract") or ""
        full_text = paper.get("full_text") or ""

        # Build content: prefer full text, fall back to abstract
        if full_text and len(full_text) > 500:
            content = f"Abstract: {abstract}\n\nFull text (first 8000 chars):\n{full_text[:8000]}"
        elif abstract:
            content = f"Abstract: {abstract}"
        else:
            # Skip papers with no content
            with lock:
                papers_failed += 1
            continue

        prompt = EXTRACTION_PROMPT.format(
            title=title, authors=authors, year=year, doi=doi, content=content,
        )

        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=4000,
            )
            text = response.choices[0].message.content or ""

            # Parse JSON — handle markdown wrapping
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            claims = json.loads(text)
            if not isinstance(claims, list):
                claims = [claims]

            # Store to central DB
            central_claims = []
            for c in claims:
                if not c.get("claim_text"):
                    continue
                central_claims.append({
                    "paper_doi": doi,
                    "paper_title": title,
                    "claim_text": c["claim_text"],
                    "claim_type": c.get("claim_type", "finding"),
                    "confidence": c.get("confidence", 0.5),
                    "supporting_quotes": json.dumps(c.get("supporting_quotes", [])) if isinstance(c.get("supporting_quotes"), list) else c.get("supporting_quotes", ""),
                    "section": c.get("section", ""),
                    "sample_size": c.get("sample_size", ""),
                    "effect_size": c.get("effect_size", ""),
                    "p_value": c.get("p_value", ""),
                    "confidence_interval": c.get("confidence_interval", ""),
                    "study_design": c.get("study_design", ""),
                    "population": c.get("population", ""),
                    "country": c.get("country", ""),
                    "year_range": c.get("year_range", ""),
                })

            if central_claims:
                result = db.store_claims(central_claims)
                with lock:
                    claims_stored += result.get("stored", 0)
                    papers_done += 1
            else:
                with lock:
                    papers_done += 1

        except json.JSONDecodeError:
            with lock:
                papers_failed += 1
        except Exception as exc:
            with lock:
                papers_failed += 1
            if "429" in str(exc) or "rate" in str(exc).lower():
                time.sleep(10)
            else:
                _log.warning("Agent %d error on '%s': %s", agent_id, title[:50], exc)
                time.sleep(2)


def progress_reporter(total: int):
    while True:
        time.sleep(60)
        elapsed = time.time() - start_time
        mins = elapsed / 60
        with lock:
            pd, cs, pf = papers_done, claims_stored, papers_failed
        rate = pd / mins if mins > 0 else 0
        remaining = total - pd - pf
        eta = remaining / rate if rate > 0 else 0
        _log.info(
            "PROGRESS [%.0fm] | Papers: %d/%d (%.0f/min) | Claims stored: %d | Failed: %d | ETA: %.0fm",
            mins, pd, total, rate, cs, pf, eta,
        )
        if pd + pf >= total:
            break


def main():
    global start_time
    db = CentralDB()

    # Get papers with DOI but no claims
    papers = db._conn.execute("""
        SELECT p.paper_id, p.title, p.authors, p.year, p.doi, p.abstract, p.full_text
        FROM papers p
        WHERE p.doi IS NOT NULL AND p.doi != ''
        AND p.doi NOT IN (
            SELECT DISTINCT paper_doi FROM claims
            WHERE paper_doi IS NOT NULL AND paper_doi != ''
        )
        ORDER BY p.paper_id
    """).fetchall()
    papers = [dict(r) for r in papers]

    _log.info("Papers with DOI but no claims: %d", len(papers))

    if not papers:
        _log.info("Nothing to extract — all done!")
        return

    # Count how many have full text
    with_ft = sum(1 for p in papers if p.get("full_text") and len(p["full_text"]) > 500)
    abstract_only = len(papers) - with_ft
    _log.info("  With full text: %d | Abstract only: %d", with_ft, abstract_only)

    # Distribute round-robin to agents
    agent_work = [[] for _ in range(NUM_AGENTS)]
    for i, p in enumerate(papers):
        agent_work[i % NUM_AGENTS].append(p)

    _log.info("Launching %d agents (~%d papers each)", NUM_AGENTS, len(papers) // NUM_AGENTS)

    start_time = time.time()
    threads = []

    # Progress reporter
    t = threading.Thread(target=progress_reporter, args=(len(papers),), daemon=True)
    t.start()

    for i in range(NUM_AGENTS):
        if agent_work[i]:
            t = threading.Thread(
                target=extract_worker, args=(i, agent_work[i]),
                name=f"extract-agent-{i}",
            )
            t.start()
            threads.append(t)

    for t in threads:
        t.join()

    elapsed = time.time() - start_time
    _log.info("=" * 60)
    _log.info(
        "DONE in %.1f min | Papers: %d/%d | Claims stored: %d | Failed: %d",
        elapsed / 60, papers_done, len(papers), claims_stored, papers_failed,
    )


if __name__ == "__main__":
    main()
