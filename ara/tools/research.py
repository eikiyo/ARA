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


def verify_claim(args: dict[str, Any], ctx: dict) -> str:
    claim_id = args.get("claim_id")
    if claim_id is None:
        return json.dumps({"error": "claim_id is required"})

    db = ctx.get("db")
    if not db:
        return json.dumps({"error": "Database not available"})

    # Get claim with its paper
    row = db._conn.execute(
        "SELECT c.*, p.title, p.abstract, p.authors, p.year, p.doi "
        "FROM claims c JOIN papers p ON c.paper_id = p.paper_id "
        "WHERE c.claim_id = ?", (claim_id,),
    ).fetchone()
    if not row:
        return json.dumps({"error": f"Claim {claim_id} not found"})

    r = dict(row)
    quotes = r.get("supporting_quotes", "[]")
    try:
        quotes = json.loads(quotes) if isinstance(quotes, str) else quotes
    except (json.JSONDecodeError, TypeError):
        quotes = []

    return json.dumps({
        "claim_id": claim_id,
        "claim_text": r.get("claim_text", ""),
        "claim_type": r.get("claim_type", ""),
        "confidence": r.get("confidence", 0),
        "supporting_quotes": quotes,
        "paper": {
            "title": r.get("title", ""),
            "authors": r.get("authors", ""),
            "year": r.get("year"),
            "abstract": r.get("abstract", ""),
            "doi": r.get("doi", ""),
        },
        "instruction": "Verify: Does the paper's abstract/content support this claim? "
                       "Check supporting quotes against the abstract. Rate as VERIFIED, PARTIALLY_VERIFIED, or UNVERIFIED.",
    }, default=str)


def assess_risk_of_bias(args: dict[str, Any], ctx: dict) -> str:
    paper_id = args.get("paper_id")
    if paper_id is None:
        return json.dumps({"error": "paper_id is required"})

    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    overall_risk = args.get("overall_risk", "unclear")
    _valid = {"low", "moderate", "high", "unclear"}
    if overall_risk not in _valid:
        overall_risk = "unclear"

    rob_id = db.store_risk_of_bias(
        session_id=session_id,
        paper_id=paper_id,
        framework=args.get("framework", "JBI"),
        selection_bias=args.get("selection_bias", "unclear"),
        performance_bias=args.get("performance_bias", "unclear"),
        detection_bias=args.get("detection_bias", "unclear"),
        attrition_bias=args.get("attrition_bias", "unclear"),
        reporting_bias=args.get("reporting_bias", "unclear"),
        overall_risk=overall_risk,
        notes=args.get("notes", ""),
    )
    return json.dumps({"stored": True, "rob_id": rob_id, "paper_id": paper_id, "overall_risk": overall_risk})


def rate_grade_evidence(args: dict[str, Any], ctx: dict) -> str:
    outcome = args.get("outcome", "")
    if not outcome:
        return json.dumps({"error": "outcome is required"})

    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    certainty = args.get("certainty", "low")
    _valid = {"high", "moderate", "low", "very low"}
    if certainty not in _valid:
        certainty = "low"

    # Validate n_studies against actual included papers with claims
    n_studies = args.get("n_studies", 0)
    cited_papers = db.get_cited_papers(session_id)
    max_included = len(cited_papers)
    if max_included > 0 and n_studies > max_included:
        n_studies = max_included  # Cap at actual included count

    grade_id = db.store_grade_evidence(
        session_id=session_id,
        outcome=outcome,
        n_studies=n_studies,
        study_designs=args.get("study_designs", ""),
        risk_of_bias_rating=args.get("risk_of_bias_rating", "not serious"),
        inconsistency=args.get("inconsistency", "not serious"),
        indirectness=args.get("indirectness", "not serious"),
        imprecision=args.get("imprecision", "not serious"),
        publication_bias=args.get("publication_bias", "undetected"),
        effect_size_range=args.get("effect_size_range", ""),
        certainty=certainty,
        direction=args.get("direction", ""),
        notes=args.get("notes", ""),
    )
    return json.dumps({"stored": True, "grade_id": grade_id, "outcome": outcome, "certainty": certainty})


def get_risk_of_bias_table(args: dict[str, Any], ctx: dict) -> str:
    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    rows = db.get_risk_of_bias(session_id)
    if not rows:
        return json.dumps({"table": [], "message": "No risk of bias assessments yet"})
    return json.dumps({"table": rows, "count": len(rows)}, default=str)


def get_grade_table(args: dict[str, Any], ctx: dict) -> str:
    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    rows = db.get_grade_evidence(session_id)
    if not rows:
        return json.dumps({"table": [], "message": "No GRADE evidence ratings yet"})
    return json.dumps({"table": rows, "count": len(rows)}, default=str)


def score_hypothesis(args: dict[str, Any], ctx: dict) -> str:
    hypothesis = args.get("hypothesis", "")
    scores = args.get("scores")
    if not hypothesis:
        return json.dumps({"error": "hypothesis is required"})

    db = ctx.get("db")
    session_id = ctx.get("session_id")

    # If scores provided, store the hypothesis with scores in DB
    if scores and isinstance(scores, dict) and db and session_id:
        # Novelty gets 2x weight — the most important dimension
        weights = {
            "novelty": 2.0, "feasibility": 1.0, "evidence_strength": 1.0,
            "methodology_fit": 1.0, "impact": 1.0, "reproducibility": 1.0,
        }
        dims = list(weights.keys())
        weighted_sum = sum(float(scores.get(d, 0) or 0) * weights[d] for d in dims)
        total_weight = sum(weights.values())  # 7.0
        overall = weighted_sum / total_weight
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
        "instruction": "Score this hypothesis then call score_hypothesis again with the scores dict to store. Keys: novelty, feasibility, evidence_strength, methodology_fit, impact, reproducibility (each 0.0-1.0). NOTE: Novelty has 2x weight in the overall score — be rigorous about novelty scoring.",
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
