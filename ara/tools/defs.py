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
    # === SEARCH TOOLS (9) ===
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
        "description": "Search arXiv for preprints in CS, physics, math, stats.",
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
        "name": "search_crossref",
        "description": "Search CrossRef for papers with DOI validation.",
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
        "name": "search_openalex",
        "description": "Search OpenAlex for papers (largest open academic index).",
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
        "name": "search_pubmed",
        "description": "Search PubMed for biomedical and life science papers.",
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
        "name": "search_core",
        "description": "Search CORE for open access papers (requires CORE_API_KEY).",
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
        "name": "search_dblp",
        "description": "Search DBLP for computer science papers.",
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
        "name": "search_europe_pmc",
        "description": "Search Europe PMC for biomedical papers.",
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
        "name": "search_base",
        "description": "Search BASE (Bielefeld Academic Search Engine).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "description": "Max results (default 10)."},
            },
            "required": ["query"],
        },
    },
    # === PAPER TOOLS ===
    {
        "name": "fetch_fulltext",
        "description": "Fetch open access version of paper via Unpaywall API.",
        "parameters": {
            "type": "object",
            "properties": {
                "doi": {"type": "string", "description": "DOI of the paper."},
            },
            "required": ["doi"],
        },
    },
    {
        "name": "read_paper",
        "description": "Read paper from database and return formatted summary.",
        "parameters": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "integer", "description": "Paper ID in database."},
            },
            "required": ["paper_id"],
        },
    },
    {
        "name": "search_similar",
        "description": "Search for papers similar to query text in current session.",
        "parameters": {
            "type": "object",
            "properties": {
                "query_text": {"type": "string", "description": "Text to find similar papers for."},
                "limit": {"type": "integer", "description": "Max results (default 5)."},
            },
            "required": ["query_text"],
        },
    },
    # === VERIFICATION TOOLS ===
    {
        "name": "check_retraction",
        "description": "Check if a paper is retracted via CrossRef API.",
        "parameters": {
            "type": "object",
            "properties": {
                "doi": {"type": "string", "description": "DOI of the paper."},
            },
            "required": ["doi"],
        },
    },
    {
        "name": "get_citation_count",
        "description": "Get citation count from Semantic Scholar.",
        "parameters": {
            "type": "object",
            "properties": {
                "doi": {"type": "string", "description": "DOI of the paper."},
            },
            "required": ["doi"],
        },
    },
    {
        "name": "validate_doi",
        "description": "Validate DOI and check if it resolves.",
        "parameters": {
            "type": "object",
            "properties": {
                "doi": {"type": "string", "description": "DOI to validate."},
            },
            "required": ["doi"],
        },
    },
    # === RESEARCH TOOLS ===
    {
        "name": "extract_claims",
        "description": "Extract atomic claims from a paper. Returns instruction for the agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "integer", "description": "Paper ID to extract claims from."},
            },
            "required": ["paper_id"],
        },
    },
    {
        "name": "score_hypothesis",
        "description": "Generate scoring template for evaluating a hypothesis across dimensions.",
        "parameters": {
            "type": "object",
            "properties": {
                "hypothesis_text": {"type": "string", "description": "The hypothesis to score."},
                "dimensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Scoring dimensions.",
                },
            },
            "required": ["hypothesis_text"],
        },
    },
    {
        "name": "branch_search",
        "description": "Perform cross-domain search based on hypothesis and branch type.",
        "parameters": {
            "type": "object",
            "properties": {
                "hypothesis_text": {"type": "string", "description": "The hypothesis to branch from."},
                "branch_type": {
                    "type": "string",
                    "description": "Type: lateral, methodological, analogical, or convergent.",
                },
                "query": {"type": "string", "description": "Search query for the branch."},
            },
            "required": ["hypothesis_text", "branch_type", "query"],
        },
    },
    # === WRITING TOOLS ===
    {
        "name": "write_section",
        "description": "Save a paper section to .ara/output/sections/{section_name}.md",
        "parameters": {
            "type": "object",
            "properties": {
                "section_name": {"type": "string", "description": "Section name (e.g., introduction)."},
                "content": {"type": "string", "description": "Markdown content of the section."},
            },
            "required": ["section_name", "content"],
        },
    },
    {
        "name": "get_citations",
        "description": "Generate BibTeX bibliography from all papers in session.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    # === PIPELINE TOOLS ===
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
        "name": "get_rules",
        "description": "Get all active user rules for the current session.",
        "parameters": {
            "type": "object",
            "properties": {},
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
