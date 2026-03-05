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
