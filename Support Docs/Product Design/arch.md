# ARA Code Architecture (v2)

> **Location:** `Support Docs/Product Design/arch.md`
> **Purpose:** Code organization guide for ARA CLI research agent
> **Based on:** OpenPlanter engine (copied, not forked)
> **Previous:** v1 archived at `Support Docs/Product Design/archive-v1/arch-v1.md`

---

## 1. Constraints

| Rule | Limit | Notes |
|------|-------|-------|
| Function body | 15 lines max | Same as v1 |
| File length | 150 lines max | Relaxed from 100 (Python is more verbose than TS) |
| Duplication | Zero tolerance | Every pattern defined once |
| Magic strings | None | All strings in config or constants |
| Exception: OpenPlanter engine files | May exceed limits | engine.py, model.py are upstream code — modify minimally |

---

## 2. Project Structure

```
ara/
  __init__.py                  ← Package init, version
  __main__.py                  ← CLI entry point: `python -m ara` or `ara`
  engine.py                    ← Recursive LLM engine (from OpenPlanter, ~80% kept)
  model.py                     ← Provider-agnostic LLM abstraction (from OpenPlanter, ~95% kept)
  config.py                    ← ARA config dataclass + loading
  builder.py                   ← Engine/model factory (from OpenPlanter, adapted)
  runtime.py                   ← Session lifecycle (from OpenPlanter, adapted)
  db.py                        ← SQLite + sqlite-vec setup, queries, migrations
  tui.py                       ← Rich TUI (from OpenPlanter, restyled for ARA)
  gates.py                     ← Approval gate TUI rendering + file writing
  credentials.py               ← API key management (from OpenPlanter, adapted)
  settings.py                  ← Config file loading: global + per-project
  patching.py                  ← File patching utilities (from OpenPlanter, if needed)
  replay_log.py                ← Session replay logger (from OpenPlanter)
  prompts/
    __init__.py                ← Prompt registry
    base.py                    ← Shared base prompt (research principles, quality rules)
    scout.py                   ← Scout phase prompt
    analyst.py                 ← Analyst triage + deep read prompts
    verifier.py                ← Verifier phase prompt
    hypothesis.py              ← Hypothesis generator prompt
    brancher.py                ← Brancher phase prompt
    critic.py                  ← Critic phase prompt
    writer.py                  ← Writer phase prompt
    manager.py                 ← Manager orchestration prompt
  tools/
    __init__.py                ← Tool registry: ALL tool definitions + dispatch
    defs.py                    ← Tool JSON schemas (like OpenPlanter's tool_defs.py)
    search.py                  ← 9 academic API search implementations
    papers.py                  ← fetch_fulltext, read_paper, search_similar
    verification.py            ← check_retraction, get_citation_count, validate_doi
    research.py                ← extract_claims, score_hypothesis, branch_search
    writing.py                 ← write_section, get_citations
    pipeline.py                ← request_approval, get_rules, track_cost, embed_text
tests/
  test_engine.py
  test_tools.py
  test_db.py
  test_prompts.py
  test_gates.py
  test_integration.py
pyproject.toml                 ← Package config, entry point: ara = ara.__main__:main
```

### File Count: ~25 source files + ~6 test files = ~31 files

---

## 3. What Comes From OpenPlanter (Copied)

| File | OpenPlanter Source | Changes for ARA |
|------|-------------------|-----------------|
| `engine.py` | `agent/engine.py` | Replace tool dispatch with ARA tool registry. Keep recursive loop, context condensation, budget management, parallel subtask execution. |
| `model.py` | `agent/model.py` | Minimal changes. Keep all providers (OpenAI, Anthropic, OpenRouter, Ollama). |
| `builder.py` | `agent/builder.py` | Adapt to ARA config. |
| `runtime.py` | `agent/runtime.py` | Replace file-based persistence with SQLite session management. |
| `tui.py` | `agent/tui.py` | Restyle branding. Add approval gate display methods. |
| `credentials.py` | `agent/credentials.py` | Add ARA-specific API keys (Semantic Scholar, NCBI, etc.). |
| `settings.py` | `agent/settings.py` | Adapt for `~/.ara/config.yaml` structure. |
| `patching.py` | `agent/patching.py` | Keep as-is (may be useful for file operations). |
| `replay_log.py` | `agent/replay_log.py` | Keep as-is for session replay. |

### What We DON'T Copy

- `agent/tools.py` — Investigation tools (file I/O, shell, web search). Replaced entirely by ARA tools.
- `agent/tool_defs.py` — Tool schemas. Replaced by `tools/defs.py` with ARA tool schemas.
- `agent/prompts.py` — OSINT investigation prompts. Replaced by `prompts/` directory.
- `agent/demo.py` — Demo mode censoring. Not needed.
- `wiki/` — Data source wiki. Not needed.
- `openplanter-desktop/` — Tauri desktop app. Not needed (CLI only for now).

---

## 4. Tool Architecture

### 4.1 Tool Registry Pattern

All tools are defined in `tools/defs.py` (JSON schemas) and dispatched from `tools/__init__.py`:

```python
# tools/__init__.py
from .search import search_semantic_scholar, search_arxiv, ...
from .papers import fetch_fulltext, read_paper, search_similar
from .verification import check_retraction, get_citation_count, validate_doi
from .research import extract_claims, score_hypothesis, branch_search
from .writing import write_section, get_citations
from .pipeline import request_approval, get_rules, track_cost, embed_text

TOOL_DISPATCH = {
    "search_semantic_scholar": search_semantic_scholar,
    "search_arxiv": search_arxiv,
    # ... all ~20 tools
}
```

