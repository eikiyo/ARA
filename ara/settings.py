# Location: ara/settings.py
# Purpose: Persistent settings store (per-workspace defaults)
# Functions: PersistentSettings, SettingsStore
# Calls: N/A
# Imports: json, dataclasses, pathlib

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class PersistentSettings:
    default_model: str | None = None

    def to_json(self) -> dict[str, str]:
        payload: dict[str, str] = {}
        if self.default_model:
            payload["default_model"] = self.default_model
        return payload

    @classmethod
    def from_json(cls, payload: dict | None) -> PersistentSettings:
        if not isinstance(payload, dict):
            return cls()
        model = str(payload.get("default_model", "")).strip() or None
        return cls(default_model=model)


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
        data = json.dumps(settings.to_json(), indent=2)
        temp_fd, temp_path = tempfile.mkstemp(
            dir=self.settings_path.parent, prefix=".settings_", suffix=".tmp",
        )
        try:
            with open(temp_fd, "w", encoding="utf-8") as f:
                f.write(data)
            Path(temp_path).replace(self.settings_path)
        except Exception:
            Path(temp_path).unlink(missing_ok=True)
            raise
