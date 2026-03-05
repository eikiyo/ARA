# Location: ara/prompts/brancher.py
# Purpose: Brancher phase prompt — cross-domain search
# Functions: BRANCHER_PROMPT
# Calls: N/A
# Imports: N/A

BRANCHER_PROMPT = """## Brancher Phase — Cross-Domain Search

Your task is to prevent tunnel vision by searching adjacent fields for connections to the selected hypothesis.

### Branch Types (run all 4)

1. **Lateral**: Search adjacent fields for similar problems.
   Example: If studying transformers in genomics → look at NLP transformer techniques that might transfer.

2. **Methodological**: Find alternative methods used for similar problems.
   Example: Attention mechanism alternatives, ensemble approaches, different architectures.

3. **Analogical**: Find analogous problems in different domains.
   Example: Protein folding ↔ language parsing structural similarities.

4. **Convergent**: Find independent research reaching similar conclusions.
   Example: Multiple groups finding transformer superiority in different biological tasks.

### Process

For each branch type:
1. Use branch_search to identify the cross-domain angle.
2. Use search tools to find relevant papers in the branched domain.
3. Note connections, confirmations, contradictions, or new angles.

### Output

Present a branch map:
- Branch type → domain explored → key findings → relevance to hypothesis
- Highlight any surprising connections or contradictions.
- Note if any branch suggests modifying the hypothesis.

Call request_approval with the branch map.
"""
