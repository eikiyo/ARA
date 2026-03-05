# Location: ara/prompts/analyst.py
# Purpose: Analyst phase prompts — triage ranking and deep reading
# Functions: ANALYST_TRIAGE_PROMPT, ANALYST_DEEP_READ_PROMPT
# Calls: N/A
# Imports: N/A

ANALYST_TRIAGE_PROMPT = """## Analyst Triage Phase — Paper Ranking

Your task is to rank all discovered papers by relevance to the research topic.

### Process

1. **Read paper metadata** using read_paper for each paper in the database.
2. **Score each paper** on a 0-1 scale considering:
   - Title and abstract relevance to the research topic
   - Citation count (higher = more established)
   - Recency (recent papers may be more relevant)
   - Source quality (peer-reviewed journals > preprints)
   - Methodology quality (if discernible from abstract)

3. **Rank papers** from highest to lowest relevance score.

4. **Recommend papers for deep reading**: Select the top papers based on:
   - High relevance scores
   - Diversity of perspectives (don't select 10 papers saying the same thing)
   - Mix of seminal works and recent contributions

5. **Present ranking** in a clear table format and call request_approval.
   The user will select which papers to deep-read.

### Output Format
Present a markdown table:
| Rank | Paper ID | Title | Year | Citations | Relevance | Recommendation |
"""

ANALYST_DEEP_READ_PROMPT = """## Analyst Deep Read Phase — Claim Extraction

Your task is to extract structured claims from selected papers.

### Process

1. **For each selected paper**, use read_paper to get full content.
2. **Try fetch_fulltext** if full text is not cached (requires DOI).
3. **Extract claims** using extract_claims tool for each paper.
4. **For each claim, identify:**
   - **claim_text**: The core assertion (one sentence)
   - **claim_type**: finding | method | limitation | gap
   - **confidence**: 0.0-1.0 (how confident are you in this extraction?)
   - **supporting_quotes**: Exact quotes from the paper
   - **section**: Which section of the paper it came from

5. **Look for:**
   - Key findings and results
   - Novel methods or approaches
   - Stated limitations
   - Research gaps (explicitly stated or implied)
   - Contradictions between papers

6. **Present extracted claims** organized by type and call request_approval.

### Quality Standards
- Each claim must have at least one supporting quote.
- Claims must be atomic (one assertion per claim).
- Contradictions between papers must be explicitly noted.
- If working from abstract only (no full text), note this limitation.
"""
