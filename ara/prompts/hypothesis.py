# Location: ara/prompts/hypothesis.py
# Purpose: Hypothesis generator phase prompt
# Functions: HYPOTHESIS_PROMPT
# Calls: N/A
# Imports: N/A

HYPOTHESIS_PROMPT = """## Hypothesis Generation Phase — Six Novelty Frameworks

Your task is to generate genuinely novel research hypotheses using structured novelty frameworks. Each hypothesis must pass the meta-test: "Would an expert believe something different after reading this?"

### Six Novelty Frameworks (ATTEMPT ALL SIX)

You MUST attempt to generate at least one hypothesis from EACH framework. Label each hypothesis with its framework.

1. **INVERSION**: Flip the dominant assumption in the literature. If the field assumes X causes Y, hypothesize that Y causes X, or that X has the opposite effect under specific conditions.
   - Test: Does the evidence actually rule out the inverted claim? If not, it's a valid hypothesis.

2. **MISSING LINK**: Map the causal chain in the evidence (A → B → C → D). Find an unstudied link — a step in the chain that nobody has directly tested. Make that link the hypothesis.
   - Test: Can you name the specific studies that cover adjacent links but NOT this one?

3. **MODERATOR**: Identify a hidden boundary condition that makes the dominant effect appear, disappear, or reverse. Look for subgroups, contexts, or thresholds that split the evidence.
   - Test: Does the moderator predict which studies found strong vs. weak vs. null effects?

4. **CROSS-DOMAIN TRANSFER**: Import a validated framework, method, or theory from Field A and apply it to Field B (the research topic). The brancher phase's cross-domain findings should directly inform this.
   - Test: Has this specific framework been applied to this specific population/outcome before?

5. **MEASUREMENT CHALLENGE**: Show that the field's standard proxy metric is uncorrelated (or weakly correlated) with the actual outcome of interest. Propose a better measurement approach.
   - Test: Can you cite studies using different measures that reach different conclusions?

6. **SYNTHESIS TAXONOMY**: The literature conflates distinct phenomena under one umbrella term. Classify the conflated evidence into 2-3 distinct sub-phenomena and show they have different mechanisms/effects.
   - Test: Do different studies' contradictory results align when separated into your taxonomy?

### Process

1. **Review verified claims and gaps** from previous phases via search_similar or read_paper.
2. **For EACH of the 6 frameworks above**, attempt to generate a hypothesis. If a framework genuinely cannot produce a hypothesis for this topic, explain why in 1-2 sentences.
3. **Score each hypothesis** using score_hypothesis on 6 dimensions:
   - **Novelty** (0-1): How new is this relative to existing literature?
   - **Feasibility** (0-1): Can this be tested with available methods?
   - **Evidence strength** (0-1): How well do verified claims support this?
   - **Methodology fit** (0-1): Does a clear methodology exist?
   - **Impact** (0-1): If true, how significant would this be?
   - **Reproducibility** (0-1): Could another researcher replicate a test?

4. **For the TOP hypothesis, specify methodology:**
   - **Review framework**: PRISMA 2020 for systematic reviews
   - **Evidence grading**: GRADE framework (High/Moderate/Low/Very Low)
   - **Quality assessment**: JBI Critical Appraisal or Newcastle-Ottawa Scale
   - **Analysis approach**: Narrative synthesis, thematic analysis, or meta-analysis
   - **PICO/PEO format**: Population, Intervention/Exposure, Comparison, Outcome
   - **Inclusion criteria**: Study types, date range, language, population
   - **Exclusion criteria**: What to filter out and why

5. **Rank hypotheses** by overall score.
6. **Present top 5 hypotheses** with framework label and methodology plan for #1.

### The Five Questions (MANDATORY for top hypothesis)

Before finalizing the top hypothesis, answer ALL five questions in sequence. These block shallow thinking:

**Q1. "What would have to be true for this to be wrong?"**
Forces falsifiability. If you can't answer, the claim isn't scientific. Name the specific evidence or result that would disprove the hypothesis. This answer goes into the paper's Limitations section.

**Q2. "Who already knows this, and what do they believe?"**
Forces literature positioning. Name the specific expert, paper, or review whose belief this hypothesis challenges or confirms. If you can't name that person or paper, you don't know the field well enough.

**Q3. "What's the mechanism?"**
Correlation is not enough. State the proposed causal pathway in 2 sentences — biological, behavioral, economic, or social. If you can't, you're describing a pattern, not explaining one. This answer informs the Causal Inference Analysis in Discussion.

**Q4. "What's the weakest point in this argument?"**
Forces self-critique before reviewers do it. Be specific: methodological gap, confound not controlled, population not represented, measurement limitation. This answer goes directly into the Limitations section.

**Q5. "So what?"**
Who changes their behavior if this is true? What decision gets made differently? What does the field stop doing or start doing? If you can't answer, the work may be technically correct but intellectually inert. This answer informs Policy Implications.

**Present all 5 answers explicitly for the top hypothesis.** The writer will use Q1 and Q4 for limitations, Q3 for causal analysis, and Q5 for implications.

### Scoring — Novelty is King

Novelty has **2x weight** in the overall score. Score accordingly:
- Novelty 0.9+ = genuinely surprising, challenges existing belief
- Novelty 0.7-0.9 = adds meaningful new angle to the field
- Novelty 0.5-0.7 = incremental extension of existing work
- Novelty <0.5 = restatement of known findings — REJECT

### Quality Standards
- Each hypothesis must be falsifiable (tested by Q1)
- Each hypothesis must cite at least 2 supporting claims from the database
- Each hypothesis must be labeled with its novelty framework (INVERSION, MISSING LINK, etc.)
- Include at least 1 hypothesis informed by cross-domain brancher findings (CROSS-DOMAIN TRANSFER)
- Hypotheses should vary in risk/novelty (some safe, some bold)
- The methodology plan must be detailed enough for the methods section
- **Meta-test**: For each hypothesis, answer: "Would an expert believe something different after reading this?" If no, the hypothesis is not novel enough.
"""
