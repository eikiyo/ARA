# Location: ara/tools/research.py
# Purpose: Research analysis tools (claim extraction, hypothesis scoring, branch search)
# Functions: extract_claims, score_hypothesis, branch_search
# Calls: search_semantic_scholar, search_openalex from search module; ARADB
# Imports: json, typing

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .search import search_semantic_scholar, search_openalex

if TYPE_CHECKING:
    from ara.db import ARADB


def extract_claims(paper_id: int, db: ARADB) -> str:
    """Generate instruction for agent to extract claims from paper."""
    try:
        paper = db.get_paper(paper_id)
        if not paper:
            return json.dumps({"error": f"Paper {paper_id} not found"})

        return json.dumps({
            "task": "extract_claims",
            "paper_id": paper["paper_id"],
            "title": paper["title"],
            "abstract": paper.get("abstract", ""),
            "instruction": (
                f"Extract atomic claims from paper {paper['paper_id']}: {paper['title']}. "
                "Analyze the abstract and identify specific, verifiable statements. "
                "Each claim should be a single assertion that can be supported or contradicted by other papers. "
                "Return as JSON array of claim objects with 'text', 'confidence', and 'evidence_type' fields."
            )
        })
    except Exception as e:
        return json.dumps({"error": f"Extract claims error: {str(e)}"})


def score_hypothesis(hypothesis_text: str, dimensions: list[str] | None = None) -> str:
    """Generate scoring template for agent to evaluate hypothesis."""
    if dimensions is None:
        dimensions = [
            "novelty",
            "evidence_strength",
            "feasibility",
            "coherence",
            "cross_domain_support",
            "methodology_fit"
        ]

    return json.dumps({
        "task": "score_hypothesis",
        "hypothesis": hypothesis_text,
        "dimensions": dimensions,
        "instruction": (
            f"Score the following hypothesis across multiple dimensions:\n\n"
            f"Hypothesis: {hypothesis_text}\n\n"
            f"Score each dimension from 0.0 to 1.0:\n"
            + "\n".join([f"  - {dim}" for dim in dimensions])
            + "\n\nProvide a brief justification for each score. "
            "Return as JSON object with scores and explanations."
        ),
        "score_template": {dim: None for dim in dimensions}
    })


def branch_search(hypothesis_text: str, branch_type: str, query: str,
                  round_num: int = 1, parent_branch_id: int | None = None,
                  session_id: int | None = None, db: ARADB | None = None) -> str:
    """Perform cross-domain search based on hypothesis and branch type.

    Tracks search budget if db and session_id provided.
    """
    try:
        # Check budget if db is available
        if db and session_id:
            budget = db.get_branch_budget(session_id)
            searches_used = budget.get('searches_used', 0)
            searches_cap = budget.get('searches_cap', 30)

            if searches_used >= searches_cap:
                return json.dumps({
                    "error": "Branch search budget exhausted",
                    "searches_used": searches_used,
                    "searches_cap": searches_cap,
                })
        else:
            budget = {"searches_used": 0, "searches_cap": 30}

        if branch_type == "lateral":
            modified_query = f"({query}) AND alternative perspective"
        elif branch_type == "methodological":
            modified_query = f"({query}) AND method technique approach"
        elif branch_type == "analogical":
            modified_query = f"({query}) AND analogy application transfer"
        elif branch_type == "convergent":
            modified_query = f"({query}) AND convergent evidence synthesis"
        elif branch_type == "contrarian":
            modified_query = f"({query}) AND contrary opposing viewpoint"
        elif branch_type == "temporal":
            modified_query = f"({query}) AND historical precedent temporal"
        elif branch_type == "geographic":
            modified_query = f"({query}) AND geographic region culture"
        elif branch_type == "scale":
            modified_query = f"({query}) AND scale micro meso macro"
        elif branch_type == "adjacent":
            modified_query = f"({query}) AND adjacent field related"
        else:
            modified_query = query

        scholar_results = search_semantic_scholar(modified_query, limit=5)
        openalex_results = search_openalex(modified_query, limit=5)

        try:
            scholar_papers = json.loads(scholar_results)
        except Exception:
            scholar_papers = []

        try:
            openalex_papers = json.loads(openalex_results)
        except Exception:
            openalex_papers = []

        scholar_list = scholar_papers[:5] if isinstance(scholar_papers, list) else []
        openalex_list = openalex_papers[:5] if isinstance(openalex_papers, list) else []

        # Increment budget if db available
        if db and session_id:
            db.increment_branch_searches(session_id)

        return json.dumps({
            "task": "branch_search",
            "hypothesis": hypothesis_text,
            "branch_type": branch_type,
            "query": modified_query,
            "round": round_num,
            "parent_branch_id": parent_branch_id,
            "results": {
                "semantic_scholar": scholar_list,
                "openalex": openalex_list,
            },
            "total_papers_found": len(scholar_list) + len(openalex_list),
            "budget_status": {
                "searches_used": budget.get('searches_used', 0) + 1,
                "searches_cap": budget.get('searches_cap', 30),
            }
        })
    except Exception as e:
        return json.dumps({"error": f"Branch search error: {str(e)}"})
