# Location: ara/db.py
# Purpose: SQLite session database layer — sessions, papers, claims, hypotheses, branches
# Functions: ARADB (all CRUD operations), delegates paper storage to CentralDB
# Calls: sqlite3, central_db.CentralDB
# Imports: sqlite3, json, datetime

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Column whitelists per table — prevents SQL injection via kwargs
_ALLOWED_COLUMNS: dict[str, set[str]] = {
    "sessions": {
        "topic", "paper_type", "citation_style", "budget_cap", "budget_spent",
        "deep_read_limit", "enabled_sources", "status", "current_phase",
    },
    "claims": {
        "claim_text", "claim_type", "confidence", "supporting_quotes", "section",
        "verification_status", "retraction_checked", "citation_count_at_check",
        "doi_valid", "verifier_notes", "contradicts",
        "sample_size", "effect_size", "p_value", "confidence_interval",
        "study_design", "population", "country", "year_range",
    },
    "hypotheses": {
        "hypothesis_text", "rank", "iteration", "novelty", "feasibility",
        "evidence_strength", "methodology_fit", "impact", "reproducibility",
        "custom_dimensions", "overall_score", "critic_decision", "critic_feedback",
        "selected", "source_branch_id", "generation",
    },
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    paper_type TEXT NOT NULL DEFAULT 'research_article',
    citation_style TEXT NOT NULL DEFAULT 'apa7',
    budget_cap REAL NOT NULL DEFAULT 5.0,
    budget_spent REAL NOT NULL DEFAULT 0.0,
    deep_read_limit INTEGER NOT NULL DEFAULT 100,
    enabled_sources TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    current_phase TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS papers (
    paper_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    title TEXT NOT NULL,
    abstract TEXT,
    authors TEXT,
    year INTEGER,
    doi TEXT,
    source TEXT NOT NULL,
    url TEXT,
    citation_count INTEGER DEFAULT 0,
    relevance_score REAL,
    selected_for_deep_read INTEGER DEFAULT 0,
    full_text TEXT,
    full_text_path TEXT,
    embedding TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    paper_id INTEGER NOT NULL REFERENCES papers(paper_id),
    claim_text TEXT NOT NULL,
    claim_type TEXT NOT NULL DEFAULT 'finding',
    confidence REAL DEFAULT 0.5,
    supporting_quotes TEXT,
    section TEXT,
    verification_status TEXT,
    retraction_checked INTEGER DEFAULT 0,
    citation_count_at_check INTEGER,
    doi_valid INTEGER,
    verifier_notes TEXT,
    contradicts TEXT,
    sample_size TEXT,
    effect_size TEXT,
    p_value TEXT,
    confidence_interval TEXT,
    study_design TEXT,
    population TEXT,
    country TEXT,
    year_range TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hypotheses (
    hypothesis_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    hypothesis_text TEXT NOT NULL,
    rank INTEGER,
    iteration INTEGER NOT NULL DEFAULT 1,
    novelty REAL,
    feasibility REAL,
    evidence_strength REAL,
    methodology_fit REAL,
    impact REAL,
    reproducibility REAL,
    custom_dimensions TEXT,
    overall_score REAL,
    critic_decision TEXT,
    critic_feedback TEXT,
    selected INTEGER DEFAULT 0,
    source_branch_id INTEGER,
    generation INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS branches (
    branch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(hypothesis_id),
    branch_type TEXT NOT NULL,
    finding_text TEXT NOT NULL,
    source_paper_id INTEGER REFERENCES papers(paper_id),
    confidence REAL,
    domain TEXT,
    round INTEGER DEFAULT 1,
    score REAL,
    status TEXT DEFAULT 'pending',
    papers_found INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_gates (
    gate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    phase TEXT NOT NULL,
    gate_data TEXT,
    action TEXT,
    user_comments TEXT,
    edited_data TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    rule_text TEXT NOT NULL,
    rule_type TEXT NOT NULL DEFAULT 'exclude',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    event_type TEXT NOT NULL,
    phase TEXT,
    payload TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_log (
    cost_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    phase TEXT,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prisma_stats (
    stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    stage TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    details TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quality_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    dimension TEXT NOT NULL,
    score REAL,
    details TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phase_checkpoints (
    checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    phase TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    result_summary TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_of_bias (
    rob_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    paper_id INTEGER NOT NULL REFERENCES papers(paper_id),
    framework TEXT NOT NULL DEFAULT 'JBI',
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
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    outcome TEXT NOT NULL,
    n_studies INTEGER NOT NULL DEFAULT 0,
    study_designs TEXT,
    risk_of_bias_rating TEXT DEFAULT 'not serious',
    inconsistency TEXT DEFAULT 'not serious',
    indirectness TEXT DEFAULT 'not serious',
    imprecision TEXT DEFAULT 'not serious',
    publication_bias TEXT DEFAULT 'undetected',
    effect_size_range TEXT,
    certainty TEXT NOT NULL DEFAULT 'low',
    direction TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_session ON papers(session_id);
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_claims_session ON claims(session_id);
CREATE INDEX IF NOT EXISTS idx_claims_paper ON claims(paper_id);
CREATE INDEX IF NOT EXISTS idx_hypotheses_session ON hypotheses(session_id);
CREATE INDEX IF NOT EXISTS idx_prisma_session ON prisma_stats(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_session ON quality_audit(session_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON phase_checkpoints(session_id);
CREATE INDEX IF NOT EXISTS idx_rob_session ON risk_of_bias(session_id);
CREATE INDEX IF NOT EXISTS idx_grade_session ON grade_evidence(session_id);

CREATE TABLE IF NOT EXISTS peer_review_scores (
    score_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    cycle INTEGER NOT NULL DEFAULT 1,
    round INTEGER NOT NULL,
    reviewer TEXT NOT NULL,
    attribute TEXT NOT NULL,
    score INTEGER NOT NULL,
    feedback TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS peer_review_consensus (
    consensus_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    cycle INTEGER NOT NULL DEFAULT 1,
    attribute TEXT NOT NULL,
    score INTEGER NOT NULL,
    feedback TEXT,
    improvement_plan TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pr_scores_session ON peer_review_scores(session_id);
CREATE INDEX IF NOT EXISTS idx_pr_consensus_session ON peer_review_consensus(session_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ARADB:
    def __init__(self, db_path: Path, central_db: Any | None = None):
        self._path = db_path
        self._central = central_db  # CentralDB instance (optional)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Add columns/tables that may not exist in older databases."""
        cur = self._conn.execute("PRAGMA table_info(papers)")
        existing = {row[1] for row in cur.fetchall()}
        if "embedding" not in existing:
            self._conn.execute("ALTER TABLE papers ADD COLUMN embedding TEXT")
            self._conn.commit()
        # Ensure phase_checkpoints table exists (for older DBs)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS phase_checkpoints ("
            "checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id INTEGER NOT NULL REFERENCES sessions(session_id), "
            "phase TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'completed', "
            "result_summary TEXT, created_at TEXT NOT NULL)"
        )
        # Add unique index on (session_id, doi) to prevent duplicate papers
        try:
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_session_doi "
                "ON papers(session_id, doi) WHERE doi IS NOT NULL"
            )
        except Exception:
            pass  # Index may already exist or partial indexes not supported
        # Ensure risk_of_bias and grade_evidence tables exist (for older DBs)
        self._conn.executescript(
            "CREATE TABLE IF NOT EXISTS risk_of_bias ("
            "rob_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id INTEGER NOT NULL REFERENCES sessions(session_id), "
            "paper_id INTEGER NOT NULL REFERENCES papers(paper_id), "
            "framework TEXT NOT NULL DEFAULT 'JBI', "
            "selection_bias TEXT DEFAULT 'unclear', "
            "performance_bias TEXT DEFAULT 'unclear', "
            "detection_bias TEXT DEFAULT 'unclear', "
            "attrition_bias TEXT DEFAULT 'unclear', "
            "reporting_bias TEXT DEFAULT 'unclear', "
            "overall_risk TEXT DEFAULT 'unclear', "
            "notes TEXT, created_at TEXT NOT NULL);\n"
            "CREATE TABLE IF NOT EXISTS grade_evidence ("
            "grade_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id INTEGER NOT NULL REFERENCES sessions(session_id), "
            "outcome TEXT NOT NULL, "
            "n_studies INTEGER NOT NULL DEFAULT 0, "
            "study_designs TEXT, "
            "risk_of_bias_rating TEXT DEFAULT 'not serious', "
            "inconsistency TEXT DEFAULT 'not serious', "
            "indirectness TEXT DEFAULT 'not serious', "
            "imprecision TEXT DEFAULT 'not serious', "
            "publication_bias TEXT DEFAULT 'undetected', "
            "effect_size_range TEXT, "
            "certainty TEXT NOT NULL DEFAULT 'low', "
            "direction TEXT, notes TEXT, created_at TEXT NOT NULL);"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── Sessions ────────────────────────────────────────────────

    def create_session(self, topic: str, **kwargs: Any) -> int:
        now = _now()
        allowed = _ALLOWED_COLUMNS["sessions"]
        for k in kwargs:
            if k not in allowed:
                raise ValueError(f"Invalid column for sessions: {k}")
        cols = ["topic", "created_at", "updated_at"]
        vals: list[Any] = [topic, now, now]
        for k, v in kwargs.items():
            cols.append(k)
            vals.append(v)
        placeholders = ", ".join("?" for _ in vals)
        col_str = ", ".join(cols)
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO sessions ({col_str}) VALUES ({placeholders})", vals,
            )
            self._conn.commit()
        return cur.lastrowid  # type: ignore

    def get_session(self, session_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_session(self, session_id: int, **kwargs: Any) -> None:
        allowed = _ALLOWED_COLUMNS["sessions"]
        for k in kwargs:
            if k not in allowed:
                raise ValueError(f"Invalid column for sessions: {k}")
        kwargs["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [session_id]
        with self._lock:
            self._conn.execute(f"UPDATE sessions SET {sets} WHERE session_id = ?", vals)
            self._conn.commit()

    # ── Papers ──────────────────────────────────────────────────

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Normalize title for dedup — lowercase, strip punctuation/whitespace."""
        import re
        t = title.lower().strip()
        t = re.sub(r'[^\w\s]', '', t)  # Remove punctuation
        t = re.sub(r'\s+', ' ', t)     # Collapse whitespace
        return t

    def store_papers(self, session_id: int, papers: list[dict[str, Any]]) -> int:
        now = _now()
        stored = 0
        skipped = 0

        # Also store in central DB if available
        if self._central:
            try:
                self._central.store_papers(papers)
            except Exception as exc:
                _log.warning("CentralDB store failed (continuing with session DB): %s", exc)

        with self._lock:
            for p in papers:
                doi = (p.get("doi") or "").strip() or None  # Normalize empty string to None
                title = p.get("title", "").strip()
                if not title:
                    continue
                norm_title = self._normalize_title(title)
                # Dedup by DOI first, then normalized title
                if doi:
                    existing = self._conn.execute(
                        "SELECT paper_id FROM papers WHERE session_id = ? AND doi = ?",
                        (session_id, doi),
                    ).fetchone()
                    if existing:
                        skipped += 1
                        continue
                # Always check title too (catches DOI-less dupes and DOI variants)
                existing = self._conn.execute(
                    "SELECT paper_id FROM papers WHERE session_id = ? AND LOWER(REPLACE(title, '.', '')) LIKE ?",
                    (session_id, f"%{norm_title[:80]}%"),
                ).fetchone()
                if existing:
                    skipped += 1
                    continue

                authors_json = json.dumps(p.get("authors", []))
                try:
                    self._conn.execute(
                        "INSERT INTO papers (session_id, title, abstract, authors, year, doi, source, url, citation_count, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (session_id, title, p.get("abstract"), authors_json,
                         p.get("year"), doi, p.get("source", "unknown"),
                         p.get("url"), p.get("citation_count", 0), now),
                    )
                    stored += 1
                except sqlite3.IntegrityError:
                    skipped += 1  # Unique constraint violation — duplicate
            self._conn.commit()
        if skipped > 0:
            _log.info("store_papers: stored=%d, skipped=%d duplicates", stored, skipped)
        return stored

    def get_paper(self, paper_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM papers WHERE paper_id = ?", (paper_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["authors"] = json.loads(d.get("authors") or "[]")
        return d

    def paper_count(self, session_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM papers WHERE session_id = ?", (session_id,),
            ).fetchone()
        return row[0] if row else 0

    def get_papers(self, session_id: int, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM papers WHERE session_id = ? ORDER BY citation_count DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["authors"] = json.loads(d.get("authors") or "[]")
            results.append(d)
        return results

    def search_papers_by_keyword(self, session_id: int, keyword: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM papers WHERE session_id = ? AND (title LIKE ? OR abstract LIKE ?) "
            "ORDER BY citation_count DESC LIMIT ?",
            (session_id, f"%{keyword}%", f"%{keyword}%", limit),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["authors"] = json.loads(d.get("authors") or "[]")
            results.append(d)
        return results

    def update_paper_fulltext(self, doi: str, url: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE papers SET full_text_path = ? WHERE doi = ?", (url, doi),
            )
            self._conn.commit()

    def store_fulltext_content(self, doi: str, text: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE papers SET full_text = ? WHERE doi = ?", (text, doi),
            )
            self._conn.commit()
        # Also store in central DB
        if self._central:
            try:
                central_paper = self._central.get_paper_by_doi(doi)
                if central_paper:
                    self._central.store_fulltext(central_paper["paper_id"], text)
            except Exception:
                pass  # Non-critical

    def get_cited_papers(self, session_id: int) -> list[dict[str, Any]]:
        # Return only papers that have claims extracted (i.e., actually analyzed/cited)
        rows = self._conn.execute(
            "SELECT DISTINCT p.* FROM papers p "
            "INNER JOIN claims c ON p.paper_id = c.paper_id AND p.session_id = c.session_id "
            "WHERE p.session_id = ? "
            "ORDER BY p.citation_count DESC",
            (session_id,),
        ).fetchall()
        if not rows:
            # Fallback: return papers selected for deep read
            rows = self._conn.execute(
                "SELECT * FROM papers WHERE session_id = ? AND selected_for_deep_read = 1 "
                "ORDER BY citation_count DESC",
                (session_id,),
            ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["authors"] = json.loads(d.get("authors") or "[]")
            results.append(d)
        return results

    # ── Embeddings ────────────────────────────────────────────────

    def store_embedding(self, paper_id: int, embedding: list[float]) -> None:
        emb_json = json.dumps(embedding)
        with self._lock:
            self._conn.execute(
                "UPDATE papers SET embedding = ? WHERE paper_id = ?",
                (emb_json, paper_id),
            )
            self._conn.commit()
        # Also store in central DB by DOI lookup
        if self._central:
            try:
                paper = self.get_paper(paper_id)
                if paper and paper.get("doi"):
                    central_paper = self._central.get_paper_by_doi(paper["doi"])
                    if central_paper:
                        self._central.store_embedding(central_paper["paper_id"], embedding)
            except Exception:
                pass  # Non-critical

    def get_unembedded_papers(self, session_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT paper_id, title, abstract, authors, full_text FROM papers "
            "WHERE session_id = ? AND embedding IS NULL",
            (session_id,),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["authors"] = json.loads(d.get("authors") or "[]")
            results.append(d)
        return results

    def get_papers_with_embeddings(self, session_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT paper_id, title, abstract, authors, year, doi, source, "
            "citation_count, embedding FROM papers "
            "WHERE session_id = ? AND embedding IS NOT NULL",
            (session_id,),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["authors"] = json.loads(d.get("authors") or "[]")
            d["embedding"] = json.loads(d["embedding"])
            results.append(d)
        return results

    # ── Claims ──────────────────────────────────────────────────

    def store_claim(self, session_id: int, paper_id: int, **kwargs: Any) -> int:
        now = _now()
        allowed = _ALLOWED_COLUMNS["claims"]
        for k in kwargs:
            if k not in allowed:
                raise ValueError(f"Invalid column for claims: {k}")
        kwargs.update({"session_id": session_id, "paper_id": paper_id, "created_at": now})
        cols = list(kwargs.keys())
        vals = list(kwargs.values())
        placeholders = ", ".join("?" for _ in vals)
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO claims ({', '.join(cols)}) VALUES ({placeholders})", vals,
            )
            self._conn.commit()
        return cur.lastrowid  # type: ignore

    def get_claims(self, session_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM claims WHERE session_id = ?", (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Hypotheses ──────────────────────────────────────────────

    def store_hypothesis(self, session_id: int, **kwargs: Any) -> int:
        now = _now()
        allowed = _ALLOWED_COLUMNS["hypotheses"]
        for k in kwargs:
            if k not in allowed:
                raise ValueError(f"Invalid column for hypotheses: {k}")
        kwargs.update({"session_id": session_id, "created_at": now})
        cols = list(kwargs.keys())
        vals = list(kwargs.values())
        placeholders = ", ".join("?" for _ in vals)
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO hypotheses ({', '.join(cols)}) VALUES ({placeholders})", vals,
            )
            self._conn.commit()
        return cur.lastrowid  # type: ignore

    def get_hypotheses(self, session_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM hypotheses WHERE session_id = ? ORDER BY overall_score DESC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Rules ───────────────────────────────────────────────────

    def add_rule(self, session_id: int, rule_text: str, rule_type: str = "exclude") -> int:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO rules (session_id, rule_text, rule_type, created_at) VALUES (?, ?, ?, ?)",
                (session_id, rule_text, rule_type, now),
            )
            self._conn.commit()
        return cur.lastrowid  # type: ignore

    def get_rules(self, session_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM rules WHERE session_id = ? AND is_active = 1", (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Cost tracking ───────────────────────────────────────────

    def log_cost(self, session_id: int, model: str,
                  input_tokens: int, output_tokens: int, cost_usd: float,
                  phase: str | None = None) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO cost_log (session_id, phase, model, input_tokens, output_tokens, cost_usd, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, phase, model, input_tokens, output_tokens, cost_usd, now),
            )
            self._conn.execute(
                "UPDATE sessions SET budget_spent = budget_spent + ? WHERE session_id = ?",
                (cost_usd, session_id),
            )
            self._conn.commit()

    def get_total_cost(self, session_id: int) -> float:
        row = self._conn.execute(
            "SELECT budget_spent FROM sessions WHERE session_id = ?", (session_id,),
        ).fetchone()
        return row["budget_spent"] if row else 0.0

    # ── Events ──────────────────────────────────────────────────

    def log_event(self, session_id: int, event_type: str,
                   phase: str | None = None, payload: str | None = None) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (session_id, event_type, phase, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, event_type, phase, payload, now),
            )
            self._conn.commit()

    # ── Approval gates ──────────────────────────────────────────

    def log_gate(self, session_id: int, phase: str, gate_data: str | None = None,
                  action: str | None = None, user_comments: str | None = None) -> int:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO approval_gates (session_id, phase, gate_data, action, user_comments, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, phase, gate_data, action, user_comments, now),
            )
            self._conn.commit()
        return cur.lastrowid  # type: ignore

    # ── PRISMA & Quality Audit ──────────────────────────────────

    def store_prisma_stat(self, session_id: int, stage: str, count: int, details: str | None = None) -> None:
        now = _now()
        with self._lock:
            # UPSERT: update if stage already exists for this session, insert otherwise
            existing = self._conn.execute(
                "SELECT stat_id FROM prisma_stats WHERE session_id = ? AND stage = ?",
                (session_id, stage),
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE prisma_stats SET count = ?, details = ?, created_at = ? WHERE stat_id = ?",
                    (count, details, now, existing[0]),
                )
            else:
                self._conn.execute(
                    "INSERT INTO prisma_stats (session_id, stage, count, details, created_at) VALUES (?, ?, ?, ?, ?)",
                    (session_id, stage, count, details, now),
                )
            self._conn.commit()

    def get_prisma_stats(self, session_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM prisma_stats WHERE session_id = ? ORDER BY stat_id", (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def store_quality_audit(self, session_id: int, dimension: str, score: float, details: str | None = None) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO quality_audit (session_id, dimension, score, details, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, dimension, score, details, now),
            )
            self._conn.commit()

    def get_quality_audit(self, session_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM quality_audit WHERE session_id = ? ORDER BY audit_id", (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Risk of Bias ─────────────────────────────────────────────

    def _get_session_topic(self, session_id: int) -> str:
        row = self._conn.execute("SELECT topic FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return row["topic"] if row else ""

    def store_risk_of_bias(
        self, session_id: int, paper_id: int, *,
        framework: str = "JBI",
        selection_bias: str = "unclear",
        performance_bias: str = "unclear",
        detection_bias: str = "unclear",
        attrition_bias: str = "unclear",
        reporting_bias: str = "unclear",
        overall_risk: str = "unclear",
        notes: str | None = None,
    ) -> int:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO risk_of_bias "
                "(session_id, paper_id, framework, selection_bias, performance_bias, "
                "detection_bias, attrition_bias, reporting_bias, overall_risk, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, paper_id, framework, selection_bias, performance_bias,
                 detection_bias, attrition_bias, reporting_bias, overall_risk, notes, now),
            )
            self._conn.commit()
        # Sync to central DB
        if self._central:
            try:
                paper = self._conn.execute(
                    "SELECT title, doi FROM papers WHERE paper_id = ?", (paper_id,)
                ).fetchone()
                if paper:
                    self._central.store_risk_of_bias(
                        session_topic=self._get_session_topic(session_id),
                        paper_title=paper["title"], paper_doi=paper["doi"],
                        framework=framework, selection_bias=selection_bias,
                        performance_bias=performance_bias, detection_bias=detection_bias,
                        attrition_bias=attrition_bias, reporting_bias=reporting_bias,
                        overall_risk=overall_risk, notes=notes,
                    )
            except Exception as exc:
                _log.warning("CentralDB RoB sync failed: %s", exc)
        return cur.lastrowid  # type: ignore

    def get_risk_of_bias(self, session_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT r.*, p.title, p.authors, p.year FROM risk_of_bias r "
            "JOIN papers p ON r.paper_id = p.paper_id "
            "WHERE r.session_id = ? ORDER BY r.rob_id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── GRADE Evidence ─────────────────────────────────────────

    def store_grade_evidence(
        self, session_id: int, outcome: str, *,
        n_studies: int = 0,
        study_designs: str | None = None,
        risk_of_bias_rating: str = "not serious",
        inconsistency: str = "not serious",
        indirectness: str = "not serious",
        imprecision: str = "not serious",
        publication_bias: str = "undetected",
        effect_size_range: str | None = None,
        certainty: str = "low",
        direction: str | None = None,
        notes: str | None = None,
    ) -> int:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO grade_evidence "
                "(session_id, outcome, n_studies, study_designs, risk_of_bias_rating, "
                "inconsistency, indirectness, imprecision, publication_bias, "
                "effect_size_range, certainty, direction, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, outcome, n_studies, study_designs, risk_of_bias_rating,
                 inconsistency, indirectness, imprecision, publication_bias,
                 effect_size_range, certainty, direction, notes, now),
            )
            self._conn.commit()
        # Sync to central DB
        if self._central:
            try:
                self._central.store_grade_evidence(
                    session_topic=self._get_session_topic(session_id),
                    outcome=outcome, n_studies=n_studies,
                    study_designs=study_designs or "",
                    risk_of_bias_rating=risk_of_bias_rating,
                    inconsistency=inconsistency, indirectness=indirectness,
                    imprecision=imprecision, publication_bias=publication_bias,
                    effect_size_range=effect_size_range or "",
                    certainty=certainty, direction=direction or "", notes=notes,
                )
            except Exception as exc:
                _log.warning("CentralDB GRADE sync failed: %s", exc)
        return cur.lastrowid  # type: ignore

    def get_grade_evidence(self, session_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM grade_evidence WHERE session_id = ? ORDER BY grade_id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Phase checkpoints ──────────────────────────────────────

    def save_phase_checkpoint(self, session_id: int, phase: str, result_summary: str | None = None) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO phase_checkpoints (session_id, phase, status, result_summary, created_at) "
                "VALUES (?, ?, 'completed', ?, ?)",
                (session_id, phase, result_summary, now),
            )
            self._conn.commit()

    def get_completed_phases(self, session_id: int) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT phase FROM phase_checkpoints WHERE session_id = ? AND status = 'completed'",
            (session_id,),
        ).fetchall()
        return {row["phase"] for row in rows}

    def get_all_papers_with_claims(self, session_id: int) -> list[dict[str, Any]]:
        """Get all papers that have at least one claim, with their claims attached."""
        papers = self.get_cited_papers(session_id)
        for p in papers:
            pid = p["paper_id"]
            claims = self._conn.execute(
                "SELECT * FROM claims WHERE session_id = ? AND paper_id = ?",
                (session_id, pid),
            ).fetchall()
            p["claims"] = [dict(c) for c in claims]
        return papers

    # ── Peer Review ───────────────────────────────────────────

    def store_peer_review_score(
        self, session_id: int, cycle: int, round_num: int,
        reviewer: str, attribute: str, score: int, feedback: str = "",
    ) -> int:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO peer_review_scores "
                "(session_id, cycle, round, reviewer, attribute, score, feedback, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, cycle, round_num, reviewer, attribute, score, feedback, now),
            )
            self._conn.commit()
        # Sync to central DB
        if self._central:
            try:
                self._central.store_peer_review_score(
                    session_topic=self._get_session_topic(session_id),
                    cycle=cycle, round_num=round_num, reviewer=reviewer,
                    attribute=attribute, score=score, feedback=feedback,
                )
            except Exception as exc:
                _log.warning("CentralDB peer review score sync failed: %s", exc)
        return cur.lastrowid

    def store_peer_review_consensus(
        self, session_id: int, cycle: int, attribute: str, score: int,
        feedback: str = "", improvement_plan: str = "",
    ) -> int:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO peer_review_consensus "
                "(session_id, cycle, attribute, score, feedback, improvement_plan, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, cycle, attribute, score, feedback, improvement_plan, now),
            )
            self._conn.commit()
        # Sync to central DB
        if self._central:
            try:
                self._central.store_peer_review_consensus(
                    session_topic=self._get_session_topic(session_id),
                    cycle=cycle, attribute=attribute, score=score,
                    feedback=feedback, improvement_plan=improvement_plan,
                )
            except Exception as exc:
                _log.warning("CentralDB peer review consensus sync failed: %s", exc)
        return cur.lastrowid

    def get_peer_review_scores(self, session_id: int, cycle: int | None = None) -> list[dict[str, Any]]:
        if cycle is not None:
            rows = self._conn.execute(
                "SELECT * FROM peer_review_scores WHERE session_id = ? AND cycle = ? ORDER BY round, reviewer, attribute",
                (session_id, cycle),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM peer_review_scores WHERE session_id = ? ORDER BY cycle, round, reviewer, attribute",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_peer_review_consensus(self, session_id: int, cycle: int | None = None) -> list[dict[str, Any]]:
        if cycle is not None:
            rows = self._conn.execute(
                "SELECT * FROM peer_review_consensus WHERE session_id = ? AND cycle = ? ORDER BY attribute",
                (session_id, cycle),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM peer_review_consensus WHERE session_id = ? ORDER BY cycle, attribute",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]
