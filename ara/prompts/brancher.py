# Location: ara/prompts/brancher.py
# Purpose: Brancher phase prompt — cross-domain search (now runs BEFORE hypothesis)
# Functions: BRANCHER_PROMPT
# Calls: N/A
# Imports: N/A

BRANCHER_PROMPT = """## Brancher Phase — SCAMPER Cross-Domain Ideation

Your task is to prevent tunnel vision by applying the SCAMPER ideation framework to the top 5 claims from deep reading. This phase runs BEFORE hypothesis generation so that cross-domain insights can inform novel hypothesis creation.

### SCAMPER Framework (apply to top 5 claims)

For each of the 5 strongest claims from deep reading, systematically apply these 7 lenses:

1. **Substitute**: What if we swap the metric, population, or method?
   - "What if this finding is tested with a different outcome measure?"
   - "What if we study a different population with the same exposure?"

2. **Combine**: What if we merge findings from different domains?
   - "What happens when we combine this finding with evidence from neuroscience/education/economics?"

3. **Adapt**: What method from an adjacent field could be applied here?
   - "Has field X solved a similar measurement problem? Can we borrow their approach?"

4. **Modify**: What if we change a key parameter or threshold?
   - "What if the dose-response relationship is non-linear?"
   - "What if the effect reverses above a certain threshold?"

5. **Put to other use**: Can this finding serve a different purpose?
   - "Could this evidence inform policy in a completely different domain?"

6. **Eliminate**: What if we remove an assumed link in the causal chain?
   - "Does the effect persist if we remove the assumed mediator?"

7. **Reverse**: What if the direction of causation is flipped?
   - "What if the outcome is actually causing the exposure?"

### Process

1. Identify the top 5 claims using search_similar or read_paper.
2. For EACH claim, apply at least 3 SCAMPER lenses (choose the most productive ones).
3. For the most promising SCAMPER-generated angles, run targeted searches (search_all) in adjacent domains.
4. Document which SCAMPER lens produced each insight.

### Output

Present a SCAMPER branch map:
```
CLAIM 1: "[claim text]" (Author, Year)
  - SUBSTITUTE: [insight] → searched [domain] → found [connection]
  - REVERSE: [insight] → searched [domain] → found [connection]
  - COMBINE: [insight] → searched [domain] → found [connection]

CLAIM 2: "[claim text]" (Author, Year)
  ...
```

For each SCAMPER insight, note:
- The specific novel angle it suggests
- Whether cross-domain evidence supports or contradicts it
- Its potential as a hypothesis candidate

### STRICT RULES
- Maximum 6 search calls total
- Focus on finding NOVEL connections, not just more papers on the same topic
- Label every insight with its SCAMPER lens
- When done, output your SCAMPER branch map as text and stop.
"""
