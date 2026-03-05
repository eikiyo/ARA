# Location: ara/prompts/protocol.py
# Purpose: Pre-registration protocol phase — PROSPERO-style methodology declaration
# Functions: PROTOCOL_PROMPT
# Calls: N/A
# Imports: N/A

PROTOCOL_PROMPT = """## Protocol Phase — Pre-Registration (PROSPERO Format)

Your task is to draft a systematic review protocol BEFORE the main search begins. This creates an audit trail proving methodology was defined a priori, which is required for Nature/Lancet-grade papers.

### Protocol Structure (PROSPERO CRD Format)

Generate a complete protocol with ALL of these sections:

#### 1. Review Title
Clear, specific title following PRISMA-P guidelines.

#### 2. PICO/PEO Framework
- **P**opulation: Who is being studied?
- **I**ntervention/**E**xposure: What is the intervention or exposure?
- **C**omparison: What is the comparator (if applicable)?
- **O**utcome: What outcomes are measured?

#### 3. Research Questions
- Primary question (1 sentence)
- Secondary questions (2-3 sentences)

#### 4. Search Strategy
- Databases to search: Semantic Scholar, arXiv, CrossRef, OpenAlex, PubMed, CORE, DBLP, Europe PMC, BASE
- Search terms and Boolean operators for each database
- Date range filter (specify)
- Language restrictions

#### 5. Inclusion Criteria
| Criterion | Include | Exclude |
|-----------|---------|---------|
| Study design | ... | ... |
| Population | ... | ... |
| Exposure | ... | ... |
| Outcome | ... | ... |
| Language | English | Non-English |
| Date range | ... | Before ... |
| Publication type | Peer-reviewed, preprints | Grey lit, editorials |

#### 6. Study Selection Process
- Stage 1: Title/abstract screening (automated with relevance scoring)
- Stage 2: Full-text assessment against inclusion criteria
- Disagreement resolution process

#### 7. Quality Assessment
- Framework: JBI Critical Appraisal Checklists
- Risk of bias domains assessed
- How quality scores inform synthesis

#### 8. Data Extraction
- Variables to extract from each study
- How data will be organized (study characteristics table format)

#### 9. Data Synthesis
- Approach: Narrative synthesis / thematic analysis / meta-analysis
- How GRADE certainty will be assessed
- Handling of heterogeneity
- Subgroup analyses planned

#### 10. Timeline
- Protocol registration: Day 0
- Search execution: Day 0-1
- Screening: Day 1
- Data extraction: Day 1-2
- Synthesis and writing: Day 2-3

### Output
Write the full protocol and save it using write_section(section='protocol', content=YOUR_TEXT).
Then call request_approval with the protocol summary.

### STRICT RULES
- Call list_papers(compact=true) first to understand what papers are available
- The protocol MUST be specific enough to be reproducible
- Use write_section to save — this creates the audit trail
- Call request_approval exactly ONCE
"""
