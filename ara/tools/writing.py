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

# LLM meta-text patterns to strip — preambles, tool-failure commentary, etc.
import re as _re

_LLM_META_PATTERNS = [
    # "Here is the drafted X section..." preambles (with or without period/colon)
    _re.compile(r'^(?:Here is|Below is|I\'ve (?:drafted|written|prepared)|The following is)[^\n]*?[.:\n]\s*(?:\*{3,}|---+)?\s*', _re.IGNORECASE),
    # "Certainly!" / "Sure!" / "Of course!" openers
    _re.compile(r'^(?:Certainly|Sure|Of course|Absolutely|Great)[!.]?\s*(?:Here is|I\'ve|Below)[^\n]*?[.:]\s*\n', _re.IGNORECASE),
    # "Let me" / "I will now" / "Continuing from"
    _re.compile(r'^(?:Let me|I will now|I\'ll now|Continuing from)[^\n]*?[.:]\s*\n', _re.IGNORECASE),
    # "I've incorporated your feedback" / "As requested" / "Following the reviewer"
    _re.compile(r'^(?:I\'ve incorporated|As requested|Following (?:the|your))[^\n]*?[.:]\s*\n', _re.IGNORECASE),
    # "Since tool calls were looping..." explanations
    _re.compile(r'^[^\n]*(?:tool call|looping|validation|provided the raw text|directly below)[^\n]*\.\s*\n', _re.IGNORECASE),
    # Markdown separators at the very top (***  or ---)
    _re.compile(r'^\s*(?:\*{3,}|---+)\s*\n'),
    # "Note:" / "Important:" / "DRAFT" meta-commentary at the start (colon, dash, em-dash)
    _re.compile(r'^(?:Note|Important|NB|Caveat|DRAFT)\s*[:.—–\-]\s*[^\n]*\n', _re.IGNORECASE),
    # Bold meta-notes: "**Note: ...**"
    _re.compile(r'^\*{1,2}(?:Note|Important|NB|Caveat)[:.][^*\n]*\*{1,2}\s*\n', _re.IGNORECASE),
]

# Patterns to strip ANYWHERE in the content (not just at the top)
_LLM_BODY_PATTERNS = [
    # Placeholders: [INSERT X HERE], [TODO: X], [PLACEHOLDER]
    _re.compile(r'\[(?:INSERT|TODO|PLACEHOLDER|ADD|INCLUDE|TBD)[^\]]*\]', _re.IGNORECASE),
    # Word count meta: [Word count: 847 words], (Word count: 847)
    _re.compile(r'[\[\(]Word count[^\]\)]*[\]\)]', _re.IGNORECASE),
    # AI self-references (catastrophic in a paper)
    _re.compile(r'(?:As (?:an? )?(?:AI|artificial intelligence|language model|LLM))[^\n.]*[.\n]', _re.IGNORECASE),
    # Emoji (any non-ASCII emoji-range character at start of paragraph)
    _re.compile(r'[\U0001F300-\U0001F9FF\U00002702-\U000027B0\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF]'),
    # HTML tags
    _re.compile(r'</?(?:b|i|em|strong|u|br|p|div|span|a|h[1-6]|ul|ol|li|table|tr|td|th|img)[^>]*>'),
    # "As requested" / "As you asked" mid-document
    _re.compile(r'\n[^\n]*(?:as (?:you )?(?:requested|asked)|per your (?:request|instructions))[^\n]*\n', _re.IGNORECASE),
]


# Predatory / low-quality DOI prefixes (known predatory publishers)
PREDATORY_DOI_PREFIXES = [
    "10.63544", "10.47857", "10.55248", "10.46254", "10.51594",
    "10.36713", "10.36348", "10.52589", "10.46328", "10.55529",
    "10.33552", "10.46568", "10.35940", "10.47772", "10.32996",
    "10.47577", "10.46632", "10.52783", "10.55014", "10.36347",
    "10.36346", "10.46471", "10.47176", "10.55708", "10.37394",
    "10.51984", "10.53819", "10.55927", "10.46484", "10.36719",
]


def _is_predatory_doi(doi: str) -> bool:
    """Check if a DOI belongs to a known predatory publisher."""
    if not doi:
        return False
    doi_lower = doi.lower().strip()
    # Strip common URL prefixes so we compare the raw DOI
    for url_prefix in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
        if doi_lower.startswith(url_prefix):
            doi_lower = doi_lower[len(url_prefix):]
            break
    return any(doi_lower.startswith(prefix) for prefix in PREDATORY_DOI_PREFIXES)


