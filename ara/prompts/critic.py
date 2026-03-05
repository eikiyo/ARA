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

### Output

Present:
1. Dimensional scores table
2. Strengths (bullet points)
3. Weaknesses (bullet points)
4. Decision: APPROVE or REJECT
5. If rejected: specific feedback for hypothesis revision

Call request_approval with your evaluation.
"""
