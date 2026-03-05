# Location: ara/prompts/brancher.py
# Purpose: Brancher phase prompt — cross-domain search (now runs BEFORE hypothesis)
# Functions: BRANCHER_PROMPT
# Calls: N/A
# Imports: N/A

BRANCHER_PROMPT = """## Brancher Phase — Cross-Domain Search

Your task is to prevent tunnel vision by searching adjacent fields for connections to the research topic and key findings from deep reading. This phase runs BEFORE hypothesis generation so that cross-domain insights can inform hypothesis creation.

### Branch Types (run all 4)

1. **Lateral**: Search adjacent fields for similar problems.
   Example: If studying screen time effects on children → look at attention/focus research from education psychology, neuroscience of habit formation.

2. **Methodological**: Find alternative methods used for similar problems.
   Example: Novel measurement approaches, different study designs, innovative data collection methods used in related fields.

3. **Analogical**: Find analogous problems in different domains.
   Example: Media consumption effects on adults ↔ children, technology adoption patterns in different age groups.

4. **Convergent**: Find independent research reaching similar conclusions.
   Example: Multiple disciplines (neuroscience, psychology, education) converging on similar findings about the research topic.

### Process

For each branch type:
1. Use search tools (search_all or search_similar) to find relevant papers from adjacent domains.
2. Note connections, confirmations, contradictions, or new angles.
3. Identify insights that could inspire novel hypotheses.

### Output

Present a branch map:
- Branch type → domain explored → key findings → potential hypothesis angles
- Highlight surprising connections or contradictions
- Note any gaps that cross-domain evidence could fill
- Suggest at least 2-3 novel angles for hypothesis generation

Call request_approval with the branch map.

### STRICT RULES
- Maximum 4 search calls (one per branch type)
- Call request_approval exactly ONCE at the end
- Focus on finding NOVEL connections, not just more papers on the same topic
"""
