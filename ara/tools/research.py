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
    claims = args.get("claims")

    if paper_id is None:
        return json.dumps({"error": "paper_id is required"})

    db = ctx.get("db")
    if not db:
        return json.dumps({"error": "Database not available"})

    session_id = ctx.get("session_id")

    # If claims are provided, store them in DB
    if claims and isinstance(claims, list) and session_id:
        stored = 0
        for c in claims:
            if not isinstance(c, dict) or not c.get("claim_text"):
                continue
            db.store_claim(
                session_id=session_id,
                paper_id=paper_id,
                claim_text=c["claim_text"],
                claim_type=c.get("claim_type", "finding"),
                confidence=c.get("confidence", 0.5),
                supporting_quotes=json.dumps(c.get("supporting_quotes", [])),
                section=c.get("section", ""),
                sample_size=c.get("sample_size", ""),
                effect_size=c.get("effect_size", ""),
                p_value=c.get("p_value", ""),
                confidence_interval=c.get("confidence_interval", ""),
                study_design=c.get("study_design", ""),
                population=c.get("population", ""),
                country=c.get("country", ""),
                year_range=c.get("year_range", ""),
            )
            stored += 1
        return json.dumps({"stored": stored, "paper_id": paper_id})

    # Otherwise return paper content for LLM to extract claims from
    paper = db.get_paper(paper_id)
    if not paper:
        return json.dumps({"error": f"Paper {paper_id} not found"})

    full_text = paper.get("full_text", "") or ""
    if len(full_text) > 5000:
        full_text = full_text[:5000] + "... [truncated]"

    return json.dumps({
        "paper_id": paper_id,
        "title": paper.get("title", ""),
        "abstract": paper.get("abstract", ""),
        "full_text": full_text,
        "instruction": "Extract claims then call extract_claims again with paper_id and claims list to store them. "
                       "For each claim include: claim_text, claim_type, confidence, supporting_quotes, section. "
                       "Also extract when available: sample_size, effect_size, p_value, confidence_interval, "
                       "study_design, population, country, year_range.",
    }, default=str)


def score_hypothesis(args: dict[str, Any], ctx: dict) -> str:
    hypothesis = args.get("hypothesis", "")
    scores = args.get("scores")
    if not hypothesis:
        return json.dumps({"error": "hypothesis is required"})

    db = ctx.get("db")
    session_id = ctx.get("session_id")

    # If scores provided, store the hypothesis with scores in DB
    if scores and isinstance(scores, dict) and db and session_id:
        dims = ["novelty", "feasibility", "evidence_strength", "methodology_fit", "impact", "reproducibility"]
        dim_values = [float(scores.get(d, 0) or 0) for d in dims]
        overall = sum(dim_values) / len(dims)
        hyp_id = db.store_hypothesis(
            session_id=session_id,
            hypothesis_text=hypothesis,
            novelty=scores.get("novelty"),
            feasibility=scores.get("feasibility"),
            evidence_strength=scores.get("evidence_strength"),
            methodology_fit=scores.get("methodology_fit"),
            impact=scores.get("impact"),
            reproducibility=scores.get("reproducibility"),
            overall_score=overall,
        )
        return json.dumps({"stored": True, "hypothesis_id": hyp_id, "overall_score": round(overall, 3)})

    # Otherwise return instructions for LLM to score
    return json.dumps({
        "hypothesis": hypothesis,
        "context": args.get("context", ""),
        "instruction": "Score this hypothesis then call score_hypothesis again with the scores dict to store. Keys: novelty, feasibility, evidence_strength, methodology_fit, impact, reproducibility (each 0.0-1.0).",
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
