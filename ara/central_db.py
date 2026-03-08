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
    embedding TEXT,
    fully_extracted INTEGER DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS paper_chunks (
    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL REFERENCES papers(paper_id),
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding TEXT,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_paper_index ON paper_chunks(paper_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_chunks_paper ON paper_chunks(paper_id);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON paper_chunks(chunk_id) WHERE embedding IS NOT NULL;
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
        # Embedding caches — loaded lazily on first MMR search
        self._claim_emb_cache: tuple | None = None   # (items, embs_np)
        self._chunk_emb_cache: tuple | None = None
        self._paper_emb_cache: tuple | None = None
        _log.info("CentralDB opened: %s", self._path)

    def _migrate(self) -> None:
        """Apply schema migrations for existing databases."""
        # Add unpaywall_checked column if missing
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(papers)").fetchall()}
        if "unpaywall_checked" not in cols:
            self._conn.execute("ALTER TABLE papers ADD COLUMN unpaywall_checked INTEGER DEFAULT 0")
        # Add journal_tier column if missing + backfill from DOI classification
        if "journal_tier" not in cols:
            self._conn.execute("ALTER TABLE papers ADD COLUMN journal_tier TEXT")
            self._conn.execute("ALTER TABLE papers ADD COLUMN journal_name TEXT")
            self._backfill_journal_tiers()
        # Add embedding + fully_extracted columns to claims if missing
        claim_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(claims)").fetchall()}
        if "embedding" not in claim_cols:
            self._conn.execute("ALTER TABLE claims ADD COLUMN embedding TEXT")
        if "fully_extracted" not in claim_cols:
            self._conn.execute("ALTER TABLE claims ADD COLUMN fully_extracted INTEGER DEFAULT 0")
        # Purge blacklisted papers from central DB
        self._purge_blacklisted()

    def _backfill_journal_tiers(self) -> None:
        """One-time backfill: classify all papers with DOIs by journal tier."""
        from .db import classify_journal
        rows = self._conn.execute(
            "SELECT paper_id, doi FROM papers WHERE doi IS NOT NULL AND doi != ''"
        ).fetchall()
        classified = 0
        for r in rows:
            j_name, j_tier = classify_journal(r[1])
            if j_tier:
                self._conn.execute(
                    "UPDATE papers SET journal_tier = ?, journal_name = ? WHERE paper_id = ?",
                    (j_tier, j_name, r[0]),
                )
                classified += 1
        self._conn.commit()
        _log.info("Central DB: backfilled journal tiers — %d/%d papers classified", classified, len(rows))

    def get_top_tier_papers(self, tier: str = "AAA", limit: int = 100) -> list[dict]:
        """Get all papers from a specific journal tier."""
        rows = self._conn.execute(
            "SELECT paper_id, title, abstract, authors, year, doi, journal_name, journal_tier, citation_count "
            "FROM papers WHERE journal_tier = ? ORDER BY citation_count DESC LIMIT ?",
            (tier, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_top_tier_relevant(self, query_embedding: list[float], tiers: list[str] | None = None,
                                  limit: int = 20, min_cosine: float = 0.40) -> list[dict]:
        """MMR search filtered to top-tier journal papers only."""
        if tiers is None:
            tiers = ["AAA", "AA"]
        # Get all top-tier paper IDs
        placeholders = ",".join("?" * len(tiers))
        tier_ids = {r[0] for r in self._conn.execute(
            f"SELECT paper_id FROM papers WHERE journal_tier IN ({placeholders})", tiers,
        ).fetchall()}
        if not tier_ids:
            return []

        # Search claims from top-tier papers using MMR
        all_claims = self.search_claims_mmr(
            query_embedding, limit=limit * 3, min_cosine=min_cosine, lam=0.7, paper_cap=2,
        )
        # Filter to only top-tier paper claims
        # Claims store paper_title, need to map to paper_ids
        tier_titles = {r[1].lower() for r in self._conn.execute(
            f"SELECT paper_id, title FROM papers WHERE journal_tier IN ({placeholders})", tiers,
        ).fetchall()}

        top_tier_claims = []
        for c in all_claims:
            if c.get("paper_title", "").lower() in tier_titles:
                top_tier_claims.append(c)
                if len(top_tier_claims) >= limit:
                    break
        return top_tier_claims

    def _purge_blacklisted(self) -> None:
        """Delete all papers from blacklisted publishers, including their claims, chunks, and embeddings."""
        from .db import is_blacklisted
        rows = self._conn.execute(
            "SELECT paper_id, doi FROM papers WHERE doi IS NOT NULL"
        ).fetchall()
        bl_papers = [(r[0], r[1]) for r in rows if is_blacklisted(r[1])]
        if not bl_papers:
            return
        bl_ids = [p[0] for p in bl_papers]
        bl_dois = [p[1] for p in bl_papers]
        id_ph = ",".join("?" for _ in bl_ids)
        doi_ph = ",".join("?" for _ in bl_dois)
        # Tables keyed by paper_id
        for table in ("paper_chunks", "paper_sources"):
            try:
                self._conn.execute(f"DELETE FROM {table} WHERE paper_id IN ({id_ph})", bl_ids)
            except Exception:
                pass
        # Tables keyed by paper_doi
        for table in ("claims", "risk_of_bias"):
            try:
                self._conn.execute(f"DELETE FROM {table} WHERE paper_doi IN ({doi_ph})", bl_dois)
            except Exception:
                pass
        # DOI validations keyed by doi
        try:
            self._conn.execute(f"DELETE FROM doi_validations WHERE doi IN ({doi_ph})", bl_dois)
        except Exception:
            pass
        # Delete the papers
        self._conn.execute(f"DELETE FROM papers WHERE paper_id IN ({id_ph})", bl_ids)
        self._conn.commit()
        _log.info("CentralDB: purged %d blacklisted papers (+ claims, chunks, embeddings)", len(bl_ids))

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

        from .db import is_blacklisted
        with self._lock:
            for p in papers:
                doi = (p.get("doi") or "").strip() or None
                title = p.get("title", "").strip()
                if not title:
                    paper_id_map.append(-1)
                    continue
                # Reject blacklisted publishers
                if is_blacklisted(doi):
                    paper_id_map.append(-1)
                    skipped += 1
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

    # ── Chunks ────────────────────────────────────────────────

    def store_chunks(self, paper_id: int, chunks: list[str]) -> int:
        """Store text chunks for a paper. Returns number stored."""
        now = _now()
        stored = 0
        with self._lock:
            for i, text in enumerate(chunks):
                try:
                    self._conn.execute(
                        "INSERT INTO paper_chunks (paper_id, chunk_index, chunk_text, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (paper_id, i, text, now),
                    )
                    stored += 1
                except sqlite3.IntegrityError:
                    pass  # already exists
            self._conn.commit()
        return stored

    def store_chunk_embedding(self, chunk_id: int, embedding: list[float]) -> None:
        emb_json = json.dumps(embedding)
        with self._lock:
            self._conn.execute(
                "UPDATE paper_chunks SET embedding = ? WHERE chunk_id = ?",
                (emb_json, chunk_id),
            )
            self._conn.commit()

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

    def get_claims_for_paper_by_title(self, title: str) -> list[dict[str, Any]]:
        """Get all claims for a paper by title hash."""
        t_hash = _title_hash(title)
        rows = self._conn.execute(
            "SELECT * FROM claims WHERE paper_title_hash = ? ORDER BY claim_id",
            (t_hash,),
        ).fetchall()
        return [dict(row) for row in rows]

    def is_paper_fully_extracted(self, title: str) -> bool:
        """Check if a paper has been fully extracted (topic-agnostic)."""
        t_hash = _title_hash(title)
        row = self._conn.execute(
            "SELECT fully_extracted FROM claims WHERE paper_title_hash = ? AND fully_extracted = 1 LIMIT 1",
            (t_hash,),
        ).fetchone()
        return bool(row)

    def mark_paper_fully_extracted(self, title: str) -> None:
        """Mark all claims for a paper as fully extracted (topic-agnostic)."""
        t_hash = _title_hash(title)
        with self._lock:
            self._conn.execute(
                "UPDATE claims SET fully_extracted = 1 WHERE paper_title_hash = ?",
                (t_hash,),
            )
            self._conn.commit()

    def store_claim_embedding(self, claim_id: int, embedding: list[float]) -> None:
        """Store embedding for a claim in central DB."""
        emb_json = json.dumps(embedding)
        with self._lock:
            self._conn.execute(
                "UPDATE claims SET embedding = ? WHERE claim_id = ?",
                (emb_json, claim_id),
            )
            self._conn.commit()

    # ── MMR (Maximal Marginal Relevance) Core ───────────────

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    @staticmethod
    def _mmr_select(
        candidates: list[tuple[dict, list[float], float]],
        limit: int,
        lam: float = 0.7,
        paper_cap: int = 0,
    ) -> list[dict]:
        """Select items using MMR: balance relevance vs diversity.

        candidates: list of (item_dict, embedding, topic_similarity)
        lam: 0.7 = mostly relevant, 0.3 = mostly diverse
        paper_cap: max items from same paper (0 = unlimited)

        Uses numpy for 50-100x speedup when available.
        """
        if not candidates:
            return []

        try:
            import numpy as np
            return CentralDB._mmr_numpy(candidates, limit, lam, paper_cap)
        except ImportError:
            pass

        # Pure Python fallback
        selected: list[tuple[dict, list[float]]] = []
        remaining = list(candidates)
        paper_counts: dict[str, int] = {}

        while remaining and len(selected) < limit:
            best_score = -999.0
            best_idx = 0

            for i, (item, emb, topic_sim) in enumerate(remaining):
                if paper_cap > 0:
                    pkey = item.get("paper_title", item.get("title", ""))
                    if paper_counts.get(pkey, 0) >= paper_cap:
                        continue

                max_sim_to_selected = 0.0
                if selected:
                    for _, sel_emb in selected:
                        sim = CentralDB._cosine(emb, sel_emb)
                        if sim > max_sim_to_selected:
                            max_sim_to_selected = sim

                mmr = lam * topic_sim - (1 - lam) * max_sim_to_selected
                if mmr > best_score:
                    best_score = mmr
                    best_idx = i

            item, emb, topic_sim = remaining.pop(best_idx)
            item["mmr_score"] = round(best_score, 4)
            selected.append((item, emb))

            if paper_cap > 0:
                pkey = item.get("paper_title", item.get("title", ""))
                paper_counts[pkey] = paper_counts.get(pkey, 0) + 1

        return [item for item, _ in selected]

    @staticmethod
    def _mmr_numpy(
        candidates: list[tuple[dict, list[float], float]],
        limit: int, lam: float, paper_cap: int,
    ) -> list[dict]:
        """Numpy-accelerated MMR — vectorized cosine similarity."""
        import numpy as np

        n = len(candidates)
        items = [c[0] for c in candidates]
        embs = np.array([c[1] for c in candidates], dtype=np.float32)
        topic_sims = np.array([c[2] for c in candidates], dtype=np.float32)

        # Pre-normalize for fast cosine
        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        embs_norm = embs / norms

        selected_indices: list[int] = []
        selected_embs: list[np.ndarray] = []
        mask = np.ones(n, dtype=bool)
        paper_counts: dict[str, int] = {}

        for _ in range(min(limit, n)):
            if not mask.any():
                break

            if selected_embs:
                sel_matrix = np.array(selected_embs)
                sim_to_selected = embs_norm @ sel_matrix.T
                max_sim = sim_to_selected.max(axis=1)
            else:
                max_sim = np.zeros(n, dtype=np.float32)

            mmr_scores = lam * topic_sims - (1 - lam) * max_sim
            mmr_scores[~mask] = -999.0

            if paper_cap > 0:
                for i in range(n):
                    if mask[i]:
                        pkey = items[i].get("paper_title", items[i].get("title", ""))
                        if paper_counts.get(pkey, 0) >= paper_cap:
                            mmr_scores[i] = -999.0

            best_idx = int(np.argmax(mmr_scores))
            if mmr_scores[best_idx] <= -999.0:
                break

            items[best_idx]["mmr_score"] = round(float(mmr_scores[best_idx]), 4)
            selected_indices.append(best_idx)
            selected_embs.append(embs_norm[best_idx])
            mask[best_idx] = False

            if paper_cap > 0:
                pkey = items[best_idx].get("paper_title", items[best_idx].get("title", ""))
                paper_counts[pkey] = paper_counts.get(pkey, 0) + 1

        return [items[i] for i in selected_indices]

    # ── Claim Search (MMR-diversified) ────────────────────

    def search_claims_by_cosine(
        self, query_embedding: list[float], limit: int = 50, min_cosine: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Find claims most relevant to a query embedding via cosine similarity."""
        rows = self._conn.execute(
            "SELECT claim_id, paper_doi, paper_title, claim_text, claim_type, "
            "confidence, supporting_quotes, section, sample_size, effect_size, "
            "p_value, confidence_interval, study_design, population, country, "
            "year_range, embedding FROM claims WHERE embedding IS NOT NULL"
        ).fetchall()
        if not rows:
            return []

        scored = []
        for row in rows:
            d = dict(row)
            emb = json.loads(d.pop("embedding"))
            sim = self._cosine(query_embedding, emb)
            if sim >= min_cosine:
                d["cosine_similarity"] = round(sim, 4)
                scored.append(d)

        scored.sort(key=lambda x: x["cosine_similarity"], reverse=True)
        return scored[:limit]

    def _load_embeddings_cached(self, table: str) -> tuple:
        """Load and cache embeddings as numpy arrays. Returns (items, embs_np, cache_key)."""
        import numpy as np

        cache_attr = f"_{table}_emb_cache"
        cached = getattr(self, cache_attr, None)
        if cached is not None:
            return cached

        if table == "claims":
            rows = self._conn.execute(
                "SELECT claim_id, paper_doi, paper_title, claim_text, claim_type, "
                "confidence, supporting_quotes, section, sample_size, effect_size, "
                "p_value, confidence_interval, study_design, population, country, "
                "year_range, embedding FROM claims WHERE embedding IS NOT NULL"
            ).fetchall()
        elif table == "chunks":
            rows = self._conn.execute(
                "SELECT c.chunk_id, c.paper_id, c.chunk_index, c.chunk_text, "
                "c.embedding, p.title AS paper_title, p.doi AS paper_doi, "
                "p.authors, p.year "
                "FROM paper_chunks c JOIN papers p ON c.paper_id = p.paper_id "
                "WHERE c.embedding IS NOT NULL"
            ).fetchall()
        elif table == "papers":
            rows = self._conn.execute(
                "SELECT paper_id, title, abstract, authors, year, doi, "
                "citation_count, embedding FROM papers WHERE embedding IS NOT NULL"
            ).fetchall()
        else:
            return ([], None)

        if not rows:
            return ([], None)

        items = []
        embs_list = []
        for row in rows:
            d = dict(row)
            emb = json.loads(d.pop("embedding"))
            items.append(d)
            embs_list.append(emb)

        embs_np = np.array(embs_list, dtype=np.float32)
        del embs_list  # free raw python list immediately
        # Pre-compute norms for fast cosine
        norms = np.linalg.norm(embs_np, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        embs_normed = embs_np / norms
        del embs_np  # only keep normed copy — saves 50% RAM

        result = (items, embs_normed, embs_normed)  # both point to same array
        setattr(self, cache_attr, result)
        _log.info("Cached %d %s embeddings (%d dims, %.0fMB)",
                  len(items), table, embs_normed.shape[1],
                  embs_normed.nbytes / 1024 / 1024)
        return result

    def invalidate_embedding_cache(self, table: str = "all") -> None:
        """Clear embedding caches after new embeddings are stored."""
        if table in ("claims", "all"):
            self._claim_emb_cache = None
        if table in ("chunks", "all"):
            self._chunk_emb_cache = None
        if table in ("papers", "all"):
            self._paper_emb_cache = None

    def extend_embedding_cache(self, table: str, new_items: list[dict], new_embeddings: list[list[float]]) -> None:
        """Append new embeddings directly into the live cache — no cold reload needed.
        If cache isn't loaded yet, stores for next load. Thread-safe via GIL."""
        if not new_items or not new_embeddings:
            return
        try:
            import numpy as np

            cache_attr = f"_{table}_emb_cache"
            cached = getattr(self, cache_attr, None)

            new_embs_np = np.array(new_embeddings, dtype=np.float32)
            norms = np.linalg.norm(new_embs_np, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            new_embs_normed = new_embs_np / norms

            if cached is not None and cached[1] is not None:
                old_items, old_normed, _ = cached
                merged_items = old_items + new_items
                merged_normed = np.vstack([old_normed, new_embs_normed])
                setattr(self, cache_attr, (merged_items, merged_normed, merged_normed))
                _log.info("Extended %s cache: %d → %d items (hot append)",
                          table, len(old_items), len(merged_items))
            else:
                # Cache not loaded yet — invalidate so next load picks up new data
                setattr(self, cache_attr, None)
                _log.info("Cache not loaded for %s — will include %d new items on next load",
                          table, len(new_items))
        except Exception as exc:
            _log.warning("extend_embedding_cache failed for %s: %s — invalidating", table, exc)
            setattr(self, f"_{table}_emb_cache", None)

    def _cached_mmr_search(
        self, table: str, query_embedding: list[float],
        limit: int, min_cosine: float, lam: float, paper_cap: int,
    ) -> list[dict[str, Any]]:
        """MMR search using cached numpy embeddings — fast after first call."""
        try:
            import numpy as np
        except ImportError:
            # Fall back to uncached path
            return self._uncached_mmr_search(table, query_embedding, limit, min_cosine, lam, paper_cap)

        items, embs_normed, embs_raw = self._load_embeddings_cached(table)
        if not items:
            return []

        query = np.array(query_embedding, dtype=np.float32)
        q_norm = np.linalg.norm(query)
        if q_norm == 0:
            return []
        query_normed = query / q_norm

        # Vectorized cosine similarity
        sims = embs_normed @ query_normed  # (n,)

        # Filter by min_cosine
        mask = sims >= min_cosine
        indices = np.where(mask)[0]

        if len(indices) == 0:
            return []

        # Build candidates for MMR
        candidates = []
        for i in indices:
            item = dict(items[i])  # copy
            item["cosine_similarity"] = round(float(sims[i]), 4)
            candidates.append((item, embs_raw[i], float(sims[i])))

        return self._mmr_select(candidates, limit, lam, paper_cap)

    def _uncached_mmr_search(
        self, table: str, query_embedding: list[float],
        limit: int, min_cosine: float, lam: float, paper_cap: int,
    ) -> list[dict[str, Any]]:
        """Fallback MMR search without numpy cache."""
        if table == "claims":
            rows = self._conn.execute(
                "SELECT claim_id, paper_doi, paper_title, claim_text, claim_type, "
                "confidence, supporting_quotes, section, sample_size, effect_size, "
                "p_value, confidence_interval, study_design, population, country, "
                "year_range, embedding FROM claims WHERE embedding IS NOT NULL"
            ).fetchall()
        elif table == "chunks":
            rows = self._conn.execute(
                "SELECT c.chunk_id, c.paper_id, c.chunk_index, c.chunk_text, "
                "c.embedding, p.title AS paper_title, p.doi AS paper_doi, "
                "p.authors, p.year "
                "FROM paper_chunks c JOIN papers p ON c.paper_id = p.paper_id "
                "WHERE c.embedding IS NOT NULL"
            ).fetchall()
        elif table == "papers":
            rows = self._conn.execute(
                "SELECT paper_id, title, abstract, authors, year, doi, "
                "citation_count, embedding FROM papers WHERE embedding IS NOT NULL"
            ).fetchall()
        else:
            return []

        candidates = self._bulk_cosine_filter(rows, query_embedding, min_cosine)
        return self._mmr_select(candidates, limit, lam, paper_cap)

    @staticmethod
    def _bulk_cosine_filter(
        rows: list, query_embedding: list[float], min_cosine: float,
    ) -> list[tuple[dict, list[float], float]]:
        """Vectorized cosine filtering — 20-50x faster than row-by-row."""
        if not rows:
            return []

        try:
            import numpy as np
            items = []
            embs_raw = []
            for row in rows:
                d = dict(row)
                emb = json.loads(d.pop("embedding"))
                items.append(d)
                embs_raw.append(emb)

            embs = np.array(embs_raw, dtype=np.float32)
            query = np.array(query_embedding, dtype=np.float32)

            # Vectorized cosine similarity
            norms = np.linalg.norm(embs, axis=1)
            q_norm = np.linalg.norm(query)
            if q_norm == 0:
                return []
            sims = (embs @ query) / (norms * q_norm + 1e-10)

            # Filter and build candidates
            mask = sims >= min_cosine
            candidates = []
            for i in range(len(items)):
                if mask[i]:
                    items[i]["cosine_similarity"] = round(float(sims[i]), 4)
                    candidates.append((items[i], embs_raw[i], float(sims[i])))
            return candidates

        except ImportError:
            # Pure Python fallback
            candidates = []
            for row in rows:
                d = dict(row)
                emb = json.loads(d.pop("embedding"))
                sim = CentralDB._cosine(query_embedding, emb)
                if sim >= min_cosine:
                    d["cosine_similarity"] = round(sim, 4)
                    candidates.append((d, emb, sim))
            return candidates

    def search_claims_mmr(
        self, query_embedding: list[float], limit: int = 150,
        min_cosine: float = 0.6, lam: float = 0.5, paper_cap: int = 3,
    ) -> list[dict[str, Any]]:
        """Find diverse, relevant claims using MMR. Max paper_cap claims per paper."""
        return self._cached_mmr_search("claims", query_embedding, limit, min_cosine, lam, paper_cap)

    # ── Chunk Search (MMR-diversified) ────────────────────

    def search_chunks_mmr(
        self, query_embedding: list[float], limit: int = 100,
        min_cosine: float = 0.6, lam: float = 0.5, paper_cap: int = 2,
    ) -> list[dict[str, Any]]:
        """Find diverse, relevant full-text chunks using MMR."""
        return self._cached_mmr_search("chunks", query_embedding, limit, min_cosine, lam, paper_cap)

    # ── Paper Search (MMR-diversified) ────────────────────

    def search_papers_mmr(
        self, query_embedding: list[float], limit: int = 60,
        min_cosine: float = 0.6, lam: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Find diverse, relevant papers using MMR."""
        return self._cached_mmr_search("papers", query_embedding, limit, min_cosine, lam, paper_cap=0)

    # ── Claim Clustering & Analysis ──────────────────────

    def cluster_claims(
        self, claim_ids: list[int] | None = None, similarity_threshold: float = 0.85,
    ) -> list[dict[str, Any]]:
        """Cluster claims by embedding similarity into evidence groups.

        Returns list of clusters, each with:
        - claims: list of claim dicts
        - n_studies: number of unique papers
        - countries: set of countries
        - study_designs: set of designs
        - effect_sizes: list of effect sizes
        - representative: the highest-confidence claim
        """
        where = "WHERE embedding IS NOT NULL"
        params: tuple = ()
        if claim_ids:
            placeholders = ",".join("?" for _ in claim_ids)
            where = f"WHERE embedding IS NOT NULL AND claim_id IN ({placeholders})"
            params = tuple(claim_ids)

        rows = self._conn.execute(
            f"SELECT claim_id, paper_doi, paper_title, claim_text, claim_type, "
            f"confidence, sample_size, effect_size, p_value, study_design, "
            f"population, country, year_range, embedding "
            f"FROM claims {where}", params,
        ).fetchall()

        if not rows:
            return []

        items = []
        for row in rows:
            d = dict(row)
            emb = json.loads(d.pop("embedding"))
            items.append((d, emb))

        # Greedy clustering: assign each item to first cluster with sim > threshold
        clusters: list[list[tuple[dict, list[float]]]] = []
        for item, emb in items:
            assigned = False
            for cluster in clusters:
                rep_emb = cluster[0][1]  # representative = first item
                if self._cosine(emb, rep_emb) >= similarity_threshold:
                    cluster.append((item, emb))
                    assigned = True
                    break
            if not assigned:
                clusters.append([(item, emb)])

        # Build cluster summaries
        result = []
        for cluster in clusters:
            if len(cluster) < 2:
                continue  # Skip singletons — not convergent evidence
            claims = [item for item, _ in cluster]
            papers = set(c.get("paper_title", "") for c in claims)
            countries = set(c.get("country", "") for c in claims if c.get("country"))
            designs = set(c.get("study_design", "") for c in claims if c.get("study_design"))
            effects = [c.get("effect_size", "") for c in claims if c.get("effect_size")]
            # Representative = highest confidence
            representative = max(claims, key=lambda c: c.get("confidence", 0))
            result.append({
                "claims": claims,
                "n_studies": len(papers),
                "n_claims": len(claims),
                "countries": sorted(countries - {""}),
                "study_designs": sorted(designs - {""}),
                "effect_sizes": [e for e in effects if e],
                "representative": representative,
            })

        result.sort(key=lambda c: c["n_studies"], reverse=True)
        return result

    def detect_contradictions(
        self, claim_ids: list[int] | None = None, similarity_threshold: float = 0.80,
    ) -> list[dict[str, Any]]:
        """Find claim pairs that are semantically similar but may contradict.

        Heuristic: high cosine (same topic) + different papers + opposing signals.
        Returns pairs for writer to acknowledge in Discussion.
        """
        where = "WHERE embedding IS NOT NULL AND claim_type = 'finding'"
        params: tuple = ()
        if claim_ids:
            placeholders = ",".join("?" for _ in claim_ids)
            where = f"WHERE embedding IS NOT NULL AND claim_type = 'finding' AND claim_id IN ({placeholders})"
            params = tuple(claim_ids)

        rows = self._conn.execute(
            f"SELECT claim_id, paper_title, claim_text, confidence, "
            f"effect_size, sample_size, study_design, country, embedding "
            f"FROM claims {where}", params,
        ).fetchall()

        if len(rows) < 2:
            return []

        items = []
        for row in rows:
            d = dict(row)
            emb = json.loads(d.pop("embedding"))
            items.append((d, emb))

        # Opposing signal keywords
        _positive = {"increase", "positive", "higher", "improve", "enhance", "benefit", "growth", "gain"}
        _negative = {"decrease", "negative", "lower", "reduce", "decline", "harm", "loss", "risk", "worsen"}

        def _signal(text: str) -> str:
            words = set(text.lower().split())
            pos = len(words & _positive)
            neg = len(words & _negative)
            if pos > neg:
                return "positive"
            if neg > pos:
                return "negative"
            return "neutral"

        contradictions = []
        for i, (a, emb_a) in enumerate(items):
            for j, (b, emb_b) in enumerate(items):
                if j <= i:
                    continue
                # Different papers only
                if a.get("paper_title") == b.get("paper_title"):
                    continue
                sim = self._cosine(emb_a, emb_b)
                if sim < similarity_threshold:
                    continue
                # Check opposing signals
                sig_a = _signal(a["claim_text"])
                sig_b = _signal(b["claim_text"])
                if sig_a != "neutral" and sig_b != "neutral" and sig_a != sig_b:
                    contradictions.append({
                        "claim_a": a,
                        "claim_b": b,
                        "similarity": round(sim, 4),
                        "signal_a": sig_a,
                        "signal_b": sig_b,
                    })

        contradictions.sort(key=lambda c: c["similarity"], reverse=True)
        return contradictions[:20]  # Top 20 most salient

    def analyze_coverage(
        self, topic_embedding: list[float], facets: list[str],
        facet_embeddings: list[list[float]],
    ) -> list[dict[str, Any]]:
        """Analyze claim coverage across topic facets. Finds genuine gaps.

        Returns per-facet: n_claims, avg_confidence, avg_rob, top_claim.
        Facets with low n_claims = genuine research gaps.
        """
        rows = self._conn.execute(
            "SELECT claim_id, paper_title, claim_text, claim_type, confidence, "
            "study_design, country, embedding FROM claims WHERE embedding IS NOT NULL"
        ).fetchall()
        if not rows:
            return []

        items = []
        for row in rows:
            d = dict(row)
            emb = json.loads(d.pop("embedding"))
            items.append((d, emb))

        results = []
        for facet_name, facet_emb in zip(facets, facet_embeddings):
            matching = []
            for item, emb in items:
                sim = self._cosine(facet_emb, emb)
                if sim >= 0.55:
                    item_copy = dict(item)
                    item_copy["facet_similarity"] = round(sim, 4)
                    matching.append(item_copy)

            matching.sort(key=lambda x: x["facet_similarity"], reverse=True)
            papers = set(c.get("paper_title", "") for c in matching)
            avg_conf = sum(c.get("confidence", 0.5) for c in matching) / len(matching) if matching else 0

            results.append({
                "facet": facet_name,
                "n_claims": len(matching),
                "n_papers": len(papers),
                "avg_confidence": round(avg_conf, 3),
                "top_claim": matching[0] if matching else None,
                "gap_severity": "critical" if len(matching) < 3 else "moderate" if len(matching) < 8 else "covered",
            })

        results.sort(key=lambda x: x["n_claims"])
        return results

    def claim_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]

    def claims_with_embeddings_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM claims WHERE embedding IS NOT NULL").fetchone()[0]

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
