# Location: ara/db.py
# Purpose: SQLite database module for ARA research agent sessions and data persistence
# Functions: Session/paper/claim/hypothesis CRUD, embeddings storage, cost tracking, approval gates
# Calls: Called by engine.py for all data operations; uses standard sqlite3 module
# Imports: sqlite3, json, Path, datetime, typing

import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any


class ARADB:
    """SQLite database interface for ARA research agent.

    Manages all research sessions, papers, claims, hypotheses, branches,
    and approval gates with JSON support for complex data types.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize database connection and create tables if needed.

        Args:
            db_path: Path to SQLite database file. Defaults to .ara/session.db
        """
        if db_path is None:
            db_path = Path.home() / ".ara" / "session.db"

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._ensure_tables()

    def _ensure_tables(self):
        """Create all tables if they don't exist. Called once on init."""
        cursor = self.conn.cursor()

        # research_sessions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS research_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                current_phase TEXT NOT NULL DEFAULT 'scout',
                paper_type TEXT NOT NULL DEFAULT 'research_article',
                citation_style TEXT NOT NULL DEFAULT 'apa7',
                budget_cap REAL NOT NULL DEFAULT 5.00,
                budget_spent REAL NOT NULL DEFAULT 0.00,
                deep_read_limit INTEGER NOT NULL DEFAULT 100,
                enabled_sources TEXT NOT NULL DEFAULT '[\"semantic_scholar\",\"arxiv\",\"openalex\",\"crossref\",\"pubmed\",\"core\",\"dblp\",\"europe_pmc\",\"base\",\"google_scholar\"]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # papers
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS papers (
                paper_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                doi TEXT,
                title TEXT NOT NULL,
                authors TEXT,
                abstract TEXT,
                source TEXT NOT NULL DEFAULT '[\"unknown\"]',
                publication_year INTEGER,
                citation_count INTEGER NOT NULL DEFAULT 0,
                retraction_status TEXT NOT NULL DEFAULT 'none',
                confidence_score REAL NOT NULL DEFAULT 1.0,
                relevance_score REAL,
                full_text_available INTEGER NOT NULL DEFAULT 0,
                deep_read_selected INTEGER NOT NULL DEFAULT 0,
                embedding BLOB,
                url TEXT,
                pdf_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES research_sessions(session_id) ON DELETE CASCADE,
                UNIQUE(session_id, doi)
            )
        """)

        # claims
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS claims (
                claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                primary_source_paper_id INTEGER NOT NULL,
                claim_text TEXT NOT NULL,
                verification_status TEXT NOT NULL DEFAULT 'unverified',
                confidence_score REAL NOT NULL DEFAULT 0.5,
                supporting_papers_count INTEGER NOT NULL DEFAULT 0,
                contradicting_papers_count INTEGER NOT NULL DEFAULT 0,
                embedding BLOB,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES research_sessions(session_id) ON DELETE CASCADE,
                FOREIGN KEY (primary_source_paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            )
        """)

        # claim_papers (many-to-many)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS claim_papers (
                claim_paper_id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id INTEGER NOT NULL,
                paper_id INTEGER NOT NULL,
                session_id INTEGER NOT NULL,
                relationship_type TEXT NOT NULL DEFAULT 'supports',
                created_at TEXT NOT NULL,
                FOREIGN KEY (claim_id) REFERENCES claims(claim_id) ON DELETE CASCADE,
                FOREIGN KEY (paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE,
                FOREIGN KEY (session_id) REFERENCES research_sessions(session_id) ON DELETE CASCADE,
                UNIQUE(claim_id, paper_id, relationship_type)
            )
        """)

        # hypotheses
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hypotheses (
                hypothesis_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                hypothesis_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'generated',
                rank INTEGER,
                overall_score REAL NOT NULL DEFAULT 0.5,
                strength TEXT,
                weakness TEXT,
                supporting_claims TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES research_sessions(session_id) ON DELETE CASCADE
            )
        """)

        # hypothesis_scores
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hypothesis_scores (
                score_id INTEGER PRIMARY KEY AUTOINCREMENT,
                hypothesis_id INTEGER NOT NULL,
                dimension TEXT NOT NULL,
                score REAL NOT NULL,
                scored_by TEXT NOT NULL,
                iteration INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(hypothesis_id) ON DELETE CASCADE,
                UNIQUE(hypothesis_id, dimension, iteration)
            )
        """)

        # branch_map
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS branch_map (
                branch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                source_hypothesis_id INTEGER NOT NULL,
                target_domain TEXT NOT NULL,
                branch_type TEXT NOT NULL,
                branch_confidence REAL NOT NULL DEFAULT 0.5,
                finding TEXT NOT NULL,
                papers_found INTEGER NOT NULL DEFAULT 0,
                relevant_papers TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES research_sessions(session_id) ON DELETE CASCADE,
                FOREIGN KEY (source_hypothesis_id) REFERENCES hypotheses(hypothesis_id) ON DELETE CASCADE
            )
        """)

        # approval_gates
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS approval_gates (
                gate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                phase TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                action TEXT,
                user_comments TEXT,
                gate_data TEXT NOT NULL,
                resolved_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES research_sessions(session_id) ON DELETE CASCADE
            )
        """)

        # rules
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                rule_text TEXT NOT NULL,
                rule_type TEXT NOT NULL DEFAULT 'exclude',
                created_by TEXT NOT NULL DEFAULT 'user',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES research_sessions(session_id) ON DELETE CASCADE
            )
        """)

        # cost_log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cost_log (
                cost_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                model_name TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost_usd REAL NOT NULL,
                tool_name TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES research_sessions(session_id) ON DELETE CASCADE
            )
        """)

        # Create indices
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_session ON papers(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_confidence ON papers(confidence_score DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_session ON claims(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(verification_status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_confidence ON claims(confidence_score DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hypotheses_session ON hypotheses(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hypotheses_status ON hypotheses(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hypotheses_score ON hypotheses(overall_score DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_branches_session ON branch_map(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_branches_hypothesis ON branch_map(source_hypothesis_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gates_session ON approval_gates(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gates_status ON approval_gates(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rules_session ON rules(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cost_session ON cost_log(session_id)")

        self.conn.commit()

    def _now(self) -> str:
        """Return current timestamp in ISO 8601 format."""
        return datetime.utcnow().isoformat() + "Z"

    # ============================================================================
    # SESSION CRUD
    # ============================================================================

    def create_session(self, topic: str, paper_type: str = "research_article",
                      citation_style: str = "apa7", budget_cap: float = 5.00,
                      description: str = "", deep_read_limit: int = 100,
                      enabled_sources: Optional[List[str]] = None) -> int:
        """Create a new research session."""
        if enabled_sources is None:
            enabled_sources = ["semantic_scholar", "arxiv", "openalex", "crossref", "pubmed"]

        cursor = self.conn.cursor()
        now = self._now()
        cursor.execute("""
            INSERT INTO research_sessions
            (topic, description, paper_type, citation_style, budget_cap,
             deep_read_limit, enabled_sources, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (topic, description, paper_type, citation_style, budget_cap,
              deep_read_limit, json.dumps(enabled_sources), now, now))
        self.conn.commit()
        return cursor.lastrowid

    def get_session(self, session_id: int) -> Optional[Dict[str, Any]]:
        """Get session by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM research_sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_session_phase(self, session_id: int, phase: str):
        """Update session current phase."""
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE research_sessions
            SET current_phase = ?, updated_at = ?
            WHERE session_id = ?
        """, (phase, self._now(), session_id))
        self.conn.commit()

    def update_session_budget(self, session_id: int, spent: float):
        """Update session budget spent."""
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE research_sessions
            SET budget_spent = ?, updated_at = ?
            WHERE session_id = ?
        """, (spent, self._now(), session_id))
        self.conn.commit()

    def update_session_status(self, session_id: int, status: str):
        """Update session status."""
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE research_sessions
            SET status = ?, updated_at = ?
            WHERE session_id = ?
        """, (status, self._now(), session_id))
        self.conn.commit()

    # ============================================================================
    # PAPERS CRUD
    # ============================================================================

    def insert_paper(self, session_id: int, title: str, doi: Optional[str] = None,
                    authors: Optional[List[str]] = None, abstract: str = "",
                    source: Optional[List[str]] = None, publication_year: Optional[int] = None,
                    citation_count: int = 0, confidence_score: float = 1.0,
                    relevance_score: Optional[float] = None, url: str = "",
                    pdf_url: str = "", full_text_available: bool = False,
                    deep_read_selected: bool = False) -> int:
        """Insert a paper into the database."""
        cursor = self.conn.cursor()
        now = self._now()
        authors_json = json.dumps(authors) if authors else "[]"
        source_json = json.dumps(source) if source else '["unknown"]'

        cursor.execute("""
            INSERT INTO papers
            (session_id, doi, title, authors, abstract, source, publication_year,
             citation_count, confidence_score, relevance_score, url, pdf_url,
             full_text_available, deep_read_selected, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (session_id, doi, title, authors_json, abstract, source_json, publication_year,
              citation_count, confidence_score, relevance_score, url, pdf_url,
              int(full_text_available), int(deep_read_selected), now, now))
        self.conn.commit()
        return cursor.lastrowid

    def get_papers(self, session_id: int, deep_read_only: bool = False) -> List[Dict[str, Any]]:
        """Get papers for a session."""
        cursor = self.conn.cursor()
        if deep_read_only:
            cursor.execute("""
                SELECT * FROM papers
                WHERE session_id = ? AND deep_read_selected = 1
                ORDER BY created_at DESC
            """, (session_id,))
        else:
            cursor.execute("""
                SELECT * FROM papers
                WHERE session_id = ?
                ORDER BY created_at DESC
            """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_paper(self, paper_id: int) -> Optional[Dict[str, Any]]:
        """Get paper by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_paper(self, paper_id: int, **kwargs):
        """Update paper fields. Accepts any column name as kwarg."""
        allowed = {
            'confidence_score', 'relevance_score', 'full_text_available',
            'deep_read_selected', 'citation_count', 'retraction_status'
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return

        updates['updated_at'] = self._now()
        cols = ", ".join([f"{k} = ?" for k in updates.keys()])
        vals = list(updates.values())
        vals.append(paper_id)

        cursor = self.conn.cursor()
        cursor.execute(f"UPDATE papers SET {cols} WHERE paper_id = ?", vals)
        self.conn.commit()

    # ============================================================================
    # CLAIMS CRUD
    # ============================================================================

    def insert_claim(self, session_id: int, paper_id: int, claim_text: str,
                    confidence_score: float = 0.5, verification_status: str = "unverified",
                    **kwargs) -> int:
        """Insert a claim."""
        cursor = self.conn.cursor()
        now = self._now()
        cursor.execute("""
            INSERT INTO claims
            (session_id, primary_source_paper_id, claim_text, verification_status,
             confidence_score, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (session_id, paper_id, claim_text, verification_status, confidence_score, now, now))
        self.conn.commit()
        return cursor.lastrowid

    def get_claims(self, session_id: int, verified_only: bool = False) -> List[Dict[str, Any]]:
        """Get claims for a session."""
        cursor = self.conn.cursor()
        if verified_only:
            cursor.execute("""
                SELECT * FROM claims
                WHERE session_id = ? AND verification_status = 'verified'
                ORDER BY confidence_score DESC
            """, (session_id,))
        else:
            cursor.execute("""
                SELECT * FROM claims
                WHERE session_id = ?
                ORDER BY confidence_score DESC
            """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_claim(self, claim_id: int) -> Optional[Dict[str, Any]]:
        """Get claim by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_claim(self, claim_id: int, **kwargs):
        """Update claim fields."""
        allowed = {
            'verification_status', 'confidence_score', 'supporting_papers_count',
            'contradicting_papers_count'
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return

        updates['updated_at'] = self._now()
        cols = ", ".join([f"{k} = ?" for k in updates.keys()])
        vals = list(updates.values())
        vals.append(claim_id)

        cursor = self.conn.cursor()
        cursor.execute(f"UPDATE claims SET {cols} WHERE claim_id = ?", vals)
        self.conn.commit()

    def link_claim_paper(self, claim_id: int, paper_id: int, session_id: int,
                        relationship: str = "supports"):
        """Link a claim to a paper."""
        cursor = self.conn.cursor()
        now = self._now()
        try:
            cursor.execute("""
                INSERT INTO claim_papers
                (claim_id, paper_id, session_id, relationship_type, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (claim_id, paper_id, session_id, relationship, now))
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass  # Already linked

    # ============================================================================
    # HYPOTHESES CRUD
    # ============================================================================

    def insert_hypothesis(self, session_id: int, text: str, status: str = "generated",
                         overall_score: float = 0.5, **kwargs) -> int:
        """Insert a hypothesis."""
        cursor = self.conn.cursor()
        now = self._now()
        cursor.execute("""
            INSERT INTO hypotheses
            (session_id, hypothesis_text, status, overall_score, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, text, status, overall_score, now, now))
        self.conn.commit()
        return cursor.lastrowid

    def get_hypotheses(self, session_id: int) -> List[Dict[str, Any]]:
        """Get hypotheses for a session."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM hypotheses
            WHERE session_id = ?
            ORDER BY overall_score DESC
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_hypothesis(self, hypothesis_id: int) -> Optional[Dict[str, Any]]:
        """Get hypothesis by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM hypotheses WHERE hypothesis_id = ?", (hypothesis_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_hypothesis(self, hypothesis_id: int, **kwargs):
        """Update hypothesis fields."""
        allowed = {'status', 'rank', 'overall_score', 'strength', 'weakness', 'supporting_claims'}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return

        # Convert lists to JSON
        if 'supporting_claims' in updates and isinstance(updates['supporting_claims'], list):
            updates['supporting_claims'] = json.dumps(updates['supporting_claims'])

        updates['updated_at'] = self._now()
        cols = ", ".join([f"{k} = ?" for k in updates.keys()])
        vals = list(updates.values())
        vals.append(hypothesis_id)

        cursor = self.conn.cursor()
        cursor.execute(f"UPDATE hypotheses SET {cols} WHERE hypothesis_id = ?", vals)
        self.conn.commit()

    def insert_hypothesis_score(self, hypothesis_id: int, dimension: str, score: float,
                               scored_by: str, iteration: int = 1):
        """Insert/update a hypothesis dimension score."""
        cursor = self.conn.cursor()
        now = self._now()
        try:
            cursor.execute("""
                INSERT INTO hypothesis_scores
                (hypothesis_id, dimension, score, scored_by, iteration, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (hypothesis_id, dimension, score, scored_by, iteration, now))
            self.conn.commit()
        except sqlite3.IntegrityError:
            # Update existing score
            cursor.execute("""
                UPDATE hypothesis_scores
                SET score = ?
                WHERE hypothesis_id = ? AND dimension = ? AND iteration = ?
            """, (score, hypothesis_id, dimension, iteration))
            self.conn.commit()

    # ============================================================================
    # BRANCHES CRUD
    # ============================================================================

    def insert_branch(self, session_id: int, hypothesis_id: int, target_domain: str,
                     branch_type: str, branch_confidence: float = 0.5,
                     finding: str = "", papers_found: int = 0,
                     relevant_papers: Optional[List[str]] = None) -> int:
        """Insert a branch."""
        cursor = self.conn.cursor()
        now = self._now()
        papers_json = json.dumps(relevant_papers) if relevant_papers else "[]"
        cursor.execute("""
            INSERT INTO branch_map
            (session_id, source_hypothesis_id, target_domain, branch_type,
             branch_confidence, finding, papers_found, relevant_papers, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (session_id, hypothesis_id, target_domain, branch_type, branch_confidence,
              finding, papers_found, papers_json, now, now))
        self.conn.commit()
        return cursor.lastrowid

    def get_branches(self, session_id: int) -> List[Dict[str, Any]]:
        """Get branches for a session."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM branch_map
            WHERE session_id = ?
            ORDER BY branch_confidence DESC
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]

    # ============================================================================
    # APPROVAL GATES
    # ============================================================================

    def insert_gate(self, session_id: int, phase: str, gate_data: Dict[str, Any]) -> int:
        """Insert an approval gate."""
        cursor = self.conn.cursor()
        now = self._now()
        gate_json = json.dumps(gate_data)
        cursor.execute("""
            INSERT INTO approval_gates
            (session_id, phase, gate_data, created_at)
            VALUES (?, ?, ?, ?)
        """, (session_id, phase, gate_json, now))
        self.conn.commit()
        return cursor.lastrowid

    def resolve_gate(self, gate_id: int, status: str, action: str, comments: Optional[str] = None):
        """Resolve an approval gate."""
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE approval_gates
            SET status = ?, action = ?, user_comments = ?, resolved_at = ?
            WHERE gate_id = ?
        """, (status, action, comments, self._now(), gate_id))
        self.conn.commit()

    def get_pending_gate(self, session_id: int) -> Optional[Dict[str, Any]]:
        """Get pending approval gate for a session."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM approval_gates
            WHERE session_id = ? AND status = 'pending'
            LIMIT 1
        """, (session_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    # ============================================================================
    # RULES
    # ============================================================================

    def insert_rule(self, session_id: int, rule_text: str, rule_type: str = "exclude",
                   created_by: str = "user") -> int:
        """Insert a rule."""
        cursor = self.conn.cursor()
        now = self._now()
        cursor.execute("""
            INSERT INTO rules
            (session_id, rule_text, rule_type, created_by, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, rule_text, rule_type, created_by, now))
        self.conn.commit()
        return cursor.lastrowid

    def get_active_rules(self, session_id: int) -> List[Dict[str, Any]]:
        """Get active rules for a session."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM rules
            WHERE session_id = ? AND is_active = 1
            ORDER BY created_at DESC
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]

    # ============================================================================
    # COST TRACKING
    # ============================================================================

    def log_cost(self, session_id: int, model_name: str, input_tokens: int,
                output_tokens: int, cost_usd: float, tool_name: str = ""):
        """Log a cost transaction."""
        cursor = self.conn.cursor()
        now = self._now()
        cursor.execute("""
            INSERT INTO cost_log
            (session_id, model_name, input_tokens, output_tokens, cost_usd, tool_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (session_id, model_name, input_tokens, output_tokens, cost_usd, tool_name, now))
        self.conn.commit()

        # Update session budget_spent
        session = self.get_session(session_id)
        if session:
            new_spent = session['budget_spent'] + cost_usd
            self.update_session_budget(session_id, new_spent)

    def get_total_cost(self, session_id: int) -> float:
        """Get total cost for a session."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT SUM(cost_usd) as total FROM cost_log
            WHERE session_id = ?
        """, (session_id,))
        row = cursor.fetchone()
        return row['total'] or 0.0 if row else 0.0

    # ============================================================================
    # VECTOR/EMBEDDING STORAGE
    # ============================================================================

    def store_embedding(self, table: str, row_id: int, embedding: bytes):
        """Store embedding as BLOB. Table must be 'papers' or 'claims'."""
        if table not in ('papers', 'claims'):
            raise ValueError("table must be 'papers' or 'claims'")

        cursor = self.conn.cursor()
        id_col = 'paper_id' if table == 'papers' else 'claim_id'
        cursor.execute(f"""
            UPDATE {table}
            SET embedding = ?
            WHERE {id_col} = ?
        """, (embedding, row_id))
        self.conn.commit()

    # ============================================================================
    # CLEANUP
    # ============================================================================

    def close(self):
        """Close database connection."""
        self.conn.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
