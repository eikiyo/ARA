# Location: scripts/extract_claims_or1.py
# Purpose: Bulk claim extraction via OpenRouter Gemini Flash Lite — DESC order (mid-high)
# Functions: Extract claims starting from middle going up
# Calls: openai (OpenRouter), ara/central_db.py
# Imports: json, time, logging

from __future__ import annotations
import json, logging, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openai import OpenAI
from ara.central_db import CentralDB
from ara.credentials import load_api_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_log = logging.getLogger("or_extract_1")

_PROMPT = """Extract ALL findings, theories, methods, limitations, and gaps from this paper.
Return a JSON array of claim objects. Each claim must have:
- claim_text: The core claim (1-3 sentences)
- claim_type: "finding", "theory", "method", "limitation", or "gap"
- confidence: a float 0.0-1.0
- section: Which part of the paper
- study_design: If applicable
- sample_size: If mentioned
- country: If mentioned

Extract EVERYTHING. Return ONLY a JSON array, no markdown fences.

Title: {title}
Text: {text}"""

def _pc(raw):
    if isinstance(raw, (int, float)): return float(raw)
    if isinstance(raw, str):
        m = {"high": 0.9, "very high": 0.95, "medium": 0.6, "moderate": 0.6, "low": 0.3}
        if raw.lower() in m: return m[raw.lower()]
        try: return float(raw)
        except ValueError: pass
    return 0.5

def _parse(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    for attempt in [text, None]:
        if attempt is None:
            s, e = text.find("["), text.rfind("]")
            if s < 0 or e <= s:
                s, e = text.find("{"), text.rfind("}")
                if s < 0 or e <= s: return []
            attempt = text[s:e + 1]
        try:
            obj = json.loads(attempt)
            if isinstance(obj, list): return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    if isinstance(v, list): return v
                if obj.get("claim_text"): return [obj]
            return []
        except json.JSONDecodeError: continue
    return []

def main():
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key: _log.error("Set OPENROUTER_API_KEY"); sys.exit(1)
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    central_db = CentralDB()

    rows = central_db._conn.execute(
        "SELECT p.paper_id, p.title, p.abstract, p.doi, p.full_text "
        "FROM papers p WHERE p.full_text IS NOT NULL AND p.full_text != '' "
        "ORDER BY p.paper_id ASC"
    ).fetchall()
    total = len(rows)
    # Take the first quarter (by paper_id ASC)
    quarter = rows[:total // 4]

    papers = []
    for row in quarter:
        if not central_db.is_paper_fully_extracted(row["title"] or ""):
            papers.append(dict(row))

    if not papers: _log.info("All done"); return
    _log.info("OR-1: Extracting %d papers (quarter 1 by paper_id)", len(papers))

    total_claims = 0; done = 0; failed = 0; t0 = time.time()
    for i, paper in enumerate(papers):
        if central_db.is_paper_fully_extracted(paper["title"] or ""): continue
        ft = paper["full_text"] or ""; ab = paper["abstract"] or ""
        text = ft[:4000] if len(ft) > 200 else ab
        if not text or len(text) < 100: continue
        try:
            resp = client.chat.completions.create(
                messages=[{"role": "user", "content": _PROMPT.format(title=paper["title"], text=text)}],
                model="google/gemini-2.5-flash-lite", max_tokens=4096, temperature=0.1,
            )
            content = resp.choices[0].message.content or ""
            claims = _parse(content)
            if not claims: failed += 1
            else:
                cc = []
                for c in claims:
                    if not isinstance(c, dict) or not c.get("claim_text"): continue
                    cc.append({"paper_title": paper["title"], "paper_doi": paper.get("doi", ""),
                        "claim_text": c["claim_text"], "claim_type": c.get("claim_type", "finding"),
                        "confidence": _pc(c.get("confidence", 0.5)),
                        "supporting_quotes": json.dumps(c.get("supporting_quotes", [])),
                        "section": c.get("section", ""), "sample_size": str(c.get("sample_size", "")),
                        "effect_size": str(c.get("effect_size", "")), "p_value": str(c.get("p_value", "")),
                        "confidence_interval": str(c.get("confidence_interval", "")),
                        "study_design": str(c.get("study_design", "")),
                        "population": str(c.get("population", "")), "country": str(c.get("country", "")),
                        "year_range": str(c.get("year_range", "")),
                    })
                if cc:
                    res = central_db.store_claims(cc, session_topic="prewarm_or1")
                    total_claims += res.get("stored", 0)
                    central_db.mark_paper_fully_extracted(paper["title"]); done += 1
        except Exception as exc:
            failed += 1
            if "429" in str(exc) or "rate" in str(exc).lower():
                _log.warning("Rate limited — 30s"); time.sleep(30)
            else: _log.warning("[%d] %s", i+1, exc)
        time.sleep(1)
        if (i+1) % 25 == 0:
            elapsed = time.time() - t0; rate = done / elapsed * 60 if elapsed > 0 else 0
            _log.info("[%d/%d] %d claims, %d done (%.0f/min, %d fail)", i+1, len(papers), total_claims, done, rate, failed)

    _log.info("OR-1 DONE: %d claims from %d papers in %.1f min", total_claims, done, (time.time()-t0)/60)

if __name__ == "__main__": main()
