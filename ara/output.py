# Location: ara/output.py
# Purpose: Output file generation — paper.md, paper.html, index.html, references.bib, quality_audit.json
# Functions: generate_output
# Calls: db.py
# Imports: json, pathlib, html, re

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_SECTION_ORDER = [
    "title", "abstract", "introduction", "literature_review",
    "methods", "results", "discussion", "conclusion",
]

_SECTION_HEADINGS = {
    "title": "",
    "abstract": "Abstract",
    "introduction": "Introduction",
    "literature_review": "Literature Review",
    "methods": "Methods",
    "results": "Results",
    "results_analysis": "Results Analysis",
    "discussion": "Discussion",
    "conclusion": "Conclusion",
}

_HTML_CSS = """
* { box-sizing: border-box; }
body {
    font-family: 'Georgia', 'Times New Roman', 'Palatino', serif;
    max-width: 850px;
    margin: 50px auto;
    padding: 0 30px;
    color: #1a1a1a;
    line-height: 1.9;
    background: #fafafa;
    font-size: 16px;
}
h1 {
    font-size: 1.8em;
    border-bottom: 2px solid #2c3e50;
    padding-bottom: 12px;
    margin-top: 40px;
    color: #1a1a1a;
    line-height: 1.3;
}
h2 {
    font-size: 1.4em;
    color: #2c3e50;
    margin-top: 35px;
    border-bottom: 1px solid #ddd;
    padding-bottom: 6px;
}
h3 {
    font-size: 1.15em;
    color: #34495e;
    margin-top: 25px;
}
p {
    text-align: justify;
    margin: 14px 0;
    text-indent: 0;
}
.abstract-box {
    background: #f4f6f8;
    padding: 24px 28px;
    border-left: 4px solid #2c3e50;
    margin: 24px 0;
    font-size: 0.95em;
    line-height: 1.7;
}
.abstract-box strong {
    color: #2c3e50;
}
.meta {
    color: #666;
    font-size: 0.9em;
    margin-bottom: 30px;
    text-align: center;
}
.citation {
    color: #2980b9;
}
.references {
    font-size: 0.92em;
    line-height: 1.6;
}
.references p {
    text-indent: -2em;
    padding-left: 2em;
    margin: 6px 0;
    text-align: left;
}
table {
    width: 100%;
    border-collapse: collapse;
    margin: 20px 0;
    font-size: 0.9em;
}
thead th {
    background: #2c3e50;
    color: white;
    padding: 10px 12px;
    text-align: left;
    font-weight: 600;
}
tbody td {
    padding: 8px 12px;
    border-bottom: 1px solid #e0e0e0;
    vertical-align: top;
}
tbody tr:nth-child(even) {
    background: #f8f9fa;
}
tbody tr:hover {
    background: #e8f4f8;
}
a { color: #2980b9; text-decoration: none; }
a:hover { text-decoration: underline; }
.prisma-container {
    text-align: center;
    margin: 30px 0;
}
.prisma-container svg {
    max-width: 100%;
    height: auto;
}
.quality-badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 4px;
    font-size: 0.8em;
    font-weight: bold;
}
.quality-pass { background: #e8f5e9; color: #2e7d32; }
.quality-fail { background: #ffebee; color: #c62828; }
blockquote {
    border-left: 3px solid #bdc3c7;
    margin: 16px 0;
    padding: 8px 20px;
    color: #555;
    font-style: italic;
}
code {
    background: #f0f0f0;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 0.9em;
}
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
.quality-summary { margin-top: 20px; padding: 16px; border-radius: 6px; }
.quality-summary.pass { background: #e8f5e9; border: 1px solid #a5d6a7; }
.quality-summary.fail { background: #fff3e0; border: 1px solid #ffcc80; }
"""


