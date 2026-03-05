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

### Decision

Based on your evaluation:
- **Approve**: Overall strong hypothesis with minor weaknesses at most.
- **Reject**: Significant weaknesses that need addressing. Provide specific feedback for revision.

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

### Evaluation Dimensions (score each 0.0-1.0)

1. **Citation Density**: Are claims properly cited? Target: 1 citation per 2-3 sentences in lit review, every factual claim cited throughout.
2. **Methodological Rigor**: Is the methods section thorough? Does it include search strategy, PRISMA data, quality assessment framework?
3. **Argumentation Depth**: Does the paper go beyond surface-level description? Is there genuine analysis and synthesis?
4. **Structural Completeness**: Are ALL required sections present with minimum word counts met?
5. **Quantitative Content**: Are effect sizes, sample sizes, CIs, p-values included where available?
6. **Writing Quality**: Is the writing clear, precise, and academic? No vague language, no unsupported generalizations?
7. **Logical Flow**: Do sections connect smoothly? Does each section build on the previous?
8. **Argument Novelty**: Does the paper contribute something beyond restating existing reviews?
9. **Methodological Transparency**: Can a reader replicate the review process from the methods section alone?
10. **Table Quality**: Are tables well-structured, complete, and informative?

### Minimum Thresholds (MUST PASS ALL)

- [ ] 60+ unique citations from verified database papers
- [ ] 6000+ total words across all sections
- [ ] 2+ data tables present (study characteristics + evidence synthesis minimum)
- [ ] 0 hallucinated citations (every Author/Year must map to a DB paper)
- [ ] All 8 sections present (abstract through references)
- [ ] Abstract is structured (Background/Objective/Methods/Results/Conclusion)
- [ ] Methods section includes PRISMA flow numbers
- [ ] Discussion includes limitations subsection
- [ ] Discussion compares with 3+ existing reviews
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
    "table_quality": 0.0-1.0
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
