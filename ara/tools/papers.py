# Location: ara/tools/papers.py
# Purpose: Paper management tools — fetch, read, similarity search
# Functions: fetch_fulltext, read_paper, search_similar
# Calls: httpx, db.py
# Imports: json, httpx

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

_log = logging.getLogger(__name__)


def fetch_fulltext(args: dict[str, Any], ctx: dict) -> str:
    doi = args.get("doi", "").strip()
    if not doi:
        return json.dumps({"error": "DOI is required"})

    # Try Unpaywall
    try:
        resp = httpx.get(
            f"https://api.unpaywall.org/v2/{doi}",
            params={"email": "ara-research@example.com"},
            timeout=15, follow_redirects=True,
        )
        if resp.status_code == 200:
            data = resp.json()
            best_oa = data.get("best_oa_location") or {}
            pdf_url = best_oa.get("url_for_pdf") or best_oa.get("url")
            if pdf_url:
                # Store in DB if available
                db = ctx.get("db")
                if db:
                    db.update_paper_fulltext(doi=doi, url=pdf_url)
                return json.dumps({
                    "status": "found",
                    "doi": doi,
                    "url": pdf_url,
                    "is_oa": data.get("is_oa", False),
                })
    except Exception as exc:
        _log.warning("Unpaywall failed for %s: %s", doi, exc)

    return json.dumps({"status": "not_found", "doi": doi, "message": "No open access full text available"})


def read_paper(args: dict[str, Any], ctx: dict) -> str:
    paper_id = args.get("paper_id")
    if paper_id is None:
        return json.dumps({"error": "paper_id is required"})

    db = ctx.get("db")
    if not db:
        return json.dumps({"error": "Database not available"})

    paper = db.get_paper(paper_id)
    if not paper:
        return json.dumps({"error": f"Paper {paper_id} not found"})

    return json.dumps(paper, default=str)


def search_similar(args: dict[str, Any], ctx: dict) -> str:
    text = args.get("text", "")
    limit = args.get("limit", 10)

    if not text:
        return json.dumps({"error": "text is required"})

    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    # For now, do keyword-based search until embeddings are wired
    papers = db.search_papers_by_keyword(session_id=session_id, keyword=text, limit=limit)
    return json.dumps({"papers": papers, "method": "keyword"}, default=str)
