# Location: ara/gates.py
# Purpose: Approval gate rendering and user decision collection
# Functions: run_approval_gate, write_gate_file, render_gate_panel
# Calls: db.py
# Imports: json, pathlib

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def write_gate_file(workspace: Path, phase: str, summary: str, data_json: str) -> Path:
    gates_dir = workspace / "ara_data" / "gates"
    gates_dir.mkdir(parents=True, exist_ok=True)
    gate_file = gates_dir / f"{phase}.md"

    content = f"# Approval Gate: {phase}\n\n"
    content += f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n\n"
    content += "## Summary\n\n"
    content += summary + "\n\n"

    if data_json and data_json != "{}":
        content += "## Full Data\n\n```json\n"
        try:
            parsed = json.loads(data_json)
            content += json.dumps(parsed, indent=2, default=str)[:5000]
        except json.JSONDecodeError:
            content += data_json[:5000]
        content += "\n```\n"

    gate_file.write_text(content, encoding="utf-8")
    return gate_file


def run_approval_gate(
    phase: str, summary: str, data_json: str, ctx: dict[str, Any],
) -> str:
    workspace = ctx.get("workspace", Path("."))
    db = ctx.get("db")
    session_id = ctx.get("session_id")

    # Write gate file
    gate_file = write_gate_file(workspace, phase, summary, data_json)

    # Try Rich rendering
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text
        console = Console()

        panel_content = summary[:1000]
        panel_content += f"\n\nFull details: {gate_file}"
        console.print(Panel(
            panel_content,
            title=f"[bold]{phase.upper()} — Approval Gate[/bold]",
            border_style="cyan",
            width=min(console.width, 100),
        ))
    except ImportError:
        print(f"\n{'='*60}")
        print(f"  APPROVAL GATE: {phase.upper()}")
        print(f"{'='*60}")
        print(summary[:1000])
        print(f"\nFull details: {gate_file}")
        print(f"{'='*60}")

    # Prompt for decision via TUI input bridge (thread-safe)
    try:
        from .tui import request_tui_input
        choice = request_tui_input("[a] Approve  [r] Reject  [e] Edit > ").lower()
        if not choice:
            choice = "a"
    except (ImportError, Exception):
        # Fallback for non-TUI usage
        choice = "a"

    if choice.startswith("r"):
        try:
            reason = request_tui_input("Rejection reason: ")
        except Exception:
            reason = ""
        decision = f"rejected: {reason}" if reason else "rejected"
    elif choice.startswith("e"):
        try:
            changes = request_tui_input("Describe changes: ")
        except Exception:
            changes = ""
        decision = f"edited: {changes}" if changes else "approved"
    else:
        decision = "approved"

    # Log to DB
    if db and session_id:
        db.log_gate(
            session_id=session_id, phase=phase,
            gate_data=data_json, action=decision,
        )

    return decision
