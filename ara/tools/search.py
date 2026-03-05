# Location: ara/tools/search.py
# Purpose: Academic API search implementations (9 sources)
# Functions: search_semantic_scholar, search_arxiv, search_crossref, search_openalex, search_pubmed, search_core, search_dblp, search_europe_pmc, search_base
# Calls: httpx for HTTP requests
# Imports: json, httpx, xml.etree.ElementTree

from __future__ import annotations

import json
import threading
import time as _time
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from ..logging import get_logger

_log = get_logger("search")

# --- Semantic Scholar API key & rate limiter ---
# TODO: Remove hardcoded key when credentials are loaded from user config/env.
# Rate limit: 1 request/second cumulative across ALL S2 endpoints.
_S2_API_KEY = "ps3k5hrLaT9v0BLcXbNCh4aqRfX7eXqW2n6kIyyg"

_s2_lock = threading.Lock()
_s2_last_call: float = 0.0


def _s2_throttle() -> None:
    """Enforce 1 request/second for Semantic Scholar API (cumulative across all endpoints)."""
    global _s2_last_call
    with _s2_lock:
        now = _time.monotonic()
        elapsed = now - _s2_last_call
        if elapsed < 1.05:  # 1.05s to add small safety margin
            _time.sleep(1.05 - elapsed)
        _s2_last_call = _time.monotonic()


def get_s2_headers() -> dict[str, str]:
    """Return Semantic Scholar headers with API key."""
    return {"x-api-key": _S2_API_KEY}


def _request_with_retry(url: str, params: dict, headers: dict | None = None,
                        max_retries: int = 3, timeout: int = 30) -> httpx.Response:
    """HTTP GET with retry on 429/5xx. Backoff: 3s, 8s, 15s."""
    delays = [3, 8, 15]
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = httpx.get(url, params=params, headers=headers,
                                 timeout=timeout, follow_redirects=True)
            if response.status_code == 429 and attempt < max_retries:
                wait = delays[min(attempt, len(delays) - 1)]
                _log.info("Rate limited by %s, retrying in %ds (attempt %d)", url.split("/")[2], wait, attempt + 1)
                _time.sleep(wait)
                continue
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 500, 502, 503) and attempt < max_retries:
                wait = delays[min(attempt, len(delays) - 1)]
                _log.info("HTTP %d from %s, retrying in %ds", e.response.status_code, url.split("/")[2], wait)
                _time.sleep(wait)
                last_exc = e
                continue
            raise
        except httpx.HTTPError as e:
            last_exc = e
            if attempt < max_retries:
                wait = delays[min(attempt, len(delays) - 1)]
                _time.sleep(wait)
                continue
            raise
    raise last_exc  # type: ignore[misc]


def search_semantic_scholar(query: str, limit: int = 10) -> str:
    """Search Semantic Scholar for papers.

    Args:
        query: Search query string
        limit: Maximum number of results (default 10)

    Returns:
        JSON string of standardized paper results
    """
    try:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            "query": query,
            "limit": min(limit, 20),  # Cap to conserve 1 req/sec budget
            "fields": "title,abstract,authors,year,citationCount,externalIds,url"
        }
        _s2_throttle()
        response = _request_with_retry(url, params=params, headers=get_s2_headers())
        data = response.json()

        results = []
        for paper in data.get("data", []):
            doi = None
            if paper.get("externalIds"):
                doi = paper["externalIds"].get("DOI")

            result = {
                "title": paper.get("title", ""),
                "abstract": paper.get("abstract", ""),
                "authors": [a.get("name", "") for a in paper.get("authors", [])],
                "year": paper.get("year"),
                "doi": doi,
                "source": "semantic_scholar",
                "url": paper.get("url", ""),
                "citation_count": paper.get("citationCount", 0)
            }
            results.append(result)

        return json.dumps(results)
    except httpx.HTTPError as e:
        _log.warning("Semantic Scholar API error: %s (query=%r)", e, query)
        return json.dumps([{"error": f"Semantic Scholar API error: {str(e)}"}])
    except Exception as e:
        _log.error("Semantic Scholar unexpected error: %s", e, exc_info=True)
        return json.dumps([{"error": f"Semantic Scholar error: {str(e)}"}])


