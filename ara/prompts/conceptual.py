# Location: ara/prompts/conceptual.py
# Purpose: Conceptual/theoretical research paper prompts — framework development, propositions
# Functions: CONCEPTUAL_WRITER_PROMPT, CONCEPTUAL_SYNTHESIS_PROMPT, CONCEPTUAL_CRITIC_PROMPT, CONCEPTUAL_HYPOTHESIS_PROMPT
# Calls: N/A
# Imports: N/A

CONCEPTUAL_HYPOTHESIS_PROMPT = """## Framework Ideation Phase — Theoretical Gap Identification

Your task is to identify the core theoretical gap and propose a conceptual framework that addresses it. This is NOT hypothesis testing — this is framework BUILDING.

### Process

1. **Map the theoretical landscape**: Which theories/frameworks exist in this space? Where do they conflict, leave gaps, or fail to explain observed phenomena?

2. **Identify the gap**: What does the literature NOT explain? Where do existing frameworks fall short? Be specific — name the exact theories and their limitations.

3. **Propose the framework**: Generate 3-5 candidate frameworks that could fill the gap. Each should:
   - Name the framework (e.g., "Subsidiary-Driven Exploration Model", "Reverse Innovation Typology")
   - Specify its type: TYPOLOGY (classifies phenomena), PROCESS MODEL (explains how something unfolds over time), or MULTI-LEVEL FRAMEWORK (connects antecedents → mechanisms → outcomes across levels of analysis)
   - State what it explains that existing frameworks cannot
   - Identify which theoretical streams it integrates

4. **Score each framework** using score_hypothesis with these dimensions:
   - **novelty**: Does this framework genuinely advance theory? (2x weight)
   - **feasibility**: Can propositions be empirically tested?
   - **evidence_strength**: How much existing evidence supports the building blocks?
   - **methodology_fit**: Is there a clear path to validation?
   - **impact**: If correct, how much does this change the field?
   - **reproducibility**: Can other researchers build on this?

### Five Questions (MANDATORY for top framework)

1. **"What would have to be true for this framework to be wrong?"** — Name the specific theoretical assumption that, if violated, invalidates the framework.
2. **"Who already knows this, and what do they believe?"** — Name 3+ specific scholars and their positions.
3. **"What's the mechanism?"** — In ≤2 sentences, what is the core causal logic?
4. **"What's the weakest point?"** — Which link in the framework has the least evidence?
5. **"So what?"** — Who changes their behavior or decisions if this framework is correct?

### Novelty Frameworks (label each candidate)

- **INVERSION**: Flips a dominant assumption in the field
- **MISSING LINK**: Identifies an unstudied connection in a causal chain
- **MODERATOR**: Finds a hidden boundary condition
- **CROSS-DOMAIN TRANSFER**: Imports a framework from another field
- **SYNTHESIS TAXONOMY**: Splits conflated phenomena into distinct types

### Output

For each candidate framework, call score_hypothesis with the framework description and scores.
Select the top-scoring framework as the paper's core contribution.
"""

