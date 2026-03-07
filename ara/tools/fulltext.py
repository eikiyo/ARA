# Location: ara/tools/fulltext.py
# Purpose: Batch full-text fetching from multiple sources (Unpaywall, CORE, S2, OpenAlex, Europe PMC, PubMed Central)
# Functions: batch_fetch_fulltext
# Calls: http.py, db.py
# Imports: json, logging, os, re, time, threading, concurrent.futures

from __future__ import annotations

import json
import logging
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .http import rate_limited_get
from .papers import _extract_text_from_pdf

_log = logging.getLogger(__name__)
_MAX_FULLTEXT_CHARS = 25000
_MAX_DOWNLOAD_BYTES = 10_000_000


# ── Source: Unpaywall (batch via individual DOI lookups, 30 rpm) ──────

_UNPAYWALL_EMAILS = ["syedmosayebalam@gmail.com", "eikiyo.netflix@gmail.com"]

def _fetch_unpaywall_batch(dois: list[str], email: str) -> dict[str, str]:
    """Fetch full text URLs from Unpaywall for a batch of DOIs. Returns {doi: text}.
    Rotates to backup email on 429 rate limits."""
    results: dict[str, str] = {}
    current_email = email
    _log.info("Unpaywall: starting %d DOI lookups", len(dois))
    for idx, doi in enumerate(dois):
        if idx % 20 == 0 and idx > 0:
            _log.info("Unpaywall: progress %d/%d, found %d so far", idx, len(dois), len(results))
        for attempt in range(2):  # Try up to 2 emails
            try:
                resp = rate_limited_get(
                    f"https://api.unpaywall.org/v2/{doi}",
                    params={"email": current_email},
                    timeout=15,
                )
                if resp.status_code == 429:
                    # Rotate to next email
                    idx = _UNPAYWALL_EMAILS.index(current_email) if current_email in _UNPAYWALL_EMAILS else -1
                    next_idx = (idx + 1) % len(_UNPAYWALL_EMAILS)
                    if _UNPAYWALL_EMAILS[next_idx] != current_email:
                        _log.info("Unpaywall 429 — rotating email to %s", _UNPAYWALL_EMAILS[next_idx])
                        current_email = _UNPAYWALL_EMAILS[next_idx]
                        time.sleep(2)
                        continue
                    break
                if resp.status_code == 422:
                    _log.warning("Unpaywall 422 for email %s — rotating", current_email)
                    idx = _UNPAYWALL_EMAILS.index(current_email) if current_email in _UNPAYWALL_EMAILS else -1
                    next_idx = (idx + 1) % len(_UNPAYWALL_EMAILS)
                    current_email = _UNPAYWALL_EMAILS[next_idx]
                    continue
                if resp.status_code != 200:
                    break
                data = resp.json()
                best_oa = data.get("best_oa_location") or {}
                url = best_oa.get("url_for_pdf") or best_oa.get("url")
                if url:
                    text = _download_and_extract(url)
                    if text:
                        results[doi] = text
                break
            except Exception as exc:
                _log.debug("Unpaywall failed for %s: %s", doi, exc)
                break
    return results


# ── Source: CORE API v3 (batch via OR-query, 10 DOIs per call) ────────

def _fetch_core_batch(dois: list[str], api_key: str) -> dict[str, str]:
    """Fetch full text from CORE API using OR-query batching. Retries on 429."""
    results: dict[str, str] = {}
    batch_size = 20  # CORE supports OR queries with 20 DOIs
    # Cap at 100 DOIs to avoid being the bottleneck
    dois = dois[:100]
    total_batches = (len(dois) + batch_size - 1) // batch_size
    _log.info("CORE: starting %d batches for %d DOIs (capped)", total_batches, len(dois))

    for i in range(0, len(dois), batch_size):
        batch_num = i // batch_size + 1
        batch = dois[i:i + batch_size]
        query = " OR ".join(f'doi:"{d}"' for d in batch)
        _log.info("CORE batch %d/%d: querying %d DOIs", batch_num, total_batches, len(batch))
        for attempt in range(3):
            try:
                resp = rate_limited_get(
                    "https://api.core.ac.uk/v3/search/works",
                    headers={"Authorization": f"Bearer {api_key}"},
                    params={"q": query, "limit": batch_size},
                    timeout=15,
                )
                if resp.status_code == 429:
                    wait = 5 * (attempt + 1)
                    _log.info("CORE 429 — retrying in %ds (attempt %d/3)", wait, attempt + 1)
                    time.sleep(wait)
                    continue
                if resp.status_code != 200:
                    _log.info("CORE batch %d returned %d — skipping", batch_num, resp.status_code)
                    break
                data = resp.json()
                for item in data.get("results", []):
                    item_doi = item.get("doi", "")
                    if not item_doi:
                        continue
                    item_doi = _normalize_doi(item_doi)
                    full_text = item.get("fullText", "")
                    if full_text and len(full_text) > 200:
                        results[item_doi] = full_text[:_MAX_FULLTEXT_CHARS]
                    elif item.get("downloadUrl"):
                        text = _download_and_extract(item["downloadUrl"])
                        if text:
                            results[item_doi] = text
                break
            except Exception as exc:
                _log.info("CORE batch %d failed (attempt %d): %s", batch_num, attempt + 1, exc)
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
        time.sleep(1)

    _log.info("CORE: completed — found %d texts", len(results))
    return results


