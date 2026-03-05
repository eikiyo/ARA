# Location: ara/prompts/analyst.py
# Purpose: Analyst phase prompts — triage ranking and deep reading
# Functions: ANALYST_TRIAGE_PROMPT, ANALYST_DEEP_READ_PROMPT
# Calls: N/A
# Imports: N/A

ANALYST_TRIAGE_PROMPT = """## Analyst Triage Phase — Paper Ranking

Your task is to rank all discovered papers by relevance to the research topic and select candidates for deep reading. Target: select **top 80-120 papers** for deep reading.

### Process

1. **Call `list_papers()` ONCE** to get ALL papers with their metadata (title, abstract, year, citations, source). This returns everything in a single call — do NOT use read_paper for triage.

2. **Score each paper** on a 0-1 scale considering:
   - Title and abstract relevance to the research topic (most important)
   - Citation count (higher = more established)
   - Recency (recent papers may be more relevant)
   - Source quality (peer-reviewed journals > preprints > grey literature)
   - Methodology quality (if discernible from abstract)
   - REMOVE irrelevant papers (e.g., papers from wrong fields that were false-positive matches)
   - EXCLUDE any papers flagged as retracted in the verification phase

   **MANDATORY EXCLUSION CRITERIA — score 0.0 and selected=false:**
   - **Systematic reviews / meta-analyses** — these are secondary sources; include only primary studies (exception: keep 3-5 landmark reviews for Discussion comparison, but mark them `selected=false` for deep reading)
   - **Protocols / study designs without results** — no data to extract
   - **Editorials / commentaries / opinion pieces** — not empirical evidence
   - **Papers with no publication year** — cannot be properly cited
   - **Papers whose population does NOT match the research topic** — e.g., if the topic is about healthcare professionals, exclude studies on general adults, students, or children
   - **Papers outside the specified date range** — check the protocol's date range
   - **Duplicate studies** — same dataset published in multiple papers; keep only the most complete version

3. **Rank papers** from highest to lowest relevance score.

4. **Select papers for deep reading** (target: 80-120 papers):
   - All papers scoring > 0.6 relevance
   - Diversity of perspectives (don't select 10 papers saying the same thing)
   - Mix of: seminal works, recent contributions, methodological papers, review papers
   - Include papers from different geographic/institutional contexts

5. **ALWAYS call rate_papers** with ratings for EVERY paper in the batch. No exceptions.

### CRITICAL RULES
- Step 1: Call list_papers() to get papers.
- Step 2: Evaluate each paper's title and abstract for relevance.
- Step 3: Call rate_papers() with ALL ratings. This is MANDATORY — never skip it.
- Do NOT call read_paper — the title and abstract from list_papers is sufficient.
- Set selected=true for papers scoring >= 0.6 relevance.
- Set selected=false for papers scoring < 0.6.
"""

