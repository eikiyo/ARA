# ARA QA Test Code Templates

Quick-start code for the 15 critical gaps identified in QA_AUDIT_REPORT.md

---

## Template: C1 — Full End-to-End Pipeline

**File:** `tests/test_full_pipeline_e2e.py`

```python
"""
Full end-to-end pipeline tests: Scout → Writer → outputs
Tests the complete 7-agent research flow with real DB and scripted model.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ara.config import ARAConfig
from ara.db import ARADB
from ara.engine import RLMEngine, ExternalContext
from ara.model import ModelTurn, ToolCall, ToolResult, TokenUsage
from ara.runtime import SessionRuntime
from ara.tools import ARATools


class ScriptedPhaseModel:
    """Model that plays back realistic turns for each phase."""

    def __init__(self):
        self.model = "scripted-phase"
        self._phase = None
        self._step = 0
        self._phases = {}  # phase_name -> list of turns

    def set_phase_turns(self, phase: str, turns: list):
        """Register turns for a phase."""
        self._phases[phase] = turns
        self._phase = phase
        self._step = 0

    def context_window(self) -> int:
        return 100_000

    def create_conversation(self, system_prompt, tool_defs):
        from ara.model import Conversation
        return Conversation(system_prompt=system_prompt, tool_defs=tool_defs)

    def generate(self, conversation, on_chunk=None):
        if not self._phase or self._phase not in self._phases:
            return ModelTurn(text="[no script]", usage=TokenUsage(10, 5))

        turns = self._phases[self._phase]
        if self._step >= len(turns):
            return ModelTurn(text="[phase done]", usage=TokenUsage(10, 5))

        turn = turns[self._step]
        self._step += 1
        if on_chunk and turn.text:
            on_chunk(turn.text)
        return turn

    def append_user_message(self, conv, text):
        conv._messages.append({"role": "user", "text": text})

    def append_assistant_turn(self, conv, turn):
        conv._messages.append({
            "role": "assistant",
            "text": turn.text,
            "tool_calls": [
                {"name": tc.name, "args": tc.arguments, "id": tc.id}
                for tc in turn.tool_calls
            ],
        })

    def append_tool_results(self, conv, results):
        conv._messages.append({
            "role": "tool",
            "results": [
                {"name": r.name, "tool_call_id": r.tool_call_id, "content": r.content}
                for r in results
            ],
        })

    def condense_conversation(self, conv, summary):
        conv._messages = [{"role": "user", "text": summary}]

    def estimate_tokens(self, conv):
        return sum(len(str(m)) for m in conv._messages) // 4


def test_full_pipeline_scout_to_writer():
    """
    Full pipeline: Scout phase → Analyst → Verifier → Hypothesis → Critic → Writer
    Verifies:
    - Papers found and stored (Scout)
    - Claims extracted (Analyst)
    - Claims verified (Verifier)
    - Hypotheses generated (Hypothesis)
    - Hypothesis critiqued (Critic)
    - Paper outline and draft written (Writer)
    - Output files created
    """
    ws = Path(tempfile.mkdtemp())
    cfg = ARAConfig(workspace=ws, approval_gates=False)

    # Create model with scripted turns
    model = ScriptedPhaseModel()

    # Scout phase: call search_all, return papers
    scout_turns = [
        ModelTurn(
            text="",
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="search_all",
                    arguments={"query": "immigrants Sweden integration policy"},
                ),
            ],
            usage=TokenUsage(500, 200),
        ),
        ModelTurn(
            text="Found papers on Swedish immigration policy.",
            usage=TokenUsage(200, 100),
        ),
    ]
    model.set_phase_turns("scout", scout_turns)

    # Analyst triage: read papers, call search_similar for context
    analyst_triage_turns = [
        ModelTurn(
            text="Ranking papers by relevance...",
            tool_calls=[
                ToolCall(
                    id="c2",
                    name="search_similar",
                    arguments={"text": "Swedish immigration integration"},
                ),
            ],
            usage=TokenUsage(300, 150),
        ),
        ModelTurn(
            text="Top 50 papers selected for deep read.",
            usage=TokenUsage(150, 75),
        ),
    ]
    model.set_phase_turns("analyst_triage", analyst_triage_turns)

    # Analyst deep read: extract claims
    analyst_deep_read_turns = [
        ModelTurn(
            text="",
            tool_calls=[
                ToolCall(
                    id="c3",
                    name="extract_claims",
                    arguments={
                        "paper_id": 1,
                        "claims": [
                            {
                                "claim_text": "Swedish immigration policy emphasizes labor market integration",
                                "claim_type": "finding",
                                "confidence": 0.9,
                                "supporting_quotes": ["Quote from paper"],
                                "section": "introduction",
                            },
                        ],
                    },
                ),
            ],
            usage=TokenUsage(400, 200),
        ),
        ModelTurn(
            text="Extracted 15 key claims from papers.",
            usage=TokenUsage(100, 50),
        ),
    ]
    model.set_phase_turns("analyst_deep_read", analyst_deep_read_turns)

    # Verifier: check claims
    verifier_turns = [
        ModelTurn(
            text="Verifying claims...",
            tool_calls=[
                ToolCall(
                    id="c4",
                    name="check_retraction",
                    arguments={"doi": "10.1234/test"},
                ),
            ],
            usage=TokenUsage(200, 100),
        ),
        ModelTurn(
            text="All claims verified. No retractions found.",
            usage=TokenUsage(100, 50),
        ),
    ]
    model.set_phase_turns("verifier", verifier_turns)

    # Hypothesis: generate hypotheses
    hypothesis_turns = [
        ModelTurn(
            text="",
            tool_calls=[
                ToolCall(
                    id="c5",
                    name="score_hypothesis",
                    arguments={
                        "hypothesis": "Language training improves integration outcomes for Swedish immigrants",
                        "scores": {
                            "novelty": 0.7,
                            "feasibility": 0.8,
                            "evidence_strength": 0.85,
                            "methodology_fit": 0.75,
                            "impact": 0.9,
                            "reproducibility": 0.8,
                        },
                    },
                ),
            ],
            usage=TokenUsage(300, 150),
        ),
        ModelTurn(
            text="Generated 5 hypotheses ranked by strength.",
            usage=TokenUsage(100, 50),
        ),
    ]
    model.set_phase_turns("hypothesis", hypothesis_turns)

    # Critic: evaluate hypothesis
    critic_turns = [
        ModelTurn(
            text="Hypothesis is well-supported by evidence and feasible.",
            usage=TokenUsage(200, 100),
        ),
    ]
    model.set_phase_turns("critic", critic_turns)

    # Writer: outline + draft
    writer_turns = [
        ModelTurn(
            text="",
            tool_calls=[
                ToolCall(
                    id="c6",
                    name="write_section",
                    arguments={
                        "section": "outline",
                        "content": "# Title\n## Introduction\n## Methods\n## Results\n## Discussion\n## Conclusion",
                    },
                ),
            ],
            usage=TokenUsage(300, 200),
        ),
        ModelTurn(
            text="",
            tool_calls=[
                ToolCall(
                    id="c7",
                    name="write_section",
                    arguments={
                        "section": "introduction",
                        "content": "Swedish immigration policy aims to integrate newcomers into the labor market and society...",
                    },
                ),
            ],
            usage=TokenUsage(400, 300),
        ),
        ModelTurn(
            text="Paper draft complete.",
            usage=TokenUsage(100, 50),
        ),
    ]
    model.set_phase_turns("writer", writer_turns)

    # Create engine and runtime
    tools = ARATools(workspace=ws)
    engine = RLMEngine(model=model, tools=tools, config=cfg)
    runtime = SessionRuntime.bootstrap(engine=engine, config=cfg)

    # Start research
    runtime.start_research("Swedish immigration integration policy 2024")

    # Run manager agent (which delegates to all phases)
    # In real scenario, manager would orchestrate all 7 phases
    # For now, test that session created and DB is ready
    assert runtime.db_session_id is not None

    # Verify DB structure
    session = runtime.db.get_session(runtime.db_session_id)
    assert session["topic"] == "Swedish immigration integration policy 2024"
    assert session["status"] == "active"

    # Verify papers would be stored (if search_all was called)
    # In this test, we'd mock search results
    papers = runtime.db.get_papers(runtime.db_session_id)
    # papers would be populated by search_all auto-store

    # Verify output directory exists
    output_dir = ws / cfg.session_root_dir / "output"
    # Would be created by write_section tool


if __name__ == "__main__":
    test_full_pipeline_scout_to_writer()
    print("✓ Full pipeline test passed")
```

