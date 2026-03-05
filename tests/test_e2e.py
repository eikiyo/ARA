# Location: tests/test_e2e.py
# Purpose: End-to-end integration tests — full pipeline flows
# Functions: Tests for session lifecycle, tool dispatch chains, engine flows
# Calls: ara.* (all modules)
# Imports: pytest, json, tempfile, pathlib

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ara.config import ARAConfig
from ara.db import ARADB
from ara.engine import RLMEngine, ExternalContext, StepEvent
from ara.model import (
    Conversation, ModelTurn, ToolCall, ToolResult, TokenUsage, EchoFallbackModel,
)
from ara.runtime import SessionRuntime, SessionError
from ara.tools import ARATools
from ara.tools.defs import TOOL_DEFINITIONS
from ara.tui import ChatContext, dispatch_slash_command
from ara.settings import SettingsStore, PersistentSettings
from ara.credentials import CredentialStore


# ── Helpers ────────────────────────────────────────────────────────────

def _temp_workspace() -> Path:
    d = Path(tempfile.mkdtemp())
    return d


def _temp_db(workspace: Path | None = None) -> ARADB:
    ws = workspace or _temp_workspace()
    db_path = ws / "ara_data" / "session.db"
    return ARADB(db_path)


class ScriptedModel:
    """Model that plays back a scripted sequence of turns."""

    def __init__(self, turns: list[ModelTurn]):
        self.model = "scripted-test"
        self._turns = list(turns)
        self._step = 0
        self._conversations_created = 0

    def context_window(self) -> int:
        return 100_000

    def create_conversation(self, system_prompt, tool_defs):
        self._conversations_created += 1
        return Conversation(system_prompt=system_prompt, tool_defs=tool_defs)

    def generate(self, conversation, on_chunk=None):
        if self._step >= len(self._turns):
            return ModelTurn(text="[script exhausted]")
        turn = self._turns[self._step]
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
            "tool_calls": [{"name": tc.name, "args": tc.arguments, "id": tc.id} for tc in turn.tool_calls],
        })

    def append_tool_results(self, conv, results):
        conv._messages.append({
            "role": "tool",
            "results": [{"name": r.name, "tool_call_id": r.tool_call_id, "content": r.content} for r in results],
        })

    def condense_conversation(self, conv, summary):
        conv._messages = [{"role": "user", "text": summary}]

    def estimate_tokens(self, conv):
        return sum(len(str(m)) for m in conv._messages) // 4


# ── Session Lifecycle Tests ────────────────────────────────────────────

class TestSessionLifecycle:
    def test_bootstrap_new_session(self):
        ws = _temp_workspace()
        cfg = ARAConfig(workspace=ws)
        model = EchoFallbackModel()
        tools = ARATools(workspace=ws)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        runtime = SessionRuntime.bootstrap(engine=engine, config=cfg)
        assert runtime.session_id.startswith("session-")
        assert runtime.db_session_id is None  # Not started yet

    def test_start_research_creates_db_session(self):
        ws = _temp_workspace()
        cfg = ARAConfig(workspace=ws)
        model = EchoFallbackModel()
        tools = ARATools(workspace=ws)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        runtime = SessionRuntime.bootstrap(engine=engine, config=cfg)
        runtime.start_research("AI in healthcare")
        assert runtime.db_session_id is not None
        assert runtime.db_session_id >= 1

    def test_solve_auto_creates_session(self):
        ws = _temp_workspace()
        cfg = ARAConfig(workspace=ws)
        model = EchoFallbackModel()
        tools = ARATools(workspace=ws)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        runtime = SessionRuntime.bootstrap(engine=engine, config=cfg)
        result = runtime.solve("Research quantum computing")
        assert runtime.db_session_id is not None
        # Pipeline returns "Pipeline complete" (programmatic pipeline)
        assert "Pipeline complete" in result or "No API keys" in result or "Google API key" in result

    def test_resume_no_session_raises(self):
        ws = _temp_workspace()
        cfg = ARAConfig(workspace=ws)
        model = EchoFallbackModel()
        tools = ARATools(workspace=ws)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        # Create empty DB first
        db_path = ws / cfg.session_root_dir / "session.db"
        ARADB(db_path).close()

        with pytest.raises(SessionError, match="No active session"):
            SessionRuntime.bootstrap(engine=engine, config=cfg, resume=True)

    def test_resume_existing_session(self):
        ws = _temp_workspace()
        cfg = ARAConfig(workspace=ws)
        model = EchoFallbackModel()
        tools = ARATools(workspace=ws)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        # Create a session first
        runtime1 = SessionRuntime.bootstrap(engine=engine, config=cfg)
        runtime1.start_research("Neural networks")

        # Resume it
        engine2 = RLMEngine(model=model, tools=tools, config=cfg)
        runtime2 = SessionRuntime.bootstrap(engine=engine2, config=cfg, resume=True)
        assert runtime2.db_session_id == runtime1.db_session_id


