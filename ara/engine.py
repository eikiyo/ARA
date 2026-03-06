# Location: ara/engine.py
# Purpose: Recursive LLM engine — tool dispatch, subtask delegation, context management
# Functions: RLMEngine, ExternalContext, StepCallback, TurnSummary
# Calls: model.py, tools/__init__.py, prompts/__init__.py
# Imports: dataclasses, time, logging, json

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import ARAConfig
from .model import (
    BaseModel, Conversation, ModelTurn, ToolCall, ToolResult,
    ModelError, RateLimitError, TokenUsage,
)
from .prompts import build_system_prompt, build_phase_system_prompt, PHASE_PROMPTS
from .tools import ARATools, PHASE_TOOLS, _tool_matches_phase

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class TurnSummary:
    role: str
    text: str
    tool_names: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExternalContext:
    topic: str = ""
    paper_type: str = "research_article"
    rules: list[dict[str, Any]] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    turn_history: list[TurnSummary] = field(default_factory=list)
    claim_count: int = 0
    papers_with_claims: int = 0


@dataclass(slots=True)
class StepEvent:
    event_type: str  # "thinking", "tool_call", "tool_result", "text", "error", "subtask_start", "subtask_end"
    data: str = ""
    tool_name: str = ""
    depth: int = 0

StepCallback = Callable[[StepEvent], None]


