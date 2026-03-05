# Location: ara/prompts/writer.py
# Purpose: Writer phase prompt — paper drafting
# Functions: WRITER_PROMPT
# Calls: N/A
# Imports: N/A

WRITER_PROMPT = """## Writer Phase — Paper Drafting

Your task is to draft a research paper based on the approved hypothesis and all gathered evidence.

### Paper Structure (IMRaD)

1. **Title**: Clear, specific, reflects the hypothesis
2. **Abstract**: 250-300 words summarizing the entire paper
3. **Introduction**: Background, research gap, hypothesis, contribution
4. **Literature Review**: Organized thematically from verified claims
5. **Methods**: Proposed methodology to test the hypothesis
6. **Results/Analysis**: Analysis of gathered evidence (this is a desk research paper)
7. **Discussion**: Implications, limitations, cross-domain connections from brancher
8. **Conclusion**: Summary, contributions, future work
9. **References**: Properly formatted citations

### Process

**Pass 1 — Outline:**
1. Generate paper outline with section headings and brief summaries
2. Plan citation placement (which papers go where)
3. Call request_approval with outline for user review

**Pass 2 — Full Draft:**
1. Write each section using write_section tool
2. Include proper in-text citations
3. Reference specific claims with their paper sources
4. Include branch findings in Discussion section
5. Use get_citations to generate the reference list
6. Call request_approval with the complete paper

### Citation Rules
- Every factual statement must cite its source
- Use the session's citation style (default: APA 7th)
- Include page numbers for direct quotes
- Never fabricate citations — only cite papers in the database
"""
