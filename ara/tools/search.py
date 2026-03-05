# Location: ara/tools/search.py
# Purpose: 9 academic API search implementations
# Functions: search_semantic_scholar, search_arxiv, search_crossref, etc.
# Calls: httpx for HTTP requests, xml.etree for XML parsing
# Imports: httpx, json, os, re, time, threading, xml.etree.ElementTree

from __future__ import annotations

import json
import logging
import os
import re
import time
import threading
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote_plus

import httpx

_log = logging.getLogger(__name__)
_TIMEOUT = 30
_MAX_RETRIES = 3
_s2_last_call = 0.0
_s2_lock = threading.Lock()


def _request_with_retry(
    url: str, headers: dict | None = None,
    params: dict | None = None, timeout: int = _TIMEOUT,
) -> dict | str | None:
    for attempt in range(_MAX_RETRIES):
        try:
            resp = httpx.get(url, headers=headers, params=params, timeout=timeout, follow_redirects=True)
            if resp.status_code == 429:
                wait = min(3 * (2 ** attempt), 30)
                _log.warning("Rate limited on %s, waiting %ds", url[:80], wait)
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                _log.warning("HTTP %d from %s", resp.status_code, url[:80])
                return None
            content_type = resp.headers.get("content-type", "")
            if "xml" in content_type or resp.text.strip().startswith("<?xml") or resp.text.strip().startswith("<"):
                return resp.text
            return resp.json()
        except Exception as exc:
            _log.warning("Request failed (attempt %d): %s", attempt + 1, exc)
            if attempt < _MAX_RETRIES - 1:
                time.sleep(2 * (attempt + 1))
    return None


def _normalize_doi(raw: str | None) -> str | None:
    """Strip common DOI prefixes, return None for empty/invalid."""
    if not raw or not isinstance(raw, str):
        return None
    doi = raw.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "https://dx.doi.org/"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi if doi else None


def _valid_year(raw: Any) -> int | None:
    """Parse and validate a year value."""
    if raw is None:
        return None
    try:
        y = int(raw)
        return y if 1800 <= y <= 2100 else None
    except (ValueError, TypeError):
        return None


def _paper_dict(
    title: str, abstract: str | None, authors: list[str],
    year: int | None, doi: str | None, source: str,
    url: str | None = None, citation_count: int = 0,
) -> dict[str, Any]:
    return {
        "title": title.strip() if title else "",
        "abstract": (abstract or "").strip()[:2000],
        "authors": authors[:20],
        "year": _valid_year(year),
        "doi": _normalize_doi(doi),
        "source": source,
        "url": url,
        "citation_count": citation_count,
    }


# ── 1. Semantic Scholar ────────────────────────────────────────────────

def search_semantic_scholar(args: dict[str, Any], ctx: dict) -> str:
    global _s2_last_call
    with _s2_lock:
        elapsed = time.time() - _s2_last_call
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        _s2_last_call = time.time()

    query = args.get("query", "")
    limit = min(args.get("limit", 20), 100)
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    headers = {"x-api-key": api_key} if api_key else {}

    params: dict[str, Any] = {
        "query": query,
        "limit": limit,
        "fields": "title,abstract,authors,year,externalIds,citationCount,url,fieldsOfStudy",
    }
    year_range = args.get("year_range")
    if year_range:
        params["year"] = year_range
    fos = args.get("fields_of_study")
    if fos:
        params["fieldsOfStudy"] = fos

    data = _request_with_retry(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        headers=headers, params=params,
    )
    if not isinstance(data, dict):
        return json.dumps({"papers": [], "error": "Semantic Scholar unavailable"})

    papers = []
    for p in data.get("data", []):
        doi = (p.get("externalIds") or {}).get("DOI")
        authors = [a.get("name", "") for a in (p.get("authors") or [])[:20]]
        papers.append(_paper_dict(
            title=p.get("title", ""),
            abstract=p.get("abstract"),
            authors=authors,
            year=p.get("year"),
            doi=doi,
            source="semantic_scholar",
            url=p.get("url"),
            citation_count=p.get("citationCount", 0),
        ))

    return json.dumps({"papers": papers, "total": data.get("total", len(papers))})


