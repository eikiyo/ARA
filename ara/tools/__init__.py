# Location: ara/tools/__init__.py
# Purpose: Tool registry — dispatch tool calls to implementations
# Functions: ARATools, TOOL_DISPATCH
# Calls: search.py, papers.py, verification.py, research.py, writing.py, pipeline.py
# Imports: json, logging

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

from .defs import TOOL_DEFINITIONS
from . import search, papers, verification, research, writing, pipeline, quality

# Phase → allowed tool names (from arch.md §4.2)
# "search_*" is a wildcard matching all search_ tools
PHASE_TOOLS: dict[str, list[str]] = {
    "scout": ["search_*", "embed_text", "batch_embed_papers", "request_approval", "track_cost"],
    "analyst_triage": ["list_papers", "read_paper", "search_similar", "embed_text", "request_approval", "track_cost"],
    "analyst_deep_read": ["read_paper", "fetch_fulltext", "extract_claims", "search_similar", "request_approval", "track_cost"],
    "verifier": ["check_retraction", "get_citation_count", "validate_doi", "read_paper", "request_approval", "track_cost"],
    "hypothesis": ["read_paper", "search_similar", "score_hypothesis", "request_approval", "track_cost"],
    "brancher": ["search_*", "search_similar", "embed_text", "request_approval", "track_cost"],
    "critic": ["read_paper", "search_similar", "request_approval", "track_cost"],
    "writer": ["read_paper", "search_similar", "write_section", "get_citations", "generate_prisma_diagram", "request_approval", "track_cost"],
    "paper_critic": ["read_paper", "search_similar", "generate_quality_audit", "generate_prisma_diagram", "validate_all_citations", "request_approval", "track_cost"],
}


def _tool_matches_phase(tool_name: str, phase_tools: list[str]) -> bool:
    for pattern in phase_tools:
        if pattern.endswith("*") and tool_name.startswith(pattern[:-1]):
            return True
        if tool_name == pattern:
            return True
    return False

TOOL_DISPATCH: dict[str, Any] = {
    # Search tools (9 academic APIs)
    "search_semantic_scholar": search.search_semantic_scholar,
    "search_arxiv": search.search_arxiv,
    "search_crossref": search.search_crossref,
    "search_openalex": search.search_openalex,
    "search_pubmed": search.search_pubmed,
    "search_core": search.search_core,
    "search_dblp": search.search_dblp,
    "search_europe_pmc": search.search_europe_pmc,
    "search_base": search.search_base,
    "search_all": search.search_all,
    # Paper tools
    "fetch_fulltext": papers.fetch_fulltext,
    "read_paper": papers.read_paper,
    "list_papers": papers.list_papers,
    "search_similar": papers.search_similar,
    # Verification tools
    "check_retraction": verification.check_retraction,
    "get_citation_count": verification.get_citation_count,
    "validate_doi": verification.validate_doi,
    # Research tools
    "extract_claims": research.extract_claims,
    "score_hypothesis": research.score_hypothesis,
    "branch_search": research.branch_search,
    # Writing tools
    "write_section": writing.write_section,
    "get_citations": writing.get_citations,
    # Quality tools
    "generate_quality_audit": quality.generate_quality_audit,
    "generate_prisma_diagram": quality.generate_prisma_diagram,
    "validate_all_citations": quality.validate_all_citations,
    # Embedding tools
    "batch_embed_papers": pipeline.batch_embed_papers,
    # Pipeline tools
    "request_approval": pipeline.request_approval,
    "get_rules": pipeline.get_rules,
    "track_cost": pipeline.track_cost,
    "embed_text": pipeline.embed_text,
}


class ARATools:
    def __init__(
        self,
        workspace: Path | None = None,
        db: Any = None,
        session_id: int | None = None,
        approval_gates: bool = True,
    ):
        self.workspace = workspace or Path(".")
        self.db = db
        self.session_id = session_id
        self.approval_gates = approval_gates

    def get_definitions(self, include_subtask: bool = True, depth: int = 0, phase: str = "") -> list[dict[str, Any]]:
        # At depth 0 (manager), only expose delegation + pipeline tools
        _MANAGER_TOOLS = {"get_rules", "track_cost"}
        if depth == 0 and include_subtask:
            defs = [td for td in TOOL_DEFINITIONS if td["name"] in _MANAGER_TOOLS]
        elif phase and phase in PHASE_TOOLS:
            # Phase-specific filtering — child agents only see their allowed tools
            allowed = PHASE_TOOLS[phase]
            defs = [td for td in TOOL_DEFINITIONS if _tool_matches_phase(td["name"], allowed)]
        else:
            defs = list(TOOL_DEFINITIONS)

        if include_subtask:
            defs.append({
                "name": "subtask",
                "description": "Delegate a sub-objective to a child agent. Use for each research phase.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "objective": {"type": "string", "description": "What the subtask should accomplish"},
                        "acceptance_criteria": {"type": "string", "description": "How to judge if the subtask succeeded"},
                        "prompt": {"type": "string", "description": "Phase name for system prompt (scout, analyst_triage, etc.)"},
                    },
                    "required": ["objective"],
                },
            })
            defs.append({
                "name": "execute",
                "description": "Run a quick sub-objective without depth tracking. Cheaper than subtask.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "objective": {"type": "string", "description": "What to accomplish"},
                    },
                    "required": ["objective"],
                },
            })
        return defs

    def dispatch(self, tool_name: str, arguments: dict[str, Any]) -> str:
        handler = TOOL_DISPATCH.get(tool_name)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        # Validate required parameters before dispatch
        tool_def = next((t for t in TOOL_DEFINITIONS if t["name"] == tool_name), None)
        if tool_def:
            required = tool_def.get("parameters", {}).get("required", [])
            for req in required:
                if req not in arguments:
                    return json.dumps({"error": f"Tool '{tool_name}' missing required parameter: {req}"})

        # Inject context for tools that need it
        ctx = {
            "workspace": self.workspace,
            "db": self.db,
            "session_id": self.session_id,
            "approval_gates": self.approval_gates,
        }

        try:
            result = handler(arguments, ctx)
        except Exception as exc:
            _log.exception("Tool %s failed", tool_name)
            return json.dumps({"error": f"Tool '{tool_name}' failed: {exc}"})

        result_str = result if isinstance(result, str) else json.dumps(result, default=str)

        # Auto-store search results in DB
        if tool_name == "search_all":
            from .search import _search_all_full_results, _SEARCH_ALL_LOCK
            papers_to_store: list[dict[str, Any]] = []
            with _SEARCH_ALL_LOCK:
                if _search_all_full_results:
                    papers_to_store = list(_search_all_full_results)
                    _search_all_full_results.clear()
            if papers_to_store:
                self._store_papers_list(papers_to_store)
        elif tool_name.startswith("search_") and tool_name != "search_similar":
            self._store_search_results(result_str)

        return result_str

    def _store_papers_list(self, papers: list[dict[str, Any]]) -> None:
        if not self.db or not self.session_id or not papers:
            return
        try:
            stored = self.db.store_papers(self.session_id, papers)
            _log.info("Auto-stored %d/%d papers in DB", stored, len(papers))
        except Exception as exc:
            _log.error("Failed to store papers in DB: %s", exc)

    def _store_search_results(self, result_str: str) -> None:
        if not self.db or not self.session_id:
            return
        try:
            data = json.loads(result_str)
        except json.JSONDecodeError as exc:
            _log.warning("Failed to parse search results for storage: %s", exc)
            return

        papers = data.get("papers", [])
        self._store_papers_list(papers)
