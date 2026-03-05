# DESIGN: ARA - Autonomous Research Agent (v2)

> **Location:** `Support Docs/Product Design/DESIGN-ARA-research-agent.md`
> **Purpose:** Full system design for ARA — a CLI research agent built on OpenPlanter's recursive engine
> **Status:** Design Complete — Ready for Implementation
> **Date:** 2026-03-05
> **Previous:** v1 archived at `Support Docs/Product Design/archive-v1/`

---

## 1. Product Vision

**What:** A CLI research agent you run from any folder. Type `ara`, describe your research topic, and ARA finds papers, extracts claims, verifies them, generates hypotheses, explores cross-domain connections, critiques the hypothesis, and drafts a research paper — with your approval at every step.

**How it feels:** Like Claude Code, but for academic research. You open a folder, type `ara`, talk naturally. ARA works, narrates progress, pauses for your approval at key checkpoints, and produces a real paper.

**Who:** Eikiyo (single user, PhD researcher). If it works, open it up later.

**Why:** PhD students cobble together Google Scholar + Zotero + ChatGPT + manual work. No tool stitches the full pipeline: discover → verify → hypothesize → cross-validate → write.

**Moat:** The Verifier (retraction/credibility checking) + Brancher (cross-domain tunnel vision prevention) are unique. No existing tool does either well.

**V1 Success Criteria:** A full IMRaD research paper with proper citations, an `index.html` preview, cached source papers, and session logs — produced end-to-end from a single research topic.

---

## 2. Architecture Overview

### 2.1 Design Philosophy

ARA is built on top of OpenPlanter's recursive language model engine. We copy OpenPlanter's core (engine, model abstraction, TUI, session management) into a fresh repo and add ARA-specific tools, prompts, and pipeline logic.

OpenPlanter's engine stays ~80% intact. We replace:
- **Tools:** Investigation tools → academic research tools (~20 tools)
- **Prompts:** OSINT investigation prompt → academic research pipeline prompt
- **TUI:** Restyle for ARA branding, add approval gate widgets

We keep:
- **Recursive engine loop** (`engine.py` — `_solve_recursive`)
- **Model abstraction** (`model.py` — OpenAI, Anthropic, OpenRouter, Ollama)
- **Model tiering cascade** (opus → sonnet → haiku)
- **Session persistence** (`runtime.py`)
- **Budget/step management**
- **Context condensation**
- **Acceptance criteria judging**
- **TUI framework** (`tui.py` — Rich-based)

### 2.2 How It Works

```
User: cd ~/research/my-topic && ara
ARA:  "What would you like to research?"
User: "The impact of transformer architectures on genomic sequence analysis"
ARA:  asks clarifying questions (scope, depth, sources)
ARA:  begins pipeline...

  Scout phase        → searches 9 academic APIs → approval gate
  Analyst (triage)   → ranks papers by relevance → approval gate
  Analyst (deep read)→ extracts claims from selected papers → approval gate
  Verifier           → checks retractions, citations, DOI → approval gate
  Hypothesis Gen     → generates ranked hypotheses → approval gate
  Brancher           → cross-domain search (4 branch types) → approval gate
  Critic             → scores hypothesis across dimensions → approval gate
  Writer             → outline → approval → full draft → approval gate

Output: .ara/output/paper.md + paper.html + index.html + references.bib + papers/
```

### 2.3 System Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Engine** | Python (OpenPlanter fork) | Recursive agent loop, tool execution, model calls |
| **LLM** | Multi-provider (Ollama primary, OpenRouter/Anthropic/OpenAI fallback) | Agent reasoning |
| **Embeddings** | Gemini text-embedding-004 (online) + Ollama nomic-embed-text (offline) | Paper/claim vectorization |
| **Database** | SQLite + sqlite-vec | Papers, claims, hypotheses, sessions, vector search |
| **TUI** | Rich (Python) | Terminal UI with tables, progress, approval gates |
| **Install** | `pip install ara-research` | Standard Python package |

### 2.4 Folder Structure (User's Perspective)

