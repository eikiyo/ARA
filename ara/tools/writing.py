# Location: ara/tools/writing.py
# Purpose: Writing tools — section drafting and citation management
# Functions: write_section, get_citations
# Calls: db.py
# Imports: json

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_section(args: dict[str, Any], ctx: dict) -> str:
    section = args.get("section", "")
    content = args.get("content", "") or args.get("content_guidance", "")
    citations_json = args.get("citations", "[]")

    if not section:
        return json.dumps({"error": "section name is required"})
    if not content:
        return json.dumps({"error": "content is required — write the section text and pass it here"})

    workspace = ctx.get("workspace", Path("."))
    output_dir = workspace / "ara_data" / "output" / "sections"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save section to file
    section_file = output_dir / f"{section}.md"
    section_file.write_text(content, encoding="utf-8")

    return json.dumps({
        "section": section,
        "saved_to": str(section_file),
        "word_count": len(content.split()),
    })


def get_citations(args: dict[str, Any], ctx: dict) -> str:
    db = ctx.get("db")
    session_id = ctx.get("session_id")

    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    papers = db.get_cited_papers(session_id)

    # Generate BibTeX entries
    bibtex_entries = []
    for p in papers:
        authors_str = " and ".join(p.get("authors", [])[:5])
        year = p.get("year", "n.d.")
        title = p.get("title", "Untitled")
        doi = p.get("doi", "")
        key = f"paper_{p.get('paper_id', 0)}"

        entry = f"@article{{{key},\n"
        entry += f"  author = {{{authors_str}}},\n"
        entry += f"  title = {{{title}}},\n"
        entry += f"  year = {{{year}}},\n"
        if doi:
            entry += f"  doi = {{{doi}}},\n"
        entry += "}\n"
        bibtex_entries.append(entry)

    bibtex_text = "\n".join(bibtex_entries)

    # Save to file
    workspace = ctx.get("workspace", Path("."))
    bib_file = workspace / "ara_data" / "output" / "references.bib"
    bib_file.parent.mkdir(parents=True, exist_ok=True)
    bib_file.write_text(bibtex_text, encoding="utf-8")

    return json.dumps({
        "citation_count": len(papers),
        "bibtex_file": str(bib_file),
        "bibtex": bibtex_text[:3000],
    })
