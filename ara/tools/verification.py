# Location: ara/tools/verification.py
# Purpose: Claim verification tools (retraction checks, citation counts, DOI validation)
# Functions: check_retraction, get_citation_count, validate_doi
# Calls: httpx for CrossRef and Semantic Scholar APIs
# Imports: json, httpx

from __future__ import annotations

import json

import httpx

from ..logging import get_logger

_log = get_logger("verification")


def check_retraction(doi: str) -> str:
    """Check if paper is retracted via CrossRef API.

    Args:
        doi: Digital Object Identifier

    Returns:
        JSON string with retraction status
    """
    try:
        url = f"https://api.crossref.org/works/{doi}"
        headers = {
            "User-Agent": "ARA/0.1 (Academic Research Agent)"
        }

        response = httpx.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        work = data.get("message", {})

        # Check for retraction/withdrawal updates
        retracted = False
        details = ""

        # Look for update-to field that indicates retraction
        updates = work.get("update-to", [])
        for update in updates:
            if update.get("type") == "retraction":
                retracted = True
                details = f"Retracted on {update.get('updated', 'unknown date')}"
                break

        # Also check the relations field for retraction-of
        relations = work.get("relation", {})
        for rel_type, rel_list in relations.items():
            if "retraction" in rel_type.lower():
                retracted = True
                details = f"Related to retraction: {rel_type}"
                break

        return json.dumps({
            "doi": doi,
            "retracted": retracted,
            "details": details,
            "title": work.get("title", "")
        })
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return json.dumps({
                "doi": doi,
                "retracted": False,
                "details": "DOI not found in CrossRef"
            })
        _log.warning("CrossRef retraction check error (status %d): %s (doi=%s)", e.response.status_code, e, doi)
        return json.dumps({
            "doi": doi,
            "error": f"CrossRef API error: {str(e)}"
        })
    except Exception as e:
        _log.error("Retraction check error: %s (doi=%s)", e, doi, exc_info=True)
        return json.dumps({
            "doi": doi,
            "error": f"Retraction check error: {str(e)}"
        })


def get_citation_count(doi: str) -> str:
    """Get citation count from Semantic Scholar.

    Args:
        doi: Digital Object Identifier

    Returns:
        JSON string with citation count
    """
    try:
        url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
        params = {
            "fields": "citationCount,externalIds"
        }

        response = httpx.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        return json.dumps({
            "doi": doi,
            "citation_count": data.get("citationCount", 0),
            "semantic_scholar_id": data.get("paperId", "")
        })
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return json.dumps({
                "doi": doi,
                "citation_count": 0,
                "message": "Paper not found in Semantic Scholar"
            })
        _log.warning("Semantic Scholar citation API error (status %d): %s (doi=%s)", e.response.status_code, e, doi)
        return json.dumps({
            "doi": doi,
            "error": f"Semantic Scholar API error: {str(e)}"
        })
    except Exception as e:
        _log.error("Citation count error: %s (doi=%s)", e, doi, exc_info=True)
        return json.dumps({
            "doi": doi,
            "error": f"Citation count error: {str(e)}"
        })


def validate_doi(doi: str) -> str:
    """Validate DOI and check if it resolves.

    Args:
        doi: Digital Object Identifier

    Returns:
        JSON string with DOI validity and resolved URL
    """
    try:
        # Use DOI.org API to validate
        url = f"https://doi.org/api/handles/{doi}"

        response = httpx.get(url, timeout=30)

        if response.status_code == 200:
            data = response.json()

            # Extract resolved URL from handle values
            resolved_url = ""
            for value in data.get("values", []):
                if value.get("type") == "URL":
                    resolved_url = value.get("data", {}).get("value", "")
                    break

            return json.dumps({
                "doi": doi,
                "valid": True,
                "resolved_url": resolved_url,
                "handle_index": data.get("handle")
            })
        else:
            return json.dumps({
                "doi": doi,
                "valid": False,
                "message": "DOI does not resolve"
            })
    except httpx.HTTPError as e:
        _log.warning("DOI validation HTTP error: %s (doi=%s)", e, doi)
        return json.dumps({
            "doi": doi,
            "valid": False,
            "error": f"DOI validation error: {str(e)}"
        })
    except Exception as e:
        _log.error("DOI validation error: %s (doi=%s)", e, doi, exc_info=True)
        return json.dumps({
            "doi": doi,
            "error": f"DOI validate error: {str(e)}"
        })
