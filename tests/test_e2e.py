# Location: tests/test_e2e.py
# Purpose: End-to-end integration test for ARA pipeline
# Functions: test_* functions covering DB, tools, engine, runtime, search storage, compile, embeddings
# Calls: ara.db, ara.tools, ara.engine, ara.runtime, ara.prompts
# Imports: pytest, tempfile, pathlib, json, unittest.mock

"""End-to-end integration tests for ARA research pipeline.

Uses ScriptedModel to simulate LLM responses. Verifies:
- DB creation and CRUD
- Tool dispatch for all tools
- Engine recursive loop with tool calls
- Runtime session bootstrap and state persistence
- Prompt registry with all phases
- Approval gate flow
- Search result auto-storage with dedup
- Paper compilation pipeline
- Embedding via Ollama
- Phase progress detection
- S2 rate limiter
"""

import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ara.config import ARAConfig
from ara.db import ARADB
from ara.engine import RLMEngine
from ara.gates import write_gate_file
from ara.model import ModelTurn, ScriptedModel, ToolCall
from ara.prompts import PHASE_PROMPTS, build_system_prompt, build_phase_prompt
from ara.runtime import SessionRuntime
from ara.tools import ARATools, _store_search_results
from ara.tools.defs import TOOL_DEFINITIONS, to_anthropic_tools, to_openai_tools


@pytest.fixture
def tmp_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def db(tmp_workspace):
    db = ARADB(db_path=tmp_workspace / "ara_data" / "session.db")
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

    def test_embed_text_ollama_down(self, tools):
        """When Ollama isn't running, embed_text returns helpful error."""
        from ara.tools.pipeline import embed_text
        import httpx as _httpx

        with patch("ara.tools.pipeline.httpx.post", side_effect=_httpx.ConnectError("refused")):
            result = json.loads(embed_text("test text"))
            assert result["status"] == "error"
            assert "ollama" in result["message"].lower()

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

    def test_subtask_fallback_is_error(self, tools):
        """subtask dispatch stub should indicate engine should handle it."""
        result = json.loads(tools.dispatch("subtask", {"objective": "test"}))
        assert result["status"] == "error"
        assert "engine" in result["message"].lower()

    def test_execute_fallback_is_error(self, tools):
        """execute dispatch stub should indicate engine should handle it."""
        result = json.loads(tools.dispatch("execute", {"objective": "test"}))
        assert result["status"] == "error"
        assert "engine" in result["message"].lower()

    def test_save_phase_output(self, tools, tmp_workspace):
        result = json.loads(tools.dispatch("save_phase_output", {
            "phase": "scout",
            "content": "# Scout Results\nFound 50 papers.",
        }))
        assert result["status"] == "saved"
        saved_file = tmp_workspace / "ara_data" / "phases" / "scout.md"
        assert saved_file.exists()
        assert "50 papers" in saved_file.read_text()

    def test_write_section(self, tools, tmp_workspace):
        result = json.loads(tools.dispatch("write_section", {
            "section_name": "introduction",
            "content": "# Introduction\nThis paper explores...",
        }))
        assert result["status"] == "saved"
        assert result["section"] == "introduction"

    def test_compile_paper(self, tools, tmp_workspace):
        """compile_paper assembles sections into paper.md + index.html."""
        # Write some sections in the workspace
        sections_dir = tmp_workspace / "ara_data" / "output" / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)
        (sections_dir / "abstract.md").write_text("# Abstract\nThis is the abstract.")
        (sections_dir / "introduction.md").write_text("# Introduction\nBackground info.")
        (sections_dir / "methods.md").write_text("# Methods\nWe used method X.")

        result = json.loads(tools.dispatch("compile_paper", {}))
        assert result["status"] == "compiled"
        assert result["sections_compiled"] == 3


# ============================================================================
# SEARCH RESULT STORAGE TESTS
# ============================================================================

