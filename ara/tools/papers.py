# Location: ara/tools/papers.py
# Purpose: Paper management tools — fetch, read, similarity search
# Functions: fetch_fulltext, read_paper, search_similar
# Calls: httpx, db.py
# Imports: json, math, os, re, httpx

from __future__ import annotations

import json
import logging
import math
import os
import re
from typing import Any

import httpx

_log = logging.getLogger(__name__)
_MAX_DOWNLOAD_BYTES = 10_000_000  # 10MB limit for fulltext downloads
_MAX_FULLTEXT_CHARS = 25000  # Store up to ~5 pages of full text for deep analysis


def _extract_text_from_pdf(pdf_bytes: bytes, max_chars: int = 25000) -> str | None:
    """Extract text from PDF using pymupdf if available, fallback to basic extraction."""
    # Try pymupdf (fitz) first — best quality
    try:
        import fitz  # pymupdf
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text_parts = []
        total_chars = 0
        for page in doc:
            page_text = page.get_text("text")
            text_parts.append(page_text)
            total_chars += len(page_text)
            if total_chars > max_chars:
                break
        doc.close()
        full_text = "\n".join(text_parts)
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars]
        return full_text if len(full_text) > 200 else None
    except ImportError:
        _log.debug("pymupdf not installed, trying pdfplumber")
    except Exception as exc:
        _log.warning("pymupdf extraction failed: %s", exc)

    # Try pdfplumber as fallback
    try:
        import pdfplumber
        import io
        pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
        text_parts = []
        total_chars = 0
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
            total_chars += len(page_text)
            if total_chars > max_chars:
                break
        pdf.close()
        full_text = "\n".join(text_parts)
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars]
        return full_text if len(full_text) > 200 else None
    except ImportError:
        _log.debug("pdfplumber not installed, PDF text extraction unavailable")
    except Exception as exc:
        _log.warning("pdfplumber extraction failed: %s", exc)

    return None


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
            if dl_resp.status_code == 200:
                content_type = dl_resp.headers.get("content-type", "")
                content_length = int(dl_resp.headers.get("content-length", 0))

                if content_length > _MAX_DOWNLOAD_BYTES:
                    _log.warning("Fulltext too large (%d bytes), skipping download", content_length)
                else:
                    # Download content
                    chunks = []
                    total = 0
                    for chunk in dl_resp.iter_bytes(chunk_size=8192):
                        chunks.append(chunk)
                        total += len(chunk)
                        if total > _MAX_DOWNLOAD_BYTES:
                            break
                    raw_bytes = b"".join(chunks)

                    if "pdf" in content_type or pdf_url.endswith(".pdf"):
                        # PDF extraction
                        full_text = _extract_text_from_pdf(raw_bytes, _MAX_FULLTEXT_CHARS)
                        if not full_text:
                            _log.info("PDF text extraction failed for %s, URL stored for reference", doi)
                    elif "html" in content_type:
                        # HTML extraction (existing logic, enhanced)
                        html_text = raw_bytes.decode("utf-8", errors="replace")
                        text = re.sub(r"<script[^>]*>.*?</script>", "", html_text, flags=re.DOTALL)
                        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
                        text = re.sub(r"<[^>]+>", " ", text)
                        text = re.sub(r"\s+", " ", text).strip()
                        if len(text) > 200:
                            full_text = text[:_MAX_FULLTEXT_CHARS]
                    elif "xml" in content_type:
                        # XML extraction (common for PubMed/Europe PMC)
                        xml_text = raw_bytes.decode("utf-8", errors="replace")
                        text = re.sub(r"<[^>]+>", " ", xml_text)
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


_MAX_READ_PAPER_CHARS = 5000  # Default read_paper output cap — enough for abstract + metadata


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

    include_fulltext = args.get("include_fulltext", False)

    if not include_fulltext:
        # Default: return metadata + abstract only (keeps context small)
        paper.pop("full_text", None)
        abstract = paper.get("abstract") or ""
        if len(abstract) > 2000:
            paper["abstract"] = abstract[:2000] + "... [truncated]"
    else:
        # Full text requested — cap at limit
        if paper.get("full_text") and len(paper["full_text"]) > _MAX_FULLTEXT_CHARS:
            paper["full_text"] = paper["full_text"][:_MAX_FULLTEXT_CHARS] + "... [truncated]"

    return json.dumps(paper, default=str)


_MAX_LIST_PAPERS_CHARS = 80_000  # Cap list_papers output to ~80K chars to prevent context flooding