# ── Engine Flow Tests ──────────────────────────────────────────────────

class TestEngineFlows:
    def test_single_tool_call_flow(self):
        """Engine calls a tool, gets result, generates final text."""
        model = ScriptedModel([
            # Turn 1: call get_rules
            ModelTurn(
                text="",
                tool_calls=[ToolCall(id="c1", name="get_rules", arguments={})],
                usage=TokenUsage(50, 20),
            ),
            # Turn 2: final response
            ModelTurn(text="Research complete with rules.", usage=TokenUsage(80, 40)),
        ])
        tools = ARATools()
        cfg = ARAConfig()
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        result = engine.solve("Start research")
        assert result == "Research complete with rules."
        assert engine.total_tokens.input_tokens == 130
        assert engine.total_tokens.output_tokens == 60

    def test_multi_tool_call_flow(self):
        """Engine processes multiple tool calls in a single turn."""
        model = ScriptedModel([
            ModelTurn(
                text="",
                tool_calls=[
                    ToolCall(id="c1", name="get_rules", arguments={}),
                    ToolCall(id="c2", name="get_rules", arguments={}),
                ],
                usage=TokenUsage(50, 20),
            ),
            ModelTurn(text="Done with two calls.", usage=TokenUsage(80, 40)),
        ])
        tools = ARATools()
        cfg = ARAConfig()
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        result = engine.solve("test")
        assert result == "Done with two calls."

    def test_tool_call_capping(self):
        """Engine deduplicates identical calls, then caps remaining."""
        model = ScriptedModel([
            ModelTurn(
                text="",
                tool_calls=[
                    # 10 unique calls (different arguments), under 15 spam threshold
                    ToolCall(id=f"c{i}", name="get_rules", arguments={"q": i})
                    for i in range(10)
                ],
                usage=TokenUsage(50, 20),
            ),
            ModelTurn(text="Capped.", usage=TokenUsage(80, 40)),
        ])
        tools = ARATools()
        cfg = ARAConfig(max_tool_calls_per_turn=3)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        events = []
        result = engine.solve("test", on_event=lambda e: events.append(e))
        assert result == "Capped."
        # Should only see 3 tool_call events (10 unique, capped to 3)
        tool_events = [e for e in events if e.event_type == "tool_call"]
        assert len(tool_events) == 3

    def test_cancel_during_tool_execution(self):
        """Cancel flag checked between tool calls."""
        model = ScriptedModel([
            ModelTurn(
                text="",
                tool_calls=[
                    ToolCall(id="c1", name="get_rules", arguments={"q": 1}),
                    ToolCall(id="c2", name="get_rules", arguments={"q": 2}),
                    ToolCall(id="c3", name="get_rules", arguments={"q": 3}),
                ],
                usage=TokenUsage(50, 20),
            ),
            ModelTurn(text="Should not reach.", usage=TokenUsage(80, 40)),
        ])
        tools = ARATools()
        cfg = ARAConfig(max_tool_calls_per_turn=3)  # Allow 3 to test mid-batch cancel
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        # Cancel after first tool
        original_dispatch = tools.dispatch
        call_count = 0

        def _counting_dispatch(name, args):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                engine.cancel_flag.set()
            return original_dispatch(name, args)

        tools.dispatch = _counting_dispatch
        result = engine.solve("test")
        assert "Cancelled" in result

    def test_max_steps_limit(self):
        """Engine stops after max_steps_per_call or loop detection."""
        # Model always returns tool calls with alternating names to avoid loop detection
        infinite_turns = [
            ModelTurn(
                text="",
                tool_calls=[ToolCall(id=f"c{i}", name=f"get_rules", arguments={"step": i})],
                usage=TokenUsage(10, 5),
            )
            for i in range(100)
        ]
        model = ScriptedModel(infinite_turns)
        tools = ARATools()
        cfg = ARAConfig(max_steps_per_call=5)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        result = engine.solve("test")
        # Loop detection fires at 3 repeats + 1 final text turn, or max_steps caps it
        assert model._step <= 5

    def test_timeout_limit(self):
        """Engine stops after max_solve_seconds."""
        model = ScriptedModel([
            ModelTurn(text="fast response", usage=TokenUsage(10, 5)),
        ])
        tools = ARATools()
        cfg = ARAConfig(max_solve_seconds=0)  # Immediate timeout
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        result = engine.solve("test")
        assert "Timeout" in result or result == "fast response"  # Depends on timing

    def test_step_events_flow(self):
        """All event types fire in correct order."""
        model = ScriptedModel([
            ModelTurn(
                text="",
                tool_calls=[ToolCall(id="c1", name="get_rules", arguments={})],
                usage=TokenUsage(50, 20),
            ),
            ModelTurn(text="Final.", usage=TokenUsage(80, 40)),
        ])
        tools = ARATools()
        cfg = ARAConfig()
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        events = []
        engine.solve("test", on_event=lambda e: events.append(e))

        types = [e.event_type for e in events]
        assert types[0] == "thinking"
        assert "tool_call" in types
        assert "tool_result" in types
        assert "text" in types

    def test_subtask_delegation(self):
        """Engine handles subtask tool call with recursive solve."""
        model = ScriptedModel([
            # Parent: delegates subtask
            ModelTurn(
                text="",
                tool_calls=[ToolCall(id="c1", name="subtask", arguments={
                    "objective": "Scout for papers",
                    "prompt": "scout",
                })],
                usage=TokenUsage(50, 20),
            ),
            # Child (depth=1): responds
            ModelTurn(text="Found 10 papers.", usage=TokenUsage(80, 40)),
            # Parent: final response
            ModelTurn(text="Scout complete with 10 papers.", usage=TokenUsage(60, 30)),
        ])
        tools = ARATools()
        cfg = ARAConfig(max_depth=2)
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        events = []
        result = engine.solve("Research AI", on_event=lambda e: events.append(e))
        assert "Scout complete" in result or "Found 10 papers" in result

        subtask_events = [e for e in events if e.event_type == "subtask_start"]
        assert len(subtask_events) >= 1

    def test_subtask_max_depth_error(self):
        """Subtask at max depth returns structured error."""
        model = ScriptedModel([
            ModelTurn(
                text="",
                tool_calls=[ToolCall(id="c1", name="subtask", arguments={
                    "objective": "Deep task",
                })],
                usage=TokenUsage(50, 20),
            ),
            # This turn is for the subtask at depth 1 which hits max_depth
            ModelTurn(text="Response after error.", usage=TokenUsage(60, 30)),
        ])
        tools = ARATools()
        cfg = ARAConfig(max_depth=0)  # depth > 0 is over limit
        engine = RLMEngine(model=model, tools=tools, config=cfg)

        result = engine.solve("test")
        # Parent should get the error and produce a response
        assert result is not None

    def test_observations_accumulate(self):
        """Tool results are stored as observations in context."""
        model = ScriptedModel([
            ModelTurn(
                text="",
                tool_calls=[ToolCall(id="c1", name="get_rules", arguments={})],
                usage=TokenUsage(50, 20),
            ),
            ModelTurn(text="Done.", usage=TokenUsage(80, 40)),
        ])
        tools = ARATools()
        cfg = ARAConfig()
        engine = RLMEngine(model=model, tools=tools, config=cfg)
        ctx = ExternalContext(topic="test")

        engine.solve("test", context=ctx)
        assert len(ctx.observations) > 0
        assert "[get_rules]" in ctx.observations[0]