def _strip_llm_meta_text(content: str) -> str:
    """Remove LLM-generated meta-commentary from section content."""
    original_len = len(content)
    # Apply top-of-content patterns iteratively (meta-text can be multi-line)
    for _ in range(5):  # Max 5 passes
        changed = False
        for pat in _LLM_META_PATTERNS:
            new_content = pat.sub('', content, count=1)
            if new_content != content:
                content = new_content.lstrip()
                changed = True
        if not changed:
            break
    # Apply body patterns globally (placeholders, AI self-refs, HTML, emoji)
    for pat in _LLM_BODY_PATTERNS:
        content = pat.sub('', content)
    # Replace em-dashes and en-dashes with appropriate punctuation
    # " — " (surrounded by spaces) → ", " (most common usage: parenthetical)
    content = _re.sub(r'\s*[\u2014\u2013]\s*', ', ', content)
    # Clean up double commas or comma-period from replacement
    content = _re.sub(r',\s*,', ',', content)
    content = _re.sub(r',\s*\.', '.', content)
    # Clean up double blank lines and trailing spaces left by removals
    content = _re.sub(r'\n{3,}', '\n\n', content)
    content = _re.sub(r' +\n', '\n', content)
    if len(content) < original_len:
        _log.info("WRITE_SECTION: Stripped %d chars of LLM meta-text", original_len - len(content))
    return content


# Track per-section rejection count so we don't loop forever
# Keyed by "session_id:section_key" — auto-cleans stale entries on access
_section_rejection_counts: dict[str, int] = {}
_section_rejection_session: int | None = None  # Track which session owns the counts
_section_too_short_counts: dict[str, int] = {}  # Track too-short rejections separately

# Internal working sections that should NOT appear in final paper output
_INTERNAL_SECTIONS = frozenset({
    "protocol", "synthesis_data", "writing_brief", "advisor_1",
    "advisor_2", "advisory_report", "evaluation",
    "hypothesis", "critic", "synthesis",  # pipeline working notes, not paper content
})
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
#   (Author, Year; Author, Year) — multi-citation parenthetical (semicolons)
#   Handles hyphenated names like Al-Maktoum, De-Sousa
_AUTHOR_FRAG = r'(?:[A-Z][a-z]+(?:-[A-Za-z]+)*)' # Single surname, optionally hyphenated
_AUTHOR_PAIR = _AUTHOR_FRAG + r'(?:\s(?:&|and)\s' + _AUTHOR_FRAG + r')?'  # One or two authors
_AUTHOR_FULL = _AUTHOR_PAIR + r'(?:\set\sal\.)?'  # With optional et al.

_CITATION_PATTERN = re.compile(
    r'(?:'
    r'(' + _AUTHOR_FULL + r'),?\s*(\d{4})'  # parenthetical or inside multi-cite
    r'|'
    r'(' + _AUTHOR_FULL + r')\s*\((\d{4})\)'  # narrative
    r')'
)