# ── 2. arXiv ───────────────────────────────────────────────────────────

def search_arxiv(args: dict[str, Any], ctx: dict) -> str:
    query = args.get("query", "")
    limit = min(args.get("limit", 20), 50)
    category = args.get("category")

    search_query = f"all:{quote_plus(query)}"
    if category:
        search_query = f"cat:{category} AND {search_query}"

    resp = _request_with_retry(
        f"http://export.arxiv.org/api/query?search_query={search_query}&max_results={limit}&sortBy=relevance"
    )
    if not isinstance(resp, str):
        return json.dumps({"papers": [], "error": "arXiv unavailable"})

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    papers = []
    try:
        root = ET.fromstring(resp)
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
            abstract = (entry.findtext("atom:summary", "", ns) or "").strip()
            authors = [a.findtext("atom:name", "", ns) for a in entry.findall("atom:author", ns)]
            published = entry.findtext("atom:published", "", ns)
            year = int(published[:4]) if published and len(published) >= 4 else None
            link = entry.find("atom:id", ns)
            url = link.text.strip() if link is not None and link.text else None
            doi_el = entry.find('.//atom:link[@title="doi"]', ns)
            doi = doi_el.get("href") if doi_el is not None else None

            papers.append(_paper_dict(
                title=title, abstract=abstract, authors=authors,
                year=year, doi=doi, source="arxiv", url=url,
            ))
    except ET.ParseError:
        return json.dumps({"papers": [], "error": "arXiv XML parse error"})

    return json.dumps({"papers": papers, "total": len(papers)})


# ── 3. CrossRef ────────────────────────────────────────────────────────

def search_crossref(args: dict[str, Any], ctx: dict) -> str:
    query = args.get("query", "")
    limit = min(args.get("limit", 20), 100)
    params: dict[str, Any] = {
        "query": query,
        "rows": limit,
        "select": "DOI,title,abstract,author,published-print,is-referenced-by-count,URL",
        "mailto": "ara-research@example.com",
    }
    from_year = args.get("from_year")
    if from_year:
        params["filter"] = f"from-pub-date:{from_year}"

    data = _request_with_retry("https://api.crossref.org/works", params=params)
    if not isinstance(data, dict):
        return json.dumps({"papers": [], "error": "CrossRef unavailable"})

    papers = []
    for item in (data.get("message", {}).get("items", [])):
        # Title can be list or string
        raw_title = item.get("title", "")
        if isinstance(raw_title, list):
            title = raw_title[0] if raw_title else ""
        else:
            title = str(raw_title)

        abstract = item.get("abstract", "")
        if "<" in abstract:
            abstract = re.sub(r"<[^>]+>", "", abstract)

        authors = [
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in (item.get("author") or [])[:20]
            if isinstance(a, dict)
        ]
        date_parts = (item.get("published-print") or item.get("published-online") or {}).get("date-parts", [[]])
        year = date_parts[0][0] if date_parts and date_parts[0] else None

        papers.append(_paper_dict(
            title=title, abstract=abstract, authors=authors,
            year=year, doi=item.get("DOI"),
            source="crossref", url=item.get("URL"),
            citation_count=item.get("is-referenced-by-count", 0),
        ))

    total = data.get("message", {}).get("total-results", len(papers))
    return json.dumps({"papers": papers, "total": total})


# ── 4. OpenAlex ────────────────────────────────────────────────────────