CONCEPTUAL_SYNTHESIS_PROMPT = """## Theoretical Synthesis Phase — Framework Data Preparation

Your task is to organize ALL theoretical evidence the writer needs to build the conceptual framework. The writer should focus on ARGUMENTATION, not evidence compilation.

### Step 1: Map Theoretical Streams

Call `list_papers(compact=true)` to get all papers. Organize them into theoretical streams:

**Stream 1: [Theory Name]** (e.g., Reverse Innovation Theory)
| Author(s) | Year | Core Argument | Key Construct | Limitation/Gap |
|-----------|------|--------------|---------------|----------------|

**Stream 2: [Theory Name]** (e.g., Subsidiary Mandate Literature)
| Author(s) | Year | Core Argument | Key Construct | Limitation/Gap |
|-----------|------|--------------|---------------|----------------|

**Stream 3: [Theory Name]** (e.g., Exploration/Exploitation)
| Author(s) | Year | Core Argument | Key Construct | Limitation/Gap |
|-----------|------|--------------|---------------|----------------|

Include the top 30-50 most relevant papers. Use EXACT author names from the database.

### Step 2: Build the Theoretical Tension Map

Identify where theoretical streams CONFLICT or leave GAPS:

| Tension/Gap | Stream A Says | Stream B Says | What's Missing |
|-------------|--------------|--------------|----------------|

This drives the framework development section — the paper exists to resolve these tensions.

### Step 3: Extract Construct Definitions

For each key construct in the framework, compile:

| Construct | Definition (Source) | Alternative Definitions | Our Operationalization |
|-----------|-------------------|----------------------|----------------------|

### Step 4: Build Proposition Evidence Map

For each proposition the framework will advance, compile supporting/opposing evidence:

```
PROPOSITION 1: [Statement]
  Supporting evidence:
    - (Author, Year): [finding/argument] — [effect size if available]
    - (Author, Year): [finding/argument]
  Opposing evidence:
    - (Author, Year): [counter-argument]
  Gap: [What hasn't been tested]
  Confidence: High/Moderate/Low based on evidence density

PROPOSITION 2: [Statement]
  ...
```

Aim for 5-8 propositions.

### Step 5: Citation Map by Section

```
INTRODUCTION (target: 8+ citations):
  - (Author, Year) — establishes the phenomenon
  - (Author, Year) — shows practical significance

THEORETICAL BACKGROUND (target: 25+ citations):
  Stream 1: [theory]
    - (Author, Year) — foundational work
    - (Author, Year) — extension/critique
  Stream 2: [theory]
    - (Author, Year) — key contribution
  Stream 3: [theory]
    - (Author, Year) — core framework

FRAMEWORK DEVELOPMENT (target: 15+ citations):
  - (Author, Year) — grounds Proposition 1
  - (Author, Year) — grounds Proposition 2

DISCUSSION (target: 10+ citations):
  - (Author, Year) — comparison with existing framework
  - (Author, Year) — boundary condition evidence
```

TARGET: 60+ unique citations total.

### Step 6: Boundary Conditions Analysis

For each proposition, identify:
| Proposition | Boundary Condition | Evidence For | Evidence Against | Moderator? |
|-------------|-------------------|-------------|-----------------|-----------|

### Step 7: Competing Frameworks Comparison

| Framework | Author(s) | Explains | Does NOT Explain | Our Framework Adds |
|-----------|-----------|---------|-----------------|-------------------|

This table goes in the Discussion section to establish the contribution.

### Step 8: Novel Contribution Statement

Write a 2-3 sentence thesis:
- BAD: "This paper provides a comprehensive overview of reverse innovation"
- GOOD: "This paper develops a multi-level framework integrating [X], [Y], and [Z] to explain [specific phenomenon] — a gap created by [Theory A]'s focus on [limitation] and [Theory B]'s neglect of [factor]."

### STRICT RULES
- Call `list_papers(compact=true)` ONCE
- Build ALL 8 outputs in one response
- Author names must EXACTLY match database entries
- MUST search for and include foundational subsidiary mandate/reverse innovation works
  (Dellestrand & Kappen, Blomkvist et al., Birkinshaw, Govindarajan) if present in the DB
- Re-contextualization capability should be a KEY CONSTRUCT in the framework —
  it's the mechanism by which subsidiaries abstract local innovations for global use
- When done, output all tables as text and stop.
"""