def _resolve_citations_openalex(unmatched: list[tuple[str, str]]) -> list[dict]:
    """Resolve unmatched citations via OpenAlex API (free, no key needed).

    Returns list of paper dicts compatible with the reference generator.
    """
    import urllib.request
    import urllib.parse

    resolved = []
    for author_frag, year in unmatched[:40]:  # Cap at 40 to avoid rate limits
        try:
            # Clean author fragment for search
            clean_author = author_frag.replace(" et al.", "").replace(" et al", "").strip()
            query = urllib.parse.quote(f"{clean_author} {year}")
            url = f"https://api.openalex.org/works?search={query}&filter=publication_year:{year}&per_page=1"
            req = urllib.request.Request(url, headers={"User-Agent": "ARA/1.0 (academic research tool)"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
                results = data.get("results", [])
                if results:
                    work = results[0]
                    # Verify author surname match
                    authorships = work.get("authorships", [])
                    author_names = []
                    match_found = False
                    for a in authorships:
                        name = a.get("author", {}).get("display_name", "")
                        author_names.append(name)
                        if clean_author.lower() in name.lower() or any(
                            t in name.lower() for t in _normalize_author(clean_author) if len(t) > 2
                        ):
                            match_found = True
                    if match_found and author_names:
                        doi = (work.get("doi") or "").replace("https://doi.org/", "")
                        resolved.append({
                            "paper_id": f"openalex_{work.get('id', '').split('/')[-1]}",
                            "title": work.get("title", "Untitled"),
                            "authors": author_names,
                            "year": work.get("publication_year", year),
                            "doi": doi,
                        })
        except Exception:
            continue  # Skip on timeout/error — don't block reference generation
    return resolved


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

    # Strip LLM meta-text artifacts (preambles, tool-failure commentary, etc.)
    content = _strip_llm_meta_text(content)

    section_key = section.lower().replace(" ", "_")

    # Reject invalid section names (dummy, test, temp, untitled, etc.)
    _VALID_SECTIONS = {
        "abstract", "introduction", "literature_review", "methods", "methodology", "results",
        "discussion", "conclusion", "theoretical_background", "framework",
        "propositions", "references",
    } | _INTERNAL_SECTIONS
    if section_key not in _VALID_SECTIONS:
        _log.warning("WRITE_SECTION: rejected invalid section name '%s'", section)
        return json.dumps({
            "error": f"Invalid section name '{section}'. Valid sections: "
                     + ", ".join(sorted(_VALID_SECTIONS - _INTERNAL_SECTIONS)),
        })

    db = ctx.get("db")
    session_id = ctx.get("session_id")
    workspace = ctx.get("workspace", Path("."))
    output_dir = workspace / "ara_data" / "output" / "sections"
    output_dir.mkdir(parents=True, exist_ok=True)

    word_count = len(content.split())
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

    # Hard reject if section has 0 citations (except abstract, conclusion, and internal sections)
    citations_in_text = _extract_citations_from_text(content)
    _exempt_from_citation_check = {"abstract", "conclusion", "protocol"} | _INTERNAL_SECTIONS
    if section_key not in _exempt_from_citation_check and len(citations_in_text) == 0:
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

        # Zero tolerance: ANY unverified citation is a hard reject
        if unverified:
            errors.append(
                f"CITATION INTEGRITY FAILURE: {len(unverified)}/{len(citations_found)} citations "
                f"could not be verified in the database. "
                f"Unverified: {', '.join(unverified[:10])}. "
                f"Remove ALL unverified citations and replace with papers from list_papers(). "
                f"ONLY cite papers that exist in the database — do NOT use your training knowledge."
            )

    # Check minimum citation density (from config if available)
    min_cites = _get_min_citations(ctx).get(section_key, 0)
    total_citations = len(citations_found)
    if min_cites > 0 and total_citations < min_cites:
        warnings.append(
            f"LOW CITATION DENSITY: section '{section}' has {total_citations} citations, "
            f"minimum is {min_cites}. Add more (Author, Year) citations from list_papers data."
        )

    # Session tracking for retry budgets
    global _section_rejection_session
    current_session = ctx.get('session_id', 0)

    # If critical errors, check retry budget before rejecting
    if errors:
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

    # Save section to file — prevent quality regression (don't overwrite with much shorter content)
    section_file = output_dir / f"{section_key}.md"
    if section_file.exists() and section_key not in _INTERNAL_SECTIONS:
        existing_words = len(section_file.read_text(encoding="utf-8").split())
        if word_count < existing_words * 0.70:  # Allow 30% shrink for quality rewrites
            too_short_key = f"{current_session}:{section_key}"
            _section_too_short_counts[too_short_key] = _section_too_short_counts.get(too_short_key, 0) + 1
            attempts = _section_too_short_counts[too_short_key]

            if attempts >= 3:
                # After 3 failed attempts, allow the write to prevent infinite loops
                _log.warning("WRITE_SECTION: allowing shorter overwrite of %s after %d attempts "
                             "(existing=%d, new=%d)", section, attempts, existing_words, word_count)
                warnings.append(
                    f"Shorter revision accepted after {attempts} attempts "
                    f"(was {existing_words} words, now {word_count} words)."
                )
                _section_too_short_counts[too_short_key] = 0
            else:
                _log.warning("WRITE_SECTION: refusing to overwrite %s (existing=%d words, new=%d words — too short, attempt %d/3)",
                              section, existing_words, word_count, attempts)
                warnings.append(
                    f"Revision rejected: new version ({word_count} words) is significantly shorter than "
                    f"existing ({existing_words} words). Expand to at least {int(existing_words * 0.70)} words. "
                    f"(attempt {attempts}/3 — will force-accept after 3 attempts)"
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

    # Get ALL papers from DB — not just those with claims
    # The writer cites papers from the full corpus, not just deeply-read ones
    rows = db._conn.execute(
        "SELECT paper_id, title, authors, year, doi FROM papers WHERE session_id = ? "
        "ORDER BY citation_count DESC",
        (session_id,),
    ).fetchall()
    all_papers = [dict(r) for r in rows]
    for p in all_papers:
        try:
            p["authors"] = json.loads(p.get("authors") or "[]")
        except (json.JSONDecodeError, TypeError):
            p["authors"] = []

    # Also check central DB for papers not in this session (foundational works)
    central_db = ctx.get("central_db")
    if central_db:
        try:
            central_rows = central_db._conn.execute(
                "SELECT paper_id, title, authors, year, doi FROM papers "
                "ORDER BY citation_count DESC LIMIT 2000"
            ).fetchall()
            central_ids = {p["paper_id"] for p in all_papers}
            for cr in central_rows:
                cd = dict(cr)
                if cd["paper_id"] not in central_ids:
                    cd["authors"] = json.loads(cd.get("authors") or "[]")
                    all_papers.append(cd)
        except Exception as exc:
            _log.warning("REFERENCES: Central DB lookup failed: %s", exc)

    # Filter papers to only those matching in-text citations
    # Build a fast lookup: normalized author surname fragments → paper
    papers = []
    unmatched_citations: list[tuple[str, str]] = []
    if in_text_citations:
        for author_frag, cite_year in in_text_citations:
            matched_paper = None
            author_tokens = _normalize_author(author_frag)
            for p in all_papers:
                if p in papers:
                    # Already included — just check if this citation matches it too
                    pass
                authors_list = p.get("authors", [])
                year = str(p.get("year", ""))
                # Year match: exact or ±1 (handles in-press / early access)
                year_ok = False
                if year.isdigit() and cite_year.isdigit():
                    year_ok = abs(int(cite_year) - int(year)) <= 1
                if not year_ok:
                    continue
                # Author match: any token from citation appears in any author name
                for a in authors_list:
                    a_str = a if isinstance(a, str) else (a.get("name", "") or a.get("family", ""))
                    paper_tokens = _normalize_author(a_str)
                    # Also try the raw surname (last word of name)
                    a_surname = a_str.strip().split()[-1].lower() if a_str.strip() else ""
                    if any(ct in paper_tokens or ct == a_surname for ct in author_tokens):
                        matched_paper = p
                        break
                if matched_paper:
                    break
            if matched_paper and matched_paper not in papers:
                papers.append(matched_paper)
            elif not matched_paper:
                unmatched_citations.append((author_frag, cite_year))

        if unmatched_citations:
            _log.warning("REFERENCES: %d in-text citations unmatched in DB: %s",
                         len(unmatched_citations),
                         ", ".join(f"({a}, {y})" for a, y in unmatched_citations[:15]))
            # Resolve unmatched citations via OpenAlex API (free, no key needed)
            _resolved_external = _resolve_citations_openalex(unmatched_citations)
            if _resolved_external:
                papers.extend(_resolved_external)
                _log.info("REFERENCES: Resolved %d/%d unmatched citations via OpenAlex",
                          len(_resolved_external), len(unmatched_citations))
    if not papers:
        papers = all_papers  # Fallback if no matches

    # Reference quality filter — remove predatory DOIs and invalid entries
    filtered_papers = []
    predatory_removed = 0
    invalid_removed = 0
    for p in papers:
        doi = p.get("doi", "")
        title = p.get("title", "") or ""
        authors_list = p.get("authors", [])

        # Skip entries with empty/missing authors or titles
        if not title or title == "Untitled" or not authors_list:
            invalid_removed += 1
            continue

        # Skip predatory DOIs
        if _is_predatory_doi(doi):
            predatory_removed += 1
            _log.info("REFERENCES: Filtered predatory DOI: %s (%s)", doi, title[:60])
            continue

        filtered_papers.append(p)

    if predatory_removed or invalid_removed:
        _log.info("REFERENCES: Filtered %d predatory DOIs, %d invalid entries (no author/title)",
                   predatory_removed, invalid_removed)
    papers = filtered_papers if filtered_papers else papers  # Fallback if all filtered

    # Generate both BibTeX and APA formatted references
    bibtex_entries = []
    apa_entries = []

    for p in papers:
        authors_list = p.get("authors", [])
        year = p.get("year", "n.d.")
        title = p.get("title", "Untitled")
        doi = p.get("doi", "")
        key = f"paper_{p.get('paper_id', 0)}"

        # Validate: skip entries with empty author strings after join
        authors_bib = " and ".join(
            (a if isinstance(a, str) else a.get("name", "")).strip()
            for a in authors_list[:5]
            if (a if isinstance(a, str) else a.get("name", "")).strip()
        )
        if not authors_bib or not title.strip():
            continue  # Skip malformed entries

        # BibTeX entry
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