class TestSearchStorage:
    def test_store_new_papers(self, tools, db, session_id):
        """Search results get stored in DB."""
        fake_results = json.dumps([
            {
                "title": "Paper Alpha",
                "doi": "10.1000/alpha",
                "authors": ["Alice"],
                "abstract": "About alpha.",
                "year": 2024,
                "citation_count": 10,
                "url": "https://example.com/alpha",
            },
            {
                "title": "Paper Beta",
                "doi": "10.1000/beta",
                "authors": ["Bob"],
                "abstract": "About beta.",
                "year": 2023,
                "citation_count": 5,
                "url": "https://example.com/beta",
            },
        ])
        result = _store_search_results(tools, fake_results, "test_source")
        papers = json.loads(result)
        assert len(papers) == 2
        assert "paper_id" in papers[0]
        assert "paper_id" in papers[1]

        # Verify in DB
        db_papers = db.get_papers(session_id)
        assert len(db_papers) == 2

    def test_dedup_by_doi(self, tools, db, session_id):
        """Duplicate DOIs within a session get merged, not duplicated."""
        fake1 = json.dumps([{
            "title": "Paper Alpha",
            "doi": "10.1000/alpha",
            "authors": ["Alice"],
            "abstract": "About alpha.",
            "year": 2024,
            "citation_count": 10,
            "url": "",
        }])
        fake2 = json.dumps([{
            "title": "Paper Alpha (different source)",
            "doi": "10.1000/alpha",
            "authors": ["Alice"],
            "abstract": "About alpha.",
            "year": 2024,
            "citation_count": 10,
            "url": "",
        }])

        _store_search_results(tools, fake1, "arxiv")
        result2 = _store_search_results(tools, fake2, "semantic_scholar")
        papers2 = json.loads(result2)

        assert papers2[0].get("deduplicated") is True
        db_papers = db.get_papers(session_id)
        assert len(db_papers) == 1  # Only 1 paper, not 2

    def test_dedup_by_title(self, tools, db, session_id):
        """Papers without DOI get deduped by exact title match."""
        fake1 = json.dumps([{
            "title": "Exact Same Title",
            "doi": None,
            "authors": ["Alice"],
            "abstract": "Content.",
            "year": 2024,
            "citation_count": 0,
            "url": "",
        }])
        fake2 = json.dumps([{
            "title": "Exact Same Title",
            "doi": None,
            "authors": ["Bob"],
            "abstract": "Different content.",
            "year": 2024,
            "citation_count": 0,
            "url": "",
        }])

        _store_search_results(tools, fake1, "arxiv")
        result2 = _store_search_results(tools, fake2, "dblp")
        papers2 = json.loads(result2)

        assert papers2[0].get("deduplicated") is True
        db_papers = db.get_papers(session_id)
        assert len(db_papers) == 1

    def test_source_merging(self, tools, db, session_id):
        """When a paper is found in multiple sources, sources get merged."""
        fake1 = json.dumps([{
            "title": "Multi-source Paper",
            "doi": "10.1000/multi",
            "authors": ["Alice"],
            "abstract": "Content.",
            "year": 2024,
            "citation_count": 5,
            "url": "",
        }])
        _store_search_results(tools, fake1, "arxiv")
        _store_search_results(tools, fake1, "crossref")

        db_papers = db.get_papers(session_id)
        assert len(db_papers) == 1
        sources = json.loads(db_papers[0]["source"])
        assert "arxiv" in sources
        assert "crossref" in sources

    def test_error_results_skipped(self, tools, db, session_id):
        """Error entries in search results are skipped."""
        fake = json.dumps([
            {"error": "API rate limited"},
            {"title": "Good Paper", "doi": "10.1000/good", "authors": ["Alice"],
             "abstract": ".", "year": 2024, "citation_count": 0, "url": ""},
        ])
        _store_search_results(tools, fake, "test")
        db_papers = db.get_papers(session_id)
        assert len(db_papers) == 1

    def test_no_db_passthrough(self, tmp_workspace):
        """Without DB, results pass through unchanged."""
        tools_no_db = ARATools(workspace=tmp_workspace, db=None, session_id=None)
        fake = json.dumps([{"title": "Test", "doi": "10.1/t"}])
        result = _store_search_results(tools_no_db, fake, "test")
        assert result == fake  # Unchanged


# ============================================================================
# COMPILE PAPER TESTS
# ============================================================================

