# Location: ara/prompts/hypothesis.py
# Purpose: Hypothesis generator phase prompt
# Functions: HYPOTHESIS_PROMPT
# Calls: N/A
# Imports: N/A

HYPOTHESIS_PROMPT = """## Hypothesis Generation Phase

Your task is to generate novel research hypotheses from verified claims and identified gaps.

### Process

1. **Review verified claims and gaps** from previous phases.
2. **Generate hypotheses** that:
   - Address identified research gaps
   - Connect findings from different papers
   - Propose testable predictions
   - Are novel (not already established in the literature)

3. **Score each hypothesis** using score_hypothesis on 6 dimensions:
   - **Novelty** (0-1): How new is this relative to existing literature?
   - **Feasibility** (0-1): Can this be tested with available methods?
   - **Evidence strength** (0-1): How well do verified claims support this?
   - **Methodology fit** (0-1): Does a clear methodology exist?
   - **Impact** (0-1): If true, how significant would this be?
   - **Reproducibility** (0-1): Could another researcher replicate a test?

4. **Rank hypotheses** by overall score.
5. **Present top hypotheses** (aim for at least 5) and call request_approval.
   The user will select which hypothesis to pursue.

### Quality Standards
- Each hypothesis must be falsifiable.
- Each hypothesis must cite at least 2 supporting claims.
- Hypotheses should vary in risk/novelty (some safe, some bold).
"""
