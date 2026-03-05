# Location: ara/settings.py
# Purpose: Persistent settings store (per-workspace defaults)
# Functions: PersistentSettings, SettingsStore
# Calls: N/A
# Imports: json, dataclasses, pathlib

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

VALID_REASONING_EFFORTS: set[str] = {"low", "medium", "high"}


@dataclass(slots=True)
class PersistentSettings:
    default_provider: str | None = None
    default_model: str | None = None
    default_reasoning_effort: str | None = None
    default_model_google: str | None = None
    default_model_openai: str | None = None
    default_model_anthropic: str | None = None
    default_model_openrouter: str | None = None
    default_model_ollama: str | None = None

    def default_model_for_provider(self, provider: str) -> str | None:
        per = {
            "google": self.default_model_google,
            "openai": self.default_model_openai,
            "anthropic": self.default_model_anthropic,
            "openrouter": self.default_model_openrouter,
            "ollama": self.default_model_ollama,
        }
        return per.get(provider) or self.default_model or None

    def to_json(self) -> dict[str, str]:
        payload: dict[str, str] = {}
        for attr in (
            "default_provider", "default_model", "default_reasoning_effort",
            "default_model_google", "default_model_openai",
            "default_model_anthropic", "default_model_openrouter",
            "default_model_ollama",
        ):
            val = getattr(self, attr)
            if val:
                payload[attr] = val
        return payload

    @classmethod
    def from_json(cls, payload: dict | None) -> PersistentSettings:
        if not isinstance(payload, dict):
            return cls()
        def _s(key: str) -> str | None:
            v = str(payload.get(key, "")).strip()
            return v or None
        effort = _s("default_reasoning_effort")
        if effort and effort not in VALID_REASONING_EFFORTS:
            effort = None
        return cls(
            default_provider=_s("default_provider"),
            default_model=_s("default_model"),
            default_reasoning_effort=effort,
            default_model_google=_s("default_model_google"),
            default_model_openai=_s("default_model_openai"),
            default_model_anthropic=_s("default_model_anthropic"),
            default_model_openrouter=_s("default_model_openrouter"),
            default_model_ollama=_s("default_model_ollama"),
        )


@dataclass(slots=True)
class SettingsStore:
    workspace: Path
    session_root_dir: str = "ara_data"
    settings_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.workspace = self.workspace.expanduser().resolve()
        root = self.workspace / self.session_root_dir
        root.mkdir(parents=True, exist_ok=True)
        self.settings_path = root / "settings.json"

    def load(self) -> PersistentSettings:
        if not self.settings_path.exists():
            return PersistentSettings()
        try:
            parsed = json.loads(self.settings_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return PersistentSettings()
        return PersistentSettings.from_json(parsed)

    def save(self, settings: PersistentSettings) -> None:
        self.settings_path.write_text(
            json.dumps(settings.to_json(), indent=2), "utf-8",
        )
