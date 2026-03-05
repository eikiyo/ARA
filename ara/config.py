# Location: ara/config.py
# Purpose: ARA configuration dataclass with env var loading
# Functions: ARAConfig, PROVIDER_DEFAULT_MODELS
# Calls: N/A
# Imports: os, dataclasses, pathlib

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "google": "gemini-2.0-flash",
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "openrouter": "google/gemini-2.0-flash-exp:free",
    "ollama": "qwen3:8b",
}


@dataclass(slots=True)
class ARAConfig:
    workspace: Path = field(default_factory=lambda: Path("."))
    session_root_dir: str = "ara_data"

    # Provider
    provider: str = "google"
    model: str = "gemini-2.0-flash"
    reasoning_effort: str | None = None

    # API keys
    google_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openrouter_api_key: str | None = None

    # Base URLs
    ollama_base_url: str = "http://localhost:11434/v1"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Engine limits
    max_depth: int = 4
    max_steps_per_call: int = 80
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
            provider=os.getenv("ARA_PROVIDER", "google"),
            model=os.getenv("ARA_MODEL", "gemini-2.0-flash"),
            reasoning_effort=os.getenv("ARA_REASONING_EFFORT"),
            google_api_key=os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            anthropic_api_key=os.getenv("ARA_ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
            openai_api_key=os.getenv("ARA_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
            openrouter_api_key=os.getenv("ARA_OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY"),
            ollama_base_url=os.getenv("ARA_OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            max_depth=int(os.getenv("ARA_MAX_DEPTH", "4")),
            max_steps_per_call=int(os.getenv("ARA_MAX_STEPS", "80")),
            budget_limit_usd=float(os.getenv("ARA_BUDGET_LIMIT", "5.0")),
        )
