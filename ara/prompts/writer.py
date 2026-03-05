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
- **Study Selection Results**: PRISMA flow numbers (exact counts for each stage)
- **Study Characteristics Table**: Markdown table with: Author/Year, Country, Design, Sample Size, Population, Key Variables, Main Findings
- **Risk of Bias Assessment**: Summary of risk of bias across included studies (use JBI framework data from synthesis)
- **Thematic Results**: Organized by research question or theme, NOT by paper
- **GRADE Evidence Certainty Table**: For each outcome, rate evidence certainty (High/Moderate/Low/Very Low) with justification
- Effect sizes with confidence intervals where available (e.g., "Cohen's d = 0.45, 95% CI: 0.22-0.68")
- Report heterogeneity across studies for each outcome (consistent/mixed/contradictory)

#### 7. Discussion (minimum 1000 words)
Must include ALL of these subsections:
- **Summary of Key Findings**: Brief recap tied to research questions, with GRADE-rated certainty
- **Causal Inference Analysis**: Dedicated subsection addressing:
  - Direction of causation (forward vs reverse causation evidence)
  - Key confounders (SES, parenting quality, pre-existing conditions)
  - Evidence from natural experiments or quasi-experimental designs
  - Effect modification by subgroup (age, SES, content type)
- **Comparison with Existing Reviews**: Compare findings with at least 3 other published reviews — what this review adds vs. prior work
- **Theoretical Integration**: Not just name-dropping theories — use theoretical frameworks to generate testable predictions evaluated against the evidence
- **Limitations**: Dedicated subsection addressing: search limitations, language bias, publication bias, lack of dual-reviewer screening, heterogeneity, generalizability constraints, cross-sectional dominance
- **Policy and Practice Implications**: Specific, actionable, evidence-graded recommendations
- **Future Research Directions**: At least 3 specific, testable hypotheses with suggested methodologies

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
7. Do NOT use markdown headers (### or ##) at the start of section content — the system adds headings automatically
8. Report effect sizes where available (e.g., "Cohen's d = 0.45", "OR = 2.3, 95% CI: 1.2-3.4")
9. After ALL sections are written, call `generate_prisma_diagram` to create the PRISMA flow diagram
10. After PRISMA, call `get_citations` to generate the reference list
11. You MUST write all 7 sections. Do NOT stop after 2-3 sections.

### Citation Density Requirements (MINIMUM per section)
- **Introduction**: 8+ citations (establish the field)
- **Literature Review**: 20+ citations (this is the core — cite heavily)
- **Methods**: 3+ citations (methodology frameworks)
- **Results**: 8+ citations (compare findings)
- **Discussion**: 10+ citations (contextualize findings)
- **Conclusion**: 3+ citations (future directions)
- **TOTAL across paper**: 60+ unique citations

### Citation Integrity Rules
- FIRST call list_papers to see all available papers with their exact author names and years
- EVERY claim must cite a paper from the list_papers results using the author's LAST NAME
- Use (Author, Year) format consistently — the author name must match what's in the database
- Example: if list_papers shows author "Maria Chen" with year 2021, cite as (Chen, 2021)
- Example: if list_papers shows author "Jean-Pierre Dupont" with year 2020, cite as (Dupont, 2020)
- If you cannot find a matching paper to cite, DO NOT make the claim
- When multiple papers support a claim, cite all: (Author1, Year; Author2, Year)
- Aim for 1 citation per 2-3 sentences in literature review, 1 per 3-4 sentences elsewhere
- You MUST write ALL 7 sections (abstract through conclusion) before finishing
"""
