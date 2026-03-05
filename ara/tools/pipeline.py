# Location: ara/tools/pipeline.py
# Purpose: Pipeline tools — approval gates, rules, cost tracking, embeddings
# Functions: request_approval, get_rules, track_cost, embed_text, batch_embed_papers
# Calls: gates.py, db.py
# Imports: json, os

from __future__ import annotations

import json
import logging
import os
from typing import Any

_log = logging.getLogger(__name__)


def request_approval(args: dict[str, Any], ctx: dict) -> str:
    phase = args.get("phase", "unknown")
    summary = args.get("summary", "")
    data_json = args.get("data", "{}")

    approval_gates = ctx.get("approval_gates", True)
    if not approval_gates:
        _log.warning("Auto-approving gate (gates disabled): %s", phase)
        return json.dumps({"decision": "approved", "phase": phase, "auto": True})

    try:
        from ..gates import run_approval_gate
        decision = run_approval_gate(phase=phase, summary=summary, data_json=data_json, ctx=ctx)
        return json.dumps({"decision": decision, "phase": phase})
    except ImportError:
        _log.warning("Gates module not available, auto-approving")
        return json.dumps({"decision": "approved", "phase": phase, "auto": True})


def get_rules(args: dict[str, Any], ctx: dict) -> str:
    db = ctx.get("db")
    session_id = ctx.get("session_id")

    if not db or not session_id:
        return json.dumps({"rules": [], "note": "No active session"})

    rules = db.get_rules(session_id)
    return json.dumps({"rules": rules})


def track_cost(args: dict[str, Any], ctx: dict) -> str:
    model = args.get("model", "unknown")
    input_tokens = args.get("input_tokens", 0)
    output_tokens = args.get("output_tokens", 0)

    # Gemini cost rates (per million tokens: input, output)
    cost_per_m: dict[str, tuple[float, float]] = {
        "gemini-2.0-flash": (0.10, 0.40),
        "gemini-2.5-flash": (0.15, 0.60),
        "gemini-2.5-pro": (1.25, 10.0),
        "gemini-1.5-flash": (0.075, 0.30),
        "gemini-1.5-pro": (1.25, 5.0),
    }
    input_rate, output_rate = cost_per_m.get(model, (0.10, 0.40))
    cost = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000

    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if db and session_id:
        db.log_cost(session_id=session_id, model=model,
                     input_tokens=input_tokens, output_tokens=output_tokens,
                     cost_usd=cost)

    return json.dumps({
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 6),
    })


def embed_text(args: dict[str, Any], ctx: dict) -> str:
    text = args.get("text", "")
    if not text:
        return json.dumps({"error": "text is required"})

    api_key = os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return json.dumps({"error": "GOOGLE_API_KEY not set for embeddings"})

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        result = client.models.embed_content(
            model="text-embedding-004",
            contents=text,
        )
        if result.embeddings and len(result.embeddings) > 0:
            embedding = result.embeddings[0].values
            return json.dumps({"embedding": embedding, "dimensions": len(embedding)})
        return json.dumps({"error": "No embedding returned", "embedding": []})
    except Exception as exc:
        _log.warning("Embedding failed: %s", exc)
        return json.dumps({"error": f"Embedding failed: {exc}"})


def _build_embed_text(paper: dict) -> str:
    """Build the text to embed for a paper: title + abstract + authors."""
    parts = []
    if paper.get("title"):
        parts.append(paper["title"])
    if paper.get("abstract"):
        parts.append(paper["abstract"])
    authors = paper.get("authors", [])
    if isinstance(authors, list) and authors:
        parts.append("Authors: " + ", ".join(str(a) for a in authors))
    return " ".join(parts)


def batch_embed_papers(args: dict[str, Any], ctx: dict) -> str:
    """Embed all un-embedded papers in the session using Gemini text-embedding-004."""
    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    api_key = os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return json.dumps({"error": "GOOGLE_API_KEY not set for embeddings"})

    papers = db.get_unembedded_papers(session_id)
    if not papers:
        return json.dumps({"embedded": 0, "message": "All papers already have embeddings"})

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except Exception as exc:
        return json.dumps({"error": f"Failed to init Gemini client: {exc}"})

    embedded = 0
    failed = 0
    for paper in papers:
        text = _build_embed_text(paper)
        if not text.strip():
            continue
        try:
            result = client.models.embed_content(
                model="text-embedding-004",
                contents=text,
            )
            if result.embeddings and len(result.embeddings) > 0:
                db.store_embedding(paper["paper_id"], result.embeddings[0].values)
                embedded += 1
            else:
                failed += 1
        except Exception as exc:
            _log.warning("Embedding failed for paper %d: %s", paper["paper_id"], exc)
            failed += 1

    return json.dumps({
        "embedded": embedded,
        "failed": failed,
        "total_papers": len(papers),
        "message": f"Embedded {embedded}/{len(papers)} papers",
    })