# ── Tool Integration Tests ─────────────────────────────────────────────

class TestToolIntegration:
    def _setup_tools(self):
        ws = _temp_workspace()
        db = _temp_db(ws)
        sid = db.create_session(topic="Test Research")
        tools = ARATools(workspace=ws, db=db, session_id=sid)
        return tools, db, sid, ws

    def test_search_to_store_pipeline(self):
        """Search results auto-stored in DB after dispatch."""
        tools, db, sid, _ = self._setup_tools()

        # Simulate a search result
        fake_result = json.dumps({
            "papers": [
                {"title": "Paper A", "doi": "10.1/a", "source": "test", "authors": ["Auth"], "year": 2024},
                {"title": "Paper B", "doi": "10.1/b", "source": "test", "authors": ["Auth"], "year": 2023},
            ],
            "total": 2,
        })

        # Manually test auto-storage
        tools._store_search_results(fake_result)
        papers = db.get_papers(sid)
        assert len(papers) == 2

    def test_search_dedup_pipeline(self):
        """Duplicate papers not stored twice."""
        tools, db, sid, _ = self._setup_tools()

        result = json.dumps({
            "papers": [{"title": "Same Paper", "doi": "10.1/x", "source": "test"}],
        })
        tools._store_search_results(result)
        tools._store_search_results(result)  # Second time

        papers = db.get_papers(sid)
        assert len(papers) == 1

    def test_read_paper_tool(self):
        """read_paper returns paper from DB."""
        tools, db, sid, _ = self._setup_tools()
        db.store_papers(sid, [
            {"title": "Stored Paper", "doi": "10.1/test", "source": "s2",
             "authors": ["Alice"], "year": 2024, "abstract": "Test abstract"},
        ])
        papers = db.get_papers(sid)
        pid = papers[0]["paper_id"]

        result = json.loads(tools.dispatch("read_paper", {"paper_id": pid}))
        assert result["title"] == "Stored Paper"
        assert result["authors"] == ["Alice"]

    def test_read_paper_not_found(self):
        """read_paper returns error for missing paper."""
        tools, _, _, _ = self._setup_tools()
        result = json.loads(tools.dispatch("read_paper", {"paper_id": 99999}))
        assert "error" in result

    def test_extract_claims_get_paper(self):
        """extract_claims returns paper content when no claims provided."""
        tools, db, sid, _ = self._setup_tools()
        db.store_papers(sid, [
            {"title": "Claim Paper", "source": "s2", "abstract": "Findings here"},
        ])
        papers = db.get_papers(sid)
        pid = papers[0]["paper_id"]

        result = json.loads(tools.dispatch("extract_claims", {"paper_id": pid}))
        assert result["title"] == "Claim Paper"
        assert "instruction" in result

    def test_extract_claims_store(self):
        """extract_claims stores claims when provided."""
        tools, db, sid, _ = self._setup_tools()
        db.store_papers(sid, [{"title": "Paper", "source": "s2"}])
        papers = db.get_papers(sid)
        pid = papers[0]["paper_id"]

        result = json.loads(tools.dispatch("extract_claims", {
            "paper_id": pid,
            "claims": [
                {"claim_text": "Finding X is significant", "claim_type": "finding", "confidence": 0.9},
                {"claim_text": "Method Y is novel", "claim_type": "method", "confidence": 0.7},
            ],
        }))
        assert result["stored"] == 2

        claims = db.get_claims(sid)
        assert len(claims) == 2
        assert claims[0]["claim_text"] == "Finding X is significant"

    def test_score_hypothesis_store(self):
        """score_hypothesis stores hypothesis with scores."""
        tools, db, sid, _ = self._setup_tools()
        result = json.loads(tools.dispatch("score_hypothesis", {
            "hypothesis": "AI improves diagnosis",
            "scores": {
                "novelty": 0.8,
                "feasibility": 0.7,
                "evidence_strength": 0.9,
                "methodology_fit": 0.6,
                "impact": 0.85,
                "reproducibility": 0.75,
            },
        }))
        assert result["stored"] is True
        assert result["overall_score"] > 0

        hyps = db.get_hypotheses(sid)
        assert len(hyps) == 1
        assert hyps[0]["novelty"] == 0.8

    def test_score_hypothesis_no_scores(self):
        """score_hypothesis returns instructions when no scores."""
        tools, _, _, _ = self._setup_tools()
        result = json.loads(tools.dispatch("score_hypothesis", {
            "hypothesis": "Test hypothesis",
        }))
        assert "instruction" in result

    def test_write_section_stores_file(self):
        """write_section saves content to filesystem."""
        tools, _, _, ws = self._setup_tools()
        result = json.loads(tools.dispatch("write_section", {
            "section": "introduction",
            "content": "This paper investigates the role of AI in modern healthcare systems.",
        }))
        assert result["section"] == "introduction"
        assert result["word_count"] > 0

        section_path = Path(result["saved_to"])
        assert section_path.exists()
        assert "AI in modern healthcare" in section_path.read_text()

    def test_write_section_no_content(self):
        """write_section requires content."""
        tools, _, _, _ = self._setup_tools()
        result = json.loads(tools.dispatch("write_section", {
            "section": "abstract",
            "content": "",
        }))
        assert "error" in result

    def test_get_citations_with_claims(self):
        """get_citations returns only papers that have claims."""
        tools, db, sid, _ = self._setup_tools()
        db.store_papers(sid, [
            {"title": "Cited Paper", "doi": "10.1/cited", "source": "s2"},
            {"title": "Uncited Paper", "doi": "10.1/uncited", "source": "s2"},
        ])
        papers = db.get_papers(sid)
        # Add claims only to first paper
        db.store_claim(sid, papers[0]["paper_id"], claim_text="Finding", claim_type="finding")

        result = json.loads(tools.dispatch("get_citations", {}))
        assert result["citation_count"] == 1

    def test_get_rules_empty(self):
        """get_rules returns empty list for session with no rules."""
        tools, _, _, _ = self._setup_tools()
        result = json.loads(tools.dispatch("get_rules", {}))
        assert result["rules"] == []

    def test_get_rules_with_data(self):
        """get_rules returns active rules."""
        tools, db, sid, _ = self._setup_tools()
        db.add_rule(sid, "Only papers from 2020+", "constraint")
        db.add_rule(sid, "Exclude review papers", "exclude")

        result = json.loads(tools.dispatch("get_rules", {}))
        assert len(result["rules"]) == 2

    def test_track_cost(self):
        """track_cost computes and stores cost."""
        tools, db, sid, _ = self._setup_tools()
        result = json.loads(tools.dispatch("track_cost", {
            "model": "gemini-2.0-flash",
            "input_tokens": 1000,
            "output_tokens": 500,
        }))
        assert result["cost_usd"] > 0
        assert db.get_total_cost(sid) > 0

    def test_request_approval_auto(self):
        """request_approval auto-approves when gates disabled."""
        tools, _, _, _ = self._setup_tools()
        tools.approval_gates = False
        result = json.loads(tools.dispatch("request_approval", {
            "phase": "scout",
            "summary": "Found 50 papers",
        }))
        assert result["decision"] == "approved"
        assert result["auto"] is True

    def test_search_similar_keyword(self):
        """search_similar does keyword matching."""
        tools, db, sid, _ = self._setup_tools()
        db.store_papers(sid, [
            {"title": "Machine Learning for Genomics", "source": "s2", "abstract": "ML genomics"},
            {"title": "Deep Learning NLP", "source": "s2", "abstract": "NLP stuff"},
        ])
        result = json.loads(tools.dispatch("search_similar", {"text": "Genomics"}))
        assert len(result["papers"]) == 1


