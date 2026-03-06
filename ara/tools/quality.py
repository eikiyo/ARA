# Location: ara/tools/quality.py
# Purpose: Quality assurance tools — audit scorecard, PRISMA generator, citation validator
# Functions: generate_quality_audit, generate_prisma_diagram, validate_all_citations
# Calls: db.py, writing.py
# Imports: json, re, logging

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def generate_quality_audit(args: dict[str, Any], ctx: dict) -> str:
    """Generate a comprehensive quality scorecard for the paper."""
    db = ctx.get("db")
    session_id = ctx.get("session_id")
    workspace = ctx.get("workspace", Path("."))

    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    sections_dir = workspace / "ara_data" / "output" / "sections"
    if not sections_dir.exists():
        return json.dumps({"error": "No sections found — paper not yet written"})

    audit: dict[str, Any] = {
        "session_id": session_id,
        "sections": {},
        "totals": {},
        "thresholds": {},
        "prisma": {},
    }

    # Analyze each section
    total_words = 0
    total_citations = 0
    sections_present = []
    from .writing import _extract_citations_from_text

    cfg = ctx.get("config")
    is_conceptual = cfg and cfg.paper_type == "conceptual" if cfg else False

    if is_conceptual:
        required_sections = [
            "abstract", "introduction", "theoretical_background", "framework",
            "propositions", "discussion", "conclusion"
        ]
    else:
        required_sections = [
            "abstract", "introduction", "literature_review", "methods",
            "results", "discussion", "conclusion"
        ]

    if cfg:
        min_words = {
            "abstract": cfg.words_abstract, "introduction": cfg.words_introduction,
            "literature_review": cfg.words_literature_review, "methods": cfg.words_methods,
            "results": cfg.words_results, "discussion": cfg.words_discussion,
            "conclusion": cfg.words_conclusion,
            "theoretical_background": cfg.words_theoretical_background,
            "framework": cfg.words_framework,
            "propositions": cfg.words_propositions,
        }
    else:
        min_words = {
            "abstract": 250, "introduction": 800, "literature_review": 1500,
            "methods": 1000, "results": 1200, "discussion": 1000, "conclusion": 400,
            "theoretical_background": 1500, "framework": 2000, "propositions": 1500,
        }

    all_citations: set[str] = set()
    citation_frequency: dict[str, int] = {}  # Track how many times each citation appears

    for f in sections_dir.iterdir():
        if f.suffix == ".md" and f.is_file():
            content = f.read_text(encoding="utf-8")
            section_name = f.stem
            words = len(content.split())
            citations = _extract_citations_from_text(content)
            unique_cites = set(f"{a},{y}" for a, y in citations)
            all_citations.update(unique_cites)

            # Count citation frequency across all sections
            for a, y in citations:
                key = f"({a}, {y})"
                citation_frequency[key] = citation_frequency.get(key, 0) + 1

            sections_present.append(section_name)
            total_words += words
            total_citations += len(unique_cites)

            section_audit = {
                "word_count": words,
                "min_words": min_words.get(section_name, 0),
                "meets_minimum": words >= min_words.get(section_name, 0),
                "citation_count": len(unique_cites),
                "has_tables": "|" in content and "---" in content,
            }
            audit["sections"][section_name] = section_audit

    # Check required sections
    missing_sections = [s for s in required_sections if s not in sections_present]

    # Count unique papers in DB with claims
    cited_papers = db.get_cited_papers(session_id)
    total_db_papers = db.paper_count(session_id)
    claims = db.get_claims(session_id)

    # PRISMA stats — skip for conceptual papers
    if not is_conceptual:
        prisma_stats = db.get_prisma_stats(session_id)
        audit["prisma"] = {s["stage"]: s["count"] for s in prisma_stats}

    # Totals
    audit["totals"] = {
        "total_words": total_words,
        "total_unique_citations_in_text": len(all_citations),
        "total_papers_in_db": total_db_papers,
        "total_cited_papers_in_db": len(cited_papers),
        "total_claims": len(claims),
        "sections_present": sections_present,
        "sections_missing": missing_sections,
    }

    # Threshold checks
    min_qual_cites = cfg.min_quality_citations if cfg else 40
    min_qual_words = cfg.min_paper_words if cfg else 6000
    min_qual_tables = cfg.min_quality_tables if cfg else 2
    table_count = sum(1 for s in audit["sections"].values() if s.get("has_tables"))
    audit["thresholds"] = {
        f"citations_{min_qual_cites}_plus": len(all_citations) >= min_qual_cites,
        f"words_{min_qual_words}_plus": total_words >= min_qual_words,
        f"tables_{min_qual_tables}_plus": table_count >= min_qual_tables,
        "all_required_sections": len(missing_sections) == 0,
        "all_sections_meet_minimum_words": all(
            audit["sections"].get(s, {}).get("meets_minimum", False)
            for s in required_sections if s in sections_present
        ),
        "papers_cited_in_db": len(cited_papers),
    }

    # RoB and GRADE checks — only for systematic reviews, not conceptual papers
    if not is_conceptual:
        rob_data = db.get_risk_of_bias(session_id)
        grade_data = db.get_grade_evidence(session_id)
        audit["thresholds"]["risk_of_bias_assessed"] = len(rob_data) >= 10
        audit["thresholds"]["grade_evidence_rated"] = len(grade_data) >= 3
        audit["totals"]["rob_assessments"] = len(rob_data)
        audit["totals"]["grade_ratings"] = len(grade_data)
    else:
        # Conceptual paper checks: propositions and framework presence
        has_propositions = "propositions" in sections_present
        has_framework = "framework" in sections_present
        audit["thresholds"]["propositions_section_present"] = has_propositions
        audit["thresholds"]["framework_section_present"] = has_framework

    # Evidence concentration check — conceptual papers allow higher citation frequency
    # for foundational works (threshold 12 vs 8 for reviews)
    max_cite_threshold = 12 if is_conceptual else 8
    max_cite_count = max(citation_frequency.values()) if citation_frequency else 0
    dominant_cites = {k: v for k, v in citation_frequency.items() if v > max_cite_threshold}
    audit["thresholds"]["no_single_study_dominance"] = len(dominant_cites) == 0
    audit["totals"]["max_citation_frequency"] = max_cite_count
    if dominant_cites:
        audit["totals"]["dominant_citations"] = dominant_cites

    # Overall pass/fail
    all_pass = all(audit["thresholds"].values())
    audit["overall_result"] = "PASS" if all_pass else "NEEDS_REVISION"

    # Store audit in DB
    for dim, score in audit["thresholds"].items():
        db.store_quality_audit(session_id, dim, 1.0 if score else 0.0, json.dumps(score))

    # Save audit file
    audit_file = workspace / "ara_data" / "output" / "quality_audit.json"
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    audit_file.write_text(json.dumps(audit, indent=2), encoding="utf-8")

    return json.dumps(audit, indent=2)


