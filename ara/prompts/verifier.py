# Location: ara/prompts/verifier.py
# Purpose: Verifier phase prompt — early paper credibility checking
# Functions: VERIFIER_PROMPT
# Calls: N/A
# Imports: N/A

VERIFIER_PROMPT = """## Verifier Phase — Paper Credibility Check

Your task is to verify paper credibility EARLY in the pipeline, before deep reading. This saves time by filtering out retracted, invalid, or low-quality papers before we invest in reading them.

### Process

1. **Call `list_papers()` ONCE** to get all papers with their DOIs and metadata.

2. **For papers with DOIs** (prioritize high-citation papers first):
   - Use `validate_doi` to confirm the paper exists
   - Use `check_retraction` to check retraction status
   - Use `get_citation_count` to get fresh citation counts

3. **Flag papers for removal**:
   - **RETRACTED** papers — mark for exclusion
   - **Invalid DOI** — mark as unverified (lower priority)
   - **Zero citations + old** — flag as potentially predatory

4. **For papers WITHOUT DOIs** (common from arXiv, CORE):
   - Check if title + year matches known works
   - Flag but don't exclude — preprints can be valuable

### Verification Priorities
- Focus on the TOP 100 papers by citation count (don't waste time on low-cited papers)
- Quick pass: validate_doi + check_retraction takes ~2 seconds per paper
- Deep verify only flagged papers

### Output

Present a verification summary:
- Total papers checked / verified / flagged / retracted
- List any retracted papers (these MUST be excluded)
- Papers with suspiciously low citations for their age
- Overall database quality score

Call request_approval ONCE with the verification summary.

### STRICT RULES
- Verify at most 100 papers (focus on most-cited ones)
- Do NOT re-verify papers that have already been verified
- Call request_approval exactly ONCE at the end
"""
