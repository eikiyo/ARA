# ARA - Current Phase

## Phase 1: Core Pipeline — COMPLETE (v0.2.0 rebuild)

Full rebuild from scratch. 49 tests passing.

### What's built:
- Native Gemini SDK (google-genai) — no OpenAI compatibility layer
- Recursive LLM engine with subtask delegation, depth control
- 9 academic search APIs (Semantic Scholar, arXiv, CrossRef, OpenAlex, PubMed, CORE, DBLP, Europe PMC, BASE)
- Auto-storage of search results into SQLite with DOI/title dedup
- Paper tools (fetch fulltext, read, similarity search)
- Verification tools (retraction check, citation count, DOI validation)
- Research tools (claim extraction, hypothesis scoring, branch search)
- Writing tools (section writer, BibTeX citations)
- Pipeline tools (approval gates, rules, cost tracking, Gemini embeddings)
- 8-phase manager orchestration (Scout → Triage → Deep Read → Verifier → Hypothesis → Brancher → Critic → Writer)
- Rich TUI with activity display, slash commands
- CLI with provider auto-detection, session resume, configurable gates
- Multi-provider support (Google Gemini, OpenAI, Anthropic, OpenRouter, Ollama)
- Per-provider model persistence across sessions
- API keys loaded from env vars or ~/.ara/credentials.json (no hardcoded keys)

---

## Phase 2: Output & Polish — NOT STARTED

| Task | Status |
|------|--------|
| HTML output (paper.md + index.html preview) | Not started |
| BibTeX generation from session DB | Not started |
| Gemini embeddings (text-embedding-004) | Not started |
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