def search_arxiv(query: str, limit: int = 10) -> str:
    """Search arXiv for preprints.

    Args:
        query: Search query string
        limit: Maximum number of results (default 10)

    Returns:
        JSON string of standardized paper results
    """
    try:
        url = "https://export.arxiv.org/api/query"
        params = {
            "search_query": f"all:{query}",
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending"
        }

        response = _request_with_retry(url, params=params)

        root = ET.fromstring(response.content)
        # Handle namespaces
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        results = []
        for entry in root.findall("atom:entry", ns):
            title = entry.find("atom:title", ns)
            summary = entry.find("atom:summary", ns)
            published = entry.find("atom:published", ns)

            # Extract authors
            authors = []
            for author in entry.findall("atom:author", ns):
                name = author.find("atom:name", ns)
                if name is not None:
                    authors.append(name.text)

            # Extract arXiv ID and URL
            arxiv_id = ""
            arxiv_url = ""
            for link in entry.findall("atom:id", ns):
                arxiv_id = link.text
                arxiv_url = arxiv_id

            # Extract year from published date
            year = None
            if published is not None and published.text:
                year = int(published.text[:4])

            result = {
                "title": title.text if title is not None else "",
                "abstract": summary.text.strip() if summary is not None else "",
                "authors": authors,
                "year": year,
                "doi": None,
                "source": "arxiv",
                "url": arxiv_url,
                "citation_count": 0
            }
            results.append(result)

        return json.dumps(results)
    except httpx.HTTPError as e:
        _log.warning("arXiv API error: %s (query=%r)", e, query)
        return json.dumps([{"error": f"arXiv API error: {str(e)}"}])
    except Exception as e:
        _log.error("arXiv unexpected error: %s", e, exc_info=True)
        return json.dumps([{"error": f"arXiv error: {str(e)}"}])


def search_crossref(query: str, limit: int = 10) -> str:
    """Search Crossref for papers.

    Args:
        query: Search query string
        limit: Maximum number of results (default 10)

    Returns:
        JSON string of standardized paper results
    """
    try:
        url = "https://api.crossref.org/works"
        params = {
            "query": query,
            "rows": limit
        }
        headers = {
            "User-Agent": "ARA/0.1 (Academic Research Agent)"
        }

        response = _request_with_retry(url, params=params, headers=headers)
        data = response.json()

        results = []
        for item in data.get("message", {}).get("items", []):
            # Extract authors
            authors = []
            for author in item.get("author", []):
                name_parts = []
                if "given" in author:
                    name_parts.append(author["given"])
                if "family" in author:
                    name_parts.append(author["family"])
                if name_parts:
                    authors.append(" ".join(name_parts))

            result = {
                "title": item.get("title", [""])[0] if item.get("title") else "",
                "abstract": item.get("abstract", ""),
                "authors": authors,
                "year": item.get("issued", {}).get("date-parts", [[None]])[0][0],
                "doi": item.get("DOI"),
                "source": "crossref",
                "url": item.get("URL", ""),
                "citation_count": item.get("is-referenced-by-count", 0)
            }
            results.append(result)

        return json.dumps(results)
    except httpx.HTTPError as e:
        _log.warning("CrossRef API error: %s (query=%r)", e, query)
        return json.dumps([{"error": f"Crossref API error: {str(e)}"}])
    except Exception as e:
        _log.error("CrossRef unexpected error: %s", e, exc_info=True)
        return json.dumps([{"error": f"Crossref error: {str(e)}"}])


