# Location: ara/tools/verification.py
# Purpose: Paper verification tools — retraction, citation, DOI validation
# Functions: check_retraction, get_citation_count, validate_doi
# Calls: httpx
# Imports: json, httpx

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

_log = logging.getLogger(__name__)


def check_retraction(args: dict[str, Any], ctx: dict) -> str:
    doi = args.get("doi", "").strip()
    if not doi:
        return json.dumps({"error": "DOI is required"})

    try:
        resp = httpx.get(
            f"https://api.crossref.org/works/{doi}",
            params={"mailto": "ara-research@example.com"},
            timeout=15, follow_redirects=True,
        )
        if resp.status_code == 200:
            data = resp.json().get("message", {})
            update_to = data.get("update-to", [])
            is_retracted = any(
                u.get("type") == "retraction" for u in update_to
            )
            return json.dumps({
                "doi": doi,
                "retracted": is_retracted,
                "update_to": [
                    {"type": u.get("type"), "DOI": u.get("DOI"), "label": u.get("label")}
                    for u in update_to
                ] if update_to else [],
            })
        return json.dumps({"doi": doi, "retracted": False, "note": "Could not verify"})
    except Exception as exc:
        _log.warning("Retraction check failed for %s: %s", doi, exc)
        return json.dumps({"doi": doi, "retracted": False, "error": str(exc)})


def get_citation_count(args: dict[str, Any], ctx: dict) -> str:
    doi = args.get("doi", "").strip()
    if not doi:
        return json.dumps({"error": "DOI is required"})

    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    headers = {"x-api-key": api_key} if api_key else {}

    try:
        resp = httpx.get(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
            headers=headers,
            params={"fields": "citationCount,influentialCitationCount,citations.title"},
            timeout=15, follow_redirects=True,
        )
        if resp.status_code == 200:
            data = resp.json()
            return json.dumps({
                "doi": doi,
                "citation_count": data.get("citationCount", 0),
                "influential_count": data.get("influentialCitationCount", 0),
            })
        return json.dumps({"doi": doi, "citation_count": 0, "note": "Paper not found in S2"})
    except Exception as exc:
        _log.warning("Citation count failed for %s: %s", doi, exc)
        return json.dumps({"doi": doi, "citation_count": 0, "error": str(exc)})


def validate_doi(args: dict[str, Any], ctx: dict) -> str:
    doi = args.get("doi", "").strip()
    if not doi:
        return json.dumps({"error": "DOI is required"})

    try:
        resp = httpx.head(
            f"https://doi.org/{doi}",
            timeout=10, follow_redirects=True,
        )
        valid = resp.status_code < 400
        return json.dumps({
            "doi": doi,
            "valid": valid,
            "resolved_url": str(resp.url) if valid else None,
            "status_code": resp.status_code,
        })
    except Exception as exc:
        _log.warning("DOI validation failed for %s: %s", doi, exc)
        return json.dumps({"doi": doi, "valid": False, "error": str(exc)})
