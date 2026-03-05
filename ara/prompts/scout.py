# Location: ara/prompts/scout.py
# Purpose: Scout phase prompt — broad paper discovery across 9 APIs
# Functions: SCOUT_PROMPT
# Calls: N/A
# Imports: N/A

SCOUT_PROMPT = """## Scout Phase — Comprehensive Paper Discovery

Your task is to conduct an exhaustive search for academic papers on the given research topic, aiming for **200+ unique papers** from diverse sources.

### Search Strategy — Multi-Round with Query Reformulation

**Round 1 — Primary Query:**
Call `search_all(query="[exact research topic]")` with the topic as given.

**Round 2 — Synonym Expansion:**
Reformulate using synonyms and alternative terminology.
Example: "cardiovascular disease immigrants" → "heart disease migrant populations"

**Round 3 — Broader Scope:**
Broaden the query to capture adjacent literature.
Example: "language barriers healthcare Sweden" → "linguistic access health services Scandinavia"

**Round 4 — Narrower/Specific:**
Target specific subtopics or methodologies.
Example: "socioeconomic determinants immigrant health outcomes longitudinal studies"

**Round 5 — Cross-disciplinary:**
Search adjacent fields that may have relevant findings.
Example: "cognitive neuroscience early childhood" if topic is about child development.

**Round 6 — Methodological focus:**
Target specific study types.
Example: "meta-analysis [topic keywords]" or "longitudinal study [topic]"

### Steps

1. Execute Round 1. Review results count.
2. Execute Round 2 with reformulated query.
3. Execute Round 3 with broadened query.
4. Execute Round 4 with narrowed query.
5. If still under 150 papers, execute Round 5.
6. If still under 150 papers, execute Round 6.
7. **Maximum: 6 calls to search_all.**
8. Call `request_approval` ONCE with your comprehensive summary.

### Summary Format
After searching, provide:
- Total unique papers found (target: 200+)
- Per-source breakdown (which APIs contributed)
- Top 10 papers by citation count (title, year, citations, DOI)
- Coverage assessment: are there enough papers covering all facets of the topic?
- Query terms used in each round

### STRICT RULES
- **Maximum 6 search_all calls.** Never more.
- **Call request_approval exactly ONCE.** Not twice, not in a loop.
- Do NOT use individual search APIs — use search_all exclusively.
- Do NOT retry failed APIs. Do NOT search endlessly.
- Papers are auto-stored in the database. No extra steps needed.
- Do NOT call batch_embed_papers — embedding happens in a later phase.
- If fewer than 100 papers found after 6 rounds, note this as a coverage limitation.
"""
