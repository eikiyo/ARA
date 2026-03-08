# DESIGN: ARA Guardian — Autonomous Pipeline Maintainer

**Status**: DRAFT — Awaiting Eikiyo approval before implementation
**Date**: 2026-03-08
**Scope**: New Python process that continuously monitors, fixes, and improves the ARA research pipeline
**Inspired by**: Conway Automaton heartbeat daemon, knowledge accumulator, self-modification engine

---

## 1. What Is This

ARA Guardian is a **separate, long-running Python agent** that acts as the pipeline's maintainer. It:

- Starts ARA pipeline runs on command or schedule
- Watches logs and database in real-time during runs
- Stops the pipeline mid-run when it detects quality problems
- Deploys fixes (config, prompts, thresholds, quality gates)
- Re-runs the pipeline with improvements applied
- Learns what works vs. what doesn't across runs
- Strives for JIBS-level paper quality as the north star

Think of it as **you, but automated** — observing ARA run, noticing problems, fixing them, running again.

---

## 2. Architecture

```
+------------------+          +------------------+
|   ARA Guardian   |  starts  |   ARA Pipeline   |
|   (Sonnet/Opus)  |--------->|   (Gemini/etc)   |
|                  |  stops   |                  |
|  Heartbeat Loop  |--------->|  11-phase engine |
|  Log Watcher     |<---------|  Structured logs |
|  DB Monitor      |<---------|  SQLite DB       |
|  Knowledge Base  |          |  Output files    |
|  Config Editor   |--------->|  ara/config.py   |
|  Audit Trail     |          |                  |
+------------------+          +------------------+
        |
        v
  ~/.ara/guardian/
    guardian.db        # Knowledge base, run history, decisions
    audit.log          # Immutable append-only decision trail
    constitution.md    # Immutable rules
    SOUL.md            # Guardian's evolving self-assessment
```

**Key principle**: Guardian and ARA are separate processes. Guardian starts ARA as a subprocess, monitors its logs/DB, and can kill + restart it. They share the filesystem (logs, DB, config) but not memory.

---

## 3. Core Components

### 3.1 Heartbeat Daemon (from Automaton)

Continuous monitoring loop that runs even between pipeline runs.

**Tasks** (checked every tick, configurable intervals):

| Task | Interval | Purpose |
|------|----------|---------|
| `watch_logs` | 5s | Parse ARA log file for errors, warnings, phase transitions |
| `watch_db` | 30s | Check paper counts, claim quality, citation integrity |
| `watch_output` | 60s | Monitor output files for quality regressions |
| `quality_pulse` | 5m | Run JIBS quality gates on current paper draft |
| `run_health` | 30s | Check if ARA subprocess is alive, hung, or crashed |
| `cost_monitor` | 60s | Track API spend, alert if budget exceeded |
| `knowledge_sync` | 10m | Persist learnings to guardian.db |
| `config_drift` | 30m | Compare current config to known-good baselines |

**Implementation**: Like Automaton's `DurableScheduler` — DB-backed, no `setInterval`. Each task returns `{ action: "none" | "warn" | "stop_pipeline" | "fix_and_rerun", details }`.

### 3.2 Log Watcher

ARA currently uses unstructured text logs. Guardian needs structured signals.

**Phase 1 (immediate)**: Parse existing `ara.log` with regex patterns:
- `PIPELINE PHASE: <name>` — track phase transitions
- `Rate limited on <model>` — track rate limit frequency
- `DISPATCH FAIL: <tool> — <error>` — track tool failures
- `WARNING` / `ERROR` lines — aggregate error rates
- Phase timing (time between phase markers)

**Phase 2 (new structured logging)**: Add a structured event log to ARA:
- `ara/events.py` — writes JSON-lines to `events.jsonl`
- Events: `phase_start`, `phase_end`, `tool_call`, `quality_gate_result`, `citation_verified`, `claim_extracted`, `section_written`, `error`
- Each event: `{"ts": "...", "event": "...", "phase": "...", "data": {...}}`
- Guardian reads this file with tail-follow semantics

### 3.3 Database Monitor

Watches ARA's SQLite databases (session DB + central DB) for quality signals:

| Signal | Query | Threshold | Action |
|--------|-------|-----------|--------|
| Paper count | `SELECT COUNT(*) FROM papers` | < min_papers at phase end | Warn: extend scout |
| Claim density | `claims / deep_read_papers` | < 2.5 claims/paper | Stop: deep_read quality problem |
| Citation integrity | `verified / total citations` | < 50% | Stop: citation pipeline broken |
| RoB coverage | `papers with RoB / total papers` | < 30% | Warn: RoB assessment gaps |
| GRADE coverage | `outcomes with GRADE / total` | < 50% | Warn: GRADE assessment gaps |
| Embedding coverage | `papers with embedding / total` | < 80% | Warn: embedding gaps |
| Journal tier ratio | `AAA+AA papers / total` | < 50% | Warn: source quality low |
| Hypothesis scores | `AVG(novelty + feasibility)` | < 6/10 | Stop: hypothesis quality low |

