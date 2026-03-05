# Location: ara/tools/__init__.py
# Purpose: Tool registry — all tool definitions + dispatch
# Functions: ARATools class, TOOL_DISPATCH mapping
# Calls: tools/defs.py, tools/pipeline.py
# Imports: dataclasses, pathlib, typing

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .defs import TOOL_DEFINITIONS, get_tool_definitions, to_openai_tools, to_anthropic_tools


@dataclass
class ARATools:
    """Tool executor for ARA research operations."""
    workspace: Path

    def dispatch(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool by name. Returns observation string."""
        handler = TOOL_DISPATCH.get(name)
        if handler is None:
            return f"Unknown tool: {name}"
        return handler(self, args)


def _think(tools: ARATools, args: dict[str, Any]) -> str:
    return f"Thought noted: {args.get('note', '')}"


def _search_semantic_scholar(tools: ARATools, args: dict[str, Any]) -> str:
    return "[stub] search_semantic_scholar not yet implemented"


def _search_arxiv(tools: ARATools, args: dict[str, Any]) -> str:
    return "[stub] search_arxiv not yet implemented"


def _request_approval(tools: ARATools, args: dict[str, Any]) -> str:
    return "approved"


def _track_cost(tools: ARATools, args: dict[str, Any]) -> str:
    return '{"total_usd": 0.0, "remaining_usd": 0.0}'


def _embed_text(tools: ARATools, args: dict[str, Any]) -> str:
    return "[stub] embed_text not yet implemented"


TOOL_DISPATCH: dict[str, Any] = {
    "think": _think,
    "search_semantic_scholar": _search_semantic_scholar,
    "search_arxiv": _search_arxiv,
    "request_approval": _request_approval,
    "track_cost": _track_cost,
    "embed_text": _embed_text,
}