class TestCompilePaper:
    def test_compile_imrad_order(self, tmp_workspace):
        """Sections are compiled in IMRaD order."""
        from ara.tools.writing import compile_paper

        sections_dir = tmp_workspace / "ara_data" / "output" / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)

        # Write sections out of order
        (sections_dir / "methods.md").write_text("# Methods\nMethod content.")
        (sections_dir / "abstract.md").write_text("# Abstract\nAbstract content.")
        (sections_dir / "results.md").write_text("# Results\nResult content.")
        (sections_dir / "introduction.md").write_text("# Introduction\nIntro content.")

        db = ARADB(db_path=tmp_workspace / "ara_data" / "session.db")
        sid = db.create_session("Test")

        result = json.loads(compile_paper(sid, db, tmp_workspace))
        assert result["status"] == "compiled"
        assert result["sections_compiled"] == 4

        # Verify order in paper.md
        paper_path = tmp_workspace / "ara_data" / "output" / "paper.md"
        paper_text = paper_path.read_text()
        abs_pos = paper_text.index("Abstract")
        intro_pos = paper_text.index("Introduction")
        methods_pos = paper_text.index("Methods")
        results_pos = paper_text.index("Results")
        assert abs_pos < intro_pos < methods_pos < results_pos

        db.close()

    def test_compile_generates_html(self, tmp_workspace):
        """compile_paper generates index.html."""
        from ara.tools.writing import compile_paper

        sections_dir = tmp_workspace / "ara_data" / "output" / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)
        (sections_dir / "abstract.md").write_text("# Abstract\nTest abstract.")

        db = ARADB(db_path=tmp_workspace / "ara_data" / "session.db")
        sid = db.create_session("Test")

        result = json.loads(compile_paper(sid, db, tmp_workspace))
        html_path = tmp_workspace / "ara_data" / "output" / "index.html"
        assert html_path.exists()
        html = html_path.read_text()
        assert "<!DOCTYPE html>" in html
        assert "ARA" in html
        assert "Abstract" in html

        db.close()

    def test_compile_no_sections(self, tmp_workspace):
        """compile_paper returns error when no sections exist."""
        from ara.tools.writing import compile_paper

        db = ARADB(db_path=tmp_workspace / "ara_data" / "session.db")
        sid = db.create_session("Test")

        result = json.loads(compile_paper(sid, db, tmp_workspace))
        assert "error" in result

        db.close()


# ============================================================================
# EMBEDDING TESTS
# ============================================================================

class TestEmbedding:
    def test_embed_text_success(self):
        """embed_text returns vector when Ollama responds."""
        from ara.tools.pipeline import embed_text

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embeddings": [[0.1, 0.2, 0.3, 0.4, 0.5]],
        }

        with patch("ara.tools.pipeline.httpx") as mock_httpx:
            mock_httpx.post.return_value = mock_response
            mock_httpx.ConnectError = Exception
            result = json.loads(embed_text("test embedding text"))

        assert result["status"] == "ok"
        assert result["dimensions"] == 5
        assert len(result["embedding"]) == 5
        assert result["model"] == "nomic-embed-text"

    def test_embed_text_fallback_model(self):
        """embed_text tries next model when first fails."""
        from ara.tools.pipeline import embed_text

        fail_response = MagicMock()
        fail_response.status_code = 404

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = {"embeddings": [[0.1, 0.2, 0.3]]}

        with patch("ara.tools.pipeline.httpx") as mock_httpx:
            mock_httpx.post.side_effect = [fail_response, ok_response]
            mock_httpx.ConnectError = Exception
            result = json.loads(embed_text("test"))

        assert result["status"] == "ok"
        assert result["model"] == "all-minilm"  # Second model in list

    def test_embed_text_ollama_not_running(self):
        """embed_text returns clear error when Ollama is down."""
        from ara.tools.pipeline import embed_text
        import httpx as _httpx

        with patch("ara.tools.pipeline.httpx") as mock_httpx:
            mock_httpx.ConnectError = _httpx.ConnectError
            mock_httpx.post.side_effect = _httpx.ConnectError("refused")
            result = json.loads(embed_text("test"))

        assert result["status"] == "error"
        assert "ollama" in result["message"].lower()


# ============================================================================
# S2 RATE LIMITER TESTS
# ============================================================================

class TestS2RateLimiter:
    def test_throttle_enforces_interval(self):
        """_s2_throttle enforces >= 1 second between calls."""
        from ara.tools.search import _s2_throttle, _s2_lock, _S2_API_KEY
        import ara.tools.search as search_mod

        # Reset the last call time
        search_mod._s2_last_call = 0.0

        t0 = time.monotonic()
        _s2_throttle()
        _s2_throttle()
        elapsed = time.monotonic() - t0

        # Second call should wait ~1 second
        assert elapsed >= 1.0

    def test_s2_api_key_configured(self):
        """S2 API key is hardcoded and available."""
        from ara.tools.search import _S2_API_KEY, get_s2_headers

        assert _S2_API_KEY is not None
        assert len(_S2_API_KEY) > 10
        headers = get_s2_headers()
        assert "x-api-key" in headers

    def test_throttle_thread_safe(self):
        """_s2_throttle works correctly under concurrent access."""
        from ara.tools.search import _s2_throttle
        import ara.tools.search as search_mod

        search_mod._s2_last_call = 0.0
        call_times: list[float] = []
        lock = threading.Lock()

        def worker():
            _s2_throttle()
            with lock:
                call_times.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(3)]
        t0 = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 3 calls should take at least 2 seconds (each waits ~1s)
        total = time.monotonic() - t0
        assert total >= 2.0