def search_openalex(query: str, limit: int = 10) -> str:
    """Search OpenAlex for papers.

    Args:
        query: Search query string
        limit: Maximum number of results (default 10)

    Returns:
        JSON string of standardized paper results
    """
    try:
        url = "https://api.openalex.org/works"
        params = {
            "search": query,
            "per_page": limit
        }
        headers = {
            "User-Agent": "ARA/0.1 (Academic Research Agent; mailto:ara@research.local)"
        }

        response = _request_with_retry(url, params=params, headers=headers)
        data = response.json()

        results = []
        for work in data.get("results", []):
            # Extract authors
            authors = []
            for author in work.get("authorships", []):
                if author.get("author", {}).get("display_name"):
                    authors.append(author["author"]["display_name"])

            result = {
                "title": work.get("title", ""),
                "abstract": work.get("abstract", ""),
                "authors": authors,
                "year": work.get("publication_year"),
                "doi": work.get("doi", "").replace("https://doi.org/", "") if work.get("doi") else None,
                "source": "openalex",
                "url": work.get("canonical_url", ""),
                "citation_count": work.get("cited_by_count", 0)
            }
            results.append(result)

        return json.dumps(results)
    except httpx.HTTPError as e:
        _log.warning("OpenAlex API error: %s (query=%r)", e, query)
        return json.dumps([{"error": f"OpenAlex API error: {str(e)}"}])
    except Exception as e:
        _log.error("OpenAlex unexpected error: %s", e, exc_info=True)
        return json.dumps([{"error": f"OpenAlex error: {str(e)}"}])


def search_pubmed(query: str, limit: int = 10) -> str:
    """Search PubMed for papers (two-step: esearch then efetch).

    Args:
        query: Search query string
        limit: Maximum number of results (default 10)

    Returns:
        JSON string of standardized paper results
    """
    try:
        # Step 1: Search for PMIDs
        esearch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        esearch_params = {
            "db": "pubmed",
            "term": query,
            "retmax": limit,
            "retmode": "json"
        }

        esearch_response = _request_with_retry(esearch_url, params=esearch_params)
        esearch_data = esearch_response.json()

        pmids = esearch_data.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return json.dumps([])

        # Step 2: Get summaries via esummary (returns proper JSON, unlike efetch)
        esummary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        esummary_params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "json"
        }

        esummary_response = _request_with_retry(esummary_url, params=esummary_params)
        esummary_data = esummary_response.json()

        results = []
        result_dict = esummary_data.get("result", {})
        for pmid in pmids:
            article_data = result_dict.get(str(pmid), {})
            if not article_data or not isinstance(article_data, dict):
                continue

            # Extract authors
            authors = []
            for author in article_data.get("authors", []):
                if "name" in author:
                    authors.append(author["name"])

            # Extract year from pubdate (e.g., "2024 Jan 15")
            year = None
            pubdate = article_data.get("pubdate", "")
            if pubdate and len(pubdate) >= 4:
                try:
                    year = int(pubdate[:4])
                except ValueError:
                    pass

            # Extract DOI from articleids
            doi = None
            for aid in article_data.get("articleids", []):
                if aid.get("idtype") == "doi":
                    doi = aid.get("value")
                    break

            result = {
                "title": article_data.get("title", ""),
                "abstract": "",  # esummary doesn't include abstracts
                "authors": authors,
                "year": year,
                "doi": doi,
                "source": "pubmed",
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "citation_count": 0
            }
            results.append(result)

        return json.dumps(results)
    except httpx.HTTPError as e:
        _log.warning("PubMed API error: %s (query=%r)", e, query)
        return json.dumps([{"error": f"PubMed API error: {str(e)}"}])
    except Exception as e:
        _log.error("PubMed unexpected error: %s", e, exc_info=True)
        return json.dumps([{"error": f"PubMed error: {str(e)}"}])


def search_core(query: str, limit: int = 10) -> str:
    """Search CORE for open access papers.

    Args:
        query: Search query string
        limit: Maximum number of results (default 10)

    Returns:
        JSON string of standardized paper results
    """
    try:
        # CORE requires API key; if not available, return stub
        # For now, return placeholder message
        return json.dumps([
            {
                "error": "CORE API key not configured. Set CORE_API_KEY environment variable to enable this source."
            }
        ])
    except Exception as e:
        return json.dumps([{"error": f"CORE error: {str(e)}"}])