```
~/research/my-topic/           ← user's research folder
  .ara/                        ← ARA workspace (like .git/)
    config.yaml                ← per-project config overrides
    session.db                 ← SQLite + sqlite-vec database
    papers/                    ← cached PDFs and full-text
    gates/                     ← approval gate data files for review
      scout_results.md
      triage_ranking.md
      claims_extracted.md
      ...
    output/
      paper.md                 ← markdown draft
      paper.html               ← HTML rendering
      index.html               ← self-contained preview (embedded CSS)
      references.bib           ← BibTeX citations
    logs/
      session.jsonl            ← full conversation replay
      events.jsonl             ← lightweight event trace

~/.ara/                        ← global config
  config.yaml                  ← default LLM provider, API keys, preferences
```

---

## 3. The 7 Agents

All 7 agents are implemented from day 1. Each phase is a separate `subtask()` call with its own tool set enforced by the engine. The manager agent orchestrates the pipeline.

### 3.1 Agent Overview

| # | Agent | Role | Input | Output | Tools Available |
|---|-------|------|-------|--------|----------------|
| 1 | **Scout** | Searches academic APIs for papers | Research topic/query | Papers stored in DB + embeddings | `search_*` (9 APIs), `embed_text`, `request_approval` |
| 2 | **Analyst (Triage)** | Ranks papers by relevance | All paper abstracts | Ranked paper list with scores | `read_paper`, `search_similar`, `embed_text`, `request_approval` |
| 2b | **Analyst (Deep Read)** | Extracts claims, gaps, contradictions | User-selected papers | Structured claims with quotes | `read_paper`, `fetch_fulltext`, `extract_claims`, `search_similar`, `request_approval` |
| 3 | **Verifier** | Checks claim credibility | Claims from Analyst | Confidence scores per claim | `check_retraction`, `get_citation_count`, `validate_doi`, `read_paper`, `request_approval` |
| 4 | **Hypothesis Generator** | Creates novel hypotheses from verified gaps | Verified claims + gaps | Ranked hypotheses with scores | `read_paper`, `search_similar`, `score_hypothesis`, `request_approval` |
| 5 | **Brancher** | Cross-domain search to prevent tunnel vision | Selected hypothesis | Branch map with adjacent findings | `search_*` (9 APIs), `search_similar`, `embed_text`, `request_approval` |
| 6 | **Critic** | Evaluates hypothesis quality | Hypothesis + branches | Dimensional scores + approve/reject | `read_paper`, `search_similar`, `request_approval` |
| 7 | **Writer** | Drafts research paper with citations | Approved hypothesis + all data | IMRaD paper + references | `read_paper`, `search_similar`, `write_section`, `get_citations`, `request_approval` |

### 3.2 Phase Flow

```
                    ┌─────────────────────────────────┐
                    │         MANAGER AGENT            │
                    │  (orchestrates the full pipeline) │
                    └──────────┬──────────────────────┘
                               │
         subtask("Scout phase: find papers on {topic}")
                               │
                    ┌──────────▼──────────┐
                    │       SCOUT         │──→ APPROVAL GATE
                    └──────────┬──────────┘
                               │
         subtask("Triage: rank {N} papers by relevance")
                               │
                    ┌──────────▼──────────┐
                    │  ANALYST (TRIAGE)   │──→ APPROVAL GATE (user picks papers)
                    └──────────┬──────────┘
                               │
         subtask("Deep read: extract claims from {M} papers")
                               │
                    ┌──────────▼──────────┐
                    │ ANALYST (DEEP READ) │──→ APPROVAL GATE
                    └──────────┬──────────┘
                               │
         subtask("Verify {K} claims")
                               │
                    ┌──────────▼──────────┐
                    │      VERIFIER       │──→ APPROVAL GATE
                    └──────────┬──────────┘
                               │
         subtask("Generate hypotheses from verified claims + gaps")
                               │
                    ┌──────────▼──────────┐
                    │  HYPOTHESIS GEN     │──→ APPROVAL GATE (user picks hypothesis)
                    └──────────┬──────────┘
                               │
         subtask("Branch search: 4 types for hypothesis {H}")
                               │
                    ┌──────────▼──────────┐
                    │      BRANCHER       │──→ APPROVAL GATE
                    └──────────┬──────────┘
                               │
         subtask("Critique hypothesis with full context")
                               │
                    ┌──────────▼──────────┐
                    │       CRITIC        │──→ APPROVAL GATE
                    └──────────┬──────────┘
                               │
              ┌────────────────┴────────────────┐
              │ If rejected (max 3 iterations):  │
              │ Loop back to Hypothesis Gen      │
              │ with feedback. After loop done,  │
              │ user approves final result.       │
              └────────────────┬────────────────┘
                               │
         subtask("Write outline") → APPROVAL → subtask("Write full draft")
                               │
                    ┌──────────▼──────────┐
                    │       WRITER        │──→ APPROVAL GATE → OUTPUT FILES
                    └─────────────────────┘
```

