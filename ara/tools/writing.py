# Location: ara/tools/writing.py
# Purpose: Writing tools — section drafting with citation verification and quality checks
# Functions: write_section, get_citations
# Calls: db.py
# Imports: json, re, logging

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Minimum word counts per section for AAA-grade output
_MIN_WORDS: dict[str, int] = {
    "abstract": 250,
    "introduction": 800,
    "literature_review": 1500,
    "methods": 1000,
    "results": 1200,
    "discussion": 1000,
    "conclusion": 400,
}

# Pattern to match citation formats:
#   (Author, Year) — standard APA
#   (Author & Author, Year) — two authors
#   (Author et al., Year) — 3+ authors
#   Author (Year) — narrative citation
#   Author and Author (Year) — narrative two authors
#   Author et al. (Year) — narrative 3+ authors
_CITATION_PATTERN = re.compile(
    r'(?:'
    r'\(([A-Z][a-z]+(?:\s(?:&|and)\s[A-Z][a-z]+)?(?:\set\sal\.)?),?\s*(\d{4})\)'  # parenthetical
    r'|'
    r'([A-Z][a-z]+(?:\s(?:&|and)\s[A-Z][a-z]+)?(?:\set\sal\.)?)\s*\((\d{4})\)'  # narrative
    r')'
)


def _extract_citations_from_text(text: str) -> list[tuple[str, str]]:
    """Extract all (Author, Year) citation tuples from text."""
    results = []
    for m in _CITATION_PATTERN.finditer(text):
        if m.group(1):  # parenthetical match
            results.append((m.group(1), m.group(2)))
        elif m.group(3):  # narrative match
            results.append((m.group(3), m.group(4)))
    return results


def _verify_citation_against_db(author_fragment: str, year: str, db: Any, session_id: int) -> dict[str, Any]:
    """Check if a citation (Author, Year) matches any paper in the database."""
    if not db or not session_id:
        return {"verified": False, "reason": "no_db"}

    year_int = int(year) if year.isdigit() else None

    # Search by author name fragment and year
    rows = db._conn.execute(
        "SELECT paper_id, title, authors, year FROM papers WHERE session_id = ? AND year = ?",
        (session_id, year_int),
    ).fetchall()

    author_lower = author_fragment.lower().strip()
    for row in rows:
        authors_json = row["authors"] or "[]"
        try:
            authors_list = json.loads(authors_json)
        except (json.JSONDecodeError, TypeError):
            authors_list = []

        # Check if any author's last name matches
        for a in authors_list:
            if isinstance(a, str):
                # Try last name match
                parts = a.strip().split()
                if parts:
                    last_name = parts[-1].lower()
                    if last_name == author_lower or author_lower in a.lower():
                        return {"verified": True, "paper_id": row["paper_id"], "title": row["title"]}
            elif isinstance(a, dict):
                name = a.get("name", "") or a.get("family", "") or ""
                if author_lower in name.lower():
                    return {"verified": True, "paper_id": row["paper_id"], "title": row["title"]}

    return {"verified": False, "reason": "not_found_in_db"}


def write_section(args: dict[str, Any], ctx: dict) -> str:
    section = args.get("section", "")
    content = args.get("content", "") or args.get("content_guidance", "")
    _log.info("WRITE_SECTION: section=%s | content_length=%d words", section, len(content.split()))
    citations_json = args.get("citations", "[]")

    if not section:
        return json.dumps({"error": "section name is required"})
    if not content:
        return json.dumps({"error": "content is required — write the section text and pass it here"})

    db = ctx.get("db")
    session_id = ctx.get("session_id")
    workspace = ctx.get("workspace", Path("."))
    output_dir = workspace / "ara_data" / "output" / "sections"
    output_dir.mkdir(parents=True, exist_ok=True)

    word_count = len(content.split())
    section_key = section.lower().replace(" ", "_")
    warnings: list[str] = []
    errors: list[str] = []

    # Check minimum word count
    min_words = _MIN_WORDS.get(section_key, 0)
    if min_words > 0 and word_count < min_words:
        warnings.append(
            f"Section '{section}' has {word_count} words, minimum is {min_words}. "
            f"Consider expanding by {min_words - word_count} words."
        )

    # Citation verification (3-tier)
    citations_found = _extract_citations_from_text(content)
    verified_count = 0
    unverified: list[str] = []
    stripped: list[str] = []

    if db and session_id and citations_found:
        for author, year in citations_found:
            result = _verify_citation_against_db(author, year, db, session_id)
            if result["verified"]:
                verified_count += 1
            else:
                unverified.append(f"({author}, {year})")

        # Calculate unverified ratio
        total_citations = len(citations_found)
        if total_citations > 0:
            unverified_ratio = len(unverified) / total_citations
            if unverified_ratio > 0.2:
                errors.append(
                    f"CITATION INTEGRITY FAILURE: {len(unverified)}/{total_citations} citations "
                    f"({unverified_ratio:.0%}) could not be verified in the database. "
                    f"Unverified: {', '.join(unverified[:10])}. "
                    f"Rewrite this section using ONLY papers from the database."
                )
            elif unverified:
                warnings.append(
                    f"{len(unverified)} citation(s) not found in database: {', '.join(unverified[:5])}. "
                    f"These should be removed or replaced with verified sources."
                )

    # If critical errors, don't save — return error for rewrite
    if errors:
        return json.dumps({
            "status": "rejected",
            "section": section,
            "word_count": word_count,
            "errors": errors,
            "warnings": warnings,
            "verified_citations": verified_count,
            "unverified_citations": unverified,
        })

    # Save section to file
    section_file = output_dir / f"{section_key}.md"
    section_file.write_text(content, encoding="utf-8")

    # Track PRISMA stats if this is the methods section
    if section_key == "methods" and db and session_id:
        _extract_prisma_from_methods(content, db, session_id)

    result = {
        "status": "saved",
        "section": section,
        "saved_to": str(section_file),
        "word_count": word_count,
        "citations_found": len(citations_found),
        "citations_verified": verified_count,
    }
    if warnings:
        result["warnings"] = warnings
    if unverified:
        result["unverified_citations"] = unverified

    return json.dumps(result)


