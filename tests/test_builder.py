# Location: tests/test_builder.py
# Purpose: Tests for engine/model builder
# Functions: test_infer_provider, test_build_engine
# Calls: ara.builder
# Imports: pytest

from ara.builder import infer_provider_for_model, build_engine
from ara.config import ARAConfig
from ara.model import EchoFallbackModel


def test_infer_google():
    assert infer_provider_for_model("gemini-2.0-flash") == "google"
    assert infer_provider_for_model("gemini-2.5-pro") == "google"


def test_infer_anthropic():
    assert infer_provider_for_model("claude-sonnet-4-6") == "anthropic"
    assert infer_provider_for_model("claude-opus-4-6") == "anthropic"


def test_infer_openai():
    assert infer_provider_for_model("gpt-4o") == "openai"
    assert infer_provider_for_model("o1-mini") == "openai"


def test_infer_openrouter():
    assert infer_provider_for_model("google/gemini-2.0-flash-exp:free") == "openrouter"
    assert infer_provider_for_model("anthropic/claude-3-opus") == "openrouter"


def test_infer_ollama():
    assert infer_provider_for_model("qwen3:8b") == "ollama"
    assert infer_provider_for_model("llama3.2") == "ollama"
    assert infer_provider_for_model("mistral") == "ollama"


def test_infer_unknown():
    assert infer_provider_for_model("some-random-model") is None


def test_build_engine_no_keys():
    cfg = ARAConfig()
    engine = build_engine(cfg)
    assert isinstance(engine.model, EchoFallbackModel)


def test_build_engine_with_google_key():
    cfg = ARAConfig(provider="google", google_api_key="test-key")
    engine = build_engine(cfg)
    # Should create a GeminiModel (not EchoFallback)
    from ara.model import GeminiModel
    assert isinstance(engine.model, GeminiModel)
