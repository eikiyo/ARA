# Location: ara/credentials.py
# Purpose: API key management — loading, storing, prompting
# Functions: CredentialBundle, CredentialStore, credentials_from_env
# Calls: N/A
# Imports: json, os, getpass, stat, pathlib, dataclasses

from __future__ import annotations

import getpass
import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class CredentialBundle:
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None
    google_api_key: str | None = None

    def has_any(self) -> bool:
        return bool(
            (self.openai_api_key and self.openai_api_key.strip())
            or (self.anthropic_api_key and self.anthropic_api_key.strip())
            or (self.openrouter_api_key and self.openrouter_api_key.strip())
            or (self.google_api_key and self.google_api_key.strip())
        )

    def merge_missing(self, other: CredentialBundle) -> None:
        for attr in ("openai_api_key", "anthropic_api_key", "openrouter_api_key", "google_api_key"):
            if not getattr(self, attr) and getattr(other, attr):
                setattr(self, attr, getattr(other, attr))

    def to_json(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for attr in ("openai_api_key", "anthropic_api_key", "openrouter_api_key", "google_api_key"):
            val = getattr(self, attr)
            if val:
                out[attr] = val
        return out

    @classmethod
    def from_json(cls, payload: dict[str, str] | None) -> CredentialBundle:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            openai_api_key=(payload.get("openai_api_key") or "").strip() or None,
            anthropic_api_key=(payload.get("anthropic_api_key") or "").strip() or None,
            openrouter_api_key=(payload.get("openrouter_api_key") or "").strip() or None,
            google_api_key=(payload.get("google_api_key") or "").strip() or None,
        )


def credentials_from_env() -> CredentialBundle:
    return CredentialBundle(
        openai_api_key=(os.getenv("ARA_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip() or None,
        anthropic_api_key=(os.getenv("ARA_ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or "").strip() or None,
        openrouter_api_key=(os.getenv("ARA_OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY") or "").strip() or None,
        google_api_key=(os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip() or None,
    )


def parse_env_file(path: Path) -> CredentialBundle:
    if not path.exists() or not path.is_file():
        return CredentialBundle()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return CredentialBundle()
    env: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("'\"")
        env[key.strip()] = value
    return CredentialBundle(
        openai_api_key=(env.get("OPENAI_API_KEY") or env.get("ARA_OPENAI_API_KEY") or "").strip() or None,
        anthropic_api_key=(env.get("ANTHROPIC_API_KEY") or env.get("ARA_ANTHROPIC_API_KEY") or "").strip() or None,
        openrouter_api_key=(env.get("OPENROUTER_API_KEY") or env.get("ARA_OPENROUTER_API_KEY") or "").strip() or None,
        google_api_key=(env.get("GOOGLE_API_KEY") or env.get("ARA_GOOGLE_API_KEY") or "").strip() or None,
    )


_USER_CONFIG_DIR = Path.home() / ".ara"


@dataclass(slots=True)
class CredentialStore:
    """User-level credential store at ~/.ara/credentials.json."""
    credentials_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.credentials_path = _USER_CONFIG_DIR / "credentials.json"

    def load(self) -> CredentialBundle:
        if not self.credentials_path.exists():
            return CredentialBundle()
        try:
            payload = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return CredentialBundle()
        return CredentialBundle.from_json(payload)

    def save(self, creds: CredentialBundle) -> None:
        payload = creds.to_json()
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        self.credentials_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            os.chmod(self.credentials_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def prompt_for_credentials(existing: CredentialBundle, force: bool = False) -> tuple[CredentialBundle, bool]:
    current = CredentialBundle(
        openai_api_key=existing.openai_api_key,
        anthropic_api_key=existing.anthropic_api_key,
        openrouter_api_key=existing.openrouter_api_key,
        google_api_key=existing.google_api_key,
    )
    should_prompt = force or not current.has_any()
    if not should_prompt or not sys.stdin.isatty():
        return current, False
    if force:
        print("Key configuration: press Enter to keep existing values.")
    else:
        print("No API keys configured. Enter keys (leave blank to skip).")
    changed = False

    def _ask(label: str, existing_value: str | None) -> str | None:
        nonlocal changed
        prompt = f"{label} API key"
        if force and existing_value:
            prompt += " [Enter to keep]"
        value = getpass.getpass(prompt + ": ").strip()
        if not value:
            return existing_value
        changed = changed or (value != (existing_value or ""))
        return value

    current.openai_api_key = _ask("OpenAI", current.openai_api_key)
    current.anthropic_api_key = _ask("Anthropic", current.anthropic_api_key)
    current.openrouter_api_key = _ask("OpenRouter", current.openrouter_api_key)
    current.google_api_key = _ask("Google AI Studio", current.google_api_key)
    if not force and current.has_any() and not existing.has_any():
        changed = True
    return current, changed