ANALYST_DEEP_READ_PROMPT = """## Analyst Deep Read Phase — Structured Claim and Data Extraction

Your task is to extract structured claims AND quantitative data from selected papers. This data builds evidence tables and synthesizes findings. Target: **150+ claims** from **50+ papers**.

### Process — FOLLOW THIS EXACT PATTERN FOR EVERY PAPER

For each paper:
1. Call `read_paper(paper_id=N)` to get full content
2. Read the title, abstract, and any full text carefully
3. Call `extract_claims(paper_id=N, claims=[...])` with 3-5 claim objects
4. Call `assess_risk_of_bias(paper_id=N, ...)` with bias ratings for the study

### EXACT Tool Call Format — COPY THIS STRUCTURE

```json
extract_claims({
  "paper_id": 42,
  "claims": [
    {
      "claim_text": "Children with >2 hours daily screen time showed 23% lower language scores",
      "claim_type": "finding",
      "confidence": 0.9,
      "supporting_quotes": ["Screen exposure exceeding 2h/day was associated with significantly lower expressive language scores (β = −0.23, p < 0.01)"],
      "section": "results",
      "sample_size": "N=1,847",
      "effect_size": "β = −0.23",
      "p_value": "p < 0.01",
      "confidence_interval": "95% CI: −0.31 to −0.15",
      "study_design": "longitudinal cohort",
      "population": "children aged 2-5 years",
      "country": "Canada",
      "year_range": "2015-2019"
    },
    {
      "claim_text": "Interactive screen content (e.g., educational apps) showed no negative effect on vocabulary",
      "claim_type": "finding",
      "confidence": 0.8,
      "supporting_quotes": ["Interactive digital media use was not significantly associated with vocabulary scores (OR = 1.02, 95% CI: 0.87-1.19)"],
      "section": "results",
      "sample_size": "N=1,847",
      "effect_size": "OR = 1.02",
      "study_design": "longitudinal cohort",
      "population": "children aged 2-5 years",
      "country": "Canada"
    },
    {
      "claim_text": "Cross-sectional designs dominate the field, limiting causal inference",
      "claim_type": "limitation",
      "confidence": 0.95,
      "supporting_quotes": ["The preponderance of cross-sectional studies precludes definitive causal conclusions"],
      "section": "discussion"
    },
    {
      "claim_text": "No studies examined the moderating role of parental co-viewing in children under 3",
      "claim_type": "gap",
      "confidence": 0.85,
      "supporting_quotes": ["Future research should examine whether parental co-viewing mediates screen time effects in the youngest age groups"],
      "section": "discussion"
    }
  ]
})
```

### Step 2: Assess Risk of Bias (MANDATORY for every paper)

After extracting claims from a paper, ALSO call `assess_risk_of_bias` to rate the study's methodological quality:

```json
assess_risk_of_bias({
  "paper_id": 42,
  "framework": "JBI",
  "selection_bias": "low",
  "performance_bias": "moderate",
  "detection_bias": "low",
  "attrition_bias": "high",
  "reporting_bias": "low",
  "overall_risk": "moderate",
  "notes": "High attrition (32% dropout) limits confidence; otherwise well-designed cohort"
})
```

Rate each domain as: **low** / **moderate** / **high** / **unclear**
- **Selection bias**: Was the sample representative? Random/consecutive selection?
- **Performance bias**: Were groups treated equally? Blinding adequate?
- **Detection bias**: Were outcomes measured objectively? Assessor blinding?
- **Attrition bias**: Was follow-up complete? How was missing data handled?
- **Reporting bias**: Were all pre-specified outcomes reported?
- **Overall risk**: Holistic judgment across all domains

### CRITICAL: claim_text MUST be a non-empty string
- NEVER send claims with empty claim_text
- NEVER send an empty claims array
- NEVER send claims as `"__dict__"` or other non-string values
- Every claim MUST have claim_text, claim_type, confidence, and supporting_quotes

### Claim Types (use exactly these)
- **finding**: A result, outcome, or empirical observation
- **method**: A methodology, measurement tool, or analytical approach
- **limitation**: A stated weakness or constraint
- **gap**: An identified research gap or unexplored question
- **theory**: A theoretical framework or model referenced
- **recommendation**: A policy or practice recommendation

### Extraction Priorities
1. **Key findings with effect sizes** — the most valuable data for the paper
2. **Study design and sample characteristics** — needed for evidence tables
3. **Stated limitations** — critical for discussion section
4. **Research gaps** — drive hypotheses and future directions
5. **Contradictions between papers** — flag with both paper IDs
6. **Methods used** — needed for methods section comparison

### Quality Standards
- Extract at least 3-5 claims per paper (aim for 5)
- If working from abstract only, set section to "abstract_only"
- Quantitative fields can be empty if not available
- Target: 150+ total claims across all papers
- Aim to process at least 50 papers

When done, output a text summary with the count of claims extracted.
"""