### 4.2 Phase Tool Sets

Each phase subtask gets a restricted tool set. The engine enforces this by passing different tool definitions per subtask.

```python
PHASE_TOOLS = {
    "scout": ["search_*", "embed_text", "request_approval", "track_cost"],
    "analyst_triage": ["read_paper", "search_similar", "embed_text", "request_approval", "track_cost"],
    "analyst_deep_read": ["read_paper", "fetch_fulltext", "extract_claims", "search_similar", "request_approval", "track_cost"],
    "verifier": ["check_retraction", "get_citation_count", "validate_doi", "read_paper", "request_approval", "track_cost"],
    "hypothesis": ["read_paper", "search_similar", "score_hypothesis", "request_approval", "track_cost"],
    "brancher": ["search_*", "search_similar", "embed_text", "request_approval", "track_cost"],
    "critic": ["read_paper", "search_similar", "request_approval", "track_cost"],
    "writer": ["read_paper", "search_similar", "write_section", "get_citations", "request_approval", "track_cost"],
}
```

### 4.3 Standardized Paper Object

All search tools return the same structure:

```python
@dataclass
class PaperResult:
    title: str
    abstract: str | None
    authors: list[str]
    year: int | None
    doi: str | None
    source: str           # "semantic_scholar", "arxiv", etc.
    url: str | None
    citation_count: int
    metadata: dict        # source-specific extra fields
```

---

## 5. Engine Integration

### 5.1 How ARA Uses the Engine

```python
# Simplified: how ara launches a research session
engine = RLMEngine(
    model=build_model(config),
    tools=ARATools(workspace=project_dir, db=session_db),
    config=agent_config,
    system_prompt=build_manager_prompt(session),
)

result = engine.solve(
    objective=f"Research: {session.topic}",
    on_event=tui.on_event,
    on_step=tui.on_step,
)
```

### 5.2 Manager Agent Flow

The manager agent receives the research topic and orchestrates all phases via `subtask()` calls. Each subtask gets a phase-specific prompt and tool set.

The manager prompt instructs it to:
1. Call phases in order: Scout → Triage → Deep Read → Verifier → Hypothesis → Brancher → Critic → Writer
2. Use `request_approval` between each phase
3. Handle Critic rejection loop (max 3 iterations)
4. Track budget throughout
5. Stop if budget exceeded

### 5.3 Approval Gate Integration

The `request_approval` tool is a blocking call:

```python
def request_approval(phase: str, data: dict, summary: str) -> str:
    # 1. Write full gate data to .ara/gates/{phase}.md
    # 2. Store gate in SQLite (approval_gates table)
    # 3. Render summary in TUI (Rich panel)
    # 4. Block on user input (prompt_toolkit)
    # 5. Record user decision in DB
    # 6. Return: "approved" / "rejected: {reason}" / "edited: {changes}" / "reverted: {feedback}"
```

The engine thread blocks until the user responds. This is simple because ARA is single-user, single-session, synchronous.

---

## 6. Data Flow

```
User types topic
    │
    ▼
Manager Agent (engine.py recursive loop)
    │
    ├── subtask("Scout: find papers on {topic}")
    │     ├── search_semantic_scholar(query)  → papers table
    │     ├── search_arxiv(query)             → papers table
    │     ├── ... (9 APIs)
    │     ├── embed_text(abstract)            → papers.embedding
    │     └── request_approval("scout", results)
    │           └── TUI: show results, wait for user
    │
    ├── subtask("Triage: rank {N} papers")
    │     ├── read_paper(id) × N
    │     ├── search_similar(topic_embedding)
    │     └── request_approval("triage", ranking)
    │           └── User picks papers for deep read
    │
    ├── subtask("Deep read: extract claims from {M} papers")
    │     ├── fetch_fulltext(doi) × M
    │     ├── extract_claims(paper_id) × M    → claims table
    │     └── request_approval("analyst", claims)
    │
    ├── subtask("Verify {K} claims")
    │     ├── check_retraction(doi) × K
    │     ├── get_citation_count(doi) × K
    │     ├── validate_doi(doi) × K           → claims.verification_status
    │     └── request_approval("verifier", results)
    │
    ├── subtask("Generate hypotheses")
    │     ├── score_hypothesis() × 20         → hypotheses table
    │     └── request_approval("hypothesis", ranked_list)
    │           └── User picks hypothesis
    │
    ├── subtask("Branch search for hypothesis {H}")
    │     ├── branch_search(H, "lateral")     → branches table
    │     ├── branch_search(H, "methodological")
    │     ├── branch_search(H, "analogical")
    │     ├── branch_search(H, "convergent")
    │     └── request_approval("brancher", branch_map)
    │
    ├── subtask("Critique hypothesis")
    │     ├── [may loop back to hypothesis gen, max 3x]
    │     └── request_approval("critic", scores)
    │
    └── subtask("Write paper")
          ├── write_section("outline")
          ├── request_approval("writer_outline", outline)
          ├── write_section("abstract") → write_section("intro") → ...
          ├── get_citations()            → references.bib
          └── request_approval("writer_draft", full_paper)
                └── Generate output files
```

---

## 7. Dependencies

| Package | Purpose | From OpenPlanter? |
|---------|---------|-------------------|
| `rich` | TUI framework | Yes |
| `prompt_toolkit` | REPL input | Yes |
| `pyfiglet` | ASCII banner | Yes |
| `sqlite-vec` | Vector search | New |
| `httpx` | HTTP client | New (replaces urllib) |
| `pyyaml` | Config parsing | New |

**6 runtime dependencies.** No heavy ML frameworks.
