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

---

## SHOWDOWN MODE — Comparative Hypothesis Evaluation

**When activated**: Called from manager after branching completes with 3 candidate hypotheses

### Overview
Instead of scoring one hypothesis in isolation, compare multiple hypotheses against \
each other on the same 8 dimensions. This reveals which is strongest, which are viable \
alternatives, and where they differ in their support.

### Input
- **Primary hypothesis**: Top-ranked from branching
- **Alternative 1**: 2nd-ranked from branching
- **Alternative 2**: 3rd-ranked from branching
- **Shared evidence**: All 3 hypotheses are grounded in same verified claims + branch findings

### Step 1: Head-to-Head Comparison

For each of the 8 dimensions, compare all 3 hypotheses AGAINST EACH OTHER:

#### Dimension 1: Novelty
- Which hypothesis is most original? (compare relative novelty)
- Which are incremental vs. transformative?
- Score each 0-1 for relative novelty (one may be 0.9, others 0.6, 0.5)

#### Dimension 2: Evidence Strength
- Which is MOST supported by verified claims?
- Which has the LEAST contradictions?
- Which has the BROADEST supporting evidence base?
- Compare directly: "Hypothesis A has support from X papers, Hypothesis B from Y, \
  Hypothesis C from Z"

#### Dimension 3: Feasibility
- Which is easiest to test? (smallest N, shortest duration, fewest resources)
- Which is hardest?
- Which has data bottlenecks others don't?

#### Dimension 4: Coherence
- Which explains the most findings with fewest assumptions?
- Which has logical gaps or questionable mechanisms?
- Which is most parsimonious?

#### Dimension 5: Cross-Domain Support
- Which had the strongest branch findings?
- Which was supported by more branch types (analogical, methodological, etc.)?
- Which had the most consistent cross-domain support?

#### Dimension 6: Methodology Fit
- Which aligns best with state-of-the-art methods?
- Which would benefit from new/emerging methods?
- Which has methodology constraints others lack?

#### Dimension 7: Impact Potential
- If true, which would have LARGEST impact on the field?
- Which would be most practically useful?
- Which is most likely to shift current thinking?

#### Dimension 8: Reproducibility
- Which is easiest for independent researchers to test?
- Which requires proprietary data or methods?
- Which has the clearest protocol?

### Step 2: Scoring Format

Create a comparison table:

```
Dimension          | Primary       | Alternative 1 | Alternative 2 | Winner
-------------------|---------------|---------------|---------------|----------
Novelty            | 0.8           | 0.6           | 0.4           | Primary
Evidence Strength  | 0.85          | 0.75          | 0.7           | Primary
Feasibility        | 0.7           | 0.9           | 0.6           | Alt 1
Coherence          | 0.85          | 0.8           | 0.6           | Primary
Cross-Domain      | 0.9           | 0.7           | 0.5           | Primary
Methodology Fit    | 0.75          | 0.75          | 0.8           | Alt 2
Impact Potential   | 0.9           | 0.7           | 0.6           | Primary
Reproducibility    | 0.7           | 0.85          | 0.75          | Alt 1
---
COMPOSITE SCORE    | 0.81          | 0.74          | 0.62          | Primary
```

### Step 3: Relative Strengths & Weaknesses

For each hypothesis, note:
- **Where it dominates**: Which dimensions does it win? (e.g., "Primary excels in evidence \
  strength and cross-domain support")
- **Where it's weakest**: Which dimensions lag? (e.g., "Alternative 1 weakest in impact potential")
- **Where it ties**: Any dimensions where scores are similar?

### Step 4: Scenario Analysis

For each hypothesis, describe a plausible future:

```
PRIMARY HYPOTHESIS — Most Likely Path
If supported: Field shifts to understanding [mechanism]. Practice changes by [concrete change]. \
Next 5 years: [expected research direction]
Risks: [what would disprove it], [what data is missing]
Best use case: [when/where is this hypothesis most applicable]

ALTERNATIVE 1 — "The Methodological Challenger"
If supported: [different mechanism, different implications]. Practice changes by [...]. \
Next 5 years: [different research direction]
Risks: [...]
Best use case: [...]

ALTERNATIVE 2 — "The Pragmatist"
If supported: [... etc]
```

### Step 5: Ranking & Recommendation

After comparison, produce final ranking:

```
=== SHOWDOWN RESULTS ===

🥇 RANKED 1: [Primary/Alternative] — Composite Score 0.81
   Reason: [Strongest in evidence strength and cross-domain support. Highest impact potential.]
   Recommendation: APPROVE for writer phase
   Caveat: [any limitations or assumptions]

🥈 RANKED 2: [Alternative 1] — Composite Score 0.74
   Reason: [Strong evidence and easiest to test. Solid alternative if primary refuted.]
   Recommendation: VIABLE ALTERNATIVE; include as supporting hypothesis in paper
   Use case: [when this might be preferred]

🥉 RANKED 3: [Alternative 2] — Composite Score 0.62
   Reason: [Weakest evidence base and lowest impact. Limited cross-domain support.]
   Recommendation: MENTION but do not pursue; candidate for future work
   Gaps: [what would need to happen for this to become primary]
```

### Step 6: Final Approval & Routing

Call request_approval with:
- Full showdown comparison table
- Relative strengths/weaknesses analysis
- Scenario analysis for each
- Final ranking and recommendation
- Routing: Primary hypothesis → Writer phase. Alternatives → included in writer as \
  context/limitations section

User will:
- Approve ranking and proceed to writer
- Dispute ranking and request re-analysis
- Request different hypothesis be primary
"""