### 3.3 Paper Type Behavior

Paper type is set at session start. It determines which phases are active and how each agent behaves.

| Paper Type | Active Phases | Notes |
|-----------|---------------|-------|
| **Research Article** | All 7 (full pipeline) | IMRaD format. V1 target. |
| **Literature Review** | Scout → Analyst → Verifier → Writer | Skip hypothesis/brancher/critic. Thematic analysis. |
| **Systematic Review** | Scout → Analyst → Verifier → Writer | PRISMA-guided. Structured data extraction. |
| **Meta-Analysis** | Scout → Analyst → Verifier → Writer | Statistical data extraction. Forest plots. |
| **Position Paper** | Scout → Analyst → Verifier → Hypothesis → Critic → Writer | Skip brancher. Argument-focused. |
| **Case Study** | Scout → Analyst → Verifier → Writer | Narrower search. Case-relevant data. |

V1 builds all 7 agents. Tests with Research Article (full pipeline). Other paper types work by skipping phases — no additional code needed.

### 3.4 Per-Phase System Prompts

Each agent gets a phase-specific prompt. Structure:

```
BASE PROMPT (shared):
  - You are ARA, an academic research agent
  - Research principles, citation rules, quality standards
  - Available tools for this phase
  - Current session config (topic, paper type, citation style)

PHASE PROMPT (injected per subtask):
  - Phase-specific instructions
  - Paper-type-specific behavior modifications
  - Expected output format

RULES BLOCK (injected once at session start):
  - All active Rule Gate rules
  - Include/exclude/constraint/methodology rules
```

---

## 4. Tools (~20)

### 4.1 Search Tools (9 Academic APIs)

| Tool | API | Auth | Returns |
|------|-----|------|---------|
| `search_semantic_scholar` | Semantic Scholar Academic Graph API | Optional key (higher limits) | Papers with abstracts, citation counts, fields of study |
| `search_arxiv` | arXiv API | None | Preprints with abstracts, categories |
| `search_crossref` | CrossRef API | None | Papers with DOIs, metadata, references |
| `search_openalex` | OpenAlex API | Email (polite pool) | Papers with concepts, institutions, open access status |
| `search_pubmed` | NCBI E-utilities | Optional NCBI key | Biomedical papers with MeSH terms |
| `search_core` | CORE API | Optional key | Open access papers with full text links |
| `search_dblp` | DBLP API | None | CS papers with venue, author info |
| `search_europe_pmc` | Europe PMC API | None | Biomedical/life science papers |
| `search_base` | BASE (Bielefeld) API | None | Broad academic content, repositories |

All search tools return a standardized paper object: `{ title, abstract, authors, year, doi, source, url, citation_count }`.

### 4.2 Paper Tools

| Tool | Purpose | Details |
|------|---------|---------|
| `fetch_fulltext` | Get full paper text via Unpaywall | Input: DOI. Returns: full text or "not available". Caches PDF to `.ara/papers/` |
| `read_paper` | Read a paper from local DB | Input: paper_id. Returns: metadata + abstract + full text (if cached) |
| `search_similar` | Vector similarity search | Input: text or embedding. Returns: top-K similar papers from session DB |
| `check_retraction` | Check retraction status | Input: DOI. Calls CrossRef API. Returns: retracted/not retracted + date |
| `get_citation_count` | Get citation count | Input: DOI. Calls Semantic Scholar. Returns: citation count + influential citations |
| `validate_doi` | Validate DOI resolves | Input: DOI. Calls doi.org. Returns: valid/invalid + resolved URL |

