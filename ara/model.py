# Location: ara/model.py
# Purpose: Provider-agnostic LLM abstraction — Gemini (native), OpenAI, Anthropic
# Functions: GeminiModel, OpenAIModel, AnthropicModel, BaseModel protocol
# Calls: google-genai, openai, anthropic SDKs
# Imports: dataclasses, typing, json, logging

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

_log = logging.getLogger(__name__)


# ── Data types ──────────────────────────────────────────────────────────

@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class ToolResult:
    tool_call_id: str
    name: str
    content: str


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class ModelTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage | None = None
    raw_response: Any = None


class ModelError(Exception):
    pass


# ── Conversation ────────────────────────────────────────────────────────

@dataclass
class Conversation:
    system_prompt: str = ""
    tool_defs: list[dict[str, Any]] = field(default_factory=list)
    _messages: list[dict[str, Any]] = field(default_factory=list)

    def message_count(self) -> int:
        return len(self._messages)


# ── Base protocol ───────────────────────────────────────────────────────

class BaseModel(Protocol):
    model: str

    def create_conversation(
        self, system_prompt: str, tool_defs: list[dict[str, Any]],
    ) -> Conversation: ...

    def generate(
        self, conversation: Conversation,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ModelTurn: ...

    def append_user_message(self, conv: Conversation, text: str) -> None: ...
    def append_assistant_turn(self, conv: Conversation, turn: ModelTurn) -> None: ...
    def append_tool_results(self, conv: Conversation, results: list[ToolResult]) -> None: ...
    def condense_conversation(self, conv: Conversation, summary: str) -> None: ...
    def estimate_tokens(self, conv: Conversation) -> int: ...
    def context_window(self) -> int: ...


# ── Gemini (native SDK) ────────────────────────────────────────────────

_GEMINI_CONTEXT_WINDOWS: dict[str, int] = {
    "gemini-2.0-flash": 1_048_576,
    "gemini-2.5-flash": 1_048_576,
    "gemini-2.5-pro": 1_048_576,
    "gemini-1.5-flash": 1_048_576,
    "gemini-1.5-pro": 2_097_152,
}


def _tool_defs_to_gemini(tool_defs: list[dict[str, Any]]) -> list[Any]:
    """Convert ARA tool definitions to Gemini FunctionDeclaration objects."""
    from google.genai import types

    declarations = []
    for td in tool_defs:
        params = td.get("parameters", {})
        # Gemini requires parameters_json_schema format
        declarations.append(types.FunctionDeclaration(
            name=td["name"],
            description=td.get("description", ""),
            parameters_json_schema=params if params.get("properties") else None,
        ))
    return [types.Tool(function_declarations=declarations)]


class GeminiModel:
    def __init__(self, model: str, api_key: str):
        from google import genai
        self.model = model
        self._client = genai.Client(api_key=api_key)

    def context_window(self) -> int:
        return _GEMINI_CONTEXT_WINDOWS.get(self.model, 1_048_576)

    def create_conversation(
        self, system_prompt: str, tool_defs: list[dict[str, Any]],
    ) -> Conversation:
        return Conversation(
            system_prompt=system_prompt,
            tool_defs=tool_defs,
        )

    def generate(
        self, conversation: Conversation,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ModelTurn:
        from google.genai import types

        tools = _tool_defs_to_gemini(conversation.tool_defs) if conversation.tool_defs else None
        contents = self._build_contents(conversation)

        config = types.GenerateContentConfig(
            system_instruction=conversation.system_prompt or None,
            tools=tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage = TokenUsage()
        raw_response = None

        try:
            for chunk in self._client.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=config,
            ):
                raw_response = chunk
                # Text content
                if chunk.text:
                    text_parts.append(chunk.text)
                    if on_chunk:
                        on_chunk(chunk.text)

                # Function calls
                if chunk.function_calls:
                    for fc in chunk.function_calls:
                        tool_calls.append(ToolCall(
                            id=f"call_{uuid.uuid4().hex[:12]}",
                            name=fc.name,
                            arguments=dict(fc.args) if fc.args else {},
                        ))

                # Usage
                if chunk.usage_metadata:
                    usage.input_tokens = chunk.usage_metadata.prompt_token_count or 0
                    usage.output_tokens = chunk.usage_metadata.candidates_token_count or 0

        except Exception as exc:
            error_text = str(exc).lower()
            if "quota" in error_text or "429" in error_text:
                raise ModelError(f"Rate limited: {exc}") from exc
            if "api key" in error_text or "403" in error_text:
                raise ModelError(f"Authentication failed: {exc}") from exc
            raise ModelError(f"Gemini API error: {exc}") from exc

        return ModelTurn(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
            raw_response=raw_response,
        )

    def append_user_message(self, conv: Conversation, text: str) -> None:
        conv._messages.append({"role": "user", "text": text})

    def append_assistant_turn(self, conv: Conversation, turn: ModelTurn) -> None:
        if not turn.text and not turn.tool_calls:
            return
        conv._messages.append({
            "role": "assistant",
            "text": turn.text,
            "tool_calls": [
                {"name": tc.name, "args": tc.arguments, "id": tc.id}
                for tc in turn.tool_calls
            ],
        })

    def append_tool_results(self, conv: Conversation, results: list[ToolResult]) -> None:
        conv._messages.append({
            "role": "tool",
            "results": [
                {"name": r.name, "tool_call_id": r.tool_call_id, "content": r.content}
                for r in results
            ],
        })

    def condense_conversation(self, conv: Conversation, summary: str) -> None:
        conv._messages = [{"role": "user", "text": f"[Previous context summary]\n{summary}"}]

    def estimate_tokens(self, conv: Conversation) -> int:
        total = len(conv.system_prompt) // 4
        for msg in conv._messages:
            total += len(str(msg)) // 4
        return total

    def _build_contents(self, conv: Conversation) -> list[Any]:
        from google.genai import types

        contents: list[types.Content] = []
        for msg in conv._messages:
            role = msg["role"]

            if role == "user":
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=msg["text"])],
                ))

            elif role == "assistant":
                parts: list[types.Part] = []
                if msg.get("text"):
                    parts.append(types.Part.from_text(text=msg["text"]))
                for tc in msg.get("tool_calls", []):
                    parts.append(types.Part.from_function_call(
                        name=tc["name"],
                        args=tc["args"],
                    ))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))

            elif role == "tool":
                parts = []
                for r in msg.get("results", []):
                    parts.append(types.Part.from_function_response(
                        name=r["name"],
                        response={"result": r["content"]},
                    ))
                if parts:
                    contents.append(types.Content(role="tool", parts=parts))

        return contents


