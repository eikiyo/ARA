# Location: ara/db.py
# Purpose: SQLite database layer — sessions, papers, claims, hypotheses, branches
# Functions: ARADB (all CRUD operations)
# Calls: sqlite3
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

CREATE INDEX IF NOT EXISTS idx_papers_session ON papers(session_id);
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_claims_session ON claims(session_id);
CREATE INDEX IF NOT EXISTS idx_hypotheses_session ON hypotheses(session_id);
CREATE INDEX IF NOT EXISTS idx_prisma_session ON prisma_stats(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_session ON quality_audit(session_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ARADB:
    def __init__(self, db_path: Path):
        self._path = db_path
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
        """Add columns that may not exist in older databases."""
        cur = self._conn.execute("PRAGMA table_info(papers)")
        existing = {row[1] for row in cur.fetchall()}
        if "embedding" not in existing:
            self._conn.execute("ALTER TABLE papers ADD COLUMN embedding TEXT")
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

    def store_papers(self, session_id: int, papers: list[dict[str, Any]]) -> int:
        now = _now()
        stored = 0
        with self._lock:
            for p in papers:
                doi = p.get("doi") or None  # Normalize empty string to None
                title = p.get("title", "").strip()
                if not title:
                    continue
                # Dedup by DOI first, then title
                if doi:
                    existing = self._conn.execute(
                        "SELECT paper_id FROM papers WHERE session_id = ? AND doi = ?",
                        (session_id, doi),
                    ).fetchone()
                    if existing:
                        continue
                else:
                    existing = self._conn.execute(
                        "SELECT paper_id FROM papers WHERE session_id = ? AND title = ?",
                        (session_id, title),
                    ).fetchone()
                    if existing:
                        continue

                authors_json = json.dumps(p.get("authors", []))
                self._conn.execute(
                    "INSERT INTO papers (session_id, title, abstract, authors, year, doi, source, url, citation_count, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, title, p.get("abstract"), authors_json,
                     p.get("year"), doi, p.get("source", "unknown"),
                     p.get("url"), p.get("citation_count", 0), now),
                )
                stored += 1
            self._conn.commit()
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

    def get_unembedded_papers(self, session_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT paper_id, title, abstract, authors FROM papers "
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