def generate_prisma_diagram(args: dict[str, Any], ctx: dict) -> str:
    """Generate a PRISMA flow diagram in both ASCII (for markdown) and SVG (for HTML)."""
    db = ctx.get("db")
    session_id = ctx.get("session_id")
    workspace = ctx.get("workspace", Path("."))

    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    # Get PRISMA stats from DB, fill defaults from paper counts
    stats = db.get_prisma_stats(session_id)
    prisma: dict[str, int] = {s["stage"]: s["count"] for s in stats}

    # Fill from actual DB data if not manually set
    total_papers = db.paper_count(session_id)
    cited_papers = db.get_cited_papers(session_id)
    included_count = len(cited_papers)

    # Count papers selected for deep read (fulltext assessment stage)
    selected_count = db._conn.execute(
        "SELECT COUNT(*) FROM papers WHERE session_id = ? AND selected_for_deep_read = 1",
        (session_id,),
    ).fetchone()[0]

    # Use selected_for_deep_read as "fulltext assessed" — more accurate than cited_papers
    # because some papers may be assessed but excluded at fulltext stage
    fulltext_default = max(selected_count, included_count)

    identified = prisma.get("records_identified", total_papers)
    duplicates = prisma.get("duplicates_removed", 0)
    screened = prisma.get("screened", identified - duplicates)
    excluded_screen = prisma.get("excluded_screening", screened - fulltext_default if screened > fulltext_default else 0)
    fulltext = prisma.get("fulltext_assessed", fulltext_default)
    no_access = prisma.get("fulltext_not_accessible", 0)
    excluded_ft = prisma.get("excluded_fulltext", fulltext - included_count - no_access if fulltext > included_count + no_access else 0)
    included = prisma.get("included_final", included_count)

    # Reconcile: ensure numbers add up (screened = excluded_screen + fulltext)
    if screened < excluded_screen + fulltext:
        screened = excluded_screen + fulltext
    if fulltext < no_access + excluded_ft + included:
        fulltext = no_access + excluded_ft + included

    # ASCII PRISMA diagram for markdown
    ft_excluded_detail = f"No access: {no_access}, Read & excluded: {excluded_ft}" if no_access > 0 else f"n = {excluded_ft}"
    ascii_diagram = f"""
```
PRISMA Flow Diagram
====================

    Identification
    +-----------------------------------------+
    | Records identified through               |
    | database searching (n = {identified:<6})         |
    +-----------------------------------------+
                       |
                       v
    +-----------------------------------------+
    | Records after duplicates                  |
    | removed (n = {identified - duplicates:<6})                    |
    +-----------------------------------------+
                       |
        Screening      |
                       v
    +-----------------------------------------+
    | Records screened        | Records excluded   |
    | (n = {screened:<6})              | (n = {excluded_screen:<6})          |
    +-----------------------------------------+
                       |
        Eligibility    |
                       v
    +-----------------------------------------+
    | Full-text articles      | Full-text articles |
    | assessed for            | excluded:          |
    | eligibility             | {ft_excluded_detail:<19}|
    | (n = {fulltext:<6})              | Total: {no_access + excluded_ft:<12}|
    +-----------------------------------------+
                       |
        Included       |
                       v
    +-----------------------------------------+
    | Studies included in                       |
    | qualitative synthesis                     |
    | (n = {included:<6})                              |
    +-----------------------------------------+
```
"""

    # SVG PRISMA diagram for HTML
    svg_diagram = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 700" style="max-width:600px;font-family:Arial,sans-serif;font-size:12px;">
  <defs>
    <style>
      .box {{ fill: #f0f7ff; stroke: #2c3e50; stroke-width: 1.5; rx: 6; }}
      .box-excluded {{ fill: #fff5f5; stroke: #c0392b; stroke-width: 1.5; rx: 6; }}
      .label {{ fill: #2c3e50; font-weight: bold; font-size: 11px; }}
      .count {{ fill: #2980b9; font-weight: bold; font-size: 13px; }}
      .arrow {{ stroke: #7f8c8d; stroke-width: 2; fill: none; marker-end: url(#arrowhead); }}
      .phase {{ fill: #8e44ad; font-weight: bold; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; }}
    </style>
    <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto">
      <polygon points="0 0, 10 3.5, 0 7" fill="#7f8c8d"/>
    </marker>
  </defs>

  <!-- Phase labels -->
  <text x="20" y="55" class="phase">Identification</text>
  <text x="20" y="225" class="phase">Screening</text>
  <text x="20" y="395" class="phase">Eligibility</text>
  <text x="20" y="565" class="phase">Included</text>

  <!-- Box 1: Records identified -->
  <rect class="box" x="150" y="30" width="280" height="60"/>
  <text x="290" y="55" text-anchor="middle" class="label">Records identified through</text>
  <text x="290" y="75" text-anchor="middle" class="label">database searching</text>
  <text x="490" y="65" class="count">n = {identified}</text>

  <!-- Arrow -->
  <line class="arrow" x1="290" y1="90" x2="290" y2="130"/>

  <!-- Box 2: After duplicates -->
  <rect class="box" x="150" y="130" width="280" height="60"/>
  <text x="290" y="155" text-anchor="middle" class="label">Records after duplicates</text>
  <text x="290" y="175" text-anchor="middle" class="label">removed</text>
  <text x="490" y="165" class="count">n = {identified - duplicates}</text>

  <!-- Arrow -->
  <line class="arrow" x1="290" y1="190" x2="290" y2="230"/>

  <!-- Box 3: Screened -->
  <rect class="box" x="150" y="230" width="200" height="60"/>
  <text x="250" y="255" text-anchor="middle" class="label">Records screened</text>
  <text x="250" y="275" text-anchor="middle" class="count">n = {screened}</text>

  <!-- Excluded screening -->
  <line class="arrow" x1="350" y1="260" x2="400" y2="260"/>
  <rect class="box-excluded" x="400" y="230" width="170" height="60"/>
  <text x="485" y="255" text-anchor="middle" class="label">Records excluded</text>
  <text x="485" y="275" text-anchor="middle" class="count">n = {excluded_screen}</text>

  <!-- Arrow -->
  <line class="arrow" x1="250" y1="290" x2="250" y2="400"/>

  <!-- Box 4: Full-text assessed -->
  <rect class="box" x="150" y="400" width="200" height="60"/>
  <text x="250" y="420" text-anchor="middle" class="label">Full-text articles assessed</text>
  <text x="250" y="440" text-anchor="middle" class="label">for eligibility</text>
  <text x="250" y="450" text-anchor="middle" class="count">n = {fulltext}</text>

  <!-- Excluded fulltext -->
  <line class="arrow" x1="350" y1="430" x2="400" y2="430"/>
  <rect class="box-excluded" x="400" y="390" width="185" height="80"/>
  <text x="492" y="410" text-anchor="middle" class="label">Full-text excluded</text>
  <text x="492" y="430" text-anchor="middle" class="count">n = {no_access + excluded_ft}</text>
  <text x="492" y="448" text-anchor="middle" style="fill:#888;font-size:10px;">No access: {no_access}</text>
  <text x="492" y="462" text-anchor="middle" style="fill:#888;font-size:10px;">Read &amp; excluded: {excluded_ft}</text>

  <!-- Arrow -->
  <line class="arrow" x1="250" y1="460" x2="250" y2="560"/>

  <!-- Box 5: Included -->
  <rect class="box" x="150" y="560" width="280" height="60" style="fill:#e8f5e9;stroke:#27ae60;"/>
  <text x="290" y="585" text-anchor="middle" class="label">Studies included in</text>
  <text x="290" y="605" text-anchor="middle" class="label">qualitative synthesis</text>
  <text x="490" y="595" class="count" style="fill:#27ae60;">n = {included}</text>
</svg>"""

    # Save both
    output_dir = workspace / "ara_data" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    ascii_file = output_dir / "prisma_ascii.md"
    ascii_file.write_text(ascii_diagram, encoding="utf-8")

    svg_file = output_dir / "prisma.svg"
    svg_file.write_text(svg_diagram, encoding="utf-8")

    return json.dumps({
        "prisma_data": {
            "records_identified": identified,
            "duplicates_removed": duplicates,
            "records_screened": screened,
            "excluded_screening": excluded_screen,
            "fulltext_assessed": fulltext,
            "fulltext_not_accessible": no_access,
            "excluded_fulltext": excluded_ft,
            "included_final": included,
        },
        "ascii_file": str(ascii_file),
        "svg_file": str(svg_file),
        "ascii_diagram": ascii_diagram,
    })


def validate_all_citations(args: dict[str, Any], ctx: dict) -> str:
    """Scan all written sections and validate every citation against the DB."""
    db = ctx.get("db")
    session_id = ctx.get("session_id")
    workspace = ctx.get("workspace", Path("."))

    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    sections_dir = workspace / "ara_data" / "output" / "sections"
    if not sections_dir.exists():
        return json.dumps({"error": "No sections found"})

    from .writing import _extract_citations_from_text, _verify_citation_against_db

    results: dict[str, Any] = {"sections": {}, "summary": {}}
    all_verified = 0
    all_unverified = 0
    all_unverified_list: list[str] = []

    for f in sections_dir.iterdir():
        if f.suffix != ".md" or not f.is_file():
            continue
        content = f.read_text(encoding="utf-8")
        citations = _extract_citations_from_text(content)

        section_verified = 0
        section_unverified = []

        for author, year in citations:
            check = _verify_citation_against_db(author, year, db, session_id)
            if check["verified"]:
                section_verified += 1
            else:
                section_unverified.append(f"({author}, {year})")

        all_verified += section_verified
        all_unverified += len(section_unverified)
        all_unverified_list.extend(section_unverified)

        results["sections"][f.stem] = {
            "total_citations": len(citations),
            "verified": section_verified,
            "unverified": section_unverified,
        }

    results["summary"] = {
        "total_verified": all_verified,
        "total_unverified": all_unverified,
        "unverified_list": list(set(all_unverified_list)),
        "integrity_score": all_verified / max(all_verified + all_unverified, 1),
        "pass": all_unverified == 0,
    }

    return json.dumps(results, indent=2)