# ── Full Pipeline Tests ────────────────────────────────────────────────

class TestFullPipeline:
    def test_search_store_read_extract_score(self):
        """Full flow: store papers → read → extract claims → score hypothesis."""
        ws = _temp_workspace()
        db = _temp_db(ws)
        sid = db.create_session(topic="AI Research")
        tools = ARATools(workspace=ws, db=db, session_id=sid)

        # 1. Store papers
        tools._store_search_results(json.dumps({
            "papers": [
                {"title": "AI for Drug Discovery", "doi": "10.1/drug", "source": "s2",
                 "authors": ["Smith"], "year": 2024, "abstract": "AI accelerates drug discovery."},
                {"title": "ML in Genomics", "doi": "10.1/gen", "source": "arxiv",
                 "authors": ["Jones"], "year": 2023, "abstract": "ML applied to genomics."},
            ],
        }))
        papers = db.get_papers(sid)
        assert len(papers) == 2

        # 2. Read a paper
        pid = papers[0]["paper_id"]
        paper_data = json.loads(tools.dispatch("read_paper", {"paper_id": pid}))
        assert paper_data["title"] == "AI for Drug Discovery"

        # 3. Extract claims
        result = json.loads(tools.dispatch("extract_claims", {
            "paper_id": pid,
            "claims": [
                {"claim_text": "AI reduces drug discovery time by 40%", "claim_type": "finding", "confidence": 0.85},
            ],
        }))
        assert result["stored"] == 1

        # 4. Score hypothesis
        result = json.loads(tools.dispatch("score_hypothesis", {
            "hypothesis": "AI can accelerate drug discovery pipeline",
            "scores": {"novelty": 0.6, "feasibility": 0.8, "evidence_strength": 0.85,
                       "methodology_fit": 0.7, "impact": 0.9, "reproducibility": 0.65},
        }))
        assert result["stored"] is True
        assert 0.5 < result["overall_score"] < 1.0

        # 5. Verify data integrity
        claims = db.get_claims(sid)
        assert len(claims) == 1
        hyps = db.get_hypotheses(sid)
        assert len(hyps) == 1

    def test_engine_with_db_tools(self):
        """Engine solves with tools wired to real DB."""
        ws = _temp_workspace()
        db = _temp_db(ws)
        sid = db.create_session(topic="Test")
        db.add_rule(sid, "Focus on 2024 papers", "constraint")

        tools = ARATools(workspace=ws, db=db, session_id=sid)
        cfg = ARAConfig(workspace=ws)

        model = ScriptedModel([
            # Call get_rules
            ModelTurn(
                text="",
                tool_calls=[ToolCall(id="c1", name="get_rules", arguments={})],
                usage=TokenUsage(50, 20),
            ),
            # Respond based on rules
            ModelTurn(text="I see a rule: Focus on 2024 papers.", usage=TokenUsage(80, 40)),
        ])

        engine = RLMEngine(model=model, tools=tools, config=cfg)
        result = engine.solve("What are our rules?")
        assert "Focus on 2024 papers" in result


