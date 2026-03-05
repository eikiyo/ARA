# Location: ara/tools/search.py
# Purpose: 9 academic API search implementations
# Functions: search_semantic_scholar, search_arxiv, search_crossref, etc.
# Calls: httpx for HTTP requests, xml.etree for XML parsing
# Imports: httpx, json, os, time, xml.etree.ElementTree

from __future__ import annotations

import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote_plus

import httpx

_log = logging.getLogger(__name__)
_TIMEOUT = 30
_MAX_RETRIES = 3
_s2_last_call = 0.0


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


def _paper_dict(
    title: str, abstract: str | None, authors: list[str],
    year: int | None, doi: str | None, source: str,
    url: str | None = None, citation_count: int = 0,
) -> dict[str, Any]:
    return {
        "title": title.strip() if title else "",
        "abstract": (abstract or "").strip()[:2000],
        "authors": authors[:20],
        "year": year,
        "doi": doi.strip().removeprefix("https://doi.org/") if doi else None,
        "source": source,
        "url": url,
        "citation_count": citation_count,
    }


# ── 1. Semantic Scholar ────────────────────────────────────────────────

def search_semantic_scholar(args: dict[str, Any], ctx: dict) -> str:
    global _s2_last_call
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
            # arXiv IDs → DOI mapping not always available
            doi_el = entry.find('.//atom:link[@title="doi"]', ns)
            doi = doi_el.get("href", "").removeprefix("http://dx.doi.org/") if doi_el is not None else None

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
        title = (item.get("title") or [""])[0]
        abstract = item.get("abstract", "")
        # Strip HTML from abstract
        if "<" in abstract:
            import re
            abstract = re.sub(r"<[^>]+>", "", abstract)
        authors = [
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in (item.get("author") or [])[:20]
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
        # OpenAlex abstracts come as inverted index — reconstruct
        abstract = ""
        inv_idx = item.get("abstract_inverted_index")
        if inv_idx and isinstance(inv_idx, dict):
            words: list[tuple[int, str]] = []
            for word, positions in inv_idx.items():
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
        doi = (item.get("doi") or "").removeprefix("https://doi.org/") or None

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

    # Fetch summaries
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
        authors = [a.get("name", "") for a in (item.get("authors") or [])[:20]]
        pubdate = item.get("pubdate", "")
        year = int(pubdate[:4]) if pubdate and len(pubdate) >= 4 and pubdate[:4].isdigit() else None
        doi_list = [eid.get("value") for eid in (item.get("articleids") or []) if eid.get("idtype") == "doi"]
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
        doi = (item.get("doi") or "").removeprefix("https://doi.org/") or None

        papers.append(_paper_dict(
            title=title, abstract=abstract, authors=authors,
            year=year, doi=doi, source="core",
            url=item.get("downloadUrl") or item.get("sourceFulltextUrls", [None])[0] if item.get("sourceFulltextUrls") else None,
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
        year_str = info.get("year", "")
        year = int(year_str) if year_str and year_str.isdigit() else None
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
            name = f"{a.get('firstName', '')} {a.get('lastName', '')}".strip()
            if name:
                authors.append(name)
        year_str = item.get("pubYear", "")
        year = int(year_str) if year_str and year_str.isdigit() else None
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
            abstract = " ".join(abstract)
        creators = doc.get("dccreator", [])
        if isinstance(creators, str):
            creators = [creators]
        year_str = (doc.get("dcyear") or "")
        if isinstance(year_str, list):
            year_str = year_str[0] if year_str else ""
        year = int(year_str) if year_str and str(year_str).isdigit() else None
        doi = doc.get("dcrelation", [None])
        if isinstance(doi, list):
            doi = next((d for d in doi if d and "doi.org" in str(d)), None)
        if isinstance(doi, str):
            doi = doi.removeprefix("https://doi.org/").removeprefix("http://dx.doi.org/")

        papers.append(_paper_dict(
            title=title, abstract=abstract if isinstance(abstract, str) else "",
            authors=creators[:20], year=year,
            doi=doi if isinstance(doi, str) else None,
            source="base",
            url=doc.get("dclink") or doc.get("dcidentifier"),
        ))

    total = resp.get("response", {}).get("numFound", len(papers))
    return json.dumps({"papers": papers, "total": total})
