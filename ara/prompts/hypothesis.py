# Location: ara/prompts/hypothesis.py
# Purpose: Hypothesis generator phase prompt
# Functions: HYPOTHESIS_PROMPT
# Calls: N/A
# Imports: N/A

HYPOTHESIS_PROMPT = """## Hypothesis Generation Phase

Your task is to generate novel research hypotheses AND specify the methodology to test them.

### Process

1. **Review verified claims and gaps** from previous phases via search_similar or read_paper.
2. **Generate hypotheses** that:
   - Address identified research gaps
   - Connect findings from different papers AND cross-domain brancher findings
   - Propose testable predictions
   - Are novel (not already established in the literature)

3. **Score each hypothesis** using score_hypothesis on 6 dimensions:
   - **Novelty** (0-1): How new is this relative to existing literature?
   - **Feasibility** (0-1): Can this be tested with available methods?
   - **Evidence strength** (0-1): How well do verified claims support this?
   - **Methodology fit** (0-1): Does a clear methodology exist?
   - **Impact** (0-1): If true, how significant would this be?
   - **Reproducibility** (0-1): Could another researcher replicate a test?

4. **For the TOP hypothesis, specify methodology:**
   - **Review framework**: PRISMA 2020 for systematic reviews
   - **Evidence grading**: GRADE framework (High/Moderate/Low/Very Low)
   - **Quality assessment**: JBI Critical Appraisal or Newcastle-Ottawa Scale
   - **Analysis approach**: Narrative synthesis, thematic analysis, or meta-analysis
   - **PICO/PEO format**: Population, Intervention/Exposure, Comparison, Outcome
   - **Inclusion criteria**: Study types, date range, language, population
   - **Exclusion criteria**: What to filter out and why

5. **Rank hypotheses** by overall score.
6. **Present top 5 hypotheses** with methodology plan for #1. Call request_approval.

### Quality Standards
- Each hypothesis must be falsifiable
- Each hypothesis must cite at least 2 supporting claims from the database
- Include at least 1 hypothesis informed by cross-domain brancher findings
- Hypotheses should vary in risk/novelty (some safe, some bold)
- The methodology plan must be detailed enough for the methods section
"""
