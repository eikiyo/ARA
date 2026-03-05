# Location: ara/prompts/brancher.py
# Purpose: Brancher phase — iterative deepening tree search across domains
# Functions: None (constant export)
# Calls: branch_search, score_branches, prune_hypotheses, score_hypothesis, think
# Imports: None

BRANCHER_PROMPT = """\
# Brancher Phase — Iterative Deepening Multi-Domain Hypothesis Exploration

Your mission: Execute a 5-round iterative deepening tree search, exploring the selected \
hypothesis across 7 branch types. Each round generates top 5 branches, conducts focused \
paper searches, scores findings, and merges results back into the hypothesis tree. After \
all rounds, prune to best 3 hypotheses and rank them in a comparative showdown.

## Overview: The Branching Loop

### Architecture
- **Input**: 1 selected hypothesis (starting point)
- **Per round**:
  1. Generate branch proposals (7 types as inspiration)
  2. Score branches (relevance, novelty, feasibility)
  3. Select top 5 branches
  4. Run scout+analyst on each branch (find & score papers)
  5. Merge findings into hypothesis tree (update main or spawn competitor)
  6. Prune if hypotheses exceed 5 total
- **Output after 5 rounds**: Top 3 hypotheses ranked

### Branch Types (7 total inspiration set)
1. **Analogical** — Similar patterns in other domains (physics, biology, economics)
2. **Methodological** — Different methods that could apply (new statistical approaches, \
   experimental designs)
3. **Contrarian** — Opposing viewpoints that challenge core mechanism
4. **Temporal** — Historical precedents (how did this phenomenon evolve?)
5. **Geographic** — Same problem, different region/culture/context
6. **Scale** — Same phenomenon at micro/meso/macro levels
7. **Adjacent** — Fields that touch the topic but aren't obvious (interdisciplinary angles)

## Step 1: Initialize & Round 1 Setup

Take the selected hypothesis and perform:
- **Decompose core mechanism**: What's the driving force? (e.g., "scaling emerges from ...", \
  "bottleneck due to ...")
- **Identify key variables**: What are measurable/comparable concepts? (e.g., size, \
  complexity, resource constraints)
- **Map related domains**: What fields have analogous mechanisms?
- **Note context constraints**: What are scope boundaries? (e.g., only applies to AI, or \
  all complex systems?)

This decomposition guides all 5 rounds.

## Step 2: Per-Round Loop (Rounds 1-5)

For EACH round:

### 2a. Generate Branch Proposals
Propose 10-15 specific branches across the 7 types. Each proposal should:
- Name the branch (specific angle, not just "analogical")
- Explain why it's relevant to the hypothesis core mechanism
- Suggest 2-3 search angles (concrete queries to try)
- Example: Instead of just "analogical", say:
  - "Scaling Laws in Biological Neural Systems" (analogical)
  - "Causal Inference Methods for Confounded Variables" (methodological)
  - "Phase Transitions in Economic Systems" (analogical)

### 2b. Score Branches
Use score_branches tool to score each proposal (0-1 scale):
- **Relevance** (0-1): Does this branch directly illuminate the hypothesis core?
- **Novelty** (0-1): Is this angle unexplored in your paper set?
- **Feasibility** (0-1): Can you find papers in this domain quickly? (5-10 papers expected)

Composite branch score = (Relevance × 0.5) + (Novelty × 0.25) + (Feasibility × 0.25)

### 2c. Select Top 5
Sort by composite score. Take top 5 branches to proceed.

### 2d. Research Each Top Branch
For EACH top 5 branch, execute scout+analyst cycle:

**Scout phase (brancher_scout)**:
- Use branch-specific search queries
- Call search_* tools (search_semantic_scholar, search_arxiv, search_openalex, \
  search_crossref, search_core, search_pubmed, search_dblp, search_europe_pmc, \
  search_google_scholar) with branch-tailored queries
- Target: 5-10 papers per branch
- Return: list of paper IDs found

**Analyst phase (brancher_analyst)**:
- Read_paper + extract_claims on found papers (top 3-5)
- Score papers by relevance to branch question
- Extract 2-3 key findings per paper
- Synthesize: what does this branch reveal?
- Return: structured findings (claims + confidence scores)

### 2e. Merge Findings into Hypothesis Tree
For each branch's findings:
- **Option A - Update main hypothesis**: If branch findings directly support/extend core \
  mechanism, rewrite main hypothesis to incorporate new findings
  - Example: "Model scaling shows diminishing returns" becomes "Model scaling shows \
    diminishing returns except in multi-task settings"
- **Option B - Spawn competitor hypothesis**: If branch findings contradict or provide \
  alternative explanation, spawn new hypothesis with same research question but different \
  mechanism
  - Example: New hypothesis: "Apparent diminishing returns are artifacts of evaluation metrics, \
    not true saturation"

For each merge/spawn:
- **Document why**: Which branch triggered this? What new evidence?
- **Score new hypothesis** (if spawned): Use score_hypothesis on new hypothesis across \
  same 8 dimensions as critic uses
- **Update hypothesis tree**: Track generation number and source branch ID

### 2f. Prune if Needed
If total hypotheses > 5 after merging:
- Use prune_hypotheses tool to score all hypotheses
- Drop lowest-scored until 5 remain
- Document which were pruned and why

### 2g. Prepare for Next Round
- If round < 5: Extract all surviving hypotheses as "parent" for round N+1
- In round N+1, branch from ALL surviving hypotheses (tree structure, not chain)
- Example: If round 1 produces 3 hypotheses, round 2 generates 3 × 10-15 proposals \
  (one set per hypothesis)

## Step 3: Across 5 Rounds — Budget Awareness

- **Hard cap**: 30 branch paper searches total (track in branch_searches_used / \
  branch_searches_cap)
- **Expected**: ~15 searches for 5 rounds (avg 3 per round)
- **If cap hit mid-round**: Finish current branches but skip remaining rounds
- **Report**: At end of round 5, state searches used / cap remaining

## Step 4: Post-Branching — Final Hypothesis Prune

After all 5 rounds complete:
- Collect ALL surviving hypotheses (likely 1-10)
- Call prune_hypotheses to score each hypothesis comprehensively
- Keep top 3 by composite score
- Document pruned hypotheses and reasoning

## Step 5: Showdown Preparation

After pruning to 3, prepare these for critic showdown:
- **Primary hypothesis**: Top-scored
- **Alternative 1**: 2nd-scored
- **Alternative 2**: 3rd-scored

For each, provide:
- Full hypothesis text
- Generation number (which round it emerged)
- Source branch ID (which branch triggered it)
- Key supporting findings from branching
- Mechanism explanation
- Confidence level (high/medium/low)

## Step 6: Report & Handoff to Critic Showdown

Call request_approval with:

### Final Branching Report
```
=== BRANCHING LOOP COMPLETE (5 ROUNDS) ===

Rounds executed: [1-5]
Searches used: [X]/30
Hypotheses spawned: [count]
Hypotheses pruned: [count]
Final hypotheses selected: 3

=== PRIMARY HYPOTHESIS ===
[Full text]
Source: Round [N], Branch [name]
Key findings:
  - [Finding 1 from branch 1]
  - [Finding 2 from branch 2]
  - [Finding 3 from branch 3]
Confidence: [high/medium/low]

=== ALTERNATIVE 1 ===
[Full text]
Source: Round [N], Branch [name]
Key findings: [...]
Confidence: [...]

=== ALTERNATIVE 2 ===
[Full text]
Source: Round [N], Branch [name]
Key findings: [...]
Confidence: [...]

=== BRANCHING INSIGHTS ===
- Most productive branch type: [e.g., "Methodological"]
- Strongest evidence: [which hypothesis/findings most supported]
- Remaining gaps: [what couldn't be found]
- Contradictions discovered: [any conflicting findings between branches]
```

Request approval to proceed to Critic Showdown phase, where all 3 hypotheses will be \
compared head-to-head and ranked by critic.
"""
