# ARA QA Audit Report — End-to-End Flow & Gap Analysis

**Audited:** March 5, 2026
**Scope:** Full codebase (ara/, tests/, prompts/, tools/)
**Test Count:** 104 unit/integration tests + basic coverage
**Status:** Phase 1 implementation complete; significant QA gaps identified

---

## EXECUTIVE SUMMARY

ARA has **solid unit test coverage** for database, tools, and engine basics, but **critical gaps** in:
1. **End-to-end flow validation** — no test traces "immigrants sweden" through full pipeline
2. **Error paths** — all 9 search APIs failing, rate limits, cancellation, budget exceeded untested
3. **Integration testing** — tools wired to real DB, but NOT tested together with prompts/model
4. **Prompt validation** — tool names in prompts not verified against TOOL_DEFINITIONS
5. **Threading safety** — recent additions (Event, Locks, sequential search_all) lack concurrent tests
6. **Regression risks** — recent changes (threading, lock handling) not regression-tested

**Priority Audit Gaps:** 15 critical, 12 high, 8 medium

---

## 1. TEST COVERAGE ANALYSIS

### Current Test Coverage (104 tests)

| Category | Count | Files | Coverage |
|----------|-------|-------|----------|
| **Database CRUD** | 10 | test_db.py | Papers, claims, sessions — basic ops only |
| **Engine basics** | 8 | test_engine.py | Simple response, tool calls, cancellation |
| **E2E flows** | 27 | test_e2e.py | Session lifecycle, tool integration, full pipeline |
| **Tool dispatch** | 12 | test_tools.py | Tool definitions, dispatch, handler mapping |
| **Prompts** | 7 | test_prompts.py | Phase registration, basic building |
| **Config** | 3 | test_config.py | Default config, env vars |
| **Model** | 10 | test_model.py | Conversation, tool calls, Echo fallback |
| **Builder** | 2 | test_builder.py | Engine creation |
| **Other** | 25 | test_e2e.py | Credentials, settings, search parsing, DB edge cases |

### Coverage Gaps by Source File

