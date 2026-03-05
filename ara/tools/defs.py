# Location: ara/tools/defs.py
# Purpose: Tool JSON schemas for LLM tool calling
# Functions: get_tool_definitions, to_openai_tools, to_anthropic_tools
# Calls: N/A
# Imports: typing

from __future__ import annotations

from typing import Any

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "think",
        "description": "Record a private reasoning note. Not visible to the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "Your reasoning note."},
            },
            "required": ["note"],
        },
    },
    {
        "name": "subtask",
        "description": (
            "Delegate a sub-objective to a child agent. The child runs in its own "
            "recursive loop with its own step budget. Use for complex multi-step work."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "description": "Clear objective for the subtask."},
                "acceptance_criteria": {
                    "type": "string",
                    "description": "Specific criteria for judging the subtask result.",
                },
                "model": {
                    "type": "string",
                    "description": "Model name override (must be equal or lower tier).",
                },
                "reasoning_effort": {
                    "type": "string",
                    "description": "Reasoning effort override (low/medium/high).",
                },
            },
            "required": ["objective"],
        },
    },
    {
        "name": "execute",
        "description": (
            "Run a focused leaf task on the cheapest available model. "
            "Use for straightforward operations that don't need recursion."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "description": "Clear objective for the task."},
                "acceptance_criteria": {
                    "type": "string",
                    "description": "Specific criteria for judging the result.",
                },
            },
            "required": ["objective"],
        },
    },
    {
        "name": "search_semantic_scholar",
        "description": "Search Semantic Scholar for academic papers.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "description": "Max results (default 10)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_arxiv",
        "description": "Search arXiv for preprints.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "description": "Max results (default 10)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "request_approval",
        "description": (
            "Request user approval at a phase gate. Blocks until user responds. "
            "Returns: approved / rejected: {reason} / edited: {changes}"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "phase": {"type": "string", "description": "Phase name (scout, triage, analyst, etc)."},
                "summary": {"type": "string", "description": "Human-readable summary of results."},
                "data": {"type": "object", "description": "Structured data for the approval gate."},
            },
            "required": ["phase", "summary"],
        },
    },
    {
        "name": "track_cost",
        "description": "Report current session cost and remaining budget.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "embed_text",
        "description": "Generate an embedding vector for text (for similarity search).",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to embed."},
            },
            "required": ["text"],
        },
    },
]


def get_tool_definitions(
    include_subtask: bool = True,
    include_acceptance_criteria: bool = True,
) -> list[dict[str, Any]]:
    """Return tool definitions, optionally filtering subtask/execute."""
    defs = list(TOOL_DEFINITIONS)
    if not include_subtask:
        defs = [d for d in defs if d["name"] not in ("subtask", "execute")]
    return defs


def to_openai_tools(
    defs: list[dict[str, Any]] | None = None,
    strict: bool = True,
) -> list[dict[str, Any]]:
    """Convert to OpenAI function-calling format."""
    source = defs if defs is not None else TOOL_DEFINITIONS
    tools = []
    for d in source:
        params = dict(d.get("parameters", {}))
        if strict:
            params["additionalProperties"] = False
        tools.append({
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d.get("description", ""),
                "parameters": params,
                **({"strict": True} if strict else {}),
            },
        })
    return tools


def to_anthropic_tools(
    defs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Convert to Anthropic tool-calling format."""
    source = defs if defs is not None else TOOL_DEFINITIONS
    return [
        {
            "name": d["name"],
            "description": d.get("description", ""),
            "input_schema": d.get("parameters", {"type": "object", "properties": {}}),
        }
        for d in source
    ]
