# Location: ara/tools/verification.py
# Purpose: Paper verification tools — retraction, citation, DOI validation (with central DB caching)
# Functions: check_retraction, get_citation_count, validate_doi
# Calls: httpx, central_db
# Imports: json, httpx

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from .http import rate_limited_get, rate_limited_head

_log = logging.getLogger(__name__)


def _find_paper_by_doi(ctx: dict, doi: str) -> tuple[Any, int | None]:
    """Find paper in session DB by DOI. Returns (db, paper_id) or (None, None)."""
    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if not db or not session_id:
        return None, None
    row = db._conn.execute(
        "SELECT paper_id FROM papers WHERE session_id = ? AND doi = ?",
        (session_id, doi),
    ).fetchone()
    return db, row["paper_id"] if row else None


def _set_flag(ctx: dict, doi: str, flag: str) -> None:
    """Set a verification flag on the paper in session DB."""
    db, pid = _find_paper_by_doi(ctx, doi)
    if db and pid:
        db._conn.execute(f"UPDATE papers SET {flag} = 1 WHERE paper_id = ?", (pid,))
        db._conn.commit()


def _has_flag(ctx: dict, doi: str, flag: str) -> bool:
    """Check if a verification flag is already set."""
    db, pid = _find_paper_by_doi(ctx, doi)
    if not db or not pid:
        return False
    row = db._conn.execute(f"SELECT {flag} FROM papers WHERE paper_id = ?", (pid,)).fetchone()
    return bool(row and row[0])


def check_retraction(args: dict[str, Any], ctx: dict) -> str:
    doi = args.get("doi", "").strip()
    if not doi:
        return json.dumps({"error": "DOI is required"})

    # Check session DB flag first
    if _has_flag(ctx, doi, "retraction_checked"):
        return json.dumps({"doi": doi, "retracted": False, "source": "session_db_flag"})

    # Check central DB cache
    central_db = ctx.get("central_db")
    if central_db:
        cached = central_db.get_doi_validation(doi)
        if cached and cached.get("retraction_permanent"):
            _set_flag(ctx, doi, "retraction_checked")
            return json.dumps({
                "doi": doi,
                "retracted": bool(cached["retracted"]),
                "update_to": json.loads(cached.get("update_to") or "[]"),
                "source": "central_db_cache",
            })

    try:
        resp = rate_limited_get(
            f"https://api.crossref.org/works/{doi}",
            params={"mailto": "ara-research@example.com"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json().get("message", {})
            update_to = data.get("update-to", [])
            is_retracted = any(
                u.get("type") == "retraction" for u in update_to
            )
            update_to_clean = [
                {"type": u.get("type"), "DOI": u.get("DOI"), "label": u.get("label")}
                for u in update_to
            ] if update_to else []

            # Cache in central DB
            if central_db:
                try:
                    central_db.store_doi_validation(
                        doi, retracted=is_retracted,
                        update_to=json.dumps(update_to_clean),
                    )
                except Exception:
                    pass

            _set_flag(ctx, doi, "retraction_checked")
            return json.dumps({
                "doi": doi,
                "retracted": is_retracted,
                "update_to": update_to_clean,
            })
        _set_flag(ctx, doi, "retraction_checked")
        return json.dumps({"doi": doi, "retracted": False, "note": "Could not verify"})
    except Exception as exc:
        _log.warning("Retraction check failed for %s: %s", doi, exc)
        return json.dumps({"doi": doi, "retracted": False, "error": str(exc)})


def get_citation_count(args: dict[str, Any], ctx: dict) -> str:
    doi = args.get("doi", "").strip()
    if not doi:
        return json.dumps({"error": "DOI is required"})

    # Check session DB flag first
    if _has_flag(ctx, doi, "citation_verified"):
        db, pid = _find_paper_by_doi(ctx, doi)
        if db and pid:
            row = db._conn.execute("SELECT citation_count FROM papers WHERE paper_id = ?", (pid,)).fetchone()
            if row and row["citation_count"]:
                return json.dumps({
                    "doi": doi, "citation_count": row["citation_count"],
                    "influential_count": 0, "source": "session_db_flag",
                })

    # Check central DB cache (skip if stale)
    central_db = ctx.get("central_db")
    if central_db:
        cached = central_db.get_doi_validation(doi)
        if cached and not cached.get("citation_count_stale") and cached.get("citation_count", 0) > 0:
            _set_flag(ctx, doi, "citation_verified")
            return json.dumps({
                "doi": doi,
                "citation_count": cached["citation_count"],
                "influential_count": 0,
                "source": "central_db_cache",
            })

    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    headers = {"x-api-key": api_key} if api_key else {}

    try:
        resp = rate_limited_get(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
            headers=headers,
            params={"fields": "citationCount,influentialCitationCount,citations.title"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            cc = data.get("citationCount", 0)
            ic = data.get("influentialCitationCount", 0)

            # Cache in central DB
            if central_db:
                try:
                    central_db.store_doi_validation(doi, citation_count=cc)
                except Exception:
                    pass

            _set_flag(ctx, doi, "citation_verified")
            return json.dumps({
                "doi": doi,
                "citation_count": cc,
                "influential_count": ic,
            })
        return json.dumps({"doi": doi, "citation_count": 0, "note": "Paper not found in S2"})
    except Exception as exc:
        _log.warning("Citation count failed for %s: %s", doi, exc)
        return json.dumps({"doi": doi, "citation_count": 0, "error": str(exc)})


def validate_doi(args: dict[str, Any], ctx: dict) -> str:
    doi = args.get("doi", "").strip()
    if not doi:
        return json.dumps({"error": "DOI is required"})

    # Check session DB flag first
    if _has_flag(ctx, doi, "doi_valid"):
        return json.dumps({"doi": doi, "valid": True, "source": "session_db_flag"})

    # Check central DB — if doi_validation exists, DOI was already validated
    central_db = ctx.get("central_db")
    if central_db:
        cached = central_db.get_doi_validation(doi)
        if cached:
            _set_flag(ctx, doi, "doi_valid")
            return json.dumps({"doi": doi, "valid": True, "source": "central_db_cache"})

    try:
        resp = rate_limited_head(
            f"https://doi.org/{doi}",
            timeout=10,
        )
        valid = resp.status_code < 400
        if valid:
            _set_flag(ctx, doi, "doi_valid")
        return json.dumps({
            "doi": doi,
            "valid": valid,
            "resolved_url": str(resp.url) if valid else None,
            "status_code": resp.status_code,
        })
    except Exception as exc:
        _log.warning("DOI validation failed for %s: %s", doi, exc)
        return json.dumps({"doi": doi, "valid": False, "error": str(exc)})
