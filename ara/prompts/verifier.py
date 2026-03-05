# Location: ara/prompts/verifier.py
# Purpose: Verifier phase prompt — claim credibility checking
# Functions: VERIFIER_PROMPT
# Calls: N/A
# Imports: N/A

VERIFIER_PROMPT = """## Verifier Phase — Claim Verification

Your task is to verify the credibility of extracted claims.

### Process

For each claim:
1. **Check retraction status** using check_retraction (requires DOI).
2. **Validate DOI** using validate_doi to confirm the paper is real.
3. **Get citation count** using get_citation_count for credibility signal.

### Verification Assessment

Assign each claim a verification status:
- **verified**: DOI valid, not retracted, reasonable citation count
- **contradicted**: Another verified claim directly contradicts this one
- **inconclusive**: Cannot fully verify (missing DOI, low citations, ambiguous)
- **retracted**: Source paper is retracted — flag prominently

### Output

Present a verification summary:
- Total claims verified / contradicted / inconclusive / retracted
- Highlight any retracted papers
- Note papers with unusually low citation counts for their age
- Flag contradictions between claims

Call request_approval with the verification summary.
"""
