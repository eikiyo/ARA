# Location: ara/engine.py
# Purpose: Recursive LLM engine — tool dispatch, subtask delegation, context management
# Functions: RLMEngine, ExternalContext, StepCallback, TurnSummary
# Calls: model.py, tools/__init__.py, prompts/__init__.py
# Imports: dataclasses, time, logging, json

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import ARAConfig
from .model import (
    BaseModel, Conversation, ModelTurn, ToolCall, ToolResult,
    ModelError, RateLimitError, TokenUsage,
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
        writer_model: BaseModel | None = None,
    ):
        self.model = model
        self.writer_model = writer_model or model
        self.tools = tools
        self.config = config
        self.cancel_flag = threading.Event()
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
        # Reset per-solve state
        from .tools.search import reset_search_all_state
        reset_search_all_state()

        _log.info("=" * 60)
        _log.info("SOLVE START — objective: %s", objective[:120])
        _log.info("=" * 60)

        ctx = context or ExternalContext()
        try:
            result = self._solve_recursive(
                objective=objective,
                context=ctx,
                depth=0,
                on_event=on_event,
            )
            _log.info("SOLVE END — result length: %d chars", len(result))
            return result
        except RateLimitError as exc:
            return f"[Rate limit] API quota exhausted. Wait a few minutes and try again.\n({exc})"

    def _solve_recursive(
        self,
        objective: str,
        context: ExternalContext,
        depth: int,
        on_event: StepCallback | None = None,
        system_prompt_override: str | None = None,
        phase: str = "",
    ) -> str:
        if depth > self.config.max_depth:
            _log.warning("MAX DEPTH %d reached — returning error", self.config.max_depth)
            return json.dumps({"error": f"Max depth {self.config.max_depth} reached"})

        # Select active model based on phase
        active_model = self.writer_model if phase in ("writer", "paper_critic", "synthesis") else self.model
        model_name = getattr(active_model, 'model', 'unknown')
        _log.info("-" * 50)
        _log.info("_solve_recursive START | depth=%d | phase=%s | model=%s", depth, phase or "manager", model_name)
        _log.info("  objective: %s", objective[:150])

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
            phase=phase,
        )
        conversation = active_model.create_conversation(
            system_prompt=system_prompt,
            tool_defs=tool_defs,
        )

        # Inject context from previous turns
        context_text = objective
        if context.observations:
            obs_text = "\n".join(f"- {o}" for o in context.observations[-20:])
            context_text += f"\n\n[Previous observations]\n{obs_text}"

        active_model.append_user_message(conversation, context_text)

        # Main loop
        last_text = ""
        steps = 0
        start_time = time.time()
        _result_cache: dict[str, str] = {}  # Cache: "name:args_json" → result
        _recent_tool_sigs: list[str] = []
        _tool_call_counts: dict[str, int] = {}
        _MAX_REPEAT = 3
        _MAX_SINGLE_TOOL = 30

        while steps < self.config.max_steps_per_call:
            if self.cancel_flag.is_set():
                _log.info("  CANCELLED at step %d", steps)
                return last_text or "[Cancelled]"

            elapsed = time.time() - start_time
            if elapsed > self.config.max_solve_seconds:
                _log.warning("  TIMEOUT at step %d after %ds", steps, int(elapsed))
                return last_text or json.dumps({"error": f"Timeout after {int(elapsed)}s"})

            steps += 1
            _log.info("  step %d/%d | depth=%d | phase=%s | elapsed=%.0fs",
                       steps, self.config.max_steps_per_call, depth, phase or "manager", elapsed)

            # Generate
            if on_event:
                on_event(StepEvent("thinking", depth=depth))

            try:
                def _on_chunk(text: str) -> None:
                    if on_event:
                        on_event(StepEvent("text", data=text, depth=depth))

                turn = active_model.generate(conversation, on_chunk=_on_chunk)
            except RateLimitError as exc:
                # Rate limit exhausted — propagate up to stop the entire pipeline
                _log.error("Rate limit exhausted at depth %d: %s", depth, exc)
                if on_event:
                    on_event(StepEvent("error", data=str(exc), depth=depth))
                raise  # Let it bubble up to the top-level solve()
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
                _log.info("  NO TOOL CALLS at step %d — ending loop | text=%d chars", steps, len(turn.text or ""))
                if turn.text:
                    active_model.append_assistant_turn(conversation, turn)
                break

            # Pre-dedup spam detection: count DUPLICATE calls (same name+args)
            _sig_counts: dict[str, int] = {}
            for tc in turn.tool_calls:
                sig = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
                _sig_counts[sig] = _sig_counts.get(sig, 0) + 1

            loop_detected = False
            for sig, count in _sig_counts.items():
                if count > 3:  # Same exact call repeated 3+ times = spam
                    tool_name = sig.split(":")[0]
                    _log.warning("Gemini sent identical call %r %d times in one turn — forcing stop", tool_name, count)
                    loop_detected = True
                    break

            if loop_detected:
                active_model.append_assistant_turn(conversation, ModelTurn(text=turn.text, usage=turn.usage))
                active_model.append_user_message(
                    conversation,
                    "STOP. You generated duplicate tool calls. "
                    "Summarize your findings and respond with text only.",
                )
                try:
                    final = active_model.generate(conversation, on_chunk=_on_chunk)
                    if final.text:
                        last_text = final.text
                    if final.usage:
                        self._total_tokens.input_tokens += final.usage.input_tokens
                        self._total_tokens.output_tokens += final.usage.output_tokens
                except ModelError:
                    pass
                break

            # Dedup identical calls
            seen_sigs: set[str] = set()
            unique_calls: list[ToolCall] = []
            for tc in turn.tool_calls:
                sig = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
                if sig not in seen_sigs:
                    seen_sigs.add(sig)
                    unique_calls.append(tc)

            # Cap after dedup — strictly 1 tool per turn (serial execution)
            capped_calls = unique_calls[:self.config.max_tool_calls_per_turn]
            dropped = len(unique_calls) - len(capped_calls)
            for tc in capped_calls:
                _log.info("  TOOL CALL: %s(%s)", tc.name, json.dumps(tc.arguments, default=str)[:120])
            if dropped > 0:
                _log.info("  DROPPED %d extra tool calls (serial mode)", dropped)

            # Append assistant turn (with only the capped calls)
            capped_turn = ModelTurn(text=turn.text, tool_calls=capped_calls, usage=turn.usage)
            active_model.append_assistant_turn(conversation, capped_turn)

            # Execute (cache prevents re-execution, no event for cached calls)
            results = self._execute_tools(
                capped_calls, context, depth, on_event, _result_cache,
            )
            active_model.append_tool_results(conversation, results)

            # Tell model about dropped calls so it doesn't re-send them blindly
            if dropped > 0:
                active_model.append_user_message(
                    conversation,
                    f"[System] {dropped} extra tool call(s) were dropped. Execute ONE tool at a time. "
                    "Wait for each result before calling the next tool.",
                )

            # Loop detection across turns — include args hash so subtask(scout) ≠ subtask(triage)
            turn_sig = "|".join(
                f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)[:300]}"
                for tc in capped_calls
            )
            _recent_tool_sigs.append(turn_sig)
            _DELEGATION_TOOLS = {"subtask", "execute"}
            for tc in capped_calls:
                if tc.name not in _DELEGATION_TOOLS:
                    _tool_call_counts[tc.name] = _tool_call_counts.get(tc.name, 0) + 1

            # Check: same EXACT pattern (name+args) 2 turns in a row
            if len(_recent_tool_sigs) >= 2 and _recent_tool_sigs[-1] == _recent_tool_sigs[-2]:
                _log.warning("Loop: pattern repeated 2 turns: %s", turn_sig[:120])
                loop_detected = True

            # Check: any non-delegation tool > MAX total
            if not loop_detected:
                for name, count in _tool_call_counts.items():
                    if count >= _MAX_SINGLE_TOOL:
                        _log.warning("Tool %r hit %d calls", name, count)
                        loop_detected = True
                        break

            if loop_detected:
                active_model.append_user_message(
                    conversation,
                    "STOP. You are looping. Summarize findings and respond with text. No more tools.",
                )
                try:
                    final = active_model.generate(conversation, on_chunk=_on_chunk)
                    if final.text:
                        last_text = final.text
                    if final.usage:
                        self._total_tokens.input_tokens += final.usage.input_tokens
                        self._total_tokens.output_tokens += final.usage.output_tokens
                except ModelError:
                    pass
                break

            # Context condensation check
            estimated_tokens = active_model.estimate_tokens(conversation)
            window = active_model.context_window()
            if estimated_tokens > window * 0.75:
                self._condense(conversation, context, _result_cache, active_model)

        return last_text or "[No response generated]"

    def _execute_tools(
        self,
        tool_calls: list[ToolCall],
        context: ExternalContext,
        depth: int,
        on_event: StepCallback | None,
        result_cache: dict[str, str] | None = None,
    ) -> list[ToolResult]:
        results: list[ToolResult] = []
        cache = result_cache if result_cache is not None else {}

        for tc in tool_calls:
            if self.cancel_flag.is_set():
                results.append(ToolResult(
                    tool_call_id=tc.id, name=tc.name or "cancelled",
                    content='{"status": "cancelled"}',
                ))
                break

            if not tc.name:
                continue

            # Cache check — same tool+args never executes twice (no event, no dispatch)
            cache_key = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
            if cache_key in cache:
                results.append(ToolResult(
                    tool_call_id=tc.id, name=tc.name, content=cache[cache_key],
                ))
                continue

            # Event fires only for NEW (uncached) tool calls
            if on_event:
                on_event(StepEvent(
                    "tool_call", data=json.dumps(tc.arguments)[:200],
                    tool_name=tc.name, depth=depth,
                ))

            # Handle subtask delegation
            if tc.name == "subtask":
                result_text = self._handle_subtask(tc, context, depth, on_event)
                cache[cache_key] = result_text
                results.append(ToolResult(
                    tool_call_id=tc.id, name="subtask", content=result_text,
                ))
                continue

            # Handle execute (lightweight subtask)
            if tc.name == "execute":
                result_text = self._handle_execute(tc, context, depth, on_event)
                cache[cache_key] = result_text
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

            # Cache the result
            cache[cache_key] = result_text

            # Store observation
            obs = f"[{tc.name}] {result_text[:300]}"
            context.observations.append(obs)
            if len(context.observations) > 100:
                context.observations = context.observations[-80:]

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
        _log.info("=" * 40)
        _log.info("SUBTASK START | depth=%d->%d | phase=%s", depth, depth + 1, phase or "inferred")
        _log.info("  objective: %s", objective[:200])

        if on_event:
            on_event(StepEvent("subtask_start", data=objective[:200], depth=depth))

        # Reset search state for search-heavy phases
        if phase and any(x in phase.lower() for x in ("scout", "brancher", "search")):
            from .tools.search import reset_search_all_state
            reset_search_all_state()

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
                phase=phase,
            )
        except Exception as exc:
            _log.exception("Subtask failed: %s", objective[:100])
            result = json.dumps({"error": f"Subtask failed: {exc}", "objective": objective[:200]})

        # Detect structured error responses from recursive calls
        if result.startswith('{"error":'):
            _log.warning("Subtask returned error: %s", result[:200])

        _log.info("SUBTASK END | depth=%d | phase=%s | result=%d chars", depth, phase or "inferred", len(result))
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
            ("synthesis", "synthesis"),
            ("pre-writer", "synthesis"),
            ("prepare data", "synthesis"),
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

    def _condense(self, conversation: Conversation, context: ExternalContext, result_cache: dict[str, str] | None = None, active_model: BaseModel | None = None) -> None:
        _log.info("Condensing conversation (estimated tokens exceeds 75%% of context window)")
        if result_cache is not None:
            result_cache.clear()
        summary_parts = []

        model = active_model or self.model

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
        model.condense_conversation(conversation, summary)
