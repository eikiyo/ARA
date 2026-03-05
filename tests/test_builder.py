# Location: tests/test_builder.py
# Purpose: Tests for engine builder
# Functions: test_build_engine
# Calls: ara.builder
# Imports: pytest

from ara.builder import build_engine
from ara.config import ARAConfig
from ara.model import EchoFallbackModel


def test_build_engine_no_keys():
    cfg = ARAConfig()
    engine = build_engine(cfg)
    assert isinstance(engine.model, EchoFallbackModel)


def test_build_engine_with_google_key():
    cfg = ARAConfig(google_api_key="test-key")
    engine = build_engine(cfg)
    from ara.model import GeminiModel
    assert isinstance(engine.model, GeminiModel)


def test_build_engine_custom_model():
    cfg = ARAConfig(model="gemini-2.5-pro", google_api_key="test-key")
    engine = build_engine(cfg)
    assert engine.model.model == "gemini-2.5-pro"


def test_build_engine_empty_model():
    cfg = ARAConfig(model="", google_api_key="test-key")
    engine = build_engine(cfg)
    assert engine.model.model == "gemini-2.0-flash"