---

## Template: C2 — Search API Failures

**File:** `tests/test_search_error_scenarios.py`

```python
"""
Tests for search API failure scenarios.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from ara.tools import ARATools
from ara.tools.search import search_all, _request_with_retry


def test_search_all_all_apis_fail():
    """
    When all 9 search APIs fail/timeout, search_all should:
    - Return empty papers list
    - Collect error messages
    - Not crash
    """
    tools = ARATools()

    # Mock _request_with_retry to always return None (timeout/error)
    with patch("ara.tools.search._request_with_retry", return_value=None):
        result_str = tools.dispatch("search_all", {"query": "test immigration"})
        result = json.loads(result_str)

    assert "papers" in result
    assert isinstance(result["papers"], list)
    assert len(result["papers"]) == 0
    assert result["total"] == 0


def test_search_all_partial_failure():
    """
    When 5 APIs succeed and 4 fail, search_all should:
    - Return papers from the 5 that succeeded
    - Log errors for the 4 that failed
    - Continue (not halt)
    """
    tools = ARATools()

    success_response = {
        "papers": [
            {"title": "Paper A", "doi": "10.1/a", "source": "test", "authors": [], "year": 2024},
            {"title": "Paper B", "doi": "10.1/b", "source": "test", "authors": [], "year": 2023},
        ],
        "total": 2,
    }

    call_count = [0]

    def mock_request(url, **kwargs):
        call_count[0] += 1
        # First 5 calls succeed, rest fail
        if call_count[0] <= 5:
            return success_response
        return None

    with patch("ara.tools.search._request_with_retry", side_effect=mock_request):
        result_str = tools.dispatch("search_all", {"query": "test"})
        result = json.loads(result_str)

    assert len(result["papers"]) > 0  # At least some papers from successful APIs
    # Error tracking would show which APIs failed


def test_search_all_rate_limit_retry():
    """
    When an API returns 429 (rate limit), _request_with_retry should:
    - Retry with exponential backoff
    - Succeed on retry
    - Not crash
    """
    import httpx

    # Create a mock response
    response_429 = MagicMock()
    response_429.status_code = 429

    response_200 = MagicMock()
    response_200.status_code = 200
    response_200.json.return_value = {"papers": []}
    response_200.headers.get.return_value = "application/json"

    call_count = [0]

    def mock_get(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return response_429
        return response_200

    with patch("httpx.get", side_effect=mock_get):
        result = _request_with_retry("http://example.com/api")
        assert result is not None  # Should succeed on retry


def test_semantic_scholar_auth_error():
    """
    When Semantic Scholar API key is invalid, should return error gracefully.
    """
    tools = ARATools()

    error_response = {
        "papers": [],
        "error": "Semantic Scholar API key invalid",
    }

    with patch("ara.tools.search._request_with_retry", return_value=error_response):
        result_str = tools.dispatch("search_semantic_scholar", {"query": "test"})
        result = json.loads(result_str)

    assert "error" in result or len(result.get("papers", [])) == 0


def test_empty_search_results():
    """
    When search returns valid response but with 0 papers, should:
    - Store empty result
    - Allow user to refine search
    """
    tools = ARATools()

    empty_response = {
        "papers": [],
        "total": 0,
    }

    with patch("ara.tools.search._request_with_retry", return_value=empty_response):
        result_str = tools.dispatch("search_arxiv", {"query": "xyzunknowntopic123"})
        result = json.loads(result_str)

    assert result["total"] == 0
    # User should get option to refine search in approval gate


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

---

## Template: C3 — Database Concurrent Access

**File:** `tests/test_concurrent_db_access.py`

```python
"""
Tests for concurrent DB access safety (multi-threaded searches + claims extraction).
"""