### 3.4 JIBS Quality Gates

Six automated gates derived from JIBS reviewer criteria. Run on current paper draft at `quality_pulse` interval and before declaring a run "complete."

**Gate 1: Intellectual Depth**
- Research question explicitly stated in introduction (pattern match)
- Theory section > 20% of paper word count
- Causal mechanisms explained (not just "X relates to Y" — must have "because/through/via")
- Testable propositions present (numbered P1, P2, etc.)
- Assumptions, limitations, alternatives discussed

**Gate 2: Methodology Rigor**
- Methods section present with statistical methodology citations
- Effect sizes reported (not just p-values)
- Sample sizes stated
- Study design described
- Limitations section present

**Gate 3: Writing Quality**
- No repeated gap statements (max 2 per section — already in ARA)
- Average sentence length < 30 words (already in ARA)
- Section overlap < 0.30 cosine similarity (already in ARA)
- No orphan citations (cited but not in references)
- No ghost references (in references but never cited)
- Consistent framework/proposition naming

**Gate 4: Citation Standards**
- All citations have direct bearing on topic (semantic similarity > 0.4 to paper abstract)
- No excessive self-citation patterns
- Recent literature included (> 30% from last 5 years)
- Interdisciplinary sources present (> 2 distinct fields)
- Journal tier ratio meets JIBS threshold (50%+ AAA/AA)

**Gate 5: Structural Completeness**
- All required sections present
- Abstract < 200 words, stands alone
- Total word count 7,000-10,000
- PRISMA flow numbers internally consistent
- Tables/figures referenced in text

**Gate 6: Contribution Assessment** (LLM-evaluated)
- Novel insight clearly articulated
- Theoretical contribution to field stated
- Practical implications discussed
- "So what?" question answered
- Future research directions suggested

**Scoring**: Each gate scores 0-100. Paper must score > 70 on all six gates to be considered JIBS-ready. Guardian tracks scores across runs to measure improvement.

### 3.5 Knowledge Base (from Automaton's Knowledge Accumulator)

SQLite database (`~/.ara/guardian/guardian.db`) that persists learnings across runs.

**Tables:**

```sql
-- What the guardian has learned
knowledge_entries (
    id TEXT PRIMARY KEY,
    category TEXT,          -- 'what_works', 'what_fails', 'config_insight',
                            -- 'quality_pattern', 'model_behavior', 'phase_timing'
    content TEXT,
    confidence REAL,        -- 0.0 to 1.0
    times_confirmed INTEGER DEFAULT 0,
    times_contradicted INTEGER DEFAULT 0,
    source_run_ids TEXT,    -- JSON array of run IDs that contributed
    created_at TEXT,
    updated_at TEXT
)

-- Run history with quality outcomes
run_history (
    id TEXT PRIMARY KEY,
    topic TEXT,
    paper_type TEXT,
    config_snapshot TEXT,    -- JSON of config at run start
    jibs_scores TEXT,       -- JSON of 6 gate scores
    overall_quality REAL,   -- 0-100 composite
    duration_seconds INTEGER,
    cost_usd REAL,
    phases_completed TEXT,   -- JSON array
    failure_reason TEXT,     -- NULL if successful
    fixes_applied TEXT,      -- JSON array of fixes applied mid-run
    created_at TEXT
)

-- Config changes with outcomes
config_experiments (
    id TEXT PRIMARY KEY,
    parameter TEXT,          -- e.g., 'triage_select_threshold'
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    run_id TEXT,
    quality_before REAL,
    quality_after REAL,
    kept BOOLEAN,            -- Did guardian keep this change?
    created_at TEXT
)

-- Decisions made (audit trail)
decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    phase TEXT,
    decision_type TEXT,      -- 'stop', 'fix', 'adjust_config', 'rerun', 'approve'
    reasoning TEXT,          -- LLM reasoning for the decision
    action_taken TEXT,       -- What was actually done
    outcome TEXT,            -- Result of the action
    created_at TEXT
)
```

**Learning loop**: After each run, Guardian reviews:
1. What JIBS gate scores improved vs. degraded?
2. What config changes correlated with improvements?
3. What phases took longest / failed most?
4. What knowledge entries were confirmed vs. contradicted?

### 3.6 Self-Modification Engine (from Automaton)

Guardian can modify ARA's behavior through these levers:

