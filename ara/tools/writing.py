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


def compile_paper(session_id: int, db: ARADB, workspace: Path | None = None) -> str:
    """Compile all sections into paper.md and generate index.html preview."""
    try:
        ws = workspace or Path(".")
        sections_dir = ws / "ara_data" / "output" / "sections"
        output_dir = ws / "ara_data" / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Ordered section names (IMRaD)
        section_order = [
            "abstract", "introduction", "methods", "methodology",
            "results", "discussion", "conclusion", "references",
        ]

        # Collect all section files
        section_files: dict[str, Path] = {}
        if sections_dir.exists():
            for f in sections_dir.iterdir():
                if f.suffix == ".md":
                    section_files[f.stem.lower()] = f

        # Build paper.md in order
        parts: list[str] = []
        # Add any sections in defined order first
        used: set[str] = set()
        for name in section_order:
            if name in section_files:
                content = section_files[name].read_text(encoding="utf-8").strip()
                parts.append(content)
                used.add(name)
        # Then any remaining sections not in the order
        for name, fpath in sorted(section_files.items()):
            if name not in used:
                content = fpath.read_text(encoding="utf-8").strip()
                parts.append(content)

        if not parts:
            return json.dumps({"error": "No sections found in ara_data/output/sections/"})

        paper_md = "\n\n---\n\n".join(parts)
        paper_path = output_dir / "paper.md"
        paper_path.write_text(paper_md, encoding="utf-8")

        # Generate index.html with embedded CSS
        html = _generate_html(paper_md, output_dir)

        return json.dumps({
            "status": "compiled",
            "paper_md": str(paper_path),
            "index_html": str(output_dir / "index.html"),
            "sections_compiled": len(parts),
            "word_count": len(paper_md.split()),
        })
    except Exception as e:
        return json.dumps({"error": f"Compile paper error: {str(e)}"})


def _generate_html(markdown_text: str, output_dir: Path) -> str:
    """Generate a self-contained index.html from markdown."""
    # Try to use markdown library, fall back to basic wrapping
    html_body = ""
    try:
        import markdown as md
        html_body = md.markdown(
            markdown_text,
            extensions=["tables", "fenced_code", "toc"],
        )
    except ImportError:
        # Basic conversion: wrap paragraphs, handle headings
        lines = markdown_text.split("\n")
        converted: list[str] = []
        for line in lines:
            if line.startswith("# "):
                converted.append(f"<h1>{line[2:]}</h1>")
            elif line.startswith("## "):
                converted.append(f"<h2>{line[3:]}</h2>")
            elif line.startswith("### "):
                converted.append(f"<h3>{line[4:]}</h3>")
            elif line.startswith("---"):
                converted.append("<hr>")
            elif line.strip():
                converted.append(f"<p>{line}</p>")
        html_body = "\n".join(converted)

    css = """
    body {
        max-width: 800px; margin: 40px auto; padding: 0 20px;
        font-family: 'Georgia', 'Times New Roman', serif;
        line-height: 1.8; color: #333; background: #fafafa;
    }
    h1 { font-size: 1.8em; border-bottom: 2px solid #333; padding-bottom: 8px; }
    h2 { font-size: 1.4em; color: #444; margin-top: 2em; }
    h3 { font-size: 1.1em; color: #555; }
    p { text-align: justify; margin: 1em 0; }
    hr { border: none; border-top: 1px solid #ccc; margin: 2em 0; }
    blockquote { border-left: 3px solid #ccc; margin: 1em 0; padding: 0.5em 1em; color: #666; }
    code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }
    pre { background: #f0f0f0; padding: 1em; border-radius: 6px; overflow-x: auto; }
    table { border-collapse: collapse; width: 100%; margin: 1em 0; }
    th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
    th { background: #f5f5f5; font-weight: bold; }
    .meta { color: #888; font-size: 0.9em; margin-bottom: 2em; }
    """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ARA Research Paper</title>
<style>{css}</style>
</head>
<body>
<div class="meta">Generated by ARA — Autonomous Research Agent</div>
{html_body}
</body>
</html>"""

    html_path = output_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")
    return str(html_path)


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
