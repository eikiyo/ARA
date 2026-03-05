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

ModelFactory = Callable[[str, str | None], BaseModel]


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
        model_factory: ModelFactory | None = None,
    ):
        self.model = model
        self.tools = tools
        self.config = config
        self.model_factory = model_factory
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
            return f"[Max depth {self.config.max_depth} reached]"

        # Build system prompt
        if system_prompt_override:
            system_prompt = system_prompt_override
        elif depth == 0:
            system_prompt = build_system_prompt(
                topic=context.topic,
                paper_type=context.paper_type,
                rules=context.rules,
            )
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

        while steps < self.config.max_steps_per_call:
            if self.cancel_flag:
                return last_text or "[Cancelled]"

            elapsed = time.time() - start_time
            if elapsed > self.config.max_solve_seconds:
                return last_text or f"[Timeout after {int(elapsed)}s]"

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
                return last_text or f"[Model error: {exc}]"

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

            # Append assistant turn
            self.model.append_assistant_turn(conversation, turn)

            # Execute tool calls
            results = self._execute_tools(
                turn.tool_calls, context, depth, on_event,
            )

            # Append results
            self.model.append_tool_results(conversation, results)

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
                result_text = json.dumps({"error": str(exc)})

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
            # Try to infer phase from objective keywords
            phase_prompt = self._infer_phase_prompt(objective, context)

        # Select model for subtask
        model_for_subtask = self.model
        requested_model = tc.arguments.get("model")
        if requested_model and self.model_factory:
            try:
                model_for_subtask = self.model_factory(requested_model, None)
            except Exception:
                _log.warning("Failed to create model '%s', using default", requested_model)

        # Run subtask recursively
        old_model = self.model
        self.model = model_for_subtask
        try:
            result = self._solve_recursive(
                objective=objective,
                context=context,
                depth=depth + 1,
                on_event=on_event,
                system_prompt_override=phase_prompt,
            )
        finally:
            self.model = old_model

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
        keyword_map = {
            "scout": "scout",
            "paper discovery": "scout",
            "search all": "scout",
            "triage": "analyst_triage",
            "rank": "analyst_triage",
            "deep read": "analyst_deep_read",
            "extract claims": "analyst_deep_read",
            "verif": "verifier",
            "retraction": "verifier",
            "hypothesis": "hypothesis",
            "branch": "brancher",
            "cross-domain": "brancher",
            "critic": "critic",
            "evaluat": "critic",
            "write": "writer",
            "draft": "writer",
            "outline": "writer",
        }
        for keyword, phase in keyword_map.items():
            if keyword in obj_lower:
                return build_phase_system_prompt(
                    phase=phase, topic=context.topic, rules=context.rules,
                )
        return None

    def _condense(self, conversation: Conversation, context: ExternalContext) -> None:
        _log.info("Condensing conversation (estimated tokens exceeds 75%% of context window)")
        summary_parts = []
        if context.observations:
            summary_parts.append("Key observations:\n" + "\n".join(
                f"- {o}" for o in context.observations[-30:]
            ))
        summary = "\n".join(summary_parts) if summary_parts else "Previous context condensed."
        self.model.condense_conversation(conversation, summary)
