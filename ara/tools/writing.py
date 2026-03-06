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

# Track per-section rejection count so we don't loop forever
# Keyed by "session_id:section_key" — auto-cleans stale entries on access
_section_rejection_counts: dict[str, int] = {}
_section_rejection_session: int | None = None  # Track which session owns the counts
_MAX_SECTION_REJECTIONS = 3  # After this many citation rejections, save anyway

# Fallback minimums — overridden by config at runtime via ctx
_MIN_WORDS: dict[str, int] = {
    "abstract": 50, "introduction": 150, "literature_review": 300,
    "methods": 200, "results": 250, "discussion": 200, "conclusion": 80,
}
_MIN_CITATIONS: dict[str, int] = {
    "introduction": 2, "literature_review": 4, "methods": 1,
    "results": 2, "discussion": 2, "conclusion": 1,
}


def _get_min_words(ctx: dict) -> dict[str, int]:
    """Get word minimums from config if available."""
    cfg = ctx.get("config")
    if cfg:
        return {
            "abstract": cfg.words_abstract, "introduction": cfg.words_introduction,
            "literature_review": cfg.words_literature_review, "methods": cfg.words_methods,
            "results": cfg.words_results, "discussion": cfg.words_discussion,
            "conclusion": cfg.words_conclusion,
        }
    return _MIN_WORDS


def _get_min_citations(ctx: dict) -> dict[str, int]:
    """Get citation minimums from config if available."""
    cfg = ctx.get("config")
    if cfg:
        return {
            "introduction": cfg.cites_introduction, "literature_review": cfg.cites_literature_review,
            "methods": cfg.cites_methods, "results": cfg.cites_results,
            "discussion": cfg.cites_discussion, "conclusion": cfg.cites_conclusion,
        }
    return _MIN_CITATIONS

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


def _normalize_author(name: str) -> list[str]:
    """Extract possible last-name tokens from an author string or dict value."""
    # Strip "et al." before processing
    name = re.sub(r"\bet\s+al\.?", "", name, flags=re.IGNORECASE).strip()
    # Remove punctuation except hyphens
    name = re.sub(r"[,.'()]", " ", name)
    parts = [p.lower() for p in name.split() if len(p) > 1]
    return parts