# ============================================================================
# PHASE PROGRESS TESTS
# ============================================================================

class TestPhaseProgress:
    def test_phase_names_complete(self):
        """All 8 pipeline phases are covered by _PHASE_NAMES."""
        from ara.tui import _PHASE_NAMES, _TOTAL_PHASES

        assert _TOTAL_PHASES == 8
        # Check all phase numbers 1-8 are represented
        nums = set()
        for key, (num, display) in _PHASE_NAMES.items():
            if isinstance(num, int):
                nums.add(num)
        assert nums == {1, 2, 3, 4, 5, 6, 7, 8}

    def test_phase_detection_keywords(self):
        """Phase names match expected keywords from subtask objectives."""
        from ara.tui import _PHASE_NAMES

        # These keywords should match subtask labels from the manager prompt
        assert "scout" in _PHASE_NAMES
        assert "verifier" in _PHASE_NAMES
        assert "hypothesis" in _PHASE_NAMES
        assert "brancher" in _PHASE_NAMES
        assert "critic" in _PHASE_NAMES
        assert "writer" in _PHASE_NAMES


# ============================================================================
# TOOL DEFINITIONS TESTS
# ============================================================================

class TestToolDefinitions:
    def test_tool_count(self):
        assert len(TOOL_DEFINITIONS) == 31

    def test_openai_format(self):
        openai_tools = to_openai_tools()
        assert len(openai_tools) == 31
        for t in openai_tools:
            assert t["type"] == "function"
            assert "name" in t["function"]

    def test_anthropic_format(self):
        anthropic_tools = to_anthropic_tools()
        assert len(anthropic_tools) == 31
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

    def test_all_tools_have_required_fields(self):
        """Every tool definition has name, description, and parameters."""
        for tool in TOOL_DEFINITIONS:
            assert "name" in tool, f"Tool missing name: {tool}"
            assert "description" in tool, f"Tool {tool['name']} missing description"
            assert "parameters" in tool, f"Tool {tool['name']} missing parameters"
            assert tool["parameters"]["type"] == "object"

    def test_no_duplicate_tool_names(self):
        """Tool names must be unique."""
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert len(names) == len(set(names)), f"Duplicate tools: {[n for n in names if names.count(n) > 1]}"


# ============================================================================
# PROMPT TESTS
# ============================================================================

