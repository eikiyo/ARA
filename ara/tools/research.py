# Location: ara/tools/research.py
# Purpose: Research tools — claim extraction, hypothesis scoring, branch search
# Functions: extract_claims, score_hypothesis, branch_search
# Calls: db.py
# Imports: json

from __future__ import annotations

import json
from typing import Any


def extract_claims(args: dict[str, Any], ctx: dict) -> str:
    paper_id = args.get("paper_id")
    if paper_id is None:
        return json.dumps({"error": "paper_id is required"})

    db = ctx.get("db")
    if not db:
        return json.dumps({"error": "Database not available"})

    paper = db.get_paper(paper_id)
    if not paper:
        return json.dumps({"error": f"Paper {paper_id} not found"})

    # Return paper content for LLM to process
    # The LLM itself extracts claims and stores via the engine
    return json.dumps({
        "paper_id": paper_id,
        "title": paper.get("title", ""),
        "abstract": paper.get("abstract", ""),
        "full_text": paper.get("full_text", ""),
        "instruction": "Extract structured claims from this paper. For each claim, identify: claim_text, claim_type (finding/method/limitation/gap), confidence (0-1), supporting_quotes, and section.",
    }, default=str)


def score_hypothesis(args: dict[str, Any], ctx: dict) -> str:
    hypothesis = args.get("hypothesis", "")
    context = args.get("context", "")
    if not hypothesis:
        return json.dumps({"error": "hypothesis is required"})

    return json.dumps({
        "hypothesis": hypothesis,
        "context": context,
        "instruction": "Score this hypothesis on 6 dimensions (0.0-1.0): novelty, feasibility, evidence_strength, methodology_fit, impact, reproducibility. Also suggest any domain-specific custom dimensions.",
        "dimensions": ["novelty", "feasibility", "evidence_strength", "methodology_fit", "impact", "reproducibility"],
    })


def branch_search(args: dict[str, Any], ctx: dict) -> str:
    hypothesis = args.get("hypothesis", "")
    branch_type = args.get("branch_type", "lateral")
    domain_hint = args.get("domain_hint", "")

    if not hypothesis:
        return json.dumps({"error": "hypothesis is required"})

    return json.dumps({
        "hypothesis": hypothesis,
        "branch_type": branch_type,
        "domain_hint": domain_hint,
        "instruction": f"Perform a {branch_type} branch search for this hypothesis. "
                       f"Search in adjacent or related fields to find connections, "
                       f"alternative approaches, or convergent evidence. "
                       f"Use search tools to find relevant papers in the branched domain.",
    })
