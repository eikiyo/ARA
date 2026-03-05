# Location: ara/tools/pipeline.py
# Purpose: Pipeline control (approval gates, rules, budget, embeddings, phase output)
# Functions: request_approval, get_rules, track_cost, embed_text, save_phase_output
# Calls: ARADB, gates.py
# Imports: json, pathlib, typing, datetime

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..gates import run_approval_gate
from ..logging import get_logger

_log = get_logger("pipeline")

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


def save_phase_output(
    phase: str,
    content: str,
    workspace: Path | None = None,
) -> str:
    """Save phase output to ara_data/phases/{phase}.md for visibility."""
    try:
        ws = workspace or Path(".")
        phases_dir = ws / "ara_data" / "phases"
        phases_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        phase_slug = phase.lower().replace(" ", "_") or "output"
        filename = f"{phase_slug}.md"
        filepath = phases_dir / filename

        # Add header with metadata
        header = f"# Phase: {phase}\n"
        header += f"Generated: {datetime.now(timezone.utc).isoformat()}\n\n---\n\n"

        filepath.write_text(header + content, encoding="utf-8")
        _log.info("Phase output saved: %s (%d bytes)", filepath, len(content))

        return json.dumps({
            "status": "saved",
            "phase": phase,
            "file_path": str(filepath),
            "bytes_written": len(content),
        })
    except Exception as e:
        _log.error("Failed to save phase output for %s: %s", phase, e)
        return json.dumps({"error": f"Save phase output error: {str(e)}"})


def score_branches(branches: list[dict[str, Any]], session_id: int, db: ARADB) -> str:
    """Score and rank branch proposals (1-10 on relevance, novelty, feasibility)."""
    try:
        if not branches:
            return json.dumps({
                "error": "No branches provided",
                "branches_scored": 0,
            })

        # Return instruction for agent to score branches
        return json.dumps({
            "task": "score_branches",
            "session_id": session_id,
            "branches_to_score": branches,
            "instruction": (
                "Score each branch proposal on a scale of 1-10 across three dimensions:\n"
                "1. Relevance: How directly does this branch address the core hypothesis?\n"
                "2. Novelty: How original or unexplored is this direction?\n"
                "3. Feasibility: How likely are we to find good evidence in this branch?\n\n"
                "Provide a composite score (average of the three) for each branch. "
                "Return as JSON array of objects with branch text/type, individual scores, "
                "composite score, and brief justification."
            ),
            "branches_count": len(branches),
        })
    except Exception as e:
        return json.dumps({"error": f"Score branches error: {str(e)}"})


def prune_hypotheses(session_id: int, keep_top_n: int, db: ARADB) -> str:
    """Drop lowest-scored hypotheses beyond top N."""
    try:
        all_hypotheses = db.get_hypotheses(session_id)

        if len(all_hypotheses) <= keep_top_n:
            return json.dumps({
                "session_id": session_id,
                "action": "no_pruning_needed",
                "total_hypotheses": len(all_hypotheses),
                "keep_top_n": keep_top_n,
                "message": f"Only {len(all_hypotheses)} hypotheses exist; no pruning needed.",
            })

        # Sort by overall_score descending
        sorted_hyps = sorted(all_hypotheses, key=lambda h: h['overall_score'], reverse=True)
        top_hyps = sorted_hyps[:keep_top_n]
        bottom_hyps = sorted_hyps[keep_top_n:]

        # Mark bottom hypotheses as pruned
        for hyp in bottom_hyps:
            db.update_hypothesis(hyp['hypothesis_id'], status='pruned')

        return json.dumps({
            "session_id": session_id,
            "action": "pruned",
            "total_hypotheses_before": len(all_hypotheses),
            "total_hypotheses_after": keep_top_n,
            "pruned_count": len(bottom_hyps),
            "kept_hypotheses": [
                {
                    "hypothesis_id": h['hypothesis_id'],
                    "text": h['hypothesis_text'],
                    "score": h['overall_score'],
                }
                for h in top_hyps
            ],
        })
    except Exception as e:
        return json.dumps({"error": f"Prune hypotheses error: {str(e)}"})
