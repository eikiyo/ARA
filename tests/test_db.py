# Location: tests/test_db.py
# Purpose: Tests for database layer
# Functions: test_session_crud, test_paper_crud, test_dedup
# Calls: ara.db
# Imports: pytest, tempfile

import tempfile
from pathlib import Path

from ara.db import ARADB


def _temp_db() -> ARADB:
    tmp = tempfile.mktemp(suffix=".db")
    return ARADB(Path(tmp))


def test_create_session():
    db = _temp_db()
    sid = db.create_session(topic="Test topic")
    assert sid >= 1
    session = db.get_session(sid)
    assert session is not None
    assert session["topic"] == "Test topic"
    assert session["status"] == "active"
    db.close()


def test_update_session():
    db = _temp_db()
    sid = db.create_session(topic="Original")
    db.update_session(sid, current_phase="scout")
    session = db.get_session(sid)
    assert session["current_phase"] == "scout"
    db.close()


def test_store_papers():
    db = _temp_db()
    sid = db.create_session(topic="Test")
    papers = [
        {"title": "Paper A", "doi": "10.1234/a", "source": "arxiv", "authors": ["Author 1"], "year": 2024},
        {"title": "Paper B", "doi": "10.1234/b", "source": "s2", "authors": ["Author 2"], "year": 2023},
    ]
    stored = db.store_papers(sid, papers)
    assert stored == 2

    # Dedup by DOI
    stored2 = db.store_papers(sid, [{"title": "Paper A Duplicate", "doi": "10.1234/a", "source": "crossref"}])
    assert stored2 == 0

    all_papers = db.get_papers(sid)
    assert len(all_papers) == 2
    db.close()


def test_dedup_by_title():
    db = _temp_db()
    sid = db.create_session(topic="Test")
    db.store_papers(sid, [{"title": "Exact Title", "source": "arxiv"}])
    stored = db.store_papers(sid, [{"title": "Exact Title", "source": "s2"}])
    assert stored == 0
    db.close()


def test_get_paper():
    db = _temp_db()
    sid = db.create_session(topic="Test")
    db.store_papers(sid, [
        {"title": "My Paper", "doi": "10.1/x", "source": "arxiv",
         "authors": ["Alice", "Bob"], "year": 2024, "abstract": "Abstract text"},
    ])
    papers = db.get_papers(sid)
    paper = db.get_paper(papers[0]["paper_id"])
    assert paper is not None
    assert paper["title"] == "My Paper"
    assert paper["authors"] == ["Alice", "Bob"]
    db.close()


def test_keyword_search():
    db = _temp_db()
    sid = db.create_session(topic="Test")
    db.store_papers(sid, [
        {"title": "Machine Learning for Genomics", "source": "s2", "abstract": "We study ML."},
        {"title": "Deep Learning in NLP", "source": "s2", "abstract": "NLP paper."},
    ])
    results = db.search_papers_by_keyword(sid, "Genomics")
    assert len(results) == 1
    assert "Genomics" in results[0]["title"]
    db.close()


def test_cost_tracking():
    db = _temp_db()
    sid = db.create_session(topic="Test")
    db.log_cost(sid, model="gemini-2.0-flash", input_tokens=1000, output_tokens=500, cost_usd=0.001)
    total = db.get_total_cost(sid)
    assert total > 0
    db.close()


def test_rules():
    db = _temp_db()
    sid = db.create_session(topic="Test")
    db.add_rule(sid, "Only papers after 2020", "constraint")
    rules = db.get_rules(sid)
    assert len(rules) == 1
    assert rules[0]["rule_type"] == "constraint"
    db.close()


def test_claims():
    db = _temp_db()
    sid = db.create_session(topic="Test")
    db.store_papers(sid, [{"title": "Paper", "source": "s2"}])
    papers = db.get_papers(sid)
    pid = papers[0]["paper_id"]
    cid = db.store_claim(sid, pid, claim_text="Finding X", claim_type="finding")
    assert cid >= 1
    claims = db.get_claims(sid)
    assert len(claims) == 1
    db.close()