### 4.3 Research Tools

| Tool | Purpose | Details |
|------|---------|---------|
| `extract_claims` | Extract structured claims from paper | LLM-powered. Returns: `[{ text, confidence, claim_type, supporting_quotes, section }]` |
| `score_hypothesis` | Score hypothesis on multiple dimensions | LLM-powered. Returns: scores on novelty, feasibility, evidence, methodology_fit, impact, reproducibility + custom dimensions |
| `branch_search` | Cross-domain search for a hypothesis | Input: hypothesis + branch_type (lateral/methodological/analogical/convergent). Searches APIs in adjacent fields. |
| `write_section` | Write one section of the paper | Input: section name + context. Returns: formatted markdown with citations |
| `get_citations` | Get all citations for the session | Queries DB. Returns: BibTeX entries for all referenced papers |

### 4.4 Pipeline Tools

| Tool | Purpose | Details |
|------|---------|---------|
| `request_approval` | Pause for human approval | Writes gate data to `.ara/gates/{phase}.md`. Shows rich TUI summary. Blocks until user approves/rejects/edits. Returns user's decision + comments. |
| `get_rules` | Get active Rule Gate rules | Queries session config. Returns: all include/exclude/constraint/methodology rules |
| `track_cost` | Track LLM token usage | Input: tokens, model. Updates session budget tracking in DB. |
| `embed_text` | Generate embedding vector | Input: text. Returns: float[768] vector. Uses Gemini API (online) or Ollama (offline). |

---

## 5. Approval Gate System

### 5.1 Overview

Every phase pauses for user approval before the next phase begins. The `request_approval` tool blocks the engine, shows results in the TUI, writes full data to a reviewable file, and waits for user input.

### 5.2 Gate Behavior

| After Phase | TUI Shows | File Written | User Actions |
|-------------|-----------|-------------|--------------|
| Scout | Paper count, source breakdown, sample titles | `gates/scout_results.md` | Approve / Reject / Edit (modify search) / Revert |
| Analyst Triage | Ranked paper table with relevance scores | `gates/triage_ranking.md` | Pick papers for deep read, Approve / Reject |
| Analyst Deep Read | Extracted claims table with sources | `gates/claims_extracted.md` | Delete/edit/add claims, Approve / Reject |
| Verifier | Verification summary (N verified, M contradicted) | `gates/verification_results.md` | Approve / Reject / Revert |
| Hypothesis Gen | 20 ranked hypotheses with dimension scores | `gates/hypotheses.md` | Pick hypothesis to pursue, Approve / Reject |
| Brancher | Branch map per type with findings | `gates/branch_map.md` | Approve / Reject / Revert |
| Critic | Dimensional scores + recommendation | `gates/critique.md` | Approve / Reject / Revert |
| Writer (outline) | Paper outline with section summaries | `gates/outline.md` | Approve / Edit outline / Reject |
| Writer (draft) | Full paper preview | `gates/draft.md` | Approve (final) / Reject / Revert |

### 5.3 Gate Actions

| Action | Behavior |
|--------|----------|
| **Approve** | Continue to next phase |
| **Reject** | Stop pipeline. User provides reason. |
| **Edit** | User modifies results before continuing (e.g., remove claims, change search terms) |
| **Revert** | Go back one step. Old data preserved. User provides feedback for re-run. |

### 5.4 TUI Approval Flow

```
╭─── Scout Results ──────────────────────────────────────────╮
│ Found 147 papers from 7 sources                            │
│                                                            │
│ Source Breakdown:                                          │
│   Semantic Scholar: 42  arXiv: 38  OpenAlex: 31            │
│   PubMed: 18  CrossRef: 12  CORE: 4  DBLP: 2              │
│                                                            │
│ Sample Papers:                                             │
│  1. "Transformer architectures for DNA seq..." (2024, 89↑) │
│  2. "Attention mechanisms in protein fold..." (2023, 156↑)  │
│  3. "BERT-based genomic variant classif..." (2023, 67↑)     │
│  ...                                                       │
│                                                            │
│ Full results: .ara/gates/scout_results.md                  │
╰────────────────────────────────────────────────────────────╯
  [a] Approve  [r] Reject  [e] Edit  [v] Revert
  > _
```