def search_dblp(query: str, limit: int = 10) -> str:
    """Search DBLP for computer science papers.

    Args:
        query: Search query string
        limit: Maximum number of results (default 10)

    Returns:
        JSON string of standardized paper results
    """
    try:
        url = "https://dblp.org/search/publ/api"
        params = {
            "q": query,
            "h": limit,
            "format": "json"
        }

        response = _request_with_retry(url, params=params)
        data = response.json()

        results = []
        for hit in data.get("result", {}).get("hits", {}).get("hit", []):
            info = hit.get("info", {})

            # Extract authors
            authors = []
            authors_obj = info.get("authors", {})
            if isinstance(authors_obj, dict) and "author" in authors_obj:
                author_list = authors_obj["author"]
                if not isinstance(author_list, list):
                    author_list = [author_list]
                authors = [a.get("text", "") for a in author_list if isinstance(a, dict)]

            # Extract year
            year = None
            year_str = info.get("year", "")
            if year_str:
                try:
                    year = int(year_str)
                except (ValueError, TypeError):
                    pass

            result = {
                "title": info.get("title", ""),
                "abstract": "",
                "authors": authors,
                "year": year,
                "doi": None,
                "source": "dblp",
                "url": info.get("url", ""),
                "citation_count": 0
            }
            results.append(result)

        return json.dumps(results)
    except httpx.HTTPError as e:
        _log.warning("DBLP API error: %s (query=%r)", e, query)
        return json.dumps([{"error": f"DBLP API error: {str(e)}"}])
    except Exception as e:
        _log.error("DBLP unexpected error: %s", e, exc_info=True)
        return json.dumps([{"error": f"DBLP error: {str(e)}"}])


def search_europe_pmc(query: str, limit: int = 10) -> str:
    """Search Europe PMC for biomedical papers.

    Args:
        query: Search query string
        limit: Maximum number of results (default 10)

    Returns:
        JSON string of standardized paper results
    """
    try:
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        params = {
            "query": query,
            "pageSize": limit,
            "format": "json"
        }

        response = _request_with_retry(url, params=params)
        data = response.json()

        results = []
        for result in data.get("resultList", {}).get("result", []):
            # Extract authors
            authors = []
            for author in result.get("authorList", {}).get("author", []):
                name_parts = []
                if "firstName" in author:
                    name_parts.append(author["firstName"])
                if "lastName" in author:
                    name_parts.append(author["lastName"])
                if name_parts:
                    authors.append(" ".join(name_parts))

            result_obj = {
                "title": result.get("title", ""),
                "abstract": result.get("abstractText", ""),
                "authors": authors,
                "year": result.get("pubYear"),
                "doi": result.get("doi"),
                "source": "europe_pmc",
                "url": result.get("pmcid") and f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{result['pmcid']}/",
                "citation_count": 0
            }
            results.append(result_obj)

        return json.dumps(results)
    except httpx.HTTPError as e:
        _log.warning("Europe PMC API error: %s (query=%r)", e, query)
        return json.dumps([{"error": f"Europe PMC API error: {str(e)}"}])
    except Exception as e:
        _log.error("Europe PMC unexpected error: %s", e, exc_info=True)
        return json.dumps([{"error": f"Europe PMC error: {str(e)}"}])


def search_base(query: str, limit: int = 10) -> str:
    """Search BASE (Bielefeld Academic Search Engine).

    Args:
        query: Search query string
        limit: Maximum number of results (default 10)

    Returns:
        JSON string of standardized paper results
    """
    try:
        url = "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi"
        params = {
            "func": "PerformSearch",
            "query": query,
            "hits": limit,
            "format": "json"
        }

        response = _request_with_retry(url, params=params)
        data = response.json()

        results = []
        for hit in data.get("data", []):
            result = {
                "title": hit.get("title", ""),
                "abstract": hit.get("abstract", ""),
                "authors": hit.get("authors", []),
                "year": hit.get("year"),
                "doi": hit.get("doi"),
                "source": "base",
                "url": hit.get("url", ""),
                "citation_count": 0
            }
            results.append(result)

        return json.dumps(results)
    except httpx.HTTPError as e:
        _log.warning("BASE API error: %s (query=%r)", e, query)
        return json.dumps([{"error": f"BASE API error: {str(e)}"}])
    except Exception as e:
        _log.error("BASE unexpected error: %s", e, exc_info=True)
        return json.dumps([{"error": f"BASE error: {str(e)}"}])
