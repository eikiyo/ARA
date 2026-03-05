# Location: ara/tools/pipeline.py
# Purpose: Pipeline control (approval gates, rules, budget, embeddings)
# Functions: request_approval, get_rules, track_cost, embed_text
# Calls: ARADB, gates.py
# Imports: json, pathlib, typing

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..gates import run_approval_gate

if TYPE_CHECKING:
    from ara.db import ARADB


def request_approval(
    phase: str,
    summary: str,
    data: dict[str, Any] | None = None,
    session_id: int | None = None,
    db: ARADB | None = None,
    workspace: Path | None = None,
) -> str:
    """Request user approval at a phase gate."""
    try:
        if data is None:
            data = {}

        # Record gate in database
        if db and session_id:
            gate_id = db.insert_gate(session_id, phase, data)

        # Run interactive approval gate
        ws = workspace or Path(".")
        decision = run_approval_gate(ws, phase, summary, data)

        # Update gate in database
        if db and session_id:
            status = "approved" if decision == "approved" else "resolved"
            action = decision.split(":")[0].strip() if ":" in decision else decision
            comments = decision.split(":", 1)[1].strip() if ":" in decision else None
            db.resolve_gate(gate_id, status, action, comments)

        return json.dumps({
            "phase": phase,
            "decision": decision,
        })
    except Exception as e:
        return json.dumps({"error": f"Request approval error: {str(e)}"})


def get_rules(session_id: int, db: ARADB) -> str:
    """Get all active rules for session."""
    try:
        rules = db.get_active_rules(session_id)

        formatted = []
        for rule in rules:
            formatted.append({
                "rule_id": rule["rule_id"],
                "text": rule["rule_text"],
                "type": rule["rule_type"],
                "created_by": rule["created_by"],
            })

        return json.dumps({
            "session_id": session_id,
            "total_rules": len(formatted),
            "rules": formatted,
        })
    except Exception as e:
        return json.dumps({"error": f"Get rules error: {str(e)}"})


def track_cost(session_id: int, db: ARADB) -> str:
    """Track current session cost and remaining budget."""
    try:
        session = db.get_session(session_id)
        if not session:
            return json.dumps({"error": f"Session {session_id} not found"})

        budget_cap = float(session.get("budget_cap", 0))
        budget_spent = float(session.get("budget_spent", 0))
        remaining = budget_cap - budget_spent

        return json.dumps({
            "session_id": session_id,
            "budget_cap_usd": budget_cap,
            "budget_spent_usd": budget_spent,
            "budget_remaining_usd": remaining,
            "utilization_percent": (budget_spent / budget_cap * 100) if budget_cap > 0 else 0,
        })
    except Exception as e:
        return json.dumps({"error": f"Track cost error: {str(e)}"})


def embed_text(text: str) -> str:
    """Generate embedding vector for text (stub for now)."""
    try:
        return json.dumps({
            "status": "stub",
            "dimensions": 768,
            "message": "Embeddings not yet configured. Set ARA_EMBEDDING_MODEL to enable.",
            "text_length": len(text),
        })
    except Exception as e:
        return json.dumps({"error": f"Embed text error: {str(e)}"})