### 5.5 Critic Rejection Loop

When the Critic rejects a hypothesis:
1. Manager receives rejection with feedback
2. Manager calls Hypothesis Generator subtask again with: original claims + gaps + Critic feedback
3. New hypothesis goes through Brancher → Critic again
4. Max 3 iterations. After loop completes, user sees final result and approves/rejects.
5. If all 3 rejected, Writer produces a "negative result" report.

---

## 6. Rule Gate

### 6.1 Overview

Rules are natural language directives that shape agent behavior. Set at session start, injected into every agent's system prompt.

### 6.2 Rule Types

| Type | Example | Effect |
|------|---------|--------|
| **Include** | "Focus on Swedish fintech companies" | Scout prioritizes relevant queries, Analyst weights accordingly |
| **Exclude** | "Do not use papers from predatory journals" | Scout filters, Verifier flags |
| **Constraint** | "Only papers published after 2020" | Scout filters by date |
| **Methodology** | "Prefer quantitative over qualitative studies" | Analyst and Verifier weight accordingly |

### 6.3 Enforcement

Rules are loaded once at session start and baked into the system prompt. Mid-session rule changes (if implemented later) take effect on the next phase.

```
[RULES]
- INCLUDE: Focus on transformer architectures specifically (not RNNs/LSTMs)
- EXCLUDE: Ignore papers with fewer than 10 citations
- CONSTRAINT: Only papers from 2020 onwards
- METHODOLOGY: Prefer empirical studies with benchmarks over theoretical papers
[/RULES]
```

---

## 7. Data Model (SQLite + sqlite-vec)

### 7.1 Schema Overview

```sql
-- Core session
CREATE TABLE sessions (
  session_id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic TEXT NOT NULL,
  paper_type TEXT NOT NULL DEFAULT 'research_article',
  citation_style TEXT NOT NULL DEFAULT 'apa7',
  budget_cap REAL NOT NULL DEFAULT 5.0,
  budget_spent REAL NOT NULL DEFAULT 0.0,
  deep_read_limit INTEGER NOT NULL DEFAULT 100,
  enabled_sources TEXT NOT NULL DEFAULT '[]',  -- JSON array
  status TEXT NOT NULL DEFAULT 'active',
  current_phase TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Papers discovered by Scout
CREATE TABLE papers (
  paper_id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(session_id),
  title TEXT NOT NULL,
  abstract TEXT,
  authors TEXT,           -- JSON array
  year INTEGER,
  doi TEXT,
  source TEXT NOT NULL,   -- which API found it
  url TEXT,
  citation_count INTEGER DEFAULT 0,
  relevance_score REAL,   -- set by Triage
  selected_for_deep_read INTEGER DEFAULT 0,
  full_text TEXT,         -- cached full text
  full_text_path TEXT,    -- path to cached PDF
  embedding FLOAT[768],  -- sqlite-vec
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Claims extracted by Analyst
CREATE TABLE claims (
  claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(session_id),
  paper_id INTEGER NOT NULL REFERENCES papers(paper_id),
  claim_text TEXT NOT NULL,
  claim_type TEXT NOT NULL DEFAULT 'finding',  -- finding, method, limitation, gap
  confidence REAL DEFAULT 0.5,
  supporting_quotes TEXT,    -- JSON array of exact quotes
  section TEXT,              -- which paper section
  -- Verification fields (set by Verifier)
  verification_status TEXT,  -- verified, contradicted, inconclusive, retracted
  retraction_checked INTEGER DEFAULT 0,
  citation_count_at_check INTEGER,
  doi_valid INTEGER,
  verifier_notes TEXT,
  -- Contradiction links
  contradicts TEXT,          -- JSON array of claim_ids
  embedding FLOAT[768],
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Hypotheses generated
CREATE TABLE hypotheses (
  hypothesis_id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(session_id),
  hypothesis_text TEXT NOT NULL,
  rank INTEGER,
  iteration INTEGER NOT NULL DEFAULT 1,
  -- Scores (set by Hypothesis Generator and Critic)
  novelty REAL,
  feasibility REAL,
  evidence_strength REAL,
  methodology_fit REAL,
  impact REAL,
  reproducibility REAL,
  custom_dimensions TEXT,    -- JSON: [{ name, score, rationale }]
  overall_score REAL,
  -- Critic result
  critic_decision TEXT,      -- approved, rejected
  critic_feedback TEXT,
  selected INTEGER DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Branch map from Brancher
CREATE TABLE branches (
  branch_id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(session_id),
  hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(hypothesis_id),
  branch_type TEXT NOT NULL,  -- lateral, methodological, analogical, convergent
  finding_text TEXT NOT NULL,
  source_paper_id INTEGER REFERENCES papers(paper_id),
  confidence REAL,
  domain TEXT,                -- the adjacent domain explored
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Approval gate history
CREATE TABLE approval_gates (
  gate_id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(session_id),
  phase TEXT NOT NULL,
  gate_data TEXT,             -- JSON: full results shown to user
  action TEXT,                -- approve, reject, edit, revert
  user_comments TEXT,
  edited_data TEXT,           -- JSON: user's modifications
  resolved_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Rule gate
CREATE TABLE rules (
  rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(session_id),
  rule_text TEXT NOT NULL,
  rule_type TEXT NOT NULL DEFAULT 'exclude',  -- include, exclude, constraint, methodology
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Event log (for session replay)
CREATE TABLE events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(session_id),
  event_type TEXT NOT NULL,
  phase TEXT,
  payload TEXT,               -- JSON
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Budget tracking
CREATE TABLE cost_log (
  cost_id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(session_id),
  phase TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0.0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 7.2 Vector Search

sqlite-vec enables vector similarity search on the `papers.embedding` and `claims.embedding` columns:

```sql
-- Find papers similar to a query embedding
SELECT paper_id, title, abstract,
       vec_distance_cosine(embedding, ?) AS distance