class TestPrompts:
    def test_phase_count(self):
        assert len(PHASE_PROMPTS) == 12

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

    def test_writer_prompt_mentions_compile(self):
        """Writer prompt instructs calling compile_paper."""
        prompt = build_phase_prompt("writer")
        assert "compile_paper" in prompt

    def test_no_phase_prompts_call_request_approval(self):
        """Phase prompts should NOT call request_approval (manager handles it)."""
        # These phases had approval removed in the bugfix
        phases_without_approval = ["scout", "analyst_triage", "analyst_deep_read",
                                    "verifier", "hypothesis", "brancher",
                                    "critic_standard", "critic_showdown", "writer"]
        for phase in phases_without_approval:
            if phase in PHASE_PROMPTS:
                prompt = PHASE_PROMPTS[phase]
                # The prompt should say NOT to call request_approval
                assert "Do NOT call request_approval" in prompt or \
                       "do NOT call request_approval" in prompt or \
                       "request_approval" not in prompt or \
                       "Do not call request_approval" in prompt, \
                    f"Phase {phase} may still call request_approval"


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

    def test_engine_handles_subtask_at_depth(self, tools, tmp_workspace):
        """Engine intercepts subtask and recurses (not dispatched to tools)."""
        turns = [
            # Depth 0: delegate subtask
            ModelTurn(
                tool_calls=[ToolCall(
                    id="tc1", name="subtask",
                    arguments={"objective": "Find papers", "acceptance_criteria": "At least 5 papers"},
                )],
                text=None, raw_response={}, input_tokens=100, output_tokens=50,
            ),
            # Depth 1: child returns answer
            ModelTurn(
                tool_calls=[], text="Found 10 papers about X.",
                raw_response={}, input_tokens=100, output_tokens=50,
            ),
            # Depth 0: final answer
            ModelTurn(
                tool_calls=[], text="Research complete. Found 10 papers.",
                raw_response={}, input_tokens=200, output_tokens=100,
            ),
        ]
        model = ScriptedModel(scripted_turns=turns)
        cfg = ARAConfig(workspace=tmp_workspace, recursive=True, max_depth=3)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        result = engine.solve("Do research")
        assert "10 papers" in result

    def test_engine_nudge_on_phase_transition(self, tools, tmp_workspace):
        """Engine nudges model when it outputs phase-transition text without tool call."""
        turns = [
            # Step 1: Phase transition text (no tool call)
            ModelTurn(
                tool_calls=[], text="Moving to: Analyst phase",
                raw_response={}, input_tokens=100, output_tokens=50,
            ),
            # Step 2: After nudge, model calls a tool
            ModelTurn(
                tool_calls=[ToolCall(id="tc1", name="think", arguments={"note": "OK"})],
                text=None, raw_response={}, input_tokens=100, output_tokens=50,
            ),
            # Step 3: Final answer
            ModelTurn(
                tool_calls=[], text="All phases done.",
                raw_response={}, input_tokens=100, output_tokens=50,
            ),
        ]
        model = ScriptedModel(scripted_turns=turns)
        cfg = ARAConfig(workspace=tmp_workspace, recursive=True, max_steps_per_call=20)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        events: list[str] = []
        result = engine.solve("Run pipeline", on_event=lambda e: events.append(e))
        assert "All phases done." == result
        assert any("nudging" in e for e in events)

    def test_engine_max_depth_respected(self, tools, tmp_workspace):
        """Engine refuses subtask when max depth is reached."""
        turns = [
            ModelTurn(
                tool_calls=[ToolCall(
                    id="tc1", name="subtask",
                    arguments={"objective": "Deep dive"},
                )],
                text=None, raw_response={}, input_tokens=100, output_tokens=50,
            ),
            ModelTurn(
                tool_calls=[], text="Done.",
                raw_response={}, input_tokens=100, output_tokens=50,
            ),
        ]
        model = ScriptedModel(scripted_turns=turns)
        # max_depth=0 means no recursion allowed
        cfg = ARAConfig(workspace=tmp_workspace, recursive=True, max_depth=0)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        result = engine.solve("Test")
        assert result == "Done."

    def test_engine_cancel(self, tools, tmp_workspace):
        """Engine stops when cancel is set during execution."""
        call_count = 0

        def cancel_on_second_call(conversation):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call returns a tool call; then set cancel
                engine.cancel()
                return ModelTurn(
                    tool_calls=[ToolCall(id="tc1", name="think", arguments={"note": "step 1"})],
                    text=None, raw_response={}, input_tokens=100, output_tokens=50,
                )
            return ModelTurn(
                tool_calls=[], text="Should not reach here.",
                raw_response={}, input_tokens=100, output_tokens=50,
            )

        model = ScriptedModel(scripted_turns=[])
        model.complete = cancel_on_second_call
        cfg = ARAConfig(workspace=tmp_workspace, recursive=False)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        result = engine.solve("Test")
        assert "cancelled" in result.lower()


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
        assert (tmp_workspace / "ara_data" / "session.db").exists()
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
        state_path = tmp_workspace / "ara_data" / "state.json"
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


# ============================================================================
# APPROVAL GATE TOGGLE TESTS
# ============================================================================

class TestApprovalGateToggle:
    def test_auto_approve_when_gates_off(self, tmp_workspace, db, session_id):
        """When approval_gates=False, request_approval auto-approves."""
        t = ARATools(workspace=tmp_workspace, db=db, session_id=session_id, approval_gates=False)
        result = json.loads(t.dispatch("request_approval", {
            "phase": "scout",
            "summary": "Found 50 papers",
        }))
        assert result["decision"] == "approved"
        assert result.get("auto") is True

    def test_config_approval_gates_default(self):
        """Default config has approval gates on."""
        cfg = ARAConfig(workspace=Path("."))
        assert cfg.approval_gates is True


# ============================================================================
# CITATIONS / BIBLIOGRAPHY TESTS
# ============================================================================

class TestCitations:
    def test_bibtex_generation(self, tools, db, session_id):
        """get_citations produces valid BibTeX."""
        db.insert_paper(session_id, "Paper One", doi="10.1/one",
                        authors=["Alice Smith", "Bob Jones"], publication_year=2024)
        db.insert_paper(session_id, "Paper Two", doi="10.1/two",
                        authors=["Carol Davis"], publication_year=2023)

        result = json.loads(tools.dispatch("get_citations", {}))
        assert result["total_papers"] == 2
        bib = result["bibliography"]
        assert "@article{" in bib
        assert "Alice Smith" in bib
        assert "2024" in bib

    def test_bibtex_file_created(self, tools, db, session_id):
        """get_citations saves references.bib file."""
        db.insert_paper(session_id, "Test Paper", doi="10.1/test", authors=["Author"])
        result = json.loads(tools.dispatch("get_citations", {}))
        bib_path = Path(result["file_path"])
        assert bib_path.exists()
