# Location: ara/prompts/scout.py
# Purpose: Scout phase prompt — broad paper discovery across 9 APIs
# Functions: SCOUT_PROMPT
# Calls: N/A
# Imports: N/A

SCOUT_PROMPT = """## Scout Phase — Paper Discovery

Your task is to search for academic papers on the given research topic.

### STRICT Rules

1. **Call each API EXACTLY ONCE per round.** Do not call the same API multiple times with different queries — one call per API is enough. Each API already returns 20 results.

2. **Round 1 — Call all 9 APIs in ONE turn with the same primary query:**
   - search_semantic_scholar(query="...")
   - search_arxiv(query="...")
   - search_crossref(query="...")
   - search_openalex(query="...")
   - search_pubmed(query="...")
   - search_core(query="...")
   - search_dblp(query="...")
   - search_europe_pmc(query="...")
   - search_base(query="...")

   That's 9 tool calls total. NOT 20, NOT 40 — exactly 9.

3. **Round 2 (optional)** — If Round 1 returned fewer than 50 papers total, do ONE more round with a reformulated query. Again, max 9 calls.

4. **Maximum total search calls: 18** (2 rounds x 9 APIs). STOP searching after that.

5. Papers are automatically stored in the database. You do NOT need to re-search to "save" them.

6. After searching, summarize:
   - Total unique papers found per source
   - Sample of top 5 papers (title, year, citations)
   - Whether coverage is sufficient

7. **Call request_approval** with your summary. The user decides whether to proceed.

### DO NOT
- Do NOT call search_semantic_scholar 10+ times with different queries
- Do NOT search endlessly trying to hit a paper count target
- Do NOT retry APIs that returned errors
"""
