# Location: ara/central_db.py
# Purpose: Persistent central database — papers, embeddings, DOI validations accumulate across all sessions
# Functions: CentralDB (paper storage, dedup, local-first search, DOI cache)
# Calls: sqlite3
# Imports: sqlite3, json, datetime, hashlib

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_PATH = Path.home() / ".ara" / "central.db"

# DOI validation cache TTL: 30 days for citation counts, permanent for retraction status
_DOI_CACHE_TTL_DAYS = 30

_CENTRAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    paper_id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    title_hash TEXT NOT NULL,
    abstract TEXT,
    authors TEXT,
    year INTEGER,
    doi TEXT,
    source TEXT NOT NULL DEFAULT 'unknown',
    url TEXT,
    citation_count INTEGER DEFAULT 0,
    full_text TEXT,
    full_text_path TEXT,
    embedding TEXT,
    unpaywall_checked INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS doi_validations (
    validation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT NOT NULL UNIQUE,
    retracted INTEGER DEFAULT 0,
    retraction_permanent INTEGER DEFAULT 1,
    citation_count INTEGER DEFAULT 0,
    citation_count_updated_at TEXT,
    update_to TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_sources (
    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL REFERENCES papers(paper_id),
    api_source TEXT NOT NULL,
    external_id TEXT,
    first_seen_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi) WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_papers_title_hash ON papers(title_hash);
CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);
CREATE INDEX IF NOT EXISTS idx_papers_embedding ON papers(paper_id) WHERE embedding IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_doi_validations_doi ON doi_validations(doi);
CREATE INDEX IF NOT EXISTS idx_paper_sources ON paper_sources(paper_id);

