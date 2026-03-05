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
**CRITICAL: Call ALL search tools in a SINGLE tool call turn.** Do NOT call them one at a time. \
Issue all 9 search calls simultaneously so they run in parallel. This saves minutes.

Available search tools (call all at once with the same query):
- search_semantic_scholar — Broad coverage, strong ranking
- search_arxiv — Preprints in CS, physics, math, stats
- search_crossref — Broad multidisciplinary with DOI validation
- search_openalex — Largest open index, good coverage
- search_pubmed — Biomedical/life sciences
- search_core — Aggregates OA content globally
- search_dblp — Computer science conferences and journals
- search_europe_pmc — Life sciences and biomedicine
- search_base — Multidisciplinary OA index

Example — call ALL in one turn:
```
search_semantic_scholar(query="your query", limit=25)
search_arxiv(query="your query", limit=15)
search_crossref(query="your query", limit=15)
search_openalex(query="your query", limit=15)
search_pubmed(query="your query", limit=15)
search_core(query="your query", limit=10)
search_dblp(query="your query", limit=10)
search_europe_pmc(query="your query", limit=15)
search_base(query="your query", limit=10)
```

Then repeat with different query variations (also all 9 in parallel per variation).
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

## Step 5: Documentation & Approval
Record in your report:
- Total papers found: [count]
- Papers per source database: [breakdown]
- Date range of papers: [from-to]
- Search queries used: [list]
- Duplicate papers removed: [count]
- Final de-duplicated count: [count]
- Any access limitations: [paywall papers, missing abstracts, etc.]

Call request_approval with a summary of papers found per source and total count. \
User will approve proceeding to analyst triage or request modifications to the search.
"""
