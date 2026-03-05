# Location: ara/prompts/scout.py
# Purpose: Scout phase — broad discovery across multiple academic databases
# Functions: None (constant export)
# Calls: search_papers, request_approval
# Imports: None

SCOUT_PROMPT = """\
# Scout Phase — Paper Discovery

Your mission: Conduct a comprehensive search across multiple academic databases to identify \
all relevant papers on the research topic. Cast a wide net, but document sources meticulously.

## Step 1: Query Formulation
Starting from the user's research question, generate multiple query variations:
- **Synonyms**: Use 2-3 alternative terms (e.g., "deep learning" vs. "neural networks")
- **Broader terms**: Search parent concepts (e.g., "machine learning" if topic is "supervised learning")
- **Narrower terms**: Search specific subtopics
- **Author-based**: If key authors are known, search their work
- **Domain-specific**: Use jargon and technical terms alongside plain language

Document each query and which databases you'll use it with.

## Step 2: Multi-Database Search (PARALLEL)

### MANDATORY: You MUST call ALL 9 search tools in ONE SINGLE tool-call turn.
**This is the most important instruction in this prompt.** If you call them one at a time, \
the search takes 10x longer. The engine parallelizes tool calls made in the same turn.

Your FIRST action after thinking about queries must be a turn with ALL 9 tool calls:

search_semantic_scholar(query="your query", limit=25)
search_arxiv(query="your query", limit=15)
search_crossref(query="your query", limit=15)
search_openalex(query="your query", limit=15)
search_pubmed(query="your query", limit=15)
search_core(query="your query", limit=10)
search_dblp(query="your query", limit=10)
search_europe_pmc(query="your query", limit=15)
search_base(query="your query", limit=10)

**DO NOT use think() between searches. DO NOT call one search, then another. ALL 9 IN ONE TURN.**

If some searches fail (rate limits, errors), that is fine — continue with whatever results you got. \
Do NOT retry failed searches individually; the retry logic is built into the tools.

Then repeat with a second query variation (also all 9 in parallel).
Store each result with: title, authors, year, DOI, abstract, source_database.

## Step 3: Deduplication & Aggregation
- Identify duplicate papers across databases (same DOI, same title+authors)
- Keep one canonical record, note all sources that returned it
- Sort papers by: relevance score (from search engine), citation count (if available), year

## Step 4: Scope Assessment
Target paper counts depend on topic breadth:
- **Narrow topics** (e.g., "specific protein interactions"): 50-100 papers
- **Medium topics** (e.g., "deep learning for medical imaging"): 100-200 papers
- **Broad topics** (e.g., "artificial intelligence"): 200+ papers (may need filtering)

If you find >500 papers, apply additional filters (year, peer-review status, citation count) \
to keep the set manageable.

## Step 5: Documentation (Final Output)
Your final text response MUST include this report:
- Total papers found: [count]
- Papers per source database: [breakdown]
- Date range of papers: [from-to]
- Search queries used: [list]
- Duplicate papers removed: [count]
- Final de-duplicated count: [count]
- Any access limitations: [paywall papers, missing abstracts, etc.]

**Do NOT call request_approval yourself.** The manager handles approval gates. \
Just return your report as a text response when done.
"""