CREATE TABLE IF NOT EXISTS claims (
    claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_doi TEXT,
    paper_title TEXT NOT NULL,
    paper_title_hash TEXT NOT NULL,
    claim_text TEXT NOT NULL,
    claim_type TEXT NOT NULL DEFAULT 'finding',
    confidence REAL DEFAULT 0.5,
    supporting_quotes TEXT,
    section TEXT,
    sample_size TEXT,
    effect_size TEXT,
    p_value TEXT,
    confidence_interval TEXT,
    study_design TEXT,
    population TEXT,
    country TEXT,
    year_range TEXT,
    session_topic TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_claims_doi ON claims(paper_doi) WHERE paper_doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_claims_title_hash ON claims(paper_title_hash);
CREATE INDEX IF NOT EXISTS idx_claims_type ON claims(claim_type);

CREATE TABLE IF NOT EXISTS peer_review_results (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_topic TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    scores_json TEXT NOT NULL,
    average_score REAL,
    improved INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_of_bias (
    rob_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_topic TEXT NOT NULL,
    paper_doi TEXT,
    paper_title TEXT NOT NULL,
    paper_title_hash TEXT NOT NULL,
    framework TEXT DEFAULT 'JBI',
    selection_bias TEXT DEFAULT 'unclear',
    performance_bias TEXT DEFAULT 'unclear',
    detection_bias TEXT DEFAULT 'unclear',
    attrition_bias TEXT DEFAULT 'unclear',
    reporting_bias TEXT DEFAULT 'unclear',
    overall_risk TEXT DEFAULT 'unclear',
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grade_evidence (
    grade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_topic TEXT NOT NULL,
    outcome TEXT NOT NULL,
    n_studies INTEGER DEFAULT 0,
    study_designs TEXT,
    risk_of_bias_rating TEXT DEFAULT 'not serious',
    inconsistency TEXT DEFAULT 'not serious',
    indirectness TEXT DEFAULT 'not serious',
    imprecision TEXT DEFAULT 'not serious',
    publication_bias TEXT DEFAULT 'not serious',
    effect_size_range TEXT,
    certainty TEXT DEFAULT 'low',
    direction TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS peer_review_scores (
    score_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_topic TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    round INTEGER NOT NULL,
    reviewer TEXT NOT NULL,
    attribute TEXT NOT NULL,
    score INTEGER NOT NULL,
    feedback TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS peer_review_consensus (
    consensus_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_topic TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    attribute TEXT NOT NULL,
    score INTEGER NOT NULL,
    feedback TEXT,
    improvement_plan TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rob_title_hash ON risk_of_bias(paper_title_hash);
CREATE INDEX IF NOT EXISTS idx_rob_topic ON risk_of_bias(session_topic);
CREATE INDEX IF NOT EXISTS idx_grade_topic ON grade_evidence(session_topic);
CREATE INDEX IF NOT EXISTS idx_pr_scores_topic ON peer_review_scores(session_topic);
CREATE INDEX IF NOT EXISTS idx_pr_consensus_topic ON peer_review_consensus(session_topic);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _title_hash(title: str) -> str:
    """Deterministic hash for dedup — lowercase, strip punctuation, collapse whitespace."""
    t = title.lower().strip()
    t = re.sub(r'[^\w\s]', '', t)
    t = re.sub(r'\s+', ' ', t)
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:32]


class CentralDB:
    """Persistent paper database that grows across all ARA sessions."""

    def __init__(self, db_path: Path | None = None):
        self._path = db_path or _DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_CENTRAL_SCHEMA)
        # Migrations for existing DBs
        self._migrate()
        self._conn.commit()
        _log.info("CentralDB opened: %s", self._path)

    def _migrate(self) -> None:
        """Apply schema migrations for existing databases."""
        # Add unpaywall_checked column if missing
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(papers)").fetchall()}
        if "unpaywall_checked" not in cols:
            self._conn.execute("ALTER TABLE papers ADD COLUMN unpaywall_checked INTEGER DEFAULT 0")

    def close(self) -> None:
        self._conn.close()

    # ── Paper Storage (with dedup) ─────────────────────────────

    def store_papers(self, papers: list[dict[str, Any]]) -> dict[str, int]:
        """Store papers, deduplicating by DOI then title hash.

        Returns: {"stored": N, "skipped": N, "updated": N}
        Also returns a mapping of input index → central paper_id for linking.
        """
        now = _now()
        stored = 0
        skipped = 0
        updated = 0
        paper_id_map: list[int] = []

        with self._lock:
            for p in papers:
                doi = (p.get("doi") or "").strip() or None
                title = p.get("title", "").strip()
                if not title:
                    paper_id_map.append(-1)
                    continue

                t_hash = _title_hash(title)
                existing_id = None

                # Check DOI first
                if doi:
                    row = self._conn.execute(
                        "SELECT paper_id, citation_count FROM papers WHERE doi = ?", (doi,),
                    ).fetchone()
                    if row:
                        existing_id = row["paper_id"]
                        # Update citation count if new one is higher
                        new_cc = p.get("citation_count", 0) or 0
                        if new_cc > (row["citation_count"] or 0):
                            self._conn.execute(
                                "UPDATE papers SET citation_count = ?, updated_at = ? WHERE paper_id = ?",
                                (new_cc, now, existing_id),
                            )
                            updated += 1

                # Check title hash if no DOI match
                if existing_id is None:
                    row = self._conn.execute(
                        "SELECT paper_id FROM papers WHERE title_hash = ?", (t_hash,),
                    ).fetchone()
                    if row:
                        existing_id = row["paper_id"]
                        # If we now have a DOI for this paper, add it
                        if doi:
                            self._conn.execute(
                                "UPDATE papers SET doi = ?, updated_at = ? WHERE paper_id = ? AND doi IS NULL",
                                (doi, now, existing_id),
                            )

                if existing_id is not None:
                    skipped += 1
                    paper_id_map.append(existing_id)
                    # Track source
                    source = p.get("source", "unknown")
                    self._conn.execute(
                        "INSERT OR IGNORE INTO paper_sources (paper_id, api_source, external_id, first_seen_at) "
                        "VALUES (?, ?, ?, ?)",
                        (existing_id, source, doi, now),
                    )
                    continue

                # New paper — insert
                authors_json = json.dumps(p.get("authors", []))
                try:
                    cur = self._conn.execute(
                        "INSERT INTO papers "
                        "(title, title_hash, abstract, authors, year, doi, source, url, "
                        "citation_count, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (title, t_hash, p.get("abstract"), authors_json,
                         p.get("year"), doi, p.get("source", "unknown"),
                         p.get("url"), p.get("citation_count", 0), now, now),
                    )
                    new_id = cur.lastrowid
                    stored += 1
                    paper_id_map.append(new_id)
                    # Track source
                    self._conn.execute(
                        "INSERT INTO paper_sources (paper_id, api_source, external_id, first_seen_at) "
                        "VALUES (?, ?, ?, ?)",
                        (new_id, p.get("source", "unknown"), doi, now),
                    )
                except sqlite3.IntegrityError:
                    skipped += 1
                    # Race condition — re-fetch
                    row = self._conn.execute(
                        "SELECT paper_id FROM papers WHERE title_hash = ?", (t_hash,),
                    ).fetchone()
                    paper_id_map.append(row["paper_id"] if row else -1)

            self._conn.commit()

        if stored > 0 or skipped > 0:
            _log.info("CentralDB store_papers: stored=%d, skipped=%d, updated=%d", stored, skipped, updated)

        return {"stored": stored, "skipped": skipped, "updated": updated, "paper_id_map": paper_id_map}

    def get_paper(self, paper_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["authors"] = json.loads(d.get("authors") or "[]")
        return d

    def get_paper_by_doi(self, doi: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM papers WHERE LOWER(doi) = LOWER(?)", (doi,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["authors"] = json.loads(d.get("authors") or "[]")
        return d

    def paper_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM papers").fetchone()
        return row[0] if row else 0

    # ── Local-First Search ─────────────────────────────────────

    def search_by_keyword(self, keyword: str, limit: int = 100) -> list[dict[str, Any]]:
        """Search central DB by keyword in title/abstract."""
        rows = self._conn.execute(
            "SELECT paper_id, title, abstract, authors, year, doi, source, citation_count "
            "FROM papers WHERE (title LIKE ? OR abstract LIKE ?) "
            "ORDER BY citation_count DESC LIMIT ?",
            (f"%{keyword}%", f"%{keyword}%", limit),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["authors"] = json.loads(d.get("authors") or "[]")
            results.append(d)
        return results

    def search_by_author(self, author: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search central DB by author name."""
        rows = self._conn.execute(
            "SELECT paper_id, title, abstract, authors, year, doi, source, citation_count "
            "FROM papers WHERE authors LIKE ? "
            "ORDER BY citation_count DESC LIMIT ?",
            (f"%{author}%", limit),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["authors"] = json.loads(d.get("authors") or "[]")
            results.append(d)
        return results

    def search_by_year_range(self, start_year: int, end_year: int, limit: int = 200) -> list[dict[str, Any]]:
        """Search central DB by year range."""
        rows = self._conn.execute(
            "SELECT paper_id, title, abstract, authors, year, doi, source, citation_count "
            "FROM papers WHERE year >= ? AND year <= ? "
            "ORDER BY citation_count DESC LIMIT ?",
            (start_year, end_year, limit),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["authors"] = json.loads(d.get("authors") or "[]")
            results.append(d)
        return results

    # ── DOI Validation Cache ───────────────────────────────────

    def get_doi_validation(self, doi: str) -> dict[str, Any] | None:
        """Get cached DOI validation. Returns None if not cached or citation count is stale."""
        row = self._conn.execute(
            "SELECT * FROM doi_validations WHERE doi = ?", (doi,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        # Citation count may be stale — check TTL
        if d.get("citation_count_updated_at"):
            updated = datetime.fromisoformat(d["citation_count_updated_at"])
            if datetime.now(timezone.utc) - updated > timedelta(days=_DOI_CACHE_TTL_DAYS):
                d["citation_count_stale"] = True
        return d

    def store_doi_validation(
        self, doi: str, *,
        retracted: bool = False,
        citation_count: int = 0,
        update_to: str | None = None,
        notes: str | None = None,
    ) -> None:
        """Store or update DOI validation result."""
        now = _now()
        with self._lock:
            existing = self._conn.execute(
                "SELECT validation_id FROM doi_validations WHERE doi = ?", (doi,),
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE doi_validations SET retracted = ?, citation_count = ?, "
                    "citation_count_updated_at = ?, update_to = ?, notes = ?, updated_at = ? "
                    "WHERE doi = ?",
                    (int(retracted), citation_count, now, update_to, notes, now, doi),
                )
            else:
                self._conn.execute(
                    "INSERT INTO doi_validations "
                    "(doi, retracted, citation_count, citation_count_updated_at, "
                    "update_to, notes, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (doi, int(retracted), citation_count, now, update_to, notes, now, now),
                )
            self._conn.commit()

    # ── Embeddings ─────────────────────────────────────────────

    def store_embedding(self, paper_id: int, embedding: list[float]) -> None:
        emb_json = json.dumps(embedding)
        now = _now()
        with self._lock:
            self._conn.execute(
                "UPDATE papers SET embedding = ?, updated_at = ? WHERE paper_id = ?",
                (emb_json, now, paper_id),
            )
            self._conn.commit()

    def get_papers_with_embeddings(self, limit: int = 5000) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT paper_id, title, abstract, authors, year, doi, citation_count, embedding "
            "FROM papers WHERE embedding IS NOT NULL LIMIT ?",
            (limit,),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["authors"] = json.loads(d.get("authors") or "[]")
            d["embedding"] = json.loads(d["embedding"])
            results.append(d)
        return results

    def get_unembedded_paper_ids(self, limit: int = 500) -> list[int]:
        rows = self._conn.execute(
            "SELECT paper_id FROM papers WHERE embedding IS NULL LIMIT ?", (limit,),
        ).fetchall()
        return [row["paper_id"] for row in rows]

    # ── Full Text ──────────────────────────────────────────────

    def store_fulltext(self, paper_id: int, text: str) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "UPDATE papers SET full_text = ?, updated_at = ? WHERE paper_id = ?",
                (text, now, paper_id),
            )
            self._conn.commit()

    def get_fulltext(self, paper_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT full_text FROM papers WHERE paper_id = ?", (paper_id,),
        ).fetchone()
        return row["full_text"] if row else None

    # ── Fulltext Source Tracking ───────────────────────────────

    def mark_unpaywall_checked(self, paper_ids: list[int]) -> int:
        """Mark papers as checked via Unpaywall (whether text was found or not).
        Prevents re-querying Unpaywall for papers that are not open access."""
        now = _now()
        updated = 0
        with self._lock:
            for pid in paper_ids:
                self._conn.execute(
                    "UPDATE papers SET unpaywall_checked = 1, updated_at = ? WHERE paper_id = ?",
                    (now, pid),
                )
                updated += 1
            self._conn.commit()
        return updated

    def get_unchecked_for_unpaywall(self, limit: int = 500) -> list[dict[str, Any]]:
        """Get papers with DOIs that haven't been checked via Unpaywall yet."""
        rows = self._conn.execute(
            "SELECT paper_id, doi FROM papers "
            "WHERE doi IS NOT NULL AND unpaywall_checked = 0 AND full_text IS NULL "
            "ORDER BY citation_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ── Stats ──────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return summary stats about the central DB."""
        total = self._conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        with_doi = self._conn.execute("SELECT COUNT(*) FROM papers WHERE doi IS NOT NULL").fetchone()[0]
        with_embedding = self._conn.execute("SELECT COUNT(*) FROM papers WHERE embedding IS NOT NULL").fetchone()[0]
        with_fulltext = self._conn.execute("SELECT COUNT(*) FROM papers WHERE full_text IS NOT NULL").fetchone()[0]
        doi_validations = self._conn.execute("SELECT COUNT(*) FROM doi_validations").fetchone()[0]
        total_claims = self._conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        sources = self._conn.execute(
            "SELECT api_source, COUNT(*) as cnt FROM paper_sources GROUP BY api_source ORDER BY cnt DESC"
        ).fetchall()
        return {
            "total_papers": total,
            "with_doi": with_doi,
            "with_embedding": with_embedding,
            "with_fulltext": with_fulltext,
            "total_claims": total_claims,
            "doi_validations_cached": doi_validations,
            "sources": {row["api_source"]: row["cnt"] for row in sources},
            "db_path": str(self._path),
        }

    # ── Peer Review Results ──────────────────────────────────────

    def store_peer_review_result(
        self, session_topic: str, cycle: int, scores: dict[str, int],
        average_score: float, improved: bool,
    ) -> int:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO peer_review_results "
                "(session_topic, cycle, scores_json, average_score, improved, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_topic, cycle, json.dumps(scores), average_score, int(improved), now),
            )
            self._conn.commit()
            return cur.lastrowid

    # ── Claims ──────────────────────────────────────────────────

    def store_claims(self, claims: list[dict[str, Any]], session_topic: str = "") -> dict[str, int]:
        """Store claims in central DB, deduplicating by (paper_title_hash, claim_text hash)."""
        now = _now()
        stored = 0
        skipped = 0

        with self._lock:
            for c in claims:
                paper_title = c.get("paper_title", "").strip()
                if not paper_title or not c.get("claim_text"):
                    continue

                t_hash = _title_hash(paper_title)
                claim_text = c["claim_text"].strip()
                # Check for duplicate: same paper + same claim text
                existing = self._conn.execute(
                    "SELECT claim_id FROM claims WHERE paper_title_hash = ? AND claim_text = ?",
                    (t_hash, claim_text),
                ).fetchone()
                if existing:
                    skipped += 1
                    continue

                try:
                    self._conn.execute(
                        "INSERT INTO claims "
                        "(paper_doi, paper_title, paper_title_hash, claim_text, claim_type, "
                        "confidence, supporting_quotes, section, sample_size, effect_size, "
                        "p_value, confidence_interval, study_design, population, country, "
                        "year_range, session_topic, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            c.get("paper_doi"), paper_title, t_hash, claim_text,
                            c.get("claim_type", "finding"), c.get("confidence", 0.5),
                            c.get("supporting_quotes"), c.get("section"),
                            c.get("sample_size"), c.get("effect_size"),
                            c.get("p_value"), c.get("confidence_interval"),
                            c.get("study_design"), c.get("population"),
                            c.get("country"), c.get("year_range"),
                            session_topic, now,
                        ),
                    )
                    stored += 1
                except sqlite3.IntegrityError:
                    skipped += 1

            self._conn.commit()

        if stored > 0:
            _log.info("CentralDB store_claims: stored=%d, skipped=%d", stored, skipped)
        return {"stored": stored, "skipped": skipped}

    def search_claims(self, keyword: str, limit: int = 100) -> list[dict[str, Any]]:
        """Search claims by keyword in claim_text."""
        rows = self._conn.execute(
            "SELECT * FROM claims WHERE claim_text LIKE ? ORDER BY confidence DESC LIMIT ?",
            (f"%{keyword}%", limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_claims_for_paper(self, doi: str) -> list[dict[str, Any]]:
        """Get all claims for a paper by DOI."""
        rows = self._conn.execute(
            "SELECT * FROM claims WHERE paper_doi = ? ORDER BY claim_id",
            (doi,),
        ).fetchall()
        return [dict(row) for row in rows]

    def claim_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]

    # ── Risk of Bias ──────────────────────────────────────────

    def store_risk_of_bias(
        self, session_topic: str, paper_title: str, paper_doi: str | None = None,
        framework: str = "JBI", selection_bias: str = "unclear",
        performance_bias: str = "unclear", detection_bias: str = "unclear",
        attrition_bias: str = "unclear", reporting_bias: str = "unclear",
        overall_risk: str = "unclear", notes: str | None = None,
    ) -> int:
        now = _now()
        t_hash = _title_hash(paper_title)
        with self._lock:
            # Dedup: same paper + same session topic
            existing = self._conn.execute(
                "SELECT rob_id FROM risk_of_bias WHERE paper_title_hash = ? AND session_topic = ?",
                (t_hash, session_topic),
            ).fetchone()
            if existing:
                return existing["rob_id"]
            cur = self._conn.execute(
                "INSERT INTO risk_of_bias "
                "(session_topic, paper_doi, paper_title, paper_title_hash, framework, "
                "selection_bias, performance_bias, detection_bias, attrition_bias, "
                "reporting_bias, overall_risk, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_topic, paper_doi, paper_title, t_hash, framework,
                 selection_bias, performance_bias, detection_bias, attrition_bias,
                 reporting_bias, overall_risk, notes, now),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_risk_of_bias(self, session_topic: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM risk_of_bias WHERE session_topic = ? ORDER BY rob_id",
            (session_topic,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ── GRADE Evidence ────────────────────────────────────────

    def store_grade_evidence(
        self, session_topic: str, outcome: str, n_studies: int = 0,
        study_designs: str = "", risk_of_bias_rating: str = "not serious",
        inconsistency: str = "not serious", indirectness: str = "not serious",
        imprecision: str = "not serious", publication_bias: str = "not serious",
        effect_size_range: str = "", certainty: str = "low",
        direction: str = "", notes: str | None = None,
    ) -> int:
        now = _now()
        with self._lock:
            # Dedup: same outcome + same session topic
            existing = self._conn.execute(
                "SELECT grade_id FROM grade_evidence WHERE outcome = ? AND session_topic = ?",
                (outcome, session_topic),
            ).fetchone()
            if existing:
                return existing["grade_id"]
            cur = self._conn.execute(
                "INSERT INTO grade_evidence "
                "(session_topic, outcome, n_studies, study_designs, risk_of_bias_rating, "
                "inconsistency, indirectness, imprecision, publication_bias, "
                "effect_size_range, certainty, direction, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_topic, outcome, n_studies, study_designs, risk_of_bias_rating,
                 inconsistency, indirectness, imprecision, publication_bias,
                 effect_size_range, certainty, direction, notes, now),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_grade_evidence(self, session_topic: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM grade_evidence WHERE session_topic = ? ORDER BY grade_id",
            (session_topic,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ── Peer Review Scores (Granular) ─────────────────────────

    def store_peer_review_score(
        self, session_topic: str, cycle: int, round_num: int,
        reviewer: str, attribute: str, score: int, feedback: str = "",
    ) -> int:
        now = _now()
        with self._lock:
            # Dedup: same topic + cycle + round + reviewer + attribute
            existing = self._conn.execute(
                "SELECT score_id FROM peer_review_scores "
                "WHERE session_topic = ? AND cycle = ? AND round = ? AND reviewer = ? AND attribute = ?",
                (session_topic, cycle, round_num, reviewer, attribute),
            ).fetchone()
            if existing:
                return existing["score_id"]
            cur = self._conn.execute(
                "INSERT INTO peer_review_scores "
                "(session_topic, cycle, round, reviewer, attribute, score, feedback, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_topic, cycle, round_num, reviewer, attribute, score, feedback, now),
            )
            self._conn.commit()
            return cur.lastrowid

    def store_peer_review_consensus(
        self, session_topic: str, cycle: int, attribute: str,
        score: int, feedback: str = "", improvement_plan: str = "",
    ) -> int:
        now = _now()
        with self._lock:
            # Dedup: same topic + cycle + attribute
            existing = self._conn.execute(
                "SELECT consensus_id FROM peer_review_consensus "
                "WHERE session_topic = ? AND cycle = ? AND attribute = ?",
                (session_topic, cycle, attribute),
            ).fetchone()
            if existing:
                return existing["consensus_id"]
            cur = self._conn.execute(
                "INSERT INTO peer_review_consensus "
                "(session_topic, cycle, attribute, score, feedback, improvement_plan, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_topic, cycle, attribute, score, feedback, improvement_plan, now),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_peer_review_scores(self, session_topic: str, cycle: int | None = None) -> list[dict[str, Any]]:
        if cycle is not None:
            rows = self._conn.execute(
                "SELECT * FROM peer_review_scores WHERE session_topic = ? AND cycle = ? ORDER BY round, reviewer",
                (session_topic, cycle),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM peer_review_scores WHERE session_topic = ? ORDER BY cycle, round, reviewer",
                (session_topic,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_peer_review_consensus(self, session_topic: str, cycle: int | None = None) -> list[dict[str, Any]]:
        if cycle is not None:
            rows = self._conn.execute(
                "SELECT * FROM peer_review_consensus WHERE session_topic = ? AND cycle = ? ORDER BY attribute",
                (session_topic, cycle),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM peer_review_consensus WHERE session_topic = ? ORDER BY cycle, attribute",
                (session_topic,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ── Import from session DB ─────────────────────────────────

    def import_from_session_db(self, session_db_path: Path) -> dict[str, int]:
        """Import papers from an existing session.db into the central DB.

        Returns: {"imported": N, "skipped": N}
        """
        if not session_db_path.exists():
            return {"imported": 0, "skipped": 0, "error": "File not found"}

        try:
            conn = sqlite3.connect(str(session_db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT title, abstract, authors, year, doi, source, url, "
                "citation_count, full_text, full_text_path, embedding "
                "FROM papers"
            ).fetchall()
            conn.close()
        except Exception as e:
            return {"imported": 0, "skipped": 0, "error": str(e)}

        papers = []
        for row in rows:
            d = dict(row)
            d["authors"] = json.loads(d.get("authors") or "[]")
            papers.append(d)

        result = self.store_papers(papers)

        # Also import full text and embeddings for matched papers
        imported_ft = 0
        imported_emb = 0
        for i, row in enumerate(rows):
            central_id = result["paper_id_map"][i] if i < len(result["paper_id_map"]) else -1
            if central_id <= 0:
                continue
            if row["full_text"] and not self.get_fulltext(central_id):
                self.store_fulltext(central_id, row["full_text"])
                imported_ft += 1
            if row["embedding"]:
                # Check if central already has embedding
                existing = self._conn.execute(
                    "SELECT embedding FROM papers WHERE paper_id = ? AND embedding IS NOT NULL",
                    (central_id,),
                ).fetchone()
                if not existing:
                    emb = json.loads(row["embedding"])
                    self.store_embedding(central_id, emb)
                    imported_emb += 1

        # Import claims, RoB, GRADE, peer review scores/consensus
        imported_claims = 0
        imported_rob = 0
        imported_grade = 0
        imported_pr_scores = 0
        imported_pr_consensus = 0

        try:
            conn2 = sqlite3.connect(str(session_db_path))
            conn2.row_factory = sqlite3.Row

            # Get session topic
            session_row = conn2.execute("SELECT topic FROM sessions LIMIT 1").fetchone()
            session_topic = session_row["topic"] if session_row else ""

            # Claims
            claim_rows = conn2.execute(
                "SELECT c.*, p.title as paper_title, p.doi as paper_doi "
                "FROM claims c JOIN papers p ON c.paper_id = p.paper_id"
            ).fetchall()
            if claim_rows:
                central_claims = []
                for cr in claim_rows:
                    central_claims.append({
                        "paper_title": cr["paper_title"],
                        "paper_doi": cr["paper_doi"] or "",
                        "claim_text": cr["claim_text"],
                        "claim_type": cr["claim_type"],
                        "confidence": cr["confidence"],
                        "supporting_quotes": cr["supporting_quotes"],
                        "section": cr["section"],
                        "sample_size": cr["sample_size"],
                        "effect_size": cr["effect_size"],
                        "p_value": cr["p_value"],
                        "confidence_interval": cr["confidence_interval"],
                        "study_design": cr["study_design"],
                        "population": cr["population"],
                        "country": cr["country"],
                        "year_range": cr["year_range"],
                    })
                claim_result = self.store_claims(central_claims, session_topic=session_topic)
                imported_claims = claim_result["stored"]

            # Risk of Bias
            try:
                rob_rows = conn2.execute(
                    "SELECT r.*, p.title as paper_title, p.doi as paper_doi "
                    "FROM risk_of_bias r JOIN papers p ON r.paper_id = p.paper_id"
                ).fetchall()
                for rr in rob_rows:
                    self.store_risk_of_bias(
                        session_topic=session_topic,
                        paper_title=rr["paper_title"],
                        paper_doi=rr["paper_doi"],
                        framework=rr["framework"] or "JBI",
                        selection_bias=rr["selection_bias"] or "unclear",
                        performance_bias=rr["performance_bias"] or "unclear",
                        detection_bias=rr["detection_bias"] or "unclear",
                        attrition_bias=rr["attrition_bias"] or "unclear",
                        reporting_bias=rr["reporting_bias"] or "unclear",
                        overall_risk=rr["overall_risk"] or "unclear",
                        notes=rr["notes"],
                    )
                    imported_rob += 1
            except Exception as exc:
                _log.warning("CentralDB RoB import failed: %s", exc)

            # GRADE Evidence
            try:
                grade_rows = conn2.execute("SELECT * FROM grade_evidence").fetchall()
                for gr in grade_rows:
                    self.store_grade_evidence(
                        session_topic=session_topic,
                        outcome=gr["outcome"],
                        n_studies=gr["n_studies"] or 0,
                        study_designs=gr["study_designs"] or "",
                        risk_of_bias_rating=gr["risk_of_bias_rating"] or "not serious",
                        inconsistency=gr["inconsistency"] or "not serious",
                        indirectness=gr["indirectness"] or "not serious",
                        imprecision=gr["imprecision"] or "not serious",
                        publication_bias=gr["publication_bias"] or "not serious",
                        effect_size_range=gr["effect_size_range"] or "",
                        certainty=gr["certainty"] or "low",
                        direction=gr["direction"] or "",
                        notes=gr["notes"],
                    )
                    imported_grade += 1
            except Exception as exc:
                _log.warning("CentralDB GRADE import failed: %s", exc)

            # Peer Review Scores (granular)
            try:
                pr_rows = conn2.execute("SELECT * FROM peer_review_scores").fetchall()
                for pr in pr_rows:
                    self.store_peer_review_score(
                        session_topic=session_topic,
                        cycle=pr["cycle"],
                        round_num=pr["round"],
                        reviewer=pr["reviewer"],
                        attribute=pr["attribute"],
                        score=pr["score"],
                        feedback=pr["feedback"] or "",
                    )
                    imported_pr_scores += 1
            except Exception as exc:
                _log.warning("CentralDB peer review scores import failed: %s", exc)

            # Peer Review Consensus
            try:
                pc_rows = conn2.execute("SELECT * FROM peer_review_consensus").fetchall()
                for pc in pc_rows:
                    self.store_peer_review_consensus(
                        session_topic=session_topic,
                        cycle=pc["cycle"],
                        attribute=pc["attribute"],
                        score=pc["score"],
                        feedback=pc["feedback"] or "",
                        improvement_plan=pc["improvement_plan"] or "",
                    )
                    imported_pr_consensus += 1
            except Exception as exc:
                _log.warning("CentralDB peer review consensus import failed: %s", exc)

            conn2.close()
        except Exception as exc:
            _log.warning("CentralDB session import failed: %s", exc)

        _log.info(
            "CentralDB import from %s: papers=%d, skipped=%d, fulltext=%d, embeddings=%d, "
            "claims=%d, rob=%d, grade=%d, pr_scores=%d, pr_consensus=%d",
            session_db_path.name, result["stored"], result["skipped"],
            imported_ft, imported_emb, imported_claims,
            imported_rob, imported_grade, imported_pr_scores, imported_pr_consensus,
        )

        return {
            "imported": result["stored"],
            "skipped": result["skipped"],
            "fulltext_imported": imported_ft,
            "embeddings_imported": imported_emb,
            "claims_imported": imported_claims,
            "rob_imported": imported_rob,
            "grade_imported": imported_grade,
            "pr_scores_imported": imported_pr_scores,
            "pr_consensus_imported": imported_pr_consensus,
        }