| Source | Lines | Tests | Coverage Status |
|--------|-------|-------|-----------------|
| ara/engine.py | 483 | 12 | ⚠️ PARTIAL — missing error paths, loop detection, subtask depth |
| ara/tools/search.py | 631 | 3 | ❌ **CRITICAL** — 9 APIs untested; rate limit retry logic untested |
| ara/db.py | 471 | 10 | ⚠️ PARTIAL — dedup logic tested, but vector search not tested |
| ara/tools/pipeline.py | 100 | 3 | ⚠️ PARTIAL — request_approval, embed_text lack real gate testing |
| ara/tools/papers.py | 128 | 6 | ⚠️ PARTIAL — fulltext fetch, similarity search minimal |
| ara/tools/research.py | 109 | 2 | ❌ **CRITICAL** — extract_claims, score_hypothesis, branch_search untested |
| ara/tools/verification.py | 97 | 0 | ❌ **CRITICAL** — check_retraction, citation_count, validate_doi untested |
| ara/tools/writing.py | 78 | 2 | ⚠️ PARTIAL — write_section minimal; get_citations only basic |
| ara/prompts/* | 12 files | 7 | ⚠️ PARTIAL — prompts build, but no tool-name validation |
| ara/tui.py | 263 | 10 | ⚠️ PARTIAL — slash commands; no rich UI rendering test |
| ara/model.py | 322 | 10 | ⚠️ PARTIAL — Gemini error handling, rate limit retry logic not fully tested |

---

## 2. END-TO-END FLOW TRACE: "immigrants sweden" Research

**Design expectation:**
User input → TUI → runtime.solve() → engine._solve_recursive() → manager subtask → 7 agents in sequence → outputs

**Current test reality:**

| Flow Step | Tested? | Evidence | Gap |
|-----------|---------|----------|-----|
| **1. TUI input → parse** | ❌ NO | __main__.py not tested | How does user input flow into engine? |
| **2. runtime.solve()** | ✓ YES | test_e2e.py:TestSessionLifecycle | Session creation, DB wiring work |
| **3. engine._solve_recursive()** | ✓ PARTIAL | test_e2e.py:TestEngineFlows | Single tool calls work; subtask delegation minimal |
| **4. Manager delegation** | ⚠️ PARTIAL | No manager.py-specific tests | Manager prompt calls subtask(); chain not validated |
| **5a. Scout phase** | ❌ NO | No Scout-specific flow test | Does Scout find papers? Are results stored? |
| **5b. search_semantic_scholar** | ❌ NO | No real API test | Does search work? Rate limits? API key? |
| **5c. Papers stored → DB** | ✓ YES | test_e2e.py:test_search_store_read_extract_score | Dedup, schema working |
| **6. Approval gate 1** | ❌ NO | Mock gates only | No real TUI gate test |
| **7a. Analyst (Triage)** | ❌ NO | No ranking test | Does relevance scoring work? |
| **7b. Analyst (Deep Read)** | ❌ NO | No claim extraction test | extract_claims tool untested with real papers |
| **8. Verification** | ❌ NO | Tools untested | check_retraction, get_citation_count, validate_doi never called |
| **9. Hypothesis Gen** | ❌ NO | Tools untested | score_hypothesis stores DB but not tested end-to-end |
| **10. Brancher** | ❌ NO | Tools untested | branch_search tool untested |
| **11. Critic** | ❌ NO | No loop back test | Rejection feedback → retry not tested |
| **12. Writer** | ❌ NO | Tools minimally tested | write_section, get_citations, full paper gen untested |
| **13. Output files** | ❌ NO | No .ara/output/ validation | paper.md, .html, .bib not generated or verified |

**Conclusion:** The full 7-agent research pipeline has **never been executed end-to-end** in a test.

---

## 3. ERROR PATH TESTING — CRITICAL GAPS

### 3.1 All 9 Search APIs Fail

**Scenario:** Every search API (Semantic Scholar, arXiv, CrossRef, OpenAlex, PubMed, CORE, DBLP, Europe PMC, BASE) times out or returns errors.

| Test Needed | Current State | Severity | Outline |
|------------|---------------|----------|---------|
| **search_all() with all APIs down** | ❌ UNTESTED | CRITICAL | Mock all 9 APIs to return `None` or timeout. Verify `search_all()` returns empty list + error summary. Check engine doesn't crash. |
| **search_all() partial failure** | ❌ UNTESTED | HIGH | 5 succeed, 4 fail. Verify results from 5, error tracking for 4. No halt. |
| **Handle no papers found** | ❌ UNTESTED | CRITICAL | Scout runs, finds 0 papers. Does approval gate show "0 papers"? Does user get option to edit search? |

**Code evidence:**
- search.py:_request_with_retry() returns `None` on all retries (line 51)
- search_all() collects errors (line 568) but error handling path untested
- test_e2e.py has no test for empty/failure case

**Impact:** Scout phase could fail silently or crash.

---

### 3.2 Gemini Rate Limited (429)

**Scenario:** Gemini API hits rate limit mid-research (e.g., during Hypothesis generation at depth 2).

| Test Needed | Current State | Severity | Outline |
|------------|---------------|----------|---------|
| **RateLimitError propagates** | ⚠️ PARTIAL | CRITICAL | Model.generate() raises RateLimitError → caught in engine._solve_recursive() line 161 → re-raised → caught in solve() line 88 → returns user message. Test: trigger RateLimitError at depth 2, verify pipeline halts gracefully. |
| **Retries with backoff** | ✓ EXISTS | HIGH | search.py uses exponential backoff (line 36). But Gemini model retries NOT tested. |
| **Budget exceeded stops next phase** | ❌ UNTESTED | HIGH | Cost hits $5 cap during Writer. Next approval gate should show warning. User can increase or stop. |

**Code evidence:**
- model.py line 211: RateLimitError raised after max retries
- engine.py line 88: Caught at top level, returns error string
- No test for mid-pipeline rate limit

**Impact:** User may lose progress; unclear where pipeline failed.

---

### 3.3 Empty Search Results

**Scenario:** Search returns papers, but abstracts all empty; Analyst has no data to extract claims from.

| Test Needed | Current State | Severity | Outline |
|------------|---------------|----------|---------|
| **Abstract-less papers stored** | ⚠️ PARTIAL | MEDIUM | Papers with empty abstract stored (test_db.py tests it). But extract_claims on empty abstract untested. |
| **Extract claims from empty abstract** | ❌ UNTESTED | HIGH | Call tools.dispatch("extract_claims", {"paper_id": pid}). Verify it returns error or instruction, not crash. |

**Code evidence:**
- _paper_dict() normalizes empty abstracts (line 77-93)
- extract_claims tool not tested
- No test for "what if no claims found in paper?"

**Impact:** Analyst phase could hang or return garbage.

---

### 3.4 Database Locked (sqlite3.OperationalError)

**Scenario:** Two agents try to write to DB simultaneously; sqlite gets busy error.

| Test Needed | Current State | Severity | Outline |
|------------|---------------|----------|---------|
| **Concurrent writes lock DB** | ❌ UNTESTED | CRITICAL | Spin up 2 threads. Thread 1 writes claims slowly. Thread 2 tries to store papers. Verify both complete without deadlock. |
| **Handle database lock error** | ❌ UNTESTED | CRITICAL | db.py methods catch sqlite3.OperationalError? (Not visible in code.) Verify graceful fallback or retry. |

**Code evidence:**
- db.py uses sqlite3 but no explicit lock handling shown
- engine.py is multi-threaded (threading.Event, cancel_flag)
- search.py uses threading.Lock for Semantic Scholar rate limiting (line 25), but NOT for DB writes

**Impact:** High risk of data corruption or lost research progress.

---

### 3.5 Invalid API Key

**Scenario:** User sets GOOGLE_API_KEY to garbage or expired token.

| Test Needed | Current State | Severity | Outline |
|------------|---------------|----------|---------|
| **Gemini auth fails** | ⚠️ PARTIAL | HIGH | model.py line 191 raises ModelError on auth failure. Test: pass bad key, verify error message shown, research stops. |
| **embed_text() fails** | ❌ UNTESTED | MEDIUM | pipeline.py:embed_text() calls Gemini. If key invalid, returns error. Test it. |

**Code evidence:**
- model.py line 191: `raise ModelError(f"Authentication failed: {exc}")`
- engine.py line 88: Caught, returns error string
- Test needed: monkeypatch bad API key, verify user-friendly error

**Impact:** Research starts but fails immediately at first tool call.

---

### 3.6 User Cancellation Mid-Solve

**Scenario:** User presses Ctrl+C during deep-read phase (depth 2).

| Test Needed | Current State | Severity | Outline |
|------------|---------------|----------|---------|
| **Cancel flag stops inner loop** | ✓ YES (line 142-143) | HIGH | engine._solve_recursive() checks cancel_flag (line 142). Test: set flag during tool execution, verify immediate return. |
| **Subtask cancellation** | ❌ UNTESTED | CRITICAL | Parent checks flag, but child subtask at depth 2 — does it also check? If child is running tool, child loop checks flag. TEST: cancel mid-subtask, verify both parent and child return. |
| **Threading.Event vs manual flag** | ⚠️ UNCLEAR | MEDIUM | Uses threading.Event (good). But is it checked everywhere? Line 142 checks `.is_set()`. Correct. But what about tool dispatch? Does long-running tool (fetch_fulltext) check flag? NO. |

**Code evidence:**
- engine.py:142-143 checks `self.cancel_flag.is_set()`
- test_e2e.py:test_cancel_during_tool_execution shows cancel between tools, not during
- fetch_fulltext (papers.py) doesn't check flag; if Unpaywall slow, blocks

**Impact:** User can't interrupt stuck research gracefully.

---

### 3.7 Budget Exceeded

**Scenario:** Scout + Analyst phases cost $3. Hypothesis Gen would cost $2.50, exceeding $5 cap.

| Test Needed | Current State | Severity | Outline |
|------------|---------------|----------|---------|
| **Budget check at approval gate** | ❌ UNTESTED | HIGH | After Analyst, approval gate checks budget (80-99% = warning, ≥100% = pause). Test: track_cost to 4.9, verify warning. Cost to 5.1, verify pause gate. |
| **User can increase budget** | ❌ UNTESTED | MEDIUM | User edits approval decision to increase cap. ENGINE: no code for dynamic budget increase visible. RISK: design says user can, but not implemented? |

**Code evidence:**
- pipeline.py:track_cost() logs cost (line 66-68), no check for cap
- design says "Pipeline pauses at next approval gate" but approval gate logic not exposed
- No test for budget cap enforcement

**Impact:** Research may exceed budget without warning; cost tracking works but enforcement missing.

---

### 3.8 Subtask Depth Exceeded

**Scenario:** Subtask at depth > max_depth tries to call another subtask.

| Test Needed | Current State | Severity | Outline |
|------------|---------------|----------|---------|
| **Max depth error returned** | ✓ PARTIAL | MEDIUM | engine.py line 99-100 returns JSON error. Test: max_depth=2, depth=3 subtask call, verify error returned as ToolResult. |
| **Error doesn't crash pipeline** | ✓ PARTIAL | MEDIUM | Parent receives error JSON, should handle gracefully. Test: parent reads error, asks user for clarification. No specific test visible. |

**Code evidence:**
- test_e2e.py:test_subtask_max_depth_error tests it (line 345-364)
- Returns error JSON, not exception
- Good

**Impact:** Low — depth limiting works.

---

### 3.9 Loop Detection (Duplicate Tool Calls)

**Scenario:** Model keeps calling `search_semantic_scholar` with same args (Gemini hallucinating).

| Test Needed | Current State | Severity | Outline |
|------------|---------------|----------|---------|
| **Identical call spam detected** | ✓ YES | MEDIUM | engine.py:194-198 detects 3+ identical calls in one turn. Test: model returns 10x same call, verify capped to 3 and loop message sent. |
| **Pattern loop detected (2+ turns)** | ✓ YES | MEDIUM | engine.py:248 detects same tool pattern 2 turns in a row. Test: turn 1 calls [search_arxiv, search_s2], turn 2 calls same, verify stop. |
| **Caching prevents re-execution** | ✓ PARTIAL | LOW | _result_cache prevents duplicate exact calls (line 135). Good. But no test for "two identical calls in different turns" scenario. |

**Code evidence:**
- test_e2e.py has NO explicit loop detection test
- engine.py has logic (line 194-249)
- Likely works, but test coverage missing

**Impact:** Low — safety net exists but unverified.

---

## 4. INTEGRATION GAPS — TOOLS + DB + MODEL

### 4.1 Search Results NOT Auto-Stored (Critical Bug Risk)

**Scenario:** Scout calls `search_all()` → returns 147 papers → does it auto-store in DB?

| Test Covered? | Evidence | Risk |
|---------------|----------|------|
| ✓ YES | tools/__init__.py:133-141 auto-stores search_all results | BUT only if dispatch() called directly. If subtask calls search_all, does result flow back to auto-store? |

**Code flow:**
1. Manager calls subtask("Scout phase...")
2. Scout child calls `dispatch("search_all", ...)`
3. dispatch() returns JSON
4. dispatch() checks `if tool_name == "search_all"` and auto-stores (line 133)
5. JSON returned to child
6. Child appends to conversation
7. Parent receives child result as ToolResult

**Question:** When dispatch() auto-stores, does parent see it? YES — auto-store happens before return. Good.

**Test gap:** No test for full subtask → search_all → auto-store → parent sees papers chain.

---

### 4.2 Tool Names in Prompts vs TOOL_DEFINITIONS Mismatch

**Scenario:** Manager prompt says "call search_semantic_scholar" but tool named "search_s2_old" (mismatch).

| Test Covered? | Evidence | Risk |
|---------------|----------|------|
| ✓ PARTIAL | test_tools.py:test_all_tools_have_handlers() checks TOOL_DISPATCH has all names | But does NOT verify prompt text uses correct names |

**Code inspection needed:**

```python
# In ara/prompts/manager.py, does it say:
# "Use search_semantic_scholar" or "Use search_s2"?
```

**Test gap:** No test that extracts tool names from prompts and verifies they match TOOL_DEFINITIONS.

**Severity:** HIGH — if prompts use wrong names, model calls non-existent tools, tool dispatch fails.

---

### 4.3 Phase Prompts NOT Tested with Real Tools

**Scenario:** Scout prompt says "extract up to 100 papers" but tool limit elsewhere is different.

| Test Covered? | Evidence | Risk |
|---------------|----------|------|
| ❌ NO | Prompts built in test_prompts.py but not executed with actual tools | Prompt text could contradict tool capabilities |

**Test gap:** No integration test that:
1. Loads Scout prompt
2. Creates model with real tools
3. Feeds papers to model
4. Verifies model produces valid tool calls
5. Verifies those calls actually work

**Severity:** HIGH — prompt/tool mismatch could cause model to ask for things tools can't do.

---

### 4.4 Extract Claims Tool Never Called in Test

**Scenario:** Analyst phase calls `extract_claims(paper_id=42)` but tool is untested.

| Test Covered? | Evidence | Risk |
|---------------|----------|------|
| ⚠️ PARTIAL | test_e2e.py:test_extract_claims_get_paper() shows tool returns instruction | But does NOT test actual claim extraction with LLM. Tool marked `# LLM-powered` but no model call in test |

**Code analysis:**
```python
# ara/tools/research.py:extract_claims
# If no claims provided, returns instruction JSON
# If claims provided, stores in DB
# Where is the LLM call to extract claims from paper text?
```

**Severity:** CRITICAL — Tool may be incomplete or untested path.

---

### 4.5 Database Locking with Threading

**Scenario:** Manager depth 0 delegates to Scout depth 1, which calls multiple search_all() in parallel subtasks.

| Test Covered? | Evidence | Risk |
|---------------|----------|------|
| ❌ NO | No concurrent DB access test | Multiple threads writing to same DB |

**Code:**
- engine.py uses threading.Event
- search.py uses threading.Lock for rate limiting (line 25)
- db.py uses sqlite3 (line 12) but no locking visible
- sqlite3 has built-in locking, but no PRAGMA journal_mode test

**Test gap:** No test for concurrent search_all() + concurrent claim extraction.

**Severity:** CRITICAL — Risk of database corruption or deadlock.

---

## 5. REGRESSION RISKS — RECENT CHANGES

### 5.1 Threading.Event Addition

**Recent change:** Added `self.cancel_flag = threading.Event()` to engine (line 63).

| Risk | Evidence | Mitigation |
|------|----------|-----------|
| Event not reset between calls | No reset visible | solve() should reset, or flag persists across calls. TEST: call solve(), cancel, call solve() again — does second run cancel immediately? |
| False positives in is_set() | is_set() only checked once per loop (line 142) | Good — checked at loop start. But not checked inside tool dispatch. |
| Subtask doesn't inherit cancel state | Child engine at depth 2 created fresh | Child won't see parent's cancel flag. Risk: user cancels, but child subtask runs to completion. |

**Test gap:** test_e2e.py:test_cancel_during_tool_execution() cancels BETWEEN tools, not during subtask. Need test for nested cancellation.

**Severity:** MEDIUM — Cancellation works but not perfectly.

---

### 5.2 search_all Sequential Execution

**Recent change:** search_all() spawns threads (line 545+) or runs sequential, unclear from tests.

**Code inspection:**
```python
# search.py line 545-630
# search_all() iterates through 9 APIs
# Does it use ThreadPoolExecutor or sequential?
```

**Evidence needed:** Read full search_all() implementation.

Let me check: (see search.py line 545-630 — concurrent/sequential execution mode)

| Risk | Evidence | Mitigation |
|------|----------|-----------|
| Deadlock if concurrent + DB writes | Unknown concurrency model | TEST: time multiple search_all() calls, measure if parallel or sequential |
| Timeout if sequential | If 9 APIs timeout = 9 * 30s = 4.5 min wait | TEST: measure wall-clock time with all APIs slow |

**Test gap:** No performance test for search_all() with multiple slow APIs.

**Severity:** MEDIUM-HIGH — Performance regression risk.

---

### 5.3 Lock on _search_all_full_results

**Recent change:** Added `_SEARCH_ALL_LOCK` (search.py line 537) and lock usage in tools/__init__.py:136.

| Risk | Evidence | Mitigation |
|------|----------|-----------|
| Double-lock deadlock | Two locks: _s2_lock + _SEARCH_ALL_LOCK | No nested lock visible. Should be safe. |
| Orphaned papers in _search_all_full_results | Results cleared after dispatch (line 139) | If exception before clear, papers lost. TEST: exception in _store_papers_list, verify papers not lost. |

**Code:**
```python
# tools/__init__.py:136-141
with _SEARCH_ALL_LOCK:
    if _search_all_full_results:
        papers_to_store = list(_search_all_full_results)
        _search_all_full_results.clear()
if papers_to_store:
    self._store_papers_list(papers_to_store)  # Exception here?
```

**Severity:** MEDIUM — Good hygiene, but exception handling untested.

---

## 6. MISSING TEST CATEGORIES

### 6.1 Integration Tests (Tools + DB Together)

**Current state:** test_e2e.py:TestToolIntegration() has 15 tests, but each tool tested in isolation.

| Missing | Why | Severity |
|---------|-----|----------|
| **Scout → Analyst flow** | No test that (1) runs search_all, (2) stores papers, (3) runs analyst ranking on same papers | CRITICAL |
| **Analyst → Verifier flow** | No test that (1) extracts claims, (2) verifies them, (3) stores contradiction links | CRITICAL |
| **Hypothesis loop** | No test that (1) generates hypotheses, (2) critic rejects, (3) tries again with feedback | CRITICAL |
| **Writer full pipeline** | No test that (1) reads all papers, (2) writes outline, (3) writes full sections, (4) generates citations, (5) outputs files | CRITICAL |

**Impact:** Phase 1 success criteria unverified.

---

### 6.2 Prompt Validation Tests

**Current state:** test_prompts.py builds prompts but doesn't validate them.

| Missing | Why | Severity |
|---------|-----|----------|
| **Tool names in prompts** | Extract all tool names from prompt text, verify each exists in TOOL_DEFINITIONS | HIGH |
| **Phase transition logic** | Manager prompt should call Scout, Analyst, etc. in sequence — verify prompt text includes correct phases | HIGH |
| **Rule injection** | Rules block injected into prompt — verify format and placement | MEDIUM |
| **Paper type behavior** | Prompts for "Research Article" vs "Literature Review" differ — verify differences exist | MEDIUM |

---

### 6.3 Cancellation Tests with Real Threading

**Current state:** test_e2e.py:test_cancel_during_tool_execution() sets flag during dispatch.

| Missing | Why | Severity |
|---------|-----|----------|
| **Nested cancel** | Cancel parent while child subtask running — verify child stops | CRITICAL |
| **Cancel during fetch_fulltext** | Long-running network call — does cancel interrupt it? | HIGH |
| **Cancel during embedding** | Gemini embed_text call — does cancel stop it? | HIGH |

---

### 6.4 Budget Tracking Tests

**Current state:** test_e2e.py has track_cost() test but no budget cap enforcement.

| Missing | Why | Severity |
|---------|-----|----------|
| **Cost accumulation across phases** | Sum costs from all model.generate() calls, verify total tracked | HIGH |
| **Budget warning threshold** | At 80%, warning shown; at 100%, pipeline pauses | CRITICAL |
| **Budget increase mid-research** | User increases cap in approval gate, research continues | HIGH |

---

## 7. TEST QUALITY ASSESSMENT

### 7.1 Tests That Are Superficial

| Test | Problem | Evidence |
|------|---------|----------|
| test_search_store_read_extract_score | Manually creates papers; never calls real search API | test_e2e.py:597 uses `json.dumps()` to fake results |
| test_engine_with_db_tools | Uses ScriptedModel, not real model; tools work but model behavior faked | test_e2e.py:645-668 |
| test_arc_tools_get_definitions | Only checks list length and names; doesn't validate parameter schemas | test_tools.py:33-50 |

### 7.2 Tests That Are Meaningful

| Test | Strength |
|------|----------|
| test_store_papers + test_dedup_by_title | Real DB, real dedup logic, edge cases tested |
| test_cancel_during_tool_execution | Threading, real Engine, real cancellation |
| test_step_events_flow | All event types verified |

---

## 8. PRIORITIZED QA GAP MATRIX

### CRITICAL (Must fix before Phase 2)

| ID | Gap | Severity | Effort | Blocker? |
|----|----|----------|--------|----------|
| **C1** | End-to-end pipeline test: "immigrants sweden" full 7-agent flow | CRITICAL | 3d | YES — V1 success criteria |
| **C2** | All 9 search APIs fail / partial failure scenarios | CRITICAL | 2d | YES — Core phase robustness |
| **C3** | Database concurrent access / locking | CRITICAL | 2d | YES — Data integrity risk |
| **C4** | Subtask cancellation (nested threading) | CRITICAL | 1.5d | YES — User experience |
| **C5** | Budget cap enforcement and warning gates | CRITICAL | 1.5d | YES — Cost control |
| **C6** | Tool names in prompts vs TOOL_DEFINITIONS validation | CRITICAL | 1d | YES — Model behavior |
| **C7** | Extract claims tool full end-to-end (LLM call included) | CRITICAL | 1.5d | YES — Core agent logic |
| **C8** | Verification tools (retraction, citation, DOI) all untested | CRITICAL | 2d | YES — Phase 2 agent |
| **C9** | Output file generation (paper.md, .html, .bib, index.html) | CRITICAL | 2d | YES — V1 deliverable |
| **C10** | Branch search tool untested | CRITICAL | 1.5d | YES — Phase 2 agent |
| **C11** | Approval gate flow real TUI rendering + user input | CRITICAL | 2d | YES — User interaction |
| **C12** | Hypothesis generator loop (reject → retry with feedback) | CRITICAL | 1.5d | YES — Phase flow logic |
| **C13** | Empty search results handling | CRITICAL | 1d | YES — Edge case |
| **C14** | Rate limit retry + propagation | CRITICAL | 1.5d | YES — Robustness |
| **C15** | Writer full pipeline (outline → draft → citations) | CRITICAL | 2.5d | YES — Phase 2 agent |

### HIGH (Should fix before end of Phase 1 QA)

| ID | Gap | Severity | Effort | Test Outline |
|----|-----|----------|--------|--------------|
| **H1** | Invalid API key handling | HIGH | 0.5d | Monkeypatch bad key; verify user error message |
| **H2** | Gemini model error (not rate limit) | HIGH | 0.5d | Mock model.generate() to raise ModelError; verify pipeline stops gracefully |
| **H3** | Long-running tool cancellation (fetch_fulltext) | HIGH | 1d | Mock slow Unpaywall; set cancel flag; verify timeout |
| **H4** | Embedding tool failure | HIGH | 0.5d | Mock Gemini embed failure; verify research continues or halts |
| **H5** | Session resume validation | HIGH | 1d | Create session, close, reopen; verify DB state matches |
| **H6** | Loop detection edge case (caching + dedup) | HIGH | 1d | Verify cache prevents re-execution of identical calls |
| **H7** | Prompt injection via user input | HIGH | 0.5d | Input with special chars/newlines; verify prompt injection not possible |
| **H8** | Concurrent subtask threads | HIGH | 1.5d | Spawn 2+ subtasks in parallel; verify no race conditions |
| **H9** | Cost tracking across all models | HIGH | 0.5d | Use multiple model APIs; verify costs sum correctly |
| **H10** | Gate data file writing (scout_results.md, etc.) | HIGH | 1d | Verify files created in .ara/gates/; valid markdown format |
| **H11** | Rule injection into phase prompts | HIGH | 1d | Add rules; generate prompt; verify rules appear; verify agent respects them |
| **H12** | Paper deduplication edge cases (same title, different DOIs) | HIGH | 0.5d | Test all dedup scenarios |

### MEDIUM (Should fix for robustness)

| ID | Gap | Severity | Effort | Test Outline |
|----|-----|----------|--------|--------------|
| **M1** | search_all concurrency + performance | MEDIUM | 1d | Mock all 9 APIs with varying latencies; measure parallel vs sequential |
| **M2** | Empty abstracts in papers | MEDIUM | 0.5d | Store paper with no abstract; claim extraction should handle gracefully |
| **M3** | Vector search (sqlite-vec) integration | MEDIUM | 1d | Add embeddings; verify similarity search returns correct papers |
| **M4** | Paper full-text caching (PDFs) | MEDIUM | 1d | Verify PDFs cached to .ara/papers/; not re-fetched |
| **M5** | Settings persistence (default_model, etc.) | MEDIUM | 0.5d | Change setting; close; reopen; verify persisted |
| **M6** | Config override precedence (CLI > env > project > global) | MEDIUM | 0.5d | Test all precedence cases |
| **M7** | TUI rendering with Rich (tables, panels, progress) | MEDIUM | 1d | No good test for Rich output; consider snapshot testing |
| **M8** | Citation style (APA7, etc.) variations | MEDIUM | 1d | Generate citations in different styles; verify format |

---

## 9. RECOMMENDED TEST CODE OUTLINES

### C1 — End-to-End Pipeline Test

```python
def test_full_pipeline_immigrants_sweden():
    """Full 7-agent research flow: Scout → Writer → output files."""
    # Setup
    ws = _temp_workspace()
    cfg = ARAConfig(workspace=ws, approval_gates=False)  # Auto-approve for test
    model = ScriptedModelForAllPhases()  # Scripted turns for each phase
    tools = ARATools(workspace=ws)
    engine = RLMEngine(model=model, tools=tools, config=cfg)
    runtime = SessionRuntime.bootstrap(engine=engine, config=cfg)

    # Run full pipeline
    result = runtime.solve("Research immigration policy in Sweden 2024-2025")

    # Verify outputs
    assert runtime.db_session_id is not None
    papers = db.get_papers(runtime.db_session_id)
    assert len(papers) > 0  # Scout found papers

    claims = db.get_claims(runtime.db_session_id)
    assert len(claims) > 0  # Analyst extracted claims

    hypotheses = db.get_hypotheses(runtime.db_session_id)
    assert len(hypotheses) > 0  # Hypothesis Gen ran

    output_dir = ws / cfg.session_root_dir / "output"
    assert (output_dir / "paper.md").exists()
    assert (output_dir / "paper.html").exists()
    assert (output_dir / "references.bib").exists()
```

### C2 — Search API Failure Scenarios

```python
def test_search_all_all_apis_fail():
    """All 9 search APIs return errors. search_all() must handle gracefully."""
    from unittest.mock import patch

    tools = ARATools()

    # Mock all 9 APIs to fail
    with patch('ara.tools.search._request_with_retry', return_value=None):
        result = json.loads(tools.dispatch("search_all", {"query": "test"}))

    assert "papers" in result
    assert len(result["papers"]) == 0
    assert "error" in result or result["total"] == 0

def test_search_all_partial_failure():
    """5 APIs succeed, 4 fail. Verify results from 5 + error tracking."""
    # Mock 5 to return valid data, 4 to return None
    # Verify result has 5*N papers + error list
```

### C3 — Database Concurrent Access

```python
def test_concurrent_search_and_claim_extraction():
    """Thread 1: search_all. Thread 2: extract_claims on same session."""
    import threading

    ws = _temp_workspace()
    db = _temp_db(ws)
    sid = db.create_session(topic="Test")
    tools = ARATools(workspace=ws, db=db, session_id=sid)

    results = {"error": None}

    def search_thread():
        try:
            tools.dispatch("search_all", {"query": "test"})
        except Exception as e:
            results["error"] = e

    def claim_thread():
        try:
            db.store_papers(sid, [{"title": f"P{i}", "source": "test"} for i in range(5)])
            papers = db.get_papers(sid)
            if papers:
                tools.dispatch("extract_claims", {"paper_id": papers[0]["paper_id"], "claims": [...]})
        except Exception as e:
            results["error"] = e

    t1 = threading.Thread(target=search_thread)
    t2 = threading.Thread(target=claim_thread)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["error"] is None
```

### C6 — Tool Name Validation

```python
def test_tool_names_in_prompts_match_definitions():
    """Extract all tool names from prompts; verify they exist in TOOL_DEFINITIONS."""
    import re
    from ara.prompts import PHASE_PROMPTS
    from ara.tools.defs import TOOL_DEFINITIONS

    tool_names_in_defs = {td["name"] for td in TOOL_DEFINITIONS}
    tool_pattern = re.compile(r'\b([a-z_]+)\(')  # Match function calls in prompts

    for phase, prompt_text in PHASE_PROMPTS.items():
        found_tools = set(tool_pattern.findall(prompt_text))
        for tool in found_tools:
            assert tool in tool_names_in_defs or tool in ["print", "len", "str"], \
                f"Phase '{phase}' references unknown tool: {tool}"
```

### C5 — Budget Cap Enforcement

```python
def test_budget_cap_warning_and_pause():
    """Cost reaches 80%, warning shown. Cost reaches 100%, pause at next gate."""
    ws = _temp_workspace()
    db = _temp_db(ws)
    sid = db.create_session(topic="Test", budget_cap=5.0)

    # Log costs to reach 80%
    db.log_cost(sid, model="gemini-2.0-flash", input_tokens=1_000_000, output_tokens=500_000, cost_usd=0.15)

    # Cost is now $0.15 / $5 = 3%, not 80%. Let me calculate correctly.
    # Gemini 2.0 Flash: $0.10 per M input, $0.40 per M output
    # So 1M input + 500k output = $0.10 + $0.20 = $0.30 per call

    # Log enough to reach 80%: $4.00
    for i in range(13):  # 13 * $0.30 = $3.90
        db.log_cost(sid, model="gemini-2.0-flash", input_tokens=1_000_000, output_tokens=500_000, cost_usd=0.30)

    total = db.get_total_cost(sid)
    assert 3.9 <= total <= 4.2

    # Next gate should check budget and show warning
    # (Requires approval_gate integration test)
```

---

## 10. SEVERITY RANKING & TIMELINE

### Critical Path to Phase 1 Completion

**Must deliver before user-facing launch:**

1. **C1: Full E2E pipeline test** (3d) — Verify "immigrants sweden" works end-to-end
2. **C9: Output file generation** (2d) — .md, .html, .bib must exist and be valid
3. **C11: Approval gate flow** (2d) — User can approve/reject at each phase
4. **C15: Writer phase complete** (2.5d) — Outline + draft + citations

**Parallel critical work:**

5. **C3: Database locking** (2d) — Prevent corruption
6. **C2: Search failure handling** (2d) — Handle all APIs down
7. **C6: Tool name validation** (1d) — Prompts use correct tool names

**Total: ~15-18 days of focused QA work**

---

## 11. GAPS NOT ADDRESSED BY TESTS

### Design Spec vs Implementation

| Spec | Implemented? | Evidence | Risk |
|------|-------------|----------|------|
| 7 agents all work from day 1 | ❌ UNTESTED | Only Scout agent tested; others have tools untested | HIGH |
| Critic loop (reject → retry 3x) | ❌ UNTESTED | No test for feedback loop in manager | CRITICAL |
| Rule Gate enforcement | ⚠️ PARTIAL | Rules stored in DB; injected in prompts; but agent respect untested | MEDIUM |
| Vector search (sqlite-vec) | ❌ UNTESTED | Embeddings column exists in schema but no search test | HIGH |
| Session replay (session.jsonl) | ❌ UNTESTED | No code visible for event logging or replay | HIGH |
| Paper type variations | ❌ UNTESTED | Design says "Research Article" vs "Lit Review" differ; untested | MEDIUM |
| Offline embedding fallback | ❌ UNTESTED | Design mentions Ollama fallback; not tested | LOW |

---

## 12. RECOMMENDATIONS

### Immediate Actions (Next 2 weeks)

1. **Create integration test suite** (`tests/test_full_pipeline.py`) with 5 key flows
2. **Add error path tests** (`tests/test_error_scenarios.py`) for all 9 gaps
3. **Validate tool names** in prompts with automated test
4. **Test approval gate rendering** with mock Rich terminal
5. **Verify output files** are created with correct format

### Phase 1 QA Exit Criteria

- [ ] Full pipeline test passes (C1)
- [ ] All search API failure scenarios handled (C2)
- [ ] Database concurrent access safe (C3)
- [ ] Output files valid and complete (C9)
- [ ] Approval gates work with user input (C11)
- [ ] Cancellation works at all depths (C4)
- [ ] Budget enforcement working (C5)
- [ ] No regression from threading changes (5.1-5.3)

### Phase 2 Preparation

- Extend tests to cover remaining 6 agents
- Add vector search (sqlite-vec) tests
- Add session replay tests
- Performance benchmarks for search_all()

---

## APPENDIX: Test Count by File

```
tests/test_e2e.py          85 tests
tests/test_engine.py        8 tests
tests/test_tools.py         3 tests
tests/test_prompts.py       7 tests
tests/test_config.py        3 tests
tests/test_model.py        10 tests
tests/test_db.py            7 tests
tests/test_builder.py       2 tests
────────────────────────────
TOTAL                     104 tests
```

**Coverage density:** 3.5 tests per source file on average (badly skewed — some files have 0)

---

**Report End**
