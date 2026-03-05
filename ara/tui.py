# Location: ara/tui.py
# Purpose: Rich TUI — REPL, splash screen, step display, activity spinner, status bar
# Functions: RichREPL, ChatContext, run_rich_repl
# Calls: runtime.py, config.py, engine.py, builder.py
# Imports: re, threading, time, dataclasses, datetime, pathlib, rich, prompt_toolkit

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import ARAConfig
from .engine import RLMEngine, _MODEL_CONTEXT_WINDOWS, _DEFAULT_CONTEXT_WINDOW
from .model import EchoFallbackModel
from .runtime import SessionRuntime
from .settings import SettingsStore

SLASH_COMMANDS: list[str] = ["/quit", "/exit", "/help", "/status", "/clear", "/model"]

HELP_LINES: list[str] = [
    "Commands:",
    "  /model              Show current model and provider",
    "  /model <name>       Switch model (e.g. /model opus, /model sonnet)",
    "  /status             Show session status and token usage",
    "  /clear              Clear screen",
    "  /quit or /exit      Exit ARA",
    "  /help               Show this help",
]

MODEL_ALIASES: dict[str, str] = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "gpt5": "gpt-5.2",
    "gpt4": "gpt-4.1",
    "llama": "llama3.2",
}


@dataclass
class ChatContext:
    runtime: SessionRuntime
    cfg: ARAConfig
    settings_store: SettingsStore


