# Location: ara/engine.py
# Purpose: Recursive LLM engine — core loop, tool dispatch, subtask delegation
# Functions: RLMEngine (_solve_recursive, _run_one_tool, _apply_tool_call)
# Calls: model.py, prompts/, tools/, config.py, replay_log.py
# Imports: json, re, time, threading, datetime, concurrent.futures, dataclasses

from __future__ import annotations

import json
import time
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import ARAConfig
from .logging import get_logger
from .model import BaseModel, ModelError, ModelTurn, ToolCall, ToolResult
from .prompts import build_system_prompt
from .replay_log import ReplayLogger
from .tools import ARATools
from .tools.defs import get_tool_definitions

_log = get_logger("engine")

EventCallback = Callable[[str], None]
StepCallback = Callable[[dict[str, Any]], None]
ContentDeltaCallback = Callable[[str, str], None]

_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-5-20250929": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "gpt-4o": 128_000,
    "gpt-4.1": 1_000_000,
}
_DEFAULT_CONTEXT_WINDOW = 128_000
_CONDENSATION_THRESHOLD = 0.75


def _summarize_args(args: dict[str, Any], max_len: int = 120) -> str:
    parts: list[str] = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s}")
    joined = ", ".join(parts)
    return joined[:max_len - 3] + "..." if len(joined) > max_len else joined


def _summarize_observation(text: str, max_len: int = 200) -> str:
    first = text.split("\n", 1)[0].strip()
    if len(first) > max_len:
        first = first[:max_len - 3] + "..."
    lines = text.count("\n") + 1
    if lines > 1:
        return f"{first} ({lines} lines, {len(text)} chars)"
    return first


def _model_tier(model_name: str, reasoning_effort: str | None = None) -> int:
    lower = model_name.lower()
    if "opus" in lower:
        return 1
    if "sonnet" in lower:
        return 2
    if "haiku" in lower:
        return 3
    return 2


def _lowest_tier_model(model_name: str) -> tuple[str, str | None]:
    if "claude" in model_name.lower():
        return ("claude-haiku-4-5-20251001", None)
    return (model_name, None)


ModelFactory = Callable[[str, str | None], "BaseModel"]


@dataclass
class ExternalContext:
    observations: list[str] = field(default_factory=list)

    def add(self, text: str) -> None:
        self.observations.append(text)

    def summary(self, max_items: int = 12, max_chars: int = 8000) -> str:
        if not self.observations:
            return "(empty)"
        recent = self.observations[-max_items:]
        joined = "\n\n".join(recent)
        if len(joined) <= max_chars:
            return joined
        return f"{joined[:max_chars]}\n...[truncated]..."


@dataclass
class TurnSummary:
    turn_number: int
    objective: str
    result_preview: str
    timestamp: str
    steps_used: int = 0
    replay_seq_start: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_number": self.turn_number,
            "objective": self.objective,
            "result_preview": self.result_preview,
            "timestamp": self.timestamp,
            "steps_used": self.steps_used,
            "replay_seq_start": self.replay_seq_start,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TurnSummary:
        return cls(
            turn_number=d["turn_number"], objective=d["objective"],
            result_preview=d["result_preview"], timestamp=d["timestamp"],
            steps_used=d.get("steps_used", 0),
            replay_seq_start=d.get("replay_seq_start", 0),
        )


