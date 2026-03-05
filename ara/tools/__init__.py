# Location: ara/tools/__init__.py
# Purpose: Tool registry — all tool definitions + dispatch
# Functions: ARATools class, TOOL_DISPATCH mapping
# Calls: tools/search.py, tools/papers.py, tools/verification.py, tools/research.py, tools/writing.py, tools/pipeline.py
# Imports: dataclasses, pathlib, typing, json

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..logging import get_logger
from .defs import TOOL_DEFINITIONS, get_tool_definitions, to_openai_tools, to_anthropic_tools

_log = get_logger("tools")
from .search import (
    search_semantic_scholar as _search_semantic_scholar_impl,
    search_arxiv as _search_arxiv_impl,
    search_crossref as _search_crossref_impl,
    search_openalex as _search_openalex_impl,
    search_pubmed as _search_pubmed_impl,
    search_core as _search_core_impl,
    search_dblp as _search_dblp_impl,
    search_europe_pmc as _search_europe_pmc_impl,
    search_base as _search_base_impl,
)
from .papers import (
    fetch_fulltext as _fetch_fulltext_impl,
    read_paper as _read_paper_impl,
    search_similar as _search_similar_impl,
)
from .verification import (
    check_retraction as _check_retraction_impl,
    get_citation_count as _get_citation_count_impl,
    validate_doi as _validate_doi_impl,
)
from .research import (
    extract_claims as _extract_claims_impl,
    score_hypothesis as _score_hypothesis_impl,
    branch_search as _branch_search_impl,
)
from .writing import (
    write_section as _write_section_impl,
    get_citations as _get_citations_impl,
    compile_paper as _compile_paper_impl,
)
from .pipeline import (
    request_approval as _request_approval_impl,
    get_rules as _get_rules_impl,
    track_cost as _track_cost_impl,
    embed_text as _embed_text_impl,
    score_branches as _score_branches_impl,
    prune_hypotheses as _prune_hypotheses_impl,
    save_phase_output as _save_phase_output_impl,
)

if TYPE_CHECKING:
    from ara.db import ARADB


