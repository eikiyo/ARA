# Location: ara/prompts/scout.py
# Purpose: Scout phase prompt — broad paper discovery across 9 APIs
# Functions: SCOUT_PROMPT
# Calls: N/A
# Imports: N/A

SCOUT_PROMPT = """## Scout Phase — Paper Discovery

Your task is to search for academic papers on the given research topic.

### How to Search

**Use `search_all(query="...")` — ONE call searches all 9 APIs in parallel.**

This is the ONLY search tool you need. It returns combined results from Semantic Scholar, arXiv, CrossRef, OpenAlex, PubMed, CORE, DBLP, Europe PMC, and BASE in a single call.

### Steps

1. Call `search_all(query="your topic")` — ONE tool call, ONE turn.
2. Review results: check `per_source` counts and `total`.
3. If fewer than 30 papers, call `search_all` ONCE more with a reformulated query.
4. **Maximum: 2 calls to search_all. Then STOP searching.**
5. Call `request_approval` ONCE with your summary. Wait for the user's decision.

### Summary Format
After searching, provide:
- Total unique papers found per source
- Top 5 papers (title, year, citations)
- Whether coverage is sufficient

### STRICT RULES
- **Maximum 2 search_all calls.** Never more.
- **Call request_approval exactly ONCE.** Not twice, not in a loop.
- Do NOT use individual search APIs (search_semantic_scholar, etc.) — use search_all.
- Do NOT retry failed APIs. Do NOT search endlessly.
- Papers are auto-stored in the database. No extra steps needed.
"""
