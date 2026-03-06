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
    _log.info("APPROVAL GATE: phase=%s | summary=%d chars", phase, len(summary))
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
        "gemini-3.1-flash-lite-preview": (0.10, 0.40),
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

    api_key = _get_api_key()
    if not api_key:
        return json.dumps({"error": "GOOGLE_API_KEY not set for embeddings"})

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        result = client.models.embed_content(
            model="gemini-embedding-001",
            contents=text,
        )
        if result.embeddings and len(result.embeddings) > 0:
            embedding = result.embeddings[0].values
            return json.dumps({"embedding": embedding, "dimensions": len(embedding)})
        return json.dumps({"error": "No embedding returned", "embedding": []})
    except Exception as exc:
        _log.warning("Embedding failed: %s", exc)
        return json.dumps({"error": f"Embedding failed: {exc}"})


def _get_api_key() -> str | None:
    """Get Google API key from env vars or credential store."""
    key = os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if key:
        return key
    try:
        from ..credentials import load_api_key
        return load_api_key()
    except Exception:
        return None


def _extract_section(full_text: str, section_names: list[str], max_words: int = 300) -> str:
    """Extract a section from full text by matching heading patterns.
    Returns up to max_words from the first matching section."""
    import re
    text_lower = full_text.lower()
    for name in section_names:
        # Match common heading patterns: "# Introduction", "1. Introduction", "INTRODUCTION", "Introduction\n"
        patterns = [
            rf'(?:^|\n)#+\s*{name}\b',           # Markdown headings
            rf'(?:^|\n)\d+\.?\s*{name}\b',        # Numbered headings
            rf'(?:^|\n){name.upper()}\s*\n',       # ALL CAPS headings
            rf'(?:^|\n){name}\s*\n',               # Plain headings
        ]
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                start = match.end()
                # Extract text until next heading or max_words
                remaining = full_text[start:]
                next_heading = re.search(r'\n(?:#+\s|\d+\.?\s+[A-Z]|[A-Z]{4,}\s*\n)', remaining)
                if next_heading:
                    remaining = remaining[:next_heading.start()]
                words = remaining.split()[:max_words]
                return " ".join(words)
    return ""


def _build_embed_text(paper: dict) -> str:
    """Build the text to embed for a paper: title + abstract + authors + key sections from full text."""
    parts = []
    if paper.get("title"):
        parts.append(paper["title"])
    if paper.get("abstract"):
        parts.append(paper["abstract"])
    authors = paper.get("authors", [])
    if isinstance(authors, list) and authors:
        parts.append("Authors: " + ", ".join(str(a) for a in authors))

    # Extract key sections from full text if available
    full_text = paper.get("full_text")
    if full_text and len(full_text) > 200:
        intro = _extract_section(full_text, ["introduction", "background"], max_words=250)
        if intro:
            parts.append("Introduction: " + intro)
        methods = _extract_section(full_text, ["method", "methods", "methodology", "research design"], max_words=200)
        if methods:
            parts.append("Methods: " + methods)
        conclusion = _extract_section(full_text, ["conclusion", "conclusions", "concluding remarks"], max_words=200)
        if conclusion:
            parts.append("Conclusion: " + conclusion)

    # Gemini embedding-001 has ~2048 token limit; cap at ~1800 words to stay safe
    combined = " ".join(parts)
    words = combined.split()
    if len(words) > 1800:
        combined = " ".join(words[:1800])
    return combined


def batch_embed_papers(args: dict[str, Any], ctx: dict) -> str:
    """Embed all un-embedded papers in the session using Gemini gemini-embedding-001."""
    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if not db or not session_id:
        return json.dumps({"error": "Database or session not available"})

    api_key = _get_api_key()
    if not api_key:
        return json.dumps({"error": "GOOGLE_API_KEY not set for embeddings"})

    papers = db.get_unembedded_papers(session_id)
    if not papers:
        return json.dumps({"embedded": 0, "message": "All papers already have embeddings"})

    # LOCAL-FIRST: Check central DB for cached embeddings before calling Gemini
    from_central = 0
    central_db = ctx.get("central_db")
    if central_db:
        for paper in list(papers):
            doi = paper.get("doi")
            if not doi:
                continue
            cp = central_db.get_paper_by_doi(doi)
            if cp and cp.get("embedding"):
                try:
                    emb = cp["embedding"] if isinstance(cp["embedding"], list) else json.loads(cp["embedding"])
                    db.store_embedding(paper["paper_id"], emb)
                    from_central += 1
                except Exception:
                    pass
        if from_central:
            _log.info("EMBED BATCH: %d/%d embeddings found in central DB", from_central, len(papers))
            papers = db.get_unembedded_papers(session_id)
            if not papers:
                return json.dumps({"embedded": from_central, "from_central_db": from_central,
                                   "message": "All embeddings found in central DB"})

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
                model="gemini-embedding-001",
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
        "embedded": embedded + from_central,
        "from_central_db": from_central,
        "from_api": embedded,
        "failed": failed,
        "total_papers": len(papers),
        "message": f"Embedded {embedded}/{len(papers)} papers",
    })
