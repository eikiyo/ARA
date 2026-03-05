# Location: ara/prompts/writer.py
# Purpose: Writer phase prompt — paper drafting
# Functions: WRITER_PROMPT
# Calls: N/A
# Imports: N/A

WRITER_PROMPT = """## Writer Phase — AAA-Grade Academic Paper Drafting

Your task is to draft a research paper that meets tier-A journal standards. Every section must be substantive, well-cited, and analytically rigorous.

### Paper Structure (IMRaD Extended)

**CRITICAL RULES:**
- Every factual statement MUST cite a paper from the database using (Author, Year) format
- NEVER fabricate citations — only cite papers you can verify in the database
- Cross-reference findings: "Smith (2020) confirms Johnson (2018)'s finding that..."
- Organize literature thematically, NOT paper-by-paper
- Include quantitative data (effect sizes, sample sizes, CIs) wherever available

### Section Requirements

#### 1. Title
Clear, specific, reflects the hypothesis. Include key variables and population.

#### 2. Abstract (250-300 words, STRUCTURED)
Format with explicit labels:
- **Background**: 2-3 sentences on the problem and its significance
- **Objective**: 1 sentence stating the research aim/hypothesis
- **Methods**: 2-3 sentences on methodology (databases searched, inclusion criteria, analysis approach)
- **Results**: 3-4 sentences on key findings with specific numbers
- **Conclusion**: 2-3 sentences on implications and future directions

#### 3. Introduction (minimum 800 words)
Must include:
- Opening hook establishing the significance of the problem
- Background context with 8+ citations establishing the field
- Clear identification of the research gap (what is NOT known)
- Explicit research questions (numbered: RQ1, RQ2, etc.)
- Statement of contribution (what this paper adds to the field)
- Brief outline of paper structure

#### 4. Literature Review (minimum 1500 words)
Must include:
- Thematic organization (NOT paper-by-paper summaries)
- At least 3 major themes/subsections
- Cross-referencing between papers: "While X found..., Y's later study contradicted..."
- A comparison/summary table of key studies (markdown table with columns: Author/Year, Study Design, Sample, Key Finding, Limitation)
- Identification of consensus areas and contested areas
- Clear transition from literature to research gap
- 20+ unique citations minimum

#### 5. Methods (minimum 1000 words) — HEAVY AND IMPENETRABLE
Must include:
- **Search Strategy**: Exact databases searched (name all 9 APIs), date ranges, Boolean search strings used, number of initial hits per database
- **Inclusion/Exclusion Criteria Table**: Two-column markdown table listing all criteria
- **Screening Process**: PRISMA-style description — records identified, duplicates removed, screened by title/abstract, full-text assessed, included in final review
- **Quality Assessment Framework**: Name the framework used (e.g., Newcastle-Ottawa Scale, GRADE, JBI Critical Appraisal) and describe how quality was assessed
- **Data Extraction Protocol**: What data was extracted from each paper and how it was categorized
- **Analysis Approach**: How findings were synthesized (thematic analysis, narrative synthesis, vote counting, etc.)
- **Limitations of Methodology**: Explicit acknowledgment of methodological constraints

#### 6. Results/Analysis (minimum 1200 words)
Must include:
- **Study Selection Results**: PRISMA flow numbers
- **Study Characteristics Table**: Markdown table with: Author/Year, Country, Design, Sample Size, Population, Key Variables, Main Findings
- **Thematic Results**: Organized by research question or theme, NOT by paper
- **Evidence Synthesis Table**: Markdown table summarizing strength of evidence per theme
- Quantitative data where available (effect sizes, confidence intervals, p-values)
- Explicit noting of evidence quality (strong/moderate/weak evidence)

#### 7. Discussion (minimum 1000 words)
Must include ALL of these subsections:
- **Summary of Key Findings**: Brief recap tied to research questions
- **Comparison with Existing Reviews**: Compare findings with at least 3 other published reviews in the same area
- **Theoretical Implications**: How findings relate to existing theoretical frameworks
- **Limitations**: Dedicated subsection addressing: search limitations, language bias, publication bias, heterogeneity of included studies, generalizability constraints
- **Policy and Practice Implications**: Specific, actionable recommendations for policymakers and practitioners
- **Future Research Directions**: At least 3 specific, testable research questions for future work

#### 8. Conclusion (minimum 400 words)
Must include:
- Summary of main contributions (what this review adds)
- Key takeaways for different audiences (researchers, policymakers, practitioners)
- Limitations acknowledgment (brief)
- 3+ specific future research questions with justification
- Closing statement on broader significance

#### 9. References
- APA 7th edition format
- Minimum 40 unique references
- Only cite papers verified in the database

### Process

**Step 0 — Load Available Papers (MANDATORY FIRST STEP):**
1. Call `list_papers` to get ALL papers in the database with their authors, years, and titles
2. Study the returned author names and years carefully — these are the ONLY valid citations
3. Build a mental citation map: which papers support which themes

**Pass 1 — Detailed Outline:**
1. Generate comprehensive outline with section headings, subsection headings, and 2-3 sentence summaries per subsection
2. Plan citation placement — map which papers (from list_papers results) go in which sections
3. Plan tables — specify what tables will appear in which sections

**Pass 2 — Full Draft (write ALL 7 sections):**
1. Write EACH section using write_section tool in order: abstract, introduction, literature_review, methods, results, discussion, conclusion
2. For EVERY citation, use the EXACT author last name as shown in list_papers results
3. If list_papers shows author "John Smith" for year 2020, cite as (Smith, 2020)
4. If list_papers shows author "J. García-López" for year 2019, cite as (García-López, 2019)
5. Build all required tables in markdown format
6. Cross-reference findings across papers
7. After ALL sections are written, call get_citations to generate the reference list
8. You MUST write all 7 sections. Do NOT stop after 2-3 sections.

### Citation Integrity Rules
- FIRST call list_papers to see all available papers with their exact author names and years
- EVERY claim must cite a paper from the list_papers results using the author's LAST NAME
- Use (Author, Year) format consistently — the author name must match what's in the database
- Example: if list_papers shows author "Maria Chen" with year 2021, cite as (Chen, 2021)
- Example: if list_papers shows author "Jean-Pierre Dupont" with year 2020, cite as (Dupont, 2020)
- If you cannot find a matching paper to cite, DO NOT make the claim
- When multiple papers support a claim, cite all: (Author1, Year; Author2, Year)
- You MUST write ALL 7 sections (abstract through conclusion) before finishing
"""
