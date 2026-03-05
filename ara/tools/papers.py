# Location: ara/tools/papers.py
# Purpose: Paper reading and similarity search
# Functions: fetch_fulltext, read_paper, search_similar
# Calls: httpx for Unpaywall API, ARADB for database queries
# Imports: json, httpx, typing

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from ara.db import ARADB


def fetch_fulltext(doi: str) -> str:
    """Fetch open access version of paper via Unpaywall API."""
    try:
        url = f"https://api.unpaywall.org/v2/{doi}"
        params = {"email": "ara@research.local"}

        response = httpx.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if data.get("is_oa"):
            oa_location = data.get("best_oa_location", {})
            if oa_location:
                return json.dumps({
                    "doi": doi,
                    "found": True,
                    "url": oa_location.get("url", ""),
                    "host_type": oa_location.get("host_type", ""),
                    "version": oa_location.get("version", "")
                })

        return json.dumps({
            "doi": doi,
            "found": False,
            "message": "No open access version found"
        })
    except httpx.HTTPError as e:
        return json.dumps({"doi": doi, "error": f"Unpaywall API error: {str(e)}"})
    except Exception as e:
        return json.dumps({"doi": doi, "error": f"Fetch fulltext error: {str(e)}"})


def read_paper(paper_id: int, db: ARADB) -> str:
    """Read paper from database and return formatted summary."""
    try:
        paper = db.get_paper(paper_id)
        if not paper:
            return json.dumps({"error": f"Paper {paper_id} not found"})

        result = {
            "paper_id": paper["paper_id"],
            "title": paper["title"],
            "abstract": paper.get("abstract", ""),
            "authors": json.loads(paper["authors"]) if isinstance(paper["authors"], str) else paper.get("authors", []),
            "year": paper.get("publication_year"),
            "doi": paper.get("doi"),
            "citation_count": paper.get("citation_count", 0),
            "url": paper.get("url", ""),
            "retraction_status": paper.get("retraction_status", "none"),
        }
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": f"Read paper error: {str(e)}"})


def search_similar(query_text: str, session_id: int, db: ARADB, limit: int = 5) -> str:
    """Search for papers similar to query text in current session."""
    try:
        papers = db.get_papers(session_id)
        # Sort by relevance_score descending, take top N
        scored = [p for p in papers if p.get("relevance_score") is not None]
        scored.sort(key=lambda p: p.get("relevance_score", 0), reverse=True)
        top = scored[:limit] if scored else papers[:limit]

        results = []
        for p in top:
            results.append({
                "paper_id": p["paper_id"],
                "title": p["title"],
                "abstract": p.get("abstract", ""),
                "authors": json.loads(p["authors"]) if isinstance(p["authors"], str) else p.get("authors", []),
                "year": p.get("publication_year"),
                "doi": p.get("doi"),
                "citation_count": p.get("citation_count", 0),
                "relevance_score": p.get("relevance_score"),
            })
        return json.dumps(results)
    except Exception as e:
        return json.dumps({"error": f"Search similar error: {str(e)}"})