def list_papers(args: dict[str, Any], ctx: dict) -> str:
    """List all papers in the session with metadata for triage/ranking. Returns compact summaries."""
    db = ctx.get("db")
    session_id = ctx.get("session_id")

    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    limit = min(args.get("limit", 200), 200)  # Hard cap at 200 per call
    offset = args.get("offset", 0)

    # Allow filtering to only selected papers
    selected_only = args.get("selected_only", False)
    where = "WHERE session_id = ?"
    params: list[Any] = [session_id]
    if selected_only:
        where += " AND selected_for_deep_read = 1"

    # Sort by relevance_score if available, fallback to citation_count
    order = "ORDER BY COALESCE(relevance_score, 0) DESC, citation_count DESC"

    rows = db._conn.execute(
        f"SELECT paper_id, title, abstract, authors, year, doi, source, citation_count, relevance_score, selected_for_deep_read "
        f"FROM papers {where} {order} LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()

    compact = args.get("compact", False)  # Compact mode for writer: less abstract, more citation info
    papers = []
    skipped_no_year = 0
    total_chars = 0
    for row in rows:
        d = dict(row)

        # Filter out papers with no year — these produce "(Author, n.d.)" citations
        if not d.get("year"):
            skipped_no_year += 1
            continue

        authors_raw = json.loads(d.get("authors") or "[]")
        # Normalize author names to strings for easy reading
        author_names = []
        for a in authors_raw:
            if isinstance(a, str):
                author_names.append(a)
            elif isinstance(a, dict):
                name = a.get("name") or a.get("family", "")
                if a.get("given"):
                    name = f"{a['given']} {name}"
                author_names.append(name)
        d["authors"] = author_names
        # In compact mode, skip abstracts to save tokens
        if compact:
            d.pop("abstract", None)
        else:
            abstract = d.get("abstract") or ""
            if len(abstract) > 300:
                abstract = abstract[:300] + "..."
            d["abstract"] = abstract
        papers.append(d)
        # Estimate chars for this paper entry
        total_chars += len(str(d))
        if total_chars > _MAX_LIST_PAPERS_CHARS:
            _log.warning("list_papers output cap reached at %d papers (of %d rows)", len(papers), len(rows))
            break

    total = db.paper_count(session_id)

    result: dict[str, Any] = {
        "papers": papers,
        "total": total,
        "returned": len(papers),
        "offset": offset,
    }
    if skipped_no_year > 0:
        result["skipped_no_year"] = skipped_no_year
        result["note"] = f"{skipped_no_year} papers excluded (missing publication year). Showing {len(papers)} of {total} total."
    elif len(papers) < total:
        result["note"] = f"Showing {len(papers)} of {total} papers. Use offset parameter to paginate."

    return json.dumps(result, default=str)


def rate_papers(args: dict[str, Any], ctx: dict) -> str:
    """Batch-rate papers for relevance and mark selected for deep reading."""
    ratings = args.get("ratings", [])
    if not ratings:
        return json.dumps({"error": "ratings list is required"})

    db = ctx.get("db")
    if not db:
        return json.dumps({"error": "Database not available"})

    updated = 0
    selected = 0
    for r in ratings:
        paper_id = r.get("paper_id")
        score = r.get("relevance_score", 0)
        is_selected = 1 if r.get("selected", False) else 0
        if paper_id is None:
            continue
        try:
            with db._lock:
                db._conn.execute(
                    "UPDATE papers SET relevance_score = ?, selected_for_deep_read = ? WHERE paper_id = ?",
                    (score, is_selected, paper_id),
                )
            updated += 1
            if is_selected:
                selected += 1
        except Exception as exc:
            _log.warning("Failed to rate paper %s: %s", paper_id, exc)

    if updated > 0:
        with db._lock:
            db._conn.commit()

    _log.info("RATE_PAPERS: %d updated, %d selected for deep read", updated, selected)
    return json.dumps({"updated": updated, "selected_for_deep_read": selected})


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _embed_query(text: str) -> list[float] | None:
    """Embed a query string using Gemini gemini-embedding-001."""
    api_key = os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        try:
            from ..credentials import load_api_key
            api_key = load_api_key()
        except Exception:
            pass
    if not api_key:
        return None
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        result = client.models.embed_content(
            model="gemini-embedding-001",
            contents=text,
        )
        if result.embeddings and len(result.embeddings) > 0:
            return result.embeddings[0].values
    except Exception as exc:
        _log.warning("Query embedding failed: %s", exc)
    return None


def search_similar(args: dict[str, Any], ctx: dict) -> str:
    """Search for similar papers using cosine similarity on embeddings, with keyword fallback."""
    text = args.get("text", "")
    limit = args.get("limit", 10)

    if not text:
        return json.dumps({"error": "text is required"})

    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    # Try vector similarity first
    papers_with_emb = db.get_papers_with_embeddings(session_id)
    if papers_with_emb:
        query_emb = _embed_query(text)
        if query_emb:
            scored = []
            for p in papers_with_emb:
                emb = p.pop("embedding")
                sim = _cosine_similarity(query_emb, emb)
                p["similarity"] = round(sim, 4)
                scored.append(p)
            scored.sort(key=lambda x: x["similarity"], reverse=True)
            return json.dumps({
                "papers": scored[:limit],
                "method": "embedding_cosine",
                "total_with_embeddings": len(papers_with_emb),
            }, default=str)

    # Fallback to keyword matching
    papers = db.search_papers_by_keyword(session_id=session_id, keyword=text, limit=limit)
    return json.dumps({"papers": papers, "method": "keyword_fallback"}, default=str)
