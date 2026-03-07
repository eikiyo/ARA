# Location: ara/engine.py
# Purpose: Recursive LLM engine — tool dispatch, subtask delegation, context management
# Functions: RLMEngine, ExternalContext, StepCallback, TurnSummary
# Calls: model.py, tools/__init__.py, prompts/__init__.py
# Imports: dataclasses, time, logging, json

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import threading as _th
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import ARAConfig
from .model import (
    BaseModel, Conversation, ModelTurn, ToolCall, ToolResult,
    ModelError, RateLimitError, TokenUsage,
)
from .paper_config import get_paper_config, is_phase_enabled, get_phase_mode
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
        hypothesis_model: BaseModel | None = None,
        light_model: BaseModel | None = None,
        deep_read_model: BaseModel | None = None,
    ):
        self.model = model
        self.writer_model = writer_model or model
        self.hypothesis_model = hypothesis_model or model
        self.light_model = light_model or model
        self.deep_read_model = deep_read_model or model
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
        # Both "review" and "scoping" use the same phase structure;
        # scoping differences are handled by phase enablement (paper_config)
        # and prompt template variables.
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
                    "{special_instructions}"
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
                    "STEP 1: Call list_papers(selected_only=true, needs_claims=true, limit=100) to get papers that STILL NEED claim extraction. "
                    "Papers that already have claims from the database are automatically excluded. "
                    "STEP 2: For EVERY listed paper: call read_paper(paper_id=ID, include_fulltext=true) to get the full paper text, "
                    "then extract_claims with specific quotes from the text, then assess_risk_of_bias. "
                    "Each claim needs: claim_text, claim_type (finding/method/limitation/gap), "
                    "confidence (0-1), supporting_quotes (EXACT quotes from the paper text). "
                    "Also extract: sample_size, effect_size, p_value, study_design, population. "
                    "Process papers one at a time. Target: {min_claims}+ claims from {min_deep_read_papers}+ papers. "
                    "SKIP any paper that returns no full text — move to the next one immediately. "
                    "DO NOT STOP until you have processed ALL listed papers. "
                    "If you run out of steps, you have failed. Keep going."
                ),
            },
            {
                "name": "brancher",
                "prompt": "brancher",
                "objective": (
                    "Cross-domain search for: {topic}. "
                    "FIRST: Call list_claims() to load all extracted evidence ({claim_count} claims from {papers_with_claims} papers). "
                    "Call get_risk_of_bias_table() to understand methodological quality. "
                    "Then search for papers from AT LEAST 3 adjacent fields, using 9+ distinct cross-disciplinary queries. "
                    "Target domains: (a) adjacent academic disciplines, (b) industry/practitioner literature, "
                    "(c) policy/regulatory reports, (d) analogous problems in other sectors, "
                    "(e) methodological innovations from other fields, (f) contradictory/disconfirming evidence. "
                    "Use search_semantic_scholar, search_crossref, search_openalex with diverse queries. "
                    "For each branch, explain HOW it connects to existing claims."
                ),
            },
            {
                "name": "hypothesis",
                "prompt": "hypothesis",
                "objective": (
                    "Generate 15+ research hypotheses from the evidence on {topic}. "
                    "FIRST: Call list_claims() to load all extracted evidence ({claim_count} claims from {papers_with_claims} papers). "
                    "Call get_risk_of_bias_table() to understand which evidence is strongest. "
                    "Call get_grade_table() to see evidence certainty ratings. "
                    "Use search_similar() per theme to find relevant papers via embeddings. "
                    "For the 3-5 most important papers, call read_paper(paper_id=ID, include_fulltext=true). "
                    "Generate hypotheses across these categories: "
                    "(a) 3+ causal mechanism hypotheses, (b) 3+ moderator/boundary condition hypotheses, "
                    "(c) 3+ novel synthesis hypotheses connecting disparate findings, "
                    "(d) 2+ contrarian hypotheses challenging conventional wisdom, "
                    "(e) 2+ methodological hypotheses about measurement/design. "
                    "Score each: novelty, feasibility, evidence_strength, methodology_fit, impact, reproducibility. "
                    "For the top 3 hypotheses, specify: methodology, analysis approach, quality assessment framework. "
                    "Use score_hypothesis to evaluate EVERY one. "
                    "Ground every hypothesis in specific claims from the database — do NOT hypothesize beyond the evidence. "
                    "{special_instructions}"
                ),
            },
            {
                "name": "critic",
                "prompt": "critic",
                "objective": (
                    "FIRST: Call list_claims() to load all extracted evidence ({claim_count} claims from {papers_with_claims} papers). "
                    "Call get_risk_of_bias_table() to assess evidence quality. "
                    "Call get_grade_table() to see evidence certainty ratings. "
                    "Use search_similar() to find the most relevant papers for each hypothesis. "
                    "Read 5-8 key papers using read_paper(paper_id=ID, include_fulltext=true) to ground your evaluation. "
                    "THEN evaluate the top 3 hypotheses across 8 dimensions each. "
                    "Verify the novelty framework label (INVERSION/MISSING LINK/MODERATOR/etc). "
                    "Apply the meta-test: would an expert believe something different? "
                    "Cross-reference claims against RoB ratings — discount claims from high-bias papers. "
                    "Decide per hypothesis: APPROVE or REJECT with detailed feedback and specific revisions."
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
                    "Call get_risk_of_bias_table() to get RoB assessments for all papers. "
                    "Call get_grade_table() to get GRADE evidence certainty ratings. "
                    "Use search_similar() per theme to find relevant papers via embeddings. "
                    "For top 5-10 papers, call read_paper(paper_id=ID, include_fulltext=true). "
                    "Build these outputs: "
                    "(1) Study characteristics table with author names, (2) Evidence synthesis "
                    "table with GRADE ratings, (3) Risk of bias summary table, "
                    "(4) PRISMA flow numbers, (5) Citation map by theme with (Author, Year), "
                    "(6) Structural causal model notes, (7) Inclusion/exclusion criteria table, "
                    "(8) Evidence quality matrix (cross-reference claims with RoB and GRADE). "
                    "{special_instructions}"
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
                    "{special_instructions}"
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
                    "STEP 1: Call list_papers(selected_only=true, needs_claims=true, limit=100) to get papers that STILL NEED claim extraction. "
                    "Papers that already have claims from the database are automatically excluded. "
                    "STEP 2: For EVERY listed paper: call read_paper(paper_id=ID, include_fulltext=true) to get the full paper text, then extract_claims. "
                    "Focus on extracting: (a) theoretical arguments and frameworks proposed, "
                    "(b) key constructs and definitions, (c) empirical findings that support/challenge theories, "
                    "(d) research gaps and limitations identified, (e) boundary conditions discussed. "
                    "Use claim_type: 'theory' for theoretical arguments, 'finding' for evidence, "
                    "'gap' for research gaps, 'method' for methodological insights. "
                    "Process papers one at a time. Target: {min_claims}+ claims from {min_deep_read_papers}+ papers. "
                    "SKIP any paper that returns no full text — move to the next one immediately. "
                    "DO NOT STOP until you have processed ALL listed papers."
                ),
            },
            {
                "name": "brancher",
                "prompt": "brancher",
                "objective": (
                    "Cross-domain search for: {topic}. "
                    "FIRST: Call list_claims() to load all extracted evidence ({claim_count} claims from {papers_with_claims} papers). "
                    "Call get_risk_of_bias_table() to understand methodological quality. "
                    "Then search for papers from AT LEAST 3 adjacent theoretical fields using 9+ cross-disciplinary queries. "
                    "Target: (a) analogous frameworks in other domains, (b) competing theories, "
                    "(c) methodological insights for proposition testing, (d) empirical evidence from adjacent fields, "
                    "(e) practitioner/industry perspectives, (f) contradictory/disconfirming evidence. "
                    "Use search_semantic_scholar, search_crossref, search_openalex with diverse queries. "
                    "For each branch, explain HOW it connects to existing claims."
                ),
            },
            {
                "name": "hypothesis",
                "prompt": "hypothesis",
                "objective": (
                    "Identify the core theoretical gap and propose 10-15 candidate frameworks for: {topic}. "
                    "FIRST: Call list_claims() to load all extracted evidence ({claim_count} claims from {papers_with_claims} papers). "
                    "Call get_risk_of_bias_table() to understand which evidence is strongest. "
                    "Call get_grade_table() to see evidence certainty ratings. "
                    "Use search_similar() per theme to find relevant papers via embeddings. "
                    "For the 3-5 most important papers, call read_paper(paper_id=ID, include_fulltext=true). "
                    "Each framework should be a TYPOLOGY, PROCESS MODEL, or MULTI-LEVEL FRAMEWORK. "
                    "Generate across categories: (a) 3+ integration frameworks, (b) 3+ process models, "
                    "(c) 2+ multi-level frameworks, (d) 2+ contrarian frameworks, (e) 2+ boundary condition models. "
                    "Score each: novelty (2x weight), feasibility, evidence_strength, methodology_fit, "
                    "impact, reproducibility. Answer the Five Questions for the top 3 frameworks. "
                    "Use score_hypothesis to evaluate EVERY one. "
                    "Ground every framework in specific claims from the database — do NOT theorize beyond the evidence. "
                    "{special_instructions}"
                ),
            },
            {
                "name": "critic",
                "prompt": "critic",
                "objective": (
                    "FIRST: Call list_claims() to load all extracted evidence ({claim_count} claims from {papers_with_claims} papers). "
                    "Call get_risk_of_bias_table() to assess evidence quality. "
                    "Call get_grade_table() to see evidence certainty ratings. "
                    "Use search_similar() to find the most relevant papers for each framework. "
                    "Read 5-8 key theoretical papers using read_paper(paper_id=ID, include_fulltext=true) to ground your evaluation. "
                    "THEN evaluate the top 3 frameworks across 8 dimensions each. "
                    "Verify the novelty framework label (INVERSION/MISSING LINK/MODERATOR/etc). "
                    "Apply the meta-test: would a management scholar believe something different? "
                    "Cross-reference claims against RoB ratings — discount claims from high-bias papers. "
                    "Decide per framework: APPROVE or REJECT with detailed feedback."
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
                    "Call get_risk_of_bias_table() to get RoB assessments for all papers. "
                    "Call get_grade_table() to get GRADE evidence certainty ratings. "
                    "Use search_similar() per theme to find relevant papers via embeddings. "
                    "For top 5-10 papers, call read_paper(paper_id=ID, include_fulltext=true). "
                    "Build these outputs: "
                    "(1) Theoretical streams table with author names and core arguments, "
                    "(2) Theoretical tension map showing conflicts/gaps between streams, "
                    "(3) Construct definitions table, (4) Proposition evidence map with supporting/opposing papers, "
                    "(5) Citation map by section with (Author, Year), "
                    "(6) Boundary conditions analysis, (7) Competing frameworks comparison table, "
                    "(8) Novel contribution statement, (9) Evidence quality matrix (cross-ref claims with RoB/GRADE). "
                    "{special_instructions}"
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

            # Skip phases disabled for this paper type
            if not is_phase_enabled(paper_type, name):
                _log.info("PIPELINE: Skipping %s (disabled for paper_type=%s)", name, paper_type)
                if on_event:
                    on_event(StepEvent("text", data=f"Skipping {name} (not required for {paper_type})", depth=0))
                if db and session_id:
                    db.save_phase_checkpoint(session_id, name)
                completed.add(name)
                continue

            _log.info("=" * 40)
            _log.info("PIPELINE PHASE: %s", name)
            _log.info("=" * 40)

            if on_event:
                on_event(StepEvent("subtask_start", data=f"Phase: {name}", depth=0))

            try:
                if name == "fetch_texts" and "embed" not in completed:
                    # Pre-load claims from central DB so deep_read skips already-extracted papers
                    central_db = self.tools.central_db
                    if central_db and db and session_id:
                        self._preload_claims_from_central(central_db, db, session_id)

                    # Run fetch_texts + embed + deep_read in parallel
                    # fetch gets full text; embed creates vectors; deep_read starts on available papers
                    deep_read_def = next(
                        (p for p in self._get_pipeline_phases() if p["name"] == "deep_read"), None
                    )
                    run_deep_read = (
                        deep_read_def and "deep_read" not in completed
                        and deep_read_def.get("objective")
                    )

                    parallel_label = "fetch_texts + embed + deep_read" if run_deep_read else "fetch_texts + embed"
                    _log.info("PIPELINE: Running %s in parallel", parallel_label)
                    if on_event:
                        on_event(StepEvent("text", data=f"Running {parallel_label} in parallel", depth=0))

                    def _run_fetch():
                        self._pipeline_fetch_texts(on_event)

                    def _run_embed():
                        self._pipeline_embed(on_event)

                    def _run_deep_read():
                        # Small delay so some full texts land before deep_read starts querying
                        import time
                        time.sleep(10)
                        # Check if claim targets already met (e.g. from central DB pre-load)
                        if db and session_id:
                            existing_claims = len(db.get_claims(session_id))
                            papers_done = len(set(c["paper_id"] for c in db.get_claims(session_id)))
                            if existing_claims >= self.config.min_claims and papers_done >= self.config.min_deep_read_papers:
                                _log.info("DEEP_READ: Targets already met (%d claims from %d papers) — skipping", existing_claims, papers_done)
                                return
                        self._pipeline_run_phase(deep_read_def, topic, paper_type, context, on_event)

                    # Fire fetch+embed in background, don't block pipeline on them
                    _th.Thread(target=_run_fetch, daemon=True).start()
                    _th.Thread(target=_run_embed, daemon=True).start()

                    # Run deep_read synchronously — it gates the pipeline
                    if run_deep_read:
                        _run_deep_read()

                    # Mark embed + deep_read as completed so they're skipped in the main loop
                    if db and session_id:
                        db.save_phase_checkpoint(session_id, "embed")
                    completed.add("embed")
                    if run_deep_read:
                        if db and session_id:
                            db.save_phase_checkpoint(session_id, "deep_read")
                        completed.add("deep_read")
                        # Run batched deep_read continuation if targets not yet met
                        if db and session_id:
                            self._deep_read_batched_continuation(
                                deep_read_def, topic, paper_type, context, on_event,
                                db, session_id,
                            )

                    # Don't wait — fetch+embed continue in background while pipeline moves on
                    _log.info("PIPELINE: fetch+embed running in background — moving to next phase")

                elif name == "embed":
                    # Already ran in parallel with fetch_texts above
                    self._pipeline_embed(on_event)
                elif name == "fetch_texts":
                    self._pipeline_fetch_texts(on_event)
                elif name == "snowball":
                    self._pipeline_snowball(topic, on_event)
                elif name == "triage":
                    self._pipeline_triage_parallel(topic, paper_type, context, on_event, completed)
                elif name == "protocol" and "verifier" not in completed:
                    # Bulk pre-populate verification flags from central DB
                    central_db = self.tools.central_db
                    if central_db and db and session_id:
                        self._prepopulate_verification_flags(central_db, db, session_id)
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

                        t1 = _th.Thread(target=_run_protocol)
                        t2 = _th.Thread(target=_run_verifier)
                        t1.start()
                        t2.start()
                        t1.join()
                        t2.join()

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
                deep_read_def_inline = phase_def
                self._deep_read_batched_continuation(
                    deep_read_def_inline, topic, paper_type, context, on_event,
                    db, session_id,
                )
                # Start brancher in parallel with re-embed if we have enough claims
                _brancher_thread = None
                brancher_def = next(
                    (p for p in self._get_pipeline_phases() if p["name"] == "brancher"), None
                )
                claims = db.get_claims(session_id)
                if (brancher_def and "brancher" not in completed
                        and len(claims) >= 50 and not self.cancel_flag.is_set()):
                    _log.info("PIPELINE: Starting brancher in parallel with re-embed")
                    if on_event:
                        on_event(StepEvent("text", data="Starting brancher + re-embed in parallel", depth=0))
                    _brancher_thread = _th.Thread(
                        target=self._pipeline_run_phase,
                        args=(brancher_def, topic, paper_type, context, on_event),
                    )
                    _brancher_thread.start()

                # Deduplicate claims
                self._deduplicate_claims(db, session_id)

                paper_cfg = get_paper_config(paper_type)
                if paper_cfg.requires_prisma:
                    self._finalize_prisma_exclusions(db, session_id)

                # Wait for brancher if it was started in parallel
                if _brancher_thread is not None:
                    _brancher_thread.join(timeout=600)
                    if _brancher_thread.is_alive():
                        _log.warning("PIPELINE: Parallel brancher timed out after 600s")
                    else:
                        _log.info("PIPELINE: Parallel brancher completed")
                    if db and session_id:
                        db.save_phase_checkpoint(session_id, "brancher")
                    completed.add("brancher")

            if db and session_id:
                db.save_phase_checkpoint(session_id, name)
            if on_event:
                on_event(StepEvent("subtask_end", data=f"Phase {name} complete", depth=0))

        # Advisory board — multi-advisor deliberation before writing
        if "advisory_board" not in completed and not self.cancel_flag.is_set():
            self._pipeline_advisory_board(topic, paper_type, context, on_event)
            if db and session_id:
                db.save_phase_checkpoint(session_id, "advisory_board")

        # Writer — section by section
        if "writer" not in completed and not self.cancel_flag.is_set():
            self._pipeline_writer(topic, paper_type, context, on_event)
            if db and session_id:
                db.save_phase_checkpoint(session_id, "writer")

        # Programmatic quality gate — fast checks + auto-rewrite before output assembly
        # (LLM-based critic removed — peer review handles qualitative evaluation post-output)
        if "paper_critic" not in completed and not self.cancel_flag.is_set():
            self._pre_critic_validation(topic, paper_type, context, on_event)
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

        # Substitute config values into prompt template variables
        _prompt_subs = {
            "{min_words_abstract}": str(self.config.words_abstract),
            "{min_words_intro}": str(self.config.words_introduction),
            "{min_words_lit}": str(self.config.words_literature_review),
            "{min_words_methods}": str(self.config.words_methods),
            "{min_words_results}": str(self.config.words_results),
            "{min_words_discussion}": str(self.config.words_discussion),
            "{min_words_conclusion}": str(self.config.words_conclusion),
            "{min_cites_intro}": str(self.config.cites_introduction),
            "{min_cites_lit}": str(self.config.cites_literature_review),
            "{min_cites_methods}": str(self.config.cites_methods),
            "{min_cites_results}": str(self.config.cites_results),
            "{min_cites_discussion}": str(self.config.cites_discussion),
            "{min_quality_citations}": str(self.config.min_quality_citations),
        }
        for placeholder, value in _prompt_subs.items():
            system_prompt = system_prompt.replace(placeholder, value)

        # Use per-phase step budget if defined
        step_budget = self._phase_step_budgets().get(phase_def["name"], self.config.max_steps_per_call)

        # Dynamic budget for deep_read: scale with papers that actually need processing
        if phase_def["name"] == "deep_read" and self.tools.db and self.tools.session_id:
            try:
                needs = self.tools.db._conn.execute(
                    "SELECT COUNT(*) FROM papers WHERE session_id = ? AND selected_for_deep_read = 1 "
                    "AND paper_id NOT IN (SELECT DISTINCT paper_id FROM claims WHERE session_id = ?)",
                    (self.tools.session_id, self.tools.session_id),
                ).fetchone()[0]
                # ~4 steps per paper (read + extract + risk_of_bias + overhead) + 5 startup steps
                dynamic_budget = needs * 4 + 5
                if dynamic_budget < step_budget:
                    _log.info("DEEP_READ: Dynamic budget %d steps for %d papers (was %d)", dynamic_budget, needs, step_budget)
                    step_budget = dynamic_budget
            except Exception:
                pass

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

    def _deep_read_batched_continuation(
        self, deep_read_def: dict, topic: str, paper_type: str,
        context: ExternalContext, on_event: StepCallback | None,
        db: Any, session_id: int,
    ) -> None:
        """Run batched deep_read continuation until claim targets are met."""
        claims = db.get_claims(session_id)
        papers_with_claims = len(set(c["paper_id"] for c in claims))
        _log.info("PIPELINE: Deep read batch 1 produced %d claims from %d papers", len(claims), papers_with_claims)

        _BATCH_SIZE = 27
        _MAX_BATCHES = 5
        _BATCH_COOLDOWN = 30

        for batch_num in range(1, _MAX_BATCHES + 1):
            # Stop if claim target met (with 10% tolerance) OR we've processed most selected papers
            _close_enough = int(self.config.min_claims * 0.90)
            if len(claims) >= _close_enough:
                _log.info("PIPELINE: Deep read claim target met (%d claims, %d papers)", len(claims), papers_with_claims)
                break
            if papers_with_claims >= self.config.min_deep_read_papers:
                _log.info("PIPELINE: Deep read paper target met (%d claims, %d papers)", len(claims), papers_with_claims)
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

            if batch_num > 0:
                _log.info("PIPELINE: Cooling down %ds between deep_read batches", _BATCH_COOLDOWN)
                time.sleep(_BATCH_COOLDOWN)

            batch_objective = (
                f"CONTINUE extracting claims from papers about {topic}. "
                f"You have {len(claims)} claims from {papers_with_claims} papers so far. "
                f"Target: {self.config.min_claims}+ claims from {self.config.min_deep_read_papers}+ papers. "
                f"Call list_papers(selected_only=true, needs_claims=true, limit={_BATCH_SIZE}) to get papers that STILL NEED claims. "
                f"Papers already processed are automatically excluded. "
                f"Process ALL listed papers. "
                f"For each: read_paper(paper_id=ID, include_fulltext=true) → extract_claims with EXACT quotes → assess_risk_of_bias. "
                f"SKIP any paper with no full text — move to next. "
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

            prev_claims = len(claims)
            claims = db.get_claims(session_id)
            papers_with_claims = len(set(c["paper_id"] for c in claims))
            new_claims = len(claims) - prev_claims
            _log.info("PIPELINE: Batch %d done: +%d claims, total %d from %d papers",
                      batch_num + 1, new_claims, len(claims), papers_with_claims)

            if new_claims == 0:
                extra_cooldown = 45
                _log.warning("PIPELINE: Batch %d produced 0 claims — extra cooldown %ds", batch_num + 1, extra_cooldown)
                time.sleep(extra_cooldown)

        # HARD GATE: If still 0 claims, this is fatal
        if len(claims) == 0:
            _log.error("PIPELINE: FATAL — 0 claims after all deep_read batches. Cannot produce a credible paper.")
            if on_event:
                on_event(StepEvent("error", data="FATAL: 0 claims extracted. Pipeline cannot continue without evidence.", depth=0))
            return

        # Store claim/paper counts in context for downstream phases
        context.claim_count = len(claims)
        context.papers_with_claims = papers_with_claims

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

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _score_and_store(self, db: Any, pid: int, sim: float) -> str:
        """Score a paper and commit immediately. Returns 'selected', 'rejected', or 'borderline'."""
        if sim >= self.config.triage_select_threshold:
            db._conn.execute(
                "UPDATE papers SET relevance_score = ?, selected_for_deep_read = 1 WHERE paper_id = ?",
                (round(sim, 3), pid),
            )
            db._conn.commit()
            return "selected"
        elif sim < self.config.triage_reject_threshold:
            db._conn.execute(
                "UPDATE papers SET relevance_score = ?, selected_for_deep_read = 0 WHERE paper_id = ?",
                (round(sim, 3), pid),
            )
            db._conn.commit()
            return "rejected"
        else:
            db._conn.execute(
                "UPDATE papers SET relevance_score = ? WHERE paper_id = ?",
                (round(sim, 3), pid),
            )
            db._conn.commit()
            return "borderline"

    def _embed_topic(self) -> tuple[Any, list[float]] | None:
        """Embed the topic string. Returns (client, embedding) or None."""
        api_key = os.getenv("GOOGLE_API_KEY") or (self.config.google_api_key if self.config else None)
        if not api_key:
            return None
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            return client, None  # type: ignore  # caller will embed
        except Exception:
            return None

    def _embedding_triage_cached(
        self, topic: str, db: Any, session_id: int, on_event: StepCallback | None,
    ) -> tuple[int, int, int, list[tuple[int, str]]]:
        """Phase 1: Score papers using ONLY cached embeddings (DB-first, no API calls).
        Returns (auto_selected, auto_rejected, borderline, papers_needing_embed)."""

        _log.info("PIPELINE TRIAGE: Phase 1 — scoring with cached embeddings")
        if on_event:
            on_event(StepEvent("text", data="Embedding triage phase 1 — scoring from cached embeddings...", depth=0))

        # Embed topic
        api_key = os.getenv("GOOGLE_API_KEY") or (self.config.google_api_key if self.config else None)
        if not api_key:
            _log.warning("PIPELINE TRIAGE: No API key for embedding triage")
            return 0, 0, 0, []

        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            result = client.models.embed_content(model="gemini-embedding-001", contents=topic)
            if not result.embeddings or len(result.embeddings) == 0:
                return 0, 0, 0, []
            self._topic_emb = result.embeddings[0].values
            self._embed_client = client
        except Exception as exc:
            _log.warning("PIPELINE TRIAGE: Failed to embed topic: %s", exc)
            return 0, 0, 0, []

        # Get all papers
        rows = db._conn.execute(
            "SELECT paper_id, title, abstract, doi, embedding FROM papers WHERE session_id = ?",
            (session_id,),
        ).fetchall()

        central_db = self.tools.central_db
        auto_selected = 0
        auto_rejected = 0
        borderline = 0
        papers_needing_embed: list[tuple[int, str]] = []

        for row in rows:
            pid = row["paper_id"]
            emb = None

            # Check session DB
            if row["embedding"]:
                emb = json.loads(row["embedding"])

            # Check central DB
            if not emb and row["doi"] and central_db:
                doi = row["doi"].strip().lower()
                cp = central_db.get_paper_by_doi(doi)
                if cp and cp.get("embedding"):
                    emb = cp["embedding"] if isinstance(cp["embedding"], list) else json.loads(cp["embedding"])
                    try:
                        db.store_embedding(pid, emb)
                    except Exception:
                        pass

            if emb:
                # Score immediately
                result = self._score_and_store(db, pid, self._cosine_sim(self._topic_emb, emb))
                if result == "selected":
                    auto_selected += 1
                elif result == "rejected":
                    auto_rejected += 1
                else:
                    borderline += 1
            else:
                # Queue for embedding generation
                text_parts = []
                if row["title"]:
                    text_parts.append(row["title"])
                if row["abstract"]:
                    text_parts.append(row["abstract"])
                text = " ".join(text_parts)
                if text.strip():
                    papers_needing_embed.append((pid, text[:3000]))
                else:
                    borderline += 1

        _log.info(
            "PIPELINE TRIAGE: Phase 1 complete — "
            "auto_selected=%d, auto_rejected=%d, borderline=%d, need_embedding=%d",
            auto_selected, auto_rejected, borderline, len(papers_needing_embed),
        )
        if on_event:
            on_event(StepEvent("text", data=(
                f"Embedding triage phase 1: {auto_selected} selected, {auto_rejected} rejected, "
                f"{borderline} borderline, {len(papers_needing_embed)} need embedding"
            ), depth=0))

        return auto_selected, auto_rejected, borderline, papers_needing_embed

    def _embedding_triage_generate(
        self, papers_needing_embed: list[tuple[int, str]], db: Any,
    ) -> tuple[int, int, int]:
        """Phase 2: Generate missing embeddings and score. Runs in background thread.
        Returns (selected, rejected, borderline)."""
        if not papers_needing_embed or not hasattr(self, '_embed_client') or not hasattr(self, '_topic_emb'):
            return 0, 0, 0

        _log.info("PIPELINE TRIAGE: Phase 2 — generating %d embeddings + scoring", len(papers_needing_embed))
        selected = 0
        rejected = 0
        borderline = 0

        for pid, text in papers_needing_embed:
            try:
                res = self._embed_client.models.embed_content(model="gemini-embedding-001", contents=text)
                if res.embeddings and len(res.embeddings) > 0:
                    emb = res.embeddings[0].values
                    try:
                        db.store_embedding(pid, emb)
                    except Exception:
                        pass
                    result = self._score_and_store(db, pid, self._cosine_sim(self._topic_emb, emb))
                    if result == "selected":
                        selected += 1
                    elif result == "rejected":
                        rejected += 1
                    else:
                        borderline += 1
                else:
                    borderline += 1
            except Exception:
                borderline += 1

        _log.info(
            "PIPELINE TRIAGE: Phase 2 complete — selected=%d, rejected=%d, borderline=%d",
            selected, rejected, borderline,
        )
        return selected, rejected, borderline

    def _pipeline_triage_parallel(
        self, topic: str, paper_type: str, context: ExternalContext,
        on_event: StepCallback | None, completed: set[str],
    ) -> None:
        """Orchestrate triage + fetch + embed + deep_read with maximum parallelism.

        Flow:
        1. Embedding triage phase 1 (cached embeddings → instant scoring)
        2. In parallel:
           a. fetch_texts + deep_read for already-selected papers
           b. Generate missing embeddings + score remaining papers
        3. LLM triage for any borderline papers
        4. Final deep_read pass for newly selected papers
        """
        db = self.tools.db
        session_id = self.tools.session_id
        if not db or not session_id:
            _log.error("PIPELINE TRIAGE: No DB or session")
            return

        total = db.paper_count(session_id)
        _log.info("PIPELINE TRIAGE PARALLEL: %d papers total", total)

        # ── Step 1: Instant triage with cached embeddings ──
        sel1, rej1, brd1, papers_needing_embed = self._embedding_triage_cached(
            topic, db, session_id, on_event,
        )

        # Pre-load claims from central DB
        central_db = self.tools.central_db
        if central_db:
            self._preload_claims_from_central(central_db, db, session_id)

        # ── Step 2: Parallel — fetch/deep_read selected + generate remaining embeddings ──
        selected_count = db._conn.execute(
            "SELECT COUNT(*) FROM papers WHERE session_id = ? AND selected_for_deep_read = 1",
            (session_id,),
        ).fetchone()[0]

        _log.info(
            "PIPELINE TRIAGE PARALLEL: %d papers selected after phase 1 — "
            "starting fetch+deep_read in parallel with %d embedding generations",
            selected_count, len(papers_needing_embed),
        )
        if on_event:
            on_event(StepEvent("text", data=(
                f"Phase 1 done: {selected_count} selected. Starting fetch+deep_read "
                f"while generating {len(papers_needing_embed)} missing embeddings..."
            ), depth=0))

        deep_read_def = next(
            (p for p in self._get_pipeline_phases() if p["name"] == "deep_read"), None
        )

        def _run_fetch_and_embed_phase():
            self._pipeline_fetch_texts(on_event)
            self._pipeline_embed(on_event)

        def _run_embedding_generation():
            if papers_needing_embed:
                self._embedding_triage_generate(papers_needing_embed, db)

        # Fire fetch+embed+embedding_gen in background daemon threads
        _bg1 = _th.Thread(target=_run_fetch_and_embed_phase, daemon=True)
        _bg2 = _th.Thread(target=_run_embedding_generation, daemon=True)
        _bg1.start()
        _bg2.start()

        # Deep_read runs synchronously — it gates the pipeline
        time.sleep(5)  # Brief delay so some full texts land first
        if deep_read_def and deep_read_def.get("objective"):
            self._pipeline_run_phase(deep_read_def, topic, paper_type, context, on_event)

        # Mark phases as completed
        for phase_name in ("fetch_texts", "embed", "deep_read"):
            if db and session_id:
                db.save_phase_checkpoint(session_id, phase_name)
            completed.add(phase_name)
        _log.info("PIPELINE: deep_read done — fetch+embed continue in background")

        # ── Step 3: LLM triage for borderline papers ──
        untriaged = db._conn.execute(
            "SELECT COUNT(*) FROM papers WHERE session_id = ? AND selected_for_deep_read IS NULL",
            (session_id,),
        ).fetchone()[0]

        if untriaged > 0:
            _log.info("PIPELINE TRIAGE PARALLEL: %d borderline papers → LLM triage", untriaged)
            self._pipeline_triage(topic, context, on_event)

            # ── Step 4: Deep read newly selected papers ──
            if deep_read_def and deep_read_def.get("objective"):
                new_selected = db._conn.execute(
                    "SELECT COUNT(*) FROM papers WHERE session_id = ? AND selected_for_deep_read = 1",
                    (session_id,),
                ).fetchone()[0]
                if new_selected > selected_count:
                    _log.info(
                        "PIPELINE TRIAGE PARALLEL: %d new papers selected by LLM — running additional deep_read",
                        new_selected - selected_count,
                    )
                    self._pipeline_fetch_texts(on_event)
                    self._pipeline_run_phase(deep_read_def, topic, paper_type, context, on_event)
        else:
            _log.info("PIPELINE TRIAGE PARALLEL: No borderline papers — skipping LLM triage")

        # Deep read continuation to meet targets
        if deep_read_def and db and session_id:
            self._deep_read_batched_continuation(
                deep_read_def, topic, paper_type, context, on_event, db, session_id,
            )

        # PRISMA stats
        final_selected = db._conn.execute(
            "SELECT COUNT(*) FROM papers WHERE session_id = ? AND selected_for_deep_read = 1",
            (session_id,),
        ).fetchone()[0]
        db.store_prisma_stat(session_id, "records_identified", total)
        db.store_prisma_stat(session_id, "records_screened", total)
        db.store_prisma_stat(session_id, "excluded_screening", total - final_selected)
        db.store_prisma_stat(session_id, "fulltext_assessed", final_selected)
        _log.info("PIPELINE TRIAGE PARALLEL: Complete — %d/%d papers selected", final_selected, total)

    def _pipeline_triage(
        self, topic: str, context: ExternalContext, on_event: StepCallback | None,
    ) -> None:
        """Run triage: embedding scoring (cached → generate) then LLM for borderline."""
        _log.info("PIPELINE: Starting batched triage")

        db = self.tools.db
        session_id = self.tools.session_id
        if not db or not session_id:
            _log.error("PIPELINE TRIAGE: No DB or session")
            return

        total = db.paper_count(session_id)
        _log.info("PIPELINE TRIAGE: %d papers to triage", total)

        # Phase 1: Score papers with CACHED embeddings only (instant, no API)
        sel1, rej1, brd1, papers_needing_embed = self._embedding_triage_cached(
            topic, db, session_id, on_event,
        )

        # Phase 2: Generate missing embeddings + score (API calls, but cheap)
        if papers_needing_embed:
            sel2, rej2, brd2 = self._embedding_triage_generate(papers_needing_embed, db)
        else:
            sel2, rej2, brd2 = 0, 0, 0

        total_selected = sel1 + sel2
        total_rejected = rej1 + rej2
        total_borderline = brd1 + brd2

        _log.info(
            "PIPELINE TRIAGE: Embedding triage totals — selected=%d, rejected=%d, borderline=%d",
            total_selected, total_rejected, total_borderline,
        )

        # Phase 3: LLM triage only for borderline papers
        untriaged = db._conn.execute(
            "SELECT COUNT(*) FROM papers WHERE session_id = ? AND selected_for_deep_read IS NULL",
            (session_id,),
        ).fetchone()[0]

        if untriaged > 0:
            _log.info("PIPELINE TRIAGE: %d borderline papers need LLM triage", untriaged)

            system_prompt = build_phase_system_prompt(
                phase="analyst_triage", topic=topic, rules=context.rules,
                paper_type=self.config.paper_type,
            )

            batch_num = 0
            offset = 0
            while offset < untriaged + self._TRIAGE_BATCH_SIZE:
                if self.cancel_flag.is_set():
                    break

                batch_num += 1
                _log.info("PIPELINE TRIAGE: LLM Batch %d (offset=%d)", batch_num, offset)
                if on_event:
                    on_event(StepEvent("text", data=f"LLM triage batch {batch_num} (borderline papers)", depth=0))

                objective = (
                    f"Triage batch {batch_num}: "
                    f"1. Call list_papers(limit={self._TRIAGE_BATCH_SIZE}, offset={offset}, filter='untriaged'). "
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

                # Check if all borderline papers are now triaged
                remaining = db._conn.execute(
                    "SELECT COUNT(*) FROM papers WHERE session_id = ? AND selected_for_deep_read IS NULL",
                    (session_id,),
                ).fetchone()[0]
                if remaining == 0:
                    _log.info("PIPELINE TRIAGE: All borderline papers triaged")
                    break
        else:
            _log.info("PIPELINE TRIAGE: No borderline papers — skipping LLM triage")

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

    # ── Advisory Board ────────────────────────────────────────

    _ADVISOR_PERSONAS = [
        {
            "id": "domain_expert",
            "name": "Domain Expert",
            "focus": (
                "You are a domain expert in the paper's field. Your focus is on: "
                "theoretical framing and which theories to use, "
                "how to position the contribution relative to existing work, "
                "which debates and tensions to engage with, "
                "what the key narrative arc should be, "
                "which findings to highlight vs. de-emphasize based on evidence quality, "
                "how to handle conflicting evidence, "
                "and how to make the paper compelling to the target audience."
            ),
        },
        {
            "id": "journal_reviewer",
            "name": "Journal Reviewer",
            # focus is set dynamically based on detected journal — see _pipeline_advisory_board
            "focus": "",
        },
    ]

    def _pipeline_advisory_board(
        self, topic: str, paper_type: str,
        context: ExternalContext, on_event: StepCallback | None,
    ) -> None:
        """Pre-writing advisory board: 4 advisors deliberate, then consensus writing brief."""
        _log.info("=" * 40)
        _log.info("PIPELINE: Advisory Board — pre-writing deliberation")
        _log.info("=" * 40)
        if on_event:
            on_event(StepEvent("subtask_start", data="Advisory Board: deliberating before writing", depth=0))

        ws = self.config.workspace
        sections_dir = ws / self.config.session_root_dir / "output" / "sections"

        # Detect target journal for the journal reviewer
        from .peer_review import _detect_journal
        journal = self.config.peer_review_journal
        if journal == "auto":
            journal = _detect_journal(topic)
        _log.info("ADVISORY BOARD: Target journal: %s", journal)

        # Load synthesis data for advisor context
        synthesis_file = sections_dir / "synthesis_data.md"
        synthesis_data = ""
        if synthesis_file.exists():
            synthesis_data = synthesis_file.read_text(encoding="utf-8")[:8000]

        # ── Round 1: Independent advisor recommendations ──────
        advisor_plans: list[dict[str, str]] = []

        for advisor in self._ADVISOR_PERSONAS:
            # Inject journal-specific focus for journal reviewer
            if advisor["id"] == "journal_reviewer":
                advisor = dict(advisor)  # shallow copy to avoid mutating class attr
                advisor["focus"] = (
                    f"You are a senior reviewer and editorial board member at {journal}. "
                    f"You have reviewed 100+ papers for this journal and know exactly what gets published. "
                    f"Your focus is on: "
                    f"(a) whether this paper fits the scope and standards of {journal}, "
                    f"(b) what {journal} reviewers specifically look for (methodology rigor, contribution clarity, "
                    f"empirical grounding, theoretical novelty), "
                    f"(c) how to frame the contribution to maximize acceptance probability at {journal}, "
                    f"(d) common rejection reasons at {journal} and how to preempt them, "
                    f"(e) which recent papers published in {journal} this paper should cite and engage with, "
                    f"(f) the appropriate tone, structure, and presentation style for {journal}."
                )
            if self.cancel_flag.is_set():
                break

            _log.info("ADVISORY BOARD: %s analyzing evidence...", advisor["name"])
            if on_event:
                on_event(StepEvent("text", data=f"Advisory Board: {advisor['name']} analyzing...", depth=0))

            advisor_objective = (
                f"You are on the advisory board for a research paper on: {topic}. "
                f"{advisor['focus']}\n\n"
                f"You have {context.claim_count} claims from {context.papers_with_claims} deeply-read papers.\n"
                f"SYNTHESIS DATA (summary):\n{synthesis_data[:3000]}\n\n"
                f"INSTRUCTIONS:\n"
                f"1. Call list_claims() to review all extracted evidence.\n"
                f"2. Call get_risk_of_bias_table() and get_grade_table() to assess evidence quality.\n"
                f"3. Use search_similar() to find the most relevant papers for key themes.\n"
                f"4. Read 3-5 key papers using read_paper(paper_id=ID, include_fulltext=true).\n"
                f"5. Based on your analysis, produce a DETAILED advisory report with:\n"
                f"   - Your recommended narrative arc (what story should this paper tell?)\n"
                f"   - Section-by-section recommendations (what each section MUST cover)\n"
                f"   - Specific papers/claims to cite in each section (with paper_id and author names)\n"
                f"   - Potential weaknesses to address proactively\n"
                f"   - What makes this paper's contribution unique\n"
                f"6. Save your report using write_section(section='advisor_{advisor['id']}', content=YOUR_REPORT)"
            )

            system_prompt = build_phase_system_prompt(
                phase="advisory_board", topic=topic, rules=context.rules,
                paper_type=paper_type,
            )

            result = self._solve_recursive(
                objective=advisor_objective,
                context=context,
                depth=self.config.max_depth,
                on_event=on_event,
                system_prompt_override=system_prompt,
                phase="advisory_board",
                max_steps=40,
            )

            # Read the saved advisor report
            advisor_file = sections_dir / f"advisor_{advisor['id']}.md"
            report = ""
            if advisor_file.exists():
                report = advisor_file.read_text(encoding="utf-8")
            elif result:
                report = result

            advisor_plans.append({
                "advisor": advisor["name"],
                "id": advisor["id"],
                "report": report,
            })
            _log.info("ADVISORY BOARD: %s done — %d chars", advisor["name"], len(report))

        if not advisor_plans:
            _log.warning("ADVISORY BOARD: No advisor reports produced — skipping")
            return

        # ── Round 2: Consensus writing brief ──────────────────
        _log.info("ADVISORY BOARD: Synthesizing consensus writing brief...")
        if on_event:
            on_event(StepEvent("text", data="Advisory Board: synthesizing consensus brief...", depth=0))

        # Build combined advisor input
        advisor_summaries = ""
        for plan in advisor_plans:
            advisor_summaries += f"\n## {plan['advisor']}'s Recommendations\n"
            advisor_summaries += plan["report"][:4000] + "\n"

        consensus_objective = (
            f"You are the Senior Editor synthesizing the advisory board's recommendations "
            f"for a research paper on: {topic}.\n"
            f"TARGET JOURNAL: {journal}\n\n"
            f"Two advisors have analyzed the evidence and produced recommendations:\n"
            f"{advisor_summaries}\n\n"
            f"IMPORTANT: The Journal Reviewer's input is CRITICAL — they know what {journal} "
            f"accepts and rejects. Prioritize their framing and scope advice.\n\n"
            f"INSTRUCTIONS:\n"
            f"1. Call list_claims() to verify the advisors' evidence references.\n"
            f"2. Call list_papers(compact=true) to get exact author names for citations.\n"
            f"3. Resolve any DISAGREEMENTS between advisors — pick the stronger argument.\n"
            f"4. Produce the DEFINITIVE WRITING BRIEF with:\n\n"
            f"   ## Target Journal: {journal}\n"
            f"   (Key requirements, style expectations, what gets published vs rejected)\n\n"
            f"   ## Paper Narrative Arc\n"
            f"   (The overarching story: problem → gap → contribution → implications)\n\n"
            f"   ## Section-by-Section Blueprint\n"
            f"   For EACH section (abstract, introduction, literature_review, methods, results, discussion, conclusion):\n"
            f"   - Key argument/purpose of this section\n"
            f"   - Specific subsections and their content\n"
            f"   - MUST-CITE papers with (Author, Year) format — at least 5 per body section\n"
            f"   - Key claims to reference (by claim_id)\n"
            f"   - Transitions to next section\n\n"
            f"   ## Theoretical Framework\n"
            f"   (Which theories to use, how they connect, what the original contribution is)\n\n"
            f"   ## Evidence Hierarchy\n"
            f"   (Strongest evidence first, how to handle weak/conflicting evidence)\n\n"
            f"   ## Proactive Defense\n"
            f"   (Anticipated reviewer objections at {journal} and how to address them in the text)\n\n"
            f"5. Save using write_section(section='writing_brief', content=YOUR_BRIEF)"
        )

        system_prompt = build_phase_system_prompt(
            phase="advisory_board", topic=topic, rules=context.rules,
            paper_type=paper_type,
        )

        self._solve_recursive(
            objective=consensus_objective,
            context=context,
            depth=self.config.max_depth,
            on_event=on_event,
            system_prompt_override=system_prompt,
            phase="advisory_board",
            max_steps=50,
        )

        # Verify brief was saved
        brief_file = sections_dir / "writing_brief.md"
        if brief_file.exists():
            brief_size = len(brief_file.read_text(encoding="utf-8"))
            _log.info("ADVISORY BOARD: Writing brief saved — %d chars", brief_size)
        else:
            _log.warning("ADVISORY BOARD: Writing brief not saved — writer will proceed without it")

        # Clean up individual advisor files (they've been synthesized)
        for advisor in self._ADVISOR_PERSONAS:
            f = sections_dir / f"advisor_{advisor['id']}.md"
            if f.exists():
                f.unlink()

        if on_event:
            on_event(StepEvent("subtask_end", data="Advisory Board complete", depth=0))

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

        # Load writing brief from advisory board (if available)
        writing_brief = ""
        brief_file = sections_dir / "writing_brief.md"
        if brief_file.exists():
            writing_brief = brief_file.read_text(encoding="utf-8")
            _log.info("PIPELINE WRITER: Loaded writing brief from advisory board (%d chars)", len(writing_brief))

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

            # Extract section-specific guidance from writing brief
            brief_guidance = ""
            if writing_brief:
                # Try to extract the section's specific guidance from the brief
                import re as _re
                # Look for section header in brief (case-insensitive)
                section_patterns = [
                    section_name.replace("_", " "),
                    section_name.replace("_", " ").title(),
                    section_name,
                ]
                for pat in section_patterns:
                    match = _re.search(
                        rf"(?:^|\n)#+\s*.*{_re.escape(pat)}.*?\n(.*?)(?=\n#+\s|\Z)",
                        writing_brief, _re.IGNORECASE | _re.DOTALL,
                    )
                    if match:
                        brief_guidance = match.group(1).strip()[:2000]
                        break
                if not brief_guidance and len(writing_brief) < 6000:
                    # Brief is short enough to include entirely
                    brief_guidance = writing_brief[:3000]

            brief_instruction = ""
            if brief_guidance:
                brief_instruction = (
                    f"\n\nADVISORY BOARD GUIDANCE for this section:\n{brief_guidance}\n"
                    f"Follow this guidance closely — it was produced by expert advisors who analyzed all evidence.\n"
                )

            objective = (
                f"Write the '{section_name}' section for the research paper on: {topic}. "
                f"{instruction} "
                f"{brief_instruction}"
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

            # Auto-save: if the writer produced substantial text but didn't call write_section,
            # save it automatically. This prevents losing 10K+ chars of output.
            section_file = sections_dir / f"{section_name}.md"
            should_auto_save = False
            if len(result) > 500:
                if not section_file.exists():
                    should_auto_save = True
                elif section_file.stat().st_size < 100:
                    should_auto_save = True
                elif section_file.stat().st_size < len(result) * 0.5:
                    # Existing file is less than half the new content — writer probably
                    # produced a better version as text instead of calling write_section
                    should_auto_save = True
            if should_auto_save:
                _log.info("PIPELINE WRITER: Auto-saving %s (%d chars — writer didn't call write_section)",
                          section_name, len(result))
                section_file.write_text(result, encoding="utf-8")

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
        """Programmatic quality gate — fix mechanical failures (word count, citations, structure) before output assembly."""
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
            _log.info("PRE-CRITIC: All sections pass programmatic checks — proceeding to output assembly")
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

    # NOTE: LLM paper_critic removed — peer review handles qualitative evaluation post-output.
    # The programmatic quality gate (_pre_critic_validation) catches mechanical issues.

    def _prepopulate_verification_flags(self, central_db: Any, db: Any, session_id: int) -> None:
        """Bulk set verification flags from central DB cache — avoids per-paper API calls."""
        rows = db._conn.execute(
            "SELECT paper_id, doi FROM papers "
            "WHERE session_id = ? AND doi IS NOT NULL "
            "AND (doi_valid = 0 OR retraction_checked = 0 OR citation_verified = 0)",
            (session_id,),
        ).fetchall()
        if not rows:
            return

        flagged = 0
        for row in rows:
            doi = row["doi"].strip().lower()
            cached = central_db.get_doi_validation(doi)
            if not cached:
                continue

            updates = []
            params = []
            # DOI valid — if it's in doi_validations, it was validated before
            updates.append("doi_valid = 1")
            # Retraction checked
            if cached.get("retraction_permanent"):
                updates.append("retraction_checked = 1")
            # Citation count
            if cached.get("citation_count", 0) > 0:
                updates.append("citation_verified = 1")
                updates.append("citation_count = ?")
                params.append(cached["citation_count"])

            if updates:
                params.append(row["paper_id"])
                db._conn.execute(
                    f"UPDATE papers SET {', '.join(updates)} WHERE paper_id = ?",
                    params,
                )
                flagged += 1

        if flagged:
            db._conn.commit()
            _log.info("PIPELINE: Pre-populated verification flags for %d/%d papers from central DB", flagged, len(rows))

    def _preload_claims_from_central(self, central_db: Any, db: Any, session_id: int) -> None:
        """Pre-load claims from central DB into session DB so deep_read skips already-extracted papers."""
        papers = db._conn.execute(
            "SELECT paper_id, doi FROM papers WHERE session_id = ? AND doi IS NOT NULL AND selected_for_deep_read = 1",
            (session_id,),
        ).fetchall()
        if not papers:
            return

        # Check which papers already have claims in session DB
        papers_with_claims = set()
        for row in db._conn.execute(
            "SELECT DISTINCT paper_id FROM claims WHERE session_id = ?", (session_id,),
        ).fetchall():
            papers_with_claims.add(row["paper_id"])

        loaded = 0
        for paper in papers:
            if paper["paper_id"] in papers_with_claims:
                continue
            doi = paper["doi"].strip().lower()
            central_claims = central_db.get_claims_for_paper(doi)
            if not central_claims:
                continue
            for c in central_claims:
                db.store_claim(
                    session_id=session_id,
                    paper_id=paper["paper_id"],
                    claim_text=c.get("claim_text", ""),
                    claim_type=c.get("claim_type", "finding"),
                    confidence=c.get("confidence", 0.5),
                    supporting_quotes=c.get("supporting_quotes", "[]"),
                    section=c.get("section", ""),
                    sample_size=c.get("sample_size", ""),
                    effect_size=c.get("effect_size", ""),
                    p_value=c.get("p_value", ""),
                    confidence_interval=c.get("confidence_interval", ""),
                    study_design=c.get("study_design", ""),
                    population=c.get("population", ""),
                    country=c.get("country", ""),
                    year_range=c.get("year_range", ""),
                )
            loaded += 1

        if loaded:
            _log.info("PIPELINE: Pre-loaded claims for %d papers from central DB (skipping LLM deep read for those)", loaded)

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
            paper_cfg = get_paper_config(context.paper_type)
            if paper_cfg.requires_prisma:
                _log.info("PIPELINE: Generating PRISMA diagram")
                self.tools.dispatch("generate_prisma_diagram", {})
            else:
                _log.info("PIPELINE: Skipping PRISMA diagram (%s paper)", context.paper_type)
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
        # Light: mechanical phases (search, verify, triage)
        # Pro: deep analysis + writing
        # Hypothesis: Opus/GPT-5.4 load-balanced for gap identification
        if phase in ("hypothesis", "critic"):
            active_model = self.hypothesis_model
        elif phase in ("writer", "synthesis", "advisory_board"):
            active_model = self.writer_model
        elif phase == "analyst_deep_read":
            active_model = self.deep_read_model  # Flash 3.1 for speed — deep_read is high-volume extraction
        elif phase == "brancher":
            active_model = self.light_model  # Flash Lite — brancher is search-heavy, doesn't need Pro
        elif phase in ("scout", "verifier", "protocol"):
            active_model = self.light_model
        else:
            active_model = self.model
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

            # Early exit: deep_read stops before wasting steps on read/fetch when target is met
            if phase == "analyst_deep_read" and steps > 0:
                _db = self.tools.db
                _sid = self.tools.session_id
                if _db and _sid:
                    _cc = _db._conn.execute(
                        "SELECT COUNT(*) FROM claims WHERE session_id = ?", (_sid,)
                    ).fetchone()[0]
                    if _cc >= self.config.min_claims:
                        _log.info("DEEP_READ: Claim target reached (%d >= %d) — stopping at step %d",
                                  _cc, self.config.min_claims, steps)
                        if on_event:
                            on_event(StepEvent("text", data=f"Claim target reached: {_cc} claims. Moving to next phase.", depth=depth))
                        return last_text or f"Claim target reached: {_cc} claims extracted."

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