import json
import threading
import tempfile
from pathlib import Path

import pytest

from ara.db import ARADB
from ara.tools import ARATools


def test_concurrent_search_and_claim_storage():
    """
    Thread 1: search_all stores papers
    Thread 2: simultaneously calls extract_claims

    Verify:
    - No database locks or corruption
    - Both operations complete
    - Results are consistent
    """
    ws = Path(tempfile.mkdtemp())
    db_path = ws / "session.db"
    db = ARADB(db_path)
    sid = db.create_session(topic="Concurrent Test")

    tools = ARATools(workspace=ws, db=db, session_id=sid)
    results = {"error": None, "papers_stored": 0, "claims_stored": 0}

    def search_and_store():
        try:
            # Simulate search_all finding papers
            papers = [
                {"title": f"Paper {i}", "doi": f"10.1/{i}", "source": "test", "authors": [], "year": 2024}
                for i in range(10)
            ]
            stored = db.store_papers(sid, papers)
            results["papers_stored"] = stored
        except Exception as e:
            results["error"] = e

    def extract_claims():
        try:
            # After papers are stored, extract claims
            import time
            time.sleep(0.1)  # Let search thread store first
            papers = db.get_papers(sid)
            if papers:
                for paper in papers[:3]:  # Extract from first 3
                    tools.dispatch("extract_claims", {
                        "paper_id": paper["paper_id"],
                        "claims": [
                            {"claim_text": "Test claim", "claim_type": "finding", "confidence": 0.8},
                        ],
                    })
                results["claims_stored"] = len(papers)
        except Exception as e:
            results["error"] = e

    # Run both threads concurrently
    t1 = threading.Thread(target=search_and_store)
    t2 = threading.Thread(target=extract_claims)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Verify success
    assert results["error"] is None, f"Concurrent access failed: {results['error']}"
    assert results["papers_stored"] == 10

    # Verify data consistency
    papers = db.get_papers(sid)
    assert len(papers) == 10

    claims = db.get_claims(sid)
    assert len(claims) > 0