def generate_output(
    output_dir: Path,
    sections_dir: Path,
    bib_path: Path | None = None,
    topic: str = "",
    paper_type: str = "research_article",
    apa_path: Path | None = None,
    prisma_svg_path: Path | None = None,
    prisma_ascii_path: Path | None = None,
    quality_audit_path: Path | None = None,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: dict[str, str] = {}

    sections = _load_sections(sections_dir)
    if not sections:
        _log.warning("No sections found in %s", sections_dir)
        return generated

    # Load APA references
    apa_text = ""
    if apa_path and apa_path.exists():
        apa_text = apa_path.read_text(encoding="utf-8")
    elif sections_dir.parent and (sections_dir.parent / "references_apa.txt").exists():
        apa_text = (sections_dir.parent / "references_apa.txt").read_text(encoding="utf-8")

    # Load PRISMA
    prisma_ascii = ""
    prisma_svg = ""
    if prisma_ascii_path and prisma_ascii_path.exists():
        prisma_ascii = prisma_ascii_path.read_text(encoding="utf-8")
    elif sections_dir.parent and (sections_dir.parent / "prisma_ascii.md").exists():
        prisma_ascii = (sections_dir.parent / "prisma_ascii.md").read_text(encoding="utf-8")

    if prisma_svg_path and prisma_svg_path.exists():
        prisma_svg = prisma_svg_path.read_text(encoding="utf-8")
    elif sections_dir.parent and (sections_dir.parent / "prisma.svg").exists():
        prisma_svg = (sections_dir.parent / "prisma.svg").read_text(encoding="utf-8")

    # Build markdown
    md = _build_markdown(sections, topic, apa_text, prisma_ascii)
    md_path = output_dir / "paper.md"
    md_path.write_text(md, encoding="utf-8")
    generated["paper.md"] = str(md_path)

    # Build HTML
    bib_text = ""
    if bib_path and bib_path.exists():
        bib_text = bib_path.read_text(encoding="utf-8")

    html_content = _build_html(sections, topic, apa_text, bib_text, prisma_svg)
    html_path = output_dir / "paper.html"
    html_path.write_text(html_content, encoding="utf-8")
    generated["paper.html"] = str(html_path)

    # Copy BibTeX
    if bib_text:
        out_bib = output_dir / "references.bib"
        out_bib.write_text(bib_text, encoding="utf-8")
        generated["references.bib"] = str(out_bib)

    # Copy quality audit if exists
    if quality_audit_path and quality_audit_path.exists():
        audit_dest = output_dir / "quality_audit.json"
        audit_dest.write_text(quality_audit_path.read_text(encoding="utf-8"), encoding="utf-8")
        generated["quality_audit.json"] = str(audit_dest)
    else:
        # Check default location
        default_audit = output_dir / "quality_audit.json"
        if default_audit.exists():
            generated["quality_audit.json"] = str(default_audit)

    # Build index
    quality_data = None
    audit_file = output_dir / "quality_audit.json"
    if audit_file.exists():
        try:
            quality_data = json.loads(audit_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    index = _build_index(generated, topic, paper_type, quality_data)
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


def _build_markdown(sections: dict[str, str], topic: str, apa_text: str, prisma_ascii: str) -> str:
    parts: list[str] = []
    if topic:
        parts.append(f"# {topic}\n")

    for name in _SECTION_ORDER:
        if name in sections:
            heading = _SECTION_HEADINGS.get(name, name.replace("_", " ").title())
            if heading:
                parts.append(f"## {heading}\n")
            parts.append(sections[name])
            parts.append("")

    # Add any extra sections not in standard order
    for name, content in sections.items():
        if name not in _SECTION_ORDER:
            heading = _SECTION_HEADINGS.get(name, name.replace("_", " ").title())
            parts.append(f"## {heading}\n")
            parts.append(content)
            parts.append("")

    # Add PRISMA diagram if available
    if prisma_ascii:
        parts.append("## PRISMA Flow Diagram\n")
        parts.append(prisma_ascii)
        parts.append("")

    # Add APA references
    if apa_text:
        parts.append("## References\n")
        parts.append(apa_text)
        parts.append("")

    return "\n".join(parts)


def _md_to_html(text: str) -> str:
    """Convert markdown text to HTML with support for tables, bold, italic, links, and lists."""
    lines = text.split("\n")
    html_parts: list[str] = []
    in_table = False
    in_list = False
    table_rows: list[str] = []
    current_para: list[str] = []

    def flush_para():
        if current_para:
            para_text = " ".join(current_para)
            para_text = _inline_format(para_text)
            html_parts.append(f"<p>{para_text}</p>")
            current_para.clear()

    def flush_table():
        nonlocal in_table
        if table_rows:
            html_parts.append(_render_table(table_rows))
            table_rows.clear()
        in_table = False

    for line in lines:
        stripped = line.strip()

        # Table detection
        if "|" in stripped and stripped.startswith("|"):
            if not in_table:
                flush_para()
                in_table = True
            table_rows.append(stripped)
            continue
        elif in_table:
            flush_table()

        # Headers
        if stripped.startswith("### "):
            flush_para()
            html_parts.append(f"<h3>{_inline_format(stripped[4:])}</h3>")
        elif stripped.startswith("#### "):
            flush_para()
            html_parts.append(f"<h4>{_inline_format(stripped[5:])}</h4>")
        # List items
        elif stripped.startswith("- ") or stripped.startswith("* "):
            flush_para()
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{_inline_format(stripped[2:])}</li>")
        elif re.match(r'^\d+\.\s', stripped):
            flush_para()
            content = re.sub(r'^\d+\.\s', '', stripped)
            if not in_list:
                html_parts.append("<ol>")
                in_list = True
            html_parts.append(f"<li>{_inline_format(content)}</li>")
        else:
            if in_list:
                html_parts.append("</ul>" if html_parts[-2].startswith("<ul") or any("<ul>" in p for p in html_parts[-5:]) else "</ol>")
                in_list = False

            if not stripped:
                flush_para()
            else:
                current_para.append(stripped)

    flush_para()
    if in_table:
        flush_table()
    if in_list:
        html_parts.append("</ul>")

    return "\n".join(html_parts)


def _inline_format(text: str) -> str:
    """Apply inline markdown formatting: bold, italic, links, citations."""
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Links
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
    # Citation highlight
    text = re.sub(
        r'\(([A-Z][a-z]+(?:\s(?:&|and)\s[A-Z][a-z]+)?(?:\set\sal\.)?(?:,\s*\d{4})?(?:;\s*[A-Z][a-z]+(?:\s(?:&|and)\s[A-Z][a-z]+)?(?:\set\sal\.)?(?:,\s*\d{4})?)*)\)',
        r'<span class="citation">(\1)</span>',
        text,
    )
    return text


def _render_table(rows: list[str]) -> str:
    """Render markdown table rows to HTML table."""
    if len(rows) < 2:
        return ""

    def parse_row(row: str) -> list[str]:
        cells = [c.strip() for c in row.strip("|").split("|")]
        return cells

    header_cells = parse_row(rows[0])

    # Skip separator row (row[1] is usually |---|---|)
    data_start = 1
    if len(rows) > 1 and all(c.strip().replace("-", "").replace(":", "") == "" for c in parse_row(rows[1])):
        data_start = 2

    html_out = "<table>\n<thead><tr>"
    for cell in header_cells:
        html_out += f"<th>{_inline_format(cell)}</th>"
    html_out += "</tr></thead>\n<tbody>\n"

    for row in rows[data_start:]:
        cells = parse_row(row)
        html_out += "<tr>"
        for cell in cells:
            html_out += f"<td>{_inline_format(cell)}</td>"
        html_out += "</tr>\n"

    html_out += "</tbody></table>"
    return html_out


def _build_html(sections: dict[str, str], topic: str, apa_text: str, bib_text: str, prisma_svg: str) -> str:
    parts: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='en'><head>",
        "<meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>{html.escape(topic or 'Research Paper')}</title>",
        f"<style>{_HTML_CSS}</style>",
        "</head><body>",
    ]
    if topic:
        parts.append(f"<h1>{html.escape(topic)}</h1>")

    for name in _SECTION_ORDER:
        if name in sections:
            heading = _SECTION_HEADINGS.get(name, name.replace("_", " ").title())
            content = sections[name]
            if name == "abstract":
                parts.append(f"<h2>{heading}</h2>")
                parts.append(f"<div class='abstract-box'>{_md_to_html(content)}</div>")
            elif heading:
                parts.append(f"<h2>{heading}</h2>")
                parts.append(_md_to_html(content))

    # Extra sections
    for name, content in sections.items():
        if name not in _SECTION_ORDER:
            heading = _SECTION_HEADINGS.get(name, name.replace("_", " ").title())
            parts.append(f"<h2>{html.escape(heading)}</h2>")
            parts.append(_md_to_html(content))

    # PRISMA SVG
    if prisma_svg:
        parts.append("<h2>PRISMA Flow Diagram</h2>")
        parts.append(f"<div class='prisma-container'>{prisma_svg}</div>")

    # APA References
    if apa_text:
        parts.append("<h2>References</h2>")
        parts.append("<div class='references'>")
        for ref in apa_text.strip().split("\n\n"):
            ref = ref.strip()
            if ref:
                # Make DOI links clickable
                ref_html = html.escape(ref)
                ref_html = re.sub(
                    r'(https://doi\.org/\S+)',
                    r'<a href="\1">\1</a>',
                    ref_html,
                )
                parts.append(f"<p>{ref_html}</p>")
        parts.append("</div>")
    elif bib_text:
        parts.append("<h2>References</h2>")
        parts.append("<div class='references'><pre>")
        parts.append(html.escape(bib_text))
        parts.append("</pre></div>")

    parts.append("</body></html>")
    return "\n".join(parts)


def _build_index(files: dict[str, str], topic: str, paper_type: str, quality_data: dict | None = None) -> str:
    descs = {
        "paper.md": "Markdown source of the research paper",
        "paper.html": "Formatted HTML version with styling and tables",
        "references.bib": "BibTeX bibliography file",
        "quality_audit.json": "Quality scorecard — word counts, citations, thresholds",
    }
    items = ""
    for name, path in files.items():
        desc = descs.get(name, "Output file")
        items += f"<li><a href='{html.escape(name)}'>{html.escape(name)}</a>"
        items += f"<span class='desc'>{html.escape(desc)}</span></li>\n"

    quality_html = ""
    if quality_data:
        totals = quality_data.get("totals", {})
        thresholds = quality_data.get("thresholds", {})
        overall = quality_data.get("overall_result", "UNKNOWN")
        css_class = "pass" if overall == "PASS" else "fail"

        quality_html = f"""
        <div class='quality-summary {css_class}'>
            <strong>Quality Audit: {overall}</strong><br>
            Words: {totals.get('total_words', 0)} |
            Citations: {totals.get('total_unique_citations_in_text', 0)} |
            Papers in DB: {totals.get('total_cited_papers_in_db', 0)} |
            Claims: {totals.get('total_claims', 0)}
        </div>
        """

    return (
        "<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        f"<title>ARA Output - {html.escape(topic or 'Research')}</title>"
        f"<style>{_INDEX_CSS}</style>"
        "</head><body>"
        f"<h1>ARA Research Output</h1>"
        f"<p class='subtitle'>{html.escape(topic or 'Research paper')}"
        f" ({html.escape(paper_type.replace('_', ' '))})</p>"
        f"<ul class='files'>{items}</ul>"
        f"{quality_html}"
        "<div class='stats'>Generated by ARA - Autonomous Research Agent</div>"
        "</body></html>"
    )
