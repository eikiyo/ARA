# Location: ara/model.py
# Purpose: LLM abstraction — Gemini native SDK only
# Functions: GeminiModel, EchoFallbackModel, BaseModel protocol
# Calls: google-genai SDK
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
        _max_retries: int = 5,
    ) -> ModelTurn:
        from google.genai import types

        tools = _tool_defs_to_gemini(conversation.tool_defs) if conversation.tool_defs else None
        contents = self._build_contents(conversation)

        config = types.GenerateContentConfig(
            system_instruction=conversation.system_prompt or None,
            tools=tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        last_exc: Exception | None = None
        for attempt in range(_max_retries):
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            usage = TokenUsage()

            try:
                for chunk in self._client.models.generate_content_stream(
                    model=self.model,
                    contents=contents,
                    config=config,
                ):
                    if chunk.text:
                        text_parts.append(chunk.text)
                        if on_chunk:
                            on_chunk(chunk.text)

                    if chunk.function_calls:
                        for fc in chunk.function_calls:
                            tool_calls.append(ToolCall(
                                id=f"call_{uuid.uuid4().hex[:12]}",
                                name=fc.name,
                                arguments=dict(fc.args) if fc.args else {},
                            ))

                    if chunk.usage_metadata:
                        usage.input_tokens = chunk.usage_metadata.prompt_token_count or 0
                        usage.output_tokens = chunk.usage_metadata.candidates_token_count or 0

                return ModelTurn(
                    text="".join(text_parts),
                    tool_calls=tool_calls,
                    usage=usage,
                )

            except Exception as exc:
                error_text = str(exc).lower()

                # Auth errors — no point retrying
                if "api key" in error_text or "403" in error_text:
                    raise ModelError(f"Authentication failed: {exc}") from exc

                # Rate limit — retry with exponential backoff
                if "quota" in error_text or "429" in error_text or "resource_exhausted" in error_text:
                    last_exc = exc
                    wait = min(2 ** attempt * 2, 60)  # 2s, 4s, 8s, 16s, 32s
                    _log.warning("Rate limited (attempt %d/%d), retrying in %ds...", attempt + 1, _max_retries, wait)
                    if on_chunk:
                        on_chunk(f"\n[Rate limited — retrying in {wait}s...]\n")
                    time.sleep(wait)
                    continue

                # Other API errors — retry once, then fail
                if attempt == 0:
                    last_exc = exc
                    _log.warning("API error (attempt 1), retrying in 3s: %s", exc)
                    time.sleep(3)
                    continue
                raise ModelError(f"Gemini API error: {exc}") from exc

        raise ModelError(f"Rate limited after {_max_retries} retries: {last_exc}") from last_exc

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
            role = msg.get("role", "")

            if role == "user":
                text = msg.get("text", "")
                if text:
                    contents.append(types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=text)],
                    ))

            elif role == "assistant":
                parts: list[types.Part] = []
                if msg.get("text"):
                    parts.append(types.Part.from_text(text=msg["text"]))
                for tc in msg.get("tool_calls", []):
                    name = tc.get("name")
                    if name:
                        parts.append(types.Part.from_function_call(
                            name=name,
                            args=tc.get("args", {}),
                        ))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))

            elif role == "tool":
                parts = []
                for r in msg.get("results", []):
                    name = r.get("name")
                    if name:
                        parts.append(types.Part.from_function_response(
                            name=name,
                            response={"result": r.get("content", "")},
                        ))
                if parts:
                    contents.append(types.Content(role="tool", parts=parts))

        return contents


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
        text = f"[ARA] {self._note}. Run `ara --configure-keys` to set up your Google API key."
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