# ── OpenAI-compatible (OpenAI, OpenRouter, Ollama) ──────────────────────

_OPENAI_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o3-mini": 200_000,
}


def _tool_defs_to_openai(tool_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools = []
    for td in tool_defs:
        params = td.get("parameters", {"type": "object", "properties": {}})
        tools.append({
            "type": "function",
            "function": {
                "name": td["name"],
                "description": td.get("description", ""),
                "parameters": params,
            },
        })
    return tools


class OpenAIModel:
    def __init__(
        self, model: str, api_key: str,
        base_url: str | None = None,
        reasoning_effort: str | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 120,
    ):
        from openai import OpenAI
        self.model = model
        self._reasoning_effort = reasoning_effort
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=extra_headers,
            timeout=timeout,
        )

    def context_window(self) -> int:
        return _OPENAI_CONTEXT_WINDOWS.get(self.model, 128_000)

    def create_conversation(
        self, system_prompt: str, tool_defs: list[dict[str, Any]],
    ) -> Conversation:
        return Conversation(system_prompt=system_prompt, tool_defs=tool_defs)

    def generate(
        self, conversation: Conversation,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ModelTurn:
        messages = self._build_messages(conversation)
        tools = _tool_defs_to_openai(conversation.tool_defs) if conversation.tool_defs else None

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort

        text_parts: list[str] = []
        tool_calls_by_idx: dict[int, dict[str, Any]] = {}
        usage = TokenUsage()

        try:
            stream = self._client.chat.completions.create(**kwargs)
            for chunk in stream:
                if not chunk.choices:
                    if chunk.usage:
                        usage.input_tokens = chunk.usage.prompt_tokens or 0
                        usage.output_tokens = chunk.usage.completion_tokens or 0
                    continue

                delta = chunk.choices[0].delta
                if delta.content:
                    text_parts.append(delta.content)
                    if on_chunk:
                        on_chunk(delta.content)

                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index if tc_delta.index is not None else 0
                        if idx not in tool_calls_by_idx:
                            tool_calls_by_idx[idx] = {
                                "id": tc_delta.id or f"call_{uuid.uuid4().hex[:12]}",
                                "name": "",
                                "arguments": "",
                            }
                        entry = tool_calls_by_idx[idx]
                        if tc_delta.id:
                            entry["id"] = tc_delta.id
                        if tc_delta.function and tc_delta.function.name:
                            entry["name"] = tc_delta.function.name
                        if tc_delta.function and tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments

        except Exception as exc:
            error_text = str(exc).lower()
            if "rate" in error_text or "429" in error_text:
                raise ModelError(f"Rate limited: {exc}") from exc
            raise ModelError(f"OpenAI API error: {exc}") from exc

        tool_calls = []
        for idx in sorted(tool_calls_by_idx):
            entry = tool_calls_by_idx[idx]
            if not entry["name"]:
                continue
            try:
                args = json.loads(entry["arguments"]) if entry["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=entry["id"], name=entry["name"], arguments=args))

        return ModelTurn(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
        )

    def append_user_message(self, conv: Conversation, text: str) -> None:
        conv._messages.append({"role": "user", "text": text})

    def append_assistant_turn(self, conv: Conversation, turn: ModelTurn) -> None:
        if not turn.text and not turn.tool_calls:
            return
        conv._messages.append({
            "role": "assistant",
            "text": turn.text,
            "tool_calls": [
                {"name": tc.name, "args": tc.arguments, "id": tc.id}
                for tc in turn.tool_calls
            ],
        })

    def append_tool_results(self, conv: Conversation, results: list[ToolResult]) -> None:
        conv._messages.append({
            "role": "tool",
            "results": [
                {"name": r.name, "tool_call_id": r.tool_call_id, "content": r.content}
                for r in results
            ],
        })

    def condense_conversation(self, conv: Conversation, summary: str) -> None:
        conv._messages = [{"role": "user", "text": f"[Previous context summary]\n{summary}"}]

    def estimate_tokens(self, conv: Conversation) -> int:
        total = len(conv.system_prompt) // 4
        for msg in conv._messages:
            total += len(str(msg)) // 4
        return total

    def _build_messages(self, conv: Conversation) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if conv.system_prompt:
            messages.append({"role": "system", "content": conv.system_prompt})

        for msg in conv._messages:
            role = msg["role"]
            if role == "user":
                messages.append({"role": "user", "content": msg["text"]})

            elif role == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": msg.get("text") or None}
                tcs = msg.get("tool_calls", [])
                if tcs:
                    entry["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])},
                        }
                        for tc in tcs
                    ]
                messages.append(entry)

            elif role == "tool":
                for r in msg.get("results", []):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": r["tool_call_id"],
                        "content": r["content"],
                    })

        return messages


