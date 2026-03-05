# Location: ara/credentials.py
# Purpose: API key management — load/save/prompt for credentials
# Functions: CredentialBundle, CredentialStore, credentials_from_env, prompt_for_credentials
# Calls: N/A
# Imports: json, dataclasses, pathlib, getpass

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class CredentialBundle:
    google_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openrouter_api_key: str | None = None

    def has_any(self) -> bool:
        return bool(self.google_api_key or self.anthropic_api_key
                     or self.openai_api_key or self.openrouter_api_key)

    def merge_missing(self, other: CredentialBundle) -> None:
        for attr in ("google_api_key", "anthropic_api_key",
                      "openai_api_key", "openrouter_api_key"):
            if not getattr(self, attr) and getattr(other, attr):
                setattr(self, attr, getattr(other, attr))


class CredentialStore:
    def __init__(self, path: Path | None = None):
        self._path = path or Path.home() / ".ara" / "credentials.json"

    def load(self) -> CredentialBundle:
        if not self._path.exists():
            return CredentialBundle()
        try:
            data = json.loads(self._path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return CredentialBundle()
        return CredentialBundle(
            google_api_key=data.get("google_api_key"),
            anthropic_api_key=data.get("anthropic_api_key"),
            openai_api_key=data.get("openai_api_key"),
            openrouter_api_key=data.get("openrouter_api_key"),
        )

    def save(self, creds: CredentialBundle) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in {
            "google_api_key": creds.google_api_key,
            "anthropic_api_key": creds.anthropic_api_key,
            "openai_api_key": creds.openai_api_key,
            "openrouter_api_key": creds.openrouter_api_key,
        }.items() if v}
        self._path.write_text(json.dumps(payload, indent=2), "utf-8")
        self._path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def credentials_from_env() -> CredentialBundle:
    return CredentialBundle(
        google_api_key=os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY"),
        anthropic_api_key=os.getenv("ARA_ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
        openai_api_key=os.getenv("ARA_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
        openrouter_api_key=os.getenv("ARA_OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY"),
    )


def parse_env_file(path: Path) -> CredentialBundle:
    creds = CredentialBundle()
    if not path.exists():
        return creds
    mapping = {
        "GOOGLE_API_KEY": "google_api_key",
        "ARA_GOOGLE_API_KEY": "google_api_key",
        "ANTHROPIC_API_KEY": "anthropic_api_key",
        "ARA_ANTHROPIC_API_KEY": "anthropic_api_key",
        "OPENAI_API_KEY": "openai_api_key",
        "ARA_OPENAI_API_KEY": "openai_api_key",
        "OPENROUTER_API_KEY": "openrouter_api_key",
        "ARA_OPENROUTER_API_KEY": "openrouter_api_key",
    }
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("\"'")
        attr = mapping.get(key)
        if attr and val:
            setattr(creds, attr, val)
    return creds


def prompt_for_credentials(
    existing: CredentialBundle, force: bool = False,
) -> tuple[CredentialBundle, bool]:
    import getpass
    labels = [
        ("Google AI Studio", "google_api_key"),
        ("Anthropic", "anthropic_api_key"),
        ("OpenAI", "openai_api_key"),
        ("OpenRouter", "openrouter_api_key"),
    ]
    changed = False
    for label, attr in labels:
        current = getattr(existing, attr) or ""
        hint = f" [{current[:8]}...]" if current else ""
        val = getpass.getpass(f"{label} API key{hint}: ").strip()
        if val:
            setattr(existing, attr, val)
            changed = True
    return existing, changed
