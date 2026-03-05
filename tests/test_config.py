# Location: tests/test_config.py
# Purpose: Tests for ARA configuration
# Functions: test_default_config, test_from_env
# Calls: ara.config
# Imports: pytest

from ara.config import ARAConfig, PROVIDER_DEFAULT_MODELS


def test_default_config():
    cfg = ARAConfig()
    assert cfg.provider == "google"
    assert cfg.model == "gemini-2.0-flash"
    assert cfg.max_depth == 4
    assert cfg.max_steps_per_call == 80
    assert cfg.approval_gates is True


def test_provider_defaults():
    assert "google" in PROVIDER_DEFAULT_MODELS
    assert "anthropic" in PROVIDER_DEFAULT_MODELS
    assert "openai" in PROVIDER_DEFAULT_MODELS


def test_from_env(monkeypatch):
    monkeypatch.setenv("ARA_PROVIDER", "anthropic")
    monkeypatch.setenv("ARA_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("ARA_MAX_DEPTH", "6")
    cfg = ARAConfig.from_env(".")
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.max_depth == 6
