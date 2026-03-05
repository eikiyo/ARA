# Location: tests/test_engine.py
# Purpose: Tests for the recursive engine
# Functions: test_engine_basics, test_solve
# Calls: ara.engine, ara.model
# Imports: pytest

import json
from unittest.mock import MagicMock

from ara.config import ARAConfig
from ara.engine import RLMEngine, ExternalContext, StepEvent, TurnSummary
from ara.model import (
    Conversation, EchoFallbackModel, ModelTurn,
    ToolCall, ToolResult, TokenUsage,
)
from ara.tools import ARATools


class MockModel:
    """Model that returns a fixed text response."""
    def __init__(self, text: str = "Mock response"):
        self.model = "mock-model"
        self._text = text
        self._call_count = 0

    def context_window(self) -> int:
        return 8000

    def create_conversation(self, system_prompt, tool_defs):
        return Conversation(system_prompt=system_prompt, tool_defs=tool_defs)

    def generate(self, conversation, on_chunk=None):
        self._call_count += 1
        if on_chunk:
            on_chunk(self._text)
        return ModelTurn(
            text=self._text,
            tool_calls=[],
            usage=TokenUsage(input_tokens=100, output_tokens=50),
        )

    def append_user_message(self, conv, text):
        conv._messages.append({"role": "user", "text": text})

    def append_assistant_turn(self, conv, turn):
        conv._messages.append({"role": "assistant", "text": turn.text})

    def append_tool_results(self, conv, results):
        pass

    def condense_conversation(self, conv, summary):
        conv._messages = [{"role": "user", "text": summary}]

    def estimate_tokens(self, conv):
        return sum(len(str(m)) for m in conv._messages) // 4


class ToolCallingModel:
    """Model that calls a tool on first turn, then responds with text."""
    def __init__(self):
        self.model = "tool-model"
        self._step = 0

    def context_window(self) -> int:
        return 8000

    def create_conversation(self, system_prompt, tool_defs):
        return Conversation(system_prompt=system_prompt, tool_defs=tool_defs)

    def generate(self, conversation, on_chunk=None):
        self._step += 1
        if self._step == 1:
            return ModelTurn(
                text="",
                tool_calls=[ToolCall(id="c1", name="get_rules", arguments={})],
                usage=TokenUsage(input_tokens=50, output_tokens=20),
            )
        text = "Research complete."
        if on_chunk:
            on_chunk(text)
        return ModelTurn(text=text, usage=TokenUsage(input_tokens=80, output_tokens=30))

    def append_user_message(self, conv, text):
        conv._messages.append({"role": "user", "text": text})

    def append_assistant_turn(self, conv, turn):
        conv._messages.append({"role": "assistant", "text": turn.text})

    def append_tool_results(self, conv, results):
        conv._messages.append({"role": "tool", "results": [{"content": r.content} for r in results]})

    def condense_conversation(self, conv, summary):
        conv._messages = [{"role": "user", "text": summary}]

    def estimate_tokens(self, conv):
        return sum(len(str(m)) for m in conv._messages) // 4


def test_engine_simple_response():
    model = MockModel("Hello from ARA")
    tools = ARATools()
    cfg = ARAConfig()
    engine = RLMEngine(model=model, tools=tools, config=cfg)

    result = engine.solve("What is AI?")
    assert result == "Hello from ARA"
    assert engine.total_tokens.input_tokens > 0


def test_engine_with_tool_call():
    model = ToolCallingModel()
    tools = ARATools()
    cfg = ARAConfig()
    engine = RLMEngine(model=model, tools=tools, config=cfg)

    result = engine.solve("Get rules")
    assert "Research complete" in result


def test_engine_cancel():
    model = MockModel()
    tools = ARATools()
    cfg = ARAConfig()
    engine = RLMEngine(model=model, tools=tools, config=cfg)
    engine.cancel_flag.set()

    result = engine.solve("test")
    assert "Cancelled" in result


def test_engine_max_depth():
    model = MockModel()
    tools = ARATools()
    cfg = ARAConfig(max_depth=0)
    engine = RLMEngine(model=model, tools=tools, config=cfg)

    # At depth 0, should still run (depth > max_depth is the check)
    result = engine.solve("test")
    assert result == "Mock response"


def test_engine_tool_call_cap():
    """Model returning more tool calls than max_tool_calls_per_turn gets capped."""
    class ManyToolsModel(MockModel):
        def generate(self, conversation, on_chunk=None):
            self._call_count += 1
            if self._call_count == 1:
                # Return 25 tool calls
                calls = [ToolCall(id=f"c{i}", name="get_rules", arguments={}) for i in range(25)]
                return ModelTurn(text="", tool_calls=calls, usage=TokenUsage(50, 20))
            return ModelTurn(text="Done", usage=TokenUsage(50, 20))

    model = ManyToolsModel()
    tools = ARATools()
    cfg = ARAConfig(max_tool_calls_per_turn=5)
    engine = RLMEngine(model=model, tools=tools, config=cfg)

    result = engine.solve("test")
    assert result == "Done"


def test_echo_fallback_integration():
    model = EchoFallbackModel(note="No keys")
    tools = ARATools()
    cfg = ARAConfig()
    engine = RLMEngine(model=model, tools=tools, config=cfg)

    result = engine.solve("test")
    assert "No keys" in result


def test_step_events():
    model = MockModel()
    tools = ARATools()
    cfg = ARAConfig()
    engine = RLMEngine(model=model, tools=tools, config=cfg)

    events = []
    result = engine.solve("test", on_event=lambda e: events.append(e))
    assert any(e.event_type == "thinking" for e in events)
    assert any(e.event_type == "text" for e in events)


def test_external_context():
    ctx = ExternalContext(topic="AI research", paper_type="research_article")
    assert ctx.topic == "AI research"
    assert len(ctx.observations) == 0


def test_turn_summary():
    ts = TurnSummary(role="assistant", text="Hello", tool_names=["search"])
    assert ts.role == "assistant"
    assert ts.tool_names == ["search"]
