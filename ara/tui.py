# Location: ara/tui.py
# Purpose: Rich terminal UI — REPL, activity display, slash commands
# Functions: run_rich_repl, dispatch_slash_command, ChatContext
# Calls: runtime.py, engine.py, config.py
# Imports: rich, prompt_toolkit, threading

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from .config import ARAConfig
from .engine import StepEvent
from .runtime import SessionRuntime
from .settings import SettingsStore


@dataclass
class ChatContext:
    runtime: SessionRuntime
    cfg: ARAConfig
    settings_store: SettingsStore


def _get_model_display_name(engine: Any) -> str:
    model = getattr(engine, "model", None)
    return getattr(model, "model", "unknown") if model else "unknown"


def dispatch_slash_command(
    text: str, ctx: ChatContext,
    emit: Any = None,
) -> str:
    if not text.startswith("/"):
        return "not_command"

    cmd = text.strip().lower().split()
    name = cmd[0]

    if name == "/quit" or name == "/exit":
        return "quit"

    if name == "/help":
        help_text = (
            "Commands:\n"
            "  /help          — Show this help\n"
            "  /model <name>  — Switch model\n"
            "  /status        — Show session status\n"
            "  /gates on|off  — Toggle approval gates\n"
            "  /clear         — Clear conversation\n"
            "  /quit          — Exit ARA\n"
        )
        if emit:
            emit(help_text)
        return "handled"

    if name == "/model" and len(cmd) > 1:
        new_model = cmd[1]
        from .builder import infer_provider_for_model, _create_model
        provider = infer_provider_for_model(new_model) or ctx.cfg.provider
        try:
            ctx.runtime.engine.model = _create_model(provider, new_model, ctx.cfg)
            ctx.cfg.model = new_model
            if emit:
                emit(f"Switched to {new_model} ({provider})")
        except Exception as exc:
            if emit:
                emit(f"Failed to switch: {exc}")
        return "handled"

    if name == "/status":
        info = (
            f"Session: {ctx.runtime.session_id}\n"
            f"Provider: {ctx.cfg.provider}\n"
            f"Model: {_get_model_display_name(ctx.runtime.engine)}\n"
            f"Tokens: {ctx.runtime.engine.total_tokens.input_tokens}in / "
            f"{ctx.runtime.engine.total_tokens.output_tokens}out\n"
        )
        if emit:
            emit(info)
        return "handled"

    if name == "/gates":
        if len(cmd) > 1:
            ctx.cfg.approval_gates = cmd[1] != "off"
            ctx.runtime.engine.tools.approval_gates = ctx.cfg.approval_gates
            if emit:
                emit(f"Approval gates: {'ON' if ctx.cfg.approval_gates else 'OFF'}")
        return "handled"

    if name == "/clear":
        return "clear"

    if emit:
        emit(f"Unknown command: {name}. Type /help for available commands.")
    return "handled"


def run_rich_repl(ctx: ChatContext, startup_info: dict[str, str] | None = None) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.live import Live
    from rich.text import Text
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    console = Console()

    # Banner
    try:
        import pyfiglet
        banner = pyfiglet.figlet_format("ARA", font="slant")
        console.print(f"[bold cyan]{banner}[/bold cyan]", end="")
    except ImportError:
        console.print("[bold cyan]ARA — Autonomous Research Agent[/bold cyan]")

    console.print("[dim]Type your research topic to begin. /help for commands.[/dim]\n")

    if startup_info:
        for key, val in startup_info.items():
            console.print(f"  [bold]{key:>10}[/bold]  {val}")
        console.print()

    session = PromptSession(history=InMemoryHistory())
    activity_lines: list[str] = []
    lock = threading.Lock()

    def on_event(event: StepEvent) -> None:
        with lock:
            if event.event_type == "thinking":
                depth_str = f"[d{event.depth}]" if event.depth > 0 else ""
                activity_lines.append(f"  [dim]thinking{depth_str}...[/dim]")
            elif event.event_type == "tool_call":
                activity_lines.append(f"  [yellow]tool:[/yellow] {event.tool_name}")
            elif event.event_type == "tool_result":
                activity_lines.append(f"  [green]result:[/green] {event.data[:80]}")
            elif event.event_type == "subtask_start":
                activity_lines.append(f"  [cyan]subtask:[/cyan] {event.data[:80]}")
            elif event.event_type == "subtask_end":
                activity_lines.append(f"  [cyan]subtask done[/cyan]")
            elif event.event_type == "error":
                activity_lines.append(f"  [red]error:[/red] {event.data[:80]}")

            # Keep only last 20 lines
            if len(activity_lines) > 20:
                del activity_lines[:len(activity_lines) - 20]

    while True:
        try:
            user_input = session.prompt("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue

        r = dispatch_slash_command(
            user_input, ctx,
            emit=lambda line: console.print(f"[bold]ara>[/bold] {line}"),
        )
        if r == "quit":
            break
        if r in ("clear", "handled"):
            continue

        activity_lines.clear()

        # Run solve in thread so we can show activity
        result_holder: list[str] = []
        error_holder: list[str] = []

        def _run() -> None:
            try:
                res = ctx.runtime.solve(user_input, on_event=on_event)
                result_holder.append(res)
            except Exception as exc:
                error_holder.append(str(exc))

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        # Show activity while waiting
        try:
            while thread.is_alive():
                with lock:
                    if activity_lines:
                        console.print(activity_lines[-1])
                time.sleep(0.5)
        except KeyboardInterrupt:
            ctx.runtime.cancel()
            console.print("[yellow]Cancelling...[/yellow]")
            thread.join(timeout=5)

        thread.join()

        if error_holder:
            console.print(f"\n[red]Error:[/red] {error_holder[0]}")
        elif result_holder:
            console.print(f"\n[bold]ara>[/bold] {result_holder[0]}\n")

    console.print("\n[dim]Goodbye.[/dim]")