FROM papers
WHERE session_id = ?
ORDER BY distance ASC
LIMIT 20;
```

---

## 8. Session Lifecycle

### 8.1 Starting a Session

```
$ cd ~/research/my-topic
$ ara

ARA v0.1.0 | Provider: ollama (llama3.2) | Embeddings: gemini

No existing session found in this directory.
What would you like to research?

> The impact of transformer architectures on genomic sequence analysis,
  focus on papers after 2022, use APA7 format

ARA: Got it. Let me confirm a few things:
  - Paper type: Research Article (IMRaD format)
  - Sources: All 9 academic APIs
  - Deep-read limit: Top 100 papers
  - Budget cap: $5.00
  - Citation style: APA 7th

  Shall I adjust any of these? (or just press Enter to start)

> budget 2, depth 50

ARA: Updated — $2.00 budget, top 50 papers. Starting research...
```

### 8.2 Resuming a Session

```
$ cd ~/research/my-topic
$ ara

ARA v0.1.0 | Provider: ollama (llama3.2) | Embeddings: gemini

Resuming session: "The impact of transformer architectures on genomic..."
  Status: Waiting for approval (Analyst Triage)
  Papers found: 147 | Budget: $0.32 / $2.00

  Last gate: Triage ranking ready for review.
  See: .ara/gates/triage_ranking.md

  [a] Approve  [r] Reject  [e] Edit  [v] Revert
  > _
```

### 8.3 Session Output

When the Writer is approved, ARA generates:

```
.ara/output/
  paper.md          ← full IMRaD paper in markdown
  paper.html        ← rendered HTML version
  index.html        ← self-contained preview (embedded CSS, no dependencies)
  references.bib    ← BibTeX file with all citations
.ara/papers/        ← all cached PDFs referenced in the paper
.ara/logs/
  session.jsonl     ← full replay log
  events.jsonl      ← event trace
```

---

## 9. Configuration

### 9.1 Global Config (`~/.ara/config.yaml`)

```yaml
# LLM Provider
provider: ollama              # ollama, openai, anthropic, openrouter
model: llama3.2               # model name
reasoning_effort: high         # low, medium, high

