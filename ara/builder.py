# Location: ara/builder.py
# Purpose: Engine construction and model factory
# Functions: build_engine, build_model_factory, infer_provider_for_model
# Calls: engine.py, model.py, config.py, tools/
# Imports: re, pathlib

from __future__ import annotations

import logging
import re
from pathlib import Path

_log = logging.getLogger(__name__)

from .config import PROVIDER_DEFAULT_MODELS, ARAConfig
from .engine import RLMEngine, ModelFactory
from .model import (
    AnthropicModel, EchoFallbackModel, ModelError,
    OpenAICompatibleModel,
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
    # Ollama models use "name:tag" format (e.g., qwen3-coder:30b, gpt-oss:120b-cloud)
    # Check this BEFORE OpenAI regex to avoid false matches like "gpt-oss" → openai
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
    return PROVIDER_DEFAULT_MODELS.get(cfg.provider, "claude-sonnet-4-6")


def build_model_factory(cfg: ARAConfig) -> ModelFactory | None:
    def _factory(model_name: str, reasoning_effort: str | None = None) -> AnthropicModel | OpenAICompatibleModel:
        provider = infer_provider_for_model(model_name)
        effort = reasoning_effort or cfg.reasoning_effort
        if provider == "google" and cfg.google_api_key:
            return OpenAICompatibleModel(
                model=model_name, api_key=cfg.google_api_key,
                base_url=cfg.google_base_url, reasoning_effort=effort,
            )
        if provider == "anthropic" and cfg.anthropic_api_key:
            return AnthropicModel(
                model=model_name, api_key=cfg.anthropic_api_key,
                base_url=cfg.anthropic_base_url, reasoning_effort=effort,
            )
        if provider in ("openai", None) and cfg.openai_api_key:
            return OpenAICompatibleModel(
                model=model_name, api_key=cfg.openai_api_key,
                base_url=cfg.openai_base_url, reasoning_effort=effort,
            )
        if provider == "openrouter" and cfg.openrouter_api_key:
            return OpenAICompatibleModel(
                model=model_name, api_key=cfg.openrouter_api_key,
                base_url=cfg.openrouter_base_url, reasoning_effort=effort,
                extra_headers={"HTTP-Referer": "https://github.com/ara-research", "X-Title": "ARA"},
            )
        if provider == "ollama":
            return OpenAICompatibleModel(
                model=model_name, api_key="ollama",
                base_url=cfg.ollama_base_url, reasoning_effort=effort,
                first_byte_timeout=120, strict_tools=False,
            )
        # Fallback: model requested a provider we don't have keys for (e.g. Gemini
        # tried to delegate to gpt-4).  Re-route to the current model instead.
        fallback = _resolve_model_name(cfg)
        fallback_provider = cfg.provider or infer_provider_for_model(fallback)
        if fallback_provider == "google" and cfg.google_api_key:
            _log.warning("No API key for '%s' (provider=%s) — falling back to '%s' via Google",
                         model_name, provider, fallback)
            return OpenAICompatibleModel(
                model=fallback, api_key=cfg.google_api_key,
                base_url=cfg.google_base_url, reasoning_effort=effort,
            )
        if fallback_provider == "ollama":
            _log.warning("No API key for '%s' (provider=%s) — falling back to '%s' via Ollama",
                         model_name, provider, fallback)
            return OpenAICompatibleModel(
                model=fallback, api_key="ollama",
                base_url=cfg.ollama_base_url, reasoning_effort=effort,
                first_byte_timeout=120, strict_tools=False,
            )
        raise ModelError(f"No API key for model '{model_name}' (provider={provider})")

    if cfg.google_api_key or cfg.anthropic_api_key or cfg.openai_api_key or cfg.openrouter_api_key or cfg.ollama_base_url:
        return _factory
    return None


def build_engine(cfg: ARAConfig) -> RLMEngine:
    tools = ARATools(workspace=cfg.workspace, approval_gates=cfg.approval_gates)
    try:
        model_name = _resolve_model_name(cfg)
    except ModelError as exc:
        model = EchoFallbackModel(note=str(exc))
        return RLMEngine(model=model, tools=tools, config=cfg)

    if cfg.provider == "google" and cfg.google_api_key:
        model = OpenAICompatibleModel(
            model=model_name, api_key=cfg.google_api_key,
            base_url=cfg.google_base_url, reasoning_effort=cfg.reasoning_effort,
        )
    elif cfg.provider == "anthropic" and cfg.anthropic_api_key:
        model = AnthropicModel(
            model=model_name, api_key=cfg.anthropic_api_key,
            base_url=cfg.anthropic_base_url, reasoning_effort=cfg.reasoning_effort,
        )
    elif cfg.provider == "openai" and cfg.openai_api_key:
        model = OpenAICompatibleModel(
            model=model_name, api_key=cfg.openai_api_key,
            base_url=cfg.openai_base_url, reasoning_effort=cfg.reasoning_effort,
        )
    elif cfg.provider == "openrouter" and cfg.openrouter_api_key:
        model = OpenAICompatibleModel(
            model=model_name, api_key=cfg.openrouter_api_key,
            base_url=cfg.openrouter_base_url, reasoning_effort=cfg.reasoning_effort,
            extra_headers={"HTTP-Referer": "https://github.com/ara-research", "X-Title": "ARA"},
        )
    elif cfg.provider == "ollama":
        model = OpenAICompatibleModel(
            model=model_name, api_key="ollama",
            base_url=cfg.ollama_base_url, reasoning_effort=cfg.reasoning_effort,
            first_byte_timeout=120, strict_tools=False,
        )
    else:
        model = EchoFallbackModel()

    return RLMEngine(
        model=model, tools=tools, config=cfg,
        model_factory=build_model_factory(cfg),
    )
