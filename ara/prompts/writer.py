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
- **Screening Process**: PRISMA-style description — records identified, duplicates removed, screened by title/abstract, full-text assessed, included in final review. Use EXACT PRISMA numbers from the pipeline — do NOT invent numbers.
- **Screening Methodology**: Describe screening as AI-automated relevance scoring with a pre-specified threshold of 0.6. Do NOT claim human dual-reviewer screening. State this honestly: "Title and abstract screening was conducted using automated relevance scoring, with papers scoring ≥0.6 selected for full-text review. A random subset of 20 papers was manually verified against automated scores to confirm screening reliability."
- **Quality Assessment Framework**: Name the framework used (e.g., Newcastle-Ottawa Scale, GRADE, JBI Critical Appraisal) and describe how quality was assessed
- **Data Extraction Protocol**: What data was extracted from each paper and how it was categorized
- **Analysis Approach**: How findings were synthesized (thematic analysis, narrative synthesis, vote counting, etc.)
- **Limitations of Methodology**: Explicit acknowledgment of methodological constraints. MUST include: "Screening was conducted using automated AI-assisted relevance scoring rather than independent human dual-reviewer screening, which is a limitation of this review."
- **Date Range**: State the exact date range used. This MUST match what the protocol specifies. Do NOT say "no start-date limitation" if the protocol specifies a start date.

#### 6. Results/Analysis (minimum 1200 words)
Must include:
- **Study Selection Results**: PRISMA flow numbers (exact counts for each stage)
- **Study Characteristics Table**: Markdown table with: Author/Year, Country, Design, Sample Size, Population, Key Variables, Main Findings
- **Risk of Bias Assessment**: Use data from `get_risk_of_bias_table()` — present the stored per-study JBI assessments as a formatted table
- **Thematic Results**: Organized by research question or theme, NOT by paper
- **GRADE Evidence Certainty Table**: Use data from `get_grade_table()` — present the stored GRADE ratings with justification for each domain downgrade
- **Quantitative Summary**: For each outcome, report: number of studies, total sample size, effect size range, median effect, confidence intervals. Use the quantitative summary table from synthesis. At least 70% of Results paragraphs MUST contain a specific number.
- **Evidence Concentration**: Match confidence language to evidence base size:
  - 5+ concordant studies: "The evidence consistently demonstrates..."
  - 3-4 studies: "Several studies suggest..."
  - 1-2 studies: "Preliminary evidence from [Author] (Year) indicates..." — NEVER present single-study findings as established
- **Geographic Heterogeneity**: Dedicated subsection comparing findings across regions. Use the geographic comparison table from synthesis. Note regional differences in effect magnitude and potential explanations.
- Report heterogeneity across studies for each outcome (consistent/mixed/contradictory)

#### 7. Discussion (minimum 1000 words)
Must include ALL of these subsections:
- **Summary of Key Findings**: Brief recap tied to research questions, with GRADE-rated certainty
- **Causal Inference Analysis**: Dedicated subsection addressing:
  - Direction of causation (forward vs reverse causation evidence)
  - Key confounders (SES, parenting quality, pre-existing conditions)
  - Evidence from natural experiments or quasi-experimental designs
  - Effect modification by subgroup (age, SES, content type)
- **Comparison with Existing Reviews**: Compare findings with at least 3 other published reviews — what this review adds vs. prior work. The novel contribution MUST be grounded in specific evidence from this review that prior reviews lacked. Do NOT claim novelty without citing the specific prior reviews and explaining what they missed.
- **Theoretical Integration**: Not just name-dropping theories — use theoretical frameworks to generate testable predictions evaluated against the evidence
- **Limitations**: Dedicated subsection addressing: search limitations, language bias, publication bias, lack of dual-reviewer screening, heterogeneity, generalizability constraints, cross-sectional dominance. USE the hypothesis's Q1 ("what would make this wrong?") and Q4 ("weakest point") answers to write honest, specific limitations — not generic disclaimers.
- **Policy and Practice Implications**: Specific, actionable, evidence-graded recommendations. USE the hypothesis's Q5 ("so what?") answer — who changes behavior, what decisions change?
- **Future Research Directions**: At least 3 specific, testable hypotheses with suggested methodologies. Informed by Q1 (falsification conditions suggest what to test next).

#### 7b. Confidence Language Rules (MANDATORY throughout paper)

Match language strength to GRADE certainty level:
- **High certainty**: "The evidence demonstrates...", "Research consistently shows..."
- **Moderate certainty**: "The evidence suggests...", "Findings indicate..."
- **Low certainty**: "Preliminary evidence suggests...", "Limited research indicates..."
- **Very Low certainty**: "Very limited evidence from [n] studies tentatively suggests..."

Match language strength to study count:
- **5+ concordant studies**: "The evidence consistently demonstrates..."
- **3-4 studies**: "Several studies suggest..."
- **1-2 studies**: "One study by [Author] (Year) found..." — NEVER present as established fact
- **Single study**: Use hedging: "preliminary", "initial", "one study reported"

**NEVER overstate conclusions**: If you only have 10-15 observational studies, you CANNOT claim "robust evidence". Use "emerging evidence from observational studies suggests..."

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

**Step 0 — Load Available Data (MANDATORY FIRST STEP):**
1. Call `read_paper` with the synthesis_data section: check if a file named `synthesis_data.md` exists in the sections directory. If it does, this contains ALL pre-built tables from the synthesis phase (study characteristics, GRADE, RoB, PRISMA, citation map, causal model). Use this as your primary structural guide.
2. Call `list_claims()` to get ALL extracted claims with their paper metadata, effect sizes, and study designs. This is your PRIMARY evidence source — every factual statement you write must trace to a claim.
3. Call `list_papers(compact=true)` to get paper metadata (authors, years, titles) for citation formatting.
4. Call `get_risk_of_bias_table()` to retrieve per-study risk of bias assessments.
5. Call `get_grade_table()` to retrieve GRADE evidence certainty ratings per outcome.
6. Study the returned author names and years carefully — these are the ONLY valid citations.
7. Build a mental citation map: which claims support which themes, which papers they come from.

**Pass 1 — Detailed Outline:**
1. Generate comprehensive outline with section headings, subsection headings, and 2-3 sentence summaries per subsection.
2. Plan citation placement — map which claims (from list_claims results) go in which sections. Every claim references a paper — use these as your citations.
3. For each major section theme, call `search_similar(text="<theme>")` to find the most relevant papers via embedding similarity. This ensures thematically appropriate citations.
4. For the 3-5 most central papers, call `read_paper(paper_id=ID, include_fulltext=true)` to read their actual text. Use direct quotes and specific findings from the full text.
5. Plan tables — specify what tables will appear in which sections.

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
