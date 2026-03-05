# Location: ara/prompts/brancher.py
# Purpose: Brancher phase — explore connections across domains and methodologies
# Functions: None (constant export)
# Calls: search_papers, request_approval
# Imports: None

BRANCHER_PROMPT = """\
# Brancher Phase — Multi-Domain Hypothesis Exploration

Your mission: Take the selected hypothesis and search for supporting evidence \
across four types of research branches: lateral, methodological, analogical, and convergent.

## Understanding Branch Types

### 1. Lateral Branch (Same Field, Different Angle)
Extend the hypothesis within the same scientific domain but from a different \
perspective or with different variables.
- Same field, different methodology
- Same question, different population or context
- Related phenomena, same theoretical framework
- Example: If hypothesis is about neural network scaling, search for studies about \
  neural network scaling in different domains (vision, NLP, speech, RL)

### 2. Methodological Branch (Methods from Another Field)
The hypothesis may be testable using methods from a different field. Find papers \
that use those methods on related questions.
- Example: If hypothesis involves causality, search for papers using causal inference \
  methods (Granger causality, instrumental variables) in different domains
- Search: "[methodology term] applied to [related domain]"
- Look for papers that borrowed methods from other fields

### 3. Analogical Branch (Analogous Phenomena in Other Domains)
Find analogous phenomena in completely different domains that support the \
underlying mechanism or principle.
- Example: If hypothesis is about scaling laws in AI, search for scaling laws in \
  biology (neural systems), economics (network effects), physics (critical phenomena)
- Reasoning: If the same pattern appears in multiple independent systems, \
  it's likely fundamental
- Look for: common mechanisms, similar mathematical models, parallel findings

### 4. Convergent Branch (Multiple Domains Supporting Same Conclusion)
Find independent lines of evidence from different domains that all point to \
the same conclusion.
- Example: If hypothesis predicts X has property Y, search for papers from \
  neuroscience, computational models, and behavioral studies all showing Y
- Reasoning: Convergent evidence from independent sources is stronger than \
  any single line
- Look for: papers that don't cite each other but reach similar conclusions

## Step 1: Hypothesis Analysis
Take the selected hypothesis and identify:
- **Core mechanism**: What causes the effect? (e.g., "scaling laws emerge from ...", \
  "bottleneck effect due to ...")
- **Key variables**: What are the main concepts? (e.g., model size, task complexity, data volume)
- **Domain**: Where is this hypothesis located? (e.g., deep learning, neuroscience, economics)
- **Related domains**: What other fields have similar mechanisms or variables?

## Step 2: Generate Branch Queries
For each branch type, create 2-3 search queries:

### Lateral Branch
- Query 1: [core phenomenon] + [different domain] (e.g., "scaling laws" + "neuroscience")
- Query 2: [core mechanism] + [related context] (e.g., "overparameterization" + "biological networks")
- Query 3: [variable 1] + [variable 2] in [broader domain]

### Methodological Branch
- Query 1: [methodology name] + [hypothesis domain]
- Query 2: [methodology] applied to [related problem]
- Query 3: [adjacent methodology] + [domain]

### Analogical Branch
- Query 1: [core principle] + [completely different domain] (e.g., "phase transitions" + "economics")
- Query 2: [analogous phenomenon] + [mechanism terms]
- Query 3: [mathematical structure] in [other domain]

### Convergent Branch
- Query 1: [conclusion] independent [evidence from multiple methods]
- Query 2: [phenomenon name] cross-domain validation
- Query 3: [theoretical prediction] empirical evidence [multiple fields]

## Step 3: Multi-Database Search
For each branch and query:
- Search: Semantic Scholar, CrossRef, arXiv, OpenAlex
- Limit: top 20 results per query (adjust if no results)
- Record: title, authors, year, DOI, relevance to branch (why does this support it?)
- Score: How strongly does this paper support the hypothesis? (0-1)

## Step 4: Branch Synthesis
For each branch type, report:
- **Branch Name**: [lateral/methodological/analogical/convergent]
- **Queries Used**: [list]
- **Papers Found**: [count] across all queries
- **Most Relevant Papers**: [top 3-5 with scores]
- **Confidence Score**: (0-1) How convincing is this branch?
  - High confidence: Multiple papers, consistent findings, high-impact sources
  - Medium confidence: Some supporting papers, mixed but not contradictory
  - Low confidence: Few papers, weak support, or limited relevance
- **Key Finding**: What does this branch add to the hypothesis?
- **Gaps**: What couldn't you find?

## Step 5: Branch Map & Approval
Create a visual summary (text format):

```
Hypothesis: [selected hypothesis]

Lateral Branch: [confidence score]
  [top 2-3 papers with support rationale]
  Finding: [what this adds]

Methodological Branch: [confidence score]
  [top 2-3 papers]
  Finding: [what this adds]

Analogical Branch: [confidence score]
  [top 2-3 papers]
  Finding: [what this adds]

Convergent Branch: [confidence score]
  [top 2-3 papers]
  Finding: [what this adds]

Overall Assessment:
- Hypothesis strength with branches: [high/medium/low]
- Most convincing branch: [which one and why]
- Remaining uncertainties: [list]
```

Call request_approval with the full branch map. User will approve for critic phase \
or request deeper exploration of specific branches.
"""
