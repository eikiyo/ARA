# Location: ara/builder.py
# Purpose: Engine construction — creates GeminiModel + RLMEngine
# Functions: build_engine
# Calls: engine.py, model.py, config.py, tools/
# Imports: logging

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

from .config import ARAConfig
from .engine import RLMEngine
from .model import GeminiModel, EchoFallbackModel, ModelError
from .tools import ARATools


def build_engine(cfg: ARAConfig) -> RLMEngine:
    tools = ARATools(workspace=cfg.workspace, approval_gates=cfg.approval_gates)

    model_name = (cfg.model or "gemini-2.0-flash").strip()
    writer_model_name = (cfg.writer_model or "gemini-2.5-pro").strip()

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

    return RLMEngine(model=model, tools=tools, config=cfg, writer_model=writer_model)
