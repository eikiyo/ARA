# Location: ara/prompts/scout.py
# Purpose: Scout phase prompt — broad paper discovery across 9 APIs
# Functions: SCOUT_PROMPT
# Calls: N/A
# Imports: N/A

SCOUT_PROMPT = """## Scout Phase — Paper Discovery

Your task is to search for academic papers on the given research topic using ALL available search APIs.

### Strategy

1. **Query formulation**: Generate 2-3 search queries per API:
   - Primary: direct topic terms
   - Synonyms/alternative terminology
   - Broader context terms

2. **MANDATORY: Call ALL 9 search APIs in parallel in ONE turn:**
   - search_semantic_scholar
   - search_arxiv
   - search_crossref
   - search_openalex
   - search_pubmed
   - search_core
   - search_dblp
   - search_europe_pmc
   - search_base

   Call all 9 simultaneously with your primary query. Then do a second round with alternative queries for APIs that returned few results.

3. **Scope targets** (by topic breadth):
   - Narrow topic: 50-100 papers
   - Medium topic: 100-200 papers
   - Broad topic: 200+ papers

4. **After searching**: Summarize results:
   - Total papers found per source
   - Sample of top papers (title, year, citation count)
   - Assessment: is coverage sufficient or do we need more queries?

5. **Call request_approval** with your summary. The user will decide whether to proceed or refine the search.

### Rules
- Never call request_approval before completing at least one round of all 9 searches.
- If an API returns an error, note it and continue with the others.
- Report the actual count of unique papers after deduplication.
"""