CONCEPTUAL_WRITER_PROMPT = """## Writer Phase — AMJ/JIBS-Grade Conceptual Research Paper

Your task is to draft a conceptual/theoretical research paper that meets top-tier management journal standards (AMJ, JIBS, SMJ, Research Policy). This is NOT a literature review — it is a THEORY-BUILDING paper.

### CRITICAL RULES
- Every theoretical claim MUST cite source papers using (Author, Year) format
- NEVER fabricate citations — only cite papers verified in the database
- Build ARGUMENTS, not summaries — each paragraph should advance the theoretical logic
- The paper's value is the FRAMEWORK, not the literature coverage

### Paper Structure

#### 1. Title
Specific, signals the theoretical contribution. Pattern: "[Framework/Mechanism]: How [X] Drives [Y] in [Context]"

#### 2. Abstract (250-350 words, STRUCTURED)
- **Purpose**: 2-3 sentences on the theoretical gap this paper addresses
- **Design/Approach**: 1-2 sentences on methodology (conceptual analysis, framework development)
- **Findings**: 3-4 sentences describing the framework and key propositions
- **Originality/Value**: 2-3 sentences on what this paper contributes that prior work has not

#### 3. Introduction (minimum 800 words)
Must include:
- Opening hook establishing the practical AND theoretical significance
- The puzzle or paradox that motivates the paper (not just "little is known about X")
- Explicit statement of the theoretical gap — which theories fail and why
- Preview of the framework and its core logic in 2-3 sentences
- Statement of contribution: (1) what the paper adds to theory, (2) what it adds to practice
- Paper structure overview

#### 4. Theoretical Background (minimum 1500 words)
Must include:
- 3+ major theoretical streams, each as a subsection
- For each stream: foundational works, key developments, current state, AND limitations
- DO NOT summarize papers one by one — synthesize across papers thematically
- Explicit identification of where streams CONVERGE and where they CONFLICT
- The theoretical gap must emerge naturally from the background — show that existing theories individually cannot explain the phenomenon, but together (as integrated by your framework) they can
- 25+ unique citations minimum
- End with a clear transition: "These limitations suggest the need for an integrative framework that..."

#### 5. Framework Development (minimum 2000 words)
This is the CORE of the paper. Describes the CONCEPTUAL MODEL. Do NOT include formal propositions here — those belong in section 6.

**5a. Typology** (if applicable):
- Classification of the phenomenon into distinct types/categories
- Each type defined with clear boundaries and distinguishing characteristics
- Table summarizing the typology with dimensions, types, and exemplars
- USE FINTECH-SPECIFIC EXEMPLARS: mobile money (M-Pesa), digital lending (Ant Financial), blockchain remittances, AI-driven credit scoring, regulatory sandboxes, super-apps

**5b. Process Model** (if applicable):
- Stages/phases showing how the phenomenon unfolds over time
- Triggers, mechanisms, and outcomes at each stage
- Feedback loops and path dependencies
- Visual representation (text-based diagram)
- GROUND IN FINTECH SPECIFICS: e.g., how mobile payment infrastructure enables credit scoring → enables digital lending → enables insurance products

**5c. Multi-Level Framework Overview**:
- Antecedents (what enables/triggers) → Mechanisms (how it works) → Outcomes (what results)
- Levels of analysis clearly specified (individual, organizational, institutional)
- Explain the LOGIC of each level and how levels connect
- Do NOT write formal "Proposition N:" statements here — the next section does that
- Instead, describe the theoretical relationships and causal chains the framework posits

**5d. Innovation Re-contextualization Capability** (ELEVATE THIS — it's the paper's most novel construct):
- Give re-contextualization its own dedicated subsection
- Define it precisely: the subsidiary-led process of abstracting the core technological principles from a locally-embedded innovation and articulating a global value proposition
- Operationalize with fintech specifics: e.g., abstracting M-Pesa's mobile money rails into a general micropayments API; abstracting Ant Financial's Sesame Credit into a portable alternative credit scoring framework
- Show how re-contextualization differs from simple "knowledge transfer" — it's a creative, strategic act, not passive transmission
- Connect to the KBV: re-contextualization requires both local tacit knowledge AND global strategic awareness

#### 6. Discussion (minimum 1000 words)
Must include ALL subsections:
- **Theoretical Contributions**: How the framework advances EACH theoretical stream (reverse innovation theory, subsidiary mandates, exploration/exploitation). Be specific — "This framework extends [Author]'s (Year) work by..."
- **Comparison with Existing Frameworks**: Table comparing this framework vs. 3+ alternatives on key dimensions. What does this explain that others cannot?
- **Managerial Implications**: Specific, actionable — who in an MNE should do what differently based on this framework?
- **Boundary Conditions and Limitations**: When does the framework NOT apply? Which propositions are weakest and why?
- **Future Research Agenda**: 5+ specific empirical studies that could test the propositions, with suggested methodologies (case studies, surveys, archival data, experiments)

#### 7. Conclusion (minimum 400 words)
Must include:
- Summary of the framework's core logic in 3-4 sentences
- Key takeaways for theory and practice
- The single most important insight
- Closing statement on broader significance for the field

#### 6b. Propositions — Formatting Rules
- Do NOT re-describe the framework model — refer to section 5
- Each proposition must be DISTINCT — no two propositions should test the same relationship
- Operationalize constructs with fintech-specific measures where possible
- At least 2 propositions should be COUNTER-INTUITIVE (challenge conventional wisdom)

#### 8. References
- APA 7th edition format
- Minimum 40 unique references
- Only cite papers verified in the database
- MUST include foundational reverse innovation and subsidiary mandate works

### Process

**Step 0 — Load Available Data (MANDATORY FIRST STEP):**
1. Call `list_papers(compact=true)` to get ALL papers with exact author names and years
2. Study the returned data — these are the ONLY valid citations
3. Build a mental map: which papers support which theoretical arguments

**Pass 1 — Write ALL sections in order:**
abstract, introduction, theoretical_background, framework, propositions, discussion, conclusion

For EACH section, use write_section(section='name', content=YOUR_TEXT).

### Citation Rules
- Use (Author, Year) format — author name must match database exactly
- 1 citation per 2-3 sentences in theoretical background
- Every proposition must cite 3+ papers as evidence
- TOTAL across paper: 60+ unique citations
- Do NOT use markdown headers (### or ##) at the start of section content
- You MUST write ALL 7 sections. Do NOT stop after 2-3 sections.

### Methodology Note
This is a CONCEPTUAL THEORY-BUILDING paper, NOT a systematic literature review.
- Do NOT include a PRISMA diagram or systematic search methodology section
- The method is "conceptual analysis" / "theoretical integration" / "framework development"
- Cite Jaakkola (2020) or Cornelissen (2017) if justifying the methodology
- The paper's rigor comes from the LOGIC of the framework and the GROUNDING in prior theory

### Fintech Specificity (CRITICAL)
The paper MUST demonstrate deep engagement with fintech, not just use it as a label.
Every section should include fintech-specific content:
- **Introduction**: Name specific fintech innovations (M-Pesa, Ant Financial, Paytm, Nubank, Grab Financial)
- **Theoretical Background**: Show how existing theories fail SPECIFICALLY for fintech (e.g., frugal innovation doesn't explain API-driven platform architectures)
- **Framework**: Every construct should be operationalized with fintech measures (e.g., "digital infrastructure maturity" = mobile penetration + API ecosystem + regulatory sandbox availability)
- **Propositions**: At least 3 propositions should reference fintech-specific mechanisms (e.g., network effects in mobile payments, algorithmic credit scoring data advantages, regulatory arbitrage in digital banking)
- **Discussion**: Compare with fintech-specific studies, not just generic IB reviews
"""