def _extract_prisma_from_methods(content: str, db: Any, session_id: int) -> None:
    """Extract PRISMA flow numbers from methods text and store in DB."""
    prisma_patterns = {
        "records_identified": r"(\d[\d,]*)\s*(?:records?|papers?|articles?|studies)\s*(?:were\s*)?identified",
        "duplicates_removed": r"(\d[\d,]*)\s*duplicates?\s*(?:were\s*)?removed",
        "screened": r"(\d[\d,]*)\s*(?:records?|papers?|articles?)\s*(?:were\s*)?screened",
        "excluded_screening": r"(\d[\d,]*)\s*(?:were\s*)?excluded\s*(?:during|at|after)\s*(?:title|abstract|screening)",
        "fulltext_assessed": r"(\d[\d,]*)\s*(?:full[- ]?text|articles?)\s*(?:were\s*)?assessed",
        "excluded_fulltext": r"(\d[\d,]*)\s*(?:were\s*)?excluded\s*(?:during|at|after)\s*full[- ]?text",
        "included_final": r"(\d[\d,]*)\s*(?:studies|papers?|articles?)\s*(?:were\s*)?included\s*(?:in|for)\s*(?:the\s*)?(?:final|review|analysis|synthesis)",
    }
    for stage, pattern in prisma_patterns.items():
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            count_str = match.group(1).replace(",", "")
            try:
                count = int(count_str)
                db.store_prisma_stat(session_id, stage, count)
            except (ValueError, AttributeError):
                pass


def get_citations(args: dict[str, Any], ctx: dict) -> str:
    db = ctx.get("db")
    session_id = ctx.get("session_id")

    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    papers = db.get_cited_papers(session_id)

    # Generate both BibTeX and APA formatted references
    bibtex_entries = []
    apa_entries = []

    for p in papers:
        authors_list = p.get("authors", [])
        year = p.get("year", "n.d.")
        title = p.get("title", "Untitled")
        doi = p.get("doi", "")
        key = f"paper_{p.get('paper_id', 0)}"

        # BibTeX entry
        authors_bib = " and ".join(a if isinstance(a, str) else a.get("name", "") for a in authors_list[:5])
        entry = f"@article{{{key},\n"
        entry += f"  author = {{{authors_bib}}},\n"
        entry += f"  title = {{{title}}},\n"
        entry += f"  year = {{{year}}},\n"
        if doi:
            entry += f"  doi = {{{doi}}},\n"
        entry += "}\n"
        bibtex_entries.append(entry)

        # APA 7th edition entry
        if len(authors_list) == 0:
            apa_author = "Unknown"
        elif len(authors_list) == 1:
            a = authors_list[0]
            apa_author = a if isinstance(a, str) else a.get("name", "Unknown")
        elif len(authors_list) == 2:
            names = [a if isinstance(a, str) else a.get("name", "") for a in authors_list[:2]]
            apa_author = f"{names[0]} & {names[1]}"
        elif len(authors_list) <= 20:
            names = [a if isinstance(a, str) else a.get("name", "") for a in authors_list]
            apa_author = ", ".join(names[:-1]) + f", & {names[-1]}"
        else:
            names = [a if isinstance(a, str) else a.get("name", "") for a in authors_list[:19]]
            last = authors_list[-1]
            last_name = last if isinstance(last, str) else last.get("name", "")
            apa_author = ", ".join(names) + f", ... {last_name}"

        apa_entry = f"{apa_author} ({year}). {title}."
        if doi:
            apa_entry += f" https://doi.org/{doi}"
        apa_entries.append(apa_entry)

    bibtex_text = "\n".join(bibtex_entries)
    apa_text = "\n\n".join(sorted(apa_entries))

    # Save files
    workspace = ctx.get("workspace", Path("."))
    bib_file = workspace / "ara_data" / "output" / "references.bib"
    bib_file.parent.mkdir(parents=True, exist_ok=True)
    bib_file.write_text(bibtex_text, encoding="utf-8")

    apa_file = workspace / "ara_data" / "output" / "references_apa.txt"
    apa_file.write_text(apa_text, encoding="utf-8")

    return json.dumps({
        "citation_count": len(papers),
        "bibtex_file": str(bib_file),
        "apa_file": str(apa_file),
        "apa_references": apa_text[:5000],
        "bibtex": bibtex_text[:3000],
    })
