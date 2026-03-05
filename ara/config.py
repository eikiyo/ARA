# Location: ara/config.py
# Purpose: ARA configuration dataclass + loading from env/yaml
# Functions: ARAConfig dataclass, from_env
# Calls: N/A
# Imports: os, dataclasses, pathlib

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "google": "gemini-2.0-flash",
    "openai": "gpt-5.2",
    "anthropic": "claude-opus-4-6",
    "openrouter": "anthropic/claude-sonnet-4-5",
    "ollama": "llama3.2",
}


@dataclass(slots=True)
class ARAConfig:
    workspace: Path
    provider: str = "google"
    model: str = "gemini-2.0-flash"
    reasoning_effort: str | None = "high"
    # Provider base URLs
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ollama_base_url: str = "http://localhost:11434/v1"
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    # API keys
    google_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None
    # Engine settings
    max_depth: int = 3
    max_steps_per_call: int = 100
    max_observation_chars: int = 6000
    max_plan_chars: int = 40_000
    max_turn_summaries: int = 50
    max_persisted_observations: int = 400
    max_solve_seconds: int = 0
    recursive: bool = True
    acceptance_criteria: bool = True
    # ARA-specific
    approval_gates: bool = True  # If False, auto-approve all phase gates
    session_root_dir: str = "ara_data"
    budget_limit_usd: float = 0.0

    @classmethod
    def from_env(cls, workspace: str | Path) -> ARAConfig:
        ws = Path(workspace).expanduser().resolve()
        return cls(
            workspace=ws,
            provider=os.getenv("ARA_PROVIDER", "google").strip().lower() or "google",
            model=os.getenv("ARA_MODEL", "gemini-2.0-flash"),
            reasoning_effort=(os.getenv("ARA_REASONING_EFFORT", "high").strip().lower() or None),
            openai_base_url=os.getenv("ARA_OPENAI_BASE_URL", "https://api.openai.com/v1"),
            anthropic_base_url=os.getenv("ARA_ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"),
            openrouter_base_url=os.getenv("ARA_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            ollama_base_url=os.getenv("ARA_OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            google_base_url=os.getenv("ARA_GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"),
            google_api_key=os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            openai_api_key=os.getenv("ARA_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
            anthropic_api_key=os.getenv("ARA_ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
            openrouter_api_key=os.getenv("ARA_OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY"),
            max_depth=int(os.getenv("ARA_MAX_DEPTH", "3")),
            max_steps_per_call=int(os.getenv("ARA_MAX_STEPS", "100")),
            max_observation_chars=int(os.getenv("ARA_MAX_OBS_CHARS", "6000")),
            max_solve_seconds=int(os.getenv("ARA_MAX_SOLVE_SECONDS", "0")),
            recursive=os.getenv("ARA_RECURSIVE", "true").strip().lower() in ("1", "true", "yes"),
            acceptance_criteria=os.getenv("ARA_ACCEPTANCE_CRITERIA", "true").strip().lower() in ("1", "true", "yes"),
            approval_gates=os.getenv("ARA_APPROVAL_GATES", "true").strip().lower() in ("1", "true", "yes"),
            session_root_dir=os.getenv("ARA_SESSION_DIR", "ara_data"),
            budget_limit_usd=float(os.getenv("ARA_BUDGET_LIMIT", "0")),
        )