# ── Source: Semantic Scholar (batch API, up to 500 per call) ──────────

def _fetch_s2_batch(dois: list[str]) -> dict[str, str]:
    """Fetch open access PDFs from Semantic Scholar batch API."""
    results: dict[str, str] = {}
    batch_size = 500
    _log.info("S2: starting batch fetch for %d DOIs", len(dois))
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    headers: dict[str, str] = {}
    if api_key:
        headers["x-api-key"] = api_key

    for i in range(0, len(dois), batch_size):
        batch = dois[i:i + batch_size]
        paper_ids = [f"DOI:{d}" for d in batch]
        for attempt in range(3):
            try:
                client = httpx.Client(timeout=60, follow_redirects=True)
                resp = client.post(
                    "https://api.semanticscholar.org/graph/v1/paper/batch",
                    json={"ids": paper_ids},
                    params={"fields": "externalIds,openAccessPdf"},
                    headers=headers,
                )
                client.close()
                if resp.status_code == 429:
                    wait = 5 * (attempt + 1)
                    _log.info("S2 429 — retrying in %ds (attempt %d/3)", wait, attempt + 1)
                    time.sleep(wait)
                    continue
                if resp.status_code != 200:
                    _log.debug("S2 batch returned %d", resp.status_code)
                    break
                for paper in resp.json():
                    if not paper or not isinstance(paper, dict):
                        continue
                    oa_pdf = paper.get("openAccessPdf") or {}
                    pdf_url = oa_pdf.get("url")
                    if not pdf_url:
                        continue
                    ext_ids = paper.get("externalIds") or {}
                    paper_doi = ext_ids.get("DOI", "")
                    if paper_doi:
                        text = _download_and_extract(pdf_url)
                        if text:
                            results[paper_doi.lower()] = text
                break
            except Exception as exc:
                _log.debug("S2 batch failed (attempt %d): %s", attempt + 1, exc)
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
        time.sleep(3)

    return results


# ── Source: OpenAlex (batch via filter, up to 50 per call) ────────────

