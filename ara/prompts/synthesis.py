# Location: ara/prompts/synthesis.py
# Purpose: Pre-Writer Synthesis phase — prepare structured data for the writer
# Functions: SYNTHESIS_PROMPT
# Calls: N/A
# Imports: N/A

SYNTHESIS_PROMPT = """## Synthesis Phase — Pre-Writer Data Preparation

Your task is to prepare ALL structured data the writer needs. The writer should focus on WRITING, not data compilation. You compile the data; the writer turns it into prose.

### Step 1: Build Study Characteristics Table

Call `list_papers(compact=true)` to get all papers, then build a markdown table:

| Author(s) | Year | Country | Study Design | Sample Size | Population | Key Variables | Main Finding | Preprint? |
|-----------|------|---------|-------------|-------------|-----------|--------------|-------------|-----------|

Include the top 30-50 most relevant papers. Use EXACT author names as they appear in the database.
- Mark papers from arXiv, bioRxiv, medRxiv, SSRN, or preprint servers as "Yes" in the Preprint column.
- Papers from peer-reviewed journals: "No". Unknown: "Unclear".
- Preprint status affects GRADE certainty (downgrade for publication bias if many preprints).

### Step 2: Rate GRADE Evidence for Each Outcome (MANDATORY tool calls)

Group claims by theme/outcome. For EACH major outcome, call `rate_grade_evidence` to store the rating:

```json
rate_grade_evidence({
  "outcome": "Screen time and language development",
  "n_studies": 12,
  "study_designs": "cohort, cross-sectional",
  "risk_of_bias_rating": "serious",
  "inconsistency": "not serious",
  "indirectness": "not serious",
  "imprecision": "serious",
  "publication_bias": "undetected",
  "effect_size_range": "β = −0.15 to −0.31",
  "certainty": "low",
  "direction": "negative association",
  "notes": "Downgraded for risk of bias (mostly observational) and imprecision (wide CIs)"
})
```

GRADE certainty ratings:
- **High**: Multiple RCTs with consistent results, no serious limitations
- **Moderate**: Downgraded RCTs or upgraded observational studies
- **Low**: Observational studies with some limitations
- **Very Low**: Observational studies with serious limitations, inconsistency, or indirectness

Rate EACH outcome (aim for 5-8 outcomes). This is MANDATORY — the writer needs stored GRADE data.

**CRITICAL**: `n_studies` must count ONLY studies that were deep-read and have extracted claims for this outcome. Do NOT count papers from the screening pool. If you are unsure, use the lower number. The system will cap n_studies at the total number of included papers automatically.

### Step 3: Retrieve and Format Risk of Bias Table

Call `get_risk_of_bias_table()` to retrieve the RoB assessments stored during deep reading. Format the returned data as:

| Study | Design | Selection Bias | Performance Bias | Detection Bias | Attrition Bias | Reporting Bias | Overall Risk |
|-------|--------|---------------|-----------------|----------------|----------------|----------------|-------------|

If no RoB data is stored, note this as a limitation and build the table from available study design info.

### Step 4: Compile PRISMA Flow Data

From the pipeline metadata:
- Records identified through database searching: [total from scout]
- Records after duplicates removed: [total papers in DB]
- Records screened by title/abstract: [papers triaged]
- Records excluded at screening: [not selected]
- Full-text articles assessed: [papers deep read]
- Full-text articles excluded: [papers excluded after deep read]
- Studies included in final synthesis: [papers with claims]

### Step 5: Build Citation Map with Effect Sizes AND Section Assignments

Create a mapping of available citations organized by theme AND assigned to paper sections:
```
INTRODUCTION (target: 8+ citations):
  - (AuthorLastName, Year) — establishes background context
  - (AuthorLastName, Year) — defines the problem scope

LITERATURE REVIEW (target: 20+ citations):
  Theme 1: [topic]
    - (AuthorLastName, Year) — finding [effect size if available]
    - (AuthorLastName, Year) — finding [effect size if available]
  Theme 2: [topic]
    - (AuthorLastName, Year) — finding [CI: 95% x-y if available]

RESULTS (target: 8+ citations):
  - (AuthorLastName, Year) — key result for evidence table

DISCUSSION (target: 10+ citations):
  - (AuthorLastName, Year) — comparison with existing review
  - (AuthorLastName, Year) — supports causal interpretation

CONCLUSION (target: 3+ citations):
  - (AuthorLastName, Year) — future research direction
```

Use EXACT author last names from the database. TARGET: 60+ unique citations total.
The writer will follow this map — assign enough papers to meet section minimums.

### Step 6: Build Structural Causal Model (Detailed)

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
- **Forward causation** (exposure → outcome): List studies with designs that support forward direction (longitudinal, experimental, dose-response)
- **Reverse causation** (outcome → exposure): List studies suggesting reverse direction
- **Bidirectional**: Any evidence of reciprocal effects?
- **Verdict**: Which direction has stronger evidence? Cite specific study designs.

**6c. Confounder analysis**:
For each major confounder, create a row:
| Confounder | Studies Controlling For It | Studies NOT Controlling | Effect on Results |
|------------|--------------------------|----------------------|-------------------|

**6d. Natural experiments / quasi-causal evidence**:
- Any policy changes, natural experiments, twin studies, instrumental variables?
- These provide the strongest evidence for causation — highlight them.

**6e. Effect modification by subgroup**:
| Subgroup | Effect Stronger | Effect Weaker | Effect Reversed | Evidence |
|----------|----------------|---------------|-----------------|----------|

### Step 7: Sensitivity and Subgroup Analysis Notes

Prepare structured notes for the writer's Discussion section:

**Sensitivity analysis** — How would results change if:
- Only low-risk-of-bias studies are included? (Use RoB table to identify these)
- Only studies with N > 500 are included?
- Only peer-reviewed (non-preprint) sources are included?

**Subgroup analysis** — Do effects differ by:
- Study design (RCT vs observational)?
- Population characteristics (age, gender, SES)?
- Geographic region?
- Measurement instrument?

For each subgroup, note the direction and magnitude of any differential effect.

### Step 8: Domain Stratification by Research Maturity

Classify the evidence base into maturity tiers:

| Maturity Level | Description | Studies (n) | Example Studies |
|---------------|-------------|-------------|-----------------|
| **Established** | Replicated findings with consistent results across 5+ studies | | |
| **Emerging** | Promising findings from 2-4 studies, not yet replicated widely | | |
| **Preliminary** | Single-study findings or pilot data | | |
| **Contested** | Studies with contradictory results — no consensus | | |

This stratification helps the writer modulate confidence language:
- Established: "The evidence consistently demonstrates..."
- Emerging: "Preliminary evidence suggests..."
- Preliminary: "One study found..."
- Contested: "Results are mixed, with [A] finding X while [B] found Y..."

### Step 9: Novel Contribution Statement

Write a 2-3 sentence thesis statement answering: "What does this review contribute that no prior review has?"

This must be specific and verifiable:
- BAD: "This review provides a comprehensive overview of the literature"
- GOOD: "This review is the first to systematically apply the [FRAMEWORK] framework to [TOPIC], revealing that [SPECIFIC FINDING] — a pattern obscured by prior reviews' failure to distinguish [X from Y]."

Reference the novelty framework from the hypothesis phase (INVERSION / MISSING LINK / MODERATOR / etc.) and ground it in the GRADE-rated evidence.

### Step 10: Evidence Concentration Warnings

For EACH outcome/theme from Step 2, check:
- How many unique studies contribute evidence?
- Does any single study provide >40% of the claims for this outcome?

Build a warning table:

| Outcome | Total Studies | Dominant Study (if any) | % of Evidence | Concentration Risk |
|---------|--------------|------------------------|---------------|-------------------|
| ... | 12 | None | — | Low |
| ... | 3 | Smith (2021) | 55% | HIGH — over-reliance |
| ... | 1 | Jones (2023) | 100% | CRITICAL — single-study |

**Rules:**
- **LOW** (5+ studies, no single study >30%): Robust evidence base
- **MODERATE** (3-4 studies, or one study >30%): Flag in limitations
- **HIGH** (1-2 studies, or one study >50%): Writer must use hedging language ("one study found...", "preliminary evidence suggests...")
- **CRITICAL** (single study): Cannot be presented as established finding. Must say "a single study by [Author] (Year) reported..."

The writer MUST match confidence language to concentration risk. This prevents over-reliance on any single study.

### Step 11: Quantitative Summary Table

For each outcome, compile ALL available quantitative data into one table:

| Outcome | N Studies | Total N (participants) | Effect Size Range | Median Effect | CI Range | p-value Range | Heterogeneity |
|---------|----------|----------------------|-------------------|---------------|----------|---------------|---------------|

**Rules:**
- If no effect sizes available for an outcome, state "No quantitative data — qualitative synthesis only"
- If only 1 study reports a number, flag as "Single-study estimate"
- Calculate direction consistency: what % of studies agree on direction?
- This table goes directly into the Results section

### Step 12: Geographic Comparison Table

Build a cross-regional evidence comparison:

| Region | N Studies | Predominant Design | Sample Size Range | Effect Direction | Effect Magnitude | Key Differences |
|--------|----------|-------------------|-------------------|-----------------|-----------------|-----------------|
| North America | | | | | | |
| Europe | | | | | | |
| Asia-Pacific | | | | | | |
| Other/Global | | | | | | |

**For each region, note:**
- Are effect sizes larger or smaller than global average?
- Do different regions use different measurement instruments?
- Are there regulatory/cultural factors that might explain regional differences?
- If a region has <3 studies, flag as "insufficient for regional comparison"

This prevents the paper from claiming geographic diversity without actually comparing across regions.

### Step 13: Build Inclusion/Exclusion Criteria Table

| Criteria | Inclusion | Exclusion |
|----------|----------|-----------|
| Study type | ... | ... |
| Population | ... | ... |
| Language | English | Non-English |
| Date range | ... | ... |
| Publication type | Peer-reviewed, preprints | Grey literature, editorials |

### Output

Present ALL tables and data as text output.

### STRICT RULES
- Call `list_papers(compact=true)` ONCE to get paper data
- Call `get_risk_of_bias_table()` to retrieve stored RoB assessments
- Call `rate_grade_evidence(...)` for EACH major outcome (5-8 outcomes minimum)
- Build ALL 13 outputs in one response
- Author names must EXACTLY match database entries
- Report effect sizes wherever available in the evidence synthesis
- GRADE ratings are MANDATORY — use the tool to store them, not just text output
- When done, output all tables as text and stop.
"""
