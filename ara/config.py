# Location: ara/config.py
# Purpose: ARA configuration dataclass with env var loading
# Functions: ARAConfig
# Calls: N/A
# Imports: os, dataclasses, pathlib

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ARAConfig:
    workspace: Path = field(default_factory=lambda: Path("."))
    session_root_dir: str = "ara_data"

    # Model
    model: str = "gemini-2.0-flash"
    google_api_key: str | None = None

    # Engine limits
    max_depth: int = 4
    max_steps_per_call: int = 80
    max_tool_calls_per_turn: int = 15
    max_solve_seconds: int = 1800
    budget_limit_usd: float = 5.0

    # Behavior
    approval_gates: bool = True

    @classmethod
    def from_env(cls, workspace: str = ".") -> ARAConfig:
        ws = Path(workspace).expanduser().resolve()
        return cls(
            workspace=ws,
            session_root_dir=os.getenv("ARA_SESSION_DIR", "ara_data"),
            model=os.getenv("ARA_MODEL", "gemini-2.0-flash"),
            google_api_key=os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            max_depth=int(os.getenv("ARA_MAX_DEPTH", "4")),
            max_steps_per_call=int(os.getenv("ARA_MAX_STEPS", "80")),
            max_tool_calls_per_turn=int(os.getenv("ARA_MAX_TOOL_CALLS_PER_TURN", "15")),
            max_solve_seconds=int(os.getenv("ARA_MAX_SOLVE_SECONDS", "1800")),
            budget_limit_usd=float(os.getenv("ARA_BUDGET_LIMIT", "5.0")),
        )