def _verify_citation_against_db(author_fragment: str, year: str, db: Any, session_id: int) -> dict[str, Any]:
    """Check if a citation (Author, Year) matches any paper in the database.

    Uses fuzzy matching: any token in the citation author must appear in any
    author string for a paper in the same year.  Also tries ±1 year to handle
    online-first vs print-year discrepancies.
    """
    if not db or not session_id:
        return {"verified": False, "reason": "no_db"}

    year_int = int(year) if year.isdigit() else None
    if year_int is None:
        return {"verified": False, "reason": "bad_year"}

    # Tokens from the citation author fragment
    cite_tokens = _normalize_author(author_fragment)
    if not cite_tokens:
        return {"verified": False, "reason": "empty_author"}

    # Search with ±1 year tolerance (online-first vs print year)
    rows = db._conn.execute(
        "SELECT paper_id, title, authors, year FROM papers "
        "WHERE session_id = ? AND year BETWEEN ? AND ?",
        (session_id, year_int - 1, year_int + 1),
    ).fetchall()

    for row in rows:
        authors_json = row["authors"] or "[]"
        try:
            authors_list = json.loads(authors_json)
        except (json.JSONDecodeError, TypeError):
            authors_list = []

        # Build a bag of all author tokens for this paper
        paper_tokens: set[str] = set()
        for a in authors_list:
            if isinstance(a, str):
                paper_tokens.update(_normalize_author(a))
            elif isinstance(a, dict):
                for key in ("name", "family", "given", "last"):
                    val = a.get(key, "")
                    if val:
                        paper_tokens.update(_normalize_author(val))

        # Match: ANY cite token appears in paper author tokens (authors only, not titles)
        for ct in cite_tokens:
            if ct in paper_tokens:
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

    # Check minimum word count — hard reject if below 80% of minimum
    min_words = _get_min_words(ctx).get(section_key, 0)
    if min_words > 0 and word_count < min_words:
        if word_count < int(min_words * 0.8):
            errors.append(
                f"WORD COUNT REJECTION: section '{section}' has {word_count} words, "
                f"minimum is {min_words} (hard floor: {int(min_words * 0.8)}). "
                f"Write at least {min_words - word_count} more words."
            )
        else:
            warnings.append(
                f"Section '{section}' has {word_count} words, target is {min_words}. "
                f"Consider expanding by {min_words - word_count} words."
            )

    # Hard reject if section has 0 citations (except abstract and conclusion)
    citations_in_text = _extract_citations_from_text(content)
    if section_key not in ("abstract", "conclusion", "protocol") and len(citations_in_text) == 0:
        errors.append(
            f"ZERO CITATIONS: section '{section}' has no (Author, Year) citations. "
            f"Every section except abstract/conclusion must cite papers from the database. "
            f"Call list_papers first, then cite using (Author, Year) format."
        )

    # Citation verification (3-tier) — reuse citations already extracted above
    citations_found = citations_in_text if citations_in_text else _extract_citations_from_text(content)
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

    # Check minimum citation density (from config if available)
    min_cites = _get_min_citations(ctx).get(section_key, 0)
    total_citations = len(citations_found)
    if min_cites > 0 and total_citations < min_cites:
        warnings.append(
            f"LOW CITATION DENSITY: section '{section}' has {total_citations} citations, "
            f"minimum is {min_cites}. Add more (Author, Year) citations from list_papers data."
        )

    # If critical errors, check retry budget before rejecting
    if errors:
        global _section_rejection_session
        current_session = ctx.get('session_id', 0)
        # Clear stale counts from previous sessions
        if _section_rejection_session != current_session:
            _section_rejection_counts.clear()
            _section_rejection_session = current_session
        rejection_key = f"{current_session}:{section_key}"
        _section_rejection_counts[rejection_key] = _section_rejection_counts.get(rejection_key, 0) + 1
        rejections = _section_rejection_counts[rejection_key]
        _log.warning("WRITE_SECTION REJECTED: section=%s | attempt=%d/%d | %s",
                      section, rejections, _MAX_SECTION_REJECTIONS, errors[0][:120])

        if rejections < _MAX_SECTION_REJECTIONS:
            return json.dumps({
                "status": "rejected",
                "section": section,
                "word_count": word_count,
                "errors": errors,
                "warnings": warnings,
                "verified_citations": verified_count,
                "unverified_citations": unverified,
                "rejection_attempt": rejections,
                "max_rejections": _MAX_SECTION_REJECTIONS,
                "hint": "Use list_papers to see available authors/years, then rewrite citations to match.",
            })
        else:
            # Exceeded retry budget — save with warnings instead of blocking forever
            _log.warning("WRITE_SECTION: saving section=%s after %d rejections (retry budget exhausted)", section, rejections)
            warnings.extend(errors)
            errors = []  # Downgrade to warnings
            _section_rejection_counts[rejection_key] = 0  # Reset for potential revisions

    # Save section to file — prevent quality regression (never overwrite with shorter content)
    section_file = output_dir / f"{section_key}.md"
    if section_file.exists():
        existing_words = len(section_file.read_text(encoding="utf-8").split())
        if word_count < existing_words * 0.85:  # Allow 15% shrink for quality rewrites, but not more
            _log.warning("WRITE_SECTION: refusing to overwrite %s (existing=%d words, new=%d words — too short)",
                          section, existing_words, word_count)
            warnings.append(
                f"Revision rejected: new version ({word_count} words) is significantly shorter than "
                f"existing ({existing_words} words). Expand the content to at least {existing_words} words."
            )
            return json.dumps({
                "status": "revision_too_short",
                "section": section,
                "existing_words": existing_words,
                "new_words": word_count,
                "warnings": warnings,
            })
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
    workspace = ctx.get("workspace", Path("."))

    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    # Collect ALL in-text citations from written sections to filter references
    sections_dir = workspace / "ara_data" / "output" / "sections"
    in_text_citations: set[tuple[str, str]] = set()
    if sections_dir.exists():
        for f in sections_dir.iterdir():
            if f.suffix == ".md" and f.is_file():
                cites = _extract_citations_from_text(f.read_text(encoding="utf-8"))
                in_text_citations.update(cites)

    # Get all papers from DB, then filter to only those actually cited in-text
    all_papers = db.get_cited_papers(session_id)
    if not all_papers:
        # Fallback: get all papers
        rows = db._conn.execute(
            "SELECT paper_id, title, authors, year, doi FROM papers WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        all_papers = [dict(r) for r in rows]
        for p in all_papers:
            p["authors"] = json.loads(p.get("authors") or "[]")

    # Filter papers to only those matching in-text citations
    papers = []
    if in_text_citations:
        for p in all_papers:
            authors_list = p.get("authors", [])
            year = str(p.get("year", ""))
            matched = False
            for author_frag, cite_year in in_text_citations:
                if abs(int(cite_year) - int(year)) <= 1 if year.isdigit() and cite_year.isdigit() else False:
                    author_tokens = _normalize_author(author_frag)
                    for a in authors_list:
                        a_str = a if isinstance(a, str) else (a.get("name", "") or a.get("family", ""))
                        paper_tokens = _normalize_author(a_str)
                        if any(ct in paper_tokens for ct in author_tokens):
                            matched = True
                            break
                    if matched:
                        break
            if matched:
                papers.append(p)
    if not papers:
        papers = all_papers  # Fallback if no matches

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
