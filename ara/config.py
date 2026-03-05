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
    model: str = "gemini-2.0-flash"
    writer_model: str = "gemini-2.5-pro"
    google_api_key: str | None = None

    # Engine limits
    max_depth: int = 4
    max_steps_per_call: int = 150
    max_tool_calls_per_turn: int = 1
    max_solve_seconds: int = 3600
    budget_limit_usd: float = 10.0

    # Paper quality gates
    min_papers: int = 50
    min_cited: int = 40
    min_paper_words: int = 6000
    max_search_rounds: int = 4
    paper_critic_max_revisions: int = 3
    section_critic_max_revisions: int = 2

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
            model=os.getenv("ARA_MODEL", "gemini-2.0-flash"),
            writer_model=os.getenv("ARA_WRITER_MODEL", "gemini-2.5-pro"),
            google_api_key=os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            max_depth=_safe_int("ARA_MAX_DEPTH", 4),
            max_steps_per_call=_safe_int("ARA_MAX_STEPS", 80),
            max_tool_calls_per_turn=_safe_int("ARA_MAX_TOOL_CALLS_PER_TURN", 1),
            max_solve_seconds=_safe_int("ARA_MAX_SOLVE_SECONDS", 1800),
            budget_limit_usd=_safe_float("ARA_BUDGET_LIMIT", 5.0),
            min_papers=_safe_int("ARA_MIN_PAPERS", 50),
            min_cited=_safe_int("ARA_MIN_CITED", 40),
            min_paper_words=_safe_int("ARA_MIN_PAPER_WORDS", 6000),
            max_search_rounds=_safe_int("ARA_MAX_SEARCH_ROUNDS", 4),
            paper_critic_max_revisions=_safe_int("ARA_PAPER_CRITIC_MAX_REVISIONS", 3),
            section_critic_max_revisions=_safe_int("ARA_SECTION_CRITIC_MAX_REVISIONS", 2),
        )