def search_openalex(args: dict[str, Any], ctx: dict) -> str:
    query = args.get("query", "")
    limit = min(args.get("limit", 20), 200)
    params: dict[str, Any] = {
        "search": query,
        "per_page": limit,
        "mailto": "ara-research@example.com",
    }
    from_year = args.get("from_year")
    if from_year:
        params["filter"] = f"from_publication_date:{from_year}-01-01"

    data = _request_with_retry("https://api.openalex.org/works", params=params)
    if not isinstance(data, dict):
        return json.dumps({"papers": [], "error": "OpenAlex unavailable"})

    papers = []
    for item in data.get("results", []):
        title = item.get("title", "")
        abstract = ""
        inv_idx = item.get("abstract_inverted_index")
        if inv_idx and isinstance(inv_idx, dict):
            words: list[tuple[int, str]] = []
            for word, positions in inv_idx.items():
                if isinstance(positions, list):
                    for pos in positions:
                        words.append((pos, word))
            words.sort()
            abstract = " ".join(w for _, w in words)

        authors = []
        for authorship in (item.get("authorships") or [])[:20]:
            name = (authorship.get("author") or {}).get("display_name", "")
            if name:
                authors.append(name)

        year = item.get("publication_year")
        doi = item.get("doi")

        papers.append(_paper_dict(
            title=title, abstract=abstract, authors=authors,
            year=year, doi=doi, source="openalex",
            url=item.get("id"),
            citation_count=item.get("cited_by_count", 0),
        ))

    total = data.get("meta", {}).get("count", len(papers))
    return json.dumps({"papers": papers, "total": total})


# ── 5. PubMed ──────────────────────────────────────────────────────────

def search_pubmed(args: dict[str, Any], ctx: dict) -> str:
    query = args.get("query", "")
    limit = min(args.get("limit", 20), 100)
    ncbi_key = os.getenv("NCBI_API_KEY", "")

    search_params: dict[str, Any] = {
        "db": "pubmed", "term": query, "retmax": limit,
        "retmode": "json", "sort": "relevance",
    }
    if ncbi_key:
        search_params["api_key"] = ncbi_key

    search_data = _request_with_retry(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params=search_params,
    )
    if not isinstance(search_data, dict):
        return json.dumps({"papers": [], "error": "PubMed unavailable"})

    ids = search_data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return json.dumps({"papers": [], "total": 0})

    summary_params: dict[str, Any] = {
        "db": "pubmed", "id": ",".join(ids),
        "retmode": "json",
    }
    if ncbi_key:
        summary_params["api_key"] = ncbi_key

    summ_data = _request_with_retry(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
        params=summary_params,
    )
    if not isinstance(summ_data, dict):
        return json.dumps({"papers": [], "error": "PubMed summary unavailable"})

    papers = []
    result = summ_data.get("result", {})
    for pmid in ids:
        item = result.get(pmid)
        if not isinstance(item, dict):
            continue
        title = item.get("title", "")
        authors = [
            a.get("name", "") for a in (item.get("authors") or [])[:20]
            if isinstance(a, dict)
        ]
        pubdate = item.get("pubdate", "")
        year = int(pubdate[:4]) if pubdate and len(pubdate) >= 4 and pubdate[:4].isdigit() else None
        doi_list = [
            eid.get("value") for eid in (item.get("articleids") or [])
            if isinstance(eid, dict) and eid.get("idtype") == "doi"
        ]
        doi = doi_list[0] if doi_list else None

        papers.append(_paper_dict(
            title=title, abstract="",
            authors=authors, year=year, doi=doi,
            source="pubmed", url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        ))

    total = int(search_data.get("esearchresult", {}).get("count", len(papers)))
    return json.dumps({"papers": papers, "total": total})


# ── 6. CORE ────────────────────────────────────────────────────────────