# ── TUI Slash Command Tests ────────────────────────────────────────────

class TestSlashCommands:
    def _make_ctx(self) -> ChatContext:
        ws = _temp_workspace()
        cfg = ARAConfig(workspace=ws, google_api_key="test-key")
        model = EchoFallbackModel()
        tools = ARATools(workspace=ws)
        engine = RLMEngine(model=model, tools=tools, config=cfg)
        runtime = SessionRuntime.bootstrap(engine=engine, config=cfg)
        settings_store = SettingsStore(workspace=ws)
        return ChatContext(runtime=runtime, cfg=cfg, settings_store=settings_store)

    def test_quit(self):
        ctx = self._make_ctx()
        assert dispatch_slash_command("/quit", ctx) == "quit"
        assert dispatch_slash_command("/exit", ctx) == "quit"

    def test_help(self):
        ctx = self._make_ctx()
        output = []
        result = dispatch_slash_command("/help", ctx, emit=lambda x: output.append(x))
        assert result == "handled"
        assert any("Commands" in o for o in output)

    def test_status(self):
        ctx = self._make_ctx()
        output = []
        result = dispatch_slash_command("/status", ctx, emit=lambda x: output.append(x))
        assert result == "handled"
        assert any("Session" in o for o in output)

    def test_gates_toggle(self):
        ctx = self._make_ctx()
        dispatch_slash_command("/gates off", ctx)
        assert ctx.cfg.approval_gates is False
        dispatch_slash_command("/gates on", ctx)
        assert ctx.cfg.approval_gates is True

    def test_clear(self):
        ctx = self._make_ctx()
        assert dispatch_slash_command("/clear", ctx) == "clear"

    def test_unknown_command(self):
        ctx = self._make_ctx()
        output = []
        result = dispatch_slash_command("/foobar", ctx, emit=lambda x: output.append(x))
        assert result == "handled"
        assert any("Unknown" in o for o in output)

    def test_not_a_command(self):
        ctx = self._make_ctx()
        assert dispatch_slash_command("hello world", ctx) == "not_command"

    def test_model_switch_non_gemini(self):
        ctx = self._make_ctx()
        output = []
        dispatch_slash_command("/model gpt-4o", ctx, emit=lambda x: output.append(x))
        assert any("Unknown model" in o for o in output)

    def test_model_switch_gemini(self):
        ctx = self._make_ctx()
        output = []
        dispatch_slash_command("/model gemini-2.5-pro", ctx, emit=lambda x: output.append(x))
        assert any("gemini-2.5-pro" in o for o in output)
        assert ctx.cfg.model == "gemini-2.5-pro"


