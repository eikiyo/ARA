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
    for doi in dois:
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
    batch_size = 10  # CORE supports OR queries

    for i in range(0, len(dois), batch_size):
        batch = dois[i:i + batch_size]
        query = " OR ".join(f'doi:"{d}"' for d in batch)
        for attempt in range(3):
            try:
                resp = rate_limited_get(
                    "https://api.core.ac.uk/v3/search/works",
                    headers={"Authorization": f"Bearer {api_key}"},
                    params={"q": query, "limit": batch_size},
                    timeout=30,
                )
                if resp.status_code == 429:
                    wait = 5 * (attempt + 1)
                    _log.info("CORE 429 — retrying in %ds (attempt %d/3)", wait, attempt + 1)
                    time.sleep(wait)
                    continue
                if resp.status_code != 200:
                    _log.debug("CORE batch returned %d", resp.status_code)
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
                _log.debug("CORE batch failed (attempt %d): %s", attempt + 1, exc)
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
        time.sleep(3)  # CORE rate limit recovery

    return results


# ── Source: Semantic Scholar (batch API, up to 500 per call) ──────────

def _fetch_s2_batch(dois: list[str]) -> dict[str, str]:
    """Fetch open access PDFs from Semantic Scholar batch API."""
    results: dict[str, str] = {}
    batch_size = 500
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

    for i in range(0, len(dois), batch_size):
        batch = dois[i:i + batch_size]
        # Search PMC for DOIs
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
        with httpx.stream("GET", url, timeout=30, follow_redirects=True) as resp:
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

    # Phase 1: Batch APIs first (cheapest, fastest)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = []

        # S2 batch — up to 500 per call, no key needed
        futures.append(pool.submit(_run_source, "semantic_scholar", _fetch_s2_batch, list(remaining)))

        # CORE batch — 10 per OR-query
        if core_key:
            futures.append(pool.submit(_run_source, "core", _fetch_core_batch, list(remaining), core_key))

        # OpenAlex batch — 50 per filter
        futures.append(pool.submit(_run_source, "openalex", _fetch_openalex_batch, list(remaining)))

        # Europe PMC — 20 per query
        futures.append(pool.submit(_run_source, "europe_pmc", _fetch_epmc_batch, list(remaining)))

        for fut in as_completed(futures):
            name, results = fut.result()
            new_found = 0
            for doi, text in results.items():
                doi_lower = doi.lower()
                if doi_lower in remaining:
                    all_results[doi_lower] = text
                    remaining.discard(doi_lower)
                    new_found += 1
            source_stats[name] = new_found
            _log.info("FULLTEXT source %s: found %d texts (%d remaining)", name, new_found, len(remaining))

    # Phase 2: PMC — needs NCBI, good for biomedical
    if remaining:
        _, pmc_results = _run_source("pmc", _fetch_pmc_batch, list(remaining))
        new_found = 0
        for doi, text in pmc_results.items():
            doi_lower = doi.lower()
            if doi_lower in remaining:
                all_results[doi_lower] = text
                remaining.discard(doi_lower)
                new_found += 1
        source_stats["pmc"] = new_found
        _log.info("FULLTEXT source pmc: found %d texts (%d remaining)", new_found, len(remaining))

    # Phase 3: Unpaywall — 1 per call, use only for remaining
    if remaining:
        # Cap at 100 to avoid excessive API calls
        unpaywall_dois = list(remaining)[:100]
        _, uw_results = _run_source("unpaywall", _fetch_unpaywall_batch, unpaywall_dois, unpaywall_email)
        new_found = 0
        for doi, text in uw_results.items():
            doi_lower = doi.lower()
            if doi_lower in remaining:
                all_results[doi_lower] = text
                remaining.discard(doi_lower)
                new_found += 1
        source_stats["unpaywall"] = new_found
        _log.info("FULLTEXT source unpaywall: found %d texts (%d remaining)", new_found, len(remaining))

    # Store all results in DB
    stored = 0
    for doi, text in all_results.items():
        try:
            db.store_fulltext_content(doi=doi, text=text[:_MAX_FULLTEXT_CHARS])
            stored += 1
        except Exception as exc:
            _log.debug("Failed to store fulltext for %s: %s", doi, exc)

    summary = {
        "total_needed": len(dois),
        "fetched": stored,
        "remaining": len(remaining),
        "coverage": f"{stored / len(dois) * 100:.1f}%" if dois else "0%",
        "sources": source_stats,
    }
    _log.info("FULLTEXT BATCH COMPLETE: %s", json.dumps(summary))
    return json.dumps(summary)