def search_core(args: dict[str, Any], ctx: dict) -> str:
    query = args.get("query", "")
    limit = min(args.get("limit", 20), 100)
    api_key = os.getenv("CORE_API_KEY", "")
    if not api_key:
        return json.dumps({"papers": [], "error": "CORE_API_KEY not set"})

    data = _request_with_retry(
        "https://api.core.ac.uk/v3/search/works",
        headers={"Authorization": f"Bearer {api_key}"},
        params={"q": query, "limit": limit},
    )
    if not isinstance(data, dict):
        return json.dumps({"papers": [], "error": "CORE unavailable"})

    papers = []
    for item in data.get("results", []):
        title = item.get("title", "")
        abstract = item.get("abstract", "")
        authors = [a.get("name", "") for a in (item.get("authors") or [])[:20] if isinstance(a, dict)]
        year = item.get("yearPublished")
        doi = item.get("doi")

        # Fix operator precedence for URL extraction
        url = item.get("downloadUrl")
        if not url:
            fulltext_urls = item.get("sourceFulltextUrls")
            if isinstance(fulltext_urls, list) and fulltext_urls:
                url = fulltext_urls[0]

        papers.append(_paper_dict(
            title=title, abstract=abstract, authors=authors,
            year=year, doi=doi, source="core", url=url,
        ))

    total = data.get("totalHits", len(papers))
    return json.dumps({"papers": papers, "total": total})


# ── 7. DBLP ────────────────────────────────────────────────────────────

def search_dblp(args: dict[str, Any], ctx: dict) -> str:
    query = args.get("query", "")
    limit = min(args.get("limit", 20), 100)

    data = _request_with_retry(
        "https://dblp.org/search/publ/api",
        params={"q": query, "h": limit, "format": "json"},
    )
    if not isinstance(data, dict):
        return json.dumps({"papers": [], "error": "DBLP unavailable"})

    papers = []
    hits = data.get("result", {}).get("hits", {}).get("hit", [])
    for hit in hits:
        info = hit.get("info", {})
        title = info.get("title", "")
        authors_raw = info.get("authors", {}).get("author", [])
        if isinstance(authors_raw, dict):
            authors_raw = [authors_raw]
        authors = [a.get("text", a) if isinstance(a, dict) else str(a) for a in authors_raw]
        year = info.get("year")
        doi = info.get("doi")

        papers.append(_paper_dict(
            title=title, abstract="", authors=authors,
            year=year, doi=doi, source="dblp",
            url=info.get("url"),
        ))

    total = int(data.get("result", {}).get("hits", {}).get("@total", len(papers)))
    return json.dumps({"papers": papers, "total": total})


# ── 8. Europe PMC ──────────────────────────────────────────────────────

def search_europe_pmc(args: dict[str, Any], ctx: dict) -> str:
    query = args.get("query", "")
    limit = min(args.get("limit", 20), 100)

    data = _request_with_retry(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        params={"query": query, "pageSize": limit, "format": "json", "resultType": "core"},
    )
    if not isinstance(data, dict):
        return json.dumps({"papers": [], "error": "Europe PMC unavailable"})

    papers = []
    for item in data.get("resultList", {}).get("result", []):
        title = item.get("title", "")
        abstract = item.get("abstractText", "")
        authors = []
        for a in (item.get("authorList", {}).get("author", []) or [])[:20]:
            if isinstance(a, dict):
                name = f"{a.get('firstName', '')} {a.get('lastName', '')}".strip()
                if name:
                    authors.append(name)
        year = item.get("pubYear")
        doi = item.get("doi")
        pmid = item.get("pmid")

        papers.append(_paper_dict(
            title=title, abstract=abstract, authors=authors,
            year=year, doi=doi, source="europe_pmc",
            url=f"https://europepmc.org/article/MED/{pmid}" if pmid else None,
            citation_count=item.get("citedByCount", 0),
        ))

    total = data.get("hitCount", len(papers))
    return json.dumps({"papers": papers, "total": total})


# ── 9. BASE ────────────────────────────────────────────────────────────

