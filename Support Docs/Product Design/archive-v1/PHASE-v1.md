# ARA - Current Phase

## Phase: Design Complete → Ready for Phase 1 Implementation

## Phase 1: Full Pipeline + Approval UI

### Infrastructure:

1. **Supabase (Postgres + pgvector + Realtime)**
   - Definition of done: Supabase project created, schema migrated (ara_schema.sql with pgvector extension), Realtime enabled on events table, connection tested from both n8n and Cloudflare Worker.

2. **n8n on Railway**
   - Definition of done: n8n community edition running on Railway (regular mode, no Redis). Manager workflow polling task_queue only while a session is active. All 9 sub-workflows deployed (Manager + 7 agents + shared-utils).

3. **Next.js on Cloudflare Worker**
   - Definition of done: Next.js app deployed as Cloudflare Worker. PIN auth via CF secret. Supabase JS client connected. Supabase Realtime subscriptions working.

### Agent Pipeline (all 7 agents):

4. **Scout**
   - Definition of done: Receives topic → expands to search queries via OpenRouter LLM → searches free academic APIs (arXiv, CrossRef, DBLP, Europe PMC, Semantic Scholar, OpenAlex, PubMed, CORE, BASE) → stores papers in Postgres + embeds abstracts in pgvector via Gemini → deduplication (DOI + title fuzzy). Abstract-only (no GROBID full-text).

5. **Analyst (Triage + Deep Read)**
   - Definition of done: Triage reads all abstracts, ranks by relevance → approval gate → Deep Read analyzes user-selected papers via RAG (pgvector) → extracts claims, gaps, contradictions → approval gate.

6. **Verifier**
   - Definition of done: Checks each claim — retraction status (CrossRef), citation count (Semantic Scholar), DOI validation → updates claim verification status → approval gate.

7. **Hypothesis Generator**
   - Definition of done: Takes verified claims + gaps → generates 20 ranked hypotheses with multi-dimensional scores via OpenRouter LLM → approval gate.

8. **Brancher**
   - Definition of done: Takes selected hypothesis → 4 parallel branch types (lateral, methodological, analogical, convergent) → searches free APIs for cross-domain connections → approval gate.

9. **Critic**
   - Definition of done: Scores hypothesis across 8 dimensions → approve/reject with feedback → if rejected, loops back to Hypothesis Generator (max 3 iterations) → approval gate.

10. **Writer**
    - Definition of done: Takes approved hypothesis + verified claims + branches → drafts Research Article (IMRaD format) via OpenRouter LLM → approval gate → session marked complete.

### Approval UI:

11. **Session Config Form**
    - Definition of done: Topic input, citation style dropdown, budget cap slider, deep-read limit slider, source multi-select (free sources functional, paid sources grayed out), paper type dropdown (Research Article functional, rest grayed out), initial rules input.

12. **Live Dashboard**
    - Definition of done: Phase stepper, live event feed (via Supabase Realtime on events table), budget meter, error panel with Retry/Skip/Abort.

13. **Approval Gate System**
    - Definition of done: Gate card with per-phase renderer, all 4 actions (Approve/Reject/Edit/Revert), comment input for reject/edit/revert. Revert re-runs previous phase with user feedback.

14. **Rule Gate Panel**
    - Definition of done: Sidebar to add/edit/delete rules (include/exclude/constraint/methodology). Rules injected into agent prompts. Post-validation via lightweight LLM call.

## Not in Phase 1:
- Session history page (Phase 2)
- Export/ZIP generation (Phase 2)
- Fork sessions (Phase 2)
- Presets — Quick Scan, Standard, Deep Research (Phase 2)
- GROBID full-text PDF parsing (Phase 2)
- Non-Research Article paper types (Phase 2)
- Paid academic sources — Google Scholar/SerpAPI (Phase 2)

## Phase 2: History, Export, Polish

1. Session history page (list + detail view + re-export)
2. Export/ZIP generation (LaTeX + PDF via Pandoc + index.html mini-site + sources)
3. Presets (Quick Scan $1/20 papers, Standard $5/100, Deep Research $20/200)
4. GROBID integration for full-text PDF parsing
5. Additional paper types (Literature Review, Systematic Review, Meta-Analysis, Position Paper, Case Study)
6. Fork sessions (copy config, start fresh)
7. Paid source integration (Google Scholar via SerpAPI)

## Phase 3: Scale & Optimize

1. Multi-session support (run multiple sessions concurrently)
2. n8n queue mode + Redis (for concurrent workers)
3. Performance optimization (caching, batch embeddings)
4. Qdrant migration if pgvector hits scale limits
5. Advanced analytics (cost trends, agent performance metrics)
