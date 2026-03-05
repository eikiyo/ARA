# Location: tests/test_output.py
# Purpose: Tests for output file generation
# Functions: test_generate_output, test_empty_sections
# Calls: ara.output
# Imports: pytest, pathlib

import tempfile
from pathlib import Path

from ara.output import generate_output


def test_generate_output_all_files():
    with tempfile.TemporaryDirectory() as tmp:
        sections_dir = Path(tmp) / "sections"
        sections_dir.mkdir()
        (sections_dir / "abstract.md").write_text("This is the abstract.")
        (sections_dir / "introduction.md").write_text("This is the introduction.")
        (sections_dir / "conclusion.md").write_text("This is the conclusion.")

        bib_path = Path(tmp) / "references.bib"
        bib_path.write_text("@article{test, author={Doe}, title={Test}}")

        output_dir = Path(tmp) / "output"
        files = generate_output(
            output_dir=output_dir,
            sections_dir=sections_dir,
            bib_path=bib_path,
            topic="AI Research",
            paper_type="research_article",
        )

        assert "paper.md" in files
        assert "paper.html" in files
        assert "index.html" in files
        assert "references.bib" in files

        md = (output_dir / "paper.md").read_text()
        assert "AI Research" in md
        assert "Abstract" in md
        assert "Introduction" in md

        html = (output_dir / "paper.html").read_text()
        assert "<style>" in html
        assert "AI Research" in html

        index = (output_dir / "index.html").read_text()
        assert "paper.md" in index
        assert "paper.html" in index


def test_generate_output_no_sections():
    with tempfile.TemporaryDirectory() as tmp:
        sections_dir = Path(tmp) / "empty_sections"
        sections_dir.mkdir()
        output_dir = Path(tmp) / "output"

        files = generate_output(
            output_dir=output_dir,
            sections_dir=sections_dir,
        )
        assert files == {}


def test_generate_output_no_bib():
    with tempfile.TemporaryDirectory() as tmp:
        sections_dir = Path(tmp) / "sections"
        sections_dir.mkdir()
        (sections_dir / "abstract.md").write_text("Abstract content.")

        output_dir = Path(tmp) / "output"
        files = generate_output(
            output_dir=output_dir,
            sections_dir=sections_dir,
        )

        assert "paper.md" in files
        assert "paper.html" in files
        assert "references.bib" not in files