# ── Anthropic ───────────────────────────────────────────────────────────

_ANTHROPIC_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
}


def _tool_defs_to_anthropic(tool_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools = []
    for td in tool_defs:
        tools.append({
            "name": td["name"],
            "description": td.get("description", ""),
            "input_schema": td.get("parameters", {"type": "object", "properties": {}}),
        })
    return tools


class AnthropicModel:
    def __init__(
        self, model: str, api_key: str,
        base_url: str | None = None,
        reasoning_effort: str | None = None,
    ):
        import anthropic
        self.model = model
        self._reasoning_effort = reasoning_effort
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**kwargs)

    def context_window(self) -> int:
        return _ANTHROPIC_CONTEXT_WINDOWS.get(self.model, 200_000)

    def create_conversation(
        self, system_prompt: str, tool_defs: list[dict[str, Any]],
    ) -> Conversation:
        return Conversation(system_prompt=system_prompt, tool_defs=tool_defs)

    def generate(
        self, conversation: Conversation,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ModelTurn:
        messages = self._build_messages(conversation)
        tools = _tool_defs_to_anthropic(conversation.tool_defs) if conversation.tool_defs else []

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16384,
            "messages": messages,
            "stream": True,
        }
        if conversation.system_prompt:
            kwargs["system"] = conversation.system_prompt
        if tools:
            kwargs["tools"] = tools

        # Thinking/extended reasoning
        if self._reasoning_effort and self._is_thinking_capable():
            budget_map = {"low": 4096, "medium": 10000, "high": 32000}
            budget = budget_map.get(self._reasoning_effort, 10000)
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage = TokenUsage()
        current_tc: dict[str, Any] | None = None

        try:
            with self._client.messages.stream(**kwargs) as stream:
                for event in stream:
                    event_type = getattr(event, "type", "")

                    if event_type == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            current_tc = {"id": block.id, "name": block.name, "args_json": ""}

                    elif event_type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            text_parts.append(delta.text)
                            if on_chunk:
                                on_chunk(delta.text)
                        elif delta.type == "input_json_delta" and current_tc is not None:
                            current_tc["args_json"] += delta.partial_json

                    elif event_type == "content_block_stop":
                        if current_tc is not None:
                            try:
                                args = json.loads(current_tc["args_json"]) if current_tc["args_json"] else {}
                            except json.JSONDecodeError:
                                args = {}
                            tool_calls.append(ToolCall(
                                id=current_tc["id"],
                                name=current_tc["name"],
                                arguments=args,
                            ))
                            current_tc = None

                    elif event_type == "message_delta":
                        u = getattr(event, "usage", None)
                        if u:
                            usage.output_tokens = getattr(u, "output_tokens", 0)

                    elif event_type == "message_start":
                        u = getattr(event.message, "usage", None)
                        if u:
                            usage.input_tokens = getattr(u, "input_tokens", 0)

        except Exception as exc:
            error_text = str(exc).lower()
            if "rate" in error_text or "429" in error_text:
                raise ModelError(f"Rate limited: {exc}") from exc
            if "overloaded" in error_text:
                raise ModelError(f"API overloaded: {exc}") from exc
            raise ModelError(f"Anthropic API error: {exc}") from exc

        return ModelTurn(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
        )

    def _is_thinking_capable(self) -> bool:
        m = self.model.lower()
        return "claude-3-7" in m or "claude-4" in m or "opus" in m or "sonnet-4" in m

    def append_user_message(self, conv: Conversation, text: str) -> None:
        conv._messages.append({"role": "user", "text": text})

    def append_assistant_turn(self, conv: Conversation, turn: ModelTurn) -> None:
        if not turn.text and not turn.tool_calls:
            return
        conv._messages.append({
            "role": "assistant",
            "text": turn.text,
            "tool_calls": [
                {"name": tc.name, "args": tc.arguments, "id": tc.id}
                for tc in turn.tool_calls
            ],
        })

    def append_tool_results(self, conv: Conversation, results: list[ToolResult]) -> None:
        conv._messages.append({
            "role": "tool",
            "results": [
                {"name": r.name, "tool_call_id": r.tool_call_id, "content": r.content}
                for r in results
            ],
        })

    def condense_conversation(self, conv: Conversation, summary: str) -> None:
        conv._messages = [{"role": "user", "text": f"[Previous context summary]\n{summary}"}]

    def estimate_tokens(self, conv: Conversation) -> int:
        total = len(conv.system_prompt) // 4
        for msg in conv._messages:
            total += len(str(msg)) // 4
        return total

    def _build_messages(self, conv: Conversation) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for msg in conv._messages:
            role = msg["role"]
            if role == "user":
                messages.append({"role": "user", "content": msg["text"]})

            elif role == "assistant":
                content: list[dict[str, Any]] = []
                if msg.get("text"):
                    content.append({"type": "text", "text": msg["text"]})
                for tc in msg.get("tool_calls", []):
                    content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["args"],
                    })
                if content:
                    messages.append({"role": "assistant", "content": content})

            elif role == "tool":
                content = []
                for r in msg.get("results", []):
                    content.append({
                        "type": "tool_result",
                        "tool_use_id": r["tool_call_id"],
                        "content": r["content"],
                    })
                if content:
                    messages.append({"role": "user", "content": content})

        return messages


# ── Fallback (no API key) ──────────────────────────────────────────────

class EchoFallbackModel:
    def __init__(self, note: str = "No API keys configured"):
        self.model = "echo-fallback"
        self._note = note

    def context_window(self) -> int:
        return 4096

    def create_conversation(self, system_prompt: str, tool_defs: list[dict[str, Any]]) -> Conversation:
        return Conversation(system_prompt=system_prompt, tool_defs=tool_defs)

    def generate(self, conversation: Conversation, on_chunk: Callable[[str], None] | None = None) -> ModelTurn:
        text = f"[ARA] {self._note}. Run `ara --configure-keys` to set up API keys."
        if on_chunk:
            on_chunk(text)
        return ModelTurn(text=text)

    def append_user_message(self, conv: Conversation, text: str) -> None:
        conv._messages.append({"role": "user", "text": text})

    def append_assistant_turn(self, conv: Conversation, turn: ModelTurn) -> None:
        pass

    def append_tool_results(self, conv: Conversation, results: list[ToolResult]) -> None:
        pass

    def condense_conversation(self, conv: Conversation, summary: str) -> None:
        pass

    def estimate_tokens(self, conv: Conversation) -> int:
        return 0
