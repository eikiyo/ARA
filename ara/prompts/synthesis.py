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

Include the top 30-50 most relevant papers. Use EXACT author names as they appear in the database — the writer will use these for citations.

### Step 2: Build Evidence Synthesis Table

Group claims by theme and build:

| Theme | Supporting Studies | Evidence Strength | Key Finding | Effect Sizes | Consensus |
|-------|-------------------|------------------|-------------|-------------|-----------|

Identify 4-6 major themes from the claims.

### Step 3: Compile PRISMA Flow Data

From the pipeline metadata, compile:
- Records identified through database searching: [total from scout]
- Records after duplicates removed: [total papers in DB]
- Records screened by title/abstract: [papers triaged]
- Records excluded at screening: [papers not selected]
- Full-text articles assessed: [papers deep read]
- Full-text articles excluded (with reasons): [papers excluded after deep read]
- Studies included in final synthesis: [papers with claims]

### Step 4: Build Citation Map

Create a mapping of available citations organized by theme:
```
Theme 1: [topic]
  - (AuthorLastName, Year) — key finding summary
  - (AuthorLastName, Year) — key finding summary

Theme 2: [topic]
  - (AuthorLastName, Year) — key finding summary
```

Use EXACT author last names from the database. The writer will copy these directly.

### Step 5: Compile Methods Metadata

Prepare:
- List of databases searched (all 9 APIs)
- Date range of search
- Search queries used (from scout phase)
- Inclusion/exclusion criteria (based on triage decisions)
- Quality assessment approach used

### Step 6: Build Inclusion/Exclusion Criteria Table

| Criteria | Inclusion | Exclusion |
|----------|----------|-----------|
| Study type | ... | ... |
| Population | ... | ... |
| Language | English | Non-English |
| Date range | ... | ... |
| Publication type | Peer-reviewed, preprints | Grey literature, editorials |

### Output

Present ALL tables and data, then call request_approval ONCE.

The writer will receive this data and use it directly — so accuracy of author names and years is CRITICAL.

### STRICT RULES
- Call `list_papers(compact=true)` ONCE to get paper data
- Build ALL 6 outputs before calling request_approval
- Author names must EXACTLY match database entries
- Do NOT fabricate data — only use what's in the database
"""