@dataclass
class ARATools:
    """Tool executor for ARA research operations."""
    workspace: Path
    db: ARADB | None = None
    session_id: int | None = None
    approval_gates: bool = True

    def dispatch(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool by name. Returns observation string."""
        handler = TOOL_DISPATCH.get(name)
        if handler is None:
            return f"Unknown tool: {name}"
        return handler(self, args)


def _store_search_results(tools: ARATools, results_json: str, source_name: str) -> str:
    """Parse search JSON and insert papers into DB. Returns the original JSON with paper_ids added."""
    if not tools.db or not tools.session_id:
        return results_json  # No DB, just pass through
    try:
        papers = json.loads(results_json)
        if not isinstance(papers, list):
            return results_json
        stored = 0
        for p in papers:
            if "error" in p:
                continue
            doi = p.get("doi")
            title = p.get("title", "").strip()
            if not title:
                continue
            # Dedup by DOI within session
            if doi:
                existing = tools.db.conn.execute(
                    "SELECT paper_id, source FROM papers WHERE session_id = ? AND doi = ?",
                    (tools.session_id, doi),
                ).fetchone()
                if existing:
                    # Merge source
                    old_sources = json.loads(existing["source"])
                    if source_name not in old_sources:
                        old_sources.append(source_name)
                        tools.db.conn.execute(
                            "UPDATE papers SET source = ? WHERE paper_id = ?",
                            (json.dumps(old_sources), existing["paper_id"]),
                        )
                        tools.db.conn.commit()
                    p["paper_id"] = existing["paper_id"]
                    p["deduplicated"] = True
                    continue
            # Dedup by title (fuzzy — exact match for now)
            existing_title = tools.db.conn.execute(
                "SELECT paper_id FROM papers WHERE session_id = ? AND title = ?",
                (tools.session_id, title),
            ).fetchone()
            if existing_title:
                p["paper_id"] = existing_title["paper_id"]
                p["deduplicated"] = True
                continue
            paper_id = tools.db.insert_paper(
                session_id=tools.session_id,
                title=title,
                doi=doi,
                authors=p.get("authors"),
                abstract=p.get("abstract", ""),
                source=[source_name],
                publication_year=p.get("year"),
                citation_count=p.get("citation_count", 0),
                url=p.get("url", ""),
            )
            p["paper_id"] = paper_id
            stored += 1
        if stored > 0:
            _log.info("Stored %d new papers from %s (session %s)", stored, source_name, tools.session_id)
        return json.dumps(papers)
    except Exception as exc:
        _log.warning("Failed to store search results from %s: %s", source_name, exc)
        return results_json  # Return original on failure


def _think(tools: ARATools, args: dict[str, Any]) -> str:
    return f"Thought noted: {args.get('note', '')}"


def _subtask(tools: ARATools, args: dict[str, Any]) -> str:
    """Fallback — engine.py intercepts subtask before dispatch reaches here.

    If this runs, the engine isn't handling subtask delegation properly.
    """
    return json.dumps({
        "status": "error",
        "message": "subtask should be handled by the engine, not tool dispatch. "
                   "Check that config.recursive=True and engine._apply_tool_call intercepts 'subtask'.",
    })


def _execute(tools: ARATools, args: dict[str, Any]) -> str:
    """Fallback — engine.py intercepts execute before dispatch reaches here.

    If this runs, the engine isn't handling execute delegation properly.
    """
    return json.dumps({
        "status": "error",
        "message": "execute should be handled by the engine, not tool dispatch. "
                   "Check that engine._apply_tool_call intercepts 'execute'.",
    })


def _search_semantic_scholar(tools: ARATools, args: dict[str, Any]) -> str:
    result = _search_semantic_scholar_impl(args.get("query", ""), args.get("limit", 10))
    return _store_search_results(tools, result, "semantic_scholar")


def _search_arxiv(tools: ARATools, args: dict[str, Any]) -> str:
    result = _search_arxiv_impl(args.get("query", ""), args.get("limit", 10))
    return _store_search_results(tools, result, "arxiv")


def _search_crossref(tools: ARATools, args: dict[str, Any]) -> str:
    result = _search_crossref_impl(args.get("query", ""), args.get("limit", 10))
    return _store_search_results(tools, result, "crossref")


def _search_openalex(tools: ARATools, args: dict[str, Any]) -> str:
    result = _search_openalex_impl(args.get("query", ""), args.get("limit", 10))
    return _store_search_results(tools, result, "openalex")


def _search_pubmed(tools: ARATools, args: dict[str, Any]) -> str:
    result = _search_pubmed_impl(args.get("query", ""), args.get("limit", 10))
    return _store_search_results(tools, result, "pubmed")


def _search_core(tools: ARATools, args: dict[str, Any]) -> str:
    result = _search_core_impl(args.get("query", ""), args.get("limit", 10))
    return _store_search_results(tools, result, "core")


def _search_dblp(tools: ARATools, args: dict[str, Any]) -> str:
    result = _search_dblp_impl(args.get("query", ""), args.get("limit", 10))
    return _store_search_results(tools, result, "dblp")


def _search_europe_pmc(tools: ARATools, args: dict[str, Any]) -> str:
    result = _search_europe_pmc_impl(args.get("query", ""), args.get("limit", 10))
    return _store_search_results(tools, result, "europe_pmc")


def _search_base(tools: ARATools, args: dict[str, Any]) -> str:
    result = _search_base_impl(args.get("query", ""), args.get("limit", 10))
    return _store_search_results(tools, result, "base")


def _fetch_fulltext(tools: ARATools, args: dict[str, Any]) -> str:
    doi = args.get("doi", "")
    return _fetch_fulltext_impl(doi)


def _read_paper(tools: ARATools, args: dict[str, Any]) -> str:
    paper_id = args.get("paper_id")
    if not tools.db:
        return json.dumps({"error": "Database connection required"})
    return _read_paper_impl(paper_id, tools.db)


def _search_similar(tools: ARATools, args: dict[str, Any]) -> str:
    query_text = args.get("query_text", "")
    limit = args.get("limit", 5)
    if not tools.db or not tools.session_id:
        return json.dumps({"error": "Database connection and session_id required"})
    return _search_similar_impl(query_text, tools.session_id, tools.db, limit)


def _check_retraction(tools: ARATools, args: dict[str, Any]) -> str:
    doi = args.get("doi", "")
    return _check_retraction_impl(doi)


def _get_citation_count(tools: ARATools, args: dict[str, Any]) -> str:
    doi = args.get("doi", "")
    return _get_citation_count_impl(doi)


def _validate_doi(tools: ARATools, args: dict[str, Any]) -> str:
    doi = args.get("doi", "")
    return _validate_doi_impl(doi)


def _extract_claims(tools: ARATools, args: dict[str, Any]) -> str:
    paper_id = args.get("paper_id")
    if not tools.db:
        return json.dumps({"error": "Database connection required"})
    return _extract_claims_impl(paper_id, tools.db)


def _score_hypothesis(tools: ARATools, args: dict[str, Any]) -> str:
    hypothesis_text = args.get("hypothesis_text", "")
    dimensions = args.get("dimensions")
    return _score_hypothesis_impl(hypothesis_text, dimensions)


def _branch_search(tools: ARATools, args: dict[str, Any]) -> str:
    hypothesis_text = args.get("hypothesis_text", "")
    branch_type = args.get("branch_type", "lateral")
    query = args.get("query", "")
    round_num = args.get("round_num", 1)
    parent_branch_id = args.get("parent_branch_id")
    return _branch_search_impl(hypothesis_text, branch_type, query, round_num,
                               parent_branch_id, tools.session_id, tools.db)


def _write_section(tools: ARATools, args: dict[str, Any]) -> str:
    section_name = args.get("section_name", "")
    content = args.get("content", "")
    if not tools.db or not tools.session_id:
        return json.dumps({"error": "Database connection and session_id required"})
    return _write_section_impl(section_name, content, tools.session_id, tools.db)


def _get_citations(tools: ARATools, args: dict[str, Any]) -> str:
    if not tools.db or not tools.session_id:
        return json.dumps({"error": "Database connection and session_id required"})
    return _get_citations_impl(tools.session_id, tools.db)


def _compile_paper(tools: ARATools, args: dict[str, Any]) -> str:
    if not tools.db or not tools.session_id:
        return json.dumps({"error": "Database connection and session_id required"})
    return _compile_paper_impl(tools.session_id, tools.db, tools.workspace)


def _save_phase_output(tools: ARATools, args: dict[str, Any]) -> str:
    phase = args.get("phase", "")
    content = args.get("content", "")
    return _save_phase_output_impl(phase, content, tools.workspace)


def _request_approval(tools: ARATools, args: dict[str, Any]) -> str:
    phase = args.get("phase", "")
    summary = args.get("summary", "")
    data = args.get("data")
    if not tools.approval_gates:
        # Auto-approve: log it but don't block for user input
        return json.dumps({"phase": phase, "decision": "approved", "auto": True})
    return _request_approval_impl(phase, summary, data, tools.session_id, tools.db, tools.workspace)


def _get_rules(tools: ARATools, args: dict[str, Any]) -> str:
    if not tools.db or not tools.session_id:
        return json.dumps({"error": "Database connection and session_id required"})
    return _get_rules_impl(tools.session_id, tools.db)


def _track_cost(tools: ARATools, args: dict[str, Any]) -> str:
    if not tools.db or not tools.session_id:
        return json.dumps({"error": "Database connection and session_id required"})
    return _track_cost_impl(tools.session_id, tools.db)


def _embed_text(tools: ARATools, args: dict[str, Any]) -> str:
    text = args.get("text", "")
    return _embed_text_impl(text)


def _score_branches(tools: ARATools, args: dict[str, Any]) -> str:
    branches = args.get("branches", [])
    if not tools.db or not tools.session_id:
        return json.dumps({"error": "Database connection and session_id required"})
    return _score_branches_impl(branches, tools.session_id, tools.db)


def _prune_hypotheses(tools: ARATools, args: dict[str, Any]) -> str:
    keep_top_n = args.get("keep_top_n", 3)
    if not tools.db or not tools.session_id:
        return json.dumps({"error": "Database connection and session_id required"})
    return _prune_hypotheses_impl(tools.session_id, keep_top_n, tools.db)


TOOL_DISPATCH: dict[str, Any] = {
    "think": _think,
    "subtask": _subtask,
    "execute": _execute,
    "search_semantic_scholar": _search_semantic_scholar,
    "search_arxiv": _search_arxiv,
    "search_crossref": _search_crossref,
    "search_openalex": _search_openalex,
    "search_pubmed": _search_pubmed,
    "search_core": _search_core,
    "search_dblp": _search_dblp,
    "search_europe_pmc": _search_europe_pmc,
    "search_base": _search_base,
    "fetch_fulltext": _fetch_fulltext,
    "read_paper": _read_paper,
    "search_similar": _search_similar,
    "check_retraction": _check_retraction,
    "get_citation_count": _get_citation_count,
    "validate_doi": _validate_doi,
    "extract_claims": _extract_claims,
    "score_hypothesis": _score_hypothesis,
    "branch_search": _branch_search,
    "score_branches": _score_branches,
    "prune_hypotheses": _prune_hypotheses,
    "save_phase_output": _save_phase_output,
    "write_section": _write_section,
    "get_citations": _get_citations,
    "request_approval": _request_approval,
    "get_rules": _get_rules,
    "track_cost": _track_cost,
    "embed_text": _embed_text,
    "compile_paper": _compile_paper,
}
