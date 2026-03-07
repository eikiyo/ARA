# Location: ara/model.py
# Purpose: LLM abstraction — Gemini, Anthropic, OpenAI, LoadBalanced
# Functions: GeminiModel, AnthropicModel, OpenAIModel, LoadBalancedModel, EchoFallbackModel
# Calls: google-genai SDK, anthropic SDK, openai SDK
# Imports: dataclasses, typing, json, logging, random

from __future__ import annotations

import json
import logging
import random
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
    thought_sig: bytes | None = None


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


class RateLimitError(ModelError):
    """Raised when all retries exhausted on rate limit. Should stop the entire pipeline."""
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

_DEEP_READ_ROUND_ROBIN_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.0-flash-lite",
]

_GEMINI_CONTEXT_WINDOWS: dict[str, int] = {
    "gemini-2.0-flash": 1_048_576,
    "gemini-2.5-flash": 1_048_576,
    "gemini-3.1-flash-lite-preview": 1_048_576,
    "gemini-2.5-pro": 1_048_576,
    "gemini-3-flash-preview": 1_048_576,
    "gemini-3.1-pro-preview": 1_048_576,
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


# Fallback chains: when primary model is rate limited, try these in order
_GEMINI_FALLBACK_CHAIN: dict[str, list[str]] = {
    "gemini-3.1-pro-preview": ["gemini-3-flash-preview", "gemini-3.1-flash-lite-preview"],
    "gemini-3-flash-preview": ["gemini-3.1-flash-lite-preview", "gemini-2.5-flash-preview-05-20"],
    "gemini-3.1-flash-lite-preview": ["gemini-2.5-flash-preview-05-20", "gemini-2.0-flash-lite"],
    "gemini-2.5-flash-preview-05-20": ["gemini-2.0-flash-lite"],
}


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

    def _stream_generate(
        self, model_name: str, contents: Any, config: Any,
        on_chunk: Callable[[str], None] | None = None,
        _stream_timeout: int = 120,
    ) -> ModelTurn:
        """Stream generate from a specific model. Handles Gemini 3.x thought_signature.

        _stream_timeout: max seconds to wait for the entire stream to complete.
        Raises ModelError if the stream hangs beyond this limit.
        """
        import threading as _th
        import queue as _q

        is_gemini3 = "3." in model_name or "3-" in model_name
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage = TokenUsage()

        # Run streaming in a thread so we can enforce a timeout
        chunk_q: _q.Queue = _q.Queue()
        _SENTINEL = object()

        def _stream_worker():
            try:
                for c in self._client.models.generate_content_stream(
                    model=model_name, contents=contents, config=config,
                ):
                    chunk_q.put(c)
                chunk_q.put(_SENTINEL)
            except Exception as exc:
                chunk_q.put(exc)

        worker = _th.Thread(target=_stream_worker, daemon=True)
        worker.start()

        deadline = time.time() + _stream_timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise ModelError(f"Stream timeout ({_stream_timeout}s) for {model_name}")
            try:
                chunk = chunk_q.get(timeout=min(remaining, 60))
            except _q.Empty:
                # Check if worker thread died
                if not worker.is_alive():
                    raise ModelError(f"Stream worker died for {model_name}")
                continue
            if chunk is _SENTINEL:
                break
            if isinstance(chunk, Exception):
                raise chunk
            if chunk.text:
                text_parts.append(chunk.text)
                if on_chunk:
                    on_chunk(chunk.text)

            if chunk.function_calls:
                for fc in chunk.function_calls:
                    tc = ToolCall(
                        id=f"call_{uuid.uuid4().hex[:12]}",
                        name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                    )
                    tool_calls.append(tc)

            # Capture thought_signature from parts (Gemini 3.x only)
            if is_gemini3 and hasattr(chunk, 'candidates') and chunk.candidates:
                for cand in chunk.candidates:
                    if hasattr(cand, 'content') and cand.content and cand.content.parts:
                        for p in cand.content.parts:
                            if hasattr(p, 'thought_signature') and p.thought_signature and p.function_call:
                                for tc in tool_calls:
                                    if tc.name == p.function_call.name and tc.thought_sig is None:
                                        tc.thought_sig = p.thought_signature

            if chunk.usage_metadata:
                usage.input_tokens = chunk.usage_metadata.prompt_token_count or 0
                usage.output_tokens = chunk.usage_metadata.candidates_token_count or 0

        return ModelTurn(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
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

        # Round-robin through primary + fallbacks: no retries, immediate rotation
        fallbacks = _GEMINI_FALLBACK_CHAIN.get(self.model, [])
        all_models = [self.model] + fallbacks
        last_exc: Exception | None = None

        for model_name in all_models:
            try:
                return self._stream_generate(model_name, contents, config, on_chunk)
            except Exception as exc:
                error_text = str(exc).lower()

                # Auth errors — no point trying other models
                if "api key" in error_text or "403" in error_text:
                    raise ModelError(f"Authentication failed: {exc}") from exc

                # Rate limit — immediately try next model, no sleep
                if "quota" in error_text or "429" in error_text or "resource_exhausted" in error_text:
                    last_exc = exc
                    _log.warning("Rate limited on %s, rotating to next model...", model_name)
                    continue

                # Other API errors — retry once on same model, then rotate
                if last_exc is None:
                    last_exc = exc
                    _log.warning("API error on %s, retrying once: %s", model_name, exc)
                    try:
                        time.sleep(2)
                        return self._stream_generate(model_name, contents, config, on_chunk)
                    except Exception:
                        _log.warning("Retry failed on %s, rotating...", model_name)
                        continue
                _log.warning("API error on %s: %s, rotating...", model_name, exc)
                continue

        # All models exhausted — single 30s wait + retry primary
        _log.warning("All %d models rate limited — waiting 30s then retrying %s",
                     len(all_models), self.model)
        time.sleep(30)
        try:
            return self._stream_generate(self.model, contents, config, on_chunk)
        except Exception as final_exc:
            raise RateLimitError(
                f"All models exhausted: {', '.join(all_models)}. "
                f"Wait a few minutes and try again."
            ) from final_exc

    def append_user_message(self, conv: Conversation, text: str) -> None:
        conv._messages.append({"role": "user", "text": text})

    def append_assistant_turn(self, conv: Conversation, turn: ModelTurn) -> None:
        if not turn.text and not turn.tool_calls:
            return
        conv._messages.append({
            "role": "assistant",
            "text": turn.text,
            "tool_calls": [
                {
                    "name": tc.name, "args": tc.arguments, "id": tc.id,
                    "thought_sig": tc.thought_sig,
                }
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
                        part = types.Part.from_function_call(
                            name=name,
                            args=tc.get("args", {}),
                        )
                        # Gemini 3.x requires thought_signature on function_call parts
                        thought_sig = tc.get("thought_sig")
                        if thought_sig:
                            part.thought_signature = thought_sig
                        parts.append(part)
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


# ── Anthropic (Claude) ─────────────────────────────────────

_ANTHROPIC_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}


class RoundRobinGeminiModel:
    """Cycles through multiple Gemini models to multiply rate limit quota."""

    def __init__(self, models: list[str], api_key: str):
        from google import genai
        self._models = models
        self._client = genai.Client(api_key=api_key)
        self._index = 0
        self.model = models[0]  # Display name

    def _next_model(self) -> str:
        model = self._models[self._index % len(self._models)]
        self._index += 1
        return model

    def context_window(self) -> int:
        return _GEMINI_CONTEXT_WINDOWS.get(self._models[0], 1_048_576)

    def create_conversation(
        self, system_prompt: str, tool_defs: list[dict[str, Any]],
    ) -> Conversation:
        return Conversation(system_prompt=system_prompt, tool_defs=tool_defs)

    def generate(
        self, conversation: Conversation,
        on_chunk: Callable[[str], None] | None = None,
        _max_retries: int = 5,
    ) -> ModelTurn:
        from google.genai import types

        tools = _tool_defs_to_gemini(conversation.tool_defs) if conversation.tool_defs else None
        contents = GeminiModel._build_contents(self, conversation)

        config = types.GenerateContentConfig(
            system_instruction=conversation.system_prompt or None,
            tools=tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        # Try each model in round-robin, skip rate-limited ones
        tried = set()
        for _ in range(len(self._models) * 2):
            model_name = self._next_model()
            if model_name in tried:
                continue
            try:
                result = GeminiModel._stream_generate(self, model_name, contents, config, on_chunk)
                _log.info("RoundRobin: %s succeeded", model_name)
                return result
            except Exception as exc:
                error_text = str(exc).lower()
                if "quota" in error_text or "429" in error_text or "resource_exhausted" in error_text:
                    tried.add(model_name)
                    _log.info("RoundRobin: %s rate limited, rotating...", model_name)
                    continue
                if "api key" in error_text or "403" in error_text:
                    raise ModelError(f"Authentication failed: {exc}") from exc
                tried.add(model_name)
                _log.warning("RoundRobin: %s failed: %s, rotating...", model_name, exc)
                continue

        # All models rate limited — wait and retry the primary
        _log.warning("RoundRobin: ALL %d models rate limited — waiting 30s", len(self._models))
        time.sleep(30)
        model_name = self._next_model()
        return GeminiModel._stream_generate(self, model_name, contents, config, on_chunk)

    def append_user_message(self, conv: Conversation, text: str) -> None:
        conv._messages.append({"role": "user", "text": text})

    def append_assistant_turn(self, conv: Conversation, turn: ModelTurn) -> None:
        conv._messages.append({"role": "assistant", "turn": turn})

    def append_tool_results(self, conv: Conversation, results: list[ToolResult]) -> None:
        conv._messages.append({"role": "tool_results", "results": results})

    # Borrow methods from GeminiModel
    _build_contents = GeminiModel._build_contents
    _stream_generate = GeminiModel._stream_generate
    estimate_tokens = GeminiModel.estimate_tokens


class AnthropicModel:
    """Claude model via Anthropic SDK — supports tool calling and streaming."""

    def __init__(self, model: str, api_key: str):
        import anthropic
        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key)

    def context_window(self) -> int:
        return _ANTHROPIC_CONTEXT_WINDOWS.get(self.model, 200_000)

    def create_conversation(
        self, system_prompt: str, tool_defs: list[dict[str, Any]],
    ) -> Conversation:
        return Conversation(system_prompt=system_prompt, tool_defs=tool_defs)

    def generate(
        self, conversation: Conversation,
        on_chunk: Callable[[str], None] | None = None,
        _max_retries: int = 3,
    ) -> ModelTurn:
        messages = self._build_messages(conversation)
        tools = self._tool_defs_to_anthropic(conversation.tool_defs) if conversation.tool_defs else None

        last_exc: Exception | None = None
        for attempt in range(_max_retries):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "max_tokens": 16384,
                    "messages": messages,
                }
                if conversation.system_prompt:
                    kwargs["system"] = conversation.system_prompt
                if tools:
                    kwargs["tools"] = tools

                text_parts: list[str] = []
                tool_calls: list[ToolCall] = []
                usage = TokenUsage()

                with self._client.messages.stream(**kwargs) as stream:
                    for event in stream:
                        if hasattr(event, 'type'):
                            if event.type == 'content_block_delta':
                                if hasattr(event.delta, 'text'):
                                    text_parts.append(event.delta.text)
                                    if on_chunk:
                                        on_chunk(event.delta.text)

                    # Get final message
                    response = stream.get_final_message()

                # Extract tool calls from final response
                for block in response.content:
                    if block.type == "tool_use":
                        tool_calls.append(ToolCall(
                            id=block.id,
                            name=block.name,
                            arguments=block.input if isinstance(block.input, dict) else {},
                        ))
                    elif block.type == "text" and not text_parts:
                        text_parts.append(block.text)

                usage.input_tokens = response.usage.input_tokens
                usage.output_tokens = response.usage.output_tokens

                return ModelTurn(
                    text="".join(text_parts),
                    tool_calls=tool_calls,
                    usage=usage,
                )

            except Exception as exc:
                error_text = str(exc).lower()

                if "authentication" in error_text or "401" in error_text:
                    raise ModelError(f"Anthropic auth failed: {exc}") from exc

                if "rate_limit" in error_text or "429" in error_text or "overloaded" in error_text:
                    last_exc = exc
                    wait = min(2 ** attempt * 3 + 2, 30)  # 5, 8, 11, ... cap 30s
                    _log.warning("Anthropic rate limited (attempt %d/%d), retrying in %ds...", attempt + 1, _max_retries, wait)
                    time.sleep(wait)
                    continue

                if attempt == 0:
                    last_exc = exc
                    _log.warning("Anthropic API error (attempt 1), retrying in 3s: %s", exc)
                    time.sleep(3)
                    continue
                raise ModelError(f"Anthropic API error: {exc}") from exc

        raise RateLimitError(f"Rate limited after {_max_retries} retries.") from last_exc

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
        """Convert conversation to Anthropic messages format."""
        messages: list[dict[str, Any]] = []
        for msg in conv._messages:
            role = msg.get("role", "")

            if role == "user":
                text = msg.get("text", "")
                if text:
                    messages.append({"role": "user", "content": text})

            elif role == "assistant":
                content: list[dict[str, Any]] = []
                if msg.get("text"):
                    content.append({"type": "text", "text": msg["text"]})
                for tc in msg.get("tool_calls", []):
                    name = tc.get("name")
                    if name:
                        content.append({
                            "type": "tool_use",
                            "id": tc.get("id", f"call_{uuid.uuid4().hex[:12]}"),
                            "name": name,
                            "input": tc.get("args", {}),
                        })
                if content:
                    messages.append({"role": "assistant", "content": content})

            elif role == "tool":
                content = []
                for r in msg.get("results", []):
                    content.append({
                        "type": "tool_result",
                        "tool_use_id": r.get("tool_call_id", ""),
                        "content": r.get("content", ""),
                    })
                if content:
                    messages.append({"role": "user", "content": content})

        return messages

    @staticmethod
    def _tool_defs_to_anthropic(tool_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert ARA tool definitions to Anthropic tool format."""
        tools = []
        for td in tool_defs:
            tool = {
                "name": td["name"],
                "description": td.get("description", ""),
                "input_schema": td.get("parameters", {"type": "object", "properties": {}}),
            }
            tools.append(tool)
        return tools


# ── OpenAI (GPT) ──────────────────────────────────────────────────────

_OPENAI_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-5.4": 200_000,
    "gpt-5.4-pro": 200_000,
    "gpt-5.3": 200_000,
    "gpt-5.2": 200_000,
    "gpt-5.1": 200_000,
    "gpt-5": 128_000,
    "gpt-4.1": 1_000_000,
    "gpt-4o": 128_000,
    "o3": 200_000,
    "o4-mini": 200_000,
}


class OpenAIModel:
    """OpenAI GPT model — supports tool calling and streaming."""

    def __init__(self, model: str, api_key: str):
        from openai import OpenAI
        self.model = model
        self._client = OpenAI(api_key=api_key)

    def context_window(self) -> int:
        return _OPENAI_CONTEXT_WINDOWS.get(self.model, 128_000)

    def create_conversation(
        self, system_prompt: str, tool_defs: list[dict[str, Any]],
    ) -> Conversation:
        return Conversation(system_prompt=system_prompt, tool_defs=tool_defs)

    def generate(
        self, conversation: Conversation,
        on_chunk: Callable[[str], None] | None = None,
        _max_retries: int = 5,
    ) -> ModelTurn:
        messages = self._build_messages(conversation)
        tools = self._tool_defs_to_openai(conversation.tool_defs) if conversation.tool_defs else None

        last_exc: Exception | None = None
        for attempt in range(_max_retries):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "stream": True,
                }
                if tools:
                    kwargs["tools"] = tools

                text_parts: list[str] = []
                tool_calls_map: dict[int, dict[str, Any]] = {}
                usage = TokenUsage()

                stream = self._client.chat.completions.create(**kwargs)
                for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue

                    if delta.content:
                        text_parts.append(delta.content)
                        if on_chunk:
                            on_chunk(delta.content)

                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_map:
                                tool_calls_map[idx] = {
                                    "id": tc_delta.id or f"call_{uuid.uuid4().hex[:12]}",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    tool_calls_map[idx]["name"] = tc_delta.function.name
                                if tc_delta.function.arguments:
                                    tool_calls_map[idx]["arguments"] += tc_delta.function.arguments

                    if hasattr(chunk, 'usage') and chunk.usage:
                        usage.input_tokens = chunk.usage.prompt_tokens or 0
                        usage.output_tokens = chunk.usage.completion_tokens or 0

                tool_calls: list[ToolCall] = []
                for tc_data in tool_calls_map.values():
                    try:
                        args = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls.append(ToolCall(
                        id=tc_data["id"],
                        name=tc_data["name"],
                        arguments=args,
                    ))

                return ModelTurn(
                    text="".join(text_parts),
                    tool_calls=tool_calls,
                    usage=usage,
                )

            except Exception as exc:
                error_text = str(exc).lower()

                if "authentication" in error_text or "401" in error_text or "invalid api key" in error_text:
                    raise ModelError(f"OpenAI auth failed: {exc}") from exc

                if "rate_limit" in error_text or "429" in error_text:
                    last_exc = exc
                    wait = min(2 ** attempt * 2, 60)
                    _log.warning("OpenAI rate limited (attempt %d/%d), retrying in %ds...",
                                 attempt + 1, _max_retries, wait)
                    if on_chunk:
                        on_chunk(f"\n[Rate limited — retrying in {wait}s...]\n")
                    time.sleep(wait)
                    continue

                if attempt == 0:
                    last_exc = exc
                    _log.warning("OpenAI API error (attempt 1), retrying in 3s: %s", exc)
                    time.sleep(3)
                    continue
                raise ModelError(f"OpenAI API error: {exc}") from exc

        raise RateLimitError(f"OpenAI rate limited after {_max_retries} retries.") from last_exc

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
        """Convert conversation to OpenAI messages format."""
        messages: list[dict[str, Any]] = []

        if conv.system_prompt:
            messages.append({"role": "system", "content": conv.system_prompt})

        for msg in conv._messages:
            role = msg.get("role", "")

            if role == "user":
                text = msg.get("text", "")
                if text:
                    messages.append({"role": "user", "content": text})

            elif role == "assistant":
                m: dict[str, Any] = {"role": "assistant"}
                if msg.get("text"):
                    m["content"] = msg["text"]
                tcs = msg.get("tool_calls", [])
                if tcs:
                    m["tool_calls"] = [
                        {
                            "id": tc.get("id", f"call_{uuid.uuid4().hex[:12]}"),
                            "type": "function",
                            "function": {
                                "name": tc.get("name", ""),
                                "arguments": json.dumps(tc.get("args", {})),
                            },
                        }
                        for tc in tcs
                    ]
                messages.append(m)

            elif role == "tool":
                for r in msg.get("results", []):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": r.get("tool_call_id", ""),
                        "content": r.get("content", ""),
                    })

        return messages

    @staticmethod
    def _tool_defs_to_openai(tool_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert ARA tool definitions to OpenAI function-calling format."""
        tools = []
        for td in tool_defs:
            tools.append({
                "type": "function",
                "function": {
                    "name": td["name"],
                    "description": td.get("description", ""),
                    "parameters": td.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return tools


# ── Load-Balanced Model (round-robin across providers) ────────────────

class LoadBalancedModel:
    """Distributes calls across multiple models with configurable weights.

    Each call randomly picks a model based on weight distribution.
    If the chosen model fails, falls back to the other(s).
    """

    def __init__(self, models: list[tuple[Any, float]]):
        """models: list of (model_instance, weight) tuples. Weights don't need to sum to 1."""
        if not models:
            raise ValueError("LoadBalancedModel requires at least one model")
        self._models = models
        total = sum(w for _, w in models)
        self._weights = [w / total for _, w in models]
        self.model = " | ".join(
            f"{m.model}({w:.0%})" for m, w in zip([m for m, _ in models], self._weights)
        )

    def _pick(self) -> tuple[Any, list[Any]]:
        """Pick a primary model and return (primary, fallbacks)."""
        idx = random.choices(range(len(self._models)), weights=self._weights, k=1)[0]
        primary = self._models[idx][0]
        fallbacks = [m for i, (m, _) in enumerate(self._models) if i != idx]
        return primary, fallbacks

    def context_window(self) -> int:
        return min(m.context_window() for m, _ in self._models)

    def create_conversation(
        self, system_prompt: str, tool_defs: list[dict[str, Any]],
    ) -> Conversation:
        return Conversation(system_prompt=system_prompt, tool_defs=tool_defs)

    def generate(
        self, conversation: Conversation,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ModelTurn:
        primary, fallbacks = self._pick()
        _log.info("LoadBalanced: picked %s", primary.model)
        try:
            return primary.generate(conversation, on_chunk=on_chunk)
        except (ModelError, RateLimitError) as exc:
            _log.warning("LoadBalanced: %s failed (%s), trying fallback...", primary.model, exc)
            for fb in fallbacks:
                try:
                    _log.info("LoadBalanced: falling back to %s", fb.model)
                    if on_chunk:
                        on_chunk(f"\n[LoadBalanced fallback: {fb.model}]\n")
                    return fb.generate(conversation, on_chunk=on_chunk)
                except (ModelError, RateLimitError) as fb_exc:
                    _log.warning("LoadBalanced: fallback %s also failed: %s", fb.model, fb_exc)
                    continue
            raise

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
