# Location: ara/builder.py
# Purpose: Engine construction — creates GeminiModel + RLMEngine + phase-specific models
# Functions: build_engine, _build_hypothesis_model
# Calls: engine.py, model.py, config.py, tools/
# Imports: logging

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

from .config import ARAConfig
from .engine import RLMEngine
from .model import (
    GeminiModel, AnthropicModel, OpenAIModel, LoadBalancedModel,
    EchoFallbackModel, ModelError,
)
from .tools import ARATools


def _build_hypothesis_model(cfg: ARAConfig) -> any:
    """Build load-balanced model for hypothesis/critic phases (Opus 50% + GPT-5.4 50%)."""
    models: list[tuple[any, float]] = []

    # Opus 4.6 — 50% weight
    if cfg.anthropic_api_key:
        try:
            opus = AnthropicModel(model="claude-opus-4-6", api_key=cfg.anthropic_api_key)
            models.append((opus, 0.5))
            _log.info("Hypothesis model: Opus 4.6 loaded (50%%)")
        except Exception as exc:
            _log.warning("Failed to create Opus 4.6 for hypothesis: %s", exc)

    # GPT-5.4 — 50% weight
    if cfg.openai_api_key:
        try:
            gpt = OpenAIModel(model="gpt-5.4", api_key=cfg.openai_api_key)
            models.append((gpt, 0.5))
            _log.info("Hypothesis model: GPT-5.4 loaded (50%%)")
        except Exception as exc:
            _log.warning("Failed to create GPT-5.4 for hypothesis: %s", exc)

    if not models:
        _log.warning("No hypothesis models available — falling back to task model")
        return None

    if len(models) == 1:
        _log.info("Hypothesis model: only one provider available — using %s at 100%%", models[0][0].model)
        return models[0][0]

    return LoadBalancedModel(models)


def build_engine(cfg: ARAConfig) -> RLMEngine:
    tools = ARATools(workspace=cfg.workspace, approval_gates=cfg.approval_gates, config=cfg)

    model_name = (cfg.model or "gemini-3.1-pro-preview").strip()
    writer_model_name = (cfg.writer_model or "gemini-3.1-pro-preview").strip()

    if cfg.google_api_key:
        try:
            model = GeminiModel(model=model_name, api_key=cfg.google_api_key)
        except Exception as exc:
            _log.error("Failed to create Gemini model: %s", exc)
            model = EchoFallbackModel(note=str(exc))
        try:
            writer_model = GeminiModel(model=writer_model_name, api_key=cfg.google_api_key)
        except Exception as exc:
            _log.warning("Failed to create writer model (%s), falling back to main model: %s", writer_model_name, exc)
            writer_model = model
    else:
        model = EchoFallbackModel(note="No Google API key configured")
        writer_model = model

    # Build light model for mechanical phases (scout, verifier, protocol)
    light_model_name = (cfg.light_model or "gemini-3.1-flash-lite-preview").strip()
    light_model = model  # fallback to main model
    if cfg.google_api_key and light_model_name != model_name:
        try:
            light_model = GeminiModel(model=light_model_name, api_key=cfg.google_api_key)
            _log.info("Light model: %s (for scout/verifier/protocol)", light_model_name)
        except Exception as exc:
            _log.warning("Failed to create light model (%s), using main model: %s", light_model_name, exc)

    # Build deep_read model (Flash 3.1 — fast extraction, avoid Pro rate limits)
    deep_read_model_name = "gemini-3-flash-preview"
    deep_read_model = model  # fallback
    if cfg.google_api_key and deep_read_model_name != model_name:
        try:
            deep_read_model = GeminiModel(model=deep_read_model_name, api_key=cfg.google_api_key)
            _log.info("Deep read model: %s (for high-volume claim extraction)", deep_read_model_name)
        except Exception as exc:
            _log.warning("Failed to create deep_read model (%s), using main model: %s", deep_read_model_name, exc)

    # Build hypothesis/critic model (Opus 50% + GPT-5.4 50%)
    hypothesis_model = None
    if cfg.hypothesis_model == "load_balanced":
        hypothesis_model = _build_hypothesis_model(cfg)

    return RLMEngine(
        model=model, tools=tools, config=cfg,
        writer_model=writer_model,
        hypothesis_model=hypothesis_model,
        light_model=light_model,
        deep_read_model=deep_read_model,
    )
