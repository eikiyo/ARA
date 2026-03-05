# Location: tests/test_e2e.py
# Purpose: End-to-end integration test for ARA pipeline (Task 14)
# Functions: test_* functions covering DB, tools, engine, runtime
# Calls: ara.db, ara.tools, ara.engine, ara.runtime, ara.prompts
# Imports: pytest, tempfile, pathlib, json

"""End-to-end integration tests for ARA research pipeline.

Uses ScriptedModel to simulate LLM responses. Verifies:
- DB creation and CRUD
- Tool dispatch for all 27 tools
- Engine recursive loop with tool calls
- Runtime session bootstrap and state persistence
- Prompt registry with all 10 phases
- Approval gate flow
"""

import json
import tempfile
from pathlib import Path

import pytest

from ara.config import ARAConfig
from ara.db import ARADB
from ara.engine import RLMEngine
from ara.gates import write_gate_file
from ara.model import ModelTurn, ScriptedModel, ToolCall
from ara.prompts import PHASE_PROMPTS, build_system_prompt, build_phase_prompt
from ara.runtime import SessionRuntime
from ara.tools import ARATools
from ara.tools.defs import TOOL_DEFINITIONS, to_anthropic_tools, to_openai_tools


@pytest.fixture
def tmp_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def db(tmp_workspace):
    db = ARADB(db_path=tmp_workspace / ".ara" / "session.db")
    yield db
    db.close()


@pytest.fixture
def session_id(db):
    return db.create_session("Test research topic", description="Integration test")


@pytest.fixture
def tools(tmp_workspace, db, session_id):
    return ARATools(workspace=tmp_workspace, db=db, session_id=session_id)


# ============================================================================
# DATABASE TESTS
# ============================================================================

class TestDatabase:
    def test_create_session(self, db):
        sid = db.create_session("Neural scaling laws")
        assert sid >= 1
        session = db.get_session(sid)
        assert session["topic"] == "Neural scaling laws"
        assert session["status"] == "active"
        assert session["current_phase"] == "scout"

    def test_paper_crud(self, db, session_id):
        pid = db.insert_paper(
            session_id, "Test Paper", doi="10.1234/test",
            authors=["Alice", "Bob"], abstract="Abstract text",
            source=["arxiv"], publication_year=2024, citation_count=42,
        )
        paper = db.get_paper(pid)
        assert paper["title"] == "Test Paper"
        assert paper["citation_count"] == 42

        papers = db.get_papers(session_id)
        assert len(papers) == 1

        db.update_paper(pid, confidence_score=0.9)
        updated = db.get_paper(pid)
        assert updated["confidence_score"] == 0.9

    def test_claim_crud(self, db, session_id):
        pid = db.insert_paper(session_id, "Source Paper")
        cid = db.insert_claim(session_id, pid, "Test claim")
        claim = db.get_claim(cid)
        assert claim["claim_text"] == "Test claim"
        assert claim["verification_status"] == "unverified"

        db.update_claim(cid, verification_status="verified", confidence_score=0.95)
        updated = db.get_claim(cid)
        assert updated["verification_status"] == "verified"

    def test_hypothesis_crud(self, db, session_id):
        hid = db.insert_hypothesis(session_id, "Scaling is universal")
        hyp = db.get_hypothesis(hid)
        assert hyp["hypothesis_text"] == "Scaling is universal"

        db.insert_hypothesis_score(hid, "novelty", 0.8, "critic")
        db.update_hypothesis(hid, overall_score=0.75)
        updated = db.get_hypothesis(hid)
        assert updated["overall_score"] == 0.75

    def test_branch_crud(self, db, session_id):
        hid = db.insert_hypothesis(session_id, "Test hyp")
        bid = db.insert_branch(session_id, hid, "biology", "analogical", finding="Similar")
        branches = db.get_branches(session_id)
        assert len(branches) == 1
        assert branches[0]["target_domain"] == "biology"

    def test_gate_lifecycle(self, db, session_id):
        gid = db.insert_gate(session_id, "scout", {"papers": 50})
        pending = db.get_pending_gate(session_id)
        assert pending is not None
        assert pending["phase"] == "scout"

        db.resolve_gate(gid, "approved", "approve")
        pending_after = db.get_pending_gate(session_id)
        assert pending_after is None

    def test_cost_tracking(self, db, session_id):
        db.log_cost(session_id, "claude-sonnet-4-6", 1000, 500, 0.05, "search")
        db.log_cost(session_id, "claude-sonnet-4-6", 2000, 1000, 0.10, "write")
        total = db.get_total_cost(session_id)
        assert abs(total - 0.15) < 0.001

        session = db.get_session(session_id)
        assert abs(session["budget_spent"] - 0.15) < 0.001

    def test_rules(self, db, session_id):
        db.insert_rule(session_id, "Exclude pre-2020 papers")
        db.insert_rule(session_id, "Focus on CS papers", rule_type="include")
        rules = db.get_active_rules(session_id)
        assert len(rules) == 2