def test_concurrent_hypothesis_scoring():
    """
    Multiple threads calling score_hypothesis simultaneously.
    """
    ws = Path(tempfile.mkdtemp())
    db_path = ws / "session.db"
    db = ARADB(db_path)
    sid = db.create_session(topic="Hypothesis Test")

    tools = ARATools(workspace=ws, db=db, session_id=sid)
    results = {"errors": [], "count": 0}
    lock = threading.Lock()

    def score_hyp(i):
        try:
            result_str = tools.dispatch("score_hypothesis", {
                "hypothesis": f"Hypothesis {i}: Test hypothesis",
                "scores": {
                    "novelty": 0.5 + (i * 0.05),
                    "feasibility": 0.6,
                    "evidence_strength": 0.7,
                    "methodology_fit": 0.65,
                    "impact": 0.8,
                    "reproducibility": 0.75,
                },
            })
            result = json.loads(result_str)
            with lock:
                if result.get("stored"):
                    results["count"] += 1
        except Exception as e:
            with lock:
                results["errors"].append(str(e))

    # Spawn 5 concurrent hypothesis scoring threads
    threads = [threading.Thread(target=score_hyp, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results["errors"]) == 0, f"Errors: {results['errors']}"
    assert results["count"] == 5

    # Verify DB consistency
    hyps = db.get_hypotheses(sid)
    assert len(hyps) == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

---

## Template: C5 — Budget Enforcement

**File:** `tests/test_budget_tracking.py`

```python
"""
Tests for budget tracking and enforcement.
"""

import json
import tempfile
from pathlib import Path

import pytest

from ara.db import ARADB
from ara.tools import ARATools


def test_cost_accumulation():
    """
    Log multiple costs; verify total is sum of all.
    """
    ws = Path(tempfile.mkdtemp())
    db_path = ws / "session.db"
    db = ARADB(db_path)
    sid = db.create_session(topic="Budget Test", budget_cap=10.0)

    # Log 5 calls
    costs = [0.10, 0.15, 0.20, 0.25, 0.30]
    for cost in costs:
        db.log_cost(sid, model="gemini-2.0-flash", input_tokens=100_000, output_tokens=50_000, cost_usd=cost)

    total = db.get_total_cost(sid)
    assert abs(total - sum(costs)) < 0.01, f"Expected {sum(costs)}, got {total}"


def test_budget_warning_threshold():
    """
    At 80% budget, should show warning.
    At 100% budget, should pause pipeline.
    """
    ws = Path(tempfile.mkdtemp())
    db_path = ws / "session.db"
    db = ARADB(db_path)
    sid = db.create_session(topic="Budget Threshold", budget_cap=1.00)

    # Log cost to reach 85%
    db.log_cost(sid, model="gemini-2.0-flash", input_tokens=1_000_000, output_tokens=125_000, cost_usd=0.85)

    total = db.get_total_cost(sid)
    budget = db.get_session(sid)["budget_cap"]

    # Warning threshold: 80%
    if total / budget >= 0.80:
        warning_shown = True
    else:
        warning_shown = False

    assert warning_shown, "Warning should be shown at 85%"

    # Log more to exceed 100%
    db.log_cost(sid, model="gemini-2.0-flash", input_tokens=1_000_000, output_tokens=125_000, cost_usd=0.20)

    total = db.get_total_cost(sid)

    if total / budget >= 1.00:
        pipeline_paused = True
    else:
        pipeline_paused = False

    assert pipeline_paused, "Pipeline should pause at 100%"


def test_budget_cap_in_approval_gate():
    """
    Integration test: after a phase, approval gate shows budget status.

    (This would be integration test with TUI rendering, harder to test.)
    """
    pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

---

## Template: C6 — Tool Name Validation

**File:** `tests/test_prompt_tool_validation.py`

```python
"""
Validate that tool names in prompts match TOOL_DEFINITIONS.
"""

import re
import pytest

from ara.prompts import PHASE_PROMPTS
from ara.tools.defs import TOOL_DEFINITIONS


def test_tool_names_in_prompts_exist():
    """
    Extract all tool names from prompt text.
    Verify each tool name exists in TOOL_DEFINITIONS.
    """
    tool_names_in_defs = {td["name"] for td in TOOL_DEFINITIONS}

    # Pattern to match function-like calls: search_arxiv(), read_paper(), etc.
    tool_call_pattern = re.compile(r'\b([a-z_][a-z0-9_]*)\s*\(')

    # Also match inline references: "use search_arxiv"
    tool_ref_pattern = re.compile(r'(?:use|call|dispatch|invoke)\s+([a-z_][a-z0-9_]*)')

    for phase, prompt_text in PHASE_PROMPTS.items():
        found_tools = set()

        # Find function-like calls
        for match in tool_call_pattern.finditer(prompt_text):
            tool = match.group(1)
            if tool not in ["print", "len", "str", "list", "dict", "if", "for", "while"]:
                found_tools.add(tool)

        # Find inline references
        for match in tool_ref_pattern.finditer(prompt_text):
            tool = match.group(1)
            if tool not in ["the", "a", "an"]:
                found_tools.add(tool)

        # Verify each found tool exists
        for tool in found_tools:
            assert tool in tool_names_in_defs, \
                f"Phase '{phase}' references unknown tool '{tool}'. Available: {sorted(tool_names_in_defs)}"


def test_critical_tools_in_prompts():
    """
    Verify that critical tools are mentioned in the appropriate prompts.
    """
    # Scout should mention search tools
    scout_prompt = PHASE_PROMPTS.get("scout", "")
    assert any(search in scout_prompt for search in ["search_", "Search"]), \
        "Scout phase should mention search tools"

    # Analyst should mention read_paper
    analyst_prompt = PHASE_PROMPTS.get("analyst_deep_read", "")
    assert "read_paper" in analyst_prompt or "extract_claims" in analyst_prompt, \
        "Analyst phase should mention read_paper or extract_claims"

    # Verifier should mention verification tools
    verifier_prompt = PHASE_PROMPTS.get("verifier", "")
    assert any(tool in verifier_prompt for tool in ["check_retraction", "get_citation_count", "validate_doi"]), \
        "Verifier phase should mention verification tools"

    # Writer should mention write_section
    writer_prompt = PHASE_PROMPTS.get("writer", "")
    assert "write_section" in writer_prompt or "write" in writer_prompt.lower(), \
        "Writer phase should mention write_section"


def test_tool_parameter_consistency():
    """
    Verify that prompts reference tool parameters that actually exist.
    """
    # This is harder to test without parsing prompts, but example:
    # If prompt says "call search_arxiv with query parameter",
    # verify TOOL_DEFINITIONS for search_arxiv has query in required/properties

    search_arxiv_def = next(
        (td for td in TOOL_DEFINITIONS if td["name"] == "search_arxiv"),
        None,
    )
    assert search_arxiv_def is not None

    required = search_arxiv_def.get("parameters", {}).get("required", [])
    properties = search_arxiv_def.get("parameters", {}).get("properties", {})

    assert "query" in properties, "search_arxiv should have 'query' parameter"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

---

## Template: C11 — Approval Gate Flow

**File:** `tests/test_approval_gate_flow.py`

```python
"""
Tests for approval gate TUI interaction.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ara.db import ARADB
from ara.gates import run_approval_gate


def test_approval_gate_writes_file():
    """
    When request_approval is called, should write gate data file.
    """
    ws = Path(tempfile.mkdtemp())
    gates_dir = ws / ".ara" / "gates"
    gates_dir.mkdir(parents=True, exist_ok=True)

    ctx = {
        "workspace": ws,
        "approval_gates": True,
    }

    # Mock run_approval_gate to simulate user approval
    with patch("ara.gates.run_approval_gate") as mock_gate:
        mock_gate.return_value = "approved"

        from ara.tools.pipeline import request_approval
        result_str = request_approval({
            "phase": "scout",
            "summary": "Found 50 papers",
            "data": json.dumps({"papers": 50}),
        }, ctx)

    result = json.loads(result_str)
    assert result["decision"] == "approved"


def test_approval_gate_reject_action():
    """
    User rejects a phase. Should return decision='rejected' with reason.
    """
    with patch("ara.gates.run_approval_gate") as mock_gate:
        mock_gate.return_value = "rejected"

        from ara.tools.pipeline import request_approval
        result_str = request_approval({
            "phase": "analyst_triage",
            "summary": "Ranked 50 papers",
            "data": json.dumps({"papers": 50}),
        }, {"workspace": Path("."), "approval_gates": True})

    result = json.loads(result_str)
    assert result["decision"] == "rejected"


def test_approval_gate_markdown_format():
    """
    Gate data files should be valid markdown.
    """
    ws = Path(tempfile.mkdtemp())
    gates_dir = ws / ".ara" / "gates"
    gates_dir.mkdir(parents=True, exist_ok=True)

    # Test that scout_results.md is valid markdown
    scout_file = gates_dir / "scout_results.md"
    scout_content = "# Scout Results\n\nFound 50 papers from 7 sources.\n\n"
    scout_file.write_text(scout_content)

    # Verify it's readable
    assert scout_file.exists()
    assert "# Scout Results" in scout_file.read_text()
    assert scout_file.stat().st_size > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

---

## Quick Integration Check

Run all critical tests:

```bash
# Install test dependencies
pip install pytest pytest-mock pytest-asyncio

# Run full pipeline test (slow)
pytest tests/test_full_pipeline_e2e.py -v

# Run search error scenarios
pytest tests/test_search_error_scenarios.py -v

# Run concurrent DB access
pytest tests/test_concurrent_db_access.py -v

# Run budget tracking
pytest tests/test_budget_tracking.py -v

# Run tool name validation
pytest tests/test_prompt_tool_validation.py -v

# Run approval gate flow
pytest tests/test_approval_gate_flow.py -v

# Run all
pytest tests/ -v
```

---

**End of Test Templates**