def _fetch_openalex_batch(dois: list[str]) -> dict[str, str]:
    """Fetch open access URLs from OpenAlex using DOI filter."""
    results: dict[str, str] = {}
    batch_size = 50  # OpenAlex filter supports pipe-separated DOIs

    for i in range(0, len(dois), batch_size):
        batch = dois[i:i + batch_size]
        doi_filter = "|".join(f"https://doi.org/{d}" for d in batch)
        try:
            resp = rate_limited_get(
                "https://api.openalex.org/works",
                params={
                    "filter": f"doi:{doi_filter}",
                    "per_page": batch_size,
                    "select": "doi,open_access,best_oa_location",
                    "mailto": "syedmosayebalam@gmail.com",
                },
                timeout=30,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            for item in data.get("results", []):
                item_doi = item.get("doi", "")
                if item_doi:
                    item_doi = _normalize_doi(item_doi)
                oa = item.get("open_access") or {}
                if not oa.get("is_oa"):
                    continue
                best_loc = item.get("best_oa_location") or {}
                pdf_url = best_loc.get("pdf_url") or best_loc.get("landing_page_url")
                if pdf_url and item_doi:
                    text = _download_and_extract(pdf_url)
                    if text:
                        results[item_doi] = text
        except Exception as exc:
            _log.debug("OpenAlex batch failed: %s", exc)

    return results


# ── Source: Europe PMC (batch via DOI search) ─────────────────────────

def _fetch_epmc_batch(dois: list[str]) -> dict[str, str]:
    """Fetch full text from Europe PMC fulltext XML API."""
    results: dict[str, str] = {}
    batch_size = 20

    for i in range(0, len(dois), batch_size):
        batch = dois[i:i + batch_size]
        query = " OR ".join(f'DOI:"{d}"' for d in batch)
        try:
            resp = rate_limited_get(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                params={"query": query, "pageSize": batch_size, "format": "json", "resultType": "core"},
                timeout=30,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            for item in data.get("resultList", {}).get("result", []):
                item_doi = item.get("doi", "")
                if not item_doi:
                    continue
                pmcid = item.get("pmcid")
                if not pmcid:
                    continue
                # Fetch full text XML from PMC
                ft_text = _fetch_epmc_fulltext(pmcid)
                if ft_text:
                    results[item_doi.lower()] = ft_text
        except Exception as exc:
            _log.debug("EPMC batch failed: %s", exc)

    return results


def _fetch_epmc_fulltext(pmcid: str) -> str | None:
    """Fetch full text XML from Europe PMC for a given PMCID."""
    try:
        resp = rate_limited_get(
            f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML",
            timeout=20,
        )
        if resp.status_code != 200:
            return None
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:_MAX_FULLTEXT_CHARS] if len(text) > 200 else None
    except Exception:
        return None


# ── Source: PubMed Central (PMC OA via NCBI) ──────────────────────────

def _fetch_pmc_batch(dois: list[str]) -> dict[str, str]:
    """Fetch full text from PubMed Central via DOI lookup + efetch."""
    results: dict[str, str] = {}
    ncbi_key = os.getenv("NCBI_API_KEY", "")
    batch_size = 20
    total_batches = (len(dois) + batch_size - 1) // batch_size
    _log.info("PMC: starting %d batches for %d DOIs", total_batches, len(dois))

    for i in range(0, len(dois), batch_size):
        batch_num = i // batch_size + 1
        batch = dois[i:i + batch_size]
        _log.info("PMC batch %d/%d: querying %d DOIs", batch_num, total_batches, len(batch))
        query = " OR ".join(f'{d}[DOI]' for d in batch)
        try:
            params: dict[str, Any] = {
                "db": "pmc", "term": query, "retmax": batch_size, "retmode": "json",
            }
            if ncbi_key:
                params["api_key"] = ncbi_key
            resp = rate_limited_get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params=params,
                timeout=20,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            pmc_ids = data.get("esearchresult", {}).get("idlist", [])
            if not pmc_ids:
                continue

            # Fetch full text XML
            fetch_params: dict[str, Any] = {
                "db": "pmc", "id": ",".join(pmc_ids), "rettype": "xml", "retmode": "xml",
            }
            if ncbi_key:
                fetch_params["api_key"] = ncbi_key
            ft_resp = rate_limited_get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params=fetch_params,
                timeout=30,
            )
            if ft_resp.status_code != 200:
                continue

            # Extract text and match DOIs
            xml_text = ft_resp.text
            # Split by article boundaries
            articles = re.split(r'<article\b', xml_text)
            for article_xml in articles[1:]:  # Skip first empty split
                # Extract DOI from this article
                doi_match = re.search(r'pub-id-type="doi">([^<]+)<', article_xml)
                if not doi_match:
                    continue
                found_doi = doi_match.group(1).lower()
                # Strip XML tags
                text = re.sub(r"<[^>]+>", " ", article_xml)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) > 200:
                    results[found_doi] = text[:_MAX_FULLTEXT_CHARS]
        except Exception as exc:
            _log.debug("PMC batch failed: %s", exc)
        time.sleep(1)

    return results


# ── Shared helpers ────────────────────────────────────────────────────

def _normalize_doi(raw: str) -> str:
    """Normalize DOI to lowercase without URL prefix."""
    doi = raw.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "https://dx.doi.org/"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi


def _download_and_extract(url: str) -> str | None:
    """Download a URL and extract text (PDF or HTML/XML)."""
    try:
        with httpx.stream("GET", url, timeout=15, follow_redirects=True) as resp:
            if resp.status_code != 200:
                return None
            content_type = resp.headers.get("content-type", "")
            chunks = []
            total = 0
            for chunk in resp.iter_bytes(chunk_size=8192):
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_DOWNLOAD_BYTES:
                    break
            raw_bytes = b"".join(chunks)

            if "pdf" in content_type or url.endswith(".pdf"):
                return _extract_text_from_pdf(raw_bytes, _MAX_FULLTEXT_CHARS)
            elif "html" in content_type:
                html = raw_bytes.decode("utf-8", errors="replace")
                text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
                text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                return text[:_MAX_FULLTEXT_CHARS] if len(text) > 200 else None
            elif "xml" in content_type:
                xml = raw_bytes.decode("utf-8", errors="replace")
                text = re.sub(r"<[^>]+>", " ", xml)
                text = re.sub(r"\s+", " ", text).strip()
                return text[:_MAX_FULLTEXT_CHARS] if len(text) > 200 else None
    except Exception as exc:
        _log.debug("Download failed for %s: %s", url[:80], exc)
    return None


