# Location: scripts/extract_claims_cerebras.py
# Purpose: Bulk claim extraction using Gemini Flash Lite (1s/paper, free)
# Functions: Extract claims from all papers with full text in central DB
# Calls: google.genai, ara/central_db.py
# Imports: json, time, logging

"""
Bulk Claim Extraction via Gemini Flash Lite
============================================
Extracts claims from ALL papers with full text that haven't been processed yet.
Gemini Flash Lite: ~1s/paper, free tier, good quality.

Usage:
    python scripts/extract_claims_cerebras.py
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
_log = logging.getLogger("bulk_extract")

_PROMPT = """Extract ALL findings, theories, methods, limitations, and gaps from this paper.
Return a JSON array of claim objects. Each claim must have:
- claim_text: The core claim (1-3 sentences)
- claim_type: "finding", "theory", "method", "limitation", or "gap"
- confidence: a float 0.0-1.0
- section: Which part of the paper
- study_design: If applicable
- sample_size: If mentioned
- country: If mentioned

Extract EVERYTHING. Return ONLY a JSON array, no markdown fences.

Title: {title}
Text: {text}"""


def _parse_confidence(raw):
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        m = {"high": 0.9, "very high": 0.95, "medium": 0.6, "moderate": 0.6, "low": 0.3}
        if raw.lower() in m:
            return m[raw.lower()]
        try:
            return float(raw)
        except ValueError:
            pass
    return 0.5


def _parse_claims(text: str) -> list:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    for attempt in [text, None]:
        if attempt is None:
            s, e = text.find("["), text.rfind("]")
            if s < 0 or e <= s:
                s, e = text.find("{"), text.rfind("}")
                if s < 0 or e <= s:
                    return []
            attempt = text[s:e + 1]
        try:
            obj = json.loads(attempt)
            if isinstance(obj, list):
                return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    if isinstance(v, list):
                        return v
                if obj.get("claim_text"):
                    return [obj]
            return []
        except json.JSONDecodeError:
            continue
    return []


def main():
    gkey = load_api_key()
    if not gkey:
        _log.error("No Google API key found")
        sys.exit(1)

    from google import genai
    client = genai.Client(api_key=gkey)

    central_db = CentralDB()
    s = central_db.stats()
    _log.info("DB: %d papers, %d texts, %d claims", s["total_papers"], s["with_fulltext"], s["total_claims"])

    rows = central_db._conn.execute(
        "SELECT p.paper_id, p.title, p.abstract, p.doi, p.full_text "
        "FROM papers p WHERE p.full_text IS NOT NULL AND p.full_text != '' "
        "ORDER BY p.citation_count DESC"
    ).fetchall()

    papers = []
    for row in rows:
        if not central_db.is_paper_fully_extracted(row["title"] or ""):
            papers.append(dict(row))

    if not papers:
        _log.info("All papers already extracted")
        return

    _log.info("Extracting %d papers using Gemini Flash Lite (~1s/paper)", len(papers))

    total_claims = 0
    done = 0
    failed = 0
    t0 = time.time()

    for i, paper in enumerate(papers):
        ft = paper["full_text"] or ""
        ab = paper["abstract"] or ""
        text = ft[:4000] if len(ft) > 200 else ab
        if not text or len(text) < 100:
            continue

        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=_PROMPT.format(title=paper["title"], text=text),
                config={"temperature": 0.1, "max_output_tokens": 4096},
            )
            content = resp.text or ""
            claims = _parse_claims(content)

            if not claims:
                failed += 1
            else:
                central_claims = []
                for c in claims:
                    if not isinstance(c, dict) or not c.get("claim_text"):
                        continue
                    central_claims.append({
                        "paper_title": paper["title"],
                        "paper_doi": paper.get("doi", ""),
                        "claim_text": c["claim_text"],
                        "claim_type": c.get("claim_type", "finding"),
                        "confidence": _parse_confidence(c.get("confidence", 0.5)),
                        "supporting_quotes": json.dumps(c.get("supporting_quotes", [])),
                        "section": c.get("section", ""),
                        "sample_size": str(c.get("sample_size", "")),
                        "effect_size": str(c.get("effect_size", "")),
                        "p_value": str(c.get("p_value", "")),
                        "confidence_interval": str(c.get("confidence_interval", "")),
                        "study_design": str(c.get("study_design", "")),
                        "population": str(c.get("population", "")),
                        "country": str(c.get("country", "")),
                        "year_range": str(c.get("year_range", "")),
                    })
                if central_claims:
                    res = central_db.store_claims(central_claims, session_topic="prewarm_gemini")
                    stored = res.get("stored", 0)
                    total_claims += stored
                    central_db.mark_paper_fully_extracted(paper["title"])
                    done += 1

        except Exception as exc:
            failed += 1
            if "429" in str(exc) or "rate" in str(exc).lower() or "quota" in str(exc).lower():
                _log.warning("Rate limited — waiting 30s")
                time.sleep(30)
            else:
                _log.warning("[%d] Error: %s", i + 1, exc)

        # Log every 25 papers
        if (i + 1) % 25 == 0:
            elapsed = time.time() - t0
            rate = done / elapsed * 60 if elapsed > 0 else 0
            _log.info("[%d/%d] %d claims from %d papers (%.0f papers/min, %d failed)",
                      i + 1, len(papers), total_claims, done, rate, failed)

    elapsed = time.time() - t0
    _log.info("=" * 60)
    _log.info("DONE in %.1f min: %d claims from %d papers (%d failed)", elapsed / 60, total_claims, done, failed)

    # Embed claims
    _log.info("Embedding claims...")
    unembedded = central_db._conn.execute(
        "SELECT claim_id, claim_text FROM claims WHERE embedding IS NULL"
    ).fetchall()
    _log.info("Embedding %d claims", len(unembedded))
    embedded = 0
    for j in range(0, len(unembedded), 50):
        batch = unembedded[j:j + 50]
        texts = [r["claim_text"][:500] for r in batch]
        try:
            result = client.models.embed_content(model="gemini-embedding-001", contents=texts)
            if result.embeddings:
                for row, emb_obj in zip(batch, result.embeddings):
                    central_db.store_claim_embedding(row["claim_id"], emb_obj.values)
                    embedded += 1
        except Exception as exc:
            _log.warning("Embed failed: %s", exc)
            time.sleep(2)
        if (j + 50) % 500 == 0:
            _log.info("  Embedded %d/%d", embedded, len(unembedded))
    _log.info("Embedded %d claims", embedded)

    after = central_db.stats()
    _log.info("DB final: %d papers, %d texts, %d claims", after["total_papers"], after["with_fulltext"], after["total_claims"])


if __name__ == "__main__":
    main()
