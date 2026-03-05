# Location: tests/test_config.py
# Purpose: Tests for ARA configuration
# Functions: test_default_config, test_from_env
# Calls: ara.config
# Imports: pytest

from ara.config import ARAConfig


def test_default_config():
    cfg = ARAConfig()
    assert cfg.model == "gemini-2.5-flash"
    assert cfg.max_depth == 4
    assert cfg.max_steps_per_call == 150
    assert cfg.max_tool_calls_per_turn == 1
    assert cfg.approval_gates is True
    assert cfg.google_api_key is None


def test_from_env(monkeypatch):
    monkeypatch.setenv("ARA_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("ARA_MAX_DEPTH", "6")
    monkeypatch.setenv("ARA_MAX_TOOL_CALLS_PER_TURN", "20")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-123")
    cfg = ARAConfig.from_env(".")
    assert cfg.model == "gemini-2.5-pro"
    assert cfg.max_depth == 6
    assert cfg.max_tool_calls_per_turn == 20
    assert cfg.google_api_key == "test-key-123"


def test_from_env_defaults(monkeypatch):
    # Clear any env vars that might interfere
    monkeypatch.delenv("ARA_MODEL", raising=False)
    monkeypatch.delenv("ARA_GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    cfg = ARAConfig.from_env(".")
    assert cfg.model == "gemini-2.5-flash"
    assert cfg.google_api_key is None