@dataclass
class RLMEngine:
    model: BaseModel
    tools: ARATools
    config: ARAConfig
    system_prompt: str = ""
    session_tokens: dict[str, dict[str, int]] = field(default_factory=dict)
    model_factory: ModelFactory | None = None
    _model_cache: dict[tuple[str, str | None], BaseModel] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    session_dir: Path | None = None
    session_id: str | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        if not self.system_prompt:
            self.system_prompt = build_system_prompt(
                self.config.recursive,
                acceptance_criteria=self.config.acceptance_criteria,
            )
        tool_defs = get_tool_definitions(
            include_subtask=self.config.recursive,
            include_acceptance_criteria=self.config.acceptance_criteria,
        )
        if hasattr(self.model, "tool_defs"):
            self.model.tool_defs = tool_defs

    def cancel(self) -> None:
        self._cancel.set()

    def solve(self, objective: str, on_event: EventCallback | None = None) -> str:
        result, _ = self.solve_with_context(objective=objective, on_event=on_event)
        return result

    def solve_with_context(
        self, objective: str,
        context: ExternalContext | None = None,
        on_event: EventCallback | None = None,
        on_step: StepCallback | None = None,
        on_content_delta: ContentDeltaCallback | None = None,
        replay_logger: ReplayLogger | None = None,
        turn_history: list[TurnSummary] | None = None,
    ) -> tuple[str, ExternalContext]:
        if not objective.strip():
            return "No objective provided.", context or ExternalContext()
        self._cancel.clear()
        active_context = context if context is not None else ExternalContext()
        deadline = (time.monotonic() + self.config.max_solve_seconds) if self.config.max_solve_seconds > 0 else 0
        result = self._solve_recursive(
            objective=objective.strip(), depth=0,
            context=active_context, on_event=on_event,
            on_step=on_step, on_content_delta=on_content_delta,
            deadline=deadline, replay_logger=replay_logger,
            turn_history=turn_history,
        )
        return result, active_context

    def _emit(self, msg: str, on_event: EventCallback | None) -> None:
        if on_event:
            try:
                on_event(msg)
            except Exception:
                pass

    def _clip_observation(self, text: str) -> str:
        mx = self.config.max_observation_chars
        return text if len(text) <= mx else f"{text[:mx]}\n...[truncated {len(text) - mx} chars]..."

    def _judge_result(self, objective: str, criteria: str, result: str, current_model: BaseModel | None = None) -> str:
        if not self.model_factory:
            return "PASS\n(no judge available)"
        cur = current_model or self.model
        cur_name = getattr(cur, "model", "")
        judge_name, judge_effort = _lowest_tier_model(cur_name)
        cache_key = ("_judge_" + judge_name, judge_effort)
        with self._lock:
            if cache_key not in self._model_cache:
                try:
                    self._model_cache[cache_key] = self.model_factory(judge_name, judge_effort)
                except Exception:
                    return "PASS\n(no judge available)"
            judge_model = self._model_cache[cache_key]
        if hasattr(judge_model, "tool_defs"):
            judge_model.tool_defs = []
        truncated = result[:4000] if len(result) > 4000 else result
        prompt = (
            "You are a judge evaluating whether a task result meets acceptance criteria.\n\n"
            f"Objective: {objective}\n\nAcceptance criteria: {criteria}\n\n"
            f"Result:\n{truncated}\n\n"
            "Respond with exactly one line starting with PASS: or FAIL: followed by a brief explanation."
        )
        try:
            conversation = judge_model.create_conversation("You are a concise evaluator.", prompt)
            turn = judge_model.complete(conversation)
            verdict = (turn.text or "").strip()
            return verdict if verdict else "PASS\n(judge returned empty response)"
        except Exception as exc:
            return f"PASS\n(judge error: {exc})"

    def _solve_recursive(
        self, objective: str, depth: int,
        context: ExternalContext,
        on_event: EventCallback | None = None,
        on_step: StepCallback | None = None,
        on_content_delta: ContentDeltaCallback | None = None,
        deadline: float = 0,
        model_override: BaseModel | None = None,
        replay_logger: ReplayLogger | None = None,
        turn_history: list[TurnSummary] | None = None,
    ) -> str:
        model = model_override or self.model
        self._emit(f"[depth {depth}] objective: {objective}", on_event)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        initial_msg_dict: dict[str, Any] = {
            "timestamp": now_iso,
            "objective": objective,
            "depth": depth,
            "max_depth": self.config.max_depth,
            "max_steps_per_call": self.config.max_steps_per_call,
            "workspace": str(self.config.workspace),
            "external_context_summary": context.summary(),
        }
        if self.session_dir is not None:
            initial_msg_dict["session_dir"] = str(self.session_dir)
        if depth == 0 and turn_history:
            # Only include last 3 turns to avoid context bloat
            recent = turn_history[-3:]
            initial_msg_dict["turn_history"] = [t.to_dict() for t in recent]
        initial_message = json.dumps(initial_msg_dict, ensure_ascii=True)
        conversation = model.create_conversation(self.system_prompt, initial_message)

        if replay_logger and replay_logger._seq == 0:
            replay_logger.write_header(
                provider=type(model).__name__,
                model=getattr(model, "model", "(unknown)"),
                base_url=getattr(model, "base_url", ""),
                system_prompt=self.system_prompt,
                tool_defs=getattr(model, "tool_defs", None) or [],
                reasoning_effort=getattr(model, "reasoning_effort", None),
                temperature=getattr(model, "temperature", None),
            )

        empty_count = 0
        invalid_tool_count = 0
        for step in range(1, self.config.max_steps_per_call + 1):
            if self._cancel.is_set():
                self._emit(f"[d{depth}] cancelled by user", on_event)
                return "Task cancelled."
            if deadline and time.monotonic() > deadline:
                return "Time limit exceeded."
            self._emit(f"[d{depth}/s{step}] calling model...", on_event)
            t0 = time.monotonic()
            if on_content_delta and depth == 0 and hasattr(model, "on_content_delta"):
                model.on_content_delta = on_content_delta
            try:
                turn = model.complete(conversation)
            except ModelError as exc:
                err_text = str(exc).lower()
                if "invalid tool call" in err_text or "invalid_request_error" in err_text:
                    invalid_tool_count += 1
                    _log.warning("Invalid tool call at d%d/s%d (attempt %d/5), retrying: %s", depth, step, invalid_tool_count, exc)
                    self._emit(f"[d{depth}/s{step}] tool call error ({invalid_tool_count}/5), retrying...", on_event)
                    if invalid_tool_count >= 5:
                        self._emit(f"[d{depth}/s{step}] too many invalid tool calls, giving up", on_event)
                        return f"Model repeatedly produced invalid tool calls at depth {depth}. This usually means the model doesn't support the tool calling format. Try a different model."
                    retry_msg = ToolResult(
                        tool_call_id="error", name="system",
                        content="Your previous tool call had invalid arguments. Please try again with simpler arguments, or provide a text response instead. Do NOT use tools if you cannot format them correctly — just respond with text.",
                    )
                    model.append_tool_results(conversation, [retry_msg])
                    continue
                _log.error("Model error at d%d/s%d: %s", depth, step, exc, exc_info=True)
                self._emit(f"[d{depth}/s{step}] model error: {exc}", on_event)
                return f"Model error at depth {depth}, step {step}: {exc}"
            finally:
                if hasattr(model, "on_content_delta"):
                    model.on_content_delta = None
            elapsed = time.monotonic() - t0

            if replay_logger:
                try:
                    replay_logger.log_call(
                        depth=depth, step=step,
                        messages=conversation.get_messages(),
                        response=turn.raw_response,
                        input_tokens=turn.input_tokens,
                        output_tokens=turn.output_tokens,
                        elapsed_sec=elapsed,
                    )
                except OSError:
                    pass

            if turn.input_tokens or turn.output_tokens:
                model_name = getattr(model, "model", "(unknown)")
                with self._lock:
                    bucket = self.session_tokens.setdefault(model_name, {"input": 0, "output": 0})
                    bucket["input"] += turn.input_tokens
                    bucket["output"] += turn.output_tokens

            model.append_assistant_turn(conversation, turn)

            # Context condensation
            if turn.input_tokens:
                model_name = getattr(model, "model", "(unknown)")
                ctx_window = _MODEL_CONTEXT_WINDOWS.get(model_name, _DEFAULT_CONTEXT_WINDOW)
                if turn.input_tokens > _CONDENSATION_THRESHOLD * ctx_window:
                    condense_fn = getattr(model, "condense_conversation", None)
                    if condense_fn:
                        condense_fn(conversation)

            if on_step:
                try:
                    on_step({
                        "depth": depth, "step": step, "objective": objective,
                        "action": {"name": "_model_turn"}, "observation": "",
                        "model_text": turn.text or "",
                        "input_tokens": turn.input_tokens,
                        "output_tokens": turn.output_tokens,
                        "elapsed_sec": round(elapsed, 2), "is_final": False,
                    })
                except Exception:
                    pass

            # Final answer: no tool calls + text
            if not turn.tool_calls and turn.text:
                # At depth 0, check if this looks like a phase transition rather than
                # a true final answer. If so, nudge the model to continue with tool calls.
                if depth == 0 and step < self.config.max_steps_per_call - 5:
                    lower = turn.text.lower()
                    # Only match explicit phase-transition phrases, not generic words
                    _PHASE_KEYWORDS = ("moving to:", "next phase:", "— initiated",
                                       "proceeding to:", "— complete\n",
                                       "moving to analyst", "moving to verifier",
                                       "moving to hypothesis", "moving to brancher",
                                       "moving to critic", "moving to writer")
                    if any(kw in lower for kw in _PHASE_KEYWORDS):
                        self._emit(f"[d{depth}/s{step}] nudging manager to continue (phase transition detected)", on_event)
                        nudge = ToolResult(
                            tool_call_id="system_nudge", name="system",
                            content=(
                                "You output text without a tool call. The engine will terminate if you don't "
                                "call a tool. You MUST call the next phase's subtask() or save_phase_output() "
                                "or request_approval() NOW. Do NOT output bare text between phases."
                            ),
                        )
                        model.append_tool_results(conversation, [nudge])
                        continue
                self._emit(f"[d{depth}/s{step}] final answer ({len(turn.text)} chars)", on_event)
                if on_step:
                    try:
                        on_step({
                            "depth": depth, "step": step, "objective": objective,
                            "action": {"name": "final", "arguments": {"text": turn.text}},
                            "observation": turn.text, "is_final": True,
                        })
                    except Exception:
                        pass
                return turn.text

            if not turn.tool_calls:
                empty_count += 1
                if empty_count >= 3:
                    self._emit(f"[d{depth}/s{step}] model stuck (no output after {empty_count} retries)", on_event)
                    return f"Model could not produce a response for: {objective}"
                empty_result = ToolResult(
                    tool_call_id="empty", name="system",
                    content="No tool calls and no text. You MUST provide a text response now.",
                )
                model.append_tool_results(conversation, [empty_result])
                continue

            empty_count = 0  # reset on successful tool call
            invalid_tool_count = 0
            tc_names = [tc.name for tc in turn.tool_calls]
            self._emit(
                f"[d{depth}/s{step}] {len(turn.tool_calls)} tool call(s) ({elapsed:.1f}s): {', '.join(tc_names)}",
                on_event,
            )

            # Execute tool calls — parallelize independent tools
            results: list[ToolResult] = []
            final_answer: str | None = None
            # Sequential: subtask/execute (need model factory for execute)
            _SEQUENTIAL_TOOLS = {"subtask", "execute"} if self.model_factory else {"subtask"}
            sequential = [(i, tc) for i, tc in enumerate(turn.tool_calls) if tc.name in _SEQUENTIAL_TOOLS]
            parallel = [(i, tc) for i, tc in enumerate(turn.tool_calls) if tc.name not in _SEQUENTIAL_TOOLS]
            indexed_results: dict[int, tuple[ToolResult, bool]] = {}

            for idx, tc in sequential:
                r, is_final = self._run_one_tool(
                    tc=tc, depth=depth, step=step, objective=objective,
                    context=context, on_event=on_event, on_step=on_step,
                    deadline=deadline, current_model=model, replay_logger=replay_logger,
                )
                indexed_results[idx] = (r, is_final)
                if is_final:
                    final_answer = r.content
                    break

            if parallel and final_answer is None:
                with ThreadPoolExecutor(max_workers=len(parallel)) as pool:
                    futures = {
                        pool.submit(
                            self._run_one_tool,
                            tc=tc, depth=depth, step=step, objective=objective,
                            context=context, on_event=on_event, on_step=on_step,
                            deadline=deadline, current_model=model, replay_logger=replay_logger,
                        ): idx
                        for idx, tc in parallel
                    }
                    for future in futures:
                        idx = futures[future]
                        r, is_final = future.result()
                        indexed_results[idx] = (r, is_final)

            for i in sorted(indexed_results):
                r, is_final = indexed_results[i]
                results.append(r)
                if is_final and final_answer is None:
                    final_answer = r.content

            # Budget warnings
            if final_answer is None and results:
                budget_total = self.config.max_steps_per_call
                remaining = budget_total - step
                ts_tag = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}]"
                budget_tag = f"[Step {step}/{budget_total}]"
                r0 = results[0]
                results[0] = ToolResult(r0.tool_call_id, r0.name, f"{ts_tag} {budget_tag} {r0.content}", r0.is_error)
                if 0 < remaining <= budget_total // 4:
                    warning = f"\n\n** BUDGET CRITICAL: {remaining} of {budget_total} steps remain. Write your output NOW."
                    rl = results[-1]
                    results[-1] = ToolResult(rl.tool_call_id, rl.name, rl.content + warning, rl.is_error)

            model.append_tool_results(conversation, results)
            if final_answer is not None:
                return final_answer
            for r in results:
                context.add(f"[depth {depth} step {step}]\n{r.content}")

        return f"Step budget exhausted at depth {depth} for: {objective}"

    def _run_one_tool(
        self, tc: ToolCall, depth: int, step: int, objective: str,
        context: ExternalContext, on_event: EventCallback | None,
        on_step: StepCallback | None, deadline: float,
        current_model: BaseModel, replay_logger: ReplayLogger | None,
    ) -> tuple[ToolResult, bool]:
        if self._cancel.is_set():
            return ToolResult(tc.id, tc.name, "Task cancelled."), False
        arg_summary = _summarize_args(tc.arguments)
        self._emit(f"[d{depth}/s{step}] {tc.name}({arg_summary})", on_event)
        t1 = time.monotonic()
        try:
            is_final, observation = self._apply_tool_call(
                tool_call=tc, depth=depth, context=context,
                on_event=on_event, on_step=on_step, deadline=deadline,
                current_model=current_model, replay_logger=replay_logger, step=step,
            )
        except Exception as exc:
            _log.error("Tool %s crashed at d%d/s%d: %s", tc.name, depth, step, exc, exc_info=True)
            observation = f"Tool {tc.name} crashed: {type(exc).__name__}: {exc}"
            is_final = False
        observation = self._clip_observation(observation)
        tool_elapsed = time.monotonic() - t1
        obs_summary = _summarize_observation(observation)
        self._emit(f"[d{depth}/s{step}]   -> {obs_summary} ({tool_elapsed:.1f}s)", on_event)
        if on_step:
            try:
                on_step({
                    "depth": depth, "step": step, "objective": objective,
                    "action": {"name": tc.name, "arguments": tc.arguments},
                    "observation": observation,
                    "elapsed_sec": round(tool_elapsed, 2), "is_final": is_final,
                })
            except Exception:
                pass
        return ToolResult(tc.id, tc.name, observation), is_final

    def _apply_tool_call(
        self, tool_call: ToolCall, depth: int,
        context: ExternalContext, on_event: EventCallback | None,
        on_step: StepCallback | None, deadline: float = 0,
        current_model: BaseModel | None = None,
        replay_logger: ReplayLogger | None = None, step: int = 0,
    ) -> tuple[bool, str]:
        name = tool_call.name
        args = tool_call.arguments

        if name == "think":
            return False, f"Thought noted: {args.get('note', '')}"

        # Subtask delegation
        if name == "subtask":
            if not self.config.recursive:
                return False, "Subtask not available in flat mode."
            if depth >= self.config.max_depth:
                return False, "Max recursion depth reached."
            obj = str(args.get("objective", "")).strip()
            if not obj:
                return False, "subtask requires objective"
            criteria = str(args.get("acceptance_criteria", "") or "").strip()
            if self.config.acceptance_criteria and not criteria:
                return False, "subtask requires acceptance_criteria"
            # Sub-model routing
            requested_model = args.get("model")
            requested_effort = args.get("reasoning_effort")
            subtask_model: BaseModel | None = None
            if (requested_model or requested_effort) and self.model_factory:
                cur = current_model or self.model
                cur_name = getattr(cur, "model", "")
                cur_effort = getattr(cur, "reasoning_effort", None)
                cur_tier = _model_tier(cur_name, cur_effort)
                req_name = requested_model or cur_name
                req_tier = _model_tier(req_name, requested_effort or cur_effort)
                if req_tier < cur_tier:
                    return False, f"Cannot delegate to higher-tier model (tier {cur_tier} -> {req_tier})."
                cache_key = (req_name, requested_effort)
                with self._lock:
                    if cache_key not in self._model_cache:
                        self._model_cache[cache_key] = self.model_factory(req_name, requested_effort)
                    subtask_model = self._model_cache[cache_key]
            # Per-subtask depth limit: if caller specifies max_depth, enforce it
            subtask_max_depth = args.get("max_depth")
            if subtask_max_depth is not None:
                subtask_max_depth = int(subtask_max_depth)
                # max_depth is *additional* levels allowed below current depth
                # e.g. max_depth=1 means the subtask can run but cannot spawn sub-subtasks
                effective_limit = depth + 1 + subtask_max_depth
            else:
                effective_limit = None
            self._emit(f"[d{depth}] >> entering subtask: {obj}", on_event)
            child_logger = replay_logger.child(depth, step) if replay_logger else None
            # Temporarily override max_depth if subtask specifies it
            original_max_depth = self.config.max_depth
            if effective_limit is not None:
                self.config.max_depth = effective_limit
            try:
                result = self._solve_recursive(
                    objective=obj, depth=depth + 1, context=context,
                    on_event=on_event, on_step=on_step, deadline=deadline,
                    model_override=subtask_model, replay_logger=child_logger,
                )
            finally:
                if effective_limit is not None:
                    self.config.max_depth = original_max_depth
            observation = f"Subtask result for '{obj}':\n{result}"
            if criteria and self.config.acceptance_criteria:
                verdict = self._judge_result(obj, criteria, result, current_model)
                tag = "PASS" if verdict.startswith("PASS") else "FAIL"
                observation += f"\n\n[ACCEPTANCE CRITERIA: {tag}]\n{verdict}"
            return False, observation

        if name == "execute":
            obj = str(args.get("objective", "")).strip()
            if not obj:
                return False, "execute requires objective"
            criteria = str(args.get("acceptance_criteria", "") or "").strip()
            if self.config.acceptance_criteria and not criteria:
                return False, "execute requires acceptance_criteria"
            if depth >= self.config.max_depth:
                return False, "Max recursion depth reached."
            cur = current_model or self.model
            cur_name = getattr(cur, "model", "")
            exec_name, exec_effort = _lowest_tier_model(cur_name)
            exec_model: BaseModel | None = None
            if self.model_factory:
                cache_key = (exec_name, exec_effort)
                with self._lock:
                    if cache_key not in self._model_cache:
                        self._model_cache[cache_key] = self.model_factory(exec_name, exec_effort)
                    exec_model = self._model_cache[cache_key]
            if exec_model and hasattr(exec_model, "tool_defs"):
                exec_model.tool_defs = get_tool_definitions(include_subtask=False)
            self._emit(f"[d{depth}] >> executing leaf: {obj}", on_event)
            child_logger = replay_logger.child(depth, step) if replay_logger else None
            result = self._solve_recursive(
                objective=obj, depth=depth + 1, context=context,
                on_event=on_event, on_step=on_step, deadline=deadline,
                model_override=exec_model, replay_logger=child_logger,
            )
            observation = f"Execute result for '{obj}':\n{result}"
            if criteria and self.config.acceptance_criteria:
                verdict = self._judge_result(obj, criteria, result, current_model)
                tag = "PASS" if verdict.startswith("PASS") else "FAIL"
                observation += f"\n\n[ACCEPTANCE CRITERIA: {tag}]\n{verdict}"
            return False, observation

        # All other tools — dispatch through ARATools
        observation = self.tools.dispatch(name, args)
        return False, observation