| Lever | What Changes | How | Reversible |
|-------|-------------|-----|-----------|
| **Config values** | `ara/config.py` env vars | Write `.env` file or pass CLI args | Yes (snapshot before) |
| **Quality thresholds** | Gate scores, word counts, citation mins | Edit config values | Yes |
| **MMR parameters** | Cosine threshold, lambda, top_k | Edit config or DB defaults | Yes |
| **Prompts** | Phase system prompts | Edit `ara/prompts/*.py` | Yes (git) |
| **Quality gates** | Add/modify checks in engine.py | Edit source | Yes (git) |
| **Tools** | Add new search sources, verification tools | Add to `ara/tools/` | Yes (git) |
| **Pipeline flow** | Skip/add phases, change phase order | Edit engine.py | Yes (git) |

**Safety rules** (from Automaton's protected files concept):

| Rule | Description |
|------|-------------|
| **Never delete papers** | Can add, never remove from DB |
| **Never weaken quality gates** | Can add gates, raise thresholds; never lower below constitution minimums |
| **Never exceed budget** | Hard budget cap respected always |
| **Git before modify** | Commit snapshot before any source edit |
| **Rate limit self-mod** | Max 5 source code changes per run cycle |
| **Log every decision** | Append-only audit trail for all decisions |

### 3.7 Constitution (Immutable Rules)

```markdown
# ARA Guardian Constitution

## I. Academic Integrity Above All
Never fabricate citations, data, or findings. Never misrepresent sources.
Never cite papers that don't exist. Never claim findings a paper doesn't contain.
This overrides all quality targets, including JIBS scores.

## II. Improve Through Honest Means
Quality improvements must come from better evidence retrieval, deeper analysis,
and stronger writing — never from gaming metrics. A real 60/100 paper is better
than a gamed 90/100 paper.

## III. Preserve Audit Trail
Every decision, config change, and source modification must be logged.
The audit trail is append-only and immutable. The creator has full visibility
into every action taken.

## IV. Respect Budget Constraints
Never exceed the configured budget limit. Stop gracefully when budget is
exhausted rather than producing incomplete work.

## V. Never Weaken Below Minimums
Quality gate minimums defined in this constitution cannot be lowered:
- Citation integrity: >= 50%
- Journal tier ratio: >= 40%
- Section overlap: <= 0.35
- Proposition limit: <= 7
- Total word count: <= 12,000
```

### 3.8 Dynamic Tool Provisioning (from Automaton's Skills)

Guardian can provide new tools to ARA at runtime. Examples:

**Immediate tools** (port from Automaton epistemic module):
- `search_google_scholar` — Google Scholar scraping for broader coverage
- `check_journal_ranking` — ABS/ABDC/SJR tier lookup for any journal
- `compute_effect_size` — Calculate Cohen's d, odds ratios from reported statistics
- `check_predatory_journal` — Beall's list + DOAJ cross-reference
- `semantic_dedup_citations` — Embedding-based citation deduplication

**Future tools** (Guardian discovers need and builds):
- New search API integrations (as new sources become available)
- Domain-specific validation (finance vs. management vs. IS)
- Temporal analysis tools (trend detection in literature)

**Mechanism**: Guardian writes tool definitions to `ara/tools/guardian_tools.py`, ARA loads them on next run via dynamic import.

---

## 4. Operational Flow

### 4.1 Normal Run Cycle

```
1. Guardian receives topic + paper_type (from user or schedule)
2. Guardian loads knowledge base, reviews past runs on similar topics
3. Guardian selects config based on knowledge (or defaults for new topic type)
4. Guardian starts ARA subprocess: `python -m ara --task "..." --no-tui --no-gates`
5. Guardian enters monitoring loop:
   a. Tail log file for errors/warnings/phase transitions
   b. Poll DB for quality signals every 30s
   c. Run JIBS quality gates on draft output every 5m
   d. Track cost accumulation
6. If quality problem detected:
   a. Log decision + reasoning to audit trail
   b. Stop ARA subprocess (SIGTERM)
   c. Apply fix (config change, prompt edit, etc.)
   d. Git commit the fix
   e. Restart ARA with --resume flag
7. When ARA completes:
   a. Run full JIBS 6-gate assessment
   b. Record run in run_history
   c. Extract knowledge entries (what worked, what failed)
   d. If quality < threshold: decide whether to re-run with fixes
   e. If quality >= threshold: declare success, archive output
```

### 4.2 Fix-and-Rerun Decision

Guardian uses Sonnet/Opus to reason about whether a fix is worth attempting:

```
Input to LLM:
- Current JIBS gate scores (6 dimensions)
- Specific failures identified
- Knowledge base: similar past fixes and their outcomes
- Budget remaining
- Run count for this topic (diminishing returns after 3 re-runs)

Output:
- Decision: rerun / accept / abandon
- If rerun: specific fixes to apply (config changes, prompt edits)
- Reasoning: why this fix should help (logged to audit trail)
```

**Hard limits on re-runs**:
- Max 3 re-runs per topic per session
- Each re-run must target different failures (no repeating same fix)
- Budget must allow at least 1 full run remaining

### 4.3 Between-Run Learning

After each run (success or failure), Guardian:

1. Compares config snapshot to quality outcome
2. Identifies which config changes correlated with improvement
3. Updates knowledge entries (confirm or contradict)
4. Generates `SOUL.md` reflection (like Automaton):
   - What topics does Guardian handle well?
   - What quality dimensions are consistently weak?
   - What config ranges produce best results?
   - What's the average JIBS score trend over time?

---

## 5. Deployment (Hetzner)

### Infrastructure

```
Hetzner VPS (CPX31 or similar):
  - 4 vCPU, 8GB RAM, 160GB SSD
  - Ubuntu 24.04
  - Python 3.12+
  - SQLite (local, no external DB needed)
  - Git (for audit trail)

Process management:
  - systemd service for Guardian (auto-restart on crash)
  - Guardian manages ARA as subprocess

Storage:
  - ~/.ara/central.db          (shared paper/claims DB)
  - ~/.ara/guardian/guardian.db (knowledge base)
  - ~/.ara/guardian/audit.log   (immutable decisions)
  - ~/ara_runs/<topic>/        (per-run output)
```

### Monitoring

- Guardian writes its own health metrics to `guardian.db`
- Simple HTTP endpoint (optional): `/health`, `/status`, `/runs`
- Log rotation on both ARA and Guardian logs
- Disk space monitoring (alert if < 5GB free)

### Cost Estimation

| Component | Model | Est. Cost/Run |
|-----------|-------|---------------|
| ARA pipeline | Gemini Flash/Pro | $5-15 |
| ARA peer review | Claude/GPT | $5-8 |
| Guardian monitoring | Sonnet | $0.50-1 per run |
| Guardian fix decisions | Opus | $0.20-0.50 per decision |
| **Total per run** | | **$10-25** |
| **Hetzner VPS** | CPX31 | **~$15/month** |

---

## 6. Implementation Phases

### Phase 1: Foundation (Build the daemon + log watcher)
- Guardian process skeleton with heartbeat loop
- Log parser for existing ARA unstructured logs
- Subprocess management (start/stop/resume ARA)
- Constitution file + audit trail (append-only log)
- Run history tracking in guardian.db
- Files: `guardian/daemon.py`, `guardian/log_watcher.py`, `guardian/process.py`, `guardian/audit.py`, `guardian/constitution.md`

### Phase 2: Quality Assessment (JIBS gates + DB monitoring)
- JIBS 6-gate quality assessment system
- Database monitoring queries (paper count, claim density, citation integrity)
- Structured event logging added to ARA (`ara/events.py`)
- Quality pulse task in heartbeat
- Files: `guardian/quality.py`, `guardian/db_monitor.py`, `ara/events.py`

### Phase 3: Self-Improvement (Knowledge base + fix engine)
- Knowledge base tables + learning loop
- Config experiment tracking
- Fix-and-rerun decision engine (LLM-powered)
- SOUL.md reflection after each run
- Files: `guardian/knowledge.py`, `guardian/fixer.py`, `guardian/soul.py`

### Phase 4: Tool Provisioning (Dynamic capabilities)
- Tool definition format for Guardian-created tools
- Dynamic import mechanism in ARA
- Initial tool set (journal ranking, effect size calc, dedup)
- Files: `guardian/tools.py`, `ara/tools/guardian_tools.py`

### Phase 5: Hetzner Deployment
- systemd service files
- Deployment script (git clone, pip install, configure)
- Health endpoint
- Log rotation config
- Monitoring alerts (disk, process, budget)

---

## 7. What This Does NOT Do

- **Not a UI** — Guardian is headless. You check results via output files and logs.
- **Not real-time chat** — Guardian doesn't take interactive input during runs.
- **Not a replacement for human review** — Guardian improves drafts toward JIBS level; final human review still essential.
- **Not unlimited budget** — Hard budget caps enforced by constitution.
- **Not autonomous research topic selection** — You tell it what to research. It decides how to research it well.

---

## 8. Open Questions for Eikiyo

1. **Notification**: Should Guardian notify you when a run completes? (Email? Telegram? Just log files?)
2. **Scheduling**: Should Guardian accept a queue of topics and process them sequentially? Or one-at-a-time?
3. **Human override**: Should there be a way to SSH in and give Guardian a one-off instruction mid-run?
4. **Multi-run**: Should Guardian be able to run multiple ARA instances in parallel on different topics?
5. **Paper storage**: Should completed papers be pushed to a git repo automatically?
