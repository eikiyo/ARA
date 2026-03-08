# Location: scripts/bulk_verify.py
# Purpose: Bulk verify all papers in central DB using batch APIs — maximum throughput
# Functions: main, openalex_batch, s2_batch, crossref_batch, europepmc_batch
# Calls: central_db, httpx
# Imports: sqlite3, httpx, concurrent.futures, threading, time, os, argparse

"""
Bulk verify all papers in central DB using batch API endpoints.

APIs used (in priority order for citations):
  1. OpenAlex    — batch 50 DOIs/call, 60 RPM, returns citations + retraction
  2. S2          — batch 500 DOIs/call, 10 RPM (100 w/ key), returns citations
  3. CrossRef    — 1 DOI/call, 30 RPM, returns retraction status
  4. Europe PMC  — 1 DOI/call, 20 RPM, returns retraction + citations
  5. OpenCitations — batch via API, 30 RPM, returns citation counts

Strategy:
  Phase 1: OpenAlex bulk (covers ~80% of DOIs for citations + retraction in minutes)
  Phase 2: S2 bulk for DOIs OpenAlex missed
  Phase 3: CrossRef parallel for retraction on remaining unchecked
  Phase 4: Write everything to central DB

Usage:
    python scripts/bulk_verify.py           # verify all
    python scripts/bulk_verify.py --dry-run  # just show counts
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("bulk_verify")

CENTRAL_DB_PATH = Path.home() / ".ara" / "central.db"

_client: httpx.Client | None = None
_client_lock = threading.Lock()


def get_client() -> httpx.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=30,
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
                )
    return _client


# ── Phase 1: OpenAlex bulk (50 DOIs per call) ───────────────────────

def openalex_batch(dois: list[str], batch_size: int = 50) -> dict[str, dict]:
    """Query OpenAlex in batches of 50 DOIs. Returns {doi: {citation_count, retracted}}."""
    results: dict[str, dict] = {}
    total_batches = (len(dois) + batch_size - 1) // batch_size

    for b in range(0, len(dois), batch_size):
        batch = dois[b:b + batch_size]
        batch_num = b // batch_size + 1

        # OpenAlex wants full DOI URLs with pipe separator
        doi_filter = "|".join(
            d if d.startswith("https://doi.org/") else f"https://doi.org/{d}"
            for d in batch
        )

        try:
            resp = get_client().get(
                "https://api.openalex.org/works",
                params={
                    "filter": f"doi:{doi_filter}",
                    "select": "doi,cited_by_count,is_retracted",
                    "per_page": "200",
                    "mailto": "ara-research@example.com",
                },
                timeout=30,
            )

            if resp.status_code == 200:
                for r in resp.json().get("results", []):
                    raw_doi = r.get("doi", "")
                    # OpenAlex returns full URL doi
                    doi = raw_doi.replace("https://doi.org/", "").strip()
                    if doi:
                        results[doi] = {
                            "citation_count": r.get("cited_by_count", 0),
                            "retracted": r.get("is_retracted", False),
                        }
            elif resp.status_code == 429:
                _log.warning("  OpenAlex 429 at batch %d — backing off 10s", batch_num)
                time.sleep(10)
                # Retry once
                resp = get_client().get(
                    "https://api.openalex.org/works",
                    params={
                        "filter": f"doi:{doi_filter}",
                        "select": "doi,cited_by_count,is_retracted",
                        "per_page": "200",
                        "mailto": "ara-research@example.com",
                    },
                    timeout=30,
                )
                if resp.status_code == 200:
                    for r in resp.json().get("results", []):
                        raw_doi = r.get("doi", "")
                        doi = raw_doi.replace("https://doi.org/", "").strip()
                        if doi:
                            results[doi] = {
                                "citation_count": r.get("cited_by_count", 0),
                                "retracted": r.get("is_retracted", False),
                            }
        except Exception as e:
            _log.warning("  OpenAlex batch %d failed: %s", batch_num, e)

        if batch_num % 10 == 0 or batch_num == total_batches:
            _log.info("  OpenAlex: %d/%d batches done (%d DOIs resolved)",
                      batch_num, total_batches, len(results))

        # ~1 req/s to stay well under limits
        time.sleep(1.0)

    return results


# ── Phase 2: S2 bulk (500 DOIs per call) ─────────────────────────────

def s2_batch(dois: list[str], api_key: str | None, batch_size: int = 500) -> dict[str, dict]:
    """Query Semantic Scholar batch endpoint. Returns {doi: {citation_count}}."""
    results: dict[str, dict] = {}
    headers = {"x-api-key": api_key} if api_key else {}
    interval = 6.0 if not api_key else 1.0  # 10 RPM vs 100 RPM
    total_batches = (len(dois) + batch_size - 1) // batch_size

    for b in range(0, len(dois), batch_size):
        batch = dois[b:b + batch_size]
        batch_num = b // batch_size + 1
        ids = [f"DOI:{d}" for d in batch]

        try:
            resp = get_client().post(
                "https://api.semanticscholar.org/graph/v1/paper/batch",
                json={"ids": ids},
                headers=headers,
                params={"fields": "citationCount,externalIds"},
                timeout=30,
            )

            if resp.status_code == 200:
                for r in resp.json():
                    if r and r.get("externalIds"):
                        doi = r["externalIds"].get("DOI", "")
                        if doi:
                            results[doi] = {"citation_count": r.get("citationCount", 0)}
            elif resp.status_code == 429:
                _log.warning("  S2 429 at batch %d — backing off 30s", batch_num)
                time.sleep(30)
                # Retry
                resp = get_client().post(
                    "https://api.semanticscholar.org/graph/v1/paper/batch",
                    json={"ids": ids},
                    headers=headers,
                    params={"fields": "citationCount,externalIds"},
                    timeout=30,
                )
                if resp.status_code == 200:
                    for r in resp.json():
                        if r and r.get("externalIds"):
                            doi = r["externalIds"].get("DOI", "")
                            if doi:
                                results[doi] = {"citation_count": r.get("citationCount", 0)}
            else:
                _log.warning("  S2 batch %d: status %d", batch_num, resp.status_code)
        except Exception as e:
            _log.warning("  S2 batch %d failed: %s", batch_num, e)

        _log.info("  S2: batch %d/%d done (%d DOIs resolved)", batch_num, total_batches, len(results))
        time.sleep(interval)

    return results


# ── Phase 3: CrossRef parallel (retraction check, 1 DOI/call) ───────

def crossref_single(doi: str) -> tuple[str, dict]:
    """Check single DOI retraction via CrossRef."""
    try:
        resp = get_client().get(
            f"https://api.crossref.org/works/{doi}",
            params={"mailto": "ara-research@example.com"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json().get("message", {})
            update_to = data.get("update-to", [])
            retracted = any(u.get("type") == "retraction" for u in update_to)
            return doi, {"retracted": retracted, "ok": True}
        if resp.status_code == 429:
            return doi, {"ok": False, "retry": True}
        return doi, {"retracted": False, "ok": True}
    except Exception:
        return doi, {"ok": False}


def crossref_parallel(dois: list[str], workers: int = 10) -> dict[str, dict]:
    """Check retraction status via CrossRef in parallel."""
    results: dict[str, dict] = {}
    interval = 2.0  # ~30 RPM
    lock = threading.Lock()
    last_time = [0.0]

    def _throttled(doi: str) -> tuple[str, dict]:
        with lock:
            now = time.monotonic()
            wait = interval - (now - last_time[0])
            if wait > 0:
                time.sleep(wait)
            last_time[0] = time.monotonic()
        return crossref_single(doi)

    retries: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_throttled, doi): doi for doi in dois}
        for i, fut in enumerate(as_completed(futures)):
            doi, result = fut.result()
            if result.get("retry"):
                retries.append(doi)
            elif result.get("ok"):
                results[doi] = result
            if (i + 1) % 100 == 0:
                _log.info("  CrossRef: %d/%d done", i + 1, len(dois))

    if retries:
        _log.info("  CrossRef: retrying %d rate-limited DOIs...", len(retries))
        time.sleep(10)
        for doi in retries:
            time.sleep(2.0)
            _, result = crossref_single(doi)
            if result.get("ok"):
                results[doi] = result

    return results


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Bulk verify all papers in central DB (batch APIs)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-s2", action="store_true", help="Skip Semantic Scholar")
    parser.add_argument("--skip-crossref", action="store_true", help="Skip CrossRef retraction check")
    parser.add_argument("--crossref-workers", type=int, default=10)
    args = parser.parse_args()

    if not CENTRAL_DB_PATH.exists():
        _log.error("Central DB not found: %s", CENTRAL_DB_PATH)
        sys.exit(1)

    db = sqlite3.connect(str(CENTRAL_DB_PATH))
    db.row_factory = sqlite3.Row

    all_rows = db.execute(
        "SELECT DISTINCT doi FROM papers WHERE doi IS NOT NULL AND doi != '' "
        "AND LOWER(TRIM(doi)) NOT IN (SELECT LOWER(doi) FROM doi_validations)"
    ).fetchall()
    dois = [r["doi"].strip() for r in all_rows if r["doi"].strip()]
    existing = db.execute("SELECT COUNT(*) as c FROM doi_validations").fetchone()["c"]

    _log.info("Central DB: %d DOIs total, %d already verified, %d to verify",
              len(dois) + existing, existing, len(dois))

    if args.dry_run or not dois:
        db.close()
        return

    s2_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    start = time.monotonic()

    # ── Phase 1: OpenAlex bulk ──
    _log.info("=" * 60)
    _log.info("PHASE 1: OpenAlex bulk (50 DOIs/call, ~60 RPM)")
    _log.info("=" * 60)
    oa_results = openalex_batch(dois)
    _log.info("OpenAlex: resolved %d/%d DOIs (%.0f%%)",
              len(oa_results), len(dois), len(oa_results) / len(dois) * 100)

    # ── Phase 2: S2 bulk for misses ──
    if not args.skip_s2:
        missed_dois = [d for d in dois if d not in oa_results]
        if missed_dois:
            _log.info("=" * 60)
            _log.info("PHASE 2: Semantic Scholar bulk (%d DOIs missed by OpenAlex)", len(missed_dois))
            _log.info("=" * 60)
            s2_results = s2_batch(missed_dois, s2_key)
            _log.info("S2: resolved %d/%d remaining DOIs", len(s2_results), len(missed_dois))
        else:
            s2_results = {}
            _log.info("PHASE 2: Skipped — OpenAlex covered all DOIs")
    else:
        s2_results = {}

    # ── Phase 3: CrossRef retraction check ──
    if not args.skip_crossref:
        # Only check DOIs not already retraction-checked by OpenAlex
        unchecked = [d for d in dois if d not in oa_results]
        if unchecked:
            _log.info("=" * 60)
            _log.info("PHASE 3: CrossRef retraction check (%d DOIs, %d workers)",
                      len(unchecked), args.crossref_workers)
            _log.info("=" * 60)
            cr_results = crossref_parallel(unchecked, args.crossref_workers)
            _log.info("CrossRef: checked %d/%d DOIs", len(cr_results), len(unchecked))
        else:
            cr_results = {}
            _log.info("PHASE 3: Skipped — all DOIs checked by OpenAlex")
    else:
        cr_results = {}

    # ── Phase 4: Merge & write to DB ──
    _log.info("=" * 60)
    _log.info("PHASE 4: Writing to central DB")
    _log.info("=" * 60)

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    retracted_count = 0

    for doi in dois:
        oa = oa_results.get(doi, {})
        s2 = s2_results.get(doi, {})
        cr = cr_results.get(doi, {})

        # Retraction: any source flagging = retracted
        retracted = oa.get("retracted", False) or cr.get("retracted", False)
        if retracted:
            retracted_count += 1
            _log.warning("RETRACTED: %s", doi)

        # Citation count: take highest from any source
        cc = max(oa.get("citation_count", 0), s2.get("citation_count", 0))

        try:
            db.execute(
                "INSERT OR IGNORE INTO doi_validations "
                "(doi, retracted, retraction_permanent, citation_count, citation_count_updated_at, created_at, updated_at) "
                "VALUES (?, ?, 1, ?, ?, ?, ?)",
                (doi.lower(), int(retracted), cc, now, now, now),
            )
            inserted += 1
        except Exception as e:
            _log.warning("DB insert failed for %s: %s", doi, e)

        if inserted % 500 == 0:
            db.commit()

    db.commit()

    elapsed = time.monotonic() - start
    final = db.execute("SELECT COUNT(*) as c FROM doi_validations").fetchone()["c"]
    _log.info("=" * 60)
    _log.info("DONE in %.1f min | +%d validations (total: %d) | %d retracted",
              elapsed / 60, inserted, final, retracted_count)
    _log.info("=" * 60)
    db.close()


if __name__ == "__main__":
    main()
