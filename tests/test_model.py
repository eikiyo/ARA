# Location: tests/test_model.py
# Purpose: Tests for model abstraction layer
# Functions: test_conversation, test_echo_fallback, test_tool_call, test_gemini_tool_defs
# Calls: ara.model
# Imports: pytest

import json
from ara.model import (
    Conversation, EchoFallbackModel, GeminiModel, ModelTurn, ToolCall,
    ToolResult, TokenUsage, _tool_defs_to_gemini,
)


def test_conversation_basics():
    conv = Conversation(system_prompt="test", tool_defs=[])
    assert conv.message_count() == 0
    conv._messages.append({"role": "user", "text": "hello"})
    assert conv.message_count() == 1


def test_echo_fallback():
    model = EchoFallbackModel(note="test note")
    assert model.model == "echo-fallback"

    conv = model.create_conversation("sys", [])
    model.append_user_message(conv, "hello")

    chunks = []
    turn = model.generate(conv, on_chunk=lambda c: chunks.append(c))
    assert "test note" in turn.text
    assert len(chunks) == 1


def test_echo_fallback_context_window():
    model = EchoFallbackModel()
    assert model.context_window() == 4096
    assert model.estimate_tokens(Conversation()) == 0


def test_tool_call_dataclass():
    tc = ToolCall(id="call_123", name="search_arxiv", arguments={"query": "AI"})
    assert tc.name == "search_arxiv"
    assert tc.arguments["query"] == "AI"


def test_tool_result_dataclass():
    tr = ToolResult(tool_call_id="call_123", name="search_arxiv", content='{"papers": []}')
    assert tr.name == "search_arxiv"


def test_model_turn():
    turn = ModelTurn(
        text="Hello",
        tool_calls=[ToolCall(id="c1", name="search", arguments={})],
        usage=TokenUsage(input_tokens=100, output_tokens=50),
    )
    assert turn.text == "Hello"
    assert len(turn.tool_calls) == 1
    assert turn.usage.input_tokens == 100


def test_gemini_tool_defs_conversion():
    defs = [{
        "name": "search_arxiv",
        "description": "Search arXiv",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }]
    try:
        tools = _tool_defs_to_gemini(defs)
        assert len(tools) == 1
    except ImportError:
        pass  # google-genai not installed in test env


def test_gemini_model_creation():
    try:
        model = GeminiModel(model="gemini-2.0-flash", api_key="test-key")
        assert model.model == "gemini-2.0-flash"
        assert model.context_window() == 1_048_576
    except ImportError:
        pass  # google-genai not installed in test env
