# Location: ara/credentials.py
# Purpose: API key management — load/save/prompt for Google, Anthropic, OpenAI keys
# Functions: CredentialStore, load_api_key, load_anthropic_api_key, load_openai_api_key
# Calls: N/A
# Imports: json, os, pathlib

from __future__ import annotations

import json
import os
import stat
from pathlib import Path


class CredentialStore:
    def __init__(self, path: Path | None = None):
        self._path = path or Path.home() / ".ara" / "credentials.json"

    def load(self) -> str | None:
        # 1. Environment variables (highest priority)
        key = os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if key:
            return key

        # 2. Credentials file
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text("utf-8"))
                key = data.get("google_api_key")
                if key:
                    return key
            except (OSError, json.JSONDecodeError):
                pass

        return None

    def load_from_env_file(self, env_path: Path) -> str | None:
        if not env_path.exists():
            return None
        for line in env_path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key_name, _, val = line.partition("=")
            key_name, val = key_name.strip(), val.strip().strip("\"'")
            if key_name in ("GOOGLE_API_KEY", "ARA_GOOGLE_API_KEY") and val:
                return val
        return None

    def save(self, api_key: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"google_api_key": api_key}, indent=2), "utf-8",
        )
        self._path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def prompt_and_save(self) -> str | None:
        import getpass
        current = self.load()
        hint = f" [{current[:8]}...]" if current else ""
        val = getpass.getpass(f"Google AI Studio API key{hint}: ").strip()
        if val:
            self.save(val)
            return val
        return current


def load_api_key(workspace: Path | None = None) -> str | None:
    store = CredentialStore()
    key = store.load()
    if key:
        return key
    if workspace:
        env_path = workspace / ".env"
        key = store.load_from_env_file(env_path)
    return key


def load_anthropic_api_key(workspace: Path | None = None) -> str | None:
    """Load Anthropic API key from env vars, credentials file, or .env file."""
    # 1. Environment variables
    key = os.getenv("ARA_ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if key:
        return key

    # 2. Credentials file
    cred_path = Path.home() / ".ara" / "credentials.json"
    if cred_path.exists():
        try:
            data = json.loads(cred_path.read_text("utf-8"))
            key = data.get("anthropic_api_key")
            if key:
                return key
        except (OSError, json.JSONDecodeError):
            pass

    # 3. Workspace .env
    if workspace:
        env_path = workspace / ".env"
        if env_path.exists():
            for line in env_path.read_text("utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key_name, _, val = line.partition("=")
                key_name, val = key_name.strip(), val.strip().strip("\"'")
                if key_name in ("ANTHROPIC_API_KEY", "ARA_ANTHROPIC_API_KEY") and val:
                    return val

    return None


def load_openai_api_key(workspace: Path | None = None) -> str | None:
    """Load OpenAI API key from env vars, credentials file, or .env file."""
    # 1. Environment variables
    key = os.getenv("ARA_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if key:
        return key

    # 2. Credentials file
    cred_path = Path.home() / ".ara" / "credentials.json"
    if cred_path.exists():
        try:
            data = json.loads(cred_path.read_text("utf-8"))
            key = data.get("openai_api_key")
            if key:
                return key
        except (OSError, json.JSONDecodeError):
            pass

    # 3. Workspace .env
    if workspace:
        env_path = workspace / ".env"
        if env_path.exists():
            for line in env_path.read_text("utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key_name, _, val = line.partition("=")
                key_name, val = key_name.strip(), val.strip().strip("\"'")
                if key_name in ("OPENAI_API_KEY", "ARA_OPENAI_API_KEY") and val:
                    return val

    return None