def search_base(args: dict[str, Any], ctx: dict) -> str:
    query = args.get("query", "")
    limit = min(args.get("limit", 20), 100)

    resp = _request_with_retry(
        "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi",
        params={"func": "PerformSearch", "query": query, "hits": limit, "format": "json"},
    )
    if not isinstance(resp, dict):
        return json.dumps({"papers": [], "error": "BASE unavailable"})

    papers = []
    for doc in resp.get("response", {}).get("docs", []):
        title = doc.get("dctitle", "")
        if isinstance(title, list):
            title = title[0] if title else ""
        abstract = doc.get("dcsubject", "") or doc.get("dcdescription", "")
        if isinstance(abstract, list):
            abstract = " ".join(str(a) for a in abstract)
        creators = doc.get("dccreator", [])
        if isinstance(creators, str):
            creators = [creators]
        year_str = doc.get("dcyear", "")
        if isinstance(year_str, list):
            year_str = year_str[0] if year_str else ""

        # Extract DOI from relations
        doi = None
        dcrelation = doc.get("dcrelation")
        if isinstance(dcrelation, list):
            doi = next((d for d in dcrelation if isinstance(d, str) and "doi.org" in d), None)
        elif isinstance(dcrelation, str) and "doi.org" in dcrelation:
            doi = dcrelation

        papers.append(_paper_dict(
            title=str(title), abstract=abstract if isinstance(abstract, str) else "",
            authors=[str(c) for c in creators[:20]], year=year_str,
            doi=doi,
            source="base",
            url=doc.get("dclink") or doc.get("dcidentifier"),
        ))

    total = resp.get("response", {}).get("numFound", len(papers))
    return json.dumps({"papers": papers, "total": total})


# ── Batch search (all APIs) ──────────────────────────────────────────

_ALL_SEARCH_FNS = [
    ("semantic_scholar", search_semantic_scholar),
    ("arxiv", search_arxiv),
    ("crossref", search_crossref),
    ("openalex", search_openalex),
    ("pubmed", search_pubmed),
    ("core", search_core),
    ("dblp", search_dblp),
    ("europe_pmc", search_europe_pmc),
    ("base", search_base),
]


def search_all(args: dict[str, Any], ctx: dict) -> str:
    """Search all 9 academic APIs in parallel with one call.

    Returns a summary to the model (counts + top 10 papers) to save tokens.
    Full results are auto-stored in the DB by the tool dispatch layer.
    """
    query = args.get("query", "")
    limit = args.get("limit", 20)

    results: dict[str, Any] = {}
    errors: list[str] = []

    def _run(name: str, fn: Any) -> None:
        try:
            raw = fn({"query": query, "limit": limit}, ctx)
            data = json.loads(raw)
            results[name] = data.get("papers", [])
            if data.get("error"):
                errors.append(f"{name}: {data['error']}")
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    threads = []
    for name, fn in _ALL_SEARCH_FNS:
        t = threading.Thread(target=_run, args=(name, fn), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=60)

    all_papers = []
    per_source: dict[str, int] = {}
    for name, papers in results.items():
        per_source[name] = len(papers)
        all_papers.extend(papers)

    # Sort by citation count (most cited first)
    all_papers.sort(key=lambda p: p.get("citation_count", 0), reverse=True)

    # Return compact summary to model (saves tokens), full data stays in DB
    top_papers = [
        {
            "title": p.get("title", "")[:120],
            "year": p.get("year"),
            "citations": p.get("citation_count", 0),
            "source": p.get("source", ""),
            "doi": p.get("doi"),
        }
        for p in all_papers[:10]
    ]

    # Store full papers via _papers_for_storage (picked up by auto-store)
    _search_all_full_results.clear()
    _search_all_full_results.extend(all_papers)

    return json.dumps({
        "total": len(all_papers),
        "per_source": per_source,
        "top_papers": top_papers,
        "errors": errors,
        "note": f"All {len(all_papers)} papers stored in database. Top 10 shown by citation count.",
    })


# Temp storage for search_all full results (consumed by auto-store in dispatch)
_search_all_full_results: list[dict[str, Any]] = []
