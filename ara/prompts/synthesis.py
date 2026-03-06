# Location: ara/prompts/synthesis.py
# Purpose: Pre-Writer Synthesis phase — construct argument architecture from evidence
# Functions: SYNTHESIS_PROMPT
# Calls: N/A
# Imports: N/A

SYNTHESIS_PROMPT = """## Synthesis Phase — Argument Architecture Construction

This is the most underestimated phase. It's not "organize data for the writer" —
it's CONSTRUCT THE ARGUMENT ARCHITECTURE. The writer receives a blueprint, not a
pile of data. If you fail here, the writer produces a literature dump, not a paper.

---

### STEP 1: Load Evidence Base (MANDATORY FIRST)

Call ALL of these before building anything:
- `list_claims()` — ALL extracted claims with effect sizes and study designs
- `list_papers(compact=true)` — full paper inventory with metadata
- `get_risk_of_bias_table()` — quality assessments for all papers
- `get_grade_table()` — GRADE evidence certainty ratings
- `search_similar(text="<theme>")` per major theme — embedding-based retrieval
- `read_paper(paper_id=ID, include_fulltext=true)` for the 5-10 most important papers

---

### STEP 2: Select the Central Thesis

This paper must have ONE thesis — not "a comprehensive overview." Choose one:

**A) Resolution thesis**: "The evidence resolves this debate in favor of [X],
   under conditions [Y], because [mechanism Z]."
   Best when: Evidence is consistent, GRADE moderate+, clear direction.

**B) Moderator thesis**: "The conflicting evidence on [X] is explained by
   [moderator M]: when M is present, effect is positive; when M is absent,
   effect reverses."
   Best when: Evidence is mixed, contradictions mappable to a variable.

**C) Framework thesis**: "Existing approaches to [X] conflate [A] and [B],
   which are distinct phenomena requiring different frameworks."
   Best when: Field uses fuzzy categories, your review unpacks them.

**D) Mechanism thesis**: "The established [X→Y] relationship operates through
   [previously unidentified mechanism M], as revealed by integrating evidence
   from [streams A and B]."
   Best when: Cross-domain evidence reveals hidden causal pathways.

State your thesis in ONE sentence. This sentence drives every subsequent decision.

---

### STEP 3: Build Evidence-to-Argument Map

For each section of the paper, define WHAT ARGUMENT it makes and WHICH EVIDENCE
supports it. This is the core architecture.

```
INTRODUCTION
  Argument: [The problem exists, it matters, and current understanding is incomplete]
  Evidence needed:
    - Scale/significance: (Author, Year) — [specific finding]
    - Current state: (Author, Year) — [what's known]
    - Gap: (Author, Year) — [what's missing/wrong]
  Transition to Lit Review: [connecting sentence]

LITERATURE REVIEW
  Stream 1: [Name]
    Argument: [What this body of work collectively establishes]
    Evidence:
      - (Author, Year) — [finding, effect size if available]
      - (Author, Year) — [finding, effect size if available]
    Internal contradiction (if any): [description]
    Bridge to Stream 2: [how this connects]

  Stream 2: [Name]
    Argument: [What this body of work collectively establishes]
    Evidence: [same format]
    Bridge to Stream 3: [how this connects]

  Stream 3: [Name]
    Argument: [same format]
    SYNTHESIS GAP: "Together, Streams 1-3 reveal [the gap the thesis addresses]"

RESULTS / PROPOSITIONS
  Theme 1: [Name]
    Argument: [What the combined evidence shows for this theme]
    Primary evidence: [claims with effect sizes]
    Contradicting evidence: [claims that disagree, with explanation]
    GRADE rating: [certainty level for this theme]
    Table: [what table supports this theme]

  Theme 2: [same format]

DISCUSSION
  Theoretical implication: [What changes in theory]
    Evidence: [findings that support this change]
  Practical implication: [What changes in practice]
    Evidence: [findings that support this recommendation]
  Limitation → Future research: [each limitation paired with a study design]
```

Every argument point MUST have at least 2 supporting citations.
Every citation MUST exist in the database — verify with `list_papers`.

---

### STEP 4: Citation Allocation Map

Assign EVERY available paper to at least one section. Unused papers are wasted evidence.

```
INTRODUCTION (target: {min_cites_intro}+ citations):
  - (AuthorLastName, Year) — establishes background context
  - (AuthorLastName, Year) — defines the problem scope
  ...

LITERATURE REVIEW (target: {min_cites_lit}+ citations):
  Stream 1: [topic]
    - (AuthorLastName, Year) — finding [effect size if available]
    - (AuthorLastName, Year) — finding [effect size if available]
  Stream 2: [topic]
    - (AuthorLastName, Year) — finding [CI: 95% x-y if available]
  ...

RESULTS (target: {min_cites_results}+ citations):
  - (AuthorLastName, Year) — key result for evidence table
  ...

DISCUSSION (target: {min_cites_discussion}+ citations):
  - (AuthorLastName, Year) — comparison with existing review
  - (AuthorLastName, Year) — supports causal interpretation
  ...

CONCLUSION (target: 3+ citations):
  - (AuthorLastName, Year) — future research direction
  ...
```

Use EXACT author last names from the database.
TARGET: {min_quality_citations}+ unique citations total.
The writer will follow this map — assign enough papers to meet section minimums.

---

### STEP 5: Table and Figure Plan

Specify EVERY table and figure the paper will contain:

```
TABLE 1: Study Characteristics
  Location: Results section
  Columns: Author/Year | Country | Design | Sample Size | Population | Key Variables | Main Finding | Preprint?
  Sources: [list which papers populate this]

TABLE 2: GRADE Evidence Summary
  Location: Results section
  Columns: Outcome | N Studies | Designs | RoB | Inconsistency | Indirectness | Imprecision | Pub Bias | Certainty | Effect
  Sources: rate_grade_evidence() calls

TABLE 3: Risk of Bias Assessment
  Location: Results section
  Columns: Study | Design | Selection | Performance | Detection | Attrition | Reporting | Overall
  Sources: get_risk_of_bias_table()

TABLE 4: [Theme-specific evidence synthesis table]
  Location: Results section
  Columns: [appropriate for the theme]

FIGURE 1: PRISMA Flow Diagram
  Location: Methods section
  Data: Pipeline metadata (records identified, screened, excluded, included)

FIGURE 2: [Framework/Causal model diagram if conceptual paper]
  Location: Discussion section
```

Mark papers from arXiv, bioRxiv, medRxiv, SSRN, or preprint servers as "Yes"
in the Preprint column. Preprint status affects GRADE certainty.

---

### STEP 6: Build Structural Causal Model

For the discussion section, build a comprehensive causal analysis:

**6a. Causal chain diagram** (text format):
```
Exposure → Mediator A → Outcome
         → Mediator B → Outcome
Confounder 1 → Exposure + Outcome
Confounder 2 → Mediator A + Outcome
```
Name each link and cite the study(ies) that support it.

**6b. Evidence by causal direction**:
- **Forward causation** (exposure → outcome): List studies with designs that support
  forward direction (longitudinal, experimental, dose-response)
- **Reverse causation** (outcome → exposure): List studies suggesting reverse direction
- **Bidirectional**: Any evidence of reciprocal effects?
- **Verdict**: Which direction has stronger evidence? Cite specific study designs.

**6c. Confounder analysis**:
| Confounder | Studies Controlling For It | Studies NOT Controlling | Effect on Results |
|------------|--------------------------|----------------------|-------------------|

**6d. Natural experiments / quasi-causal evidence**:
- Any policy changes, natural experiments, twin studies, instrumental variables?
- These provide the strongest evidence for causation — highlight them.

**6e. Effect modification by subgroup**:
| Subgroup | Effect Stronger | Effect Weaker | Effect Reversed | Evidence |
|----------|----------------|---------------|-----------------|----------|

---

### STEP 7: Tension Documentation

Every review has tensions — evidence that doesn't fit neatly. Document them:

```
TENSION 1: [Description]
  Evidence FOR: (Author, Year) — [finding]
  Evidence AGAINST: (Author, Year) — [finding]
  Possible resolution: [moderator, methodological difference, population difference]
  Writer instruction: "Navigate this by [specific approach]"

TENSION 2: [same format]
```

For each tension, specify the GRADE-calibrated language the writer should use:
- GRADE High: "The evidence demonstrates..."
- GRADE Moderate: "The evidence suggests..."
- GRADE Low: "Preliminary evidence indicates..."
- GRADE Very Low: "While limited, initial findings point toward..."

---

### STEP 8: Rate GRADE Evidence for Each Outcome (MANDATORY tool calls)

Group claims by theme/outcome. For EACH major outcome, call `rate_grade_evidence`:

```json
rate_grade_evidence({
  "outcome": "Theme description",
  "n_studies": N,
  "study_designs": "cohort, cross-sectional",
  "risk_of_bias_rating": "serious",
  "inconsistency": "not serious",
  "indirectness": "not serious",
  "imprecision": "serious",
  "publication_bias": "undetected",
  "effect_size_range": "range",
  "certainty": "low",
  "direction": "direction of effect",
  "notes": "Justification for downgrades"
})
```

Rate EACH outcome (aim for 5-8 outcomes). This is MANDATORY — the writer needs
stored GRADE data.

**CRITICAL**: `n_studies` must count ONLY studies that were deep-read and have
extracted claims for this outcome. Do NOT count papers from the screening pool.

---

### STEP 9: Domain Stratification by Research Maturity

Classify the evidence base into maturity tiers:

| Maturity Level | Description | Studies (n) | Example Studies |
|---------------|-------------|-------------|-----------------|
| **Established** | Replicated findings with consistent results across 5+ studies | | |
| **Emerging** | Promising findings from 2-4 studies, not yet replicated widely | | |
| **Preliminary** | Single-study findings or pilot data | | |
| **Contested** | Studies with contradictory results — no consensus | | |

---

### STEP 10: Evidence Concentration Warnings

For EACH outcome/theme, check:
- How many unique studies contribute evidence?
- Does any single study provide >40% of the claims for this outcome?

| Outcome | Total Studies | Dominant Study (if any) | % of Evidence | Concentration Risk |
|---------|--------------|------------------------|---------------|-------------------|

**Rules:**
- **LOW** (5+ studies, no single study >30%): Robust evidence base
- **MODERATE** (3-4 studies, or one study >30%): Flag in limitations
- **HIGH** (1-2 studies, or one study >50%): Writer must use hedging language
- **CRITICAL** (single study): Cannot be presented as established finding

---

### STEP 11: Quantitative Summary Table

| Outcome | N Studies | Total N | Effect Size Range | Median Effect | CI Range | p-value Range | Heterogeneity |
|---------|----------|---------|-------------------|---------------|----------|---------------|---------------|

- If no effect sizes available, state "No quantitative data — qualitative synthesis only"
- If only 1 study reports a number, flag as "Single-study estimate"
- Calculate direction consistency: what % of studies agree on direction?

---

### STEP 12: Geographic Comparison Table

| Region | N Studies | Predominant Design | Sample Size Range | Effect Direction | Effect Magnitude | Key Differences |
|--------|----------|-------------------|-------------------|-----------------|-----------------|-----------------|
| North America | | | | | | |
| Europe | | | | | | |
| Asia-Pacific | | | | | | |
| Other/Global | | | | | | |

For each region, note whether effect sizes differ from global average and why.

---

### STEP 13: Novel Contribution Statement

Write a 2-3 sentence thesis statement answering: "What does this paper contribute
that no prior work has?"

This must be specific and verifiable:
- BAD: "This review provides a comprehensive overview of the literature"
- GOOD: "This review is the first to systematically apply the [FRAMEWORK] framework
  to [TOPIC], revealing that [SPECIFIC FINDING] — a pattern obscured by prior
  reviews' failure to distinguish [X from Y]."

Reference the novelty framework from the hypothesis phase.

---

### STEP 14: Advisory Board Instructions

Based on your analysis, write instructions for the advisory board on:
1. **Tone**: What level of confidence is appropriate given the evidence?
2. **Emphasis**: Which findings deserve the most space? Which are minor?
3. **Caution zones**: Where must the writer be careful about overclaiming?
4. **Structural risks**: Which sections are hardest to write well given the evidence?

---

### Output

Present ALL outputs (thesis, evidence-to-argument map, citation allocation,
table plan, causal model, tension documentation, GRADE ratings, stratification,
concentration warnings, quantitative summary, geographic comparison, contribution
statement, advisory instructions) as text output.

### STRICT RULES
- Call `list_claims()` FIRST — this is your primary evidence source. Every table,
  every GRADE rating, every finding MUST trace back to an extracted claim.
- Call `list_papers(compact=true)` to get paper metadata for citation formatting.
- Call `get_risk_of_bias_table()` to retrieve stored RoB assessments.
- For the top 5-10 most important papers, call `read_paper(paper_id=ID, include_fulltext=true)`
  to read their full text.
- Use `search_similar(text="<theme>")` to find papers related to each outcome theme.
- Call `rate_grade_evidence(...)` for EACH major outcome (5-8 outcomes minimum).
- Build ALL outputs in one response.
- Author names must EXACTLY match database entries.
- Report effect sizes wherever available — pull these from claims, not from memory.
- GRADE ratings are MANDATORY — use the tool to store them, not just text output.
- When ALL outputs are built, call `write_section(section='synthesis_data', content=ALL_TEXT)`
  to persist them. The writer will load this data. This is MANDATORY.
- When done, stop.
"""
