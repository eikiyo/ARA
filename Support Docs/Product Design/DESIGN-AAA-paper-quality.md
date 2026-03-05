# DESIGN: AAA-Grade Paper Quality System

## Goal
Transform ARA's paper output from undergraduate-level (3.5/10) to tier-A journal quality (8.5+/10).

## Decisions Made

### Model Strategy
- Use `gemini-2.5-pro` for writer and paper-critic phases (deep reasoning needed)
- Keep `gemini-2.0-flash` for search-heavy phases (scout, brancher) where speed matters
- New config field: `writer_model` defaults to `gemini-2.5-pro`

### Paper Volume
- Scout target: 100+ papers discovered, 40-60 cited in final paper
- 3-4 search rounds with query reformulation (synonyms, MeSH terms, broader/narrower)
- Minimum acceptance: 50 papers from 4+ sources

### Fulltext Strategy
- Add `pymupdf` dependency for PDF parsing
- Increase `_MAX_FULLTEXT_CHARS` to 25,000 (5 pages)
- Extract structured data: effect sizes, sample sizes, p-values, CI, study design

### Writing System — 4-Pass
1. Outline with citation plan
2. Rough draft (all sections)
3. Paper critic review + revision feedback
4. Polished final with critic-approved quality

### Citation Verification (3-tier)
1. First: attempt to find the real paper via search_all
2. Then: strip the hallucinated citation and flag it
3. Finally: reject the section if >20% citations unverifiable

### Tables & Figures
- Full Cochrane-style evidence tables (study characteristics + findings + effect sizes)
- PRISMA flow diagram as SVG embedded in HTML, ASCII art in markdown
- Methodology comparison matrix
- Evidence synthesis summary table

### Per-Section Requirements
| Section | Min Words | Requirements |
|---------|-----------|--------------|
| Abstract | 250-300 | Structured: Background/Objective/Methods/Results/Conclusion |
| Introduction | 800 | Research gap, explicit RQs, contribution statement |
| Literature Review | 1500 | Thematic organization, comparison table, cross-referencing |
| Methods | 1000 | Boolean search strategy, inclusion/exclusion table, PRISMA data, quality assessment framework |
| Results | 1200 | 2+ tables, quantitative data, effect sizes where available |
| Discussion | 1000 | 3+ existing review comparisons, limitations subsection, policy implications, future directions |
| Conclusion | 400 | Contributions, limitations, 3+ specific future research questions |
| References | N/A | 40-60 APA-formatted entries + BibTeX export |

### Post-Draft Quality Gate (Paper Critic — Phase 9)
Dimensions scored:
- Citation density (target: 1 citation per 2-3 sentences in lit review)
- Methodological rigor
- Argumentation depth
- Structural completeness
- Quantitative content
- Writing quality
- Logical flow between sections
- Argument novelty vs existing reviews
- Methodological transparency

Minimum thresholds:
- 40+ unique citations from DB
- 6000+ total words
- 2+ tables present
- 0 hallucinated citations
- All sections present with minimum word counts met

Revision: 2 per section, 3 for full paper max

### Self-Audit Scorecard
Saved alongside paper as `quality_audit.json`:
- Citation count, word count per section, table count
- Unique sources, missing sections
- PRISMA stage numbers
- Quality scores from paper critic

### Structured Claim Extraction
New fields: `sample_size`, `effect_size`, `p_value`, `study_design`, `population`, `country`, `year_range`

### Cross-Referencing
Writer must synthesize: "Smith (2020) confirms Johnson (2018)..." rather than paper-by-paper reporting

### Methodology Auto-Population
Methods section pulls actual data from DB: databases searched, date ranges, hit counts, screening results, inclusion/exclusion applied

### Discussion Requirements
All four: (a) comparison with 3+ existing reviews, (b) limitations subsection, (c) policy/practice implications, (d) future research with testable questions

## Files to Modify
1. `ara/config.py` — add writer_model, increase limits
2. `ara/prompts/scout.py` — expand search strategy
3. `ara/prompts/writer.py` — complete rewrite with AAA requirements
4. `ara/prompts/critic.py` — add paper critic mode
5. `ara/prompts/manager.py` — add Phase 9, revision loops
6. `ara/prompts/analyst.py` — structured claim extraction
7. `ara/tools/writing.py` — citation verification, quality checks, tables
8. `ara/tools/research.py` — structured claim fields
9. `ara/tools/papers.py` — increase fulltext limits, PDF parsing
10. `ara/output.py` — PRISMA diagram, APA references, tables, audit scorecard
11. `ara/engine.py` — dual-model support for writer phases
12. `ara/builder.py` — build writer model
13. `ara/model.py` — support multiple model instances
14. `ara/db.py` — new columns for structured claims, PRISMA stats
15. `ara/tools/defs.py` — new tool definitions

## New Files
1. `ara/prompts/paper_critic.py` — paper-level critic (distinct from hypothesis critic)
2. `ara/tools/quality.py` — audit scorecard, PRISMA generator, citation validator