# ── Main batch orchestrator ───────────────────────────────────────────

def batch_fetch_fulltext(args: dict[str, Any], ctx: dict) -> str:
    """Batch fetch full text from 6 sources in parallel. Stores results in DB.
    Sources: Semantic Scholar (500/batch), CORE (10/batch), OpenAlex (50/batch),
    Unpaywall (1/call), Europe PMC (20/batch), PubMed Central (20/batch)."""

    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    # Get papers without full text that have DOIs
    rows = db._conn.execute(
        "SELECT paper_id, doi FROM papers "
        "WHERE session_id = ? AND doi IS NOT NULL AND full_text IS NULL",
        (session_id,),
    ).fetchall()

    if not rows:
        return json.dumps({"fetched": 0, "message": "All papers already have full text or no DOIs available"})

    doi_to_pid: dict[str, int] = {}
    dois: list[str] = []
    for r in rows:
        doi = r["doi"].strip().lower()
        doi_to_pid[doi] = r["paper_id"]
        dois.append(doi)

    _log.info("FULLTEXT BATCH: %d papers need full text", len(dois))

    # LOCAL-FIRST: Check central DB for cached full texts before hitting APIs
    central_found = 0
    central_db = ctx.get("central_db")
    if central_db:
        for doi in list(dois):
            cp = central_db.get_paper_by_doi(doi)
            if cp:
                text = central_db.get_fulltext(cp["paper_id"])
                if text:
                    try:
                        db.store_fulltext_content(doi=doi, text=text[:_MAX_FULLTEXT_CHARS])
                        central_found += 1
                    except Exception:
                        pass
        if central_found:
            _log.info("FULLTEXT BATCH: %d/%d texts found in central DB — skipping those", central_found, len(dois))
            # Re-query to get updated list of papers still needing full text
            rows = db._conn.execute(
                "SELECT paper_id, doi FROM papers "
                "WHERE session_id = ? AND doi IS NOT NULL AND full_text IS NULL",
                (session_id,),
            ).fetchall()
            doi_to_pid = {}
            dois = []
            for r in rows:
                d = r["doi"].strip().lower()
                doi_to_pid[d] = r["paper_id"]
                dois.append(d)
            _log.info("FULLTEXT BATCH: %d papers still need full text after central DB", len(dois))
            if not dois:
                return json.dumps({"fetched": central_found, "from_central_db": central_found,
                                   "message": "All full texts found in central DB"})

    # Load credentials
    core_key = os.getenv("CORE_API_KEY", "")
    if not core_key:
        try:
            creds = json.loads((os.path.expanduser("~/.ara/credentials.json") and open(os.path.expanduser("~/.ara/credentials.json")).read()))
            core_key = creds.get("core_api_key", "")
        except Exception:
            pass
    _EMAILS = ["syedmosayebalam@gmail.com", "eikiyo.netflix@gmail.com"]
    unpaywall_email = _EMAILS[0]

    # Run sources in parallel threads
    all_results: dict[str, str] = {}
    source_stats: dict[str, int] = {}

    def _run_source(name: str, func: Any, *func_args: Any) -> tuple[str, dict[str, str]]:
        try:
            results = func(*func_args)
            return name, results
        except Exception as exc:
            _log.warning("FULLTEXT source %s failed: %s", name, exc)
            return name, {}

    # Remaining DOIs after each source succeeds — avoid re-fetching
    remaining = set(dois)

    def _collect_results(name: str, results: dict[str, str]) -> int:
        """Store results in DB and update remaining set. Returns count of new texts."""
        new_found = 0
        for doi, text in results.items():
            doi_lower = doi.lower()
            if doi_lower in remaining:
                all_results[doi_lower] = text
                remaining.discard(doi_lower)
                new_found += 1
                try:
                    db.store_fulltext_content(doi=doi_lower, text=text[:_MAX_FULLTEXT_CHARS])
                except Exception as exc:
                    _log.debug("Immediate store failed for %s: %s", doi_lower, exc)
        source_stats[name] = new_found
        _log.info("FULLTEXT source %s: found %d texts (%d remaining)", name, new_found, len(remaining))
        return new_found

    # Phase 1: Batch APIs first (cheapest, fastest) — 180s total timeout
    _PHASE1_TIMEOUT = 180
    _log.info("FULLTEXT Phase 1: starting parallel batch APIs (S2, OpenAlex, EPMC, CORE)")
    pool = ThreadPoolExecutor(max_workers=4)
    futures: dict[Any, str] = {}

    # S2 batch — up to 500 per call, no key needed
    futures[pool.submit(_run_source, "semantic_scholar", _fetch_s2_batch, list(remaining))] = "semantic_scholar"

    # CORE batch — 20 per OR-query, capped at 100 DOIs
    if core_key:
        futures[pool.submit(_run_source, "core", _fetch_core_batch, list(remaining), core_key)] = "core"

    # OpenAlex batch — 50 per filter
    futures[pool.submit(_run_source, "openalex", _fetch_openalex_batch, list(remaining))] = "openalex"

    # Europe PMC — 20 per query
    futures[pool.submit(_run_source, "europe_pmc", _fetch_epmc_batch, list(remaining))] = "europe_pmc"

    try:
        for fut in as_completed(futures, timeout=_PHASE1_TIMEOUT):
            try:
                name, results = fut.result(timeout=5)
                _collect_results(name, results)
            except Exception as exc:
                src = futures.get(fut, "unknown")
                _log.warning("FULLTEXT Phase 1 source %s failed: %s", src, exc)
    except TimeoutError:
        _log.warning("FULLTEXT Phase 1 timeout (%ds) — cancelling remaining futures", _PHASE1_TIMEOUT)
        for fut in futures:
            fut.cancel()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    _log.info("FULLTEXT Phase 1 complete: %d texts found so far, %d remaining", len(all_results), len(remaining))

    # Phase 2: PMC — needs NCBI, good for biomedical (cap at 100 DOIs, 120s timeout)
    if remaining:
        pmc_dois = list(remaining)[:100]
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_run_source, "pmc", _fetch_pmc_batch, pmc_dois)
            try:
                _, pmc_results = fut.result(timeout=120)
            except Exception as exc:
                _log.warning("FULLTEXT PMC phase timed out or failed: %s", exc)
                pmc_results = {}
        new_found = 0
        for doi, text in pmc_results.items():
            doi_lower = doi.lower()
            if doi_lower in remaining:
                all_results[doi_lower] = text
                remaining.discard(doi_lower)
                new_found += 1
                try:
                    db.store_fulltext_content(doi=doi_lower, text=text[:_MAX_FULLTEXT_CHARS])
                except Exception:
                    pass
        source_stats["pmc"] = new_found
        _log.info("FULLTEXT source pmc: found %d texts (%d remaining)", new_found, len(remaining))

    # Phase 3: Unpaywall — 1 per call, use only for remaining (cap at 100, 120s timeout)
    if remaining:
        unpaywall_dois = list(remaining)[:100]
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_run_source, "unpaywall", _fetch_unpaywall_batch, unpaywall_dois, unpaywall_email)
            try:
                _, uw_results = fut.result(timeout=120)
            except Exception as exc:
                _log.warning("FULLTEXT Unpaywall phase timed out or failed: %s", exc)
                uw_results = {}
        new_found = 0
        for doi, text in uw_results.items():
            doi_lower = doi.lower()
            if doi_lower in remaining:
                all_results[doi_lower] = text
                remaining.discard(doi_lower)
                new_found += 1
                try:
                    db.store_fulltext_content(doi=doi_lower, text=text[:_MAX_FULLTEXT_CHARS])
                except Exception:
                    pass
        source_stats["unpaywall"] = new_found
        _log.info("FULLTEXT source unpaywall: found %d texts (%d remaining)", new_found, len(remaining))

    # Final count (all already committed individually above)
    stored = len(all_results)

    total_fetched = stored + central_found
    total_needed_original = len(dois) + central_found
    summary = {
        "papers_without_fulltext": total_needed_original,
        "total_fetched": total_fetched,
        "from_central_db": central_found,
        "from_apis": stored,
        "still_missing": len(remaining),
        "note": f"Central DB matched {central_found}/{total_needed_original} papers (rest are from different topics or lack fulltext in cache)",
        "coverage": f"{total_fetched / total_needed_original * 100:.1f}%" if total_needed_original else "0%",
        "sources": source_stats,
    }
    _log.info("FULLTEXT BATCH COMPLETE: %s", json.dumps(summary))
    return json.dumps(summary)
