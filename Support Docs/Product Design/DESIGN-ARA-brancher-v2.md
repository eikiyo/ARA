# DESIGN: ARA Brancher v2 — Iterative Deepening with Tree Search

## Summary

Replace the single-pass brancher phase with an iterative deepening tree search. Each round generates branches, scores them, and the top branches trigger mini research cycles (scout + analyst). Findings merge back into the main hypothesis and can spawn competing hypotheses. 5 rounds max, top 3 hypotheses ranked in a final showdown.

## User Decisions

| # | Question | Answer |
|---|----------|--------|
| 1 | Rounds of branching | 5 |
| 2 | Real paper searches or LLM reasoning | Real (API searches) |
| 3 | User approves branches or agent picks | Agent picks |
| 4 | Budget concern | OK |
| 5 | Merge or spawn competing hypotheses | Both |
| 6 | Branches per round | Top 5 |
| 7 | Tree (all surviving) or chain (best single) | Tree |
| 8 | Kill threshold | No — always keep top 5 |
| 9 | Papers per branch scout | 5-10 |
| 10 | Branch research cycle | Scout + Analyst (no verifier) |
| 11 | Search APIs | Same 9 as main |
| 12 | Max competing hypotheses at end | Best 3 |
| 13 | Final showdown ranking | Yes |
| 14 | Merge behavior | Rewrite main hypothesis with branch findings |
| 15 | Hard cap on branch searches | 5 extra buffer (20 total: 5 rounds x 3 avg + 5 buffer) |

## Architecture

### Current Flow (v1)
```
Scout -> Analyst -> Verifier -> Hypothesis -> Brancher (1 pass) -> Critic -> Writer
```

### New Flow (v2)
```
Scout -> Analyst -> Verifier -> Hypothesis -> Brancher Loop -> Critic Showdown -> Writer
                                                  |
                                                  v
                                         Round 1: Generate 7 branch types
                                                  |
                                                  v
                                         Score + pick top 5
                                                  |
                                         +--------+--------+--------+--------+
                                         v        v        v        v        v
                                      Branch1  Branch2  Branch3  Branch4  Branch5
                                      (scout)  (scout)  (scout)  (scout)  (scout)
                                      (analyst)(analyst)(analyst)(analyst)(analyst)
                                         |        |        |        |        |
                                         v        v        v        v        v
                                         Merge findings -> Update/spawn hypotheses
                                                  |
                                                  v
                                         Round 2: Branch from ALL surviving branches
                                                  |
                                                  v
                                         ... repeat up to 5 rounds ...
                                                  |
                                                  v
                                         Select best 3 hypotheses
                                                  |
                                                  v
                                         Critic Showdown (rank 3 against each other)
                                                  |
                                                  v
                                         Writer (top hypothesis + supporting evidence)
```

### Branch Types (7 total)

1. **Analogical** — similar patterns in other domains
2. **Methodological** — different methods that could apply
3. **Contrarian** — opposing viewpoints
4. **Temporal** — historical precedents
5. **Geographic** — same problem, different region/culture
6. **Scale** — same phenomenon at micro/meso/macro level
7. **Adjacent** — fields that touch the topic but aren't obvious

### Round Mechanics

Each round:
1. **Generate**: Agent proposes branches (7 types as inspiration, not mandatory)
2. **Score**: Agent scores each branch 1-10 on (relevance, novelty, feasibility)
3. **Select**: Top 5 branches proceed
4. **Research**: Each branch triggers:
   - `search_*` tools with branch-specific queries (5-10 papers)
   - `read_paper` + `extract_claims` on top results
5. **Merge**: Findings integrate into existing hypotheses or spawn new ones
6. **Prune**: If total hypotheses > 5, drop lowest-scored ones

### Budget Guardrails

- Max 5 rounds
- Max 5 branches per round = 25 branch research cycles max
- 5 buffer = 30 total branch searches hard cap
- Each branch scout: 5-10 papers (not 50)
- Counter tracked in DB: `branch_searches_used` / `branch_searches_cap`
- If cap hit mid-round, finish current branches but skip remaining rounds

### Hypothesis Showdown

After branching completes:
1. Collect all surviving hypotheses (may be 1-10+)
2. Run critic scoring on each (8 dimensions)
3. Rank by composite score
4. Keep top 3
5. Writer receives all 3 with ranking — primary hypothesis + 2 alternatives

### DB Changes

```sql
-- Add to hypotheses table
ALTER TABLE hypotheses ADD COLUMN source_branch_id INTEGER REFERENCES branches(branch_id);
ALTER TABLE hypotheses ADD COLUMN generation INTEGER DEFAULT 0;  -- which round spawned it

-- Add to branches table
ALTER TABLE branches ADD COLUMN round INTEGER DEFAULT 1;
ALTER TABLE branches ADD COLUMN score REAL;
ALTER TABLE branches ADD COLUMN status TEXT DEFAULT 'pending';  -- pending, active, completed, pruned
ALTER TABLE branches ADD COLUMN papers_found INTEGER DEFAULT 0;

-- New table: branch search budget
CREATE TABLE IF NOT EXISTS branch_budget (
    session_id INTEGER REFERENCES research_sessions(session_id),
    searches_used INTEGER DEFAULT 0,
    searches_cap INTEGER DEFAULT 30,
    PRIMARY KEY (session_id)
);
```

### Prompt Changes

- **brancher.py**: Rewrite to support multi-round loop with branch generation + scoring
- **New: brancher_scout.py**: Lightweight scout prompt for branch-specific paper discovery
- **New: brancher_analyst.py**: Quick analyst for branch papers (score + extract claims only)
- **critic.py**: Add showdown mode — compare N hypotheses against each other
- **manager.py**: Update orchestration to handle brancher loop + showdown

### Tool Changes

- `branch_search`: Update to accept round number, branch type, and parent branch ID
- New tool: `score_branches` — score and rank a list of branch proposals
- New tool: `prune_hypotheses` — drop lowest-scored hypotheses beyond top N
- `score_hypothesis`: Update to support comparative mode (rank N hypotheses)

### Implementation Tasks

1. DB migrations (branch_budget table, new columns)
2. Update brancher prompt for multi-round loop
3. Create brancher_scout and brancher_analyst prompts
4. Update critic prompt for showdown mode
5. Add score_branches and prune_hypotheses tools
6. Update branch_search tool for round tracking
7. Update manager prompt for new brancher flow
8. Add branch budget tracking to pipeline tools
9. Update tool definitions (defs.py) with new tools
10. Integration tests for multi-round branching
