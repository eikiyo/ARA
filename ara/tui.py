# Location: ara/tui.py
# Purpose: Rich TUI — REPL, splash screen, step display, activity spinner
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

SLASH_COMMANDS: list[str] = ["/quit", "/exit", "/help", "/status", "/clear", "/model", "/gates"]

HELP_LINES: list[str] = [
    "Commands:",
    "  /model              Show current model and provider",
    "  /model <name>       Switch model (e.g. /model opus, /model sonnet)",
    "  /gates              Toggle approval gates on/off",
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


def _format_session_tokens(session_tokens: dict[str, dict[str, int]]) -> str:
    total_in = sum(v["input"] for v in session_tokens.values())
    total_out = sum(v["output"] for v in session_tokens.values())
    if total_in == 0 and total_out == 0:
        return ""
    return f"{_format_token_count(total_in)} in / {_format_token_count(total_out)} out"


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

_PHASE_NAMES: dict[str, tuple[int, str]] = {
    "scout": (1, "Scout — Paper Discovery"),
    "triage": (2, "Analyst Triage — Ranking"),
    "analyst_triage": (2, "Analyst Triage — Ranking"),
    "deep_read": (3, "Analyst Deep Read — Claim Extraction"),
    "analyst_deep_read": (3, "Analyst Deep Read — Claim Extraction"),
    "verifier": (4, "Verifier — Claim Validation"),
    "hypothesis": (5, "Hypothesis Generation"),
    "brancher": (6, "Brancher — Cross-Domain Search"),
    "critic_showdown": (6.5, "Critic Showdown — Comparison"),
    "critic": (7, "Critic — Evaluation"),
    "writer": (8, "Writer — Paper Draft"),
}
_TOTAL_PHASES = 8

_THINKING_TAIL_LINES = 6
_THINKING_MAX_LINE_WIDTH = 80

_EVENT_MAX_CHARS = 300


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
    if isinstance(val, list):
        val = ", ".join(str(x) for x in val[:3])
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
        mode = "recursive" if ctx.cfg.recursive else "flat"
        emit(f"Provider: {ctx.cfg.provider} | Model: {model_name} | Reasoning: {effort} | Mode: {mode}")
        tokens = ctx.runtime.engine.session_tokens
        if tokens:
            for mname, counts in tokens.items():
                emit(f"  {mname}: {_format_token_count(counts['input'])} in / {_format_token_count(counts['output'])} out")
        else:
            emit("  Tokens: (none yet)")
        return "handled"
    if command == "/clear":
        return "clear"
    if command == "/gates":
        current = ctx.cfg.approval_gates
        ctx.cfg.approval_gates = not current
        ctx.runtime.engine.tools.approval_gates = ctx.cfg.approval_gates
        state = "ON" if ctx.cfg.approval_gates else "OFF (auto-approve)"
        emit(f"Approval gates: {state}")
        return "handled"
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
            # Auto-persist model + provider
            settings = ctx.settings_store.load()
            settings.default_provider = ctx.cfg.provider
            provider = ctx.cfg.provider
            if provider == "openai":
                settings.default_model_openai = new_model
            elif provider == "anthropic":
                settings.default_model_anthropic = new_model
            elif provider == "openrouter":
                settings.default_model_openrouter = new_model
            elif provider == "ollama":
                settings.default_model_ollama = new_model
            else:
                settings.default_model = new_model
            ctx.settings_store.save(settings)
        except Exception as exc:
            emit(f"Failed: {exc}")
        return "handled"
    return None


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
        self._tool_key_arg: str = ""
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
            self._tool_key_arg = ""
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
            self._tool_name = ""
            self._tool_key_arg = ""
            self._tool_arg_buf = ""
            self._tool_arg_name = ""

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
            self._tool_key_arg = key_arg
            self._text_buf = ""
            self._tool_arg_buf = ""
            self._tool_arg_name = ""
            if step_label:
                self._step_label = step_label
            self._start_time = time.monotonic()
        if not self._active:
            self.start(mode="tool", step_label=step_label)

    @staticmethod
    def _extract_preview(buf: str) -> str:
        for key in ('"content": "', '"content":"', '"patch": "', '"patch":"'):
            idx = buf.find(key)
            if idx < 0:
                continue
            value_start = idx + len(key)
            raw_value = buf[value_start:]
            raw_value = (
                raw_value
                .replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
            if raw_value.endswith("\\"):
                raw_value = raw_value[:-1]
            return raw_value
        lines = buf.splitlines()
        return "\n".join(lines[-3:]) if lines else buf

    def _build_renderable(self) -> Any:
        from rich.text import Text
        elapsed = time.monotonic() - self._start_time if self._start_time else 0.0
        with self._lock:
            mode = self._mode
            buf = self._text_buf
            step_label = self._step_label
            tool_name = self._tool_name
            tool_key_arg = self._tool_key_arg
            tool_arg_buf = self._tool_arg_buf
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
        if mode == "tool":
            if tool_key_arg:
                arg_display = tool_key_arg
                if len(arg_display) > _THINKING_MAX_LINE_WIDTH:
                    arg_display = arg_display[:_THINKING_MAX_LINE_WIDTH - 3] + "..."
                return Text.from_markup(f"\u2800 {header}\n  [dim italic]{arg_display}[/dim italic]")
            return Text.from_markup(f"\u2800 {header}")
        if mode == "tool_args":
            if not tool_arg_buf:
                return Text.from_markup(f"\u2800 {header}")
            preview = self._extract_preview(tool_arg_buf)
            lines = preview.splitlines()
            tail = lines[-_THINKING_TAIL_LINES:]
            clipped = []
            for ln in tail:
                if len(ln) > _THINKING_MAX_LINE_WIDTH:
                    ln = ln[:_THINKING_MAX_LINE_WIDTH - 3] + "..."
                clipped.append(ln)
            snippet = "\n".join(f"  [dim italic]{ln}[/dim italic]" for ln in clipped)
            return Text.from_markup(f"\u2800 {header}\n{snippet}")
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

    @property
    def active(self) -> bool:
        return self._active


# ============================================================================
# LEFT-ALIGNED MARKDOWN
# ============================================================================

def _make_left_markdown():
    from rich import box as _box
    from rich.markdown import Markdown as _RichMarkdown, Heading as _RichHeading
    from rich.panel import Panel as _Panel
    from rich.text import Text as _Text

    class _LeftHeading(_RichHeading):
        def __rich_console__(self, console, options):
            text = self.text
            text.justify = "left"
            if self.tag == "h1":
                yield _Panel(text, box=_box.HEAVY, style="markdown.h1.border")
            else:
                if self.tag == "h2":
                    yield _Text("")
                yield text

    class _LeftMarkdown(_RichMarkdown):
        elements = {**_RichMarkdown.elements, "heading_open": _LeftHeading}

    return _LeftMarkdown


_LeftMarkdown = _make_left_markdown()


# ============================================================================
# RICH REPL
# ============================================================================

class RichREPL:
    def __init__(self, ctx: ChatContext, startup_info: dict[str, str] | None = None) -> None:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from rich.console import Console

        self.ctx = ctx
        self.console = Console()
        self._startup_info = startup_info or {}
        self._current_step: _StepState | None = None
        self._agent_thread: threading.Thread | None = None
        self._agent_result: str | None = None
        self._queued_input: list[str] = []
        self._activity = _ActivityDisplay(self.console)

        history_dir = Path.home() / ".ara"
        history_dir.mkdir(parents=True, exist_ok=True)

        completer = WordCompleter(SLASH_COMMANDS, sentence=True)

        kb = KeyBindings()

        @kb.add("escape", "enter")
        def _multiline(event: object) -> None:
            buf = getattr(event, "current_buffer", None) or getattr(event, "app", None)
            if buf is not None and hasattr(buf, "insert_text"):
                buf.insert_text("\n")
            elif hasattr(event, "current_buffer"):
                event.current_buffer.insert_text("\n")

        @kb.add("escape")
        def _cancel_agent(event: object) -> None:
            if self._agent_thread is not None and self._agent_thread.is_alive():
                self.ctx.runtime.engine.cancel()
                self.console.print("[dim]Cancelling...[/dim]")

        self.session: PromptSession[str] = PromptSession(
            history=FileHistory(str(history_dir / "repl_history")),
            completer=completer,
            key_bindings=kb,
            multiline=False,
        )

    def _on_event(self, msg: str) -> None:
        m = _RE_PREFIX.match(msg)
        body = msg[m.end():] if m else msg
        if _RE_CALLING.search(body):
            self._flush_step()
            # Show a brief "working" indicator so user knows agent is active
            depth = int(m.group(1)) if m and m.group(1) else 0
            step = int(m.group(2)) if m and m.group(2) else 0
            from rich.text import Text
            self.console.print(Text(f"  ⠿ working... (d{depth}/s{step})", style="dim"), end="\r")
            return
        if _RE_SUBTASK.search(body) or _RE_EXECUTE.search(body):
            self._flush_step()
            label = re.sub(r">> (entering subtask|executing leaf):\s*", "", body).strip()
            if len(label) > 120:
                label = label[:117] + "..."
            # Detect phase from subtask label
            phase_tag = ""
            lower_label = label.lower()
            for key, (num, display) in _PHASE_NAMES.items():
                if key in lower_label:
                    filled = int(num)
                    bar = "█" * filled + "░" * (_TOTAL_PHASES - filled)
                    phase_tag = f"  [bold cyan]Phase {num}/{_TOTAL_PHASES}[/bold cyan] [{bar}] {display}"
                    break
            if phase_tag:
                self.console.print(phase_tag)
            self.console.rule(f"[dim]{label}[/dim]", style="dim")
            return
        if _RE_ERROR.search(body):
            from rich.text import Text
            first_line = msg.split("\n", 1)[0]
            if len(first_line) > _EVENT_MAX_CHARS:
                first_line = first_line[:_EVENT_MAX_CHARS] + "..."
            self.console.print(Text(first_line, style="bold red"))
            return

    def _on_step(self, step_event: dict[str, Any]) -> None:
        action = step_event.get("action")
        if not isinstance(action, dict):
            return
        name = action.get("name", "")
        if name == "_model_turn":
            self._current_step = _StepState(
                depth=step_event.get("depth", 0),
                step=step_event.get("step", 0),
                max_steps=self.ctx.cfg.max_steps_per_call,
                model_text=step_event.get("model_text", ""),
                model_elapsed_sec=step_event.get("elapsed_sec", 0.0),
                input_tokens=step_event.get("input_tokens", 0),
                output_tokens=step_event.get("output_tokens", 0),
            )
            return
        if name == "final":
            self._flush_step()
            return
        if self._current_step is not None:
            key_arg = _extract_key_arg(name, action.get("arguments", {}))
            elapsed = step_event.get("elapsed_sec", 0.0)
            is_error = bool(
                step_event.get("observation", "").startswith("Tool ")
                and "crashed" in step_event.get("observation", "")
            )
            self._current_step.tool_calls.append(
                _ToolCallRecord(name=name, key_arg=key_arg, elapsed_sec=elapsed, is_error=is_error)
            )

    def _on_content_delta(self, delta_type: str, text: str) -> None:
        pass  # No live activity display — step headers show results after completion

    def _flush_step(self) -> None:
        step = self._current_step
        if step is None:
            return
        self._current_step = None
        from rich.text import Text

        ts = datetime.now().strftime("%H:%M:%S")
        model_name = getattr(self.ctx.runtime.engine.model, "model", "(unknown)")
        context_window = _MODEL_CONTEXT_WINDOWS.get(model_name, _DEFAULT_CONTEXT_WINDOW)
        ctx_str = f"{_format_token_count(step.input_tokens)}/{_format_token_count(context_window)}"

        left = f" {ts}  Step {step.step} "
        right_parts = []
        if step.depth > 0:
            right_parts.append(f"depth {step.depth}")
        if step.max_steps:
            right_parts.append(f"{step.step}/{step.max_steps}")
        if step.input_tokens or step.output_tokens:
            right_parts.append(f"{_format_token_count(step.input_tokens)}in/{_format_token_count(step.output_tokens)}out")
        right_parts.append(f"[{ctx_str}]")
        right = " | ".join(right_parts) if right_parts else ""
        self.console.rule(f"[bold]{left}[/bold][dim]{right}[/dim]", style="cyan")

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
            parts.append(f"{tc.name}", style="bold red" if tc.is_error else "bold")
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
            try:
                app = self.session.app
                if app is not None:
                    app.exit("")
            except Exception:
                pass

    def _pad_bottom(self) -> None:
        """Print 8 blank lines to keep output away from terminal bottom."""
        self.console.print("\n" * 7, end="")

    def _present_result(self, answer: str) -> None:
        self._flush_step()
        self.console.print()
        self.console.print(_LeftMarkdown(answer), justify="left")
        token_str = _format_session_tokens(self.ctx.runtime.engine.session_tokens)
        if token_str:
            from rich.text import Text
            self.console.print(Text(f"  tokens: {token_str}", style="dim"))
        self.console.print()
        self._pad_bottom()

    def run(self) -> None:
        from prompt_toolkit.patch_stdout import patch_stdout
        from prompt_toolkit.styles import Style
        from rich.text import Text

        _queue_style = Style.from_dict({"dim": "ansigray"})

        self.console.clear()
        self.console.print(Text(SPLASH_ART, style="bold cyan"))
        self.console.print(Text("  Autonomous Research Agent", style="bold"))
        self.console.print()
        if self._startup_info:
            for key, val in self._startup_info.items():
                self.console.print(Text(f"  {key:>10}  {val}", style="dim"))
            self.console.print()
        self.console.print(
            "Type /help for commands, Ctrl+D to exit.  ESC or Ctrl+C to cancel a running task.",
            style="dim",
        )
        self.console.print()
        self._pad_bottom()
        with patch_stdout(raw=True):
            while True:
                if self._queued_input:
                    user_input = self._queued_input.pop(0)
                    self.console.print(Text(f"you> {user_input}", style="bold"))
                else:
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
                self._agent_thread = threading.Thread(
                    target=self._run_agent, args=(user_input,), daemon=True,
                )
                self._agent_thread.start()
                _quit_requested = False
                while self._agent_thread.is_alive():
                    try:
                        queued = self.session.prompt(
                            [("class:dim", "... ")],
                            style=_queue_style,
                        ).strip()
                        if not queued:
                            continue
                        if queued.startswith("/"):
                            r = dispatch_slash_command(
                                queued, self.ctx,
                                emit=lambda line: self.console.print(Text(line, style="cyan")),
                            )
                            if r == "quit":
                                self.ctx.runtime.engine.cancel()
                                _quit_requested = True
                                break
                            if r == "clear":
                                self.console.clear()
                            continue
                        self._queued_input.append(queued)
                        self.console.print(
                            Text(f"  (queued: {queued[:60]}{'...' if len(queued) > 60 else ''})", style="dim"),
                        )
                    except KeyboardInterrupt:
                        self.ctx.runtime.engine.cancel()
                        self.console.print("[dim]Cancelling...[/dim]")
                        break
                    except EOFError:
                        break
                self._agent_thread.join()
                self._agent_thread = None
                if self._agent_result is not None:
                    self._present_result(self._agent_result)
                if _quit_requested:
                    break


def run_rich_repl(ctx: ChatContext, startup_info: dict[str, str] | None = None) -> None:
    repl = RichREPL(ctx, startup_info=startup_info)
    repl.run()
