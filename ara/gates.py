# Location: ara/gates.py
# Purpose: Approval gate TUI rendering and user decision collection
# Functions: write_gate_file, render_gate_panel, prompt_user_decision, run_approval_gate
# Calls: pathlib.Path, json, datetime, Rich (optional), prompt_toolkit (optional)
# Imports: __future__, json, datetime, pathlib, typing

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_gate_file(workspace: Path, phase: str, summary: str, data: dict[str, Any]) -> Path:
    """Write gate data to .ara/gates/{phase}.md for user review.

    Returns the path to the written file.
    """
    gates_dir = workspace / "ara_data" / "gates"
    gates_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Build markdown content
    lines = [
        f"# Approval Gate: {phase.replace('_', ' ').title()}",
        f"",
        f"**Timestamp:** {timestamp}",
        f"",
        f"## Summary",
        f"",
        summary,
        f"",
        f"## Full Data",
        f"",
        f"```json",
        json.dumps(data, indent=2, default=str),
        f"```",
    ]

    phase_slug = phase or "checkpoint"
    gate_path = gates_dir / f"{phase_slug}.md"
    gate_path.write_text("\n".join(lines), encoding="utf-8")
    return gate_path


def render_gate_panel(phase: str, summary: str) -> None:
    """Render an approval gate summary panel in the terminal using Rich."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text

        console = Console()
        phase_display = (phase or "checkpoint").replace("_", " ").title()
        title = f"Approval Gate: {phase_display}"
        body = summary or "(no summary provided by model)"
        panel = Panel(
            Text(body),
            title=f"[bold cyan]{title}[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
        console.print()
        console.print(panel)
        console.print()
    except ImportError:
        # Fallback if Rich not available
        print(f"\n=== Approval Gate: {phase} ===")
        print(summary)
        print("=" * 40)


def prompt_user_decision() -> str:
    """Block and prompt the user for an approval decision.

    Returns one of:
    - "approved"
    - "rejected: {reason}"
    - "edited: {changes}"

    Uses prompt_toolkit for input with completion hints.
    """
    try:
        from rich.console import Console
        console = Console()
        console.print("[bold]Options:[/bold]")
        console.print("  [green]a[/green] / [green]approve[/green]  — Accept and continue")
        console.print("  [red]r[/red] / [red]reject[/red]   — Reject with feedback")
        console.print("  [yellow]e[/yellow] / [yellow]edit[/yellow]     — Edit/modify results")
        console.print()
    except ImportError:
        print("Options: (a)pprove, (r)eject, (e)dit")

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.key_binding import KeyBindings

        kb = KeyBindings()

        @kb.add("escape")
        def _esc(event: Any) -> None:
            """Treat ESC as approve."""
            event.current_buffer.text = "a"
            event.current_buffer.validate_and_handle()

        session: Any = PromptSession(key_bindings=kb)
        print()  # ensure clean line before prompt
        response = session.prompt(HTML("<b>Decision&gt;</b> ")).strip().lower()
    except (ImportError, EOFError, KeyboardInterrupt):
        try:
            response = input("Decision> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "approved"

    if response in ("a", "approve", "yes", "y", ""):
        return "approved"

    if response in ("r", "reject", "no", "n"):
        try:
            from prompt_toolkit import PromptSession as _PS
            reason = _PS().prompt("Reason for rejection> ").strip()
        except (ImportError, EOFError, KeyboardInterrupt):
            try:
                reason = input("Reason for rejection> ").strip()
            except (EOFError, KeyboardInterrupt):
                reason = "No reason given"
        return f"rejected: {reason}" if reason else "rejected: No reason given"

    if response in ("e", "edit"):
        try:
            from prompt_toolkit import PromptSession as _PS
            changes = _PS().prompt("Describe changes> ").strip()
        except (ImportError, EOFError, KeyboardInterrupt):
            try:
                changes = input("Describe changes> ").strip()
            except (EOFError, KeyboardInterrupt):
                changes = "No changes specified"
        return f"edited: {changes}" if changes else "edited: No changes specified"

    # Default: treat unknown input as approval
    return "approved"


def run_approval_gate(
    workspace: Path,
    phase: str,
    summary: str,
    data: dict[str, Any],
) -> str:
    """Full approval gate flow: write file, render panel, prompt user.

    Returns the user's decision string.
    """
    # 1. Write gate file
    gate_path = write_gate_file(workspace, phase, summary, data)

    # 2. Render panel
    render_gate_panel(phase, summary)

    try:
        from rich.console import Console
        console = Console()
        console.print(f"[dim]  saved → {gate_path.name}[/dim]")
    except ImportError:
        pass

    # 3. Prompt for decision
    decision = prompt_user_decision()

    return decision
