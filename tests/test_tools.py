# Location: tests/test_tools.py
# Purpose: Tests for tool definitions and dispatch
# Functions: test_tool_defs, test_dispatch
# Calls: ara.tools
# Imports: pytest, json

import json
from ara.tools import ARATools, TOOL_DISPATCH
from ara.tools.defs import TOOL_DEFINITIONS


def test_tool_definitions_structure():
    assert len(TOOL_DEFINITIONS) >= 20
    for td in TOOL_DEFINITIONS:
        assert "name" in td
        assert "description" in td
        assert "parameters" in td
        assert isinstance(td["name"], str)
        assert len(td["name"]) > 0


def test_all_tools_have_handlers():
    for td in TOOL_DEFINITIONS:
        name = td["name"]
        assert name in TOOL_DISPATCH, f"Tool '{name}' has no handler in TOOL_DISPATCH"


def test_tool_names_unique():
    names = [td["name"] for td in TOOL_DEFINITIONS]
    assert len(names) == len(set(names)), "Duplicate tool names found"


def test_ara_tools_get_definitions():
    tools = ARATools()
    # At depth 0 (manager), only delegation + pipeline tools
    defs = tools.get_definitions(include_subtask=True, depth=0)
    names = [d["name"] for d in defs]
    assert "subtask" in names
    assert "execute" in names
    assert "search_semantic_scholar" not in names  # hidden at depth 0

    # At depth 1+ (worker), all tools exposed
    defs_worker = tools.get_definitions(include_subtask=True, depth=1)
    names_worker = [d["name"] for d in defs_worker]
    assert "search_semantic_scholar" in names_worker
    assert "search_all" in names_worker

    defs_no_sub = tools.get_definitions(include_subtask=False)
    names_no_sub = [d["name"] for d in defs_no_sub]
    assert "subtask" not in names_no_sub


def test_dispatch_unknown_tool():
    tools = ARATools()
    result = tools.dispatch("nonexistent_tool", {})
    parsed = json.loads(result)
    assert "error" in parsed


def test_search_tools_count():
    search_tools = [td for td in TOOL_DEFINITIONS if td["name"].startswith("search_")]
    assert len(search_tools) == 11  # 9 APIs + search_similar + search_all


def test_phase_tool_filtering():
    """Phase-specific tool filtering restricts tools per phase."""
    tools = ARATools()

    # Scout phase only gets search tools + pipeline tools
    scout_defs = tools.get_definitions(include_subtask=False, depth=1, phase="scout")
    scout_names = {d["name"] for d in scout_defs}
    assert "search_all" in scout_names
    assert "search_semantic_scholar" in scout_names
    assert "embed_text" not in scout_names  # Embedding removed from scout phase
    assert "read_paper" not in scout_names
    assert "extract_claims" not in scout_names
    assert "write_section" not in scout_names

    # Writer phase gets writing tools, not search tools
    writer_defs = tools.get_definitions(include_subtask=False, depth=1, phase="writer")
    writer_names = {d["name"] for d in writer_defs}
    assert "write_section" in writer_names
    assert "get_citations" in writer_names
    assert "read_paper" in writer_names
    assert "search_all" not in writer_names
    assert "extract_claims" not in writer_names

    # Verifier phase gets verification tools
    verifier_defs = tools.get_definitions(include_subtask=False, depth=1, phase="verifier")
    verifier_names = {d["name"] for d in verifier_defs}
    assert "check_retraction" in verifier_names
    assert "validate_doi" in verifier_names
    assert "search_all" not in verifier_names

    # Unknown phase gets all tools (no filtering)
    all_defs = tools.get_definitions(include_subtask=False, depth=1, phase="unknown_phase")
    assert len(all_defs) == len(TOOL_DEFINITIONS)
