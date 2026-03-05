# Location: ara/prompts/critic.py
# Purpose: Critic phase — evaluate hypothesis against evidence and make recommendation
# Functions: None (constant export)
# Calls: request_approval
# Imports: None

CRITIC_PROMPT = """\
# Critic Phase — Hypothesis Evaluation & Recommendation

Your mission: Critically evaluate the hypothesis against all accumulated evidence \
(verified claims, branch findings, contradictions). Make a clear recommendation: \
approve, request revision, or reject.

## Step 1: Comprehensive Evidence Review
Gather all evidence relevant to the hypothesis:
- **Verified claims**: From verifier phase (organized by status: verified/likely/contradicted)
- **Branch findings**: From brancher phase (lateral, methodological, analogical, convergent)
- **Contradictions**: From analyst phase (claims that conflict with hypothesis)
- **Gaps**: Identified in all phases

Organize by dimension (see scoring below).

## Step 2: Multi-Dimensional Scoring
Score the hypothesis on 8 dimensions (0-1 scale). For each, document \
evidence AND reasoning.

### Dimension 1: Novelty (0-1)
- Does the hypothesis present genuinely new insights?
- Are similar hypotheses already established in the literature?
- Scoring:
  - 0.0-0.2: Already proven or obvious extension of prior work
  - 0.4-0.6: Incremental advance or combination of known ideas
  - 0.7-0.9: Novel synthesis with new angle
  - 1.0: Truly original and unexpected

Evidence: [list papers that claim similar ideas, or note if genuinely novel]

### Dimension 2: Evidence Strength (0-1)
- How well do verified claims support the hypothesis?
- Are contradictions overwhelming or manageable?
- Scoring:
  - 0.0-0.2: Contradicted by verified claims or no supporting evidence
  - 0.4-0.6: Mixed evidence; some support but with significant contradictions
  - 0.7-0.9: Strong direct support from verified/likely claims
  - 1.0: Multiple high-credibility sources directly confirm

Evidence: [list verified claims supporting it, note any contradictions]

### Dimension 3: Feasibility (0-1)
- Can this hypothesis realistically be tested?
- Are required methods available? Data accessible?
- Scoring:
  - 0.0-0.2: Impossible to test with current methods or data
  - 0.4-0.6: Testable but requires significant new methods/data
  - 0.7-0.9: Testable with existing methods, though data collection is non-trivial
  - 1.0: Directly testable with readily available data and standard methods

Evidence: [describe what test would look like, what obstacles exist]

### Dimension 4: Coherence (0-1)
- Is the hypothesis internally consistent?
- Do the proposed mechanisms make logical sense?
- Scoring:
  - 0.0-0.2: Logically contradictory or implausible
  - 0.4-0.6: Plausible but with questionable assumptions
  - 0.7-0.9: Logically sound with minor uncertainties
  - 1.0: Entirely coherent and mechanistically clear

Evidence: [identify any logical issues, state reasoning for score]

### Dimension 5: Cross-Domain Support (0-1)
- Do multiple independent domains support this finding?
- How strong are the branch findings?
- Scoring:
  - 0.0-0.2: Only supported in one domain or branch
  - 0.4-0.6: Supported in 2 branches with medium confidence
  - 0.7-0.9: Supported in 3+ branches, mostly high confidence
  - 1.0: Supported across all 4 branch types with high confidence

Evidence: [summarize branch findings and their confidence scores]

### Dimension 6: Methodology Fit (0-1)
- Do the methodologies used in source papers match the hypothesis?
- Are there red flags about methods?
- Scoring:
  - 0.0-0.2: Methods are mismatched or problematic
  - 0.4-0.6: Methods are adequate but not ideal
  - 0.7-0.9: Methods are well-suited, minor limitations
  - 1.0: Methods are state-of-the-art for testing this hypothesis

Evidence: [note methodological strengths/weaknesses in source papers]

### Dimension 7: Impact Potential (0-1)
- If true, would this hypothesis advance the field?
- Would it change current understanding or practice?
- Scoring:
  - 0.0-0.2: Minimal impact; incremental or niche relevance
  - 0.4-0.6: Moderate impact; would refine existing understanding
  - 0.7-0.9: Significant impact; would shift understanding or enable new methods
  - 1.0: Transformative impact; would fundamentally change the field

Evidence: [cite evidence about field importance, application potential]

### Dimension 8: Reproducibility (0-1)
- Can independent researchers reproduce findings that would test this?
- Is the proposed path to testing transparent and well-specified?
- Scoring:
  - 0.0-0.2: Highly dependent on specific data/conditions; hard to reproduce
  - 0.4-0.6: Reproducible with effort; some data/method access issues
  - 0.7-0.9: Reproducible with standard methods and accessible data
  - 1.0: Easily reproducible; clear protocol, public data

Evidence: [note open data availability, method clarity in sources]

## Step 3: Strengths & Weaknesses Summary

### Strengths (top 3-5 points where hypothesis is strongest)
- [Dimension]: [Why this is strong]

### Weaknesses (top 3-5 points where hypothesis is weakest)
- [Dimension]: [Why this is weak]

### Critical Gaps (unresolved questions or missing evidence)
- [Gap 1]: [Why it matters]
- [Gap 2]: ...

## Step 4: Recommendation
Choose ONE of three recommendations:

### A. APPROVE
Conditions:
- Evidence strength ≥ 0.7
- Feasibility ≥ 0.6
- Coherence ≥ 0.7
- At least one composite score ≥ 0.75 (average of all 8 dimensions)

Meaning: Hypothesis is strong enough to move to writer phase.

Text: "Recommend APPROVAL. This hypothesis is well-supported by \
[X] verified claims, has [domain] cross-domain support, and is testable with \
[method]. Primary concern: [if any]. Ready for research paper synthesis."

### B. REVISE
Conditions:
- Composite score 0.5-0.75 (some dimensions weak, but not fatally)
- Coherence ≥ 0.5 (makes logical sense)
- At least one strong dimension (≥0.8)

Meaning: Hypothesis has promise but needs refinement before moving to writer.

Text: "Recommend REVISION. Hypothesis is [partially supported / needs narrowing / \
requires additional branches]. Suggest [specific modification]: [detail]. \
After revision, likely suitable for paper."

Specific revision suggestions:
- Narrow scope to [specific subtopic]?
- Combine with [alternative hypothesis]?
- Add constraint: [domain/method/population]?
- Reframe as: [modified statement]?

### C. REJECT
Conditions:
- Composite score < 0.5
- Evidence strength < 0.6 AND contradicted by verified claims
- Coherence < 0.5 (logically flawed)
- Feasibility < 0.4 (untestable)

Meaning: Hypothesis cannot be defended with available evidence.

Text: "Recommend REJECTION. This hypothesis is [contradicted by X claims / lacks \
supporting evidence / is incoherent / is infeasible]. Fundamental issue: [specific problem]. \
User should return to hypothesis phase with feedback: [revision direction]."

## Step 5: Approval & Routing
Report:
- Recommendation: [APPROVE / REVISE / REJECT]
- Composite Score: [average of all 8 dimensions, 0-1]
- Scores by dimension: [all 8 scores]
- Strengths/weaknesses summary (as above)
- Next steps: [Writer phase / Hypothesis phase revision / User decision]

Call request_approval with full evaluation report, including:
- All 8 dimension scores with evidence
- Clear recommendation with reasoning
- If revising: specific modification suggestions
- If approving: readiness statement for writer phase

Note: REVISE recommendations may loop back to hypothesis phase (max 3 iterations). \
User approves any major changes before continuing.
"""
