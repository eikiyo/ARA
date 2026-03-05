# Location: ara/__init__.py
# Purpose: ARA package init — exports core classes
# Functions: N/A
# Calls: N/A
# Imports: config, engine, model, runtime

"""ARA — Adaptive Research Agent."""

__version__ = "0.1.0"

from .config import ARAConfig
from .engine import RLMEngine
from .model import (
    AnthropicModel,
    Conversation,
    ModelTurn,
    OpenAICompatibleModel,
    ToolCall,
    ToolResult,
)
from .runtime import SessionRuntime, SessionStore

__all__ = [
    "ARAConfig",
    "AnthropicModel",
    "Conversation",
    "ModelTurn",
    "OpenAICompatibleModel",
    "RLMEngine",
    "SessionRuntime",
    "SessionStore",
    "ToolCall",
    "ToolResult",
]