# API Keys (or set via environment variables)
# openai_api_key: sk-...
# anthropic_api_key: sk-ant-...
# openrouter_api_key: sk-or-...
# semantic_scholar_api_key: ...
# ncbi_api_key: ...

# Embeddings
embedding_provider: gemini     # gemini, ollama
embedding_model: text-embedding-004

# Defaults
default_paper_type: research_article
default_citation_style: apa7
default_budget_cap: 5.0
default_deep_read_limit: 100
default_sources:
  - semantic_scholar
  - arxiv
  - crossref
  - openalex
  - pubmed
  - core
  - dblp
  - europe_pmc
  - base
```

### 9.2 Per-Project Config (`.ara/config.yaml`)

Overrides global config for this research folder:

```yaml
provider: anthropic
model: claude-sonnet-4-5-20250929
budget_cap: 10.0
```

### 9.3 Environment Variables

All config values can be set via `ARA_` prefixed env vars:

```bash
export ARA_PROVIDER=anthropic
export ARA_ANTHROPIC_API_KEY=sk-ant-...
export ARA_EMBEDDING_PROVIDER=ollama
```

Priority: CLI flags > env vars > per-project config > global config.

---

## 10. Error Handling

Every error is shown to the user immediately. User decides:

| Error Type | Example | User Options |
|-----------|---------|-------------|
| API timeout | Semantic Scholar not responding | Retry / Skip source / Abort |
| Rate limit | arXiv rate limit hit | Wait and retry / Skip source / Abort |
| LLM error | Model returned garbage | Retry / Abort |
| Parse failure | Couldn't extract claims from paper | Retry / Skip paper / Abort |
| No results | Scout found 0 papers | Edit search / Abort |
| Budget exceeded | Cost hit cap | Increase budget / Finish with current data / Abort |

---

## 11. Budget Tracking

| Budget State | Trigger | Behavior |
|-------------|---------|----------|
| Normal (< 80%) | — | Pipeline runs normally |
| Warning (80-99%) | Shown in TUI | Yellow warning, pipeline continues |
| Exceeded (>=100%) | Current phase finishes | Pipeline pauses at next approval gate. User can increase budget or stop. |

Cost is tracked per LLM call in the `cost_log` table. The `track_cost` tool is called after every model invocation.

---

## 12. Claim Structure

Each claim extracted by the Analyst has:

```json
{
  "claim_text": "Transformer models achieve 94% accuracy on splice site prediction",
  "claim_type": "finding",
  "confidence": 0.85,
  "supporting_quotes": [
    "Our model achieves 94.2% accuracy on the Splice2 benchmark (Table 3)",
    "This represents a 7% improvement over the previous LSTM baseline"
  ],
  "section": "results",
  "source_paper_id": 42,
  "contradicts": [17, 23]
}
```

Claim types: `finding` | `method` | `limitation` | `gap`

---

## 13. Hypothesis Scoring

Each hypothesis is scored on 6 base dimensions + LLM-suggested custom dimensions:

| Dimension | What it measures |
|-----------|-----------------|
| Novelty | How new is this idea relative to existing literature? |
| Feasibility | Can this be tested with available methods/data? |
| Evidence strength | How well do verified claims support this? |
| Methodology fit | Does a clear methodology exist to test this? |
| Impact | If true, how significant would this be? |
| Reproducibility | Could another researcher replicate a test of this? |
| *Custom* | LLM may add domain-specific dimensions (e.g., "clinical applicability") |

Scores are 0.0-1.0. Overall score = weighted average. User can see all dimensions in the approval gate.

---

## 14. Writer Process

The Writer operates in two passes:

**Pass 1 — Outline:**
1. Writer generates paper outline (title, abstract, section headings with brief summaries)
2. Approval gate: user reviews outline, can edit structure
3. On approve → proceed to full draft

**Pass 2 — Full Draft:**
1. Writer generates full IMRaD paper section by section using `write_section` tool
2. Each section properly cites papers from DB via `get_citations`
3. Approval gate: user reviews complete paper
4. On approve → generate output files (markdown, HTML, index.html, BibTeX)

---

## 15. Brancher Types

The Brancher runs 4 parallel searches per hypothesis:

| Type | What it does | Example |
|------|-------------|---------|
| **Lateral** | Searches adjacent fields for similar problems | Genomics transformer → NLP transformer techniques that might transfer |
| **Methodological** | Finds alternative methods used for similar problems | Attention mechanism alternatives, ensemble approaches |
| **Analogical** | Finds analogous problems in different domains | Protein folding ↔ language parsing structural similarities |
| **Convergent** | Finds independent research reaching similar conclusions | Multiple groups finding transformer superiority in different biological tasks |

---

## 16. Project Structure (Code)

```
ara/
  __init__.py
  __main__.py              ← CLI entry point (`ara` command)
  engine.py                ← OpenPlanter recursive engine (kept ~80%)
  model.py                 ← Provider-agnostic LLM abstraction (kept ~95%)
  config.py                ← ARA config dataclass
  builder.py               ← Engine/model factory
  runtime.py               ← Session persistence and lifecycle
  prompts/
    base.py                ← Shared research agent base prompt
    scout.py               ← Scout phase prompt
    analyst.py             ← Analyst (triage + deep read) prompts
    verifier.py            ← Verifier phase prompt
    hypothesis.py          ← Hypothesis generator prompt
    brancher.py            ← Brancher phase prompt
    critic.py              ← Critic phase prompt
    writer.py              ← Writer phase prompt
  tools/
    __init__.py            ← Tool registry + definitions
    search.py              ← 9 academic API search tools
    papers.py              ← fetch_fulltext, read_paper, search_similar
    verification.py        ← check_retraction, get_citation_count, validate_doi
    research.py            ← extract_claims, score_hypothesis, branch_search
    writing.py             ← write_section, get_citations
    pipeline.py            ← request_approval, get_rules, track_cost, embed_text
  tui.py                   ← Rich TUI (forked from OpenPlanter, restyled)
  gates.py                 ← Approval gate rendering + file writing
  db.py                    ← SQLite + sqlite-vec setup and queries
  credentials.py           ← API key management
  settings.py              ← Config file loading (global + per-project)
