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
from . import search, papers, verification, research, writing, pipeline, quality, fulltext, economic_data, novelty

# Phase → allowed tool names (from arch.md §4.2)
# "search_*" is a wildcard matching all search_ tools
PHASE_TOOLS: dict[str, list[str]] = {
    "scout": ["search_*"],
    "analyst_triage": ["list_papers", "read_paper", "rate_papers"],
    "analyst_deep_read": ["read_paper", "fetch_fulltext", "extract_claims", "assess_risk_of_bias", "search_similar", "list_papers", "list_claims"],
    "verifier": ["list_papers", "check_retraction", "get_citation_count", "validate_doi", "verify_claim"],
    "hypothesis": ["read_paper", "list_papers", "list_claims", "search_similar", "search_evidence", "score_hypothesis", "score_novelty", "identify_gaps", "get_risk_of_bias_table", "get_grade_table", "compute_effect_size", "check_journal_ranking"],
    "brancher": ["search_*", "search_similar", "search_evidence", "list_claims", "list_papers", "read_paper", "get_risk_of_bias_table", "get_grade_table", "score_novelty", "identify_gaps", "compute_effect_size", "check_journal_ranking"],
    "critic": ["read_paper", "list_papers", "list_claims", "search_similar", "search_evidence", "get_risk_of_bias_table", "get_grade_table", "score_novelty", "compute_effect_size", "check_journal_ranking"],
    "synthesis": ["list_papers", "read_paper", "rate_grade_evidence", "get_risk_of_bias_table", "get_grade_table", "write_section"],
    "protocol": ["list_papers", "write_section"],
    "writer": ["list_papers", "list_claims", "read_paper", "search_similar", "search_evidence", "write_section", "get_citations", "get_risk_of_bias_table", "get_grade_table", "generate_prisma_diagram"],
    "advisory_board": ["write_section"],  # All data pre-gathered — advisory only needs to save the JSON plan
    "paper_critic": ["read_paper", "search_similar", "search_evidence", "list_papers", "list_claims", "get_risk_of_bias_table", "get_grade_table", "generate_quality_audit", "generate_prisma_diagram", "validate_all_citations", "write_section"],
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
    "snowball_references": search.snowball_references,
    # Economic data tools (7 IFC/economic sources)
    "search_world_bank": economic_data.search_world_bank,
    "search_fred": economic_data.search_fred,
    "search_imf": economic_data.search_imf,
    "search_oecd": economic_data.search_oecd,
    "search_comtrade": economic_data.search_comtrade,
    "search_eurostat": economic_data.search_eurostat,
    "search_countries": economic_data.search_countries,
    # Tier 1 data tools
    "search_exchange_rates": economic_data.search_exchange_rates,
    "search_patents": economic_data.search_patents,
    "search_wto": economic_data.search_wto,
    "search_transparency": economic_data.search_transparency,
    # Tier 2 data tools
    "search_sec_edgar": economic_data.search_sec_edgar,
    "search_un_sdg": economic_data.search_un_sdg,
    "search_who": economic_data.search_who,
    "search_ilo": economic_data.search_ilo,
    "search_air_quality": economic_data.search_air_quality,
    # Paper tools
    "fetch_fulltext": papers.fetch_fulltext,
    "read_paper": papers.read_paper,
    "list_papers": papers.list_papers,
    "search_similar": papers.search_similar,
    "search_evidence": papers.search_evidence,
    "list_claims": papers.list_claims,
    "rate_papers": papers.rate_papers,
    # Verification tools
    "check_retraction": verification.check_retraction,
    "get_citation_count": verification.get_citation_count,
    "validate_doi": verification.validate_doi,
    # Research tools
    "extract_claims": research.extract_claims,
    "verify_claim": research.verify_claim,
    "assess_risk_of_bias": research.assess_risk_of_bias,
    "rate_grade_evidence": research.rate_grade_evidence,
    "get_risk_of_bias_table": research.get_risk_of_bias_table,
    "get_grade_table": research.get_grade_table,
    "score_hypothesis": research.score_hypothesis,
    "branch_search": research.branch_search,
    # Novelty & gap analysis tools
    "score_novelty": novelty.score_novelty,
    "identify_gaps": novelty.identify_gaps,
    "compute_effect_size": novelty.compute_effect_size,
    "check_journal_ranking": novelty.check_journal_ranking,
    # Writing tools
    "write_section": writing.write_section,
    "get_citations": writing.get_citations,
    # Quality tools
    "generate_quality_audit": quality.generate_quality_audit,
    "generate_prisma_diagram": quality.generate_prisma_diagram,
    "validate_all_citations": quality.validate_all_citations,
    # Fulltext tools
    "batch_fetch_fulltext": fulltext.batch_fetch_fulltext,
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
        config: Any = None,
    ):
        self.workspace = workspace or Path(".")
        self.db = db
        self.session_id = session_id
        self.approval_gates = approval_gates
        self.config = config
        self.central_db = getattr(db, '_central', None) if db else None
        self.plan: dict[str, Any] | None = None  # Advisory board plan (set by engine during writer phase)
        self.topic: str = ""  # Research topic (set by engine)

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
        _log.info("DISPATCH: %s(%s)", tool_name, json.dumps(arguments, default=str)[:150])
        handler = TOOL_DISPATCH.get(tool_name)
        if handler is None:
            _log.warning("DISPATCH: unknown tool %s", tool_name)
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        # Validate required parameters before dispatch
        tool_def = next((t for t in TOOL_DEFINITIONS if t["name"] == tool_name), None)
        if tool_def:
            required = tool_def.get("parameters", {}).get("required", [])
            for req in required:
                if req not in arguments:
                    return json.dumps({"error": f"Tool '{tool_name}' missing required parameter: {req}"})

        # Inject context for tools that need it
        ctx: dict[str, Any] = {
            "workspace": self.workspace,
            "db": self.db,
            "session_id": self.session_id,
            "approval_gates": self.approval_gates,
            "central_db": self.central_db,
        }
        if self.config:
            ctx["min_papers"] = self.config.min_papers
            ctx["config"] = self.config
        if self.plan:
            ctx["plan"] = self.plan
        if self.topic:
            ctx["topic"] = self.topic

        try:
            import signal
            import platform
            import threading

            # Long-running tools get extended timeouts
            _LONG_TOOLS = {"search_all", "batch_fetch_fulltext", "batch_embed_papers"}
            if tool_name.startswith("search_") or tool_name in _LONG_TOOLS:
                _TOOL_TIMEOUT = 600
            else:
                _TOOL_TIMEOUT = 120

            # SIGALRM only works in main thread — skip timeout in worker threads
            is_main_thread = threading.current_thread() is threading.main_thread()

            if platform.system() != "Windows" and is_main_thread:
                def _timeout_handler(signum, frame):
                    raise TimeoutError(f"Tool '{tool_name}' timed out after {_TOOL_TIMEOUT}s")

                old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
                signal.alarm(_TOOL_TIMEOUT)
                try:
                    result = handler(arguments, ctx)
                finally:
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, old_handler)
            else:
                result = handler(arguments, ctx)
        except TimeoutError as exc:
            _log.error("DISPATCH TIMEOUT: %s — %s", tool_name, exc)
            return json.dumps({"error": f"Tool '{tool_name}' timed out after {_TOOL_TIMEOUT}s"})
        except Exception as exc:
            _log.exception("DISPATCH FAIL: %s — %s", tool_name, exc)
            return json.dumps({"error": f"Tool '{tool_name}' failed: {exc}"})

        result_str = result if isinstance(result, str) else json.dumps(result, default=str)
        _log.info("DISPATCH RESULT: %s → %d chars | preview: %s", tool_name, len(result_str), result_str[:120])

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
        elif tool_name.startswith("search_") and tool_name not in ("search_similar", "search_evidence"):
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
