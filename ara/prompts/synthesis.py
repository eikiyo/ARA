# Location: ara/prompts/synthesis.py
# Purpose: Pre-Writer Synthesis phase — prepare structured data for the writer
# Functions: SYNTHESIS_PROMPT
# Calls: N/A
# Imports: N/A

SYNTHESIS_PROMPT = """## Synthesis Phase — Pre-Writer Data Preparation

Your task is to prepare ALL structured data the writer needs. The writer should focus on WRITING, not data compilation. You compile the data; the writer turns it into prose.

### Step 1: Build Study Characteristics Table

Call `list_papers(compact=true)` to get all papers, then build a markdown table:

| Author(s) | Year | Country | Study Design | Sample Size | Population | Key Variables | Main Finding |
|-----------|------|---------|-------------|-------------|-----------|--------------|-------------|

Include the top 30-50 most relevant papers. Use EXACT author names as they appear in the database.

### Step 2: Build Evidence Synthesis Table with GRADE Ratings

Group claims by theme/outcome and build:

| Outcome/Theme | Supporting Studies (n) | Study Designs | Effect Size Range | GRADE Certainty | Direction | Key Finding |
|--------------|----------------------|--------------|-------------------|-----------------|-----------|-------------|

GRADE certainty ratings:
- **High**: Multiple RCTs with consistent results, no serious limitations
- **Moderate**: Downgraded RCTs or upgraded observational studies
- **Low**: Observational studies with some limitations
- **Very Low**: Observational studies with serious limitations, inconsistency, or indirectness

Rate each outcome using GRADE factors: risk of bias, inconsistency, indirectness, imprecision, publication bias.

### Step 3: Build Risk of Bias Assessment Table

For each included study, assess using the JBI critical appraisal framework:

| Study | Design | Selection Bias | Performance Bias | Detection Bias | Attrition Bias | Reporting Bias | Overall Risk |
|-------|--------|---------------|-----------------|----------------|----------------|----------------|-------------|

Rate each as: Low / Moderate / High / Unclear

### Step 4: Compile PRISMA Flow Data

From the pipeline metadata:
- Records identified through database searching: [total from scout]
- Records after duplicates removed: [total papers in DB]
- Records screened by title/abstract: [papers triaged]
- Records excluded at screening: [not selected]
- Full-text articles assessed: [papers deep read]
- Full-text articles excluded: [papers excluded after deep read]
- Studies included in final synthesis: [papers with claims]

### Step 5: Build Citation Map with Effect Sizes

Create a mapping of available citations organized by theme:
```
Theme 1: [topic]
  - (AuthorLastName, Year) — finding [effect size if available, e.g., d=0.45, OR=2.3]
  - (AuthorLastName, Year) — finding [effect size if available]

Theme 2: [topic]
  - (AuthorLastName, Year) — finding [CI: 95% x-y if available]
```

Use EXACT author last names from the database.

### Step 6: Build Structural Causal Model Notes

For the discussion section, prepare:
- **Primary causal pathway**: exposure → mediator → outcome (with evidence)
- **Reverse causation evidence**: what studies suggest reverse direction?
- **Key confounders**: list with evidence (SES, parenting quality, pre-existing conditions)
- **Natural experiments or quasi-causal evidence**: any studies that help isolate direction?
- **Effect modification**: which subgroups show stronger/weaker effects?

### Step 7: Build Inclusion/Exclusion Criteria Table

| Criteria | Inclusion | Exclusion |
|----------|----------|-----------|
| Study type | ... | ... |
| Population | ... | ... |
| Language | English | Non-English |
| Date range | ... | ... |
| Publication type | Peer-reviewed, preprints | Grey literature, editorials |

### Output

Present ALL tables and data, then call request_approval ONCE.

### STRICT RULES
- Call `list_papers(compact=true)` ONCE to get paper data
- Build ALL 7 outputs before calling request_approval
- Author names must EXACTLY match database entries
- Report effect sizes wherever available in the evidence synthesis
- GRADE ratings are MANDATORY for each outcome
"""