def _format_token_count(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 10000:
        return f"{n / 1000:.1f}k"
    if n < 1000000:
        return f"{n / 1000:.0f}k"
    return f"{n / 1000000:.1f}M"


def _get_model_display_name(engine: RLMEngine) -> str:
    model = engine.model
    if isinstance(model, EchoFallbackModel):
        return "(no model)"
    return getattr(model, "model", "(unknown)")


def _build_splash() -> str:
    try:
        import pyfiglet
        art = pyfiglet.figlet_format("ARA", font="standard").rstrip()
    except Exception:
        art = "   ARA"
    return art


SPLASH_ART = _build_splash()

_RE_PREFIX = re.compile(r"^\[d(\d+)(?:/s(\d+))?\]\s*")
_RE_CALLING = re.compile(r"calling model")
_RE_SUBTASK = re.compile(r">> entering subtask")
_RE_EXECUTE = re.compile(r">> executing leaf")
_RE_ERROR = re.compile(r"model error:", re.IGNORECASE)
_RE_TOOL_START = re.compile(r"(\w+)\((.*)?\)$")

_THINKING_TAIL_LINES = 6
_THINKING_MAX_LINE_WIDTH = 80


@dataclass
class _ToolCallRecord:
    name: str
    key_arg: str
    elapsed_sec: float
    is_error: bool = False


@dataclass
class _StepState:
    depth: int = 0
    step: int = 0
    max_steps: int = 0
    model_text: str = ""
    model_elapsed_sec: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[_ToolCallRecord] = field(default_factory=list)


_KEY_ARGS: dict[str, str] = {
    "search_semantic_scholar": "query",
    "search_arxiv": "query",
    "search_crossref": "query",
    "search_openalex": "query",
    "search_pubmed": "query",
    "search_core": "query",
    "search_dblp": "query",
    "search_europe_pmc": "query",
    "search_base": "query",
    "request_approval": "phase",
    "embed_text": "text",
    "subtask": "objective",
    "execute": "objective",
    "think": "note",
    "branch_search": "query",
    "fetch_fulltext": "doi",
    "check_retraction": "doi",
    "validate_doi": "doi",
    "get_citation_count": "doi",
    "write_section": "section_name",
    "score_hypothesis": "hypothesis_text",
    "extract_claims": "paper_id",
    "read_paper": "paper_id",
}


def _extract_key_arg(name: str, arguments: dict[str, Any]) -> str:
    key = _KEY_ARGS.get(name)
    if not key:
        for v in arguments.values():
            if isinstance(v, str) and v.strip():
                s = v.strip()
                return s[:57] + "..." if len(s) > 60 else s
        return ""
    val = arguments.get(key, "")
    s = str(val).strip()
    return s[:57] + "..." if len(s) > 60 else s


def dispatch_slash_command(
    command: str, ctx: ChatContext, emit: Callable[[str], None],
) -> str | None:
    if command in {"/quit", "/exit"}:
        return "quit"
    if command == "/help":
        for ln in HELP_LINES:
            emit(ln)
        return "handled"
    if command == "/status":
        model_name = _get_model_display_name(ctx.runtime.engine)
        effort = ctx.cfg.reasoning_effort or "(off)"
        emit(f"Provider: {ctx.cfg.provider} | Model: {model_name} | Reasoning: {effort}")
        tokens = ctx.runtime.engine.session_tokens
        if tokens:
            for mname, counts in tokens.items():
                emit(f"  {mname}: {_format_token_count(counts['input'])} in / {_format_token_count(counts['output'])} out")
        else:
            emit("  Tokens: (none yet)")
        return "handled"
    if command == "/clear":
        return "clear"
    if command.startswith("/model"):
        args = command[len("/model"):].strip()
        if not args:
            model_name = _get_model_display_name(ctx.runtime.engine)
            emit(f"Current model: {model_name}")
            return "handled"
        new_model = MODEL_ALIASES.get(args.lower(), args)
        from .builder import build_engine, infer_provider_for_model
        inferred = infer_provider_for_model(new_model)
        if inferred and inferred != ctx.cfg.provider:
            ctx.cfg.provider = inferred
        ctx.cfg.model = new_model
        try:
            new_engine = build_engine(ctx.cfg)
            ctx.runtime.engine = new_engine
            emit(f"Switched to: {new_model}")
        except Exception as exc:
            emit(f"Failed: {exc}")
        return "handled"
    return None


# ============================================================================
# STATUS BAR
# ============================================================================

@dataclass
class _StatusBarState:
    """Mutable state for the bottom status bar."""
    model_name: str = ""
    provider: str = ""
    current_phase: str = "idle"
    current_step: int = 0
    max_steps: int = 100
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    context_window: int = 128_000
    budget_cap: float = 0.0
    budget_spent: float = 0.0
    is_processing: bool = False


def _build_progress_bar(pct: float, width: int = 16) -> str:
    """Build a text-based progress bar."""
    filled = int(pct / 100 * width)
    filled = max(0, min(width, filled))
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    return bar


def _build_toolbar_text(state: _StatusBarState) -> list[tuple[str, str]]:
    """Build prompt_toolkit formatted text for the status bar."""
    # Calculate token percentage
    total_tokens = state.total_input_tokens + state.total_output_tokens
    ctx_pct = (state.total_input_tokens / state.context_window * 100) if state.context_window > 0 else 0
    ctx_pct = min(ctx_pct, 100)

    # Progress bar
    bar = _build_progress_bar(ctx_pct)

    # Left side
    parts: list[tuple[str, str]] = []

    # Separator line (empty line above toolbar for visual gap)
    parts.append(("class:toolbar.sep", "\n"))

    # Model indicator
    model_short = state.model_name
    if len(model_short) > 20:
        model_short = model_short[:18] + ".."
    parts.append(("class:toolbar.model", f" {model_short} "))
    parts.append(("class:toolbar.dim", " | "))

    # Progress bar + percentage
    if ctx_pct < 50:
        bar_style = "class:toolbar.bar-ok"
    elif ctx_pct < 80:
        bar_style = "class:toolbar.bar-warn"
    else:
        bar_style = "class:toolbar.bar-crit"
    parts.append((bar_style, bar))
    parts.append(("class:toolbar.text", f" {ctx_pct:.0f}%"))
    parts.append(("class:toolbar.dim", " | "))

    # Token usage
    tok_in = _format_token_count(state.total_input_tokens)
    tok_out = _format_token_count(state.total_output_tokens)
    ctx_win = _format_token_count(state.context_window)
    parts.append(("class:toolbar.text", f"Tokens: {tok_in}/{ctx_win}"))
    parts.append(("class:toolbar.dim", " | "))

    # Phase
    phase_display = state.current_phase.replace("_", " ").title()
    if state.is_processing:
        parts.append(("class:toolbar.phase-active", f"Phase: {phase_display}"))
    else:
        parts.append(("class:toolbar.text", f"Phase: {phase_display}"))
    parts.append(("class:toolbar.dim", " | "))

    # Step
    if state.is_processing and state.current_step > 0:
        parts.append(("class:toolbar.text", f"Step: {state.current_step}/{state.max_steps}"))
    else:
        parts.append(("class:toolbar.text", f"Step: -/{state.max_steps}"))

    # Right side — budget remaining (pad to right-align)
    if state.budget_cap > 0:
        remaining = max(0, state.budget_cap - state.budget_spent)
        budget_text = f"Budget: ${remaining:.2f} remaining"
    else:
        budget_text = "Budget: unlimited"

    # Calculate padding for right alignment (approximate terminal width)
    left_len = sum(len(text) for _, text in parts)
    try:
        import shutil
        term_width = shutil.get_terminal_size().columns
    except Exception:
        term_width = 120
    pad = max(1, term_width - left_len - len(budget_text) - 2)
    parts.append(("class:toolbar.text", " " * pad))

    if state.budget_cap > 0 and remaining < state.budget_cap * 0.2:
        parts.append(("class:toolbar.budget-low", budget_text))
    else:
        parts.append(("class:toolbar.budget", budget_text))
    parts.append(("class:toolbar.text", " "))

    return parts


# prompt_toolkit style for the toolbar
_TOOLBAR_STYLE_DICT = {
    "toolbar":              "bg:#1a1a2e #e0e0e0",
    "toolbar.sep":          "bg:#0d0d1a #333",
    "toolbar.model":        "bg:#16213e bold #00d4ff",
    "toolbar.dim":          "bg:#1a1a2e #555555",
    "toolbar.text":         "bg:#1a1a2e #aaaaaa",
    "toolbar.bar-ok":       "bg:#1a1a2e #00d4ff",
    "toolbar.bar-warn":     "bg:#1a1a2e #ffaa00",
    "toolbar.bar-crit":     "bg:#1a1a2e #ff4444",
    "toolbar.phase-active": "bg:#1a1a2e bold #00ff88",
    "toolbar.budget":       "bg:#1a1a2e #888888",
    "toolbar.budget-low":   "bg:#1a1a2e bold #ff4444",
}


# ============================================================================
# ACTIVITY DISPLAY
# ============================================================================

class _ActivityDisplay:
    def __init__(self, console: Any) -> None:
        self._console = console
        self._lock = threading.Lock()
        self._text_buf: str = ""
        self._mode: str = "thinking"
        self._step_label: str = ""
        self._tool_name: str = ""
        self._tool_arg_buf: str = ""
        self._tool_arg_name: str = ""
        self._start_time: float = 0.0
        self._live: Any | None = None
        self._active = False

    def __rich__(self) -> Any:
        return self._build_renderable()

    def start(self, mode: str = "thinking", step_label: str = "") -> None:
        from rich.live import Live
        with self._lock:
            self._mode = mode
            self._step_label = step_label
            self._text_buf = ""
            self._tool_name = ""
            self._tool_arg_buf = ""
            self._tool_arg_name = ""
            self._start_time = time.monotonic()
        if self._active and self._live is not None:
            return
        self._active = True
        self._live = Live(self, console=self._console, transient=True, refresh_per_second=8)
        self._live.__enter__()

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        if self._live is not None:
            try:
                self._live.__exit__(None, None, None)
            except Exception:
                pass
            self._live = None
        with self._lock:
            self._text_buf = ""

    def feed(self, delta_type: str, text: str) -> None:
        if not self._active:
            return
        with self._lock:
            if delta_type == "tool_call_start":
                self._mode = "tool_args"
                self._tool_arg_name = text
                self._tool_arg_buf = ""
                return
            if delta_type == "tool_call_args":
                self._tool_arg_buf += text
                return
            if delta_type == "text" and self._mode in ("thinking", "tool_args"):
                self._mode = "streaming"
                self._text_buf = ""
            if delta_type in ("thinking", "text"):
                self._text_buf += text

    def set_tool(self, tool_name: str, key_arg: str = "", step_label: str = "") -> None:
        with self._lock:
            self._mode = "tool"
            self._tool_name = tool_name
            self._text_buf = ""
            self._tool_arg_buf = ""
            if step_label:
                self._step_label = step_label
            self._start_time = time.monotonic()
        if not self._active:
            self.start(mode="tool", step_label=step_label)

    def _build_renderable(self) -> Any:
        from rich.text import Text
        elapsed = time.monotonic() - self._start_time if self._start_time else 0.0
        with self._lock:
            mode = self._mode
            buf = self._text_buf
            step_label = self._step_label
            tool_name = self._tool_name
            tool_arg_name = self._tool_arg_name
        step_part = f"  [dim]{step_label}[/dim]" if step_label else ""
        if mode == "thinking":
            header = f"[bold cyan]Thinking...[/bold cyan]  [dim]({elapsed:.1f}s)[/dim]{step_part}"
        elif mode == "streaming":
            header = f"[bold green]Responding...[/bold green]  [dim]({elapsed:.1f}s)[/dim]{step_part}"
        elif mode == "tool_args":
            header = f"[bold yellow]Generating {tool_arg_name}...[/bold yellow]  [dim]({elapsed:.1f}s)[/dim]{step_part}"
        else:
            header = f"[bold yellow]Running {tool_name}...[/bold yellow]  [dim]({elapsed:.1f}s)[/dim]{step_part}"
        if not buf:
            return Text.from_markup(f"\u2800 {header}")
        lines = buf.splitlines()
        tail = lines[-_THINKING_TAIL_LINES:]
        clipped = []
        for ln in tail:
            if len(ln) > _THINKING_MAX_LINE_WIDTH:
                ln = ln[:_THINKING_MAX_LINE_WIDTH - 3] + "..."
            clipped.append(ln)
        snippet = "\n".join(f"  [dim italic]{ln}[/dim italic]" for ln in clipped)
        return Text.from_markup(f"\u2800 {header}\n{snippet}")


# ============================================================================
# RICH REPL
# ============================================================================

class RichREPL:
    def __init__(self, ctx: ChatContext, startup_info: dict[str, str] | None = None) -> None:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.styles import Style
        from rich.console import Console
        self.ctx = ctx
        self.console = Console()
        self._startup_info = startup_info or {}
        self._current_step: _StepState | None = None
        self._agent_thread: threading.Thread | None = None
        self._agent_result: str | None = None
        self._activity = _ActivityDisplay(self.console)

        # Status bar state
        self._status = _StatusBarState(
            model_name=_get_model_display_name(ctx.runtime.engine),
            provider=ctx.cfg.provider,
            max_steps=ctx.cfg.max_steps_per_call,
            context_window=_MODEL_CONTEXT_WINDOWS.get(
                getattr(ctx.runtime.engine.model, "model", ""), _DEFAULT_CONTEXT_WINDOW
            ),
            budget_cap=ctx.cfg.budget_limit_usd,
        )

        # Load budget from DB if available
        if ctx.runtime.db:
            try:
                # Use the first session or create one
                import sqlite3
                cursor = ctx.runtime.db.conn.cursor()
                cursor.execute("SELECT budget_cap, budget_spent FROM research_sessions ORDER BY session_id DESC LIMIT 1")
                row = cursor.fetchone()
                if row:
                    self._status.budget_cap = float(row[0] or 0)
                    self._status.budget_spent = float(row[1] or 0)
            except Exception:
                pass

        # prompt_toolkit style
        pt_style = Style.from_dict(_TOOLBAR_STYLE_DICT)

        history_dir = Path.home() / ".ara"
        history_dir.mkdir(parents=True, exist_ok=True)
        self.session: PromptSession[str] = PromptSession(
            history=FileHistory(str(history_dir / "repl_history")),
            style=pt_style,
            bottom_toolbar=self._toolbar,
            reserve_space_for_menu=6,
        )

    def _toolbar(self) -> list[tuple[str, str]]:
        """Return formatted text for the bottom toolbar."""
        return _build_toolbar_text(self._status)

    def _update_status_tokens(self) -> None:
        """Refresh token counts from engine."""
        tokens = self.ctx.runtime.engine.session_tokens
        total_in = sum(v["input"] for v in tokens.values())
        total_out = sum(v["output"] for v in tokens.values())
        self._status.total_input_tokens = total_in
        self._status.total_output_tokens = total_out
        self._status.model_name = _get_model_display_name(self.ctx.runtime.engine)
        self._status.context_window = _MODEL_CONTEXT_WINDOWS.get(
            getattr(self.ctx.runtime.engine.model, "model", ""), _DEFAULT_CONTEXT_WINDOW
        )

    def _on_event(self, msg: str) -> None:
        m = _RE_PREFIX.match(msg)
        body = msg[m.end():] if m else msg
        step_label = ""
        if m and m.group(2):
            step_num = int(m.group(2))
            step_label = f"Step {step_num}/{self.ctx.cfg.max_steps_per_call}"
            self._status.current_step = step_num
            self._status.is_processing = True
        if _RE_CALLING.search(body):
            self._flush_step()
            self._activity.start(mode="thinking", step_label=step_label)
            return
        if _RE_SUBTASK.search(body) or _RE_EXECUTE.search(body):
            self._flush_step()
            self._activity.stop()
            label = re.sub(r">> (entering subtask|executing leaf):\s*", "", body).strip()
            self.console.rule(f"[dim]{label}[/dim]", style="dim")
            return
        if _RE_ERROR.search(body):
            self._activity.stop()
            from rich.text import Text
            self.console.print(Text(msg[:300], style="bold red"))
            return
        tm = _RE_TOOL_START.search(body)
        if tm:
            self._activity.set_tool(tm.group(1), key_arg=tm.group(2) or "", step_label=step_label)

    def _on_step(self, step_event: dict[str, Any]) -> None:
        action = step_event.get("action")
        if not isinstance(action, dict):
            return
        name = action.get("name", "")
        if name == "_model_turn":
            self._activity.stop()
            self._current_step = _StepState(
                depth=step_event.get("depth", 0),
                step=step_event.get("step", 0),
                max_steps=self.ctx.cfg.max_steps_per_call,
                model_text=step_event.get("model_text", ""),
                model_elapsed_sec=step_event.get("elapsed_sec", 0.0),
                input_tokens=step_event.get("input_tokens", 0),
                output_tokens=step_event.get("output_tokens", 0),
            )
            # Update status bar after each step
            self._update_status_tokens()
            self._status.current_step = step_event.get("step", 0)
            return
        if name == "final":
            self._flush_step()
            return
        if self._current_step is not None:
            key_arg = _extract_key_arg(name, action.get("arguments", {}))
            elapsed = step_event.get("elapsed_sec", 0.0)
            is_error = "crashed" in step_event.get("observation", "")
            self._current_step.tool_calls.append(
                _ToolCallRecord(name=name, key_arg=key_arg, elapsed_sec=elapsed, is_error=is_error)
            )

    def _on_content_delta(self, delta_type: str, text: str) -> None:
        self._activity.feed(delta_type, text)

    def _flush_step(self) -> None:
        step = self._current_step
        if step is None:
            return
        self._current_step = None
        from rich.text import Text
        ts = datetime.now().strftime("%H:%M:%S")
        model_name = getattr(self.ctx.runtime.engine.model, "model", "(unknown)")
        ctx_window = _MODEL_CONTEXT_WINDOWS.get(model_name, _DEFAULT_CONTEXT_WINDOW)
        ctx_str = f"{_format_token_count(step.input_tokens)}/{_format_token_count(ctx_window)}"
        right_parts = []
        if step.depth > 0:
            right_parts.append(f"depth {step.depth}")
        right_parts.append(f"{step.step}/{step.max_steps}")
        if step.input_tokens or step.output_tokens:
            right_parts.append(f"{_format_token_count(step.input_tokens)}in/{_format_token_count(step.output_tokens)}out")
        right_parts.append(f"[{ctx_str}]")
        right = " | ".join(right_parts)
        self.console.rule(f"[bold] {ts}  Step {step.step} [/bold][dim]{right}[/dim]", style="cyan")
        if step.model_text:
            preview = step.model_text.strip()
            if len(preview) > 200:
                preview = preview[:197] + "..."
            self.console.print(Text(f"  ({step.model_elapsed_sec:.1f}s) {preview}", style="dim"))
        n = len(step.tool_calls)
        for i, tc in enumerate(step.tool_calls):
            is_last = i == n - 1
            connector = "\u2514\u2500" if is_last else "\u251c\u2500"
            parts = Text()
            parts.append(f"  {connector} ", style="dim")
            parts.append(f"{tc.name}", style="bold red" if tc.is_error else "")
            if tc.key_arg:
                parts.append(f'  "{tc.key_arg}"', style="dim")
            parts.append(f"  {tc.elapsed_sec:.1f}s", style="dim")
            self.console.print(parts)

    def _run_agent(self, objective: str) -> None:
        try:
            self._agent_result = self.ctx.runtime.solve(
                objective, on_event=self._on_event,
                on_step=self._on_step, on_content_delta=self._on_content_delta,
            )
        except Exception as exc:
            self._agent_result = f"Agent error: {type(exc).__name__}: {exc}"
        finally:
            self._status.is_processing = False
            self._update_status_tokens()
            try:
                app = self.session.app
                if app is not None:
                    app.exit("")
            except Exception:
                pass

    def _present_result(self, answer: str) -> None:
        from rich.markdown import Markdown
        self._activity.stop()
        self._flush_step()
        self.console.print()
        self.console.print(Markdown(answer), justify="left")
        self._update_status_tokens()
        tokens = self.ctx.runtime.engine.session_tokens
        total_in = sum(v["input"] for v in tokens.values())
        total_out = sum(v["output"] for v in tokens.values())
        if total_in or total_out:
            from rich.text import Text
            self.console.print(Text(
                f"  tokens: {_format_token_count(total_in)} in / {_format_token_count(total_out)} out",
                style="dim",
            ))
        self.console.print()

    def run(self) -> None:
        from prompt_toolkit.patch_stdout import patch_stdout
        from rich.text import Text
        self.console.clear()
        self.console.print(Text(SPLASH_ART, style="bold cyan"))
        self.console.print(Text("  Adaptive Research Agent", style="bold"))
        self.console.print()
        if self._startup_info:
            for key, val in self._startup_info.items():
                self.console.print(Text(f"  {key:>10}  {val}", style="dim"))
            self.console.print()
        self.console.print("Type your research topic, or /help for commands. Ctrl+D to exit.", style="dim")
        self.console.print()
        with patch_stdout(raw=True):
            while True:
                try:
                    user_input = self.session.prompt("you> ").strip()
                except KeyboardInterrupt:
                    continue
                except EOFError:
                    break
                if not user_input:
                    continue
                result = dispatch_slash_command(
                    user_input, self.ctx,
                    emit=lambda line: self.console.print(Text(line, style="cyan")),
                )
                if result == "quit":
                    break
                if result == "clear":
                    self.console.clear()
                    continue
                if result == "handled":
                    continue
                self.console.print()
                self._agent_result = None
                self._status.is_processing = True
                self._status.current_step = 0
                self._status.current_phase = "processing"
                self._agent_thread = threading.Thread(target=self._run_agent, args=(user_input,), daemon=True)
                self._agent_thread.start()
                while self._agent_thread.is_alive():
                    try:
                        queued = self.session.prompt("... ").strip()
                        if queued.startswith("/"):
                            r = dispatch_slash_command(
                                queued, self.ctx,
                                emit=lambda line: self.console.print(Text(line, style="cyan")),
                            )
                            if r == "quit":
                                self.ctx.runtime.engine.cancel()
                                break
                        elif queued:
                            self.console.print(Text(f"  (queued: {queued[:60]})", style="dim"))
                    except (KeyboardInterrupt, EOFError):
                        self.ctx.runtime.engine.cancel()
                        self.console.print("[dim]Cancelling...[/dim]")
                        break
                self._agent_thread.join()
                self._agent_thread = None
                if self._agent_result is not None:
                    self._present_result(self._agent_result)


def run_rich_repl(ctx: ChatContext, startup_info: dict[str, str] | None = None) -> None:
    repl = RichREPL(ctx, startup_info=startup_info)
    repl.run()
