# Location: ara/output.py
# Purpose: Output file generation — paper.md, paper.html, index.html, references.bib
# Functions: generate_output
# Calls: db.py
# Imports: json, pathlib, html

from __future__ import annotations

import html
import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_SECTION_ORDER = [
    "title", "abstract", "introduction", "literature_review",
    "methods", "results", "discussion", "conclusion",
]

_HTML_CSS = """
body { font-family: 'Georgia', 'Times New Roman', serif; max-width: 800px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; line-height: 1.8; background: #fafafa; }
h1 { font-size: 1.8em; border-bottom: 2px solid #333; padding-bottom: 10px; margin-top: 40px; }
h2 { font-size: 1.4em; color: #2c3e50; margin-top: 30px; }
h3 { font-size: 1.1em; color: #34495e; }
p { text-align: justify; margin: 12px 0; }
.abstract { background: #f0f0f0; padding: 20px; border-left: 4px solid #2c3e50; margin: 20px 0; font-style: italic; }
.meta { color: #666; font-size: 0.9em; margin-bottom: 30px; }
.citation { color: #2980b9; }
.references { font-size: 0.9em; }
.references li { margin: 8px 0; }
a { color: #2980b9; text-decoration: none; }
a:hover { text-decoration: underline; }
"""

_INDEX_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 700px; margin: 60px auto; padding: 0 20px; color: #1a1a1a; background: #fff; }
h1 { font-size: 2em; margin-bottom: 5px; }
.subtitle { color: #666; margin-bottom: 30px; }
.files { list-style: none; padding: 0; }
.files li { padding: 12px 16px; margin: 8px 0; background: #f8f9fa; border-radius: 6px; border: 1px solid #e9ecef; }
.files a { color: #2980b9; font-weight: 600; text-decoration: none; }
.files a:hover { text-decoration: underline; }
.files .desc { color: #666; font-size: 0.9em; display: block; margin-top: 4px; }
.stats { margin-top: 30px; padding: 16px; background: #f0f7ff; border-radius: 6px; font-size: 0.9em; color: #444; }
"""


def generate_output(
    output_dir: Path,
    sections_dir: Path,
    bib_path: Path | None = None,
    topic: str = "",
    paper_type: str = "research_article",
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: dict[str, str] = {}

    sections = _load_sections(sections_dir)
    if not sections:
        _log.warning("No sections found in %s", sections_dir)
        return generated

    md = _build_markdown(sections, topic)
    md_path = output_dir / "paper.md"
    md_path.write_text(md, encoding="utf-8")
    generated["paper.md"] = str(md_path)

    bib_text = ""
    if bib_path and bib_path.exists():
        bib_text = bib_path.read_text(encoding="utf-8")

    html_content = _build_html(sections, topic, bib_text)
    html_path = output_dir / "paper.html"
    html_path.write_text(html_content, encoding="utf-8")
    generated["paper.html"] = str(html_path)

    if bib_text:
        out_bib = output_dir / "references.bib"
        out_bib.write_text(bib_text, encoding="utf-8")
        generated["references.bib"] = str(out_bib)

    index = _build_index(generated, topic, paper_type)
    index_path = output_dir / "index.html"
    index_path.write_text(index, encoding="utf-8")
    generated["index.html"] = str(index_path)

    return generated


def _load_sections(sections_dir: Path) -> dict[str, str]:
    sections: dict[str, str] = {}
    if not sections_dir.exists():
        return sections
    for f in sections_dir.iterdir():
        if f.suffix == ".md" and f.is_file():
            sections[f.stem] = f.read_text(encoding="utf-8")
    return sections


def _build_markdown(sections: dict[str, str], topic: str) -> str:
    parts: list[str] = []
    if topic:
        parts.append(f"# {topic}\n")
    for name in _SECTION_ORDER:
        if name in sections:
            parts.append(f"## {name.replace('_', ' ').title()}\n")
            parts.append(sections[name])
            parts.append("")
    for name, content in sections.items():
        if name not in _SECTION_ORDER:
            parts.append(f"## {name.replace('_', ' ').title()}\n")
            parts.append(content)
            parts.append("")
    return "\n".join(parts)


def _build_html(sections: dict[str, str], topic: str, bib_text: str) -> str:
    parts: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='en'><head>",
        "<meta charset='utf-8'>",
        f"<title>{html.escape(topic or 'Research Paper')}</title>",
        f"<style>{_HTML_CSS}</style>",
        "</head><body>",
    ]
    if topic:
        parts.append(f"<h1>{html.escape(topic)}</h1>")
    for name in _SECTION_ORDER:
        if name in sections:
            heading = name.replace("_", " ").title()
            content = html.escape(sections[name])
            if name == "abstract":
                parts.append(f"<h2>{heading}</h2>")
                parts.append(f"<div class='abstract'>{_md_to_html(content)}</div>")
            else:
                parts.append(f"<h2>{heading}</h2>")
                parts.append(_md_to_html(content))
    for name, content in sections.items():
        if name not in _SECTION_ORDER:
            heading = name.replace("_", " ").title()
            parts.append(f"<h2>{html.escape(heading)}</h2>")
            parts.append(_md_to_html(html.escape(content)))
    if bib_text:
        parts.append("<h2>References</h2>")
        parts.append("<div class='references'><pre>")
        parts.append(html.escape(bib_text))
        parts.append("</pre></div>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _md_to_html(text: str) -> str:
    paragraphs = text.split("\n\n")
    return "".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())


def _build_index(files: dict[str, str], topic: str, paper_type: str) -> str:
    descs = {
        "paper.md": "Markdown source of the research paper",
        "paper.html": "Formatted HTML version with styling",
        "references.bib": "BibTeX bibliography file",
    }
    items = ""
    for name, path in files.items():
        desc = descs.get(name, "Output file")
        items += f"<li><a href='{html.escape(name)}'>{html.escape(name)}</a>"
        items += f"<span class='desc'>{html.escape(desc)}</span></li>\n"
    return (
        "<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        f"<title>ARA Output — {html.escape(topic or 'Research')}</title>"
        f"<style>{_INDEX_CSS}</style>"
        "</head><body>"
        f"<h1>ARA Research Output</h1>"
        f"<p class='subtitle'>{html.escape(topic or 'Research paper')}"
        f" ({html.escape(paper_type.replace('_', ' '))})</p>"
        f"<ul class='files'>{items}</ul>"
        "<div class='stats'>Generated by ARA — Autonomous Research Agent</div>"
        "</body></html>"
    )
