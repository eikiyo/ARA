# Location: ara/prompts/critic.py
# Purpose: Critic phase prompt — hypothesis evaluation
# Functions: CRITIC_PROMPT
# Calls: N/A
# Imports: N/A

CRITIC_PROMPT = """## Critic Phase — Hypothesis Evaluation

Your task is to critically evaluate the selected hypothesis considering all evidence gathered.

### Evaluation Criteria

Score the hypothesis on each dimension (0.0-1.0):

1. **Novelty**: Is this genuinely new or a restatement of existing work?
2. **Feasibility**: Can this realistically be tested? Are methods available?
3. **Evidence strength**: How strong is the supporting evidence from verified claims?
4. **Methodology fit**: Does a clear, rigorous methodology exist to test this?
5. **Impact**: If confirmed, how significant would this be for the field?
6. **Reproducibility**: Could independent researchers replicate a test?
7. **Cross-domain support**: Do branch findings strengthen or weaken the hypothesis?
8. **Logical coherence**: Is the reasoning chain sound? Any logical gaps?

### Novelty Framework Verification (MANDATORY)

Each hypothesis MUST be labeled with one of these novelty frameworks:
- **INVERSION**: Flips a dominant assumption
- **MISSING LINK**: Identifies an unstudied link in a causal chain
- **MODERATOR**: Finds a hidden boundary condition
- **CROSS-DOMAIN TRANSFER**: Imports a framework from another field
- **MEASUREMENT CHALLENGE**: Questions the standard proxy metric
- **SYNTHESIS TAXONOMY**: Splits conflated phenomena into distinct types

**REJECT if:**
- The hypothesis has no framework label
- The hypothesis claims INVERSION but merely confirms consensus with a caveat
- The hypothesis claims MISSING LINK but the link has already been studied
- The hypothesis claims CROSS-DOMAIN TRANSFER but the transfer has already been done
- The hypothesis is a trivial restatement dressed up with a framework label

**Apply the meta-test**: "Would a domain expert believe something different after reading this hypothesis?" If the answer is no, REJECT.

### Five Questions Audit (MANDATORY)

The hypothesis generator should have answered these. Verify the quality of each answer:

1. **"What would have to be true for this to be wrong?"** — Is the answer specific and testable? Vague answers like "more research needed" = REJECT.
2. **"Who already knows this, and what do they believe?"** — Did they name specific papers/experts? Generic positioning = weak.
3. **"What's the mechanism?"** — Is there a concrete causal pathway in ≤2 sentences? "Correlation suggests..." = insufficient.
4. **"What's the weakest point?"** — Did they identify a real weakness or dodge with "there isn't one"? Dodging = strongest signal the hypothesis needs work.
5. **"So what?"** — Is there a concrete consequence? "Contributes to the literature" is not an answer.

If answers to Q1 and Q4 are weak, the hypothesis hasn't been stress-tested. REJECT with specific guidance.

### Decision

Based on your evaluation:
- **Approve**: Genuinely novel hypothesis with clear framework justification, all Five Questions answered substantively, and minor weaknesses at most.
- **Reject**: Lacks genuine novelty, has significant weaknesses, fails the meta-test, or has weak Five Questions answers. Provide specific feedback for revision.

### Output Format

Return your evaluation as structured JSON in this exact format:
```json
{
  "decision": "APPROVE" or "REJECT",
  "scores": {
    "novelty": 0.0-1.0,
    "feasibility": 0.0-1.0,
    "evidence_strength": 0.0-1.0,
    "methodology_fit": 0.0-1.0,
    "impact": 0.0-1.0,
    "reproducibility": 0.0-1.0,
    "cross_domain_support": 0.0-1.0,
    "logical_coherence": 0.0-1.0
  },
  "five_questions_audit": {
    "q1_falsifiability": "pass/fail — brief assessment",
    "q2_positioning": "pass/fail — brief assessment",
    "q3_mechanism": "pass/fail — brief assessment",
    "q4_weakest_point": "pass/fail — brief assessment",
    "q5_so_what": "pass/fail — brief assessment"
  },
  "strengths": ["strength 1", "strength 2"],
  "weaknesses": ["weakness 1", "weakness 2"],
  "issues": ["specific issue requiring revision"],
  "suggestions": ["concrete suggestion for improvement"]
}
```

If REJECT: issues and suggestions MUST be specific enough for the hypothesis generator to revise.
Maximum 3 rejection cycles — after 3 rejections, approve the best available hypothesis.
"""