# ============================================================================
# TOOL DISPATCH TESTS
# ============================================================================

class TestToolDispatch:
    def test_think(self, tools):
        result = tools.dispatch("think", {"note": "test"})
        assert "Thought noted" in result

    def test_read_paper(self, tools, db, session_id):
        pid = db.insert_paper(session_id, "Readable Paper", abstract="Content")
        result = json.loads(tools.dispatch("read_paper", {"paper_id": pid}))
        assert result["title"] == "Readable Paper"

    def test_extract_claims(self, tools, db, session_id):
        pid = db.insert_paper(session_id, "Claims Paper", abstract="We found X")
        result = json.loads(tools.dispatch("extract_claims", {"paper_id": pid}))
        assert result["task"] == "extract_claims"

    def test_score_hypothesis(self, tools):
        result = json.loads(tools.dispatch("score_hypothesis", {"hypothesis_text": "X causes Y"}))
        assert "novelty" in result["dimensions"]

    def test_track_cost(self, tools):
        result = json.loads(tools.dispatch("track_cost", {}))
        assert "budget_cap_usd" in result

    def test_get_rules(self, tools):
        result = json.loads(tools.dispatch("get_rules", {}))
        assert result["total_rules"] == 0

    def test_embed_text(self, tools):
        result = json.loads(tools.dispatch("embed_text", {"text": "test"}))
        assert result["status"] == "stub"

    def test_get_citations(self, tools, db, session_id):
        db.insert_paper(session_id, "Cited Paper", doi="10.1234/cited", authors=["Smith"])
        result = json.loads(tools.dispatch("get_citations", {}))
        assert result["total_papers"] == 1
        assert "Smith" in result["bibliography"]

    def test_unknown_tool(self, tools):
        result = tools.dispatch("nonexistent", {})
        assert "Unknown tool" in result

    def test_all_tools_in_dispatch(self, tools):
        """Every tool in TOOL_DEFINITIONS must be dispatchable."""
        from ara.tools import TOOL_DISPATCH
        for tool_def in TOOL_DEFINITIONS:
            name = tool_def["name"]
            assert name in TOOL_DISPATCH, f"Tool {name} not in TOOL_DISPATCH"


# ============================================================================
# TOOL DEFINITIONS TESTS
# ============================================================================

class TestToolDefinitions:
    def test_tool_count(self):
        assert len(TOOL_DEFINITIONS) == 27

    def test_openai_format(self):
        openai_tools = to_openai_tools()
        assert len(openai_tools) == 27
        for t in openai_tools:
            assert t["type"] == "function"
            assert "name" in t["function"]

    def test_anthropic_format(self):
        anthropic_tools = to_anthropic_tools()
        assert len(anthropic_tools) == 27
        for t in anthropic_tools:
            assert "name" in t
            assert "input_schema" in t

    def test_filter_subtask(self):
        from ara.tools.defs import get_tool_definitions
        filtered = get_tool_definitions(include_subtask=False)
        names = [d["name"] for d in filtered]
        assert "subtask" not in names
        assert "execute" not in names
        assert "think" in names


# ============================================================================
# PROMPT TESTS
# ============================================================================

class TestPrompts:
    def test_phase_count(self):
        assert len(PHASE_PROMPTS) == 10

    def test_all_phases_have_content(self):
        for phase, prompt in PHASE_PROMPTS.items():
            assert len(prompt) > 100, f"Phase {phase} prompt too short"

    def test_build_system_prompt(self):
        prompt = build_system_prompt()
        assert "ARA" in prompt
        assert "subtask" in prompt.lower()

    def test_build_phase_prompt(self):
        prompt = build_phase_prompt("scout")
        assert "Scout" in prompt

    def test_build_phase_prompt_invalid(self):
        with pytest.raises(ValueError):
            build_phase_prompt("nonexistent_phase")