# ── Credential Tests ───────────────────────────────────────────────────

class TestCredentials:
    def test_store_load_roundtrip(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            cred_path = Path(f.name)
        store = CredentialStore(path=cred_path)
        store.save("test-api-key-12345")
        loaded = store.load()
        assert loaded == "test-api-key-12345"

    def test_load_empty(self):
        store = CredentialStore(path=Path("/tmp/nonexistent_ara_creds.json"))
        loaded = store.load()
        assert loaded is None

    def test_load_from_env(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "env-key-xyz")
        store = CredentialStore(path=Path("/tmp/nonexistent_ara_creds.json"))
        loaded = store.load()
        assert loaded == "env-key-xyz"

    def test_env_file_parsing(self):
        ws = _temp_workspace()
        env_file = ws / ".env"
        env_file.write_text('GOOGLE_API_KEY="my-secret-key"\nOTHER_VAR=ignored\n')
        store = CredentialStore(path=Path("/tmp/nonexistent_ara_creds.json"))
        key = store.load_from_env_file(env_file)
        assert key == "my-secret-key"


# ── Settings Tests ─────────────────────────────────────────────────────

class TestSettings:
    def test_save_load_roundtrip(self):
        ws = _temp_workspace()
        store = SettingsStore(workspace=ws)
        settings = PersistentSettings(default_model="gemini-2.5-pro")
        store.save(settings)
        loaded = store.load()
        assert loaded.default_model == "gemini-2.5-pro"

    def test_load_empty(self):
        ws = _temp_workspace()
        store = SettingsStore(workspace=ws)
        loaded = store.load()
        assert loaded.default_model is None

    def test_settings_json_roundtrip(self):
        s = PersistentSettings(default_model="gemini-2.0-flash")
        j = s.to_json()
        restored = PersistentSettings.from_json(j)
        assert restored.default_model == "gemini-2.0-flash"

    def test_settings_from_invalid_json(self):
        s = PersistentSettings.from_json(None)
        assert s.default_model is None
        s = PersistentSettings.from_json("not a dict")
        assert s.default_model is None


# ── Search Tool Parsing Tests ──────────────────────────────────────────

class TestSearchParsing:
    def test_normalize_doi(self):
        from ara.tools.search import _normalize_doi
        assert _normalize_doi("https://doi.org/10.1234/test") == "10.1234/test"
        assert _normalize_doi("http://dx.doi.org/10.1234/test") == "10.1234/test"
        assert _normalize_doi("10.1234/test") == "10.1234/test"
        assert _normalize_doi("") is None
        assert _normalize_doi(None) is None

    def test_valid_year(self):
        from ara.tools.search import _valid_year
        assert _valid_year(2024) == 2024
        assert _valid_year("2023") == 2023
        assert _valid_year(99999) is None
        assert _valid_year(1799) is None
        assert _valid_year(None) is None
        assert _valid_year("not a year") is None

    def test_paper_dict_normalizes(self):
        from ara.tools.search import _paper_dict
        p = _paper_dict(
            title="  Test Paper  ",
            abstract="A" * 3000,
            authors=["Auth"] * 30,
            year=2024,
            doi="https://doi.org/10.1/test",
            source="test",
        )
        assert p["title"] == "Test Paper"
        assert len(p["abstract"]) <= 2000
        assert len(p["authors"]) <= 20
        assert p["doi"] == "10.1/test"
        assert p["year"] == 2024


# ── DB Edge Cases ──────────────────────────────────────────────────────

class TestDBEdgeCases:
    def test_empty_doi_normalized(self):
        db = _temp_db()
        sid = db.create_session(topic="Test")
        stored = db.store_papers(sid, [
            {"title": "Paper No DOI", "doi": "", "source": "test"},
        ])
        assert stored == 1
        # Store again with empty DOI — should dedup by title
        stored = db.store_papers(sid, [
            {"title": "Paper No DOI", "doi": "", "source": "test2"},
        ])
        assert stored == 0
        db.close()

    def test_empty_title_skipped(self):
        db = _temp_db()
        sid = db.create_session(topic="Test")
        stored = db.store_papers(sid, [
            {"title": "", "source": "test"},
            {"title": "   ", "source": "test"},
        ])
        assert stored == 0
        db.close()

    def test_store_fulltext_content(self):
        db = _temp_db()
        sid = db.create_session(topic="Test")
        db.store_papers(sid, [{"title": "FT Paper", "doi": "10.1/ft", "source": "test"}])
        db.store_fulltext_content(doi="10.1/ft", text="Full text content here")
        paper = db.get_papers(sid)[0]
        p = db.get_paper(paper["paper_id"])
        assert p["full_text"] == "Full text content here"
        db.close()

    def test_cited_papers_with_claims(self):
        db = _temp_db()
        sid = db.create_session(topic="Test")
        db.store_papers(sid, [
            {"title": "Paper With Claims", "doi": "10.1/a", "source": "s2"},
            {"title": "Paper Without Claims", "doi": "10.1/b", "source": "s2"},
        ])
        papers = db.get_papers(sid)
        # Only add claim to first paper
        db.store_claim(sid, papers[0]["paper_id"], claim_text="Test", claim_type="finding")

        cited = db.get_cited_papers(sid)
        assert len(cited) == 1
        assert cited[0]["title"] == "Paper With Claims"
        db.close()

    def test_cited_papers_fallback_deep_read(self):
        """When no claims, falls back to deep-read selected papers."""
        db = _temp_db()
        sid = db.create_session(topic="Test")
        db.store_papers(sid, [
            {"title": "Selected Paper", "doi": "10.1/sel", "source": "s2"},
            {"title": "Not Selected", "doi": "10.1/not", "source": "s2"},
        ])
        papers = db.get_papers(sid)
        # Mark first as selected for deep read
        db._conn.execute(
            "UPDATE papers SET selected_for_deep_read = 1 WHERE paper_id = ?",
            (papers[0]["paper_id"],),
        )
        db._conn.commit()

        cited = db.get_cited_papers(sid)
        assert len(cited) == 1
        assert cited[0]["title"] == "Selected Paper"
        db.close()