class RLMEngine:
    def __init__(
        self,
        model: BaseModel,
        tools: ARATools,
        config: ARAConfig,
        writer_model: BaseModel | None = None,
    ):
        self.model = model
        self.writer_model = writer_model or model
        self.tools = tools
        self.config = config
        self.cancel_flag = threading.Event()
        self._total_tokens = TokenUsage()

    @property
    def total_tokens(self) -> TokenUsage:
        return self._total_tokens

    def solve(
        self,
        objective: str,
        context: ExternalContext | None = None,
        on_event: StepCallback | None = None,
    ) -> str:
        # Reset per-solve state
        from .tools.search import reset_search_all_state
        reset_search_all_state()

        _log.info("=" * 60)
        _log.info("SOLVE START — objective: %s", objective[:120])
        _log.info("=" * 60)

        ctx = context or ExternalContext()
        try:
            result = self._solve_recursive(
                objective=objective,
                context=ctx,
                depth=0,
                on_event=on_event,
            )
            _log.info("SOLVE END — result length: %d chars", len(result))
            return result
        except RateLimitError as exc:
            return f"[Rate limit] API quota exhausted. Wait a few minutes and try again.\n({exc})"

    # ── Programmatic Pipeline ─────────────────────────────────────

    def _phase_step_budgets(self) -> dict[str, int]:
        """Step budgets from config."""
        c = self.config
        return {
            "scout": c.steps_scout,
            "protocol": c.steps_protocol,
            "verifier": c.steps_verifier,
            "triage": c.steps_triage,
            "deep_read": c.steps_deep_read,
            "brancher": c.steps_brancher,
            "hypothesis": c.steps_hypothesis,
            "critic": c.steps_critic,
            "synthesis": c.steps_synthesis,
        }

    def _get_pipeline_phases(self) -> list[dict[str, str | None]]:
        """Return pipeline phase definitions — branches on paper_type."""
        if self.config.paper_type == "conceptual":
            return self._pipeline_phases_conceptual()
        return self._pipeline_phases_review()

    @staticmethod
    def _pipeline_phases_review() -> list[dict[str, str | None]]:
        """Pipeline phases for systematic literature review."""
        return [
            {
                "name": "scout",
                "prompt": "scout",
                "objective": (
                    "Scout phase: Search for papers on: {topic}. "
                    "Use search_all() with 1-2 query formulations. "
                    "Target: {min_papers}+ unique papers. Date range: {search_start_year} to present. "
                    "When the database reports target reached, stop searching."
                ),
            },
            {
                "name": "snowball",
                "prompt": None,
                "objective": None,
            },
            {
                "name": "protocol",
                "prompt": "protocol",
                "objective": (
                    "Draft a PROSPERO-style pre-registration protocol for: {topic}. "
                    "Call list_papers(compact=true, limit=50) to see available papers. "
                    "Write the protocol with: PICO, search strategy, inclusion/exclusion criteria, "
                    "quality assessment (JBI), data extraction protocol, synthesis approach (GRADE). "
                    "Date range for inclusion: {search_start_year} to present. "
                    "Screening methodology: AI-automated relevance scoring (threshold 0.6). "
                    "Do NOT claim human dual-reviewer screening. "
                    "Save using write_section(section='protocol', content=...). "
                    "Do NOT read individual papers. Do NOT create outlines or drafts. Only the protocol."
                ),
            },
            {
                "name": "verifier",
                "prompt": "verifier",
                "objective": (
                    "Verify paper credibility. Call list_papers(limit=100) to get top papers. "
                    "For each paper with a DOI, call validate_doi, check_retraction, get_citation_count. "
                    "Work through papers systematically. Flag any retracted or suspicious papers."
                ),
            },
            {
                "name": "triage",
                "prompt": None,
                "objective": None,
            },
            {
                "name": "fetch_texts",
                "prompt": None,
                "objective": None,
            },
            {
                "name": "embed",
                "prompt": None,
                "objective": None,
            },
            {
                "name": "deep_read",
                "prompt": "analyst_deep_read",
                "objective": (
                    "Extract structured claims from papers about {topic}. "
                    "STEP 1: Call list_papers(selected_only=true, limit=100) to get triage-selected papers. "
                    "STEP 2: For EVERY selected paper: call read_paper(paper_id=ID, include_fulltext=true) to get the full paper text, "
                    "then extract_claims with specific quotes from the text, then assess_risk_of_bias. "
                    "Each claim needs: claim_text, claim_type (finding/method/limitation/gap), "
                    "confidence (0-1), supporting_quotes (EXACT quotes from the paper text). "
                    "Also extract: sample_size, effect_size, p_value, study_design, population. "
                    "Process papers one at a time. Target: {min_claims}+ claims from {min_deep_read_papers}+ papers. "
                    "DO NOT STOP until you have processed ALL selected papers. "
                    "If you run out of steps, you have failed. Keep going."
                ),
            },
            {
                "name": "brancher",
                "prompt": "brancher",
                "objective": (
                    "Cross-domain search for: {topic}. "
                    "Search for papers from adjacent fields, alternative methodologies, "
                    "analogous problems in other domains, and independent confirmations. "
                    "Use search_semantic_scholar, search_crossref, etc. with cross-disciplinary queries."
                ),
            },
            {
                "name": "hypothesis",
                "prompt": "hypothesis",
                "objective": (
                    "Generate 5+ research hypotheses from the evidence on {topic}. "
                    "You have {claim_count} claims from {papers_with_claims} deeply-read papers. "
                    "Score each: novelty, feasibility, evidence_strength, methodology_fit, impact, reproducibility. "
                    "For the top hypothesis, specify: methodology (PRISMA/GRADE), "
                    "analysis approach, quality assessment framework (JBI/Newcastle-Ottawa). "
                    "Use score_hypothesis to evaluate each one. "
                    "Ground every hypothesis in specific claims from the database — do NOT hypothesize beyond the evidence."
                ),
            },
            {
                "name": "critic",
                "prompt": "critic",
                "objective": (
                    "FIRST: Call list_claims() to load all extracted evidence. "
                    "You have {claim_count} claims from {papers_with_claims} deeply-read papers. "
                    "Use search_similar() to find the most relevant papers for the hypothesis. "
                    "Read 3-5 key papers using read_paper(paper_id=ID, include_fulltext=true) to ground your evaluation. "
                    "THEN evaluate the top hypothesis across 8 dimensions. "
                    "Verify the novelty framework label (INVERSION/MISSING LINK/MODERATOR/etc). "
                    "Apply the meta-test: would an expert believe something different? "
                    "Decide: APPROVE or REJECT with detailed feedback and specific revisions."
                ),
            },
            {
                "name": "synthesis",
                "prompt": "synthesis",
                "objective": (
                    "Prepare structured data for the writer. You have {claim_count} claims from "
                    "{papers_with_claims} deeply-read papers. "
                    "FIRST: Call list_claims() to load all extracted evidence. "
                    "Call list_papers(compact=true) to get exact author names. "
                    "Use search_similar() per theme to find relevant papers via embeddings. "
                    "For top 5-10 papers, call read_paper(paper_id=ID, include_fulltext=true). "
                    "Build these outputs: "
                    "(1) Study characteristics table with author names, (2) Evidence synthesis "
                    "table with GRADE ratings, (3) Risk of bias assessment, "
                    "(4) PRISMA flow numbers, (5) Citation map by theme with (Author, Year), "
                    "(6) Structural causal model notes, (7) Inclusion/exclusion criteria table. "
                    "Save ALL tables using write_section(section='synthesis_data', content=...) so the writer can load them."
                ),
            },
        ]

    @staticmethod
    def _pipeline_phases_conceptual() -> list[dict[str, str | None]]:
        """Pipeline phases for conceptual/theoretical paper."""
        return [
            {
                "name": "scout",
                "prompt": "scout",
                "objective": (
                    "Scout phase: Search for papers on: {topic}. "
                    "Use search_all() with 1-2 query formulations. "
                    "Target: {min_papers}+ unique papers. Date range: {search_start_year} to present. "
                    "Focus on: (a) core theoretical papers, (b) empirical studies providing evidence "
                    "for framework building, (c) competing frameworks and models. "
                    "{special_authors_instruction}"
                    "When the database reports target reached, stop searching."
                ),
            },
            {
                "name": "snowball",
                "prompt": None,
                "objective": None,
            },
            {
                "name": "protocol",
                "prompt": "protocol",
                "objective": (
                    "Draft a research protocol for conceptual paper on: {topic}. "
                    "Call list_papers(compact=true, limit=50) to see available papers. "
                    "Write the protocol with: research questions, theoretical streams to cover, "
                    "framework development approach (typology + process model + propositions), "
                    "inclusion/exclusion criteria for literature, analysis approach. "
                    "Date range: {search_start_year} to present. "
                    "Save using write_section(section='protocol', content=...). "
                    "Do NOT read individual papers. Only the protocol."
                ),
            },
            {
                "name": "verifier",
                "prompt": "verifier",
                "objective": (
                    "Verify paper credibility. Call list_papers(limit=100) to get top papers. "
                    "For each paper with a DOI, call validate_doi, check_retraction, get_citation_count. "
                    "Work through papers systematically. Flag any retracted or suspicious papers."
                ),
            },
            {
                "name": "triage",
                "prompt": None,
                "objective": None,
            },
            {
                "name": "fetch_texts",
                "prompt": None,
                "objective": None,
            },
            {
                "name": "embed",
                "prompt": None,
                "objective": None,
            },
            {
                "name": "deep_read",
                "prompt": "analyst_deep_read",
                "objective": (
                    "Extract theoretical arguments and evidence from papers about {topic}. "
                    "STEP 1: Call list_papers(selected_only=true, limit=100) to get triage-selected papers. "
                    "STEP 2: For EVERY selected paper: call read_paper(paper_id=ID, include_fulltext=true) to get the full paper text, then extract_claims. "
                    "Focus on extracting: (a) theoretical arguments and frameworks proposed, "
                    "(b) key constructs and definitions, (c) empirical findings that support/challenge theories, "
                    "(d) research gaps and limitations identified, (e) boundary conditions discussed. "
                    "Use claim_type: 'theory' for theoretical arguments, 'finding' for evidence, "
                    "'gap' for research gaps, 'method' for methodological insights. "
                    "Process papers one at a time. Target: {min_claims}+ claims from {min_deep_read_papers}+ papers. "
                    "DO NOT STOP until you have processed ALL selected papers."
                ),
            },
            {
                "name": "brancher",
                "prompt": "brancher",
                "objective": (
                    "Cross-domain search for: {topic}. "
                    "Search for papers from adjacent theoretical fields that could inform "
                    "the conceptual framework. Look for: analogous frameworks in other domains, "
                    "competing theories, and methodological insights for proposition testing. "
                    "Use search_semantic_scholar, search_crossref, etc. with cross-disciplinary queries."
                ),
            },
            {
                "name": "hypothesis",
                "prompt": "hypothesis",
                "objective": (
                    "Identify the core theoretical gap and propose 3-5 candidate frameworks for: {topic}. "
                    "You have {claim_count} theoretical claims from {papers_with_claims} deeply-read papers. "
                    "Each framework should be a TYPOLOGY, PROCESS MODEL, or MULTI-LEVEL FRAMEWORK. "
                    "Score each: novelty (2x weight), feasibility, evidence_strength, methodology_fit, "
                    "impact, reproducibility. Answer the Five Questions for the top framework. "
                    "Use score_hypothesis to evaluate each one. "
                    "Ground every framework in specific claims from the database — do NOT theorize beyond the evidence."
                ),
            },
            {
                "name": "critic",
                "prompt": "critic",
                "objective": (
                    "FIRST: Call list_claims() to load all extracted evidence. "
                    "You have {claim_count} claims from {papers_with_claims} deeply-read papers. "
                    "Use search_similar() to find the most relevant papers for the framework. "
                    "Read 3-5 key theoretical papers using read_paper(paper_id=ID, include_fulltext=true) to ground your evaluation. "
                    "THEN evaluate the top framework across 8 dimensions. "
                    "Verify the novelty framework label (INVERSION/MISSING LINK/MODERATOR/etc). "
                    "Apply the meta-test: would a management scholar believe something different? "
                    "Decide: APPROVE or REJECT with detailed feedback."
                ),
            },
            {
                "name": "synthesis",
                "prompt": "synthesis",
                "objective": (
                    "Prepare structured theoretical data for the writer. "
                    "You have {claim_count} claims from {papers_with_claims} deeply-read papers. "
                    "FIRST: Call list_claims() to load all extracted evidence. "
                    "Call list_papers(compact=true) to get exact author names. "
                    "Use search_similar() per theme to find relevant papers via embeddings. "
                    "For top 5-10 papers, call read_paper(paper_id=ID, include_fulltext=true). "
                    "Build these outputs: "
                    "(1) Theoretical streams table with author names and core arguments, "
                    "(2) Theoretical tension map showing conflicts/gaps between streams, "
                    "(3) Construct definitions table, (4) Proposition evidence map with supporting/opposing papers, "
                    "(5) Citation map by section with (Author, Year), "
                    "(6) Boundary conditions analysis, (7) Competing frameworks comparison table, "
                    "(8) Novel contribution statement. "
                    "Save ALL tables using write_section(section='synthesis_data', content=...) so the writer can load them."
                ),
            },
        ]

    def _writer_sections(self) -> list[tuple[str, str]]:
        """Build writer section instructions from config word/citation targets."""
        c = self.config
        if c.paper_type == "conceptual":
            return self._writer_sections_conceptual()
        return self._writer_sections_review()

    def _writer_sections_review(self) -> list[tuple[str, str]]:
        """Writer sections for systematic literature review."""
        c = self.config
        return [
            ("abstract", (
                f"Write a structured abstract ({c.words_abstract}+ words). Include: Background, Objective, Methods, Results, Conclusion. "
                "Use (Author, Year) citations. Call list_papers first."
            )),
            ("introduction", (
                f"Write the introduction ({c.words_introduction}+ words). Include: background with {c.cites_introduction}+ citations, "
                "research gap, research questions."
            )),
            ("literature_review", (
                f"Write the literature review ({c.words_literature_review}+ words). Thematic organization, {c.cites_literature_review}+ citations. "
                "Cross-reference findings across papers."
            )),
            ("methods", (
                f"Write the methods section ({c.words_methods}+ words). Include: search strategy, "
                "inclusion/exclusion criteria, quality assessment approach."
            )),
            ("results", (
                f"Write the results section ({c.words_results}+ words). Include: study characteristics, "
                f"thematic results by research question, {c.cites_results}+ citations."
            )),
            ("discussion", (
                f"Write the discussion ({c.words_discussion}+ words). Include: key findings summary, "
                "comparison with existing work, limitations, future directions."
            )),
            ("conclusion", (
                f"Write the conclusion ({c.words_conclusion}+ words). Include: main contributions, "
                "key takeaways, future questions."
            )),
        ]

    def _writer_sections_conceptual(self) -> list[tuple[str, str]]:
        """Writer sections for conceptual/theoretical paper."""
        c = self.config
        return [
            ("abstract", (
                f"Write a structured abstract ({c.words_abstract}+ words). Include: Purpose, "
                "Design/Approach, Findings (framework + key propositions), Originality/Value. "
                "Be PRECISE about contribution — do not overclaim. Acknowledge that prior work "
                "has addressed related questions. "
                "State what THIS paper adds: the integrative framework connecting them. "
                "Use (Author, Year) citations. Call list_papers first."
            )),
            ("introduction", (
                f"Write the introduction ({c.words_introduction}+ words). Include: opening hook, "
                f"the theoretical puzzle, explicit gap statement, framework preview, "
                f"contribution statement (theory + practice), {c.cites_introduction}+ citations. "
                f"Be PRECISE about contribution claims — acknowledge related prior work "
                f"before stating what THIS paper adds."
            )),
            ("theoretical_background", (
                f"Write the theoretical background ({c.words_theoretical_background}+ words). "
                f"Cover 3+ theoretical streams as subsections. For each: foundational works, "
                f"key developments, current state, AND limitations. "
                f"Show where streams converge and conflict. {c.cites_literature_review}+ citations. "
                f"Search list_papers for foundational authors in the field. "
                f"End with transition to framework development."
            )),
            ("framework", (
                f"Write the framework development section ({c.words_framework}+ words). "
                "This section describes the CONCEPTUAL MODEL — do NOT include formal propositions here "
                "(those go in the next section). Include: "
                "(a) Typology with classification table, "
                "(b) Process model showing stages/mechanisms with text-based diagram, "
                "(c) Multi-level framework overview (antecedents → mechanisms → outcomes) explaining "
                "the logic of each level and how they connect. "
                "Use domain-specific examples throughout. "
                "Ground every element in the theoretical background. 15+ citations."
            )),
            ("propositions", (
                f"Write the propositions section ({c.words_propositions}+ words). "
                "This section contains the FORMAL TESTABLE PROPOSITIONS derived from the framework "
                "described in the previous section. Do NOT repeat the framework description. "
                "Present 5-8 formal propositions. For EACH: "
                "formal statement ('Proposition N: ...'), 2-3 paragraphs of theoretical justification, "
                "supporting evidence from literature, boundary conditions. "
                "Identify the most novel proposition and give it its OWN subsection — "
                "operationalize the construct precisely with domain-specific examples. "
                "At least 2 counter-intuitive propositions that challenge conventional wisdom. "
                "End with a summary table of all propositions. 3+ citations per proposition."
            )),
            ("discussion", (
                f"Write the discussion ({c.words_discussion}+ words). Include: "
                "theoretical contributions (how framework extends each stream), "
                "comparison table with 3+ existing frameworks, "
                "specific managerial implications, boundary conditions/limitations, "
                "5+ future research studies with suggested methodologies. {c.cites_discussion}+ citations."
            )),
            ("conclusion", (
                f"Write the conclusion ({c.words_conclusion}+ words). Include: "
                "framework's core logic summary, key takeaways for theory and practice, "
                "the single most important insight, closing statement on broader significance."
            )),
        ]

    def run_pipeline(
        self,
        topic: str,
        paper_type: str,
        context: ExternalContext,
        on_event: StepCallback | None = None,
    ) -> str:
        """Run the full research pipeline programmatically — deterministic phase sequence."""
        _log.info("=" * 60)
        _log.info("PIPELINE START — topic: %s | type: %s", topic[:100], paper_type)
        _log.info("=" * 60)

        db = self.tools.db
        session_id = self.tools.session_id
        completed: set[str] = set()
        if db and session_id:
            completed = db.get_completed_phases(session_id)
            if completed:
                _log.info("PIPELINE: Resuming — completed phases: %s", completed)

        start_time = time.time()

        # Run main phases
        for phase_def in self._get_pipeline_phases():
            if self.cancel_flag.is_set():
                _log.info("PIPELINE: Cancelled")
                break

            elapsed = time.time() - start_time
            if elapsed > self.config.max_solve_seconds:
                _log.warning("PIPELINE: Timeout after %ds", int(elapsed))
                break

            name = phase_def["name"]
            if name in completed:
                _log.info("PIPELINE: Skipping %s (checkpoint found)", name)
                if on_event:
                    on_event(StepEvent("text", data=f"Skipping {name} (already completed)", depth=0))
                continue

            _log.info("=" * 40)
            _log.info("PIPELINE PHASE: %s", name)
            _log.info("=" * 40)

            if on_event:
                on_event(StepEvent("subtask_start", data=f"Phase: {name}", depth=0))

            try:
                if name == "embed":
                    self._pipeline_embed(on_event)
                elif name == "fetch_texts":
                    self._pipeline_fetch_texts(on_event)
                elif name == "snowball":
                    self._pipeline_snowball(topic, on_event)
                elif name == "triage":
                    self._pipeline_triage(topic, context, on_event)
                elif name == "protocol" and "verifier" not in completed:
                    # Run protocol + verifier concurrently — they're independent
                    verifier_def = next(
                        (p for p in self._get_pipeline_phases() if p["name"] == "verifier"), None
                    )
                    if verifier_def:
                        _log.info("PIPELINE: Running protocol + verifier in parallel")
                        if on_event:
                            on_event(StepEvent("text", data="Running protocol + verifier in parallel", depth=0))

                        def _run_protocol():
                            self._pipeline_run_phase(phase_def, topic, paper_type, context, on_event)

                        def _run_verifier():
                            self._pipeline_run_phase(verifier_def, topic, paper_type, context, on_event)

                        with ThreadPoolExecutor(max_workers=2) as pool:
                            futures = [pool.submit(_run_protocol), pool.submit(_run_verifier)]
                            for fut in as_completed(futures):
                                try:
                                    fut.result()
                                except Exception as exc:
                                    _log.exception("PIPELINE: Parallel phase failed: %s", exc)

                        # Mark verifier as completed so it's skipped in the main loop
                        if db and session_id:
                            db.save_phase_checkpoint(session_id, "verifier")
                        completed.add("verifier")
                    else:
                        self._pipeline_run_phase(phase_def, topic, paper_type, context, on_event)
                else:
                    self._pipeline_run_phase(phase_def, topic, paper_type, context, on_event)
            except RateLimitError as exc:
                _log.error("PIPELINE: Rate limit at phase %s: %s", name, exc)
                if on_event:
                    on_event(StepEvent("error", data=f"Rate limit at {name}: {exc}", depth=0))
                break
            except Exception as exc:
                _log.exception("PIPELINE: Phase %s failed: %s", name, exc)
                if on_event:
                    on_event(StepEvent("error", data=f"Phase {name} failed: {exc}", depth=0))
                # Continue to next phase — don't stop the whole pipeline

            # After deep_read: run batched continuation until targets met
            if name == "deep_read" and db and session_id:
                claims = db.get_claims(session_id)
                papers_with_claims = len(set(c["paper_id"] for c in claims))
                _log.info("PIPELINE: Deep read batch 1 produced %d claims from %d papers", len(claims), papers_with_claims)

                # Batched deep_read: keep launching batches of 15 papers until targets met
                _BATCH_SIZE = 15
                _MAX_BATCHES = 12  # 12 batches × 15 papers = 180 papers max
                _BATCH_COOLDOWN = 15  # seconds between batches for rate limit recovery

                for batch_num in range(1, _MAX_BATCHES + 1):
                    if len(claims) >= self.config.min_claims and papers_with_claims >= self.config.min_deep_read_papers:
                        _log.info("PIPELINE: Deep read targets met (%d claims, %d papers)", len(claims), papers_with_claims)
                        break
                    if self.cancel_flag.is_set():
                        break

                    _log.info(
                        "PIPELINE: Deep read batch %d/%d — %d claims from %d papers so far (need %d from %d)",
                        batch_num + 1, _MAX_BATCHES + 1, len(claims), papers_with_claims,
                        self.config.min_claims, self.config.min_deep_read_papers,
                    )
                    if on_event:
                        on_event(StepEvent("text", data=(
                            f"Deep read batch {batch_num + 1}: {len(claims)} claims from "
                            f"{papers_with_claims} papers — processing next {_BATCH_SIZE}"
                        ), depth=0))

                    # Cooldown between batches to let rate limits recover
                    if batch_num > 0:
                        _log.info("PIPELINE: Cooling down %ds between deep_read batches", _BATCH_COOLDOWN)
                        time.sleep(_BATCH_COOLDOWN)

                    batch_objective = (
                        f"CONTINUE extracting claims from papers about {topic}. "
                        f"You have {len(claims)} claims from {papers_with_claims} papers so far. "
                        f"Target: {self.config.min_claims}+ claims from {self.config.min_deep_read_papers}+ papers. "
                        f"Call list_papers(selected_only=true) to see ALL selected papers. "
                        f"Skip papers you already processed (call list_claims() to see which paper_ids have claims). "
                        f"Process the NEXT {_BATCH_SIZE} unprocessed papers. "
                        f"For each: read_paper(paper_id=ID, include_fulltext=true) → extract_claims with EXACT quotes → assess_risk_of_bias. "
                        f"Stop after processing {_BATCH_SIZE} papers — another batch will follow."
                    )
                    batch_phase = {
                        "name": "deep_read",
                        "prompt": "analyst_deep_read",
                        "objective": batch_objective,
                    }
                    try:
                        self._pipeline_run_phase(batch_phase, topic, paper_type, context, on_event)
                    except Exception as exc:
                        _log.exception("PIPELINE: Deep read batch %d failed: %s", batch_num + 1, exc)

                    # Recount after each batch
                    prev_claims = len(claims)
                    claims = db.get_claims(session_id)
                    papers_with_claims = len(set(c["paper_id"] for c in claims))
                    new_claims = len(claims) - prev_claims
                    _log.info("PIPELINE: Batch %d done: +%d claims, total %d from %d papers",
                              batch_num + 1, new_claims, len(claims), papers_with_claims)

                    # If a batch produced 0 new claims, increase cooldown (likely rate limited)
                    if new_claims == 0:
                        extra_cooldown = 45
                        _log.warning("PIPELINE: Batch %d produced 0 claims — extra cooldown %ds", batch_num + 1, extra_cooldown)
                        time.sleep(extra_cooldown)

                # HARD GATE: If still 0 claims, this is fatal
                if len(claims) == 0:
                    _log.error("PIPELINE: FATAL — 0 claims after all deep_read batches. Cannot produce a credible paper.")
                    if on_event:
                        on_event(StepEvent("error", data="FATAL: 0 claims extracted. Pipeline cannot continue without evidence.", depth=0))
                    break

                # Store claim/paper counts in context for downstream phases
                context.claim_count = len(claims)
                context.papers_with_claims = papers_with_claims

                # Re-embed: papers may have gained full text during deep_read
                _log.info("PIPELINE: Re-embedding papers after deep_read (full text may have changed)")
                if on_event:
                    on_event(StepEvent("text", data="Re-embedding papers with updated full text...", depth=0))
                self._pipeline_embed(on_event)

                # Deduplicate claims
                self._deduplicate_claims(db, session_id)

                if paper_type != "conceptual":
                    self._finalize_prisma_exclusions(db, session_id)

            if db and session_id:
                db.save_phase_checkpoint(session_id, name)
            if on_event:
                on_event(StepEvent("subtask_end", data=f"Phase {name} complete", depth=0))

        # Writer — section by section
        if "writer" not in completed and not self.cancel_flag.is_set():
            self._pipeline_writer(topic, paper_type, context, on_event)
            if db and session_id:
                db.save_phase_checkpoint(session_id, "writer")

        # Pre-critic programmatic validation — fix obvious failures before LLM critic
        if "paper_critic" not in completed and not self.cancel_flag.is_set():
            self._pre_critic_validation(topic, paper_type, context, on_event)

        # Paper critic
        if "paper_critic" not in completed and not self.cancel_flag.is_set():
            self._pipeline_paper_critic(topic, paper_type, context, on_event)
            if db and session_id:
                db.save_phase_checkpoint(session_id, "paper_critic")

        # Generate references
        if not self.cancel_flag.is_set():
            self._pipeline_references(context, on_event)

        _log.info("=" * 60)
        _log.info("PIPELINE COMPLETE — elapsed: %ds", int(time.time() - start_time))
        _log.info("=" * 60)

        return "Pipeline complete"

    def _pipeline_run_phase(
        self, phase_def: dict, topic: str, paper_type: str,
        context: ExternalContext, on_event: StepCallback | None,
    ) -> str:
        """Run a single LLM-driven pipeline phase."""
        prompt_name = phase_def["prompt"]
        # Build special author instruction from config
        special_authors = self.config.special_authors
        if special_authors:
            special_authors_instruction = f"ALSO search specifically for foundational authors: {special_authors}. "
        else:
            special_authors_instruction = ""

        objective = phase_def["objective"].format(
            topic=topic, paper_type=paper_type,
            min_claims=self.config.min_claims,
            min_deep_read_papers=self.config.min_deep_read_papers,
            min_papers=self.config.min_papers,
            search_start_year=self.config.search_start_year,
            claim_count=context.claim_count,
            papers_with_claims=context.papers_with_claims,
            special_authors_instruction=special_authors_instruction,
            special_instructions=self.config.special_instructions,
        )

        # Reset search state for search phases
        if prompt_name in ("scout", "brancher"):
            from .tools.search import reset_search_all_state
            reset_search_all_state()

        system_prompt = build_phase_system_prompt(
            phase=prompt_name,
            topic=topic,
            rules=context.rules,
            paper_type=paper_type,
        )

        # Use per-phase step budget if defined
        step_budget = self._phase_step_budgets().get(phase_def["name"], self.config.max_steps_per_call)

        result = self._solve_recursive(
            objective=objective,
            context=context,
            depth=self.config.max_depth,  # No subtask — direct tool use only
            on_event=on_event,
            system_prompt_override=system_prompt,
            phase=prompt_name,
            max_steps=step_budget,
        )
        _log.info("PIPELINE: Phase %s completed — %d chars", phase_def["name"], len(result))
        return result

    def _pipeline_fetch_texts(self, on_event: StepCallback | None) -> None:
        """Batch fetch full text from 6 sources — no LLM needed."""
        _log.info("PIPELINE: Running batch full-text fetch")
        if on_event:
            on_event(StepEvent("text", data="Fetching full text from 6 sources (S2, CORE, OpenAlex, EPMC, PMC, Unpaywall)...", depth=0))
        result = self.tools.dispatch("batch_fetch_fulltext", {})
        _log.info("PIPELINE: Full-text fetch result: %s", result[:300])
        if on_event:
            on_event(StepEvent("tool_result", data=result[:300], tool_name="batch_fetch_fulltext", depth=0))

        # Check coverage and warn
        try:
            import json as _json
            data = _json.loads(result)
            fetched = data.get("fetched", 0)
            total = data.get("total_needed", 0)
            coverage_pct = (fetched / total * 100) if total > 0 else 0
            if coverage_pct < 20:
                _log.warning("PIPELINE: LOW FULLTEXT COVERAGE (%.1f%%) — %d/%d papers. "
                             "Deep read will rely heavily on abstracts. Paper quality may be reduced.",
                             coverage_pct, fetched, total)
                if on_event:
                    on_event(StepEvent("error", data=f"WARNING: Low full-text coverage ({coverage_pct:.0f}%). "
                                                     f"Only {fetched}/{total} papers have full text. "
                                                     f"Deep read will rely on abstracts for remaining papers.", depth=0))
        except Exception:
            pass

    def _pipeline_embed(self, on_event: StepCallback | None) -> None:
        """Run embedding phase programmatically — no LLM needed."""
        _log.info("PIPELINE: Running batch embedding")
        if on_event:
            on_event(StepEvent("text", data="Embedding all papers...", depth=0))
        result = self.tools.dispatch("batch_embed_papers", {})
        _log.info("PIPELINE: Embedding result: %s", result[:200])
        if on_event:
            on_event(StepEvent("tool_result", data=result[:200], tool_name="batch_embed_papers", depth=0))

    def _pipeline_snowball(self, topic: str, on_event: StepCallback | None) -> None:
        """Reference snowballing — pull references of top cited papers."""
        _log.info("PIPELINE: Running reference snowballing")
        if on_event:
            on_event(StepEvent("text", data="Snowballing references from top papers...", depth=0))
        result = self.tools.dispatch("snowball_references", {
            "limit": self.config.snowball_top_papers,
            "refs_per_paper": self.config.snowball_refs_per_paper,
        })
        _log.info("PIPELINE: Snowball result: %s", result[:200])
        if on_event:
            on_event(StepEvent("tool_result", data=result[:200], tool_name="snowball_references", depth=0))

    @property
    def _TRIAGE_BATCH_SIZE(self) -> int:
        return self.config.triage_batch_size

    def _pipeline_triage(
        self, topic: str, context: ExternalContext, on_event: StepCallback | None,
    ) -> None:
        """Run batched triage — each batch evaluates ~40 papers."""
        _log.info("PIPELINE: Starting batched triage")

        db = self.tools.db
        session_id = self.tools.session_id
        if not db or not session_id:
            _log.error("PIPELINE TRIAGE: No DB or session")
            return

        total = db.paper_count(session_id)
        _log.info("PIPELINE TRIAGE: %d papers to triage", total)

        system_prompt = build_phase_system_prompt(
            phase="analyst_triage", topic=topic, rules=context.rules,
            paper_type=self.config.paper_type,
        )

        batch_num = 0
        offset = 0
        while offset < total:
            if self.cancel_flag.is_set():
                break

            batch_num += 1
            _log.info("PIPELINE TRIAGE: Batch %d (offset=%d)", batch_num, offset)
            if on_event:
                on_event(StepEvent("text", data=f"Triage batch {batch_num} (papers {offset}-{offset + self._TRIAGE_BATCH_SIZE})", depth=0))

            objective = (
                f"Triage batch {batch_num}: "
                f"1. Call list_papers(limit={self._TRIAGE_BATCH_SIZE}, offset={offset}). "
                f"2. For EACH paper, score relevance 0.0-1.0 to: {topic}. "
                f"3. You MUST call rate_papers with ALL ratings. Set selected=true for score >= 0.6. "
                f"Score < 0.3 for unrelated papers. "
                f"You MUST call rate_papers even if no papers are relevant — rate them all as low."
            )

            self._solve_recursive(
                objective=objective,
                context=context,
                depth=self.config.max_depth,
                on_event=on_event,
                system_prompt_override=system_prompt,
                phase="analyst_triage",
                max_steps=self.config.triage_step_budget,
            )
            offset += self._TRIAGE_BATCH_SIZE

        # Report
        selected_count = 0
        try:
            row = db._conn.execute(
                "SELECT COUNT(*) FROM papers WHERE session_id = ? AND selected_for_deep_read = 1",
                (session_id,),
            ).fetchone()
            selected_count = row[0] if row else 0
        except Exception:
            pass
        _log.info("PIPELINE TRIAGE: Complete — %d papers selected for deep read out of %d total", selected_count, total)

        # Store PRISMA stats for screening stage
        if db and session_id:
            db.store_prisma_stat(session_id, "records_identified", total)
            db.store_prisma_stat(session_id, "records_screened", total)
            db.store_prisma_stat(session_id, "excluded_screening", total - selected_count)
            db.store_prisma_stat(session_id, "fulltext_assessed", selected_count)

        if on_event:
            on_event(StepEvent("text", data=f"Triage complete: {selected_count}/{total} papers selected for deep reading", depth=0))

    def _pipeline_writer(
        self, topic: str, paper_type: str,
        context: ExternalContext, on_event: StepCallback | None,
    ) -> None:
        """Run writer phase — one subtask per section for reliability."""
        writer_sections = self._writer_sections()
        _log.info("PIPELINE: Starting writer phase — %d sections", len(writer_sections))

        # Check which sections already exist
        ws = self.config.workspace
        sections_dir = ws / self.config.session_root_dir / "output" / "sections"
        existing_sections = set()
        if sections_dir.exists():
            for f in sections_dir.iterdir():
                if f.suffix == ".md" and f.stat().st_size > 100:
                    existing_sections.add(f.stem)

        system_prompt = build_phase_system_prompt(
            phase="writer", topic=topic, rules=context.rules,
            paper_type=paper_type,
        )

        for section_name, instruction in writer_sections:
            if self.cancel_flag.is_set():
                break

            if section_name in existing_sections:
                _log.info("PIPELINE WRITER: Skipping %s (already exists)", section_name)
                if on_event:
                    on_event(StepEvent("text", data=f"Skipping {section_name} (exists)", depth=0))
                continue

            _log.info("PIPELINE WRITER: Writing section — %s", section_name)
            if on_event:
                on_event(StepEvent("subtask_start", data=f"Writing: {section_name}", depth=0))

            objective = (
                f"Write the '{section_name}' section for the research paper on: {topic}. "
                f"{instruction} "
                f"MANDATORY FIRST: Call list_claims() to load extracted evidence, then list_papers(compact=true) for citation formatting. "
                f"Use search_similar(text='<section theme>') to find the most relevant papers for this section. "
                f"For the 2-3 most important papers in this section, call read_paper(paper_id=ID, include_fulltext=true). "
                f"Use write_section(section='{section_name}', content=YOUR_TEXT) to save. "
                f"Do NOT use markdown headers at the start — the system adds them. "
                f"Every factual statement must cite a paper verified via list_claims or list_papers."
            )

            result = self._solve_recursive(
                objective=objective,
                context=context,
                depth=self.config.max_depth,
                on_event=on_event,
                system_prompt_override=system_prompt,
                phase="writer",
            )
            _log.info("PIPELINE WRITER: Section %s done — %d chars", section_name, len(result))

            # Inline section critic — programmatic check, rewrite if failed
            section_issues = self._check_section_quality(section_name, sections_dir, context)
            if section_issues and not self.cancel_flag.is_set():
                _log.warning("PIPELINE WRITER: Section %s failed inline checks: %s", section_name, section_issues)
                if on_event:
                    on_event(StepEvent("text", data=f"Section {section_name} needs rewrite: {section_issues[0]}", depth=0))

                rewrite_objective = (
                    f"REWRITE the '{section_name}' section. The following issues were found:\n"
                    + "\n".join(f"- {issue}" for issue in section_issues) + "\n\n"
                    f"FIRST: Call list_claims() to load extracted evidence, then list_papers(compact=true) for citation formatting. "
                    f"Use search_similar(text='<section theme>') to find relevant papers. "
                    f"Read the current version, fix ALL issues, and save using "
                    f"write_section(section='{section_name}', content=YOUR_TEXT). "
                    f"Do NOT shorten the section — only expand and fix."
                )
                self._solve_recursive(
                    objective=rewrite_objective,
                    context=context,
                    depth=self.config.max_depth,
                    on_event=on_event,
                    system_prompt_override=system_prompt,
                    phase="writer",
                )
                _log.info("PIPELINE WRITER: Section %s rewrite complete", section_name)

            if on_event:
                on_event(StepEvent("subtask_end", data=f"Section {section_name} done", depth=0))

    def _check_section_quality(
        self, section_name: str, sections_dir: Path, context: ExternalContext,
    ) -> list[str]:
        """Programmatic section quality check — returns list of issues (empty = pass)."""
        import re as _re
        from .tools.writing import _extract_citations_from_text, _verify_citation_against_db

        section_file = sections_dir / f"{section_name}.md"
        if not section_file.exists():
            return [f"Section file '{section_name}.md' was not saved"]

        content = section_file.read_text(encoding="utf-8")
        word_count = len(content.split())
        issues: list[str] = []

        # 1. Word count check
        cfg = self.config
        min_words_map = {
            "abstract": cfg.words_abstract, "introduction": cfg.words_introduction,
            "literature_review": cfg.words_literature_review, "methods": cfg.words_methods,
            "results": cfg.words_results, "discussion": cfg.words_discussion,
            "conclusion": cfg.words_conclusion,
            "theoretical_background": cfg.words_theoretical_background,
            "framework": cfg.words_framework, "propositions": cfg.words_propositions,
        }
        min_words = min_words_map.get(section_name, 0)
        if min_words > 0 and word_count < int(min_words * 0.8):
            issues.append(f"Too short: {word_count} words (need {min_words}+, hard floor {int(min_words * 0.8)})")

        # 2. Citation count check (skip abstract/conclusion/protocol)
        if section_name not in ("abstract", "conclusion", "protocol"):
            citations = _extract_citations_from_text(content)
            if len(citations) == 0:
                issues.append("Zero citations — every body section must cite papers from the database")
            elif len(citations) < 3 and section_name not in ("methods",):
                issues.append(f"Only {len(citations)} citations — need at least 3 for this section")

            # 3. Citation verification — check >50% are real
            db = self.tools.db
            session_id = self.tools.session_id
            if db and session_id and citations:
                verified = sum(
                    1 for a, y in citations
                    if _verify_citation_against_db(a, y, db, session_id).get("verified")
                )
                if len(citations) > 0 and verified / len(citations) < 0.5:
                    issues.append(
                        f"Citation integrity: only {verified}/{len(citations)} verified in DB. "
                        f"Use list_papers to find real (Author, Year) pairs."
                    )

        # 4. Citation density — no paragraph >300 words without a citation
        if section_name not in ("abstract", "conclusion", "protocol"):
            paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 50]
            long_uncited = 0
            for para in paragraphs:
                para_words = len(para.split())
                para_cites = _extract_citations_from_text(para)
                if para_words > 300 and len(para_cites) == 0:
                    long_uncited += 1
            if long_uncited > 0:
                issues.append(f"{long_uncited} paragraph(s) over 300 words with no citations")

        # 5. Duplication check — compare with other sections (>60% overlap = problem)
        _COMMON_ACADEMIC_WORDS = {
            "the", "and", "of", "to", "in", "a", "is", "that", "for", "was", "on", "are", "with",
            "as", "this", "by", "from", "be", "have", "an", "has", "their", "been", "were", "or",
            "which", "not", "its", "also", "it", "more", "between", "these", "than", "other",
            "study", "studies", "research", "findings", "results", "evidence", "paper", "review",
            "analysis", "data", "based", "found", "literature", "may", "can", "however", "al",
            "et", "significant", "associated", "effect", "effects", "participants", "reported",
        }
        if sections_dir.exists():
            content_words = set(content.lower().split()) - _COMMON_ACADEMIC_WORDS
            for other_file in sections_dir.iterdir():
                if other_file.suffix == ".md" and other_file.stem != section_name and other_file.stat().st_size > 200:
                    other_words = set(other_file.read_text(encoding="utf-8").lower().split()) - _COMMON_ACADEMIC_WORDS
                    if content_words and other_words:
                        overlap = len(content_words & other_words) / min(len(content_words), len(other_words))
                        if overlap > 0.6:
                            issues.append(f"High overlap ({overlap:.0%}) with '{other_file.stem}' — likely duplication")

        return issues

    def _pre_critic_validation(
        self, topic: str, paper_type: str,
        context: ExternalContext, on_event: StepCallback | None,
    ) -> None:
        """Programmatic pre-critic scan — fix obvious failures before wasting an LLM critic call."""
        from pathlib import Path
        ws = self.config.workspace
        sections_dir = ws / self.config.session_root_dir / "output" / "sections"
        if not sections_dir.exists():
            _log.warning("PRE-CRITIC: No sections directory — skipping validation")
            return

        # Gather all section files
        section_files = {f.stem: f for f in sections_dir.iterdir() if f.suffix == ".md" and f.stat().st_size > 50}

        # Determine expected sections
        if paper_type == "conceptual":
            expected = {"abstract", "introduction", "theoretical_background", "framework", "propositions", "discussion", "conclusion"}
        else:
            expected = {"abstract", "introduction", "literature_review", "methods", "results", "discussion", "conclusion"}

        missing = expected - set(section_files.keys())
        if missing:
            _log.warning("PRE-CRITIC: Missing sections: %s", missing)

        # Check each existing section and collect failures
        failing_sections: list[tuple[str, list[str]]] = []
        total_words = 0
        total_citations: set[tuple[str, str]] = set()

        from .tools.writing import _extract_citations_from_text

        for section_name in expected:
            if section_name not in section_files:
                continue
            issues = self._check_section_quality(section_name, sections_dir, context)
            content = section_files[section_name].read_text(encoding="utf-8")
            total_words += len(content.split())
            total_citations.update(_extract_citations_from_text(content))
            if issues:
                failing_sections.append((section_name, issues))

        _log.info("PRE-CRITIC: total_words=%d, unique_citations=%d, failing_sections=%d/%d",
                   total_words, len(total_citations), len(failing_sections), len(expected))

        if not failing_sections:
            _log.info("PRE-CRITIC: All sections pass programmatic checks — proceeding to LLM critic")
            return

        # Auto-rewrite failing sections (max 3 to avoid infinite loop)
        if on_event:
            on_event(StepEvent("text", data=f"Pre-critic: {len(failing_sections)} sections need fixes before critic review", depth=0))

        system_prompt = build_phase_system_prompt(
            phase="writer", topic=topic, rules=context.rules,
            paper_type=paper_type,
        )

        for section_name, issues in failing_sections[:3]:
            if self.cancel_flag.is_set():
                break
            _log.info("PRE-CRITIC: Auto-rewriting section '%s' — issues: %s", section_name, issues)
            if on_event:
                on_event(StepEvent("subtask_start", data=f"Pre-critic fix: {section_name}", depth=0))

            rewrite_objective = (
                f"REWRITE the '{section_name}' section to fix these issues found by automated checks:\n"
                + "\n".join(f"- {issue}" for issue in issues) + "\n\n"
                f"FIRST: Call list_claims() to load extracted evidence, then list_papers(compact=true) for citation formatting. "
                f"Use search_similar(text='<section theme>') to find relevant papers. "
                f"Fix ALL issues and save using write_section(section='{section_name}', content=YOUR_TEXT). "
                f"Do NOT shorten the section — only expand and fix."
            )
            self._solve_recursive(
                objective=rewrite_objective,
                context=context,
                depth=self.config.max_depth,
                on_event=on_event,
                system_prompt_override=system_prompt,
                phase="writer",
            )
            if on_event:
                on_event(StepEvent("subtask_end", data=f"Pre-critic fix done: {section_name}", depth=0))

    def _pipeline_paper_critic(
        self, topic: str, paper_type: str,
        context: ExternalContext, on_event: StepCallback | None,
    ) -> None:
        """Run paper critic with revision loop (max 3 cycles)."""
        system_prompt = build_phase_system_prompt(
            phase="paper_critic", topic=topic, rules=context.rules,
            paper_type=paper_type,
        )

        # Pre-check: count actually available citable papers and set dynamic threshold
        db = self.tools.db
        session_id = self.tools.session_id
        available_citations = 0
        if db and session_id:
            try:
                # Count papers with claims (these are the ones the writer can actually cite)
                available_citations = db._conn.execute(
                    "SELECT COUNT(DISTINCT paper_id) FROM claims WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
            except Exception:
                pass
        # Dynamic threshold: 60% of available papers, but at least 15 and at most the configured max
        dynamic_citation_min = max(15, min(self.config.min_quality_citations, int(available_citations * 0.6)))
        _log.info("PIPELINE CRITIC: available_citations=%d, dynamic_threshold=%d (config=%d)",
                   available_citations, dynamic_citation_min, self.config.min_quality_citations)

        for cycle in range(self.config.paper_critic_max_revisions + 1):
            if self.cancel_flag.is_set():
                break

            _log.info("PIPELINE CRITIC: Cycle %d/%d", cycle + 1, self.config.paper_critic_max_revisions + 1)
            if on_event:
                on_event(StepEvent("subtask_start", data=f"Paper critic cycle {cycle + 1}", depth=0))

            if paper_type == "conceptual":
                objective = (
                    "Evaluate the complete conceptual paper draft against AMJ/JIBS/SMJ standards. "
                    "Call generate_quality_audit to get the scorecard. Score 12 dimensions. "
                    f"The database has {available_citations} citable papers with extracted claims. "
                    f"Check ALL minimum thresholds ({dynamic_citation_min}+ unique citations, "
                    f"{self.config.min_paper_words}+ words, "
                    "all sections present, 5+ propositions, 3+ theoretical streams, "
                    "3+ framework comparisons, 5+ future research studies, boundary conditions). "
                    "ALSO CHECK: (1) domain specificity — every section must use topic-relevant examples, "
                    "not generic examples, (2) NO PRISMA diagram or systematic review methodology, "
                    "(3) framework and propositions sections have DISTINCT content — no duplication, "
                    "(4) at least 2 counter-intuitive propositions. "
                    "If revision needed, specify exactly which sections and what to fix."
                )
            else:
                objective = (
                    "Evaluate the complete paper draft against Nature/Lancet systematic review standards. "
                    "Call generate_quality_audit to get the scorecard. Score 10 dimensions. "
                    f"The database has {available_citations} citable papers with extracted claims. "
                    f"Check ALL minimum thresholds ({dynamic_citation_min}+ unique citations, "
                    f"{self.config.min_paper_words}+ words, {self.config.min_quality_tables}+ tables, "
                    "all sections present, structured abstract, PRISMA in methods, limitations, "
                    "3+ review comparisons, 3+ future questions). "
                    "If revision needed, specify exactly which sections and what to fix."
                )

            result = self._solve_recursive(
                objective=objective,
                context=context,
                depth=self.config.max_depth,
                on_event=on_event,
                system_prompt_override=system_prompt,
                phase="paper_critic",
            )
            if on_event:
                on_event(StepEvent("subtask_end", data=f"Critic cycle {cycle + 1} done", depth=0))

            # Check if revision needed (look for REVISE in result)
            if "APPROVE" in result.upper() or cycle >= self.config.paper_critic_max_revisions:
                _log.info("PIPELINE CRITIC: Paper approved (or max cycles reached)")
                break

            # Run revision pass
            _log.info("PIPELINE CRITIC: Revision needed — running writer revision")
            if on_event:
                on_event(StepEvent("text", data="Paper needs revision — rewriting flagged sections", depth=0))

            writer_prompt = build_phase_system_prompt(
                phase="writer", topic=topic, rules=context.rules,
                paper_type=paper_type,
            )
            revision_objective = (
                f"Revise the paper based on critic feedback below. Follow the exact_fixes VERBATIM — "
                f"the critic has written specific replacement text and citations for you.\n\n"
                f"CRITIC FEEDBACK:\n{result[:5000]}\n\n"
                f"FIRST: Call list_claims() to load extracted evidence, then list_papers(compact=true) for citation formatting. "
                f"INSTRUCTIONS: For each section in sections_needing_revision, read the exact_fixes "
                f"and apply them. Use write_section to save each revised section. "
                f"Do NOT shorten sections. Add the specific citations and text the critic requested."
            )
            self._solve_recursive(
                objective=revision_objective,
                context=context,
                depth=self.config.max_depth,
                on_event=on_event,
                system_prompt_override=writer_prompt,
                phase="writer",
            )

    def _deduplicate_claims(self, db: Any, session_id: int) -> None:
        """Remove duplicate claims (same paper_id + near-identical claim_text)."""
        try:
            claims = db.get_claims(session_id)
            seen: dict[tuple[int, str], int] = {}  # (paper_id, normalized_text) -> claim_id
            duplicates: list[int] = []
            for c in claims:
                # Normalize: lowercase, strip whitespace, first 100 chars
                key = (c["paper_id"], c["claim_text"].lower().strip()[:100])
                if key in seen:
                    duplicates.append(c["claim_id"])
                else:
                    seen[key] = c["claim_id"]
            if duplicates:
                with db._lock:
                    db._conn.execute(
                        f"DELETE FROM claims WHERE claim_id IN ({','.join('?' * len(duplicates))})",
                        duplicates,
                    )
                    db._conn.commit()
                _log.info("PIPELINE: Removed %d duplicate claims", len(duplicates))
        except Exception as exc:
            _log.warning("Claim deduplication failed: %s", exc)

    def _finalize_prisma_exclusions(self, db: Any, session_id: int) -> None:
        """After deep_read: compute full-text exclusions with honest breakdown."""
        try:
            selected_count = db._conn.execute(
                "SELECT COUNT(*) FROM papers WHERE session_id = ? AND selected_for_deep_read = 1",
                (session_id,),
            ).fetchone()[0]
            # Papers with full text available
            selected_with_fulltext = db._conn.execute(
                "SELECT COUNT(*) FROM papers WHERE session_id = ? AND selected_for_deep_read = 1 AND full_text IS NOT NULL",
                (session_id,),
            ).fetchone()[0]
            # Papers that were actually read and yielded claims
            papers_with_claims = db._conn.execute(
                "SELECT COUNT(DISTINCT paper_id) FROM claims WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]

            # Honest breakdown
            no_fulltext_access = selected_count - selected_with_fulltext
            read_but_excluded = max(0, selected_with_fulltext - papers_with_claims)

            db.store_prisma_stat(session_id, "fulltext_not_accessible", no_fulltext_access,
                                "Full text not retrievable from any source (behind paywall or no OA version)")
            db.store_prisma_stat(session_id, "excluded_fulltext", read_but_excluded,
                                "Excluded after full-text review: no extractable claims or irrelevant on deeper reading")
            db.store_prisma_stat(session_id, "included_final", papers_with_claims)
            _log.info("PRISMA FINALIZED: selected=%d, no_fulltext=%d, read_excluded=%d, included=%d",
                       selected_count, no_fulltext_access, read_but_excluded, papers_with_claims)
        except Exception as exc:
            _log.error("PRISMA finalization failed: %s", exc)

    def _pipeline_references(self, context: ExternalContext, on_event: StepCallback | None) -> None:
        """Generate reference list and PRISMA diagram (PRISMA skipped for conceptual papers)."""
        _log.info("PIPELINE: Generating references")
        try:
            self.tools.dispatch("get_citations", {})
            if context.paper_type != "conceptual":
                _log.info("PIPELINE: Generating PRISMA diagram")
                self.tools.dispatch("generate_prisma_diagram", {})
            else:
                _log.info("PIPELINE: Skipping PRISMA diagram (conceptual paper)")
        except Exception as exc:
            _log.error("PIPELINE: Reference generation failed: %s", exc)

    def _solve_recursive(
        self,
        objective: str,
        context: ExternalContext,
        depth: int,
        on_event: StepCallback | None = None,
        system_prompt_override: str | None = None,
        phase: str = "",
        max_steps: int | None = None,
    ) -> str:
        if depth > self.config.max_depth:
            _log.warning("MAX DEPTH %d reached — returning error", self.config.max_depth)
            return json.dumps({"error": f"Max depth {self.config.max_depth} reached"})

        # Select active model based on phase
        active_model = self.writer_model if phase in ("writer", "paper_critic", "synthesis") else self.model
        model_name = getattr(active_model, 'model', 'unknown')
        _log.info("-" * 50)
        _log.info("_solve_recursive START | depth=%d | phase=%s | model=%s", depth, phase or "manager", model_name)
        _log.info("  objective: %s", objective[:150])

        # Build system prompt
        if system_prompt_override:
            system_prompt = system_prompt_override
        else:
            system_prompt = build_system_prompt(
                topic=context.topic,
                paper_type=context.paper_type,
                rules=context.rules,
                include_delegation=depth < self.config.max_depth,
            )

        # Create conversation
        tool_defs = self.tools.get_definitions(
            include_subtask=depth < self.config.max_depth,
            depth=depth,
            phase=phase,
        )
        conversation = active_model.create_conversation(
            system_prompt=system_prompt,
            tool_defs=tool_defs,
        )

        # Inject context from previous turns
        context_text = objective
        if context.observations:
            obs_text = "\n".join(f"- {o}" for o in context.observations[-20:])
            context_text += f"\n\n[Previous observations]\n{obs_text}"

        active_model.append_user_message(conversation, context_text)

        # Main loop
        last_text = ""
        steps = 0
        start_time = time.time()
        _result_cache: dict[str, str] = {}  # Cache: "name:args_json" → result
        _recent_tool_sigs: list[str] = []
        _tool_call_counts: dict[str, int] = {}
        _MAX_REPEAT = 3
        _MAX_SINGLE_TOOL = 30
        step_limit = max_steps if max_steps is not None else self.config.max_steps_per_call

        while steps < step_limit:
            if self.cancel_flag.is_set():
                _log.info("  CANCELLED at step %d", steps)
                return last_text or "[Cancelled]"

            elapsed = time.time() - start_time
            if elapsed > self.config.max_solve_seconds:
                _log.warning("  TIMEOUT at step %d after %ds", steps, int(elapsed))
                return last_text or json.dumps({"error": f"Timeout after {int(elapsed)}s"})

            steps += 1
            _log.info("  step %d/%d | depth=%d | phase=%s | elapsed=%.0fs",
                       steps, step_limit, depth, phase or "manager", elapsed)

            # Generate
            if on_event:
                on_event(StepEvent("thinking", depth=depth))

            try:
                def _on_chunk(text: str) -> None:
                    if on_event:
                        on_event(StepEvent("text", data=text, depth=depth))

                turn = active_model.generate(conversation, on_chunk=_on_chunk)
            except RateLimitError as exc:
                # Rate limit exhausted — propagate up to stop the entire pipeline
                _log.error("Rate limit exhausted at depth %d: %s", depth, exc)
                if on_event:
                    on_event(StepEvent("error", data=str(exc), depth=depth))
                raise  # Let it bubble up to the top-level solve()
            except ModelError as exc:
                _log.error("Model error at depth %d: %s", depth, exc)
                if on_event:
                    on_event(StepEvent("error", data=str(exc), depth=depth))
                return last_text or json.dumps({"error": f"Model error: {exc}"})

            # Track tokens
            if turn.usage:
                self._total_tokens.input_tokens += turn.usage.input_tokens
                self._total_tokens.output_tokens += turn.usage.output_tokens

            if turn.text:
                last_text = turn.text

            # No tool calls → done
            if not turn.tool_calls:
                _log.info("  NO TOOL CALLS at step %d — ending loop | text=%d chars", steps, len(turn.text or ""))
                if turn.text:
                    active_model.append_assistant_turn(conversation, turn)
                break

            # Pre-dedup spam detection: count DUPLICATE calls (same name+args)
            _sig_counts: dict[str, int] = {}
            for tc in turn.tool_calls:
                sig = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
                _sig_counts[sig] = _sig_counts.get(sig, 0) + 1

            loop_detected = False
            for sig, count in _sig_counts.items():
                if count > 3:  # Same exact call repeated 3+ times = spam
                    tool_name = sig.split(":")[0]
                    _log.warning("Gemini sent identical call %r %d times in one turn — forcing stop", tool_name, count)
                    loop_detected = True
                    break

            if loop_detected:
                active_model.append_assistant_turn(conversation, ModelTurn(text=turn.text, usage=turn.usage))
                active_model.append_user_message(
                    conversation,
                    "STOP. You generated duplicate tool calls. "
                    "Summarize your findings and respond with text only.",
                )
                try:
                    final = active_model.generate(conversation, on_chunk=_on_chunk)
                    if final.text:
                        last_text = final.text
                    if final.usage:
                        self._total_tokens.input_tokens += final.usage.input_tokens
                        self._total_tokens.output_tokens += final.usage.output_tokens
                except ModelError:
                    pass
                break

            # Dedup identical calls
            seen_sigs: set[str] = set()
            unique_calls: list[ToolCall] = []
            for tc in turn.tool_calls:
                sig = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
                if sig not in seen_sigs:
                    seen_sigs.add(sig)
                    unique_calls.append(tc)

            # Cap after dedup — strictly 1 tool per turn (serial execution)
            capped_calls = unique_calls[:self.config.max_tool_calls_per_turn]
            dropped = len(unique_calls) - len(capped_calls)
            for tc in capped_calls:
                _log.info("  TOOL CALL: %s(%s)", tc.name, json.dumps(tc.arguments, default=str)[:120])
            if dropped > 0:
                _log.info("  DROPPED %d extra tool calls (serial mode)", dropped)

            # Append assistant turn (with only the capped calls)
            capped_turn = ModelTurn(text=turn.text, tool_calls=capped_calls, usage=turn.usage)
            active_model.append_assistant_turn(conversation, capped_turn)

            # Execute (cache prevents re-execution, no event for cached calls)
            results = self._execute_tools(
                capped_calls, context, depth, on_event, _result_cache, phase=phase,
            )
            active_model.append_tool_results(conversation, results)

            # Tell model about dropped calls so it doesn't re-send them blindly
            if dropped > 0:
                active_model.append_user_message(
                    conversation,
                    f"[System] {dropped} extra tool call(s) were dropped. Execute ONE tool at a time. "
                    "Wait for each result before calling the next tool.",
                )

            # Loop detection across turns — include args hash so subtask(scout) ≠ subtask(triage)
            turn_sig = "|".join(
                f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)[:300]}"
                for tc in capped_calls
            )
            _recent_tool_sigs.append(turn_sig)
            _DELEGATION_TOOLS = {"subtask", "execute"}
            for tc in capped_calls:
                if tc.name not in _DELEGATION_TOOLS:
                    _tool_call_counts[tc.name] = _tool_call_counts.get(tc.name, 0) + 1

            # Check: same EXACT pattern (name+args) 2 turns in a row
            if len(_recent_tool_sigs) >= 2 and _recent_tool_sigs[-1] == _recent_tool_sigs[-2]:
                _log.warning("Loop: pattern repeated 2 turns: %s", turn_sig[:120])
                loop_detected = True

            # Check: any non-delegation tool > MAX total
            if not loop_detected:
                for name, count in _tool_call_counts.items():
                    if count >= _MAX_SINGLE_TOOL:
                        _log.warning("Tool %r hit %d calls", name, count)
                        loop_detected = True
                        break

            if loop_detected:
                active_model.append_user_message(
                    conversation,
                    "STOP. You are looping. Summarize findings and respond with text. No more tools.",
                )
                try:
                    final = active_model.generate(conversation, on_chunk=_on_chunk)
                    if final.text:
                        last_text = final.text
                    if final.usage:
                        self._total_tokens.input_tokens += final.usage.input_tokens
                        self._total_tokens.output_tokens += final.usage.output_tokens
                except ModelError:
                    pass
                break

            # Context condensation check
            estimated_tokens = active_model.estimate_tokens(conversation)
            window = active_model.context_window()
            if estimated_tokens > window * 0.75:
                self._condense(conversation, context, _result_cache, active_model)

        return last_text or "[No response generated]"

    def _execute_tools(
        self,
        tool_calls: list[ToolCall],
        context: ExternalContext,
        depth: int,
        on_event: StepCallback | None,
        result_cache: dict[str, str] | None = None,
        phase: str = "",
    ) -> list[ToolResult]:
        results: list[ToolResult] = []
        cache = result_cache if result_cache is not None else {}

        for tc in tool_calls:
            if self.cancel_flag.is_set():
                results.append(ToolResult(
                    tool_call_id=tc.id, name=tc.name or "cancelled",
                    content='{"status": "cancelled"}',
                ))
                break

            if not tc.name:
                continue

            # Phase enforcement — reject tools not allowed for current phase
            _DELEGATION_TOOLS = {"subtask", "execute"}
            if phase and phase in PHASE_TOOLS and tc.name not in _DELEGATION_TOOLS:
                if not _tool_matches_phase(tc.name, PHASE_TOOLS[phase]):
                    _log.warning("PHASE BLOCKED: %s not allowed in phase %s", tc.name, phase)
                    results.append(ToolResult(
                        tool_call_id=tc.id, name=tc.name,
                        content=json.dumps({"error": f"Tool '{tc.name}' is not available in the {phase} phase"}),
                    ))
                    continue

            # Cache check — same tool+args never executes twice (no event, no dispatch)
            cache_key = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
            if cache_key in cache:
                results.append(ToolResult(
                    tool_call_id=tc.id, name=tc.name, content=cache[cache_key],
                ))
                continue

            # Event fires only for NEW (uncached) tool calls
            if on_event:
                on_event(StepEvent(
                    "tool_call", data=json.dumps(tc.arguments)[:200],
                    tool_name=tc.name, depth=depth,
                ))

            # Handle subtask delegation
            if tc.name == "subtask":
                result_text = self._handle_subtask(tc, context, depth, on_event)
                cache[cache_key] = result_text
                results.append(ToolResult(
                    tool_call_id=tc.id, name="subtask", content=result_text,
                ))
                continue

            # Handle execute (lightweight subtask)
            if tc.name == "execute":
                result_text = self._handle_execute(tc, context, depth, on_event)
                cache[cache_key] = result_text
                results.append(ToolResult(
                    tool_call_id=tc.id, name="execute", content=result_text,
                ))
                continue

            # Regular tool dispatch
            try:
                result_text = self.tools.dispatch(tc.name, tc.arguments)
            except Exception as exc:
                _log.exception("Tool dispatch failed: %s", tc.name)
                result_text = json.dumps({"error": f"Tool '{tc.name}' failed: {exc}"})

            # Cache the result
            cache[cache_key] = result_text

            # Store observation
            obs = f"[{tc.name}] {result_text[:300]}"
            context.observations.append(obs)
            if len(context.observations) > 100:
                context.observations = context.observations[-80:]

            if on_event:
                on_event(StepEvent(
                    "tool_result", data=result_text[:200],
                    tool_name=tc.name, depth=depth,
                ))

            results.append(ToolResult(
                tool_call_id=tc.id, name=tc.name, content=result_text,
            ))

        return results

    def _handle_subtask(
        self,
        tc: ToolCall,
        context: ExternalContext,
        depth: int,
        on_event: StepCallback | None,
    ) -> str:
        objective = tc.arguments.get("objective", "")
        phase = tc.arguments.get("prompt", "")
        _log.info("=" * 40)
        _log.info("SUBTASK START | depth=%d->%d | phase=%s", depth, depth + 1, phase or "inferred")
        _log.info("  objective: %s", objective[:200])

        if on_event:
            on_event(StepEvent("subtask_start", data=objective[:200], depth=depth))

        # Reset search state for search-heavy phases
        if phase and any(x in phase.lower() for x in ("scout", "brancher", "search")):
            from .tools.search import reset_search_all_state
            reset_search_all_state()

        # Resolve phase-specific system prompt
        phase_prompt = None
        if phase and phase in PHASE_PROMPTS:
            phase_prompt = build_phase_system_prompt(
                phase=phase,
                topic=context.topic,
                rules=context.rules,
            )
        elif not phase:
            phase_prompt = self._infer_phase_prompt(objective, context)

        # Run subtask recursively
        try:
            result = self._solve_recursive(
                objective=objective,
                context=context,
                depth=depth + 1,
                on_event=on_event,
                system_prompt_override=phase_prompt,
                phase=phase,
            )
        except Exception as exc:
            _log.exception("Subtask failed: %s", objective[:100])
            result = json.dumps({"error": f"Subtask failed: {exc}", "objective": objective[:200]})

        # Detect structured error responses from recursive calls
        if result.startswith('{"error":'):
            _log.warning("Subtask returned error: %s", result[:200])

        _log.info("SUBTASK END | depth=%d | phase=%s | result=%d chars", depth, phase or "inferred", len(result))
        if on_event:
            on_event(StepEvent("subtask_end", data=result[:200], depth=depth))

        return result

    def _handle_execute(
        self,
        tc: ToolCall,
        context: ExternalContext,
        depth: int,
        on_event: StepCallback | None,
    ) -> str:
        objective = tc.arguments.get("objective", "")
        return self._solve_recursive(
            objective=objective,
            context=context,
            depth=depth + 1,
            on_event=on_event,
        )

    def _infer_phase_prompt(self, objective: str, context: ExternalContext) -> str | None:
        obj_lower = objective.lower()
        # Longest keywords first to avoid false matches
        keyword_map = [
            ("paper discovery", "scout"),
            ("search all", "scout"),
            ("extract claims", "analyst_deep_read"),
            ("deep read", "analyst_deep_read"),
            ("cross-domain", "brancher"),
            ("scout", "scout"),
            ("triage", "analyst_triage"),
            ("rank", "analyst_triage"),
            ("retraction", "verifier"),
            ("verif", "verifier"),
            ("hypothesis", "hypothesis"),
            ("branch", "brancher"),
            ("critic", "critic"),
            ("evaluat", "critic"),
            ("synthesis", "synthesis"),
            ("pre-writer", "synthesis"),
            ("prepare data", "synthesis"),
            ("outline", "writer"),
            ("draft", "writer"),
            ("write", "writer"),
        ]
        for keyword, phase in keyword_map:
            if keyword in obj_lower:
                return build_phase_system_prompt(
                    phase=phase, topic=context.topic, rules=context.rules,
                )
        return None

    def _condense(self, conversation: Conversation, context: ExternalContext, result_cache: dict[str, str] | None = None, active_model: BaseModel | None = None) -> None:
        _log.info("Condensing conversation (estimated tokens exceeds 75%% of context window)")
        # Preserve cache keys (tool signatures already called) but clear cached results
        # so re-execution uses fresh data. The keys tell us what NOT to re-call.
        called_tools: list[str] = []
        if result_cache is not None:
            called_tools = [k.split(":")[0] for k in result_cache.keys()]
            result_cache.clear()
        summary_parts = []

        model = active_model or self.model

        # Preserve turn history summary
        if context.turn_history:
            recent = context.turn_history[-10:]
            turns_text = "\n".join(
                f"- [{t.role}] {t.text[:200]}" + (f" (tools: {', '.join(t.tool_names)})" if t.tool_names else "")
                for t in recent
            )
            summary_parts.append(f"Recent turns:\n{turns_text}")

        if context.observations:
            summary_parts.append("Key observations:\n" + "\n".join(
                f"- {o}" for o in context.observations[-30:]
            ))

        # Tell the model which tools were already called to prevent re-calling
        if called_tools:
            tool_counts: dict[str, int] = {}
            for t in called_tools:
                tool_counts[t] = tool_counts.get(t, 0) + 1
            tools_summary = ", ".join(f"{name}(×{count})" for name, count in tool_counts.items())
            summary_parts.append(f"Tools already called (do NOT re-call unless needed): {tools_summary}")

        summary = "\n\n".join(summary_parts) if summary_parts else "Previous context condensed — continue from current state."
        model.condense_conversation(conversation, summary)
