# Location: ara/tools/papers.py
# Purpose: Paper management tools — fetch, read, similarity search
# Functions: fetch_fulltext, read_paper, search_similar
# Calls: httpx, db.py
# Imports: json, re, httpx

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

_log = logging.getLogger(__name__)
_MAX_DOWNLOAD_BYTES = 5_000_000  # 5MB limit for fulltext downloads
_MAX_FULLTEXT_CHARS = 5000  # Truncate stored full text to 5KB


def fetch_fulltext(args: dict[str, Any], ctx: dict) -> str:
    doi = args.get("doi", "").strip()
    if not doi:
        return json.dumps({"error": "DOI is required"})

    # Try Unpaywall to find OA URL
    pdf_url = None
    is_oa = False
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
            is_oa = data.get("is_oa", False)
    except Exception as exc:
        _log.warning("Unpaywall failed for %s: %s", doi, exc)

    if not pdf_url:
        return json.dumps({"status": "not_found", "doi": doi, "message": "No open access full text available"})

    # Try to download and extract text content
    full_text = None
    try:
        with httpx.stream("GET", pdf_url, timeout=30, follow_redirects=True) as dl_resp:
            if dl_resp.status_code != 200:
                pass
            else:
                content_type = dl_resp.headers.get("content-type", "")
                content_length = int(dl_resp.headers.get("content-length", 0))

                if content_length > _MAX_DOWNLOAD_BYTES:
                    _log.warning("Fulltext too large (%d bytes), skipping download", content_length)
                elif "html" in content_type:
                    # Read up to limit
                    chunks = []
                    total = 0
                    for chunk in dl_resp.iter_bytes(chunk_size=8192):
                        chunks.append(chunk)
                        total += len(chunk)
                        if total > _MAX_DOWNLOAD_BYTES:
                            break
                    html = b"".join(chunks).decode("utf-8", errors="replace")
                    # Strip scripts, styles, tags
                    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
                    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
                    text = re.sub(r"<[^>]+>", " ", text)
                    text = re.sub(r"\s+", " ", text).strip()
                    if len(text) > 200:
                        full_text = text[:_MAX_FULLTEXT_CHARS]
    except Exception as exc:
        _log.warning("Failed to download fulltext from %s: %s", str(pdf_url)[:80], exc)

    # Store in DB
    db = ctx.get("db")
    if db:
        db.update_paper_fulltext(doi=doi, url=pdf_url)
        if full_text:
            db.store_fulltext_content(doi=doi, text=full_text[:_MAX_FULLTEXT_CHARS])

    result: dict[str, Any] = {
        "status": "found",
        "doi": doi,
        "url": pdf_url,
        "is_oa": is_oa,
    }
    if full_text:
        result["text_preview"] = full_text[:3000]
        result["text_length"] = len(full_text)
    else:
        result["note"] = "URL found but text extraction not available (PDF binary). URL stored for reference."

    return json.dumps(result)


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

    # Truncate full_text to prevent context bloat
    if paper.get("full_text") and len(paper["full_text"]) > _MAX_FULLTEXT_CHARS:
        paper["full_text"] = paper["full_text"][:_MAX_FULLTEXT_CHARS] + "... [truncated]"

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

    papers = db.search_papers_by_keyword(session_id=session_id, keyword=text, limit=limit)
    return json.dumps({"papers": papers, "method": "keyword"}, default=str)
