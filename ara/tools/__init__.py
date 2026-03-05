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
from . import search, papers, verification, research, writing, pipeline

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
    # Paper tools
    "fetch_fulltext": papers.fetch_fulltext,
    "read_paper": papers.read_paper,
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

    def get_definitions(self, include_subtask: bool = True) -> list[dict[str, Any]]:
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
        if tool_name.startswith("search_") and tool_name != "search_similar":
            self._store_search_results(result_str)

        return result_str

    def _store_search_results(self, result_str: str) -> None:
        if not self.db or not self.session_id:
            return
        try:
            data = json.loads(result_str)
        except json.JSONDecodeError as exc:
            _log.warning("Failed to parse search results for storage: %s", exc)
            return

        papers = data.get("papers", [])
        if papers:
            try:
                stored = self.db.store_papers(self.session_id, papers)
                _log.info("Auto-stored %d/%d papers in DB", stored, len(papers))
            except Exception as exc:
                _log.error("Failed to store papers in DB: %s", exc)
