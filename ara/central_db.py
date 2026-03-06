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
        row = self._conn.execute("SELECT * FROM papers WHERE doi = ?", (doi,)).fetchone()
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

        # Import claims
        imported_claims = 0
        try:
            conn2 = sqlite3.connect(str(session_db_path))
            conn2.row_factory = sqlite3.Row
            claim_rows = conn2.execute(
                "SELECT c.*, p.title as paper_title, p.doi as paper_doi "
                "FROM claims c JOIN papers p ON c.paper_id = p.paper_id"
            ).fetchall()
            conn2.close()

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
                claim_result = self.store_claims(central_claims)
                imported_claims = claim_result["stored"]
        except Exception as exc:
            _log.warning("CentralDB claim import failed: %s", exc)

        _log.info("CentralDB import from %s: papers=%d, skipped=%d, fulltext=%d, embeddings=%d, claims=%d",
                   session_db_path.name, result["stored"], result["skipped"], imported_ft, imported_emb, imported_claims)

        return {
            "imported": result["stored"],
            "skipped": result["skipped"],
            "fulltext_imported": imported_ft,
            "embeddings_imported": imported_emb,
            "claims_imported": imported_claims,
        }
