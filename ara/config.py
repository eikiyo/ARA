# Location: ara/config.py
# Purpose: ARA configuration dataclass with env var loading
# Functions: ARAConfig
# Calls: N/A
# Imports: os, dataclasses, pathlib

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class ARAConfig:
    workspace: Path = field(default_factory=lambda: Path("."))
    session_root_dir: str = "ara_data"

    # Model
    model: str = "gemini-3.1-pro-preview"
    writer_model: str = "gemini-3.1-pro-preview"
    light_model: str = "gemini-2.5-flash-lite"
    google_api_key: str | None = None

    # Engine limits
    max_depth: int = 4
    max_steps_per_call: int = 150
    max_tool_calls_per_turn: int = 1
    max_solve_seconds: int = 5400  # 90 minutes — 11-phase pipeline needs time
    budget_limit_usd: float = 15.0

    # Paper quality gates
    min_papers: int = 200
    min_cited: int = 60
    min_paper_words: int = 6000
    max_search_rounds: int = 6
    paper_critic_max_revisions: int = 3
    section_critic_max_revisions: int = 2

    # Deep read targets
    min_claims: int = 300
    min_deep_read_papers: int = 120

    # Snowball
    snowball_top_papers: int = 15
    snowball_refs_per_paper: int = 8

    # Triage
    triage_select_threshold: float = 0.65
    triage_reject_threshold: float = 0.40
    triage_batch_size: int = 40

    # Per-phase step budgets
    steps_scout: int = 60
    steps_protocol: int = 15
    steps_verifier: int = 80
    steps_triage: int = 20
    steps_deep_read: int = 400
    steps_brancher: int = 120
    steps_hypothesis: int = 150
    steps_critic: int = 60
    steps_synthesis: int = 40

    # Writer section word minimums (lean — verbosity kills academic writing)
    words_abstract: int = 200
    words_introduction: int = 600
    words_literature_review: int = 1200
    words_methods: int = 800
    words_results: int = 1000
    words_discussion: int = 800
    words_conclusion: int = 300

    # Writer section citation minimums
    cites_introduction: int = 8
    cites_literature_review: int = 20
    cites_methods: int = 3
    cites_results: int = 8
    cites_discussion: int = 10
    cites_conclusion: int = 3

    # Quality audit thresholds
    min_quality_citations: int = 40
    min_quality_tables: int = 2

    # Triage
    triage_step_budget: int = 20

    # Search date range
    search_start_year: int = 2014
    search_end_year: int | None = None  # None = current year

    # Paper type: "review" (SLR), "scoping" (scoping review), or "conceptual" (theoretical/framework)
    paper_type: str = "review"

    # Conceptual paper section word minimums (lean targets — depth over length)
    words_theoretical_background: int = 2000
    words_framework: int = 1500
    words_propositions: int = 1000
    words_methodology: int = 400  # Conceptual paper methodology section

    # Writing quality constraints
    max_propositions: int = 5  # Hard cap on propositions per paper
    max_avg_sentence_length: int = 30  # Words per sentence — reject above this
    max_section_overlap: float = 0.30  # Cosine similarity between sections — flag repetition above this

    # Journal tier priority — controls how much top-tier journals are preferred
    # 2.0 = for every 1 unranked citation, require 2 from AAA/AA (100% more priority)
    # 1.5 = 50% more top-tier, 1.0 = equal weight (no priority)
    journal_tier_ratio: float = 2.0  # AAA/AA citations per unranked citation
    journal_tier_min_pct: float = 0.50  # Hard floor: at least 50% citations must be AAA/AA

    # Scope mode: "broad" (default) or "narrow"
    # Narrow mode reduces targets for focused reviews on specific subtopics
    scope_mode: str = "broad"

    # Special instructions (topic-specific, passed to scout/writer)
    special_instructions: str = ""
    special_authors: str = ""  # Comma-separated foundational authors to search for

    # Peer review pipeline
    peer_review_enabled: bool = True
    peer_review_budget: float = 8.0
    peer_review_journal: str = "auto"  # "auto" = detect from topic, or explicit journal name
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # Hypothesis/Critic model: "load_balanced" (Opus + GPT-5.4) or "default" (use task model)
    hypothesis_model: str = "load_balanced"

    # Behavior
    approval_gates: bool = True

    @classmethod
    def from_env(cls, workspace: str = ".") -> ARAConfig:
        ws = Path(workspace).expanduser().resolve()

        def _safe_int(env_var: str, default: int) -> int:
            val = os.getenv(env_var)
            if val is None:
                return default
            try:
                return int(val)
            except ValueError:
                _log.warning("Invalid int for %s: %r, using default %d", env_var, val, default)
                return default

        def _safe_float(env_var: str, default: float) -> float:
            val = os.getenv(env_var)
            if val is None:
                return default
            try:
                return float(val)
            except ValueError:
                _log.warning("Invalid float for %s: %r, using default %s", env_var, val, default)
                return default

        return cls(
            workspace=ws,
            session_root_dir=os.getenv("ARA_SESSION_DIR", "ara_data"),
            model=os.getenv("ARA_MODEL", "gemini-3.1-pro-preview"),
            writer_model=os.getenv("ARA_WRITER_MODEL", "gemini-3.1-pro-preview"),
            light_model=os.getenv("ARA_LIGHT_MODEL", "gemini-2.5-flash-lite"),
            google_api_key=os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            max_depth=_safe_int("ARA_MAX_DEPTH", 4),
            max_steps_per_call=_safe_int("ARA_MAX_STEPS", 150),
            max_tool_calls_per_turn=_safe_int("ARA_MAX_TOOL_CALLS_PER_TURN", 1),
            max_solve_seconds=_safe_int("ARA_MAX_SOLVE_SECONDS", 5400),
            budget_limit_usd=_safe_float("ARA_BUDGET_LIMIT", 15.0),
            min_papers=_safe_int("ARA_MIN_PAPERS", 200),
            min_cited=_safe_int("ARA_MIN_CITED", 60),
            min_paper_words=_safe_int("ARA_MIN_PAPER_WORDS", 6000),
            max_search_rounds=_safe_int("ARA_MAX_SEARCH_ROUNDS", 6),
            paper_critic_max_revisions=_safe_int("ARA_PAPER_CRITIC_MAX_REVISIONS", 3),
            section_critic_max_revisions=_safe_int("ARA_SECTION_CRITIC_MAX_REVISIONS", 2),
            min_claims=_safe_int("ARA_MIN_CLAIMS", 300),
            min_deep_read_papers=_safe_int("ARA_MIN_DEEP_READ_PAPERS", 120),
            snowball_top_papers=_safe_int("ARA_SNOWBALL_TOP_PAPERS", 15),
            snowball_refs_per_paper=_safe_int("ARA_SNOWBALL_REFS_PER_PAPER", 8),
            triage_select_threshold=_safe_float("ARA_TRIAGE_SELECT_THRESHOLD", 0.65),
            triage_reject_threshold=_safe_float("ARA_TRIAGE_REJECT_THRESHOLD", 0.40),
            triage_batch_size=_safe_int("ARA_TRIAGE_BATCH_SIZE", 40),
            triage_step_budget=_safe_int("ARA_TRIAGE_STEP_BUDGET", 20),
            min_quality_citations=_safe_int("ARA_MIN_QUALITY_CITATIONS", 40),
            min_quality_tables=_safe_int("ARA_MIN_QUALITY_TABLES", 2),
            words_abstract=_safe_int("ARA_WORDS_ABSTRACT", 250),
            words_introduction=_safe_int("ARA_WORDS_INTRODUCTION", 800),
            words_literature_review=_safe_int("ARA_WORDS_LITERATURE_REVIEW", 1500),
            words_methods=_safe_int("ARA_WORDS_METHODS", 1000),
            words_results=_safe_int("ARA_WORDS_RESULTS", 1200),
            words_discussion=_safe_int("ARA_WORDS_DISCUSSION", 1000),
            words_conclusion=_safe_int("ARA_WORDS_CONCLUSION", 400),
            cites_introduction=_safe_int("ARA_CITES_INTRODUCTION", 8),
            cites_literature_review=_safe_int("ARA_CITES_LITERATURE_REVIEW", 20),
            cites_methods=_safe_int("ARA_CITES_METHODS", 3),
            cites_results=_safe_int("ARA_CITES_RESULTS", 8),
            cites_discussion=_safe_int("ARA_CITES_DISCUSSION", 10),
            cites_conclusion=_safe_int("ARA_CITES_CONCLUSION", 3),
            search_start_year=_safe_int("ARA_SEARCH_START_YEAR", 2014),
            scope_mode=os.getenv("ARA_SCOPE_MODE", "broad"),
            paper_type=os.getenv("ARA_PAPER_TYPE", "review"),
            words_theoretical_background=_safe_int("ARA_WORDS_THEORETICAL_BACKGROUND", 2000),
            words_framework=_safe_int("ARA_WORDS_FRAMEWORK", 1500),
            words_propositions=_safe_int("ARA_WORDS_PROPOSITIONS", 1000),
            max_propositions=_safe_int("ARA_MAX_PROPOSITIONS", 5),
            max_avg_sentence_length=_safe_int("ARA_MAX_AVG_SENTENCE_LENGTH", 30),
            max_section_overlap=_safe_float("ARA_MAX_SECTION_OVERLAP", 0.30),
            journal_tier_ratio=_safe_float("ARA_JOURNAL_TIER_RATIO", 2.0),
            journal_tier_min_pct=_safe_float("ARA_JOURNAL_TIER_MIN_PCT", 0.50),
            special_instructions=os.getenv("ARA_SPECIAL_INSTRUCTIONS", ""),
            special_authors=os.getenv("ARA_SPECIAL_AUTHORS", ""),
            peer_review_enabled=os.getenv("ARA_PEER_REVIEW_ENABLED", "true").lower() not in ("false", "0", "no"),
            peer_review_budget=_safe_float("ARA_PEER_REVIEW_BUDGET", 8.0),
            peer_review_journal=os.getenv("ARA_PEER_REVIEW_JOURNAL", "auto"),
            anthropic_api_key=os.getenv("ARA_ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
            openai_api_key=os.getenv("ARA_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
            hypothesis_model=os.getenv("ARA_HYPOTHESIS_MODEL", "load_balanced"),
        )

    def apply_narrow_scope(self) -> None:
        """Switch to narrow scope — halves targets for focused subtopic reviews."""
        self.scope_mode = "narrow"
        self.min_papers = max(150, self.min_papers // 2)
        self.min_cited = max(30, self.min_cited // 2)
        self.min_claims = max(75, self.min_claims // 2)
        self.min_deep_read_papers = max(60, self.min_deep_read_papers // 2)
        self.min_quality_citations = max(20, self.min_quality_citations // 2)
        self.snowball_top_papers = max(5, self.snowball_top_papers // 2)