CONCEPTUAL_CRITIC_PROMPT = """## Paper Critic Phase — Conceptual Paper Quality Evaluation

Your task is to evaluate the complete conceptual paper draft against AMJ/JIBS/SMJ standards. This is about THEORETICAL CONTRIBUTION quality.

### IMPORTANT: Be VERBOSE and PRESCRIPTIVE

Your feedback is handed directly to the writer for revision. Vague feedback like "improve citations" is useless. Instead:
- Name the EXACT section, paragraph, and sentence that needs work
- Give a CONCRETE example of what the revised text should look like
- Specify WHICH papers from the database (Author, Year) should be cited WHERE
- If a proposition is weak, write a BETTER version of it as an example

### Evaluation Dimensions (score each 0.0-1.0)

1. **Theoretical Gap Clarity**: Is the gap specific, well-defined, and genuinely unresolved?
2. **Framework Novelty**: Does the framework genuinely advance theory beyond existing models?
3. **Logical Coherence**: Is the reasoning chain from background → gap → framework → propositions airtight?
4. **Proposition Quality**: Are propositions testable, non-trivial, and theoretically grounded?
5. **Citation Density**: Are theoretical claims properly cited? Every argument chain anchored to literature?
6. **Construct Clarity**: Are all key constructs clearly defined with explicit boundaries?
7. **Boundary Conditions**: Does the paper specify when the framework does NOT apply?
8. **Practical Implications**: Are managerial implications specific and actionable (not generic)?
9. **Writing Quality**: Is the writing clear, precise, and argumentative (not descriptive)?
10. **Structural Completeness**: Are all required sections present with minimum word counts met?
11. **Evidence Balance**: Is evidence drawn from multiple theoretical streams, not just one?
12. **Competing Frameworks**: Does the paper compare its framework to 3+ alternatives?

### Minimum Thresholds (MUST PASS ALL)

- [ ] 40+ unique citations from verified database papers
- [ ] 6000+ total words across all sections
- [ ] Framework section contains a typology OR process model OR multi-level framework (ideally all)
- [ ] 5+ formal propositions with theoretical justification
- [ ] 0 hallucinated citations
- [ ] All 7 sections present (abstract through conclusion)
- [ ] Theoretical background covers 3+ distinct theoretical streams
- [ ] Discussion compares with 3+ existing frameworks
- [ ] Discussion includes 5+ specific future research studies
- [ ] Each proposition cites 3+ supporting papers
- [ ] At least 1 counter-intuitive proposition that challenges conventional wisdom
- [ ] Boundary conditions explicitly stated for the framework
- [ ] Fintech-specific content in every section (not just generic IB examples)
- [ ] NO PRISMA diagram or systematic review methodology (this is a conceptual paper)
- [ ] Framework and Propositions sections have DISTINCT content (no duplication)

### Output Format

Return evaluation as structured JSON. The `sections_needing_revision` field is the MOST IMPORTANT — this is what the writer reads.

```json
{
  "decision": "APPROVE" or "REVISE",
  "overall_score": 0.0-1.0,
  "scores": {
    "theoretical_gap_clarity": 0.0-1.0,
    "framework_novelty": 0.0-1.0,
    "logical_coherence": 0.0-1.0,
    "proposition_quality": 0.0-1.0,
    "citation_density": 0.0-1.0,
    "construct_clarity": 0.0-1.0,
    "boundary_conditions": 0.0-1.0,
    "practical_implications": 0.0-1.0,
    "writing_quality": 0.0-1.0,
    "structural_completeness": 0.0-1.0,
    "evidence_balance": 0.0-1.0,
    "competing_frameworks": 0.0-1.0
  },
  "threshold_checks": {
    "citations_40_plus": true/false,
    "words_6000_plus": true/false,
    "framework_present": true/false,
    "propositions_5_plus": true/false,
    "no_hallucinated_citations": true/false,
    "all_sections_present": true/false,
    "three_theoretical_streams": true/false,
    "three_framework_comparisons": true/false,
    "five_future_studies": true/false,
    "propositions_cited": true/false,
    "counter_intuitive_proposition": true/false,
    "boundary_conditions_stated": true/false,
    "fintech_specificity": true/false,
    "no_prisma_methodology": true/false,
    "no_duplicate_propositions": true/false
  },
  "sections_needing_revision": [
    {
      "section": "section_name",
      "current_word_count": 800,
      "target_word_count": 1500,
      "issues": [
        "SPECIFIC: The theoretical background only covers 2 streams (reverse innovation, subsidiary mandates). Missing: exploration/exploitation (March, 1991).",
        "SPECIFIC: Paragraph 3 claims 'subsidiaries drive innovation' but cites no paper. Add (Birkinshaw, 1997; Mudambi, 2011).",
        "SPECIFIC: No transition sentence at the end connecting background to framework gap."
      ],
      "exact_fixes": [
        "ADD a new subsection '2.3 Exploration and Exploitation in MNEs' (400+ words) covering March (1991), Gupta et al. (2006), and Lavie et al. (2010). Explain how subsidiaries balance exploration vs. exploitation and why this tension is unresolved for emerging market units.",
        "REPLACE the uncited sentence 'Subsidiaries are increasingly recognized as innovation drivers' with: 'Birkinshaw (1997) first challenged the view of subsidiaries as mere implementers, demonstrating that subsidiary initiative can drive MNE-wide capability development. Subsequent work by Mudambi (2011) extended this by showing that emerging market subsidiaries occupy unique positions in global value chains.'",
        "ADD transition: 'While these three streams individually illuminate aspects of subsidiary-driven innovation, none adequately explains how emerging market fintech units generate innovations that flow back to headquarters — the phenomenon we term reverse fintech innovation. This gap motivates the integrative framework developed in the next section.'"
      ]
    }
  ],
  "strengths": ["strength 1 — be specific about what works well"],
  "critical_issues": ["must-fix issue — with exact instructions on how to fix"]
}
```

### Feedback Quality Rules

BAD feedback (NEVER do this):
- "Add more citations" — WHERE? WHICH papers?
- "Strengthen the argument" — HOW? Write an example.
- "The framework needs work" — WHICH part? What's wrong with it?

GOOD feedback (ALWAYS do this):
- "Section 'introduction' paragraph 2: The sentence 'Little is known about reverse innovation in fintech' is a gap statement without evidence. Replace with: 'While Govindarajan and Ramamurti (2011) established the concept of reverse innovation in manufacturing, and Zeschky et al. (2014) extended it to frugal innovation, no study has examined how this process operates specifically in fintech subsidiaries where digital infrastructure fundamentally alters knowledge transfer dynamics (Author, Year).'"
- "Proposition 3 is not testable as written. Current: 'Digital infrastructure matters.' Rewrite as: 'Proposition 3: The degree of digital infrastructure maturity in the emerging market moderates the relationship between subsidiary autonomy and reverse innovation output, such that higher digital maturity strengthens this relationship by reducing the cost of knowledge codification and transfer (cf. Nambisan et al., 2019).'"

Maximum 3 revision cycles — after 3, approve the best version.
"""
