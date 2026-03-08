# Location: ara/tools/fulltext.py
# Purpose: Batch full-text fetching from 20+ sources — all racing in parallel
# Functions: batch_fetch_fulltext, 20 source fetchers
# Calls: http.py, db.py, papers.py
# Imports: json, logging, os, re, time, threading, concurrent.futures

from __future__ import annotations

import json
import logging
import os
import re
import time
import threading as _th
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .http import rate_limited_get
from .papers import _extract_text_from_pdf

_log = logging.getLogger(__name__)
_MAX_FULLTEXT_CHARS = 25000
_MAX_DOWNLOAD_BYTES = 10_000_000


# ══════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ══════════════════════════════════════════════════════════════════════

def _normalize_doi(raw: str) -> str:
    doi = raw.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "https://dx.doi.org/"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi


def _download_and_extract(url: str, timeout: int = 15) -> str | None:
    """Download a URL and extract text (PDF or HTML/XML)."""
    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
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


def _strip_xml(xml: str) -> str | None:
    text = re.sub(r"<[^>]+>", " ", xml)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_FULLTEXT_CHARS] if len(text) > 200 else None


_CHUNK_CHARS = 2000
_CHUNK_OVERLAP = 200


def _chunk_text(text: str, chunk_chars: int = _CHUNK_CHARS, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks at sentence boundaries."""
    if not text or len(text) < 200:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_chars
        if end < len(text):
            search_start = end - int(chunk_chars * 0.2)
            best_break = -1
            for sep in ['. ', '.\n', '? ', '! ', '\n\n']:
                pos = text.rfind(sep, search_start, end)
                if pos > best_break:
                    best_break = pos + len(sep)
            if best_break > search_start:
                end = best_break
        chunk = text[start:end].strip()
        if len(chunk) >= 100:
            chunks.append(chunk)
        start = end - overlap
        if start >= len(text):
            break
    return chunks


# ══════════════════════════════════════════════════════════════════════
# SOURCE 1: Semantic Scholar (batch 500 DOIs/call)
# ══════════════════════════════════════════════════════════════════════

def _fetch_s2_batch(dois: list[str]) -> dict[str, str]:
    results: dict[str, str] = {}
    batch_size = 500
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    headers: dict[str, str] = {"x-api-key": api_key} if api_key else {}

    for i in range(0, len(dois), batch_size):
        batch = dois[i:i + batch_size]
        ids = [f"DOI:{d}" for d in batch]
        for attempt in range(3):
            try:
                client = httpx.Client(timeout=60, follow_redirects=True)
                resp = client.post(
                    "https://api.semanticscholar.org/graph/v1/paper/batch",
                    json={"ids": ids},
                    params={"fields": "externalIds,openAccessPdf,abstract,tldr"},
                    headers=headers,
                )
                client.close()
                if resp.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    break

                # Collect PDF URLs for parallel download + abstracts as fallback
                pdf_tasks: list[tuple[str, str]] = []
                for paper in resp.json():
                    if not paper or not isinstance(paper, dict):
                        continue
                    paper_doi = (paper.get("externalIds") or {}).get("DOI", "")
                    if not paper_doi:
                        continue
                    doi_lower = paper_doi.lower()

                    # Store abstract/tldr as fallback immediately
                    abstract = paper.get("abstract") or ""
                    tldr = (paper.get("tldr") or {}).get("text", "")
                    fallback = abstract or tldr
                    if fallback and len(fallback) > 100 and doi_lower not in results:
                        results[doi_lower] = fallback[:_MAX_FULLTEXT_CHARS]

                    # Queue PDF for parallel download (will overwrite abstract)
                    pdf_url = (paper.get("openAccessPdf") or {}).get("url")
                    if pdf_url:
                        pdf_tasks.append((doi_lower, pdf_url))

                # Parallel PDF downloads (10 concurrent)
                if pdf_tasks:
                    with ThreadPoolExecutor(max_workers=10) as pool:
                        futures = {pool.submit(_download_and_extract, url): doi
                                   for doi, url in pdf_tasks}
                        for fut in as_completed(futures, timeout=120):
                            doi_key = futures[fut]
                            try:
                                text = fut.result()
                                if text:
                                    results[doi_key] = text  # Full text overwrites abstract
                            except Exception:
                                pass
                break
            except Exception:
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
        time.sleep(3)
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 2: OpenAlex (batch 50 DOIs/call)
# ══════════════════════════════════════════════════════════════════════

def _fetch_openalex_batch(dois: list[str]) -> dict[str, str]:
    results: dict[str, str] = {}
    batch_size = 50
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
            for item in resp.json().get("results", []):
                item_doi = _normalize_doi(item.get("doi", ""))
                if not item_doi:
                    continue
                oa = item.get("open_access") or {}
                if not oa.get("is_oa"):
                    continue
                best_loc = item.get("best_oa_location") or {}
                pdf_url = best_loc.get("pdf_url") or best_loc.get("landing_page_url")
                if pdf_url:
                    text = _download_and_extract(pdf_url)
                    if text:
                        results[item_doi] = text
        except Exception as exc:
            _log.debug("OpenAlex batch failed: %s", exc)
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 3: Europe PMC (batch 20 DOIs/call → full text XML)
# ══════════════════════════════════════════════════════════════════════

def _fetch_epmc_batch(dois: list[str]) -> dict[str, str]:
    results: dict[str, str] = {}
    lock = _th.Lock()
    batch_size = 100  # Europe PMC supports up to 1000
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

            # Collect PMC IDs for parallel fulltext fetch
            pmc_tasks: list[tuple[str, str]] = []
            for item in resp.json().get("resultList", {}).get("result", []):
                item_doi = (item.get("doi") or "").lower()
                pmcid = item.get("pmcid")
                if pmcid and item_doi:
                    pmc_tasks.append((item_doi, pmcid))

            def _fetch_xml(task: tuple[str, str]) -> None:
                doi, pmcid = task
                try:
                    ft_resp = rate_limited_get(
                        f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML",
                        timeout=20,
                    )
                    if ft_resp.status_code == 200:
                        text = _strip_xml(ft_resp.text)
                        if text:
                            with lock:
                                results[doi] = text
                except Exception:
                    pass

            if pmc_tasks:
                with ThreadPoolExecutor(max_workers=10) as pool:
                    list(pool.map(_fetch_xml, pmc_tasks))
        except Exception:
            pass
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 4: CORE (batch 20 DOIs/call)
# ══════════════════════════════════════════════════════════════════════

def _fetch_core_batch(dois: list[str], api_key: str) -> dict[str, str]:
    results: dict[str, str] = {}
    batch_size = 100  # CORE v3 supports up to 100 per call
    dois = dois[:300]
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
                    time.sleep(5 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    break

                # Collect items with downloadUrl for parallel fetch
                download_tasks: list[tuple[str, str]] = []
                for item in resp.json().get("results", []):
                    item_doi = _normalize_doi(item.get("doi", ""))
                    if not item_doi:
                        continue
                    full_text = item.get("fullText", "")
                    if full_text and len(full_text) > 200:
                        results[item_doi] = full_text[:_MAX_FULLTEXT_CHARS]
                    elif item.get("downloadUrl"):
                        download_tasks.append((item_doi, item["downloadUrl"]))

                # Parallel download for items without inline fullText
                if download_tasks:
                    with ThreadPoolExecutor(max_workers=8) as pool:
                        futures = {pool.submit(_download_and_extract, url): doi
                                   for doi, url in download_tasks}
                        for fut in as_completed(futures, timeout=60):
                            doi_key = futures[fut]
                            try:
                                text = fut.result()
                                if text:
                                    results[doi_key] = text
                            except Exception:
                                pass
                break
            except Exception:
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
        time.sleep(1)
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 5: PubMed Central (batch via NCBI)
# ══════════════════════════════════════════════════════════════════════

def _fetch_pmc_batch(dois: list[str]) -> dict[str, str]:
    results: dict[str, str] = {}
    ncbi_key = os.getenv("NCBI_API_KEY", "")
    batch_size = 20
    dois = dois[:150]

    for i in range(0, len(dois), batch_size):
        batch = dois[i:i + batch_size]
        query = " OR ".join(f'{d}[DOI]' for d in batch)
        try:
            params: dict[str, Any] = {"db": "pmc", "term": query, "retmax": batch_size, "retmode": "json"}
            if ncbi_key:
                params["api_key"] = ncbi_key
            resp = rate_limited_get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params=params, timeout=20,
            )
            if resp.status_code != 200:
                continue
            pmc_ids = resp.json().get("esearchresult", {}).get("idlist", [])
            if not pmc_ids:
                continue
            fetch_params: dict[str, Any] = {"db": "pmc", "id": ",".join(pmc_ids), "rettype": "xml", "retmode": "xml"}
            if ncbi_key:
                fetch_params["api_key"] = ncbi_key
            ft_resp = rate_limited_get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params=fetch_params, timeout=30,
            )
            if ft_resp.status_code != 200:
                continue
            articles = re.split(r'<article\b', ft_resp.text)
            for article_xml in articles[1:]:
                doi_match = re.search(r'pub-id-type="doi">([^<]+)<', article_xml)
                if not doi_match:
                    continue
                found_doi = doi_match.group(1).lower()
                text = _strip_xml(article_xml)
                if text:
                    results[found_doi] = text
        except Exception:
            pass
        time.sleep(1)
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 6: Unpaywall (1 DOI/call)
# ══════════════════════════════════════════════════════════════════════

_UNPAYWALL_EMAILS = ["syedmosayebalam@gmail.com", "eikiyo.netflix@gmail.com"]

def _fetch_unpaywall_batch(dois: list[str], email: str) -> dict[str, str]:
    results: dict[str, str] = {}
    lock = _th.Lock()
    email_idx = [0]  # mutable for threads
    dois = dois[:200]

    def _fetch_one(doi: str) -> None:
        eidx = email_idx[0] % len(_UNPAYWALL_EMAILS)
        current_email = _UNPAYWALL_EMAILS[eidx]
        for attempt in range(2):
            try:
                resp = rate_limited_get(
                    f"https://api.unpaywall.org/v2/{doi}",
                    params={"email": current_email}, timeout=15,
                )
                if resp.status_code == 429 or resp.status_code == 422:
                    email_idx[0] += 1
                    current_email = _UNPAYWALL_EMAILS[email_idx[0] % len(_UNPAYWALL_EMAILS)]
                    time.sleep(2)
                    continue
                if resp.status_code != 200:
                    break
                best_oa = resp.json().get("best_oa_location") or {}
                url = best_oa.get("url_for_pdf") or best_oa.get("url")
                if url:
                    text = _download_and_extract(url)
                    if text:
                        with lock:
                            results[doi] = text
                break
            except Exception:
                break

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(_fetch_one, dois))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 7: CrossRef (TDM links — batch 1 DOI/call)
# ══════════════════════════════════════════════════════════════════════

def _fetch_crossref_tdm(dois: list[str]) -> dict[str, str]:
    """CrossRef text-and-data-mining links → direct full text (parallelized)."""
    results: dict[str, str] = {}
    lock = _th.Lock()
    dois = dois[:200]

    def _fetch_one(doi: str) -> None:
        try:
            resp = rate_limited_get(
                f"https://api.crossref.org/works/{doi}",
                params={"mailto": "ara-research@example.com"},
                timeout=15,
            )
            if resp.status_code != 200:
                return
            links = resp.json().get("message", {}).get("link", [])
            for link in links:
                ct = link.get("content-type", "")
                url = link.get("URL", "")
                if url and ("xml" in ct or "plain" in ct or "pdf" in ct):
                    text = _download_and_extract(url)
                    if text:
                        with lock:
                            results[doi] = text
                        break
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(_fetch_one, dois))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 8: arXiv (direct PDF by DOI pattern or search)
# ══════════════════════════════════════════════════════════════════════

def _fetch_arxiv_batch(dois: list[str]) -> dict[str, str]:
    """arXiv: extract arXiv IDs from DOIs and fetch PDFs directly (parallelized)."""
    results: dict[str, str] = {}
    lock = _th.Lock()
    # arXiv DOIs look like 10.48550/arXiv.XXXX.XXXXX
    arxiv_dois = [(d, re.search(r'arxiv\.(\d+\.\d+)', d, re.I)) for d in dois]
    arxiv_papers = [(d, m.group(1)) for d, m in arxiv_dois if m]

    def _fetch_pdf(task: tuple[str, str]) -> None:
        doi, arxiv_id = task
        try:
            text = _download_and_extract(f"https://arxiv.org/pdf/{arxiv_id}")
            if text:
                with lock:
                    results[doi] = text
        except Exception:
            pass

    if arxiv_papers:
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(_fetch_pdf, arxiv_papers[:100]))

    # Also try S2 to find arXiv PDFs for non-arXiv DOIs
    non_arxiv = [d for d in dois if not any(d == ad[0] for ad in arxiv_papers)]

    def _search_arxiv(doi: str) -> None:
        try:
            resp = rate_limited_get(
                f"https://export.arxiv.org/api/query?search_query=doi:{doi}&max_results=1",
                timeout=15,
            )
            if resp.status_code == 200 and "<entry>" in resp.text:
                id_match = re.search(r'<id>http://arxiv.org/abs/([^<]+)</id>', resp.text)
                if id_match:
                    arxiv_id = id_match.group(1)
                    text = _download_and_extract(f"https://arxiv.org/pdf/{arxiv_id}")
                    if text:
                        with lock:
                            results[doi] = text
        except Exception:
            pass

    if non_arxiv:
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(_search_arxiv, non_arxiv[:50]))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 9: bioRxiv / medRxiv (DOI pattern → direct PDF)
# ══════════════════════════════════════════════════════════════════════

def _fetch_biorxiv_batch(dois: list[str]) -> dict[str, str]:
    """bioRxiv/medRxiv: DOI → direct PDF download (parallelized)."""
    results: dict[str, str] = {}
    lock = _th.Lock()
    biorxiv_dois = [d for d in dois if "10.1101/" in d]

    def _fetch_one(doi: str) -> None:
        try:
            text = _download_and_extract(f"https://www.biorxiv.org/content/{doi}v1.full.pdf")
            if not text:
                text = _download_and_extract(f"https://www.medrxiv.org/content/{doi}v1.full.pdf")
            if text:
                with lock:
                    results[doi] = text
        except Exception:
            pass

    if biorxiv_dois:
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(_fetch_one, biorxiv_dois[:100]))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 10: DOAJ (Open Access journal lookup)
# ══════════════════════════════════════════════════════════════════════

def _fetch_doaj_batch(dois: list[str]) -> dict[str, str]:
    """DOAJ: search by DOI, get full text link (parallelized)."""
    results: dict[str, str] = {}
    lock = _th.Lock()

    def _fetch_one(doi: str) -> None:
        try:
            resp = rate_limited_get(
                f"https://doaj.org/api/search/articles/doi:{doi}",
                timeout=15,
            )
            if resp.status_code != 200:
                return
            items = resp.json().get("results", [])
            if items:
                bibjson = items[0].get("bibjson", {})
                links = bibjson.get("link", [])
                for link in links:
                    url = link.get("url", "")
                    if url:
                        text = _download_and_extract(url)
                        if text:
                            with lock:
                                results[doi] = text
                            break
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_fetch_one, dois[:100]))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 11: Zenodo (DOI → file download)
# ══════════════════════════════════════════════════════════════════════

def _fetch_zenodo_batch(dois: list[str]) -> dict[str, str]:
    """Zenodo: DOI lookup → download attached PDF (parallelized)."""
    results: dict[str, str] = {}
    lock = _th.Lock()
    zenodo_dois = [d for d in dois if "zenodo" in d.lower()]

    def _fetch_one(doi: str) -> None:
        try:
            match = re.search(r'zenodo\.(\d+)', doi, re.I)
            if not match:
                return
            record_id = match.group(1)
            resp = rate_limited_get(
                f"https://zenodo.org/api/records/{record_id}",
                timeout=15,
            )
            if resp.status_code != 200:
                return
            files = resp.json().get("files", [])
            for f in files:
                if f.get("key", "").endswith(".pdf"):
                    url = f.get("links", {}).get("self", "")
                    if url:
                        text = _download_and_extract(url)
                        if text:
                            with lock:
                                results[doi] = text
                            break
        except Exception:
            pass

    if zenodo_dois:
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(_fetch_one, zenodo_dois[:50]))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 12: HAL (French open archive)
# ══════════════════════════════════════════════════════════════════════

def _fetch_hal_batch(dois: list[str]) -> dict[str, str]:
    """HAL: search by DOI, get PDF if available (parallelized)."""
    results: dict[str, str] = {}
    lock = _th.Lock()

    def _fetch_one(doi: str) -> None:
        try:
            resp = rate_limited_get(
                "https://api.archives-ouvertes.fr/search/",
                params={"q": f'doiId_s:"{doi}"', "fl": "doiId_s,fileMain_s,uri_s", "wt": "json"},
                timeout=15,
            )
            if resp.status_code != 200:
                return
            docs = resp.json().get("response", {}).get("docs", [])
            if docs:
                pdf_url = docs[0].get("fileMain_s") or docs[0].get("uri_s")
                if pdf_url:
                    text = _download_and_extract(pdf_url)
                    if text:
                        with lock:
                            results[doi] = text
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_fetch_one, dois[:100]))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 13: DBLP (CS papers → PDF links)
# ══════════════════════════════════════════════════════════════════════

def _fetch_dblp_batch(dois: list[str]) -> dict[str, str]:
    """DBLP: DOI lookup → electronic edition link → PDF (parallelized)."""
    results: dict[str, str] = {}
    lock = _th.Lock()

    def _fetch_one(doi: str) -> None:
        try:
            resp = rate_limited_get(
                "https://dblp.org/search/publ/api",
                params={"q": doi, "format": "json", "h": "1"},
                timeout=15,
            )
            if resp.status_code != 200:
                return
            hits = resp.json().get("result", {}).get("hits", {}).get("hit", [])
            if hits:
                info = hits[0].get("info", {})
                ee = info.get("ee", "")
                if isinstance(ee, list):
                    ee = ee[0] if ee else ""
                if ee:
                    text = _download_and_extract(ee)
                    if text:
                        with lock:
                            results[doi] = text
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_fetch_one, dois[:100]))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 14: BASE (Bielefeld Academic Search Engine)
# ══════════════════════════════════════════════════════════════════════

def _fetch_base_batch(dois: list[str]) -> dict[str, str]:
    """BASE: search by DOI → OA full text link (parallelized)."""
    results: dict[str, str] = {}
    lock = _th.Lock()

    def _fetch_one(doi: str) -> None:
        try:
            resp = rate_limited_get(
                "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi",
                params={"func": "PerformSearch", "query": f'dcdoi:"{doi}"',
                        "format": "json", "hits": "1"},
                timeout=15,
            )
            if resp.status_code != 200:
                return
            docs = resp.json().get("response", {}).get("docs", [])
            if docs:
                url = docs[0].get("dclink") or docs[0].get("dcidentifier")
                if url and isinstance(url, str):
                    text = _download_and_extract(url)
                    if text:
                        with lock:
                            results[doi] = text
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_fetch_one, dois[:100]))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 15: Internet Archive Scholar
# ══════════════════════════════════════════════════════════════════════

def _fetch_ia_scholar_batch(dois: list[str]) -> dict[str, str]:
    """Internet Archive Scholar: search cached academic papers (parallelized)."""
    results: dict[str, str] = {}
    lock = _th.Lock()

    def _fetch_one(doi: str) -> None:
        try:
            resp = rate_limited_get(
                "https://scholar.archive.org/search",
                params={"q": f'doi:"{doi}"', "format": "json"},
                timeout=15,
            )
            if resp.status_code != 200:
                return
            hits = resp.json().get("results", [])
            if hits:
                for hit in hits[:1]:
                    access = hit.get("access", [])
                    for a in access:
                        url = a.get("access_url", "")
                        if url:
                            text = _download_and_extract(url)
                            if text:
                                with lock:
                                    results[doi] = text
                                return
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_fetch_one, dois[:100]))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 16: SciELO (Latin American OA journals)
# ══════════════════════════════════════════════════════════════════════

def _fetch_scielo_batch(dois: list[str]) -> dict[str, str]:
    """SciELO: DOI redirect → scrape article page (parallelized)."""
    results: dict[str, str] = {}
    lock = _th.Lock()
    scielo_dois = [d for d in dois if "scielo" in d.lower() or "10.1590/" in d]

    def _fetch_one(doi: str) -> None:
        try:
            text = _download_and_extract(f"https://doi.org/{doi}")
            if text:
                with lock:
                    results[doi] = text
        except Exception:
            pass

    if scielo_dois:
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(_fetch_one, scielo_dois[:50]))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 17: Figshare (research data/papers)
# ══════════════════════════════════════════════════════════════════════

def _fetch_figshare_batch(dois: list[str]) -> dict[str, str]:
    """Figshare: search by DOI, download attached files (parallelized)."""
    results: dict[str, str] = {}
    lock = _th.Lock()

    def _fetch_one(doi: str) -> None:
        try:
            resp = httpx.post(
                "https://api.figshare.com/v2/articles/search",
                json={"doi": doi},
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code != 200 or not resp.json():
                return
            article = resp.json()[0]
            article_id = article.get("id")
            if not article_id:
                return
            detail = rate_limited_get(
                f"https://api.figshare.com/v2/articles/{article_id}",
                timeout=15,
            )
            if detail.status_code == 200:
                for f in detail.json().get("files", []):
                    if f.get("name", "").endswith(".pdf"):
                        text = _download_and_extract(f.get("download_url", ""))
                        if text:
                            with lock:
                                results[doi] = text
                            break
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(_fetch_one, dois[:50]))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 18: DOI direct resolve (publisher landing pages)
# ══════════════════════════════════════════════════════════════════════

def _fetch_doi_direct(dois: list[str]) -> dict[str, str]:
    """Resolve DOI → publisher page → extract HTML text (parallelized)."""
    results: dict[str, str] = {}
    lock = _th.Lock()
    dois = dois[:200]

    def _fetch_one(doi: str) -> None:
        try:
            text = _download_and_extract(f"https://doi.org/{doi}")
            if text:
                with lock:
                    results[doi] = text
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(_fetch_one, dois))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 19: SSRN (preprints)
# ══════════════════════════════════════════════════════════════════════

def _fetch_ssrn_batch(dois: list[str]) -> dict[str, str]:
    """SSRN: DOIs with 10.2139/ssrn → direct PDF download (parallelized)."""
    results: dict[str, str] = {}
    lock = _th.Lock()
    ssrn_dois = [d for d in dois if "10.2139/ssrn" in d]

    def _fetch_one(doi: str) -> None:
        try:
            ssrn_id = doi.replace("10.2139/ssrn.", "")
            pdf_url = f"https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID{ssrn_id}_code.pdf"
            text = _download_and_extract(pdf_url)
            if text:
                with lock:
                    results[doi] = text
        except Exception:
            pass

    if ssrn_dois:
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(_fetch_one, ssrn_dois[:50]))
    return results


# ══════════════════════════════════════════════════════════════════════
# SOURCE 20: PubMed abstracts (fallback — abstract only)
# ══════════════════════════════════════════════════════════════════════

def _fetch_pubmed_abstracts(dois: list[str]) -> dict[str, str]:
    """PubMed: fetch abstracts as fallback when full text unavailable."""
    results: dict[str, str] = {}
    ncbi_key = os.getenv("NCBI_API_KEY", "")
    batch_size = 50
    dois = dois[:200]

    for i in range(0, len(dois), batch_size):
        batch = dois[i:i + batch_size]
        query = " OR ".join(f'{d}[DOI]' for d in batch)
        try:
            params: dict[str, Any] = {"db": "pubmed", "term": query, "retmax": batch_size, "retmode": "json"}
            if ncbi_key:
                params["api_key"] = ncbi_key
            resp = rate_limited_get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params=params, timeout=20,
            )
            if resp.status_code != 200:
                continue
            pmids = resp.json().get("esearchresult", {}).get("idlist", [])
            if not pmids:
                continue
            fetch_params: dict[str, Any] = {
                "db": "pubmed", "id": ",".join(pmids),
                "rettype": "abstract", "retmode": "xml",
            }
            if ncbi_key:
                fetch_params["api_key"] = ncbi_key
            ft_resp = rate_limited_get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params=fetch_params, timeout=30,
            )
            if ft_resp.status_code != 200:
                continue
            # Parse XML for DOI + abstract pairs
            articles = re.split(r'<PubmedArticle', ft_resp.text)
            for article_xml in articles[1:]:
                doi_match = re.search(r'<ArticleId IdType="doi">([^<]+)<', article_xml)
                abstract_match = re.search(r'<AbstractText[^>]*>(.*?)</AbstractText>', article_xml, re.DOTALL)
                if doi_match and abstract_match:
                    found_doi = doi_match.group(1).lower()
                    abstract_text = re.sub(r'<[^>]+>', '', abstract_match.group(1)).strip()
                    if len(abstract_text) > 100:
                        results[found_doi] = abstract_text[:_MAX_FULLTEXT_CHARS]
        except Exception:
            pass
        time.sleep(1)
    return results


# ══════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR — ALL SOURCES RACE IN PARALLEL
# ══════════════════════════════════════════════════════════════════════

def batch_fetch_fulltext(args: dict[str, Any], ctx: dict) -> str:
    """Race 20 sources in parallel. First result for each DOI wins.
    Sources: S2, OpenAlex, EPMC, CORE, PMC, Unpaywall, CrossRef TDM,
    arXiv, bioRxiv, DOAJ, Zenodo, HAL, DBLP, BASE, IA Scholar,
    SciELO, Figshare, DOI direct, SSRN, PubMed abstracts."""

    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    rows = db._conn.execute(
        "SELECT paper_id, doi FROM papers "
        "WHERE session_id = ? AND doi IS NOT NULL AND full_text IS NULL",
        (session_id,),
    ).fetchall()
    if not rows:
        return json.dumps({"fetched": 0, "message": "All papers already have full text"})

    doi_to_pid: dict[str, int] = {}
    dois: list[str] = []
    for r in rows:
        doi = r["doi"].strip().lower()
        doi_to_pid[doi] = r["paper_id"]
        dois.append(doi)

    _log.info("FULLTEXT BATCH: %d papers need full text", len(dois))

    # ── LOCAL-FIRST: Central DB cache ──
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
            _log.info("FULLTEXT BATCH: %d/%d texts from central DB cache", central_found, len(dois))
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
            if not dois:
                return json.dumps({"fetched": central_found, "from_central_db": central_found,
                                   "message": "All full texts found in central DB"})

    _log.info("FULLTEXT BATCH: %d papers still need full text — launching 20 sources", len(dois))

    # ── Credentials ──
    core_key = os.getenv("CORE_API_KEY", "")
    if not core_key:
        try:
            creds = json.loads(open(os.path.expanduser("~/.ara/credentials.json")).read())
            core_key = creds.get("core_api_key", "")
        except Exception:
            pass

    # ── Thread-safe results collection ──
    found: dict[str, str] = {}  # doi → text (first writer wins)
    found_lock = _th.Lock()
    source_stats: dict[str, int] = {}

    def _run_source(name: str, func, *func_args):
        """Run a source and collect results. Thread-safe."""
        try:
            results = func(*func_args)
            count = 0
            with found_lock:
                for doi, text in results.items():
                    doi_lower = doi.lower()
                    if doi_lower not in found and doi_lower in doi_to_pid:
                        found[doi_lower] = text
                        count += 1
                        try:
                            db.store_fulltext_content(doi=doi_lower, text=text[:_MAX_FULLTEXT_CHARS])
                        except Exception:
                            pass
                source_stats[name] = count
            _log.info("FULLTEXT source %s: +%d texts (total: %d/%d)",
                      name, count, len(found), len(dois))
        except Exception as exc:
            _log.warning("FULLTEXT source %s failed: %s", name, exc)
            source_stats[name] = 0

    # ── LAUNCH ALL 20 SOURCES IN PARALLEL ──
    _RACE_TIMEOUT = 300  # 5 min total timeout for the race

    # Build source list — all get the same DOI list
    remaining_dois = list(dois)
    sources = [
        ("semantic_scholar", _fetch_s2_batch, remaining_dois),
        ("openalex", _fetch_openalex_batch, remaining_dois),
        ("europe_pmc", _fetch_epmc_batch, remaining_dois),
        ("pmc", _fetch_pmc_batch, remaining_dois),
        ("unpaywall", _fetch_unpaywall_batch, remaining_dois, _UNPAYWALL_EMAILS[0]),
        ("crossref_tdm", _fetch_crossref_tdm, remaining_dois),
        ("arxiv", _fetch_arxiv_batch, remaining_dois),
        ("biorxiv", _fetch_biorxiv_batch, remaining_dois),
        ("doaj", _fetch_doaj_batch, remaining_dois),
        ("zenodo", _fetch_zenodo_batch, remaining_dois),
        ("hal", _fetch_hal_batch, remaining_dois),
        ("dblp", _fetch_dblp_batch, remaining_dois),
        ("base", _fetch_base_batch, remaining_dois),
        ("ia_scholar", _fetch_ia_scholar_batch, remaining_dois),
        ("scielo", _fetch_scielo_batch, remaining_dois),
        ("figshare", _fetch_figshare_batch, remaining_dois),
        ("doi_direct", _fetch_doi_direct, remaining_dois),
        ("ssrn", _fetch_ssrn_batch, remaining_dois),
        ("pubmed_abstracts", _fetch_pubmed_abstracts, remaining_dois),
    ]
    if core_key:
        sources.append(("core", _fetch_core_batch, remaining_dois, core_key))

    threads = []
    for source_args in sources:
        name = source_args[0]
        func = source_args[1]
        args_rest = source_args[2:]
        t = _th.Thread(target=_run_source, args=(name, func, *args_rest), daemon=True)
        threads.append(t)

    _log.info("FULLTEXT RACE: launching %d sources in parallel", len(threads))
    start_time = time.monotonic()

    for t in threads:
        t.start()

    # Wait for all threads with timeout
    for t in threads:
        remaining_time = _RACE_TIMEOUT - (time.monotonic() - start_time)
        if remaining_time <= 0:
            break
        t.join(timeout=max(remaining_time, 1))

    alive = sum(1 for t in threads if t.is_alive())
    if alive:
        _log.warning("FULLTEXT RACE: %d sources still running after %ds timeout — moving on",
                      alive, _RACE_TIMEOUT)

    elapsed = time.monotonic() - start_time
    total_fetched = len(found) + central_found
    total_needed = len(dois) + central_found

    summary = {
        "papers_without_fulltext": total_needed,
        "total_fetched": total_fetched,
        "from_central_db": central_found,
        "from_apis": len(found),
        "still_missing": len(dois) - len(found),
        "coverage": f"{total_fetched / total_needed * 100:.1f}%" if total_needed else "0%",
        "elapsed_seconds": round(elapsed, 1),
        "sources": source_stats,
    }
    _log.info("FULLTEXT RACE COMPLETE in %.1fs: %s", elapsed, json.dumps(summary))
    return json.dumps(summary)
