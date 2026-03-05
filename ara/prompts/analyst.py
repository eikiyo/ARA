# Location: ara/prompts/analyst.py
# Purpose: Analyst phase prompts — triage ranking and deep reading
# Functions: ANALYST_TRIAGE_PROMPT, ANALYST_DEEP_READ_PROMPT
# Calls: N/A
# Imports: N/A

ANALYST_TRIAGE_PROMPT = """## Analyst Triage Phase — Paper Ranking

Your task is to rank all discovered papers by relevance to the research topic and select candidates for deep reading. Target: select top 40-60 papers for deep reading.

### Process

1. **Read paper metadata** using read_paper for each paper in the database.
2. **Score each paper** on a 0-1 scale considering:
   - Title and abstract relevance to the research topic
   - Citation count (higher = more established)
   - Recency (recent papers may be more relevant)
   - Source quality (peer-reviewed journals > preprints > grey literature)
   - Methodology quality (if discernible from abstract)
   - Author credibility (prolific authors in the field)

3. **Rank papers** from highest to lowest relevance score.

4. **Select papers for deep reading** (target: 40-60 papers):
   - All papers scoring > 0.7 relevance
   - Diversity of perspectives (don't select 10 papers saying the same thing)
   - Mix of: seminal works, recent contributions, methodological papers, review papers
   - Include papers from different geographic/institutional contexts

5. **Present ranking** in a clear table format and call request_approval.

### Output Format
Present a markdown table:
| Rank | Paper ID | Title | Year | Citations | Source | Relevance | Selected | Rationale |
"""

ANALYST_DEEP_READ_PROMPT = """## Analyst Deep Read Phase — Structured Claim and Data Extraction

Your task is to extract structured claims AND quantitative data from selected papers. This data will be used to build evidence tables and synthesize findings.

### Process

1. **For each selected paper**, use read_paper to get full content.
2. **Try fetch_fulltext** if full text is not cached (requires DOI).
3. **Extract claims** using extract_claims tool for each paper.

### Claim Structure — MANDATORY Fields

For each claim, provide ALL of these fields:
- **claim_text**: The core assertion (one sentence, precise)
- **claim_type**: finding | method | limitation | gap | theory | recommendation
- **confidence**: 0.0-1.0 (how confident are you in this extraction?)
- **supporting_quotes**: Exact quotes from the paper (at least one)
- **section**: Which section of the paper it came from

### Quantitative Data — Extract When Available
- **sample_size**: e.g., "N=1,234" or "342 participants"
- **effect_size**: e.g., "OR=2.3", "Cohen's d=0.45", "HR=1.8"
- **p_value**: e.g., "p<0.001", "p=0.034"
- **confidence_interval**: e.g., "95% CI: 1.2-3.4"
- **study_design**: e.g., "cross-sectional", "longitudinal cohort", "RCT", "systematic review", "meta-analysis"
- **population**: e.g., "first-generation immigrants aged 30-65"
- **country**: e.g., "Sweden", "Denmark", "multi-country EU"
- **year_range**: e.g., "2010-2018" (data collection period)

### Extraction Priorities
1. **Key findings and results** — especially quantitative results with effect sizes
2. **Novel methods or approaches** — with enough detail to compare across papers
3. **Stated limitations** — critical for the discussion section
4. **Research gaps** — explicitly stated or implied, these drive hypotheses
5. **Contradictions between papers** — flag these prominently with both paper IDs
6. **Sample characteristics** — needed for evidence tables

### Quality Standards
- Each claim MUST have at least one supporting quote
- Claims MUST be atomic (one assertion per claim)
- Contradictions between papers MUST be explicitly noted with references to both papers
- If working from abstract only (no full text), note "abstract_only" in section field
- Extract at least 3-5 claims per paper (aim for comprehensive extraction)
- Quantitative data fields can be empty if not available, but ALWAYS attempt extraction

### Cross-Paper Synthesis Notes
After extracting claims from all papers, note:
- Which findings are confirmed by multiple papers (convergent evidence)
- Which findings are contradicted (divergent evidence)
- Which gaps are identified by multiple authors
- Patterns in methodology choices across the field

Present extracted claims organized by type and call request_approval.
"""
