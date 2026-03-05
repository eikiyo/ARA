# Location: ara/engine.py
# Purpose: Recursive LLM engine — tool dispatch, subtask delegation, context management
# Functions: RLMEngine, ExternalContext, StepCallback, TurnSummary
# Calls: model.py, tools/__init__.py, prompts/__init__.py
# Imports: dataclasses, time, logging, json

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import ARAConfig
from .model import (
    BaseModel, Conversation, ModelTurn, ToolCall, ToolResult,
    ModelError, TokenUsage,
)
from .prompts import build_system_prompt, build_phase_system_prompt, PHASE_PROMPTS
from .tools import ARATools

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class TurnSummary:
    role: str
    text: str
    tool_names: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExternalContext:
    topic: str = ""
    paper_type: str = "research_article"
    rules: list[dict[str, Any]] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    turn_history: list[TurnSummary] = field(default_factory=list)


@dataclass(slots=True)
class StepEvent:
    event_type: str  # "thinking", "tool_call", "tool_result", "text", "error", "subtask_start", "subtask_end"
    data: str = ""
    tool_name: str = ""
    depth: int = 0

StepCallback = Callable[[StepEvent], None]


class RLMEngine:
    def __init__(
        self,
        model: BaseModel,
        tools: ARATools,
        config: ARAConfig,
    ):
        self.model = model
        self.tools = tools
        self.config = config
        self.cancel_flag = False
        self._total_tokens = TokenUsage()

    @property
    def total_tokens(self) -> TokenUsage:
        return self._total_tokens

    def solve(
        self,
        objective: str,
        context: ExternalContext | None = None,
        on_event: StepCallback | None = None,
    ) -> str:
        ctx = context or ExternalContext()
        return self._solve_recursive(
            objective=objective,
            context=ctx,
            depth=0,
            on_event=on_event,
        )

    def _solve_recursive(
        self,
        objective: str,
        context: ExternalContext,
        depth: int,
        on_event: StepCallback | None = None,
        system_prompt_override: str | None = None,
    ) -> str:
        if depth > self.config.max_depth:
            return json.dumps({"error": f"Max depth {self.config.max_depth} reached"})

        # Build system prompt
        if system_prompt_override:
            system_prompt = system_prompt_override
        else:
            system_prompt = build_system_prompt(
                topic=context.topic,
                paper_type=context.paper_type,
                rules=context.rules,
                include_delegation=depth < self.config.max_depth,
            )

        # Create conversation
        tool_defs = self.tools.get_definitions(
            include_subtask=depth < self.config.max_depth,
            depth=depth,
        )
        conversation = self.model.create_conversation(
            system_prompt=system_prompt,
            tool_defs=tool_defs,
        )

        # Inject context from previous turns
        context_text = objective
        if context.observations:
            obs_text = "\n".join(f"- {o}" for o in context.observations[-20:])
            context_text += f"\n\n[Previous observations]\n{obs_text}"

        self.model.append_user_message(conversation, context_text)

        # Main loop
        last_text = ""
        steps = 0
        start_time = time.time()
        _recent_tool_sigs: list[str] = []  # Track recent tool call signatures for loop detection
        _MAX_REPEAT = 3  # Break if same tool signature repeats this many times

        while steps < self.config.max_steps_per_call:
            if self.cancel_flag:
                return last_text or "[Cancelled]"

            elapsed = time.time() - start_time
            if elapsed > self.config.max_solve_seconds:
                return last_text or json.dumps({"error": f"Timeout after {int(elapsed)}s"})

            steps += 1

            # Generate
            if on_event:
                on_event(StepEvent("thinking", depth=depth))

            try:
                def _on_chunk(text: str) -> None:
                    if on_event:
                        on_event(StepEvent("text", data=text, depth=depth))

                turn = self.model.generate(conversation, on_chunk=_on_chunk)
            except ModelError as exc:
                _log.error("Model error at depth %d: %s", depth, exc)
                if on_event:
                    on_event(StepEvent("error", data=str(exc), depth=depth))
                return last_text or json.dumps({"error": f"Model error: {exc}"})

            # Track tokens
            if turn.usage:
                self._total_tokens.input_tokens += turn.usage.input_tokens
                self._total_tokens.output_tokens += turn.usage.output_tokens

            if turn.text:
                last_text = turn.text

            # No tool calls → done
            if not turn.tool_calls:
                if turn.text:
                    self.model.append_assistant_turn(conversation, turn)
                break

            # Cap tool calls per turn to prevent runaway behavior
            capped_calls = turn.tool_calls[:self.config.max_tool_calls_per_turn]
            if len(turn.tool_calls) > self.config.max_tool_calls_per_turn:
                _log.warning(
                    "Capped tool calls from %d to %d",
                    len(turn.tool_calls), self.config.max_tool_calls_per_turn,
                )

            # Append assistant turn (with only the capped calls)
            capped_turn = ModelTurn(
                text=turn.text,
                tool_calls=capped_calls,
                usage=turn.usage,
            )
            self.model.append_assistant_turn(conversation, capped_turn)

            # Execute tool calls
            results = self._execute_tools(
                capped_calls, context, depth, on_event,
            )

            # Append results
            self.model.append_tool_results(conversation, results)

            # Loop detection: break if model keeps calling the same tools
            turn_sig = "|".join(sorted(tc.name for tc in capped_calls))
            _recent_tool_sigs.append(turn_sig)
            if len(_recent_tool_sigs) > _MAX_REPEAT:
                _recent_tool_sigs = _recent_tool_sigs[-_MAX_REPEAT:]
            if len(_recent_tool_sigs) == _MAX_REPEAT and len(set(_recent_tool_sigs)) == 1:
                _log.warning("Loop detected: same tool pattern %r repeated %d times, breaking", turn_sig, _MAX_REPEAT)
                self.model.append_user_message(
                    conversation,
                    "STOP. You are repeating the same tool calls in a loop. "
                    "Summarize your findings and respond with text. Do NOT call any more tools.",
                )
                # Give model one more chance to produce text
                try:
                    final = self.model.generate(conversation, on_chunk=_on_chunk)
                    if final.text:
                        last_text = final.text
                    if final.usage:
                        self._total_tokens.input_tokens += final.usage.input_tokens
                        self._total_tokens.output_tokens += final.usage.output_tokens
                except ModelError:
                    pass
                break

            # Context condensation check
            estimated_tokens = self.model.estimate_tokens(conversation)
            window = self.model.context_window()
            if estimated_tokens > window * 0.75:
                self._condense(conversation, context)

        return last_text or "[No response generated]"

    def _execute_tools(
        self,
        tool_calls: list[ToolCall],
        context: ExternalContext,
        depth: int,
        on_event: StepCallback | None,
    ) -> list[ToolResult]:
        results: list[ToolResult] = []

        for tc in tool_calls:
            if self.cancel_flag:
                results.append(ToolResult(
                    tool_call_id=tc.id, name=tc.name or "cancelled",
                    content='{"status": "cancelled"}',
                ))
                break

            if not tc.name:
                continue

            if on_event:
                on_event(StepEvent(
                    "tool_call", data=json.dumps(tc.arguments)[:200],
                    tool_name=tc.name, depth=depth,
                ))

            # Handle subtask delegation
            if tc.name == "subtask":
                result_text = self._handle_subtask(tc, context, depth, on_event)
                results.append(ToolResult(
                    tool_call_id=tc.id, name="subtask", content=result_text,
                ))
                continue

            # Handle execute (lightweight subtask)
            if tc.name == "execute":
                result_text = self._handle_execute(tc, context, depth, on_event)
                results.append(ToolResult(
                    tool_call_id=tc.id, name="execute", content=result_text,
                ))
                continue

            # Regular tool dispatch
            try:
                result_text = self.tools.dispatch(tc.name, tc.arguments)
            except Exception as exc:
                _log.exception("Tool dispatch failed: %s", tc.name)
                result_text = json.dumps({"error": f"Tool '{tc.name}' failed: {exc}"})

            # Store observation
            obs = f"[{tc.name}] {result_text[:300]}"
            context.observations.append(obs)
            if len(context.observations) > 100:
                context.observations = context.observations[-50:]

            if on_event:
                on_event(StepEvent(
                    "tool_result", data=result_text[:200],
                    tool_name=tc.name, depth=depth,
                ))

            results.append(ToolResult(
                tool_call_id=tc.id, name=tc.name, content=result_text,
            ))

        return results

    def _handle_subtask(
        self,
        tc: ToolCall,
        context: ExternalContext,
        depth: int,
        on_event: StepCallback | None,
    ) -> str:
        objective = tc.arguments.get("objective", "")
        phase = tc.arguments.get("prompt", "")

        if on_event:
            on_event(StepEvent("subtask_start", data=objective[:200], depth=depth))

        # Resolve phase-specific system prompt
        phase_prompt = None
        if phase and phase in PHASE_PROMPTS:
            phase_prompt = build_phase_system_prompt(
                phase=phase,
                topic=context.topic,
                rules=context.rules,
            )
        elif not phase:
            phase_prompt = self._infer_phase_prompt(objective, context)

        # Run subtask recursively
        try:
            result = self._solve_recursive(
                objective=objective,
                context=context,
                depth=depth + 1,
                on_event=on_event,
                system_prompt_override=phase_prompt,
            )
        except Exception as exc:
            _log.exception("Subtask failed: %s", objective[:100])
            result = json.dumps({"error": f"Subtask failed: {exc}", "objective": objective[:200]})

        # Detect structured error responses from recursive calls
        if result.startswith('{"error":'):
            _log.warning("Subtask returned error: %s", result[:200])

        if on_event:
            on_event(StepEvent("subtask_end", data=result[:200], depth=depth))

        return result

    def _handle_execute(
        self,
        tc: ToolCall,
        context: ExternalContext,
        depth: int,
        on_event: StepCallback | None,
    ) -> str:
        objective = tc.arguments.get("objective", "")
        return self._solve_recursive(
            objective=objective,
            context=context,
            depth=depth + 1,
            on_event=on_event,
        )

    def _infer_phase_prompt(self, objective: str, context: ExternalContext) -> str | None:
        obj_lower = objective.lower()
        # Longest keywords first to avoid false matches
        keyword_map = [
            ("paper discovery", "scout"),
            ("search all", "scout"),
            ("extract claims", "analyst_deep_read"),
            ("deep read", "analyst_deep_read"),
            ("cross-domain", "brancher"),
            ("scout", "scout"),
            ("triage", "analyst_triage"),
            ("rank", "analyst_triage"),
            ("retraction", "verifier"),
            ("verif", "verifier"),
            ("hypothesis", "hypothesis"),
            ("branch", "brancher"),
            ("critic", "critic"),
            ("evaluat", "critic"),
            ("outline", "writer"),
            ("draft", "writer"),
            ("write", "writer"),
        ]
        for keyword, phase in keyword_map:
            if keyword in obj_lower:
                return build_phase_system_prompt(
                    phase=phase, topic=context.topic, rules=context.rules,
                )
        return None

    def _condense(self, conversation: Conversation, context: ExternalContext) -> None:
        _log.info("Condensing conversation (estimated tokens exceeds 75%% of context window)")
        summary_parts = []

        # Preserve turn history summary
        if context.turn_history:
            recent = context.turn_history[-10:]
            turns_text = "\n".join(
                f"- [{t.role}] {t.text[:200]}" + (f" (tools: {', '.join(t.tool_names)})" if t.tool_names else "")
                for t in recent
            )
            summary_parts.append(f"Recent turns:\n{turns_text}")

        if context.observations:
            summary_parts.append("Key observations:\n" + "\n".join(
                f"- {o}" for o in context.observations[-30:]
            ))

        summary = "\n\n".join(summary_parts) if summary_parts else "Previous context condensed — continue from current state."
        self.model.condense_conversation(conversation, summary)
