# Location: ara/prompts/analyst.py
# Purpose: Analyst phase — triage and deep reading of papers
# Functions: None (constant export)
# Calls: fetch_fulltext, request_approval
# Imports: None

ANALYST_TRIAGE_PROMPT = """\
# Analyst Triage Phase — Paper Scoring & Selection

Your mission: Read abstracts of all discovered papers and score their relevance to the \
research question. Identify the most important papers for deep reading.

## Step 1: Relevance Scoring
For each paper, score relevance on a 0-1 scale based on:
- **Direct relevance** (0.3): Does the paper address the core research question directly?
- **Topic alignment** (0.3): Does it cover key concepts or related phenomena?
- **Methodological relevance** (0.2): Does its methodology apply to the question?
- **Source credibility** (0.2): Is it peer-reviewed? Highly cited? By recognized authors?

Score = (direct_weight × 0.3) + (topic_weight × 0.3) + (method_weight × 0.2) + \
        (credibility_weight × 0.2)

Document the reasoning for each score.

## Step 2: Thematic Grouping
Cluster papers by subtopic or theme:
- Identify natural groupings (e.g., by methodology, by domain, by theoretical perspective)
- Label each cluster (e.g., "computational methods", "empirical studies", "theoretical frameworks")
- Note which clusters are well-represented and which are sparse

## Step 3: Selection for Deep Read
Apply tiered selection:
- **Tier 1** (must read): Relevance ≥ 0.8, or foundational papers, or key methodological papers
- **Tier 2** (should read): Relevance 0.6-0.8, or addresses important gaps
- **Tier 3** (optional): Relevance 0.4-0.6, or provides context

Typically select 10-30 papers for deep reading (Tiers 1 & 2). Adjust based on topic breadth.

## Step 4: Ranked List & Approval
Produce a ranked list of top 30 papers, formatted as:
[Rank]. [Relevance Score] | [Authors, Year] | [Title] | [DOI/Source]
    Reason: [brief justification]

Call request_approval with the ranked list and cluster summary. \
User will approve the selection or request adjustments (e.g., "skip this cluster", \
"include more methods papers").
"""

ANALYST_DEEP_READ_PROMPT = """\
# Analyst Deep Read Phase — Claim Extraction

Your mission: Read the full text of selected papers and extract atomic claims, \
noting methodology, limitations, and contradictions.

## Step 1: Full-Text Retrieval
For each selected paper:
- Attempt to fetch_fulltext using DOI
- If full text unavailable, use the abstract + available methods/results sections
- Log access failures (paywall, format issues)
- Note which papers are open-access vs. restricted

## Step 2: Atomic Claim Extraction
For each paper, identify and extract:
- **Atomic claim**: A specific, factual statement (e.g., "Study X found that Y increases \
  Z by 15% under conditions W")
- **Evidence type**: empirical result, theoretical prediction, meta-analysis finding, \
  survey result, expert opinion
- **Confidence of claim**: How confident are the authors? (from paper language: "demonstrates", \
  "suggests", "likely", "speculates")
- **Scope**: What population/context does this apply to? (sample size, domain, constraints)
- **Supporting quote**: Exact text from the paper that states the claim
- **Methodology**: How did they reach this conclusion? (experiment, survey, model, etc.)
- **Limitations noted**: What do the authors say are limitations of this finding?

Format each claim:
```
[Paper Author, Year]
Claim: [atomic statement]
Evidence Type: [empirical/theoretical/meta/survey/opinion]
Confidence: [high/medium/low]
Scope: [population/context]
Quote: "[exact text]"
Method: [how derived]
Limitations: [author-noted limits]
```

## Step 3: Contradiction Flagging
As you extract claims, identify contradictions:
- When two papers make opposite or conflicting claims about the same phenomenon
- Document each contradiction with both claims side-by-side
- Note possible reasons (different populations, different methods, different time periods, etc.)
- Do not try to resolve — just flag and report

Format contradictions:
```
Contradiction: [phenomenon in question]
Paper A (Author, Year): [claim A]
Paper B (Author, Year): [claim B]
Possible causes: [methodological difference / population difference / temporal difference / ...]
```

## Step 4: Gap Identification
Note:
- Claims that appear frequently (consensus)
- Claims that appear in only one or two papers (outliers)
- Questions mentioned by papers that are NOT answered in any paper (gaps)
- Methodological innovations or debates across papers

## Step 5: Approval & Handoff
Report:
- Total papers read: [count]
- Total claims extracted: [count] (broken down by evidence type)
- Contradictions found: [count]
- Major gaps identified: [list]
- Papers with access issues: [count]

Call request_approval with all extracted claims organized by theme. \
Include the list of contradictions and identified gaps. User will approve for verification phase.
"""
