# Location: ara/builder.py
# Purpose: Engine construction and model factory
# Functions: build_engine, build_model_factory, infer_provider_for_model
# Calls: engine.py, model.py, config.py, tools/
# Imports: re

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

from .config import PROVIDER_DEFAULT_MODELS, ARAConfig
from .engine import RLMEngine, ModelFactory
from .model import (
    GeminiModel, OpenAIModel, AnthropicModel,
    EchoFallbackModel, ModelError,
)
from .tools import ARATools

_GOOGLE_RE = re.compile(r"^gemini", re.IGNORECASE)
_ANTHROPIC_RE = re.compile(r"^claude", re.IGNORECASE)
_OPENAI_RE = re.compile(r"^(gpt-[0-9]|o[1-4]-|o[1-4]$|chatgpt)", re.IGNORECASE)
_OLLAMA_RE = re.compile(
    r"^(llama|mistral|gemma|phi|codellama|deepseek|vicuna|"
    r"tinyllama|dolphin|wizardlm|orca|nous-hermes|qwen|"
    r"gpt-oss|minimax|glm|mxbai|nomic|all-minilm)",
    re.IGNORECASE,
)


def infer_provider_for_model(model: str) -> str | None:
    if "/" in model:
        return "openrouter"
    if ":" in model:
        return "ollama"
    if _GOOGLE_RE.search(model):
        return "google"
    if _ANTHROPIC_RE.search(model):
        return "anthropic"
    if _OPENAI_RE.search(model):
        return "openai"
    if _OLLAMA_RE.search(model):
        return "ollama"
    return None


def _resolve_model_name(cfg: ARAConfig) -> str:
    selected = (cfg.model or "").strip()
    if selected and selected.lower() != "newest":
        return selected
    return PROVIDER_DEFAULT_MODELS.get(cfg.provider, "gemini-2.0-flash")


def _create_model(
    provider: str, model_name: str, cfg: ARAConfig,
    reasoning_effort: str | None = None,
) -> GeminiModel | OpenAIModel | AnthropicModel:
    effort = reasoning_effort or cfg.reasoning_effort

    if provider == "google" and cfg.google_api_key:
        return GeminiModel(model=model_name, api_key=cfg.google_api_key)

    if provider == "anthropic" and cfg.anthropic_api_key:
        return AnthropicModel(
            model=model_name, api_key=cfg.anthropic_api_key,
            reasoning_effort=effort,
        )

    if provider == "openai" and cfg.openai_api_key:
        return OpenAIModel(
            model=model_name, api_key=cfg.openai_api_key,
            reasoning_effort=effort,
        )

    if provider == "openrouter" and cfg.openrouter_api_key:
        return OpenAIModel(
            model=model_name, api_key=cfg.openrouter_api_key,
            base_url=cfg.openrouter_base_url,
            reasoning_effort=effort,
            extra_headers={"HTTP-Referer": "https://github.com/ara-research", "X-Title": "ARA"},
        )

    if provider == "ollama":
        return OpenAIModel(
            model=model_name, api_key="ollama",
            base_url=cfg.ollama_base_url,
            reasoning_effort=effort,
            timeout=120,
        )

    raise ModelError(f"No API key for provider '{provider}'")


def build_model_factory(cfg: ARAConfig) -> ModelFactory | None:
    def _factory(model_name: str, reasoning_effort: str | None = None) -> GeminiModel | OpenAIModel | AnthropicModel:
        provider = infer_provider_for_model(model_name)
        if not provider:
            provider = cfg.provider
        try:
            return _create_model(provider, model_name, cfg, reasoning_effort)
        except ModelError:
            # Fallback to current provider's default model
            fallback = _resolve_model_name(cfg)
            _log.warning("No key for '%s' (provider=%s), falling back to '%s'",
                         model_name, provider, fallback)
            return _create_model(cfg.provider, fallback, cfg, reasoning_effort)

    has_any = (cfg.google_api_key or cfg.anthropic_api_key or cfg.openai_api_key
               or cfg.openrouter_api_key or cfg.provider == "ollama")
    return _factory if has_any else None


def build_engine(cfg: ARAConfig) -> RLMEngine:
    tools = ARATools(workspace=cfg.workspace, approval_gates=cfg.approval_gates)

    try:
        model_name = _resolve_model_name(cfg)
        model = _create_model(cfg.provider, model_name, cfg)
    except ModelError as exc:
        model = EchoFallbackModel(note=str(exc))

    return RLMEngine(
        model=model, tools=tools, config=cfg,
        model_factory=build_model_factory(cfg),
    )
