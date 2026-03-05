# Location: ara/tools/writing.py
# Purpose: Paper writing and citation management
# Functions: write_section, get_citations
# Calls: ARADB, file I/O
# Imports: json, pathlib, typing

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ara.db import ARADB


def write_section(section_name: str, content: str, session_id: int, db: ARADB) -> str:
    """Save a section to ara_data/output/sections/{section_name}.md"""
    try:
        output_dir = Path("ara_data/output/sections")
        output_dir.mkdir(parents=True, exist_ok=True)

        section_file = output_dir / f"{section_name}.md"
        section_file.write_text(content, encoding="utf-8")

        return json.dumps({
            "task": "write_section",
            "section": section_name,
            "file_path": str(section_file.absolute()),
            "status": "saved",
            "bytes_written": len(content)
        })
    except Exception as e:
        return json.dumps({"error": f"Write section error: {str(e)}"})


def get_citations(session_id: int, db: ARADB) -> str:
    """Generate BibTeX citations from all cited papers in session."""
    try:
        papers = db.get_papers(session_id)

        bibtex_entries = []
        for paper in papers:
            authors_raw = paper.get("authors", "[]")
            if isinstance(authors_raw, str):
                try:
                    authors = json.loads(authors_raw)
                except Exception:
                    authors = []
            else:
                authors = authors_raw or []

            title = paper.get("title", "Untitled")
            year = paper.get("publication_year")
            doi = paper.get("doi")
            url = paper.get("url", "")
            paper_id = paper.get("paper_id", 0)

            # Build BibTeX key
            if authors:
                first_author = authors[0].split()[-1] if authors[0] else "Unknown"
                bibtex_key = f"{first_author}{year}" if year else f"{first_author}nd"
            else:
                bibtex_key = f"paper{paper_id}"

            authors_str = " and ".join(authors) if authors else "Unknown"

            bibtex = f"@article{{{bibtex_key},\n"
            bibtex += f'  title={{{title}}},\n'
            bibtex += f'  author={{{authors_str}}},\n'
            if year:
                bibtex += f'  year={{{year}}},\n'
            if doi:
                bibtex += f'  doi={{{doi}}},\n'
            if url:
                bibtex += f'  url={{{url}}},\n'
            bibtex += "}"

            bibtex_entries.append(bibtex)

        bibliography = "\n\n".join(bibtex_entries)

        # Save bibliography file
        output_dir = Path("ara_data/output")
        output_dir.mkdir(parents=True, exist_ok=True)
        bib_file = output_dir / "references.bib"
        bib_file.write_text(bibliography, encoding="utf-8")

        return json.dumps({
            "task": "get_citations",
            "session_id": session_id,
            "total_papers": len(bibtex_entries),
            "bibliography": bibliography,
            "file_path": str(bib_file.absolute())
        })
    except Exception as e:
        return json.dumps({"error": f"Get citations error: {str(e)}"})
