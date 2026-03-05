# ARA - Current Phase

## Phase 1: Core Pipeline — COMPLETE

All 14 tasks from the original plan are implemented and tested (67 tests passing).

### What's built:
- Recursive LLM engine with subtask delegation, model routing, depth control
- 9 academic search APIs (Semantic Scholar, arXiv, CrossRef, OpenAlex, PubMed, CORE, DBLP, Europe PMC, BASE)
- Auto-storage of search results into SQLite with DOI/title dedup
- Paper tools (fetch fulltext, read, similarity search)
- Verification tools (retraction check, citation count, DOI validation)
- Research tools (claim extraction, hypothesis scoring, branch search)
- Writing tools (section writer, BibTeX citations, paper compiler)
- Pipeline tools (approval gates, rules, cost tracking, embeddings via Ollama)
- 8-phase manager orchestration (Scout → Triage → Deep Read → Verifier → Hypothesis → Brancher → Critic → Writer)
- Rich TUI with phase progress bar, approval gate UI, working indicator
- CLI with provider auto-detection, session resume, configurable gates
- Multi-provider support (OpenAI, Anthropic, OpenRouter, Ollama)
- Per-provider model persistence across sessions

### API Keys (hardcoded for dev):
- Semantic Scholar: 1 req/sec rate limiter (thread-safe)
- CORE: Authenticated access

---

## Phase 2: Output & Polish — IN PROGRESS

| Task | Status |
|------|--------|
| HTML output (paper.md + index.html preview) | Done |
| BibTeX generation from session DB | Done |
| Ollama embeddings (nomic-embed-text) | Done |
| Session history / replay from logs | Not started |
| Additional paper types (Lit Review, Systematic Review) | Not started |
| `ara config` interactive setup command | Not started |

## Phase 3: Scale & Extras

| Task | Status |
|------|--------|
| Multiple concurrent subtasks within phases | Not started |
| Advanced TUI (scrollable tables, search) | Not started |
| LaTeX + PDF export (via pandoc) | Not started |
| Performance optimization (batch embeddings, caching) | Not started |
| Web version (FastAPI + Next.js + Supabase) | Not started |
