# Location: ara/tools/search.py
# Purpose: Academic API search implementations (9 sources)
# Functions: search_semantic_scholar, search_arxiv, search_crossref, search_openalex, search_pubmed, search_core, search_dblp, search_europe_pmc, search_base
# Calls: httpx for HTTP requests
# Imports: json, httpx, xml.etree.ElementTree

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from ..logging import get_logger

_log = get_logger("search")


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
            "limit": limit,
            "fields": "title,abstract,authors,year,citationCount,externalIds,url"
        }

        response = httpx.get(url, params=params, timeout=30)
        response.raise_for_status()
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
        url = "http://export.arxiv.org/api/query"
        params = {
            "search_query": f"all:{query}",
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending"
        }

        response = httpx.get(url, params=params, timeout=30)
        response.raise_for_status()

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

        response = httpx.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
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

        response = httpx.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
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
        # Step 1: Search
        esearch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        esearch_params = {
            "db": "pubmed",
            "term": query,
            "retmax": limit,
            "retmode": "json"
        }

        esearch_response = httpx.get(esearch_url, params=esearch_params, timeout=30)
        esearch_response.raise_for_status()
        esearch_data = esearch_response.json()

        pmids = esearch_data.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return json.dumps([])

        # Step 2: Fetch details
        efetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        efetch_params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "json"
        }

        efetch_response = httpx.get(efetch_url, params=efetch_params, timeout=30)
        efetch_response.raise_for_status()
        efetch_data = efetch_response.json()

        results = []
        for article in efetch_data.get("result", {}).get("uids", []):
            if article == "uids":
                continue

            article_data = efetch_data.get("result", {}).get(str(article), {})

            # Extract authors
            authors = []
            for author in article_data.get("authors", []):
                if "name" in author:
                    authors.append(author["name"])

            # Get PMID for URL
            pmid = article

            result = {
                "title": article_data.get("title", ""),
                "abstract": article_data.get("abstract", ""),
                "authors": authors,
                "year": article_data.get("pubdate_year"),
                "doi": article_data.get("doi"),
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

        response = httpx.get(url, params=params, timeout=30)
        response.raise_for_status()
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

            # Extract year from key or other field
            year = None
            key = info.get("key", "")
            if "/" in key:
                year_str = key.split("/")[-1]
                try:
                    year = int(year_str)
                except:
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

        response = httpx.get(url, params=params, timeout=30)
        response.raise_for_status()
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

        response = httpx.get(url, params=params, timeout=30)
        response.raise_for_status()
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