PAPER_CRITIC_PROMPT = """## Paper Critic Phase — Draft Quality Evaluation

Your task is to critically evaluate the complete paper draft against tier-A journal standards. This is NOT about the hypothesis — it's about the PAPER quality.

### IMPORTANT: Be VERBOSE and PRESCRIPTIVE
Your feedback goes directly to the writer. Vague feedback wastes a revision cycle.
- Name the EXACT section and paragraph that needs work
- Give CONCRETE example text showing what the fix should look like
- Specify WHICH papers (Author, Year) should be cited WHERE
- In `sections_needing_revision`, include an `exact_fixes` array with ready-to-use replacement text

### Evaluation Dimensions (score each 0.0-1.0)

1. **Citation Density**: Are claims properly cited? Target: 1 citation per 2-3 sentences in lit review, every factual claim cited throughout.
2. **Methodological Rigor**: Is the methods section thorough? Does it include search strategy, PRISMA data, quality assessment framework?
3. **Argumentation Depth**: Does the paper go beyond surface-level description? Is there genuine analysis and synthesis?
4. **Structural Completeness**: Are ALL required sections present with minimum word counts met?
5. **Quantitative Content**: Are effect sizes, sample sizes, CIs, p-values included where available? At least 70% of Results paragraphs should contain a number.
6. **Writing Quality**: Is the writing clear, precise, and academic? No vague language, no unsupported generalizations?
7. **Logical Flow**: Do sections connect smoothly? Does each section build on the previous?
8. **Argument Novelty**: Does the paper contribute something beyond restating existing reviews?
9. **Methodological Transparency**: Can a reader replicate the review process from the methods section alone?
10. **Table Quality**: Are tables well-structured, complete, and informative?
11. **Evidence Balance**: Is evidence spread across multiple studies, or does one study dominate? No single study should be cited >8 times.
12. **Geographic Analysis**: Are cross-regional differences analyzed, not just listed? Effect sizes compared across regions?
13. **Confidence Calibration**: Does hedging language match evidence strength? Single-study findings use "preliminary", multi-study use "demonstrates"?

### Minimum Thresholds (MUST PASS ALL)

- [ ] 60+ unique citations from verified database papers
- [ ] 6000+ total words across all sections
- [ ] 2+ data tables present (study characteristics + evidence synthesis minimum)
- [ ] 0 hallucinated citations (every Author/Year must map to a DB paper)
- [ ] All 8 sections present (abstract through references)
- [ ] Abstract is structured (Background/Objective/Methods/Results/Conclusion)
- [ ] Methods section includes PRISMA flow numbers
- [ ] Discussion includes limitations subsection with SPECIFIC limitations (not generic disclaimers)
- [ ] Limitations reference the hypothesis's falsification conditions and weakest points
- [ ] Discussion compares with 3+ existing reviews
- [ ] Discussion includes causal inference analysis with mechanism and confounders
- [ ] No single study cited more than 8 times across the paper (evidence concentration check)
- [ ] Results section: 70%+ of paragraphs contain at least one quantitative value (effect size, CI, OR, N, or %)
- [ ] Results includes geographic heterogeneity comparison (not just listing countries)
- [ ] Single-study findings use hedging language ("one study found...", NOT "evidence shows...")
- [ ] Conclusion includes 3+ future research questions

### Output Format

Return evaluation as structured JSON:
```json
{
  "decision": "APPROVE" or "REVISE",
  "overall_score": 0.0-1.0,
  "scores": {
    "citation_density": 0.0-1.0,
    "methodological_rigor": 0.0-1.0,
    "argumentation_depth": 0.0-1.0,
    "structural_completeness": 0.0-1.0,
    "quantitative_content": 0.0-1.0,
    "writing_quality": 0.0-1.0,
    "logical_flow": 0.0-1.0,
    "argument_novelty": 0.0-1.0,
    "methodological_transparency": 0.0-1.0,
    "table_quality": 0.0-1.0,
    "evidence_balance": 0.0-1.0,
    "geographic_analysis": 0.0-1.0,
    "confidence_calibration": 0.0-1.0
  },
  "threshold_checks": {
    "citations_40_plus": true/false,
    "words_6000_plus": true/false,
    "tables_2_plus": true/false,
    "no_hallucinated_citations": true/false,
    "all_sections_present": true/false,
    "structured_abstract": true/false,
    "prisma_in_methods": true/false,
    "limitations_subsection": true/false,
    "no_single_study_dominance": true/false,
    "quantitative_density_70pct": true/false,
    "geographic_comparison_present": true/false,
    "confidence_language_calibrated": true/false,
    "three_review_comparisons": true/false,
    "three_future_questions": true/false
  },
  "sections_needing_revision": [
    {
      "section": "section_name",
      "issues": ["specific issue 1", "specific issue 2"],
      "suggestions": ["concrete fix 1", "concrete fix 2"],
      "current_word_count": 500,
      "minimum_word_count": 1000
    }
  ],
  "strengths": ["strength 1", "strength 2"],
  "critical_issues": ["must-fix issue 1", "must-fix issue 2"]
}
```

If REVISE: sections_needing_revision MUST specify exactly what to fix.
Maximum 3 full-paper revision cycles — after 3, approve the best version.
Maximum 2 per-section revision cycles.
"""