tests/
  ...
pyproject.toml
```

---

## 17. Dependencies

| Package | Purpose |
|---------|---------|
| `rich` | TUI framework (tables, progress, panels) |
| `prompt_toolkit` | REPL input handling |
| `sqlite-vec` | Vector search in SQLite |
| `httpx` | HTTP client for API calls |
| `pyyaml` | Config file parsing |
| `pyfiglet` | ASCII art banner |

6 runtime dependencies. No heavy ML frameworks (embeddings via API or Ollama HTTP).

---

## 18. Implementation Phases

### Phase 1: Core Pipeline
1. Copy OpenPlanter engine into fresh `ara/` package
2. Implement SQLite + sqlite-vec database layer (`db.py`)
3. Implement config system (global + per-project)
4. Build tool framework with tool registry
5. Implement all 9 search tools
6. Implement paper tools (fetch_fulltext, read_paper, search_similar)
7. Implement verification tools
8. Implement research tools (extract_claims, score_hypothesis, branch_search)
9. Implement writing tools (write_section, get_citations)
10. Implement pipeline tools (request_approval, get_rules, track_cost, embed_text)
11. Write all 7 agent phase prompts
12. Implement approval gate TUI + file writing
13. Implement manager agent prompt (orchestrates all phases)
14. Build TUI (fork + restyle OpenPlanter's)
15. Build CLI entry point (`ara` command)
16. End-to-end test: Research Article on a real topic

### Phase 2: Polish & Output
1. HTML output generation (paper.html + index.html)
2. BibTeX generation
3. Session history / replay
4. Additional paper types (Literature Review, etc.)
5. Offline embedding fallback (Ollama)
6. `ara config` command for interactive setup

### Phase 3: Scale
1. Multiple concurrent research tasks within a session
2. Advanced TUI (scrollable tables, search within results)
3. Export improvements (LaTeX, PDF via pandoc)
4. Performance optimization (batch embeddings, caching)