# ============================================================================
# GATES TESTS
# ============================================================================

class TestGates:
    def test_write_gate_file(self, tmp_workspace):
        path = write_gate_file(tmp_workspace, "scout", "Found 50 papers", {"count": 50})
        assert path.exists()
        content = path.read_text()
        assert "Scout" in content
        assert "50 papers" in content


# ============================================================================
# ENGINE TESTS
# ============================================================================

class TestEngine:
    def test_solve_with_think_and_answer(self, tools, tmp_workspace):
        turns = [
            ModelTurn(
                tool_calls=[ToolCall(id="tc1", name="think", arguments={"note": "Planning"})],
                text=None, raw_response={}, input_tokens=100, output_tokens=50,
            ),
            ModelTurn(
                tool_calls=[], text="Final answer here.",
                raw_response={}, input_tokens=200, output_tokens=100,
            ),
        ]
        model = ScriptedModel(scripted_turns=turns)
        cfg = ARAConfig(workspace=tmp_workspace, recursive=False)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        result = engine.solve("Test objective")
        assert result == "Final answer here."

    def test_solve_with_search_tool(self, tools, tmp_workspace):
        turns = [
            ModelTurn(
                tool_calls=[ToolCall(
                    id="tc1", name="search_semantic_scholar",
                    arguments={"query": "neural scaling", "limit": 2},
                )],
                text=None, raw_response={}, input_tokens=100, output_tokens=50,
            ),
            ModelTurn(
                tool_calls=[], text="Search completed.",
                raw_response={}, input_tokens=200, output_tokens=100,
            ),
        ]
        model = ScriptedModel(scripted_turns=turns)
        cfg = ARAConfig(workspace=tmp_workspace, recursive=False)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        result = engine.solve("Search for papers")
        assert result == "Search completed."


# ============================================================================
# RUNTIME TESTS
# ============================================================================

class TestRuntime:
    def test_bootstrap_creates_db(self, tmp_workspace):
        cfg = ARAConfig(workspace=tmp_workspace, recursive=False)
        turns = [
            ModelTurn(tool_calls=[], text="Ready.", raw_response={}, input_tokens=10, output_tokens=5),
        ]
        model = ScriptedModel(scripted_turns=turns)
        tools = ARATools(workspace=tmp_workspace)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        runtime = SessionRuntime.bootstrap(engine=engine, config=cfg)

        assert runtime.db is not None
        assert (tmp_workspace / ".ara" / "session.db").exists()
        assert engine.tools.db is not None

    def test_solve_persists_state(self, tmp_workspace):
        cfg = ARAConfig(workspace=tmp_workspace, recursive=False)
        turns = [
            ModelTurn(tool_calls=[], text="Answer 1.", raw_response={}, input_tokens=10, output_tokens=5),
        ]
        model = ScriptedModel(scripted_turns=turns)
        tools = ARATools(workspace=tmp_workspace)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        runtime = SessionRuntime.bootstrap(engine=engine, config=cfg)
        result = runtime.solve("First question")
        assert result == "Answer 1."

        # Verify state persisted
        state_path = tmp_workspace / ".ara" / "state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert "turn_history" in state
        assert len(state["turn_history"]) == 1

    def test_session_resume(self, tmp_workspace):
        cfg = ARAConfig(workspace=tmp_workspace, recursive=False)

        # First session
        turns1 = [
            ModelTurn(tool_calls=[], text="First.", raw_response={}, input_tokens=10, output_tokens=5),
        ]
        model1 = ScriptedModel(scripted_turns=turns1)
        tools1 = ARATools(workspace=tmp_workspace)
        engine1 = RLMEngine(model=model1, tools=tools1, config=cfg)
        runtime1 = SessionRuntime.bootstrap(engine=engine1, config=cfg)
        runtime1.solve("Q1")
        sid1 = runtime1.session_id

        # Resume
        turns2 = [
            ModelTurn(tool_calls=[], text="Second.", raw_response={}, input_tokens=10, output_tokens=5),
        ]
        model2 = ScriptedModel(scripted_turns=turns2)
        tools2 = ARATools(workspace=tmp_workspace)
        engine2 = RLMEngine(model=model2, tools=tools2, config=cfg)
        runtime2 = SessionRuntime.bootstrap(engine=engine2, config=cfg, resume=True)

        assert runtime2.session_id == sid1
        assert len(runtime2.turn_history) == 1
