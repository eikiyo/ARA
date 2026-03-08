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
                    "Extract ALL structured claims from papers — topic-agnostic comprehensive extraction. "
                    "STEP 1: Call list_papers(selected_only=true, needs_claims=true, limit=100) to get papers that STILL NEED claim extraction. "
                    "Papers that already have claims from the database are automatically excluded. "
                    "STEP 2: For EVERY listed paper: call read_paper(paper_id=ID, include_fulltext=true) to get the full paper text, "
                    "then extract_claims with specific quotes from the text, then assess_risk_of_bias. "
                    "Extract EVERY finding, theory, method, limitation, and gap — not just those about {topic}. "
                    "This enables cross-topic reuse. Each claim needs: claim_text, claim_type (finding/theory/method/limitation/gap), "
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
                    "MMR-diversified evidence is pre-loaded below — this is your PRIMARY evidence source. "
                    "It covers causal, moderator, contrarian, synthesis, and methodological themes from {papers_with_claims} papers. "
                    "Call get_risk_of_bias_table() to understand which evidence is strongest. "
                    "Call get_grade_table() to see evidence certainty ratings. "
                    "For the 3-5 most important papers from the evidence, call read_paper(paper_id=ID, include_fulltext=true) to verify key claims. "
                    "Generate hypotheses across these categories: "
                    "(a) 3+ causal mechanism hypotheses, (b) 3+ moderator/boundary condition hypotheses, "
                    "(c) 3+ novel synthesis hypotheses connecting disparate findings, "
                    "(d) 2+ contrarian hypotheses challenging conventional wisdom, "
                    "(e) 2+ methodological hypotheses about measurement/design. "
                    "Score each: novelty, feasibility, evidence_strength, methodology_fit, impact, reproducibility. "
                    "For the top 3 hypotheses, specify: methodology, analysis approach, quality assessment framework. "
                    "Use score_hypothesis to evaluate EVERY one. "
                    "Ground every hypothesis in specific claims from the pre-loaded evidence — do NOT hypothesize beyond it. "
                    "{special_instructions}"
                ),
            },
            {
                "name": "critic",
                "prompt": "critic",
                "objective": (
                    "MMR-diversified evidence is pre-loaded below — this includes counter-arguments, "
                    "contradictions, and alternative explanations from the full evidence base. "
                    "Call get_risk_of_bias_table() to assess evidence quality. "
                    "Call get_grade_table() to see evidence certainty ratings. "
                    "Read 5-8 key papers using read_paper(paper_id=ID, include_fulltext=true) to ground your evaluation. "
                    "THEN evaluate the top 3 hypotheses across 8 dimensions each. "
                    "Verify the novelty framework label (INVERSION/MISSING LINK/MODERATOR/etc). "
                    "Apply the meta-test: would an expert believe something different? "
                    "Cross-reference claims against RoB ratings — discount claims from high-bias papers. "
                    "Use the CONTRADICTIONS section to identify claims that need reconciliation. "
                    "Decide per hypothesis: APPROVE or REJECT with detailed feedback and specific revisions."
                ),
            },
            {
                "name": "synthesis",
                "prompt": "synthesis",
                "objective": (
                    "Prepare structured data for the writer. "
                    "MMR-diversified evidence is pre-loaded below — organized by theme with convergent evidence clusters. "
                    "Call list_papers(compact=true) to get exact author names. "
                    "Call get_risk_of_bias_table() to get RoB assessments for all papers. "
                    "Call get_grade_table() to get GRADE evidence certainty ratings. "
                    "For top 5-10 papers from the evidence, call read_paper(paper_id=ID, include_fulltext=true). "
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
                    "Extract ALL theoretical arguments and evidence from papers — topic-agnostic comprehensive extraction. "
                    "STEP 1: Call list_papers(selected_only=true, needs_claims=true, limit=100) to get papers that STILL NEED claim extraction. "
                    "Papers that already have claims from the database are automatically excluded. "
                    "STEP 2: For EVERY listed paper: call read_paper(paper_id=ID, include_fulltext=true) to get the full paper text, then extract_claims. "
                    "Extract EVERYTHING from the paper, not just content about {topic}: "
                    "(a) theoretical arguments and frameworks proposed, "
                    "(b) key constructs and definitions, (c) ALL empirical findings, "
                    "(d) research gaps and limitations identified, (e) boundary conditions discussed, "
                    "(f) methodological insights. "
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
                    "MMR-diversified evidence is pre-loaded below — this is your PRIMARY evidence source. "
                    "It covers causal, moderator, contrarian, synthesis, and methodological themes from {papers_with_claims} papers. "
                    "Call get_risk_of_bias_table() to understand which evidence is strongest. "
                    "Call get_grade_table() to see evidence certainty ratings. "
                    "For the 3-5 most important papers from the evidence, call read_paper(paper_id=ID, include_fulltext=true) to verify key claims. "
                    "Each framework should be a TYPOLOGY, PROCESS MODEL, or MULTI-LEVEL FRAMEWORK. "
                    "Generate across categories: (a) 3+ integration frameworks, (b) 3+ process models, "
                    "(c) 2+ multi-level frameworks, (d) 2+ contrarian frameworks, (e) 2+ boundary condition models. "
                    "Score each: novelty (2x weight), feasibility, evidence_strength, methodology_fit, "
                    "impact, reproducibility. Answer the Five Questions for the top 3 frameworks. "
                    "Use score_hypothesis to evaluate EVERY one. "
                    "Ground every framework in specific claims from the pre-loaded evidence — do NOT theorize beyond it. "
                    "{special_instructions}"
                ),
            },
            {
                "name": "critic",
                "prompt": "critic",
                "objective": (
                    "MMR-diversified evidence is pre-loaded below — this includes counter-arguments, "
                    "contradictions, and alternative explanations from the full evidence base. "
                    "Call get_risk_of_bias_table() to assess evidence quality. "
                    "Call get_grade_table() to see evidence certainty ratings. "
                    "Read 5-8 key theoretical papers using read_paper(paper_id=ID, include_fulltext=true) to ground your evaluation. "
                    "THEN evaluate the top 3 frameworks across 8 dimensions each. "
                    "Verify the novelty framework label (INVERSION/MISSING LINK/MODERATOR/etc). "
                    "Apply the meta-test: would a management scholar believe something different? "
                    "Cross-reference claims against RoB ratings — discount claims from high-bias papers. "
                    "Use the CONTRADICTIONS section to identify claims that need reconciliation. "
                    "Decide per framework: APPROVE or REJECT with detailed feedback."
                ),
            },
            {
                "name": "synthesis",
                "prompt": "synthesis",
                "objective": (
                    "Prepare structured theoretical data for the writer. "
                    "MMR-diversified evidence is pre-loaded below — organized by theme with convergent evidence clusters. "
                    "Call list_papers(compact=true) to get exact author names. "
                    "Call get_risk_of_bias_table() to get RoB assessments for all papers. "
                    "Call get_grade_table() to get GRADE evidence certainty ratings. "
                    "For top 5-10 papers from the evidence, call read_paper(paper_id=ID, include_fulltext=true). "
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
                "Use (Author, Year) citations. Call list_papers first. "
                "ANTI-REPETITION: State the gap ONCE in the Purpose line. Do NOT restate "
                "the gap in Findings or Originality — refer to it, don't repeat it."
            )),
            ("introduction", (
                f"Write the introduction ({c.words_introduction}+ words). Include: opening hook, "
                f"the theoretical puzzle, explicit gap statement, framework preview, "
                f"contribution statement (theory + practice), {c.cites_introduction}+ citations. "
                f"Be PRECISE about contribution claims — acknowledge related prior work "
                f"before stating what THIS paper adds. "
                f"ANTI-REPETITION: This is the ONE place to state the gap and contribution "
                f"fully. All other sections should REFERENCE the intro's gap statement, "
                f"not restate it. Write 'As argued in the introduction...' or 'The gap "
                f"identified above...' instead of restating the same language."
            )),
            ("methodology", (
                "Write the methodology section (400-500 words). Even conceptual papers must explain "
                "the literature selection approach. Cover: (1) Research design and approach — "
                "state this is a conceptual paper using integrative literature review methodology. "
                "(2) Database search strategy — which databases (Scopus, Web of Science, OpenAlex, "
                "Semantic Scholar), search terms used, date range, inclusion/exclusion criteria. "
                "(3) Paper selection — how many initial results, screening process, final sample size. "
                "(4) Analytical approach — how claims were extracted, how theoretical streams were identified, "
                "how the framework was developed. Be concise and precise. "
                "Do NOT pad with philosophical justifications for conceptual research."
            )),
            ("theoretical_background", (
                f"Write the theoretical background ({c.words_theoretical_background}+ words). "
                f"Cover 2-3 theoretical streams as subsections. For each: foundational works, "
                f"key developments, current state, AND limitations. "
                f"Show where streams converge and conflict. {c.cites_literature_review}+ citations. "
                f"This is the ONLY section that should contain construct definition tables "
                f"and competing framework comparison tables. Define all key constructs HERE "
                f"with precise names — later sections must use the EXACT same names. "
                f"End with a clear 'Theoretical Gap' subsection showing what the three streams "
                f"together cannot explain. Boundary conditions go HERE, not in framework or discussion. "
                f"ANTI-REPETITION: Do NOT restate the gap from the introduction."
            )),
            ("framework", (
                f"Write the framework development section ({c.words_framework}+ words). "
                "This section describes the CONCEPTUAL MODEL ONLY. "
                "STRICT RULES: "
                "(a) Do NOT include any definition tables — those are in theoretical_background. "
                "(b) Do NOT include comparison tables with existing frameworks — already done. "
                "(c) Do NOT include boundary conditions — already in theoretical_background. "
                "(d) Do NOT include formal propositions — those go in the NEXT section. "
                "(e) Do NOT create new construct names — use the EXACT names from theoretical_background. "
                "WHAT TO INCLUDE: "
                "(1) The integrative logic — how the theoretical streams connect into ONE framework. "
                "(2) Maximum 2-3 novel constructs total (ONE primary, others subordinate). "
                "Name them clearly and consistently. "
                "(3) Causal mechanisms — explain the logic connecting antecedents → mechanisms → outcomes. "
                "(4) Use domain-specific examples. 15+ citations. "
                "NO ASCII diagrams — describe the model in prose. "
                "Keep it tight. Every paragraph must advance the framework, not repeat background."
            )),
            ("propositions", (
                f"Write the propositions section ({c.words_propositions}+ words). "
                "FORMAL TESTABLE PROPOSITIONS derived from the framework. "
                "STRICT RULES: "
                "(a) Present 3-4 propositions MAXIMUM. Fewer is better if argued deeply. "
                "(b) Do NOT repeat framework description — just reference it. "
                "(c) Do NOT re-define constructs — use names from theoretical_background. "
                "(d) Do NOT include definition tables or comparison tables. "
                "(e) Use the EXACT construct names from framework section — no renaming. "
                "For EACH proposition: formal statement, 2-3 paragraphs of justification, "
                "supporting evidence, boundary conditions. 3+ citations per proposition. "
                "End with ONE summary table — descriptions must MATCH proposition text exactly. "
                "NOVELTY FILTER: Drop any proposition that restates well-established findings. "
                "If the synthesis phase produced REVISED propositions, use those as authoritative."
            )),
            ("discussion", (
                f"Write the discussion ({c.words_discussion}+ words). "
                "STRICT RULES — the discussion MUST NOT contain: "
                "(a) Any tables (no comparison tables, no definition tables, no before/after tables). "
                "(b) Boundary conditions (already in theoretical_background). "
                "(c) Construct definitions (already in theoretical_background). "
                "(d) Framework re-description (already in framework section). "
                "(e) Gap restatement (already in introduction). "
                "(f) New propositions or re-labeled propositions — propositions are ONLY in the propositions section. "
                "Do NOT introduce P1, P2, etc. in this section. REFER to propositions by their original labels. "
                "(g) New construct names — use ONLY the construct names established in theoretical_background "
                "and framework. Do NOT invent alternative names for the same concepts. "
                "WHAT TO INCLUDE: "
                "(1) How the framework resolves the theoretical tensions from the lit review — "
                "in prose, not tables. Start with 'This paper developed...' "
                "(2) Specific managerial implications — concrete, actionable, not generic. "
                "(3) Limitations (2-3 sentences, not a subsection). "
                "(4) Future research agenda: 3-5 specific empirical studies with methodologies. "
                f"{c.cites_discussion}+ citations. Be concise. No subsection headers for limitations."
            )),
            ("conclusion", (
                f"Write the conclusion ({c.words_conclusion}+ words). "
                "Do NOT summarize the paper — the reader just read it. "
                "Do NOT restate the gap, framework, or propositions. "
                "Do NOT include any tables. "
                "Include ONLY: (1) The single most important theoretical insight in 2-3 sentences. "
                "(2) One concrete implication for practice. "
                "(3) A forward-looking closing statement. "
                "If the author has a positionality statement or conflict of interest "
                "(e.g., works at a company mentioned as a case), include it as the final paragraph."
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
                        self._preload_claims_from_central(central_db, db, session_id, topic=topic)

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

                    # Run fetch+embed synchronously — deep_read needs full texts
                    _run_fetch()
                    _run_embed()

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
                elif name == "scout":
                    # Programmatic scout — no LLM needed
                    self._pipeline_scout_programmatic(topic, paper_type, context, on_event)
                elif name == "snowball":
                    self._pipeline_snowball(topic, on_event)
                elif name == "triage":
                    self._pipeline_triage_parallel(topic, paper_type, context, on_event, completed)
                elif name == "protocol":
                    # Programmatic protocol + verifier — no LLM needed
                    self._pipeline_protocol_template(topic, paper_type, on_event)
                    if "verifier" not in completed:
                        self._pipeline_verify_batch(on_event)
                        if db and session_id:
                            db.save_phase_checkpoint(session_id, "verifier")
                        completed.add("verifier")
                elif name == "verifier":
                    # Already handled with protocol above, but handle standalone case
                    self._pipeline_verify_batch(on_event)
                elif name == "brancher":
                    # Programmatic brancher — already handled after deep_read in most cases
                    self._pipeline_brancher_programmatic(topic, on_event)
                elif name == "synthesis":
                    # Programmatic synthesis — tables from DB + short LLM narrative
                    self._pipeline_synthesis_programmatic(topic, paper_type, context, on_event)
                else:
                    # Quality gate: hypothesis/synthesis/writer require 30+ papers with claims
                    if name in ("hypothesis", "synthesis", "writer") and db and session_id:
                        _PHASE_MIN_PAPERS = 30
                        _phase_claims = db.get_claims(session_id)
                        _phase_papers_with_claims = len(set(c["paper_id"] for c in _phase_claims))
                        if _phase_papers_with_claims < _PHASE_MIN_PAPERS:
                            _log.error(
                                "PIPELINE GATE: %s requires %d+ papers with claims, only have %d — BLOCKING",
                                name, _PHASE_MIN_PAPERS, _phase_papers_with_claims,
                            )
                            if on_event:
                                on_event(StepEvent("error", data=(
                                    f"Quality gate: {name} blocked — need {_PHASE_MIN_PAPERS} papers "
                                    f"with claims, only have {_phase_papers_with_claims}"
                                ), depth=0))
                            continue
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
                # Run programmatic brancher after deep_read (no threading needed — it's fast)
                claims = db.get_claims(session_id)
                if ("brancher" not in completed
                        and len(claims) >= 50 and not self.cancel_flag.is_set()):
                    self._pipeline_brancher_programmatic(topic, on_event)
                    if db and session_id:
                        db.save_phase_checkpoint(session_id, "brancher")
                    completed.add("brancher")

                # Deduplicate claims
                self._deduplicate_claims(db, session_id)

                paper_cfg = get_paper_config(paper_type)
                if paper_cfg.requires_prisma:
                    self._finalize_prisma_exclusions(db, session_id)

            # C. Critic rejection loop — if critic rejected, re-run hypothesis with feedback
            if name == "critic" and db and session_id:
                pipeline_dir = self.config.workspace / self.config.session_root_dir / "output" / "pipeline"
                critic_file = pipeline_dir / "critic.md"
                hypothesis_def = next(
                    (p for p in self._get_pipeline_phases() if p["name"] == "hypothesis"), None
                )
                if critic_file.exists() and hypothesis_def:
                    critic_text = critic_file.read_text(encoding="utf-8")
                    _MAX_CRITIC_LOOPS = 2  # max re-runs (total = 3 attempts including original)
                    for loop_i in range(_MAX_CRITIC_LOOPS):
                        if self.cancel_flag.is_set():
                            break
                        # Check for REJECT signal in critic output
                        critic_lower = critic_text.lower()
                        has_reject = "reject" in critic_lower and "approve" not in critic_lower
                        # D. Critic tool enforcement — if critic output is too short, it didn't use tools
                        critic_too_short = len(critic_text) < 500
                        if not has_reject and not critic_too_short:
                            break
                        if critic_too_short:
                            _log.warning("PIPELINE: Critic output too short (%d chars) — re-running with tool enforcement", len(critic_text))
                            # Re-run critic with stronger tool-use instructions
                            critic_def = next(
                                (p for p in self._get_pipeline_phases() if p["name"] == "critic"), None
                            )
                            if critic_def:
                                enforced_def = dict(critic_def)
                                enforced_def["objective"] = (
                                    "YOU MUST USE TOOLS BEFORE RESPONDING. This is mandatory.\n"
                                    "Step 1: Call search_similar(text='<hypothesis core claim>') to find supporting/contradicting evidence.\n"
                                    "Step 2: Call list_claims() to review extracted claims.\n"
                                    "Step 3: Call list_papers(compact=true) to check citation coverage.\n"
                                    "Step 4: ONLY THEN evaluate the hypothesis against the evidence.\n"
                                    "If your response is under 500 characters, it will be REJECTED.\n\n"
                                    + enforced_def["objective"]
                                )
                                self._pipeline_run_phase(enforced_def, topic, paper_type, context, on_event)
                                if critic_file.exists():
                                    critic_text = critic_file.read_text(encoding="utf-8")
                                continue
                        _log.info("PIPELINE: Critic REJECTED hypothesis (loop %d/%d) — re-running hypothesis with feedback",
                                  loop_i + 1, _MAX_CRITIC_LOOPS)
                        if on_event:
                            on_event(StepEvent("text", data=f"Critic rejected — revising hypothesis (attempt {loop_i + 2})", depth=0))
                        # Inject critic feedback into hypothesis re-run
                        revised_hyp_def = dict(hypothesis_def)
                        revised_hyp_def["objective"] = (
                            hypothesis_def["objective"]
                            + f"\n\nCRITIC FEEDBACK (MUST ADDRESS):\n{critic_text[:3000]}\n"
                            f"The critic REJECTED the previous hypothesis. Revise based on their specific objections."
                        )
                        self._pipeline_run_phase(revised_hyp_def, topic, paper_type, context, on_event)
                        # Re-run critic on revised hypothesis
                        critic_def = next(
                            (p for p in self._get_pipeline_phases() if p["name"] == "critic"), None
                        )
                        if critic_def:
                            self._pipeline_run_phase(critic_def, topic, paper_type, context, on_event)
                            if critic_file.exists():
                                critic_text = critic_file.read_text(encoding="utf-8")

            if db and session_id:
                db.save_phase_checkpoint(session_id, name)
            if on_event:
                on_event(StepEvent("subtask_end", data=f"Phase {name} complete", depth=0))

        # Advisory board — unified writing brief
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

        # B. Brancher → Hypothesis/Critic/Synthesis/Advisory handover
        if prompt_name in ("hypothesis", "critic", "synthesis", "advisory_board"):
            pipeline_dir = self.config.workspace / self.config.session_root_dir / "output" / "pipeline"
            brancher_file = pipeline_dir / "brancher.md"
            if brancher_file.exists():
                brancher_output = brancher_file.read_text(encoding="utf-8")[:8000]
                _phase_brancher_instructions = {
                    "hypothesis": (
                        "Use these SCAMPER cross-domain insights to generate novel hypotheses. "
                        "At least 3 hypotheses MUST build on cross-domain analogies from the brancher. "
                        "Map each analogy explicitly: source domain → mechanism → application to topic."
                    ),
                    "critic": (
                        "Evaluate whether cross-domain hypotheses have valid analogical mappings. "
                        "Check: is the source domain's mechanism actually transferable? "
                        "Flag any false analogies where surface similarity masks structural differences."
                    ),
                    "synthesis": (
                        "Include cross-domain evidence in your synthesis tables. "
                        "Create a dedicated 'Cross-Domain Evidence Map' showing source fields and transferable insights."
                    ),
                    "advisory_board": (
                        "Assess which SCAMPER insights are strongest for the paper's contribution. "
                        "Recommend which cross-domain analogies the writer should emphasize."
                    ),
                }
                instruction = _phase_brancher_instructions.get(prompt_name, "")
                objective += (
                    f"\n\nSCAMPER CROSS-DOMAIN INSIGHTS FROM BRANCHER PHASE:\n{brancher_output}\n"
                    f"{instruction}\n"
                )
                _log.info("PIPELINE: Injected brancher output (%d chars) into %s phase", len(brancher_output), prompt_name)

        # MMR-diversified evidence injection for analytical phases
        if prompt_name in ("hypothesis", "critic", "synthesis"):
            mmr_context = self._build_mmr_context_for_phase(prompt_name, topic)
            if mmr_context:
                objective += mmr_context
                _log.info("PIPELINE: Injected MMR evidence (%d chars) into %s phase", len(mmr_context), prompt_name)

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
            "{journal_tier_ratio}": f"{self.config.journal_tier_ratio:.0f}",
            "{journal_tier_min_pct_display}": f"{self.config.journal_tier_min_pct:.0%}",
        }
        for placeholder, value in _prompt_subs.items():
            system_prompt = system_prompt.replace(placeholder, value)

        # Use per-phase step budget if defined
        step_budget = self._phase_step_budgets().get(phase_def["name"], self.config.max_steps_per_call)

        # #7: Pre-filter no-content papers before deep_read to save LLM steps
        if phase_def["name"] == "deep_read" and self.tools.db and self.tools.session_id:
            try:
                # Mark papers with no full text AND no abstract as not needing deep_read
                no_content = self.tools.db._conn.execute(
                    "SELECT paper_id FROM papers WHERE session_id = ? AND selected_for_deep_read = 1 "
                    "AND (full_text IS NULL OR full_text = '') "
                    "AND (abstract IS NULL OR abstract = '' OR length(abstract) < 50)",
                    (self.tools.session_id,),
                ).fetchall()
                if no_content:
                    pids = [r["paper_id"] for r in no_content]
                    self.tools.db._conn.execute(
                        f"UPDATE papers SET selected_for_deep_read = 0 WHERE paper_id IN ({','.join('?' * len(pids))})",
                        pids,
                    )
                    self.tools.db._conn.commit()
                    _log.info("DEEP_READ: Pre-filtered %d papers with no text/abstract — saving LLM steps", len(pids))
            except Exception as exc:
                _log.warning("DEEP_READ: Pre-filter failed: %s", exc)

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

        # Save phase output to pipeline folder for phases that produce analytical text
        if phase_def["name"] in ("hypothesis", "critic", "synthesis") and result and len(result) > 100:
            try:
                pipeline_dir = self.config.workspace / self.config.session_root_dir / "output" / "pipeline"
                pipeline_dir.mkdir(parents=True, exist_ok=True)
                out_file = pipeline_dir / f"{phase_def['name']}.md"
                out_file.write_text(result, encoding="utf-8")
                _log.info("PIPELINE: Saved %s output to %s (%d chars)", phase_def["name"], out_file, len(result))
            except Exception as exc:
                _log.warning("PIPELINE: Failed to save %s output: %s", phase_def["name"], exc)

        return result

    def _build_mmr_context_for_phase(self, phase: str, topic: str) -> str:
        """Build MMR-diversified evidence context for hypothesis/critic/synthesis phases."""
        central_db = self.tools.central_db
        if not central_db:
            return ""

        try:
            from .tools.pipeline import _get_api_key
            api_key = _get_api_key()
            if not api_key:
                return ""

            from google import genai
            client = genai.Client(api_key=api_key)

            # Phase-specific queries for targeted MMR retrieval
            _phase_queries = {
                "hypothesis": [
                    (f"{topic} causal mechanisms drivers", "causal"),
                    (f"{topic} moderating conditions boundary", "moderator"),
                    (f"{topic} contradictions conflicting evidence", "contrarian"),
                    (f"{topic} novel connections cross-domain synthesis", "synthesis"),
                    (f"{topic} methodology measurement design gaps", "method"),
                ],
                "critic": [
                    (f"{topic} limitations weaknesses methodology", "weakness"),
                    (f"{topic} contradicting evidence counter-arguments", "counter"),
                    (f"{topic} alternative explanations rival hypotheses", "alternative"),
                    (f"{topic} replication validity robustness", "validity"),
                ],
                "synthesis": [
                    (f"{topic} key findings empirical evidence", "findings"),
                    (f"{topic} theoretical framework conceptual model", "theory"),
                    (f"{topic} research gaps future directions", "gaps"),
                    (f"{topic} practical implications policy recommendations", "implications"),
                ],
            }

            queries = _phase_queries.get(phase, [])
            if not queries:
                return ""

            context_parts: list[str] = []
            seen_claims: set[int] = set()  # Deduplicate across queries
            total_claims = 0

            for query_text, label in queries:
                try:
                    emb_result = client.models.embed_content(
                        model="gemini-embedding-001", contents=query_text[:500],
                    )
                    if not emb_result.embeddings:
                        continue
                    query_emb = emb_result.embeddings[0].values

                    # MMR claims — diverse, max 3 per paper
                    claims = central_db.search_claims_mmr(
                        query_emb, limit=15, min_cosine=0.40, lam=0.65, paper_cap=3,
                    )
                    # Deduplicate
                    new_claims = [c for c in claims if c.get("claim_id") not in seen_claims]
                    for c in new_claims:
                        seen_claims.add(c.get("claim_id"))

                    if new_claims:
                        section = f"\n### {label.upper()} EVIDENCE ({len(new_claims)} claims, MMR-diversified):\n"
                        for c in new_claims[:10]:
                            meta = ""
                            if c.get("effect_size"):
                                meta += f" effect={c['effect_size']}"
                            if c.get("p_value"):
                                meta += f" p={c['p_value']}"
                            if c.get("sample_size"):
                                meta += f" N={c['sample_size']}"
                            section += (
                                f"- [{c.get('claim_type', 'finding')}] {c['claim_text'][:200]} "
                                f"(paper: {c.get('paper_title', '')[:60]}, "
                                f"sim={c.get('cosine_similarity', '?')}{meta})\n"
                            )
                        context_parts.append(section)
                        total_claims += len(new_claims[:10])

                    # MMR chunks for grounded evidence (hypothesis + synthesis only)
                    if phase in ("hypothesis", "synthesis"):
                        chunks = central_db.search_chunks_mmr(
                            query_emb, limit=4, min_cosine=0.45, lam=0.6, paper_cap=1,
                        )
                        if chunks:
                            chunk_section = f"  Full-text excerpts ({label}):\n"
                            for ch in chunks[:3]:
                                chunk_section += (
                                    f"  --- [{ch.get('paper_title', '')[:50]}] ---\n"
                                    f"  {ch['chunk_text'][:250]}...\n\n"
                                )
                            context_parts.append(chunk_section)

                except Exception as exc:
                    _log.debug("MMR query failed for %s/%s: %s", phase, label, exc)
                    continue

            if not context_parts:
                return ""

            # Add contradictions for critic phase
            if phase == "critic":
                try:
                    contradictions = central_db.detect_contradictions(limit=8)
                    if contradictions:
                        contra = "\n### CONTRADICTIONS DETECTED:\n"
                        for ct in contradictions[:5]:
                            contra += (
                                f"- CLAIM A ({ct['signal_a']}): {ct['claim_a']['claim_text'][:120]} "
                                f"[{ct['claim_a'].get('paper_title', '')[:40]}]\n"
                                f"  vs CLAIM B ({ct['signal_b']}): {ct['claim_b']['claim_text'][:120]} "
                                f"[{ct['claim_b'].get('paper_title', '')[:40]}]\n"
                                f"  Similarity: {ct['similarity']}\n\n"
                            )
                        context_parts.append(contra)
                except Exception:
                    pass

            # Add evidence clusters for synthesis phase
            if phase == "synthesis":
                try:
                    clusters = central_db.cluster_claims(similarity_threshold=0.85)
                    if clusters:
                        cluster_text = "\n### CONVERGENT EVIDENCE CLUSTERS:\n"
                        for i, cl in enumerate(clusters[:10], 1):
                            rep = cl["representative"]
                            cluster_text += (
                                f"Cluster {i}: {rep['claim_text'][:120]}\n"
                                f"  {cl['n_studies']} studies, {cl['n_claims']} claims\n"
                            )
                            if cl["countries"]:
                                cluster_text += f"  Countries: {', '.join(cl['countries'][:5])}\n"
                            if cl["effect_sizes"]:
                                cluster_text += f"  Effects: {', '.join(cl['effect_sizes'][:3])}\n"
                        context_parts.append(cluster_text)
                except Exception:
                    pass

            header = (
                f"\n\n{'='*60}\n"
                f"MMR-DIVERSIFIED EVIDENCE FROM CENTRAL DATABASE ({total_claims} claims across {len(seen_claims)} unique sources)\n"
                f"This evidence is pre-retrieved using Maximal Marginal Relevance to maximize diversity.\n"
                f"Use these claims as grounding — cite the paper titles and verify with read_paper() for key claims.\n"
                f"{'='*60}\n"
            )
            return header + "".join(context_parts)

        except Exception as exc:
            _log.warning("_build_mmr_context_for_phase failed for %s: %s", phase, exc)
            return ""

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
        _MAX_BATCHES = 8
        _BATCH_COOLDOWN = 30
        _MIN_PAPERS_HARD = 30  # Hard minimum — never stop below this

        for batch_num in range(1, _MAX_BATCHES + 1):
            # Stop if claim target met (with 10% tolerance) OR we've processed most selected papers
            # BUT never stop below _MIN_PAPERS_HARD papers with claims
            _close_enough = int(self.config.min_claims * 0.90)
            if papers_with_claims >= _MIN_PAPERS_HARD:
                if len(claims) >= _close_enough:
                    _log.info("PIPELINE: Deep read claim target met (%d claims, %d papers)", len(claims), papers_with_claims)
                    break
                if papers_with_claims >= self.config.min_deep_read_papers:
                    _log.info("PIPELINE: Deep read paper target met (%d claims, %d papers)", len(claims), papers_with_claims)
                    break
            else:
                _log.info("PIPELINE: Deep read below hard minimum (%d/%d papers) — continuing",
                          papers_with_claims, _MIN_PAPERS_HARD)
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
                f"CONTINUE extracting ALL claims from papers — topic-agnostic comprehensive extraction. "
                f"You have {len(claims)} claims from {papers_with_claims} papers so far. "
                f"Target: {self.config.min_claims}+ claims from {self.config.min_deep_read_papers}+ papers. "
                f"Call list_papers(selected_only=true, needs_claims=true, limit={_BATCH_SIZE}) to get papers that STILL NEED claims. "
                f"Papers already processed are automatically excluded. "
                f"Process ALL listed papers. "
                f"Extract EVERY finding, theory, method, limitation, and gap from each paper — not just content about {topic}. "
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

        # HARD GATE: If still below minimum, this is fatal
        if len(claims) == 0:
            _log.error("PIPELINE: FATAL — 0 claims after all deep_read batches. Cannot produce a credible paper.")
        elif papers_with_claims < _MIN_PAPERS_HARD:
            _log.error("PIPELINE: WARNING — only %d papers with claims (minimum %d). Paper quality may suffer.",
                        papers_with_claims, _MIN_PAPERS_HARD)
            if on_event:
                on_event(StepEvent("error", data="FATAL: 0 claims extracted. Pipeline cannot continue without evidence.", depth=0))
            return

        # Store claim/paper counts in context for downstream phases
        context.claim_count = len(claims)
        context.papers_with_claims = papers_with_claims

    # ── Programmatic phases (no LLM) ─────────────────────────────

    def _pipeline_scout_programmatic(
        self, topic: str, paper_type: str, context: ExternalContext,
        on_event: StepCallback | None,
    ) -> None:
        """Programmatic scout: generate query variants and call search_all for each."""
        _log.info("PIPELINE: Programmatic scout — generating query variants")
        if on_event:
            on_event(StepEvent("subtask_start", data="Scout: programmatic multi-query search", depth=0))

        # Generate query variants from the topic
        base = topic.strip()
        queries = [base]
        # Variant 1: quoted core phrase
        words = base.split()
        if len(words) > 3:
            queries.append(f'"{" ".join(words[:4])}" {" ".join(words[4:])}')
        # Variant 2: broader — first 3 key nouns
        stop = {"the", "of", "in", "and", "a", "an", "for", "on", "to", "with", "from", "by", "as", "is", "are", "at"}
        key_words = [w for w in words if w.lower() not in stop and len(w) > 2][:5]
        if key_words and len(key_words) >= 2:
            queries.append(" ".join(key_words))
        # Variant 3: add "systematic review" or "framework" depending on paper type
        if paper_type == "conceptual":
            queries.append(f"{base} theoretical framework")
            queries.append(f"{base} conceptual model propositions")
        else:
            queries.append(f"{base} systematic review")
            queries.append(f"{base} meta-analysis")

        # Special authors as query
        if self.config.special_authors:
            for author in self.config.special_authors.split(",")[:3]:
                author = author.strip()
                if author:
                    queries.append(f"{author} {' '.join(key_words[:3])}")

        # Deduplicate
        seen = set()
        unique_queries = []
        for q in queries:
            q_lower = q.lower().strip()
            if q_lower not in seen:
                seen.add(q_lower)
                unique_queries.append(q)

        _log.info("PIPELINE SCOUT: %d unique queries: %s", len(unique_queries), unique_queries)

        total_found = 0
        for i, query in enumerate(unique_queries):
            if self.cancel_flag.is_set():
                break
            # Check if we already have enough papers
            if self.tools.db and self.tools.session_id:
                existing = self.tools.db.paper_count(self.tools.session_id)
                if existing >= self.config.min_papers:
                    _log.info("PIPELINE SCOUT: Target reached (%d papers) — stopping", existing)
                    break

            _log.info("PIPELINE SCOUT: Query %d/%d: %s", i + 1, len(unique_queries), query[:80])
            if on_event:
                on_event(StepEvent("text", data=f"Scout query {i+1}/{len(unique_queries)}: {query[:60]}", depth=0))

            result = self.tools.dispatch("search_all", {"query": query, "limit": 30})
            try:
                data = json.loads(result)
                total_found += data.get("total", 0)
            except Exception:
                pass

        # Run snowball after search
        self._pipeline_snowball(topic, on_event)

        final_count = self.tools.db.paper_count(self.tools.session_id) if self.tools.db and self.tools.session_id else 0
        _log.info("PIPELINE SCOUT: Complete — %d papers in DB", final_count)
        if on_event:
            on_event(StepEvent("subtask_end", data=f"Scout complete: {final_count} papers", depth=0))

    def _pipeline_protocol_template(
        self, topic: str, paper_type: str,
        on_event: StepCallback | None,
    ) -> None:
        """Generate research protocol from template — no LLM needed."""
        _log.info("PIPELINE: Generating protocol from template")
        if on_event:
            on_event(StepEvent("text", data="Generating research protocol (template)...", depth=0))

        c = self.config
        import datetime
        current_year = datetime.datetime.now().year

        if paper_type == "conceptual":
            protocol = (
                f"# Research Protocol\n\n"
                f"## Title\n{topic}\n\n"
                f"## Paper Type\nConceptual/Theoretical Paper\n\n"
                f"## Research Questions\n"
                f"1. What are the key theoretical streams relevant to {topic}?\n"
                f"2. What framework best integrates the identified theoretical tensions?\n"
                f"3. What testable propositions emerge from the integrative framework?\n\n"
                f"## Theoretical Streams\n"
                f"To be identified during literature analysis phase.\n\n"
                f"## Framework Development Approach\n"
                f"- Typology development\n- Process model construction\n"
                f"- Multi-level framework integration\n- Proposition derivation\n\n"
                f"## Inclusion Criteria\n"
                f"- Published between {c.search_start_year} and {current_year}\n"
                f"- Peer-reviewed journal articles, book chapters, and conference proceedings\n"
                f"- Directly relevant to the theoretical domain of {topic}\n"
                f"- Written in English\n\n"
                f"## Exclusion Criteria\n"
                f"- Non-peer-reviewed sources (blogs, news articles, opinion pieces)\n"
                f"- Studies outside the date range\n"
                f"- Retracted publications\n"
                f"- Purely descriptive or atheoretical papers\n\n"
                f"## Search Strategy\n"
                f"- Databases: Semantic Scholar, OpenAlex, CrossRef, arXiv, PubMed, CORE, DBLP, Europe PMC, BASE\n"
                f"- AI-automated relevance screening (embedding similarity threshold: {c.triage_select_threshold})\n"
                f"- Reference snowballing of top-cited papers\n\n"
                f"## Analysis Approach\n"
                f"- Thematic analysis of theoretical arguments\n"
                f"- Construct identification and definition\n"
                f"- Evidence quality assessment (GRADE framework)\n"
                f"- Framework synthesis and proposition development\n"
            )
        else:
            protocol = (
                f"# Research Protocol — Systematic Literature Review\n\n"
                f"## Title\n{topic}\n\n"
                f"## Paper Type\nSystematic Literature Review\n\n"
                f"## Research Questions\n"
                f"1. What is the current state of evidence on {topic}?\n"
                f"2. What methodological approaches have been used?\n"
                f"3. What gaps exist in the current literature?\n\n"
                f"## PICO Framework\n"
                f"- **Population**: To be defined based on topic scope\n"
                f"- **Intervention/Exposure**: {topic}\n"
                f"- **Comparison**: Alternative approaches or absence of intervention\n"
                f"- **Outcome**: Key dependent variables identified during search\n\n"
                f"## Inclusion Criteria\n"
                f"- Published between {c.search_start_year} and {current_year}\n"
                f"- Peer-reviewed empirical studies\n"
                f"- Directly relevant to {topic}\n"
                f"- Written in English\n\n"
                f"## Exclusion Criteria\n"
                f"- Non-peer-reviewed sources\n- Studies outside the date range\n"
                f"- Retracted publications\n- Conference abstracts without full text\n\n"
                f"## Search Strategy\n"
                f"- Databases: Semantic Scholar, OpenAlex, CrossRef, arXiv, PubMed, CORE, DBLP, Europe PMC, BASE\n"
                f"- AI-automated relevance screening (embedding similarity threshold: {c.triage_select_threshold})\n"
                f"- Reference snowballing of top {c.snowball_top_papers} cited papers\n\n"
                f"## Quality Assessment\n"
                f"- Risk of bias assessment (JBI Critical Appraisal tools)\n"
                f"- GRADE evidence certainty ratings\n"
                f"- AI-assisted screening — single automated reviewer with programmatic validation\n\n"
                f"## Data Extraction\n"
                f"- Structured claim extraction with supporting quotes\n"
                f"- Quantitative data: sample size, effect size, p-value, CI\n"
                f"- Study characteristics: design, population, country, year range\n\n"
                f"## Synthesis Approach\n"
                f"- Thematic narrative synthesis\n- Evidence quality matrix\n"
                f"- PRISMA flow diagram\n"
            )

        # Save protocol
        ws = c.workspace
        pipeline_dir = ws / c.session_root_dir / "output" / "pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        protocol_file = pipeline_dir / "protocol.md"
        protocol_file.write_text(protocol, encoding="utf-8")
        _log.info("PIPELINE: Protocol saved (%d chars)", len(protocol))
        if on_event:
            on_event(StepEvent("subtask_end", data="Protocol generated (template)", depth=0))

    def _pipeline_verify_batch(self, on_event: StepCallback | None) -> None:
        """Batch verify all papers programmatically — no LLM needed."""
        db = self.tools.db
        session_id = self.tools.session_id
        if not db or not session_id:
            return

        _log.info("PIPELINE: Running batch verification (programmatic)")
        if on_event:
            on_event(StepEvent("text", data="Verifying papers (batch — DOI, retraction, citations)...", depth=0))

        # Pre-populate from central DB first
        central_db = self.tools.central_db
        if central_db:
            self._prepopulate_verification_flags(central_db, db, session_id)

        # Get papers that still need verification
        rows = db._conn.execute(
            "SELECT paper_id, doi, retraction_checked, citation_verified FROM papers "
            "WHERE session_id = ? AND doi IS NOT NULL "
            "AND (doi_valid = 0 OR retraction_checked = 0 OR citation_verified = 0)",
            (session_id,),
        ).fetchall()

        if not rows:
            _log.info("PIPELINE VERIFY: All papers already verified")
            if on_event:
                on_event(StepEvent("subtask_end", data="All papers already verified", depth=0))
            return

        _log.info("PIPELINE VERIFY: %d papers need verification", len(rows))
        from .tools.http import rate_limited_get
        import concurrent.futures

        verified = 0
        retracted = 0

        # Build per-paper flags so threads only call APIs that are actually needed
        needs_retraction = {row["doi"] for row in rows if not row["retraction_checked"]}
        needs_citation = {row["doi"] for row in rows if not row["citation_verified"]}

        def _verify_one_http(doi: str) -> dict[str, Any]:
            """HTTP-only verification — no DB access. Returns results dict."""
            result: dict[str, Any] = {"doi": doi, "retracted": False,
                                       "retraction_checked": False, "citation_count": 0, "citation_verified": False}

            # Skip DOI HEAD validation — already set by pre-populate from central DB

            if doi in needs_retraction:
                try:
                    resp = rate_limited_get(
                        f"https://api.crossref.org/works/{doi}",
                        params={"mailto": "ara-research@example.com"}, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json().get("message", {})
                        update_to = data.get("update-to", [])
                        result["retracted"] = any(u.get("type") == "retraction" for u in update_to)
                    result["retraction_checked"] = True
                except Exception:
                    pass

            if doi in needs_citation:
                try:
                    import os
                    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
                    headers = {"x-api-key": api_key} if api_key else {}
                    resp = rate_limited_get(
                        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
                        headers=headers,
                        params={"fields": "citationCount,influentialCitationCount"}, timeout=15)
                    if resp.status_code == 200:
                        result["citation_count"] = resp.json().get("citationCount", 0)
                        result["citation_verified"] = True
                except Exception:
                    pass

            return result

        # Build DOI→paper_id map for batch DB update
        doi_to_pid: dict[str, int] = {}
        for row in rows:
            if row["doi"]:
                doi_to_pid[row["doi"]] = row["paper_id"]
        dois = list(doi_to_pid.keys())

        # Run HTTP calls in parallel — NO DB access in threads
        results: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as pool:
            futures = {pool.submit(_verify_one_http, doi): doi for doi in dois}
            for i, fut in enumerate(concurrent.futures.as_completed(futures)):
                if self.cancel_flag.is_set():
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    results.append(fut.result())
                except Exception:
                    pass
                if (i + 1) % 50 == 0:
                    _log.info("PIPELINE VERIFY: %d/%d papers checked", i + 1, len(dois))

        # Batch update DB from main thread (single-threaded, safe)
        for r in results:
            pid = doi_to_pid.get(r["doi"])
            if not pid:
                continue
            updates = []
            params: list[Any] = []
            if r["retraction_checked"]:
                updates.append("retraction_checked = 1")
                verified += 1
            if r["retracted"]:
                retracted += 1
            if r["citation_verified"]:
                updates.append("citation_verified = 1")
                updates.append("citation_count = ?")
                params.append(r["citation_count"])
            if updates:
                params.append(pid)
                db._conn.execute(f"UPDATE papers SET {', '.join(updates)} WHERE paper_id = ?", params)

            # Also cache in central DB
            if central_db and (r["retraction_checked"] or r["citation_verified"]):
                try:
                    central_db.store_doi_validation(
                        r["doi"], retracted=r["retracted"],
                        citation_count=r["citation_count"],
                    )
                except Exception:
                    pass

        db._conn.commit()
        _log.info("PIPELINE VERIFY: Complete — %d verified, %d retracted", verified, retracted)
        if on_event:
            on_event(StepEvent("subtask_end", data=f"Verified {verified} papers ({retracted} retracted)", depth=0))

    def _pipeline_brancher_programmatic(
        self, topic: str, on_event: StepCallback | None,
    ) -> None:
        """Brancher v2: SCAMPER-driven cross-domain insight discovery using MMR + LLM analogical reasoning."""
        db = self.tools.db
        session_id = self.tools.session_id
        if not db or not session_id:
            return

        _log.info("PIPELINE: Brancher v2 — SCAMPER cross-domain insight discovery")
        if on_event:
            on_event(StepEvent("subtask_start", data="Brancher: SCAMPER cross-domain insight discovery", depth=0))

        central_db = self.tools.central_db
        claims = db.get_claims(session_id)
        claim_summary = "\n".join(
            f"- [{c.get('claim_type', 'finding')}] {c['claim_text'][:150]}"
            for c in claims[:30]
        )

        # ── STEP 1: LLM generates SCAMPER analogies ──
        scamper_prompt = (
            f"You are a cross-domain research strategist. The topic is:\n{topic}\n\n"
            f"Key findings so far:\n{claim_summary}\n\n"
            f"Apply the SCAMPER framework to generate analogical search queries from OTHER industries/fields:\n\n"
            f"S (Substitute): What concept from another field could replace a core assumption here?\n"
            f"C (Combine): What two unrelated fields could merge to explain this phenomenon?\n"
            f"A (Adapt): What framework from another domain (e.g., biology, physics, military) maps onto this?\n"
            f"M (Modify): How would changing scale/context/geography transform the findings?\n"
            f"P (Put to other use): What if these findings were applied in healthcare/education/agriculture?\n"
            f"E (Eliminate): What would happen if a key assumption were removed entirely?\n"
            f"R (Reverse): What if the causal direction were flipped?\n\n"
            f"For EACH SCAMPER letter, generate exactly 2 academic search queries that target a DIFFERENT field "
            f"but could yield transferable insights. The queries must NOT contain the topic's core keywords "
            f"(e.g., don't use 'fintech' or 'financial crisis' — use the analogous domain's language).\n\n"
            f"Format: One query per line, prefixed with the letter. Example:\n"
            f"S: platform ecosystem resilience during regulatory disruption\n"
            f"S: technology stack migration under external shock healthcare systems\n"
            f"C: ...\n"
        )

        scamper_queries: list[tuple[str, str]] = []  # (label, query)
        try:
            response = self.light_model.generate_content(scamper_prompt)
            raw_text = response.text if hasattr(response, "text") else str(response)
            _log.info("BRANCHER: LLM generated SCAMPER queries (%d chars)", len(raw_text))

            for line in raw_text.strip().split("\n"):
                line = line.strip()
                if len(line) < 10:
                    continue
                # Parse "S: query text" or "S - query text"
                for sep in [":", "-", "."]:
                    if sep in line[:3]:
                        letter = line[:line.index(sep)].strip().upper()
                        query = line[line.index(sep) + 1:].strip()
                        if letter in ("S", "C", "A", "M", "P", "E", "R") and len(query) > 15:
                            scamper_queries.append((letter, query))
                        break
        except Exception as exc:
            _log.warning("BRANCHER: SCAMPER LLM call failed: %s — falling back to templates", exc)

        # Fallback if LLM failed or returned too few
        if len(scamper_queries) < 8:
            _fallback = [
                ("S", "platform ecosystem resilience during regulatory disruption"),
                ("S", "technology stack migration under external shock in healthcare"),
                ("C", "supply chain digital transformation during pandemic disruption"),
                ("C", "organizational ambidexterity exploration exploitation crisis response"),
                ("A", "biological immune system adaptive response environmental stress"),
                ("A", "military strategic pivot resource reallocation under threat"),
                ("M", "small island developing states digital infrastructure leapfrogging"),
                ("M", "agricultural technology adoption developing economies subsistence crisis"),
                ("P", "crisis-driven telemedicine adoption patterns rural communities"),
                ("P", "education technology pivot during institutional disruption"),
                ("E", "technology adoption without trust intermediary institutions"),
                ("E", "market competition without intellectual property protection"),
                ("R", "technology portfolio driving crisis resilience rather than crisis driving technology"),
                ("R", "reverse innovation emerging market solutions adopted by developed economies"),
            ]
            # Add only those not already covered
            existing = {q for _, q in scamper_queries}
            for label, query in _fallback:
                if query not in existing and len(scamper_queries) < 14:
                    scamper_queries.append((label, query))

        _log.info("BRANCHER: %d SCAMPER queries ready", len(scamper_queries))

        # ── STEP 2: MMR search central DB for cross-domain evidence ──
        cross_domain_claims: list[dict] = []
        if central_db:
            try:
                from .tools.pipeline import _get_api_key
                api_key = _get_api_key()
                if api_key:
                    from google import genai
                    client = genai.Client(api_key=api_key)

                    seen_ids: set[int] = set()
                    for label, query in scamper_queries[:10]:
                        try:
                            emb_result = client.models.embed_content(
                                model="gemini-embedding-001", contents=query[:500],
                            )
                            if not emb_result.embeddings:
                                continue
                            query_emb = emb_result.embeddings[0].values
                            results = central_db.search_claims_mmr(
                                query_emb, limit=8, min_cosine=0.30, lam=0.5, paper_cap=2,
                            )
                            for r in results:
                                cid = r.get("claim_id")
                                if cid and cid not in seen_ids:
                                    seen_ids.add(cid)
                                    r["scamper_label"] = label
                                    r["scamper_query"] = query
                                    cross_domain_claims.append(r)
                        except Exception as exc:
                            _log.debug("BRANCHER MMR query failed: %s", exc)
                    _log.info("BRANCHER: Found %d cross-domain claims via MMR", len(cross_domain_claims))
            except Exception as exc:
                _log.warning("BRANCHER: MMR search failed: %s", exc)

        # ── STEP 3: API search for novel cross-domain papers ──
        total_new = 0
        from .tools.search import reset_search_all_state
        reset_search_all_state()

        for i, (label, query) in enumerate(scamper_queries[:12]):
            if self.cancel_flag.is_set():
                break
            _log.info("BRANCHER: [%s] Query %d/%d: %s", label, i + 1, min(len(scamper_queries), 12), query[:80])
            result = self.tools.dispatch("search_all", {"query": query, "limit": 10})
            try:
                data = json.loads(result)
                total_new += data.get("total", 0)
            except Exception:
                pass

        # ── STEP 4: LLM synthesizes cross-domain insights ──
        insight_brief = ""
        if cross_domain_claims:
            claims_for_synthesis = "\n".join(
                f"- [{c.get('scamper_label', '?')}] {c['claim_text'][:200]} "
                f"(from: {c.get('paper_title', '')[:60]}, sim={c.get('cosine_similarity', '?')})"
                for c in cross_domain_claims[:25]
            )
            synthesis_prompt = (
                f"You are synthesizing cross-domain insights for a research paper on:\n{topic}\n\n"
                f"The following claims were found by searching OUTSIDE the topic's core field using SCAMPER:\n"
                f"{claims_for_synthesis}\n\n"
                f"For each SCAMPER letter that has results, write a 2-3 sentence insight explaining:\n"
                f"1. What the cross-domain finding is\n"
                f"2. How it maps back to the topic as a transferable framework/mechanism/analogy\n"
                f"3. What novel hypothesis this enables\n\n"
                f"Be specific. Name the source papers. Focus on genuinely novel connections."
            )
            try:
                response = self.light_model.generate_content(synthesis_prompt)
                insight_brief = response.text if hasattr(response, "text") else str(response)
                _log.info("BRANCHER: Synthesized %d chars of cross-domain insights", len(insight_brief))
            except Exception as exc:
                _log.warning("BRANCHER: Synthesis LLM call failed: %s", exc)

        # ── STEP 5: Save brancher output ──
        ws = self.config.workspace
        pipeline_dir = ws / self.config.session_root_dir / "output" / "pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        brancher_summary = f"# Brancher Phase — SCAMPER Cross-Domain Insights\n\n"
        brancher_summary += f"## SCAMPER Queries ({len(scamper_queries)})\n"
        for label, query in scamper_queries:
            brancher_summary += f"- **{label}**: {query}\n"

        brancher_summary += f"\n## Cross-Domain Evidence from Central DB ({len(cross_domain_claims)} claims)\n"
        for c in cross_domain_claims[:20]:
            brancher_summary += (
                f"- [{c.get('scamper_label', '?')}] {c['claim_text'][:200]} "
                f"(paper: {c.get('paper_title', '')[:60]})\n"
            )

        brancher_summary += f"\n## New Papers from API Search\n- {total_new} papers found\n"

        if insight_brief:
            brancher_summary += f"\n## Synthesized Cross-Domain Insights\n{insight_brief}\n"

        (pipeline_dir / "brancher.md").write_text(brancher_summary, encoding="utf-8")

        _log.info("BRANCHER: Complete — %d SCAMPER queries, %d cross-domain claims, %d new papers",
                   len(scamper_queries), len(cross_domain_claims), total_new)
        if on_event:
            on_event(StepEvent("subtask_end",
                     data=f"Brancher complete: {len(cross_domain_claims)} cross-domain insights, {total_new} new papers",
                     depth=0))

    def _pipeline_synthesis_programmatic(
        self, topic: str, paper_type: str, context: ExternalContext,
        on_event: StepCallback | None,
    ) -> None:
        """Programmatic synthesis: build structured tables from DB, then short LLM narrative."""
        db = self.tools.db
        session_id = self.tools.session_id
        if not db or not session_id:
            return

        _log.info("PIPELINE: Programmatic synthesis — building data tables from DB")
        if on_event:
            on_event(StepEvent("subtask_start", data="Synthesis: building structured data tables", depth=0))

        claims = db.get_claims(session_id)
        papers_with_claims = set(c["paper_id"] for c in claims)

        # 1. Study characteristics table
        study_table = "| Paper ID | Authors | Year | Design | Population | Country |\n|---|---|---|---|---|---|\n"
        paper_rows = db._conn.execute(
            "SELECT paper_id, title, authors, year FROM papers "
            "WHERE session_id = ? AND paper_id IN ({}) "
            "ORDER BY citation_count DESC LIMIT 50".format(
                ",".join("?" * len(papers_with_claims))
            ),
            [session_id] + list(papers_with_claims),
        ).fetchall() if papers_with_claims else []

        for r in paper_rows:
            try:
                authors_list = json.loads(r["authors"] or "[]")
                first_author = authors_list[0] if authors_list else "Unknown"
                if isinstance(first_author, dict):
                    first_author = first_author.get("name", first_author.get("family", "Unknown"))
            except Exception:
                first_author = "Unknown"
            # Find study design from claims
            paper_claims = [c for c in claims if c["paper_id"] == r["paper_id"]]
            design = next((c.get("study_design", "") for c in paper_claims if c.get("study_design")), "")
            population = next((c.get("population", "") for c in paper_claims if c.get("population")), "")
            country = next((c.get("country", "") for c in paper_claims if c.get("country")), "")
            study_table += f"| {r['paper_id']} | {first_author} | {r['year']} | {design[:30]} | {population[:30]} | {country[:20]} |\n"

        # 2. Evidence synthesis by claim type
        claims_by_type: dict[str, list] = {}
        for c in claims:
            ct = c.get("claim_type", "finding")
            claims_by_type.setdefault(ct, []).append(c)
        evidence_table = "## Evidence Summary by Type\n\n"
        for ct, ct_claims in sorted(claims_by_type.items()):
            evidence_table += f"### {ct.title()} ({len(ct_claims)} claims)\n"
            for c in ct_claims[:10]:
                evidence_table += f"- {c.get('claim_text', '')[:150]} (paper_id={c.get('paper_id')}, conf={c.get('confidence', '')})\n"
            evidence_table += "\n"

        # 3. Citation map by theme (top keywords → papers)
        keyword_papers: dict[str, set] = {}
        for c in claims:
            text = c.get("claim_text", "").lower()
            for kw in text.split():
                kw = kw.strip(".,;:()[]\"'")
                if len(kw) > 5 and kw.isalpha():
                    keyword_papers.setdefault(kw, set()).add(c.get("paper_id", 0))
        # Top 15 themes by paper coverage
        top_themes = sorted(keyword_papers.items(), key=lambda x: -len(x[1]))[:15]
        citation_map = "## Citation Map by Theme\n\n"
        for theme, pids in top_themes:
            citation_map += f"- **{theme}**: {len(pids)} papers (IDs: {', '.join(str(p) for p in list(pids)[:8])})\n"

        # 4. PRISMA numbers
        total_papers = db.paper_count(session_id)
        selected = db._conn.execute(
            "SELECT COUNT(*) FROM papers WHERE session_id = ? AND selected_for_deep_read = 1",
            (session_id,),
        ).fetchone()[0]
        prisma = (
            f"## PRISMA Flow Numbers\n"
            f"- Records identified: {total_papers}\n"
            f"- Records screened: {total_papers}\n"
            f"- Full-text assessed: {selected}\n"
            f"- Included in synthesis: {len(papers_with_claims)}\n"
        )

        # 5. RoB summary
        rob_table = ""
        try:
            rob_result = self.tools.dispatch("get_risk_of_bias_table", {})
            rob_data = json.loads(rob_result)
            if rob_data.get("table"):
                rob_table = f"## Risk of Bias Summary\n\n{rob_data['table']}\n"
        except Exception:
            pass

        # 6. GRADE summary
        grade_table = ""
        try:
            grade_result = self.tools.dispatch("get_grade_table", {})
            grade_data = json.loads(grade_result)
            if grade_data.get("table"):
                grade_table = f"## GRADE Evidence Ratings\n\n{grade_data['table']}\n"
        except Exception:
            pass

        # Combine all synthesis data
        synthesis_output = (
            f"# Synthesis Data — Structured Tables\n\n"
            f"## Study Characteristics\n\n{study_table}\n\n"
            f"{evidence_table}\n"
            f"{citation_map}\n\n"
            f"{prisma}\n\n"
            f"{rob_table}\n"
            f"{grade_table}\n"
        )

        # Save synthesis data
        ws = self.config.workspace
        pipeline_dir = ws / self.config.session_root_dir / "output" / "pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        (pipeline_dir / "synthesis_data.md").write_text(synthesis_output, encoding="utf-8")
        _log.info("PIPELINE SYNTHESIS: Saved programmatic synthesis_data.md (%d chars)", len(synthesis_output))

        # Short LLM call for narrative synthesis + proposition revision (10 steps)
        _log.info("PIPELINE SYNTHESIS: Running short LLM call for narrative synthesis")
        narrative_objective = (
            f"You are the synthesis agent for a research paper on: {topic}.\n"
            f"The structured data tables have been pre-built. Your job is to:\n"
            f"1. Read the synthesis_data.md file in sections/\n"
            f"2. Write a NARRATIVE synthesis (not tables — those are done) identifying:\n"
            f"   - Key thematic clusters across {len(claims)} claims from {len(papers_with_claims)} papers\n"
            f"   - Theoretical tensions and contradictions\n"
            f"   - Revised proposition set (drop established-knowledge items, merge redundant ones)\n"
            f"   - Evidence quality assessment narrative\n"
            f"3. Save using write_section(section='synthesis', content=YOUR_NARRATIVE)\n"
            f"Keep it under 2000 words. Focus on insights, not data listing."
        )

        system_prompt = build_phase_system_prompt(
            phase="synthesis", topic=topic, rules=context.rules,
            paper_type=paper_type,
        )
        self._solve_recursive(
            objective=narrative_objective,
            context=context,
            depth=self.config.max_depth,
            on_event=on_event,
            system_prompt_override=system_prompt,
            phase="synthesis",
            max_steps=15,  # Short — tables are already built
        )

        _log.info("PIPELINE SYNTHESIS: Complete")
        if on_event:
            on_event(StepEvent("subtask_end", data="Synthesis complete", depth=0))

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
            fetched = data.get("total_fetched", 0)
            total = data.get("papers_without_fulltext", 0)
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

        # Spawn background thread to chunk+embed new fulltexts for future MMR
        self._spawn_bg_chunk_embed()

    def _spawn_bg_chunk_embed(self) -> None:
        """Background thread: chunk newly fetched full texts and embed them into central DB."""
        central_db = self.tools.central_db
        if not central_db:
            return

        import threading

        def _bg_chunk_embed():
            try:
                _log.info("BG_CHUNK_EMBED: Starting background chunk+embed for new fulltexts")

                # Find papers with full text but no chunks
                rows = central_db._conn.execute(
                    "SELECT p.paper_id, p.title, p.full_text FROM papers p "
                    "WHERE p.full_text IS NOT NULL AND p.full_text != '' "
                    "AND p.paper_id NOT IN (SELECT DISTINCT paper_id FROM paper_chunks) "
                    "ORDER BY p.paper_id"
                ).fetchall()

                if not rows:
                    _log.info("BG_CHUNK_EMBED: No new papers to chunk")
                    return

                _log.info("BG_CHUNK_EMBED: %d papers need chunking", len(rows))

                # Phase 1: Chunk
                from ara.tools.fulltext import _chunk_text as chunk_text
                total_chunks = 0
                for row in rows:
                    chunks = chunk_text(row["full_text"])
                    if chunks:
                        stored = central_db.store_chunks(row["paper_id"], chunks)
                        total_chunks += stored

                _log.info("BG_CHUNK_EMBED: Stored %d chunks from %d papers", total_chunks, len(rows))

                if total_chunks == 0:
                    return

                # Phase 2: Embed unembedded chunks
                unembedded = central_db._conn.execute(
                    "SELECT chunk_id, chunk_text FROM paper_chunks WHERE embedding IS NULL ORDER BY chunk_id"
                ).fetchall()

                if not unembedded:
                    _log.info("BG_CHUNK_EMBED: All chunks already embedded")
                    return

                _log.info("BG_CHUNK_EMBED: Embedding %d chunks", len(unembedded))

                # Try Google direct first, fall back to OpenRouter
                from .tools.pipeline import _get_api_key
                api_key = _get_api_key()
                embedded = 0
                # Collect new items + embeddings for hot cache append
                new_cache_items: list[dict] = []
                new_cache_embeddings: list[list[float]] = []

                # Build a lookup: chunk_id → paper metadata for cache items
                chunk_paper_meta: dict[int, dict] = {}
                paper_ids = set()
                for row in unembedded:
                    # Get paper_id from paper_chunks table
                    meta_row = central_db._conn.execute(
                        "SELECT c.chunk_id, c.paper_id, c.chunk_index, c.chunk_text, "
                        "p.title AS paper_title, p.doi AS paper_doi, p.authors, p.year "
                        "FROM paper_chunks c JOIN papers p ON c.paper_id = p.paper_id "
                        "WHERE c.chunk_id = ?", (row["chunk_id"],)
                    ).fetchone()
                    if meta_row:
                        chunk_paper_meta[row["chunk_id"]] = dict(meta_row)

                def _store_and_collect(chunk_id: int, embedding: list[float]) -> None:
                    nonlocal embedded
                    central_db.store_chunk_embedding(chunk_id, embedding)
                    embedded += 1
                    # Collect for hot cache append
                    meta = chunk_paper_meta.get(chunk_id)
                    if meta:
                        new_cache_items.append(meta)
                        new_cache_embeddings.append(embedding)

                if api_key:
                    from google import genai
                    client = genai.Client(api_key=api_key)
                    for j in range(0, len(unembedded), 50):
                        batch = unembedded[j:j + 50]
                        texts = [r["chunk_text"][:500] for r in batch]
                        try:
                            result = client.models.embed_content(model="gemini-embedding-001", contents=texts)
                            if result.embeddings:
                                for row, emb_obj in zip(batch, result.embeddings):
                                    _store_and_collect(row["chunk_id"], emb_obj.values)
                        except Exception as exc:
                            _log.warning("BG_CHUNK_EMBED: Embed error: %s", exc)
                            if "429" in str(exc) or "rate" in str(exc).lower():
                                import time; time.sleep(30)
                            else:
                                import time; time.sleep(2)
                else:
                    import os
                    or_key = os.getenv("OPENROUTER_API_KEY", "")
                    if not or_key:
                        _log.warning("BG_CHUNK_EMBED: No API key for embeddings — skipping")
                        return
                    from openai import OpenAI
                    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=or_key)
                    for j in range(0, len(unembedded), 50):
                        batch = unembedded[j:j + 50]
                        texts = [r["chunk_text"][:500] for r in batch]
                        try:
                            result = client.embeddings.create(model="google/gemini-embedding-001", input=texts)
                            for row, emb in zip(batch, result.data):
                                _store_and_collect(row["chunk_id"], emb.embedding)
                        except Exception as exc:
                            _log.warning("BG_CHUNK_EMBED: Embed error: %s", exc)
                            if "429" in str(exc) or "rate" in str(exc).lower():
                                import time; time.sleep(10)
                            else:
                                import time; time.sleep(2)

                # Hot-append into live cache (no cold reload needed)
                if new_cache_items:
                    central_db.extend_embedding_cache("chunks", new_cache_items, new_cache_embeddings)
                _log.info("BG_CHUNK_EMBED: Complete — embedded %d/%d chunks, hot-appended to cache",
                          embedded, len(unembedded))

            except Exception as exc:
                _log.error("BG_CHUNK_EMBED: Failed: %s", exc, exc_info=True)

        t = threading.Thread(target=_bg_chunk_embed, daemon=True, name="bg-chunk-embed")
        t.start()
        _log.info("BG_CHUNK_EMBED: Background thread spawned")

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

        import queue as _q
        _EMBED_TIMEOUT = 30  # seconds per embedding call

        for idx, (pid, text) in enumerate(papers_needing_embed):
            try:
                # Timeout-wrapped embed_content to prevent CLOSE_WAIT spin
                result_q: _q.Queue = _q.Queue()
                def _do_embed(t=text):
                    try:
                        r = self._embed_client.models.embed_content(model="gemini-embedding-001", contents=t)
                        result_q.put(r)
                    except Exception as e:
                        result_q.put(e)
                worker = _th.Thread(target=_do_embed)
                worker.start()
                worker.join(timeout=_EMBED_TIMEOUT)
                if worker.is_alive():
                    _log.warning("TRIAGE EMBED: timeout on paper %d (%d/%d) — skipping", pid, idx+1, len(papers_needing_embed))
                    borderline += 1
                    continue
                res = result_q.get_nowait()
                if isinstance(res, Exception):
                    raise res

                if res.embeddings and len(res.embeddings) > 0:
                    emb = res.embeddings[0].values
                    try:
                        db.store_embedding(pid, emb)
                    except Exception:
                        pass
                    score_result = self._score_and_store(db, pid, self._cosine_sim(self._topic_emb, emb))
                    if score_result == "selected":
                        selected += 1
                    elif score_result == "rejected":
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
            self._preload_claims_from_central(central_db, db, session_id, topic=topic)

        # ── Step 1b: Inject top-tier papers from central DB ──
        # Ensures the session has AAA/AA papers for citation quality gates
        if central_db:
            self._inject_top_tier_papers(central_db, db, session_id, topic, on_event)

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

        # Run fetch + embed + embedding generation — skip if already done (checkpoints)
        if "fetch_texts" not in completed:
            self._pipeline_fetch_texts(on_event)
        else:
            _log.info("PIPELINE TRIAGE: Skipping fetch_texts (checkpoint found)")

        if "embed" not in completed:
            self._pipeline_embed(on_event)
            if papers_needing_embed:
                try:
                    self._embedding_triage_generate(papers_needing_embed, db)
                except Exception as exc:
                    _log.warning("Embedding generation failed: %s", exc)
        else:
            _log.info("PIPELINE TRIAGE: Skipping embed (checkpoint found)")

        # Deep_read runs synchronously — it gates the pipeline
        if "deep_read" not in completed and deep_read_def and deep_read_def.get("objective"):
            self._pipeline_run_phase(deep_read_def, topic, paper_type, context, on_event)
        elif "deep_read" in completed:
            _log.info("PIPELINE TRIAGE: Skipping deep_read (checkpoint found)")

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
        """Advisory board: single source of truth. Produces JSON paper plan that writer executes."""
        _log.info("=" * 40)
        _log.info("PIPELINE: Advisory Board — producing JSON paper plan")
        _log.info("=" * 40)
        if on_event:
            on_event(StepEvent("subtask_start", data="Advisory Board: producing paper plan", depth=0))

        ws = self.config.workspace
        pipeline_dir = ws / self.config.session_root_dir / "output" / "pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        # Detect target journal
        from .peer_review import _detect_journal
        journal = self.config.peer_review_journal
        if journal == "auto":
            journal = _detect_journal(topic)
        _log.info("ADVISORY BOARD: Target journal: %s", journal)

        # ── PRE-GATHER ALL UPSTREAM DATA (no truncation) ──

        # 1. Papers with tiers (citation menu)
        db = self.tools.db
        session_id = self.tools.session_id
        papers_data = ""
        if db and session_id:
            try:
                rows = db._conn.execute(
                    "SELECT paper_id, title, authors, year, journal_name, journal_tier, citation_count "
                    "FROM papers WHERE session_id = ? AND selected_for_deep_read = 1 "
                    "ORDER BY CASE WHEN journal_tier = 'AAA' THEN 0 "
                    "WHEN journal_tier = 'AA' THEN 1 ELSE 2 END, citation_count DESC",
                    (session_id,),
                ).fetchall()
                if rows:
                    paper_lines = []
                    aaa_count = aa_count = 0
                    for r in rows:
                        authors_raw = r["authors"] or "[]"
                        try:
                            al = json.loads(authors_raw)
                            fa = al[0] if al else "Unknown"
                            if isinstance(fa, dict):
                                fa = fa.get("name", fa.get("family", "Unknown"))
                        except Exception:
                            fa = "Unknown"
                        tier = r["journal_tier"] or ""
                        if tier == "AAA":
                            aaa_count += 1
                        elif tier == "AA":
                            aa_count += 1
                        tier_tag = f" [{tier}]" if tier in ("AAA", "AA") else ""
                        j_name = r["journal_name"] or ""
                        paper_lines.append(
                            f"  paper_id={r['paper_id']}{tier_tag} | {fa} ({r['year']}) | "
                            f"{r['title'][:90]} | {j_name} | cites={r['citation_count'] or 0}"
                        )
                    papers_data = (
                        f"PAPERS ({len(rows)} deeply-read, {aaa_count} AAA + {aa_count} AA):\n"
                        + "\n".join(paper_lines)
                    )
                    _log.info("ADVISORY BOARD: Pre-gathered %d papers (%d AAA, %d AA)", len(rows), aaa_count, aa_count)
            except Exception as exc:
                _log.warning("ADVISORY BOARD: Failed to pre-gather papers: %s", exc)

        # 2. Claims (all, not truncated)
        claims_data = ""
        if db and session_id:
            try:
                claims = db.get_claims(session_id)
                if claims:
                    claim_lines = []
                    for c in claims:
                        meta = ""
                        if c.get("effect_size"):
                            meta += f" effect={c['effect_size']}"
                        if c.get("p_value"):
                            meta += f" p={c['p_value']}"
                        if c.get("sample_size"):
                            meta += f" N={c['sample_size']}"
                        if c.get("study_design"):
                            meta += f" design={c['study_design'][:20]}"
                        claim_lines.append(
                            f"  claim_id={c.get('claim_id')} paper_id={c.get('paper_id')} "
                            f"[{c.get('claim_type', 'finding')}] {c.get('claim_text', '')[:200]} "
                            f"(conf={c.get('confidence', '')}{meta})"
                        )
                    claims_data = f"CLAIMS ({len(claims)} total):\n" + "\n".join(claim_lines)
                    _log.info("ADVISORY BOARD: Pre-gathered %d claims", len(claims))
            except Exception as exc:
                _log.warning("ADVISORY BOARD: Failed to pre-gather claims: %s", exc)

        # 3. RoB and GRADE tables
        rob_data = ""
        grade_data = ""
        if db and session_id:
            try:
                rob_rows = db.get_risk_of_bias(session_id)
                if rob_rows:
                    rob_data = f"RISK OF BIAS ({len(rob_rows)} assessments):\n"
                    for r in rob_rows:
                        rob_data += (
                            f"  paper_id={r.get('paper_id')} overall={r.get('overall_risk')} "
                            f"selection={r.get('selection_bias')} performance={r.get('performance_bias')} "
                            f"detection={r.get('detection_bias')} attrition={r.get('attrition_bias')} "
                            f"reporting={r.get('reporting_bias')}\n"
                        )
            except Exception:
                pass
            try:
                grade_rows = db.get_grade_evidence(session_id)
                if grade_rows:
                    grade_data = f"GRADE EVIDENCE ({len(grade_rows)} outcomes):\n"
                    for g in grade_rows:
                        grade_data += (
                            f"  outcome={g.get('outcome')} certainty={g.get('certainty')} "
                            f"n_studies={g.get('n_studies')} direction={g.get('direction')} "
                            f"rob={g.get('risk_of_bias_rating')} inconsistency={g.get('inconsistency')}\n"
                        )
            except Exception:
                pass

        # 4. Upstream phase outputs (FULL, not truncated)
        upstream_outputs = ""
        for phase_file in ("synthesis_data.md", "synthesis.md", "hypothesis.md", "critic.md", "brancher.md"):
            pf = pipeline_dir / phase_file
            if pf.exists():
                content = pf.read_text(encoding="utf-8")
                upstream_outputs += f"\n{'='*40}\n## {phase_file.replace('.md', '').replace('_', ' ').title()}\n{'='*40}\n"
                upstream_outputs += content + "\n"

        # ── BUILD QUALITY GATE RULES (everything the writer will be checked against) ──
        cfg = self.config
        _is_conceptual = paper_type == "conceptual"

        if _is_conceptual:
            _section_specs = {
                "abstract": {"words_min": cfg.words_abstract, "words_max": 280, "cites_min": 0, "tables": "NONE", "notes": "Structured: Purpose/Design/Findings/Originality. NO citations. State gap ONCE in Purpose."},
                "introduction": {"words_min": cfg.words_introduction, "words_max": 960, "cites_min": cfg.cites_introduction, "tables": "NONE", "notes": "ONLY place to fully state gap + contribution. Opening hook, theoretical puzzle, gap, framework preview, contribution."},
                "methodology": {"words_min": 400, "words_max": 560, "cites_min": 0, "tables": "NONE", "notes": "Literature selection approach: databases, search terms, date range, screening, analytical approach. Concise."},
                "theoretical_background": {"words_min": cfg.words_theoretical_background, "words_max": 2000, "cites_min": cfg.cites_literature_review, "tables": "ALLOWED (definition tables, comparison tables, boundary conditions)", "notes": "2-3 theoretical streams. Define ALL constructs HERE with precise names. End with Theoretical Gap subsection."},
                "framework": {"words_min": cfg.words_framework, "words_max": 1600, "cites_min": 15, "tables": "NONE", "notes": "Conceptual model in PROSE only. Max 2-3 constructs. Use EXACT names from theoretical_background. NO definition tables, NO comparison tables, NO boundary conditions, NO propositions, NO ASCII diagrams."},
                "propositions": {"words_min": cfg.words_propositions, "words_max": 1440, "cites_min": 9, "tables": "ONE summary table only", "notes": f"3-{cfg.max_propositions} MAX. Formal testable propositions. No construct redefinition. Drop any restating established findings. Summary table must match text exactly."},
                "discussion": {"words_min": cfg.words_discussion, "words_max": 1200, "cites_min": cfg.cites_discussion, "tables": "NONE", "notes": "NO tables, NO boundary conditions, NO construct definitions, NO framework re-description, NO gap restatement, NO new propositions. Prose only: how framework resolves tensions, managerial implications, limitations (2-3 sentences), future research (3-5 empirical studies)."},
                "conclusion": {"words_min": cfg.words_conclusion, "words_max": 400, "cites_min": 0, "tables": "NONE", "notes": "NO summary/restatement. Single insight + one implication + forward-looking close."},
            }
        else:
            _section_specs = {
                "abstract": {"words_min": cfg.words_abstract, "words_max": 280, "cites_min": 0, "tables": "NONE", "notes": "Structured: Background/Objective/Methods/Results/Conclusion. NO citations."},
                "introduction": {"words_min": cfg.words_introduction, "words_max": 960, "cites_min": cfg.cites_introduction, "tables": "NONE", "notes": "Background, gap, research questions."},
                "literature_review": {"words_min": cfg.words_literature_review, "words_max": 2000, "cites_min": cfg.cites_literature_review, "tables": "ALLOWED", "notes": "Thematic organization, cross-reference findings."},
                "methods": {"words_min": cfg.words_methods, "words_max": 1200, "cites_min": cfg.cites_methods, "tables": "NONE", "notes": "Search strategy, inclusion/exclusion, quality assessment."},
                "results": {"words_min": cfg.words_results, "words_max": 1600, "cites_min": cfg.cites_results, "tables": "ALLOWED", "notes": "Study characteristics, thematic results."},
                "discussion": {"words_min": cfg.words_discussion, "words_max": 1200, "cites_min": cfg.cites_discussion, "tables": "NONE", "notes": "Key findings, comparison, limitations, future directions."},
                "conclusion": {"words_min": cfg.words_conclusion, "words_max": 400, "cites_min": cfg.cites_conclusion, "tables": "NONE", "notes": "Main contributions, takeaways."},
            }

        quality_gates = (
            "QUALITY GATES — the writer output will be checked against ALL of these. "
            "Your plan MUST ensure every section passes on first try:\n\n"
            "WORD COUNTS per section:\n"
            + "\n".join(f"  {s}: {spec['words_min']}-{spec['words_max']} words, "
                        f"min {spec['cites_min']} citations, tables={spec['tables']}"
                        for s, spec in _section_specs.items())
            + f"\n\nCITATION QUALITY:\n"
            f"  - At least {cfg.journal_tier_min_pct:.0%} of citations MUST be from AAA/AA journals\n"
            f"  - Ratio: {cfg.journal_tier_ratio:.0f}:1 top-tier per unranked citation\n"
            f"  - Every (Author, Year) must match a real paper in the database\n"
            f"  - No paragraph >300 words without a citation\n"
            f"  - No paper cited 4+ times in one section\n"
            f"\nSTRUCTURAL RULES:\n"
            f"  - Gap statement: ONLY in introduction (all other sections reference it, never restate)\n"
            f"  - Construct names: defined ONCE in theoretical_background, used identically everywhere\n"
            f"  - Section overlap: unique-word overlap must stay below {cfg.max_section_overlap:.0%} between any two sections\n"
            f"  - Propositions: max {cfg.max_propositions}, ONLY in propositions section\n"
            f"  - Tables: only in theoretical_background + propositions (ONE summary table)\n"
            f"  - No orphaned Table/Figure references (don't reference Table N unless it exists)\n"
            f"\nWRITING STYLE:\n"
            f"  - Average sentence length: max {cfg.max_avg_sentence_length} words\n"
            f"  - NO hedging: 'it is imperative', 'plays a crucial role', 'it should be noted'\n"
            f"  - NO hollow openers: 'This section aims to discuss...'\n"
            f"  - NO shopping-list: 'Author (Year) found... Author (Year) showed...' — synthesize thematically\n"
            f"  - NO bullet lists — continuous prose only\n"
            f"  - NO em-dashes or en-dashes — use commas, semicolons, colons, parentheses\n"
            f"  - NO banned paragraph openers: 'Furthermore,', 'Additionally,', 'Moreover,'\n"
            f"  - NO single-sentence paragraphs — minimum 4 sentences per paragraph\n"
            f"  - Methodology: do NOT claim human dual-reviewer screening (this is AI-assisted)\n"
            f"  - NO self-referential excess: 'this paper/study' max 5 times per section\n"
            f"  - Abstract must NOT contain citations\n"
        )

        # ── JSON SCHEMA the advisory board must produce ──
        section_names = list(_section_specs.keys())
        json_schema = (
            "OUTPUT FORMAT — you MUST produce a valid JSON object. No markdown, no explanation, ONLY JSON.\n"
            "Save it using: write_section(section='paper_plan', content=YOUR_JSON_STRING)\n\n"
            "JSON SCHEMA:\n"
            "{\n"
            '  "target_journal": "string — journal name",\n'
            '  "narrative_arc": "string — the overarching story in 3-4 sentences: problem → gap → contribution → implications",\n'
            '  "constructs": [\n'
            '    {"name": "ExactConstructName", "definition": "1 sentence definition", "defined_in": "theoretical_background"}\n'
            '  ],\n'
            '  "propositions": [\n'
            '    {\n'
            '      "id": "P1",\n'
            '      "statement": "exact formal proposition statement",\n'
            '      "constructs_used": ["construct name 1"],\n'
            '      "key_citations": [{"paper_id": 123, "author_year": "Author (Year)"}]\n'
            '    }\n'
            '  ],\n'
            '  "sections": {\n'
        )
        for sname, spec in _section_specs.items():
            json_schema += (
                f'    "{sname}": {{\n'
                f'      "thesis": "string — the ONE central argument of this section (unique, no overlap with other sections)",\n'
                f'      "key_points": ["point 1 — specific argument", "point 2"],\n'
                f'      "subsections": ["subsection title 1", "subsection title 2"],\n'
                f'      "citations": [{{"paper_id": 123, "author_year": "Author (Year)", "use_for": "what this citation supports"}}],\n'
                f'      "tables": ["Table N: title" or empty list if tables={spec["tables"]}],\n'
                f'      "must_not": ["what this section must NOT contain"],\n'
                f'      "transition_to_next": "how to connect to next section"\n'
                f'    }},\n'
            )
        json_schema += (
            '  },\n'
            '  "proactive_defense": ["anticipated reviewer objection 1 and how to address it"],\n'
            '  "evidence_hierarchy": "string — strongest to weakest evidence, how to handle conflicts"\n'
            '}\n\n'
            f"HARD REQUIREMENTS FOR THE PLAN:\n"
            f"- Every section's citations list must use ONLY paper_ids from the PAPERS list above\n"
            f"- Total unique papers cited across all sections must be >= {min(context.papers_with_claims, 30)}\n"
            f"- Every deeply-read paper with claims should appear in at least one section\n"
            f"- Propositions: {cfg.max_propositions} maximum, use EXACT construct names\n"
            f"- Each section's thesis must be UNIQUE — zero content overlap between sections\n"
            f"- Citations per section must meet minimums AND {cfg.journal_tier_min_pct:.0%} must be [AAA]/[AA]\n"
        )

        # ── COMPOSE THE ADVISORY BOARD OBJECTIVE ──
        advisory_objective = (
            f"You are the Senior Advisory Board for a research paper on:\n{topic}\n"
            f"Paper type: {paper_type}\n"
            f"TARGET JOURNAL: {journal}\n\n"
            f"You combine TWO roles:\n"
            f"(A) DOMAIN EXPERT — theoretical framing, contribution positioning, narrative arc\n"
            f"(B) JOURNAL REVIEWER for {journal} — scope fit, what gets published vs rejected\n\n"
            f"ALL DATA HAS BEEN PRE-GATHERED FOR YOU. Do NOT call list_papers, list_claims, "
            f"or any search tools. All evidence is below. Your ONLY job is to analyze this data "
            f"and produce the JSON paper plan.\n\n"
            f"{'='*40}\n{papers_data}\n\n"
            f"{'='*40}\n{claims_data}\n\n"
            f"{'='*40}\n{rob_data}\n\n"
            f"{'='*40}\n{grade_data}\n\n"
            f"{'='*40}\nUPSTREAM PHASE OUTPUTS:\n{upstream_outputs}\n\n"
            f"{'='*40}\n{quality_gates}\n\n"
            f"{'='*40}\n{json_schema}\n\n"
            f"PRODUCE THE JSON PAPER PLAN NOW. Save it using write_section(section='paper_plan', content=YOUR_JSON).\n"
            f"The JSON must be valid and parseable. No markdown wrapping, no code fences in the content string."
        )

        system_prompt = build_phase_system_prompt(
            phase="advisory_board", topic=topic, rules=context.rules,
            paper_type=paper_type,
        )

        # Advisory board gets minimal tools — just write_section to save the plan
        self._solve_recursive(
            objective=advisory_objective,
            context=context,
            depth=self.config.max_depth,
            on_event=on_event,
            system_prompt_override=system_prompt,
            phase="advisory_board",
            max_steps=30,
        )

        # ── VALIDATE AND PARSE THE JSON PLAN ──
        plan_file = pipeline_dir / "paper_plan.md"
        plan_valid = False
        if plan_file.exists():
            raw_plan = plan_file.read_text(encoding="utf-8")
            _log.info("ADVISORY BOARD: Paper plan saved — %d chars", len(raw_plan))

            # Try to parse JSON (strip markdown code fences if present)
            import re as _plan_re
            json_text = raw_plan.strip()
            # Remove ```json ... ``` wrapping if present
            fence_match = _plan_re.search(r'```(?:json)?\s*\n?(.*?)\n?```', json_text, _plan_re.DOTALL)
            if fence_match:
                json_text = fence_match.group(1).strip()

            try:
                plan = json.loads(json_text)
                plan_valid = True
                _log.info("ADVISORY BOARD: JSON plan parsed successfully")

                # Validate required fields
                missing = []
                if "sections" not in plan:
                    missing.append("sections")
                if "constructs" not in plan:
                    missing.append("constructs")
                if "propositions" not in plan:
                    missing.append("propositions")
                if missing:
                    _log.warning("ADVISORY BOARD: Plan missing fields: %s", missing)

                # Validate section coverage
                plan_sections = set(plan.get("sections", {}).keys())
                expected_sections = set(_section_specs.keys())
                missing_sections = expected_sections - plan_sections
                if missing_sections:
                    _log.warning("ADVISORY BOARD: Plan missing sections: %s", missing_sections)

                # Validate citation distribution
                total_cited = set()
                for sec_plan in plan.get("sections", {}).values():
                    for cite in sec_plan.get("citations", []):
                        pid = cite.get("paper_id")
                        if pid:
                            total_cited.add(pid)
                _log.info("ADVISORY BOARD: Plan distributes %d unique papers across sections", len(total_cited))

                # Save clean JSON (for writer consumption)
                plan_json_file = pipeline_dir / "paper_plan.json"
                plan_json_file.write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")
                _log.info("ADVISORY BOARD: Clean JSON plan saved to paper_plan.json")

            except json.JSONDecodeError as exc:
                _log.warning("ADVISORY BOARD: Failed to parse JSON plan: %s", exc)
                _log.warning("ADVISORY BOARD: Raw plan preview: %s", json_text[:500])

        if not plan_valid:
            _log.warning("ADVISORY BOARD: No valid JSON plan — falling back to legacy writing brief mode")
            # Fall back: save upstream context as a basic brief so writer has something
            fallback_brief = (
                f"# Fallback Writing Brief (advisory board JSON plan failed)\n\n"
                f"Target journal: {journal}\n\n"
                f"## Upstream Context\n{upstream_outputs[:8000]}\n"
            )
            (pipeline_dir / "writing_brief.md").write_text(fallback_brief, encoding="utf-8")

        if on_event:
            on_event(StepEvent("subtask_end", data="Advisory Board complete — paper plan produced", depth=0))

    def _pipeline_writer(
        self, topic: str, paper_type: str,
        context: ExternalContext, on_event: StepCallback | None,
    ) -> None:
        """Writer phase — executes the advisory board's paper plan. No thinking, just writing."""
        _log.info("PIPELINE: Starting writer phase")

        ws = self.config.workspace
        sections_dir = ws / self.config.session_root_dir / "output" / "sections"

        # Check which sections already exist
        existing_sections = set()
        if sections_dir.exists():
            for f in sections_dir.iterdir():
                if f.suffix == ".md" and f.stat().st_size > 100:
                    existing_sections.add(f.stem)

        # ── LOAD THE PAPER PLAN (single source of truth from advisory board) ──
        plan: dict = {}
        pipeline_dir = ws / self.config.session_root_dir / "output" / "pipeline"
        plan_file = pipeline_dir / "paper_plan.json"
        if plan_file.exists():
            try:
                plan = json.loads(plan_file.read_text(encoding="utf-8"))
                _log.info("PIPELINE WRITER: Loaded paper plan from advisory board (%d sections)",
                          len(plan.get("sections", {})))
            except json.JSONDecodeError as exc:
                _log.warning("PIPELINE WRITER: Failed to parse paper_plan.json: %s", exc)

        # Fallback: if no JSON plan, try legacy writing brief
        legacy_brief = ""
        if not plan:
            brief_file = pipeline_dir / "writing_brief.md"
            if brief_file.exists():
                legacy_brief = brief_file.read_text(encoding="utf-8")
                _log.warning("PIPELINE WRITER: No JSON plan — falling back to legacy brief (%d chars)", len(legacy_brief))

        # ── BUILD CITATION MENU (writer needs exact author names for formatting) ──
        cached_papers_summary = ""
        db = self.tools.db
        session_id = self.tools.session_id
        if db and session_id:
            try:
                rows = db._conn.execute(
                    "SELECT paper_id, title, authors, year, journal_name, journal_tier FROM papers "
                    "WHERE session_id = ? AND selected_for_deep_read = 1 "
                    "ORDER BY CASE WHEN journal_tier = 'AAA' THEN 0 "
                    "WHEN journal_tier = 'AA' THEN 1 ELSE 2 END, "
                    "citation_count DESC LIMIT 60",
                    (session_id,),
                ).fetchall()
                if rows:
                    paper_lines = []
                    aaa_count = aa_count = 0
                    for idx, r in enumerate(rows, 1):
                        authors_raw = r["authors"] or "[]"
                        try:
                            al = json.loads(authors_raw)
                            fa = al[0] if al else "Unknown"
                            if isinstance(fa, dict):
                                fa = fa.get("name", fa.get("family", "Unknown"))
                        except Exception:
                            fa = "Unknown"
                        tier_tag = ""
                        j_tier = r["journal_tier"]
                        if j_tier == "AAA":
                            tier_tag = " [AAA]"
                            aaa_count += 1
                        elif j_tier == "AA":
                            tier_tag = " [AA]"
                            aa_count += 1
                        j_name = r["journal_name"] or ""
                        journal_info = f" ({j_name})" if j_name else ""
                        paper_lines.append(
                            f"[{idx}]{tier_tag} ({fa}, {r['year']}) — "
                            f"{r['title'][:90]}{journal_info} [paper_id={r['paper_id']}]"
                        )
                    cached_papers_summary = (
                        f"CITATION MENU ({len(rows)} verified papers):\n"
                        f"  TOP-TIER: {aaa_count} AAA + {aa_count} AA journals\n\n"
                        + "\n".join(paper_lines)
                        + "\n\nHARD RULE: Every (Author, Year) you write MUST match an entry above."
                    )
                    _log.info("PIPELINE WRITER: Built citation menu with %d papers", len(rows))
            except Exception as exc:
                _log.warning("PIPELINE WRITER: Failed to build citation menu: %s", exc)

        # ── PRE-WARM MMR CACHES ──
        central_db = self.tools.central_db
        _topic_emb = getattr(self, "_topic_emb", None)
        if central_db and _topic_emb:
            import threading as _thr
            def _prewarm():
                try:
                    central_db._load_embeddings_cached("claims")
                    central_db._load_embeddings_cached("chunks")
                except Exception:
                    pass
            _thr.Thread(target=_prewarm, daemon=True).start()
            _log.info("PIPELINE WRITER: Pre-warming embedding caches")

        # Pre-compute contradictions for discussion section
        _contradictions = []
        if central_db and _topic_emb:
            try:
                _contradictions = central_db.detect_contradictions(similarity_threshold=0.80)
            except Exception:
                pass

        # ── STATIC STYLE GUIDE (applies to ALL sections) ──
        cfg = self.config
        style_guide = (
            "\nWRITING STYLE RULES (apply to ALL sections):\n"
            f"- Average sentence length: max {cfg.max_avg_sentence_length} words. Break long sentences.\n"
            "- NO hedging: remove 'it is imperative', 'plays a crucial role', 'it should be noted'\n"
            "- NO hollow openers: never write 'This section aims to discuss...'\n"
            "- NO shopping-list: never write 'Author (Year) found... Author (Year) showed...'. Synthesize thematically.\n"
            "- NO bullet lists. Use continuous prose paragraphs.\n"
            "- NO em-dashes or en-dashes. Use commas, semicolons, colons, parentheses.\n"
            "- NO 'Furthermore,', 'Additionally,', 'Moreover,' as paragraph openers.\n"
            "- Paragraphs must have 4+ sentences. No single-sentence paragraphs.\n"
            "- Max 5 uses of 'this paper/study/review' per section.\n"
            "- Do NOT cite the same paper 4+ times in one section.\n"
            "- No paragraph >300 words without a citation.\n"
            "- AI-assisted review: do NOT claim human dual-reviewer screening.\n"
        )

        system_prompt = build_phase_system_prompt(
            phase="writer", topic=topic, rules=context.rules,
            paper_type=paper_type,
        )

        # ── DETERMINE SECTION ORDER ──
        if plan and "sections" in plan:
            # Use the plan's section order
            section_names = list(plan["sections"].keys())
        else:
            # Fallback to default section order
            section_names = [name for name, _ in self._writer_sections()]

        _log.info("PIPELINE WRITER: Writing %d sections", len(section_names))

        for section_name in section_names:
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

            # ── BUILD SECTION INSTRUCTION FROM PLAN ──
            section_instruction = ""
            sec_plan = plan.get("sections", {}).get(section_name, {}) if plan else {}

            if sec_plan:
                # Plan-driven instruction — the advisory board's exact blueprint
                thesis = sec_plan.get("thesis", "")
                key_points = sec_plan.get("key_points", [])
                subsections = sec_plan.get("subsections", [])
                citations = sec_plan.get("citations", [])
                tables = sec_plan.get("tables", [])
                must_not = sec_plan.get("must_not", [])
                transition = sec_plan.get("transition_to_next", "")

                # Get word targets from config
                _word_targets = {
                    "abstract": (cfg.words_abstract, 280),
                    "introduction": (cfg.words_introduction, 960),
                    "methodology": (400, 560),
                    "theoretical_background": (cfg.words_theoretical_background, 2000),
                    "framework": (cfg.words_framework, 1600),
                    "propositions": (cfg.words_propositions, 1440),
                    "discussion": (cfg.words_discussion, 1200),
                    "conclusion": (cfg.words_conclusion, 400),
                    "literature_review": (cfg.words_literature_review, 2000),
                    "methods": (cfg.words_methods, 1200),
                    "results": (cfg.words_results, 1600),
                }
                word_min, word_max = _word_targets.get(section_name, (500, 1500))

                section_instruction = (
                    f"WRITE THE '{section_name}' SECTION. Execute EXACTLY as specified below.\n\n"
                    f"THESIS: {thesis}\n\n"
                )
                if key_points:
                    section_instruction += "KEY POINTS (cover ALL of these):\n"
                    for i, p in enumerate(key_points, 1):
                        section_instruction += f"  {i}. {p}\n"
                    section_instruction += "\n"

                if subsections:
                    section_instruction += f"SUBSECTIONS: {', '.join(subsections)}\n\n"

                if citations:
                    section_instruction += "CITATIONS TO USE (from advisory board plan):\n"
                    for c in citations:
                        use_for = c.get("use_for", "")
                        section_instruction += f"  - {c.get('author_year', '?')} [paper_id={c.get('paper_id')}] — {use_for}\n"
                    section_instruction += "\n"

                if tables:
                    section_instruction += f"TABLES: {', '.join(tables)}\n\n"
                else:
                    section_instruction += "TABLES: NONE — do not create any tables in this section.\n\n"

                if must_not:
                    section_instruction += "MUST NOT:\n"
                    for mn in must_not:
                        section_instruction += f"  - {mn}\n"
                    section_instruction += "\n"

                if transition:
                    section_instruction += f"TRANSITION: End by connecting to the next section — {transition}\n\n"

                section_instruction += f"WORD COUNT: {word_min}-{word_max} words.\n"

                # Add global plan context (constructs, propositions)
                constructs = plan.get("constructs", [])
                if constructs:
                    section_instruction += "\nCONSTRUCT NAMES (use these EXACT names, never rename):\n"
                    for ct in constructs:
                        section_instruction += f"  - *{ct.get('name', '')}*: {ct.get('definition', '')}\n"

                propositions = plan.get("propositions", [])
                if propositions and section_name not in ("abstract", "methodology"):
                    section_instruction += f"\nPROPOSITIONS (locked by advisory board, do not modify):\n"
                    for p in propositions:
                        section_instruction += f"  - {p.get('id', '?')}: {p.get('statement', '')[:150]}\n"

            elif legacy_brief:
                # Fallback: use legacy brief + default section instruction
                fallback_sections = dict(self._writer_sections())
                section_instruction = fallback_sections.get(section_name, f"Write the {section_name} section.")
                section_instruction += f"\n\nWRITING BRIEF:\n{legacy_brief[:3000]}\n"
            else:
                # No plan, no brief — use default section instructions
                fallback_sections = dict(self._writer_sections())
                section_instruction = fallback_sections.get(section_name, f"Write the {section_name} section.")

            # ── INJECT ALREADY-WRITTEN SECTION SUMMARIES (anti-overlap) ──
            already_written_summary = ""
            for prev_name in section_names:
                if prev_name == section_name:
                    break
                prev_file = sections_dir / f"{prev_name}.md"
                if prev_file.exists() and prev_file.stat().st_size > 100:
                    prev_content = prev_file.read_text(encoding="utf-8")
                    import re as _aw_re
                    tables = _aw_re.findall(r'\*\*(?:Table \d+[a-z]?:?\s*[^\*]+)\*\*', prev_content)
                    constructs = _aw_re.findall(r'\*([A-Z][^*]{3,50})\*', prev_content)
                    headers = _aw_re.findall(r'^#{2,4}\s+(.+)$', prev_content, _aw_re.MULTILINE)
                    # Also extract first sentence of each paragraph for argument tracking
                    paras = [p.strip() for p in prev_content.split("\n\n") if len(p.strip()) > 50 and not p.strip().startswith("|") and not p.strip().startswith("#")]
                    first_sentences = []
                    for para in paras[:5]:
                        sent_end = min(
                            (para.find(". ") if para.find(". ") > 0 else 9999),
                            (para.find("? ") if para.find("? ") > 0 else 9999),
                        )
                        if sent_end < 9999:
                            first_sentences.append(para[:sent_end + 1].strip()[:100])
                    summary_parts = []
                    if tables:
                        summary_parts.append(f"Tables: {', '.join(t[:60] for t in tables[:5])}")
                    if constructs:
                        summary_parts.append(f"Constructs: {', '.join(set(c[:40] for c in constructs[:6]))}")
                    if headers:
                        summary_parts.append(f"Subsections: {', '.join(h[:40] for h in headers[:6])}")
                    if first_sentences:
                        summary_parts.append(f"Arguments: {'; '.join(first_sentences[:3])}")
                    if summary_parts:
                        already_written_summary += f"\n- {prev_name}: " + " | ".join(summary_parts)

            if already_written_summary:
                already_written_summary = (
                    f"\n\nALREADY WRITTEN (DO NOT REPEAT any of these arguments, tables, or constructs):"
                    f"{already_written_summary}\n"
                )

            # ── MMR EVIDENCE INJECTION (grounding data for the writer) ──
            mmr_injection = ""
            _section_claim_types = {
                "introduction": {"gap", "finding", "theory"},
                "theoretical_background": {"theory", "finding", "method"},
                "literature_review": {"finding", "theory", "gap"},
                "framework": {"theory", "finding", "gap"},
                "propositions": {"theory", "finding", "gap"},
                "methods": {"method"},
                "results": {"finding", "method"},
                "discussion": {"finding", "gap", "limitation", "theory"},
                "conclusion": {"gap", "finding"},
                "abstract": set(),
            }
            relevant_types = _section_claim_types.get(section_name, set())

            if central_db and _topic_emb and section_name not in ("abstract",):
                try:
                    section_query = f"{topic} {section_name} {sec_plan.get('thesis', '')[:200]}" if sec_plan else f"{topic} {section_name}"
                    sec_emb_result = self._embed_client.models.embed_content(
                        model="gemini-embedding-001", contents=section_query[:500],
                    )
                    if sec_emb_result.embeddings:
                        sec_emb = sec_emb_result.embeddings[0].values

                        # MMR claims — diverse, relevant
                        mmr_claims = central_db.search_claims_mmr(
                            sec_emb, limit=25, min_cosine=0.45, lam=0.7, paper_cap=3,
                        )
                        if relevant_types:
                            mmr_claims = [c for c in mmr_claims
                                          if c.get("claim_type", "finding") in relevant_types][:20]
                        if mmr_claims:
                            mmr_injection += f"\nEVIDENCE for {section_name} ({len(mmr_claims)} claims, MMR-diversified):\n"
                            for c in mmr_claims:
                                meta = ""
                                if c.get("effect_size"):
                                    meta += f" effect={c['effect_size']}"
                                if c.get("p_value"):
                                    meta += f" p={c['p_value']}"
                                if c.get("sample_size"):
                                    meta += f" N={c['sample_size']}"
                                mmr_injection += (
                                    f"- [{c.get('claim_type', 'finding')}] {c['claim_text'][:200]} "
                                    f"(paper: {c.get('paper_title', '')[:60]}{meta})\n"
                                )

                        # MMR chunks — grounded full-text passages
                        mmr_chunks = central_db.search_chunks_mmr(
                            sec_emb, limit=6, min_cosine=0.50, lam=0.6, paper_cap=1,
                        )
                        if mmr_chunks:
                            mmr_injection += f"\nGROUNDED EXCERPTS ({len(mmr_chunks)} passages):\n"
                            for ch in mmr_chunks:
                                mmr_injection += (
                                    f"--- [{ch.get('paper_title', '')[:50]}] ---\n"
                                    f"{ch['chunk_text'][:300]}...\n\n"
                                )

                        # Contradictions for discussion
                        if section_name == "discussion" and _contradictions:
                            mmr_injection += f"\nCONTRADICTIONS TO ADDRESS:\n"
                            for ct in _contradictions[:5]:
                                mmr_injection += (
                                    f"- {ct['claim_a']['claim_text'][:100]} vs "
                                    f"{ct['claim_b']['claim_text'][:100]}\n"
                                )
                except Exception as exc:
                    _log.warning("PIPELINE WRITER: MMR retrieval failed for %s: %s", section_name, exc)

            # ── COMPOSE THE OBJECTIVE ──
            objective = (
                f"{section_instruction}\n\n"
                f"{style_guide}\n"
                f"{already_written_summary}\n"
            )
            if mmr_injection:
                objective += f"\n{mmr_injection}\n"
            if cached_papers_summary:
                objective += f"\n{cached_papers_summary}\n"
            objective += (
                f"\nSave using write_section(section='{section_name}', content=YOUR_TEXT). "
                f"Do NOT use markdown headers at the start. "
                f"Every factual statement must cite using (Author, Year) format from the citation menu."
            )
            if self.config.special_instructions:
                objective += f"\n\nAUTHOR DOMAIN EXPERTISE:\n{self.config.special_instructions}\n"

            result = self._solve_recursive(
                objective=objective,
                context=context,
                depth=self.config.max_depth,
                on_event=on_event,
                system_prompt_override=system_prompt,
                phase="writer",
            )
            _log.info("PIPELINE WRITER: Section %s done — %d chars", section_name, len(result))

            # Auto-save if writer didn't call write_section
            section_file = sections_dir / f"{section_name}.md"
            should_auto_save = False
            if len(result) > 500:
                if not section_file.exists():
                    should_auto_save = True
                elif section_file.stat().st_size < 100:
                    should_auto_save = True
                elif section_file.stat().st_size < len(result) * 0.5:
                    should_auto_save = True
            if should_auto_save:
                from .tools.writing import _strip_llm_meta_text
                result = _strip_llm_meta_text(result)
                _log.info("PIPELINE WRITER: Auto-saving %s (%d chars)", section_name, len(result))
                section_file.write_text(result, encoding="utf-8")

            # Inline quality check — safety net (plan should prevent most issues)
            section_issues = self._check_section_quality(section_name, sections_dir, context)
            if section_issues and not self.cancel_flag.is_set():
                _log.warning("PIPELINE WRITER: Section %s failed inline checks: %s", section_name, section_issues)
                if on_event:
                    on_event(StepEvent("text", data=f"Section {section_name} needs rewrite: {section_issues[0]}", depth=0))

                # Rewrite with the plan context + issue fixes
                rewrite_objective = (
                    f"REWRITE the '{section_name}' section. The following quality issues were found:\n"
                    + "\n".join(f"- {issue}" for issue in section_issues) + "\n\n"
                    f"The original plan for this section:\n{section_instruction[:2000]}\n\n"
                    f"Read the current version, fix ALL issues, and save using "
                    f"write_section(section='{section_name}', content=YOUR_TEXT).\n"
                )
                if cached_papers_summary:
                    rewrite_objective += f"\n{cached_papers_summary}\n"

                self._solve_recursive(
                    objective=rewrite_objective,
                    context=context,
                    depth=self.config.max_depth,
                    on_event=on_event,
                    system_prompt_override=system_prompt,
                    phase="writer",
                )
                _log.info("PIPELINE WRITER: Section %s rewrite complete", section_name)

            # Append per-section reference footer
            self._append_section_references(section_name, sections_dir)

            if on_event:
                on_event(StepEvent("subtask_end", data=f"Section {section_name} done", depth=0))

    def _append_section_references(self, section_name: str, sections_dir: Path) -> None:
        """Append a per-section reference footer listing exactly which papers this section cites."""
        from .tools.writing import _extract_citations_from_text, _verify_citation_against_db

        section_file = sections_dir / f"{section_name}.md"
        if not section_file.exists() or section_file.stat().st_size < 100:
            return

        content = section_file.read_text(encoding="utf-8")

        # Strip any existing section reference footer (from prior rewrite)
        _FOOTER_MARKER = "\n\n---\n<!-- SECTION_REFS:"
        if _FOOTER_MARKER in content:
            content = content[:content.index(_FOOTER_MARKER)]

        citations = _extract_citations_from_text(content)
        if not citations:
            _log.info("SECTION REFS: %s — 0 citations found", section_name)
            return

        # Deduplicate
        unique_cites = list(dict.fromkeys(citations))

        # Resolve each citation against DB
        db = self.tools.db
        session_id = self.tools.session_id
        resolved: list[str] = []
        unresolved: list[str] = []

        for author_frag, year in unique_cites:
            if db and session_id:
                match = _verify_citation_against_db(author_frag, year, db, session_id)
                if match.get("verified") and match.get("paper"):
                    p = match["paper"]
                    authors_list = p.get("authors", [])
                    if authors_list:
                        first = authors_list[0]
                        name = first if isinstance(first, str) else first.get("name", "Unknown")
                    else:
                        name = "Unknown"
                    resolved.append(
                        f"- ({author_frag}, {year}) → paper_id={p.get('paper_id')} | "
                        f"{name} et al. — {p.get('title', 'Untitled')[:80]}"
                    )
                else:
                    unresolved.append(f"- ({author_frag}, {year}) — NOT FOUND IN DB")
            else:
                unresolved.append(f"- ({author_frag}, {year}) — no DB available")

        # Build footer (HTML comment so it's invisible in rendered markdown)
        footer = f"{_FOOTER_MARKER} {section_name} -->\n"
        footer += f"<!-- Verified: {len(resolved)} | Unresolved: {len(unresolved)} -->\n"
        for line in resolved:
            footer += f"<!-- {line} -->\n"
        if unresolved:
            footer += "<!-- UNRESOLVED (will be missing from final references): -->\n"
            for line in unresolved:
                footer += f"<!-- {line} -->\n"
        footer += "<!-- /SECTION_REFS -->"

        section_file.write_text(content + footer, encoding="utf-8")
        _log.info("SECTION REFS: %s — %d verified, %d unresolved",
                  section_name, len(resolved), len(unresolved))

    def _validate_writing_brief(self, brief: str) -> list[str]:
        """Programmatic gate on advisory board brief — catches rule violations before writer sees them."""
        import re as _vre
        violations: list[str] = []
        brief_lower = brief.lower()

        # Split brief into per-section blocks for targeted checks
        # Match markdown headers like ## Discussion, ### Framework, etc.
        section_blocks: dict[str, str] = {}
        for m in _vre.finditer(r'(?:^|\n)#{1,4}\s*.*?(abstract|introduction|methodology|theoretical.?background|framework|propositions?|discussion|conclusion).*?\n(.*?)(?=\n#{1,4}\s|\Z)', brief, _vre.IGNORECASE | _vre.DOTALL):
            sec_name = m.group(1).lower().replace(" ", "_").rstrip("s")
            if sec_name == "proposition":
                sec_name = "propositions"
            if sec_name == "theoretical_background" or "theoretical" in sec_name:
                sec_name = "theoretical_background"
            section_blocks[sec_name] = m.group(2)

        # 1. Table placement violations — tables only in theoretical_background + propositions (summary)
        _no_table_sections = {"discussion", "framework", "conclusion", "introduction", "methodology", "abstract"}
        _table_pattern = _vre.compile(r'\btable\b', _vre.IGNORECASE)
        for sec, content in section_blocks.items():
            if sec in _no_table_sections:
                table_mentions = _table_pattern.findall(content)
                # Filter out "summary table" in propositions (allowed) and generic references
                if len(table_mentions) >= 2:  # Multiple mentions = likely instructing to create one
                    violations.append(
                        f"OVERRIDE {sec}: Brief suggests tables — {sec} must NOT contain tables. "
                        f"Tables are only allowed in theoretical_background and propositions (one summary table)."
                    )

        # 2. Framework section should not have boundary conditions
        fw_block = section_blocks.get("framework", "")
        if _vre.search(r'boundar(?:y|ies)\s+condition', fw_block, _vre.IGNORECASE):
            violations.append(
                "OVERRIDE framework: Brief assigns boundary conditions to framework — "
                "boundary conditions belong in theoretical_background ONLY."
            )

        # 3. Discussion should not restate gap or re-describe framework
        disc_block = section_blocks.get("discussion", "")
        if _vre.search(r'(?:restat|re-stat|repeat|reiterat).*(?:gap|research question)', disc_block, _vre.IGNORECASE):
            violations.append(
                "OVERRIDE discussion: Brief instructs gap restatement — "
                "discussion must NOT restate the gap (already in introduction)."
            )
        if _vre.search(r'(?:re-?descri|summariz|recap).*framework', disc_block, _vre.IGNORECASE):
            violations.append(
                "OVERRIDE discussion: Brief instructs framework re-description — "
                "discussion must NOT re-describe the framework (already in framework section)."
            )

        # 4. Conclusion should not summarize or restate
        conc_block = section_blocks.get("conclusion", "")
        if _vre.search(r'(?:summari[sz]|recap|restat|re-stat|overview of)', conc_block, _vre.IGNORECASE):
            violations.append(
                "OVERRIDE conclusion: Brief instructs summarization — "
                "conclusion must NOT summarize the paper. Only: single insight, one implication, forward-looking close."
            )

        # 5. Propositions count — check if brief suggests too many
        prop_block = section_blocks.get("propositions", "")
        prop_numbers = _vre.findall(r'(?:proposition|P)\s*(\d+)', prop_block, _vre.IGNORECASE)
        if prop_numbers:
            max_prop = max(int(n) for n in prop_numbers)
            if max_prop > self.config.max_propositions:
                violations.append(
                    f"OVERRIDE propositions: Brief suggests {max_prop} propositions — "
                    f"maximum allowed is {self.config.max_propositions}. Drop weakest propositions."
                )

        # 6. Check construct naming consistency — if same concept has multiple names
        # Extract italicized constructs from the brief (common pattern: *Construct Name*)
        constructs = _vre.findall(r'\*([A-Z][^*]{3,50})\*', brief)
        if constructs:
            # Normalize and check for near-duplicates
            normalized = {}
            for c in constructs:
                key = c.lower().strip()
                # Strip common suffixes for matching
                for suffix in (" theory", " framework", " model", " perspective"):
                    key = key.replace(suffix, "")
                normalized.setdefault(key, set()).add(c)
            for key, names in normalized.items():
                if len(names) > 1:
                    violations.append(
                        f"OVERRIDE naming: Multiple names for same construct: {', '.join(sorted(names))}. "
                        f"Use ONE consistent name across all sections."
                    )

        if violations:
            _log.info("ADVISORY BOARD GATE: Found %d violations in writing brief", len(violations))
        else:
            _log.info("ADVISORY BOARD GATE: Writing brief passed all checks")

        return violations

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

        # 1b. Word count MAXIMUM — enforce hard ceiling at 2x minimum (prevent bloat)
        max_words_map = {
            "abstract": 280, "introduction": 960, "methodology": 560,
            "literature_review": 2000, "methods": 1200,
            "results": 1600, "discussion": 1200,
            "conclusion": 400,
            "theoretical_background": 2000, "framework": 1600, "propositions": 1440,
        }
        max_words = max_words_map.get(section_name, 0)
        if max_words > 0 and word_count > max_words:
            issues.append(
                f"Too long: {word_count} words (max {max_words}). "
                f"Cut {word_count - max_words} words. Remove redundant arguments, "
                f"condense examples, eliminate hedging. Be direct and concise."
            )

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

        # 5. Proposition table/text consistency check
        if section_name == "propositions":
            import re as _re2
            # Extract proposition statements from text (P1:, P2:, Proposition 1:, etc.)
            prop_statements = _re2.findall(
                r'(?:Proposition\s+\d+|P\d+)\s*[:\.]\s*(.{30,200})',
                content, _re2.IGNORECASE,
            )
            # Check if there's a summary table (line-based to avoid multi-column double-counting)
            table_lines = [l.strip() for l in content.split('\n')
                           if l.strip().startswith('|') and l.strip().endswith('|')]
            if prop_statements and table_lines:
                # Data rows: exclude header (first row) and separator (---) rows
                data_rows = [l for l in table_lines
                             if '---' not in l and table_lines.index(l) > 0]
                if len(data_rows) > 0 and abs(len(prop_statements) - len(data_rows)) > 1:
                    issues.append(
                        f"Proposition count mismatch: {len(prop_statements)} in text vs "
                        f"{len(data_rows)} in summary table — table must match text exactly"
                    )

        # 6. Gap statement repetition detection
        if section_name in ("discussion", "conclusion", "theoretical_background", "framework"):
            gap_phrases = _re.findall(
                r'(?:gap|lacuna|understudied|under-explored|overlooked|neglected|'
                r'limited attention|little is known|remains unclear|yet to)',
                content, _re.IGNORECASE,
            )
            if len(gap_phrases) > 3:
                issues.append(
                    f"Gap statement repeated {len(gap_phrases)} times in this section. "
                    f"State the gap in the introduction only — reference it here, don't restate it."
                )

        # 7. Duplication check — compare body sections (skip abstract/conclusion — they summarize)
        _COMMON_ACADEMIC_WORDS = {
            "the", "and", "of", "to", "in", "a", "is", "that", "for", "was", "on", "are", "with",
            "as", "this", "by", "from", "be", "have", "an", "has", "their", "been", "were", "or",
            "which", "not", "its", "also", "it", "more", "between", "these", "than", "other",
            "study", "studies", "research", "findings", "results", "evidence", "paper", "review",
            "analysis", "data", "based", "found", "literature", "may", "can", "however", "al",
            "et", "significant", "associated", "effect", "effects", "participants", "reported",
            # Common academic structure words that inflate false positives
            "section", "framework", "theory", "theoretical", "proposed", "model", "approach",
            "context", "relationship", "suggests", "demonstrated", "implications", "contributes",
            "previous", "existing", "present", "provides", "examines", "explores", "discusses",
            "further", "particularly", "specifically", "importantly", "across", "during", "through",
            "platform", "firms", "financial", "digital", "technology", "crisis", "crises",
        }
        # Abstract and conclusion naturally overlap with all sections — don't flag them
        _overlap_exempt = {"abstract", "conclusion", "methodology", "protocol"}
        if sections_dir.exists() and section_name not in _overlap_exempt:
            content_words = set(content.lower().split()) - _COMMON_ACADEMIC_WORDS
            for other_file in sections_dir.iterdir():
                if (other_file.suffix == ".md" and other_file.stem != section_name
                        and other_file.stem not in _overlap_exempt
                        and other_file.stat().st_size > 200):
                    other_words = set(other_file.read_text(encoding="utf-8").lower().split()) - _COMMON_ACADEMIC_WORDS
                    # Skip if either set too small — overlap metric is noisy below 100 unique words
                    if len(content_words) < 100 or len(other_words) < 100:
                        continue
                    if content_words and other_words:
                        overlap = len(content_words & other_words) / min(len(content_words), len(other_words))
                        # Conceptual paper sections that build on each other get a higher threshold
                        # (TB→framework→propositions share constructs by design)
                        _construct_family = {"theoretical_background", "framework", "propositions"}
                        if section_name in _construct_family and other_file.stem in _construct_family:
                            threshold = 0.45  # Same construct family — allow more shared vocabulary
                        else:
                            threshold = 0.30
                        if overlap > threshold:
                            issues.append(f"High overlap ({overlap:.0%}) with '{other_file.stem}' — likely duplication")

        # 8. Sentence length check — verbose academic writing kills readability
        sentences = _re.split(r'(?<=[.!?])\s+', content)
        sentences = [s for s in sentences if len(s.split()) > 3]  # ignore fragments
        if sentences:
            avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
            max_avg = self.config.max_avg_sentence_length
            if avg_len > max_avg:
                issues.append(
                    f"Average sentence length {avg_len:.0f} words (max {max_avg}). "
                    f"Break long sentences. Remove hedging phrases ('it is imperative to', "
                    f"'it is necessary to', 'it should be noted that'). Be direct."
                )

        # 9. Hedging phrase detection — flag ornamental academic padding
        _HEDGING_PHRASES = [
            r'it is (?:imperative|necessary|important|crucial|vital|essential) (?:to|that)',
            r'it (?:should|must|can) be (?:noted|observed|argued|emphasized|stressed) that',
            r'(?:in order|so as) to',
            r'(?:a (?:significant|substantial|considerable|growing) (?:body|amount|number|volume) of)',
            r'(?:plays? (?:a|an) (?:significant|crucial|vital|important|key|critical|pivotal) role)',
            r'(?:in the context of|within the (?:context|framework|domain) of)',
            r'(?:serves? as (?:a|an) (?:catalyst|driver|enabler|mechanism|conduit|vehicle))',
            r'(?:deserve|warrant|merit)s?\s+(?:careful|further|close|special)\s+(?:consideration|attention|examination|investigation)',
            r'(?:carry|have|hold|bear)s?\s+(?:significant|important|profound|substantial)\s+implications',
            r'(?:the )?(?:broader|wider|larger)\s+context\s+(?:within\s+which|of|in\s+which)',
        ]
        hedge_count = 0
        for hp in _HEDGING_PHRASES:
            hedge_count += len(_re.findall(hp, content, _re.IGNORECASE))
        if hedge_count > 5:
            issues.append(
                f"{hedge_count} hedging/filler phrases detected. Cut ornamental language. "
                f"Replace 'it is imperative to X' with 'X'. Replace 'plays a crucial role in' "
                f"with 'drives/enables/shapes'. Be direct."
            )

        # 10. Table proliferation check — only theoretical_background should have definition/comparison tables
        if section_name in ("framework", "discussion", "conclusion", "propositions"):
            table_count = len(_re.findall(r'\|[^|]+\|[^|]+\|[^|]+\|', content))
            if table_count > 5:  # More than a header + separator + 1 data row = a real table
                table_type = "summary" if section_name == "propositions" else "any"
                max_tables = 1 if section_name == "propositions" else 0
                if section_name == "propositions" and table_count > 15:
                    issues.append(
                        f"Too many table rows ({table_count}) in {section_name}. "
                        f"Keep only ONE summary table at the end. Remove all definition "
                        f"and comparison tables — those belong in theoretical_background only."
                    )
                elif section_name in ("discussion", "conclusion"):
                    issues.append(
                        f"Tables found in {section_name} ({table_count} rows). "
                        f"Discussion and conclusion must NOT contain tables. "
                        f"Remove all tables — present insights in prose only. "
                        f"Definition tables belong in theoretical_background."
                    )

        # 11. Table content dedup — detect duplicate tables across sections
        if section_name in ("framework", "discussion", "propositions"):
            # Extract table blocks from this section
            table_blocks = _re.findall(r'(\|[^\n]+\|\n(?:\|[^\n]+\|\n){2,})', content)
            if table_blocks and sections_dir.exists():
                for other_file in sections_dir.iterdir():
                    if other_file.suffix == ".md" and other_file.stem != section_name and other_file.stat().st_size > 200:
                        other_content = other_file.read_text(encoding="utf-8")
                        other_tables = _re.findall(r'(\|[^\n]+\|\n(?:\|[^\n]+\|\n){2,})', other_content)
                        for tbl in table_blocks:
                            tbl_words = set(tbl.lower().split()) - _COMMON_ACADEMIC_WORDS
                            for otbl in other_tables:
                                otbl_words = set(otbl.lower().split()) - _COMMON_ACADEMIC_WORDS
                                if tbl_words and otbl_words:
                                    tbl_overlap = len(tbl_words & otbl_words) / min(len(tbl_words), len(otbl_words))
                                    if tbl_overlap > 0.30:
                                        issues.append(
                                            f"Duplicate table detected: table in '{section_name}' has {tbl_overlap:.0%} "
                                            f"overlap with a table in '{other_file.stem}'. "
                                            f"Remove the duplicate — tables should appear only ONCE. "
                                            f"Definition/comparison tables belong in theoretical_background only."
                                        )
                                        break  # One match is enough
                            if issues and "Duplicate table" in issues[-1]:
                                break  # Don't flag multiple duplicates per section

        # 12. Proposition count cap (propositions section only)
        if section_name == "propositions":
            prop_count = len(_re.findall(
                r'(?:^|\n)\s*\*?\*?(?:Proposition|P)\s*\d+\s*[:.]',
                content, _re.IGNORECASE,
            ))
            max_props = self.config.max_propositions
            if prop_count > max_props:
                issues.append(
                    f"Too many propositions: {prop_count} (max {max_props}). "
                    f"Keep only the sharpest, most novel ones. Depth over breadth. "
                    f"Drop any that restate well-established findings."
                )

        # 13. Discussion must NOT contain new propositions (P1, P2, Proposition 1, etc.)
        if section_name == "discussion":
            disc_props = _re.findall(
                r'(?:^|\n)\s*\*?\*?(?:Proposition|P)\s*\d+\s*[:.]',
                content, _re.IGNORECASE,
            )
            if disc_props:
                issues.append(
                    f"Discussion contains {len(disc_props)} proposition labels (P1, P2, etc.) — "
                    f"propositions belong ONLY in the propositions section. "
                    f"REFER to existing propositions by label, do NOT define new ones here."
                )

        # 14. Construct naming consistency — check this section against theoretical_background
        if section_name in ("framework", "discussion", "propositions"):
            tb_file = sections_dir / "theoretical_background.md"
            if tb_file.exists():
                tb_content = tb_file.read_text(encoding="utf-8")
                # Extract italicized constructs (single *italic*, NOT **bold**)
                # Pattern: non-asterisk before *, capture content, * followed by non-asterisk
                _construct_pat = r'(?<!\*)\*([A-Z][^*]{3,50})\*(?!\*)'
                tb_constructs = set(c.strip() for c in _re.findall(_construct_pat, tb_content))
                sec_constructs = set(c.strip() for c in _re.findall(_construct_pat, content))
                # Find constructs in this section that don't appear in theoretical_background
                new_constructs = sec_constructs - tb_constructs
                # Filter out false positives: section names, proposition labels, short strings
                new_constructs = {c for c in new_constructs if len(c) > 10
                    and not _re.match(r'(?:Proposition|P)\s*\d', c, _re.IGNORECASE)
                    and c.lower() not in (
                    "theoretical background", "literature review", "research question",
                    "future research", "practical implications",
                )}
                if len(new_constructs) >= 2:
                    issues.append(
                        f"Construct naming drift: {section_name} introduces {len(new_constructs)} "
                        f"new construct names not in theoretical_background: "
                        f"{', '.join(sorted(new_constructs)[:4])}. "
                        f"Use the EXACT construct names from theoretical_background."
                    )

        # 15. LLM artifact detection — catches patterns that survive meta-text stripping
        _llm_artifacts = [
            # AI self-references (catastrophic)
            (r'(?:as (?:an? )?(?:AI|artificial intelligence|language model|LLM))', "AI self-reference"),
            # Conversational preambles mid-text
            (r'(?:as (?:you )?(?:requested|asked)|per your (?:request|instructions))', "conversational meta-text"),
            # Placeholders
            (r'\[(?:INSERT|TODO|PLACEHOLDER|ADD|INCLUDE|TBD)[^\]]*\]', "placeholder text"),
            # Word count annotations
            (r'[\[\(]word count[^\]\)]*[\]\)]', "word count annotation"),
            # Emoji
            (r'[\U0001F300-\U0001F9FF\U00002702-\U000027B0\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF]', "emoji"),
            # HTML tags
            (r'</?(?:b|i|em|strong|u|br|p|div|span|a|h[1-6])[^>]*>', "HTML tags"),
        ]
        for pat_str, label in _llm_artifacts:
            if _re.search(pat_str, content, _re.IGNORECASE):
                issues.append(
                    f"LLM artifact: {label} detected. Remove ALL non-academic content. "
                    f"A published paper must contain zero AI meta-commentary, placeholders, or markup."
                )

        # 16. Hollow topic sentences — "This section aims to discuss..."
        if section_name not in ("abstract", "protocol"):
            _hollow_patterns = [
                r'(?:this|the)\s+(?:section|part|chapter)\s+(?:aims?|seeks?|attempts?|intends?|will)\s+(?:to\s+)?(?:discuss|examine|explore|analyze|present|address|investigate)',
                r'(?:in|within)\s+this\s+(?:section|part),?\s+we\s+(?:will|shall|aim to|seek to)\s+(?:discuss|examine|explore)',
                r'(?:the (?:purpose|aim|goal|objective) of this (?:section|part) is to)',
            ]
            hollow_count = 0
            for hp in _hollow_patterns:
                hollow_count += len(_re.findall(hp, content, _re.IGNORECASE))
            if hollow_count > 0:
                issues.append(
                    f"Hollow topic sentence(s): {hollow_count} found. Do NOT announce what "
                    f"the section will do — just DO it. Replace 'This section discusses X' "
                    f"with the actual argument about X. Top journals reject self-referential writing."
                )

        # 17. Shopping-list literature review detection
        # Pattern: 3+ consecutive "Author (Year) found/showed/demonstrated..." sentences
        if section_name in ("discussion", "literature_review", "theoretical_background", "results"):
            shopping_matches = _re.findall(
                r'(?:^|\. )([A-Z][a-z]+(?:\s+(?:et\s+al\.|&\s+[A-Z][a-z]+))?)\s*\(\d{4}\)\s+'
                r'(?:found|showed|demonstrated|reported|argued|suggested|observed|noted|indicated|revealed|concluded|proposed)',
                content,
            )
            if len(shopping_matches) >= 4:
                issues.append(
                    f"Shopping-list writing: {len(shopping_matches)} consecutive 'Author (Year) found...' sentences. "
                    f"SYNTHESIZE — group findings by theme, not by author. "
                    f"A top-tier paper integrates evidence, it doesn't catalog it. "
                    f"Use thematic topic sentences: 'Crisis-driven diversification follows three patterns: ...'"
                )

        # 18. Bullet-list detection — top journals use prose, not lists
        if section_name not in ("abstract", "protocol", "methodology"):
            bullet_lines = _re.findall(r'^\s*[-*•]\s+', content, _re.MULTILINE)
            if len(bullet_lines) >= 3:
                issues.append(
                    f"Bullet list detected ({len(bullet_lines)} items). Top-tier journals require "
                    f"continuous prose, not bullet points. Convert each bullet into a full sentence "
                    f"within a paragraph. Use transition words to connect ideas."
                )

        # 19. Em-dash usage — replace with proper punctuation
        em_dashes = content.count('\u2014')  # —
        en_dashes = content.count('\u2013')  # –
        total_dashes = em_dashes + en_dashes
        if total_dashes > 0:
            issues.append(
                f"Em/en-dash found ({total_dashes} instances). Replace ALL dashes: "
                f"use commas, semicolons, colons, or parentheses instead. "
                f"No em-dashes (\u2014) or en-dashes (\u2013) anywhere in the paper."
            )

        # 20. Banned paragraph openers — filler transitions
        if section_name not in ("abstract",):
            banned_openers = _re.findall(
                r'(?:^|\n)\s*(?:Furthermore|Additionally|Moreover)\s*[,;]',
                content,
            )
            if banned_openers:
                issues.append(
                    f"Banned paragraph opener(s): {len(banned_openers)} found "
                    f"('Furthermore,', 'Additionally,', 'Moreover,'). "
                    f"These are filler transitions that signal no new argument. "
                    f"Replace with the actual argument point as topic sentence."
                )

        # 21. Unresolved (Author, Year) placeholders — template text never filled in
        placeholders = _re.findall(r'\(Author,?\s*Year\)', content, _re.IGNORECASE)
        if placeholders:
            issues.append(
                f"Unresolved citation placeholder(s): {len(placeholders)} instances of '(Author, Year)'. "
                f"Replace each with a real (Author, Year) citation from the database, "
                f"or remove the claim if no supporting citation exists."
            )

        # 22. Abstract must NOT contain citations
        if section_name == "abstract":
            abstract_cites = _re.findall(r'\([A-Z][a-z]+(?:\s+et\s+al\.?)?,\s*\d{4}\)', content)
            if abstract_cites:
                issues.append(
                    f"Abstract contains {len(abstract_cites)} citation(s). "
                    f"Abstracts must not cite specific papers. Remove all (Author, Year) "
                    f"references and restate findings in your own words."
                )

        # 23. Single-sentence paragraphs — weak academic writing
        if section_name not in ("abstract", "protocol", "methodology"):
            paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 20]
            single_sent = 0
            for para in paragraphs:
                # Skip tables and headings
                if para.startswith("|") or para.startswith("#"):
                    continue
                sentences = _re.split(r'(?<=[.!?])\s+', para)
                if len(sentences) == 1 and len(para.split()) > 5:
                    single_sent += 1
            if single_sent >= 2:
                issues.append(
                    f"{single_sent} single-sentence paragraph(s). Academic writing requires "
                    f"paragraphs of 4-8 sentences. Merge orphan sentences into adjacent "
                    f"paragraphs or expand them with supporting evidence."
                )

        # 24. Orphaned table/figure references — mentions Table/Figure N but it doesn't exist
        if section_name not in ("abstract", "protocol"):
            table_refs = set(_re.findall(r'Table\s+(\d+)', content))
            table_defs = set(_re.findall(r'\*\*Table\s+(\d+)', content))
            # Also check if table definitions exist in other sections
            for sf in sections_dir.iterdir():
                if sf.suffix == ".md" and sf.stem != section_name:
                    other = sf.read_text(encoding="utf-8")
                    table_defs.update(_re.findall(r'\*\*Table\s+(\d+)', other))
            orphan_tables = table_refs - table_defs
            if orphan_tables:
                issues.append(
                    f"Orphaned table reference(s): Table {', '.join(sorted(orphan_tables))} "
                    f"mentioned but never defined. Either create the table or remove the reference."
                )

            figure_refs = set(_re.findall(r'Figure\s+(\d+)', content))
            figure_defs = set(_re.findall(r'\*\*Figure\s+(\d+)', content))
            for sf in sections_dir.iterdir():
                if sf.suffix == ".md" and sf.stem != section_name:
                    other = sf.read_text(encoding="utf-8")
                    figure_defs.update(_re.findall(r'\*\*Figure\s+(\d+)', other))
            orphan_figures = figure_refs - figure_defs
            if orphan_figures:
                issues.append(
                    f"Orphaned figure reference(s): Figure {', '.join(sorted(orphan_figures))} "
                    f"mentioned but never defined. Either create the figure or remove the reference."
                )

        # 25. "et al." misuse — APA requires 3+ authors for et al.
        if section_name not in ("abstract",):
            # Find "Author & Other et al." patterns — et al. with only 2 visible authors
            bad_etal = _re.findall(
                r'([A-Z][a-z]+)\s+(?:and|&)\s+([A-Z][a-z]+)\s+et\s+al\.',
                content,
            )
            if bad_etal:
                issues.append(
                    f"'et al.' misuse: {len(bad_etal)} instance(s) with 2 named authors. "
                    f"APA 7th edition: use 'et al.' only when 3+ authors exist. "
                    f"For 2 authors, always name both: '(Author & Author, Year)'."
                )

        # 26. Future year citations — data integrity check
        import datetime as _dt
        current_year = _dt.datetime.now().year
        future_cites = _re.findall(rf'\([A-Z][a-z]+.*?,\s*({current_year + 2}\d*)\)', content)
        if future_cites:
            issues.append(
                f"Future year citation(s): year {', '.join(set(future_cites))} is implausible. "
                f"Check for typos in citation years."
            )

        # 27. Excessive self-referential language — "this paper" > 5 times
        if section_name not in ("abstract", "methodology", "protocol"):
            self_refs = len(_re.findall(r'\b(?:this paper|this study|this review|our (?:paper|study|review|framework|analysis))\b', content, _re.IGNORECASE))
            if self_refs > 5:
                issues.append(
                    f"Excessive self-reference: 'this paper/study/review' used {self_refs} times. "
                    f"Reduce to 2-3 per section. Let the argument speak for itself."
                )

        # 28. Repeated citation clusters — same (Author, Year) cited 4+ times in one section
        if section_name not in ("abstract", "protocol"):
            cite_counts: dict[str, int] = {}
            for m in _re.finditer(r'\(([A-Z][a-z]+(?:\s+et\s+al\.?)?),?\s*(\d{4})\)', content):
                key = f"{m.group(1)}, {m.group(2)}"
                cite_counts[key] = cite_counts.get(key, 0) + 1
            over_cited = [(k, v) for k, v in cite_counts.items() if v >= 4]
            if over_cited:
                issues.append(
                    f"Over-citation: {', '.join(f'{k} ({v}x)' for k, v in over_cited)}. "
                    f"Citing the same paper 4+ times in one section suggests over-reliance. "
                    f"Diversify evidence sources or consolidate mentions."
                )

        # 29. Methodology claiming human dual-reviewer screening (must use AI-automated language)
        if section_name in ("methods", "methodology"):
            human_review_claims = _re.findall(
                r'(?:dual[\s-]?reviewer|two\s+(?:independent\s+)?reviewers?\s+(?:screened|assessed|evaluated|independently)|'
                r'inter[\s-]?rater\s+(?:reliability|agreement)|consensus\s+(?:meeting|was\s+reached\s+between\s+reviewers)|'
                r'cohen[\'\u2019]?s?\s+kappa|percent\s+agreement\s+between\s+(?:raters|reviewers)|'
                r'disagreements?\s+(?:were|was)\s+resolved\s+(?:through|by|via)\s+discussion)',
                content, _re.IGNORECASE,
            )
            if human_review_claims:
                issues.append(
                    f"Methodology claims human dual-reviewer screening ({len(human_review_claims)} instance(s)). "
                    f"This is an AI-generated review. Replace with: 'Title and abstract screening was "
                    f"conducted using automated relevance scoring.' Acknowledge AI-assisted screening "
                    f"as a limitation."
                )

        # 30. Unicode/non-ASCII in author names within citations
        if section_name not in ("abstract",):
            unicode_cites = _re.findall(r'\(([^\x00-\x7F][^)]+),\s*\d{4}\)', content)
            if unicode_cites:
                issues.append(
                    f"Non-ASCII characters in {len(unicode_cites)} citation(s). "
                    f"Transliterate author names to ASCII for consistency: "
                    f"{', '.join(unicode_cites[:3])}"
                )

        # 31. Content that is 100% table with no prose
        if section_name not in ("abstract", "protocol"):
            non_table_lines = [l for l in content.split('\n')
                               if l.strip() and not l.strip().startswith('|') and not l.strip().startswith('#')]
            table_lines = [l for l in content.split('\n')
                          if l.strip() and l.strip().startswith('|')]
            if table_lines and len(non_table_lines) < 3:
                issues.append(
                    f"Section is almost entirely table ({len(table_lines)} table lines, "
                    f"{len(non_table_lines)} prose lines). Sections must contain substantive "
                    f"prose analysis. Tables supplement the argument, they don't replace it."
                )

        # 32. Journal tier citation quality — at least 30% citations should be AAA/AA
        if section_name not in ("abstract", "protocol", "methodology"):
            from .db import classify_journal
            citations = _extract_citations_from_text(content)
            if len(citations) >= 5:
                db = self.tools.db
                session_id = self.tools.session_id
                if db and session_id:
                    tier_hits = 0
                    for author, year in citations:
                        check = _verify_citation_against_db(author, year, db, session_id)
                        if check.get("verified") and check.get("paper_id"):
                            try:
                                row = db._conn.execute(
                                    "SELECT journal_tier FROM papers WHERE paper_id = ?",
                                    (check["paper_id"],),
                                ).fetchone()
                                if row and row[0] in ("AAA", "AA"):
                                    tier_hits += 1
                            except Exception:
                                pass
                    tier_ratio = tier_hits / len(citations)
                    min_pct = self.config.journal_tier_min_pct
                    target_ratio = self.config.journal_tier_ratio
                    if tier_ratio < min_pct:
                        issues.append(
                            f"Low journal quality: only {tier_hits}/{len(citations)} ({tier_ratio:.0%}) "
                            f"citations are from AAA/AA journals (target: {min_pct:.0%}+, "
                            f"ratio: {target_ratio:.0f}:1 top-tier per unranked). "
                            f"Replace weaker citations with top-tier sources marked [AAA] or [AA] "
                            f"in the citation menu. Top-tier journals include: AMJ, SMJ, JIBS, "
                            f"Research Policy, Nature, Science, Lancet, JF, AER."
                        )

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
            expected = {"abstract", "introduction", "methodology", "theoretical_background", "framework", "propositions", "discussion", "conclusion"}
        else:
            expected = {"abstract", "introduction", "literature_review", "methods", "results", "discussion", "conclusion"}

        missing = expected - set(section_files.keys())
        if missing:
            _log.warning("PRE-CRITIC: Missing sections: %s", missing)

        # Check each existing section and collect failures
        failing_sections: list[tuple[str, list[str]]] = []
        total_words = 0
        total_citations: set[tuple[str, str]] = set()

        from .tools.writing import _extract_citations_from_text, _verify_citation_against_db

        for section_name in expected:
            if section_name not in section_files:
                continue
            issues = self._check_section_quality(section_name, sections_dir, context)
            content = section_files[section_name].read_text(encoding="utf-8")
            total_words += len(content.split())
            total_citations.update(_extract_citations_from_text(content))
            if issues:
                failing_sections.append((section_name, issues))

        # Total word count check — most journals have 10K-12K word limits
        _MAX_TOTAL_WORDS = 12000
        if total_words > _MAX_TOTAL_WORDS:
            # Find the longest body section and flag it for trimming
            longest_section = max(
                ((s, len(section_files[s].read_text(encoding="utf-8").split()))
                 for s in expected if s in section_files and s not in ("abstract", "methodology")),
                key=lambda x: x[1],
            )
            _log.warning("PRE-CRITIC: Total words %d exceeds %d limit — flagging %s (%d words) for trimming",
                         total_words, _MAX_TOTAL_WORDS, longest_section[0], longest_section[1])
            failing_sections.append((longest_section[0], [
                f"Paper is {total_words} words — journal limit is {_MAX_TOTAL_WORDS}. "
                f"This section is the longest ({longest_section[1]} words). "
                f"Cut {total_words - _MAX_TOTAL_WORDS} words total. Remove redundant arguments, "
                f"shorten examples, eliminate hedging phrases. Be ruthless."
            ]))

        # Section-to-section repetition detection via embedding cosine similarity
        # If two body sections are too similar, one is likely redundant
        _body_sections = [s for s in expected if s not in ("abstract", "conclusion", "methodology", "protocol")]
        body_contents = {}
        for s in _body_sections:
            if s in section_files:
                body_contents[s] = section_files[s].read_text(encoding="utf-8")
        if len(body_contents) >= 2 and hasattr(self, '_embed_client') and self._embed_client:
            try:
                section_embs: dict[str, list[float]] = {}
                for s, text in body_contents.items():
                    # Embed first 3000 chars of each section
                    res = self._embed_client.models.embed_content(
                        model="gemini-embedding-001", contents=text[:3000],
                    )
                    if res.embeddings and len(res.embeddings) > 0:
                        section_embs[s] = res.embeddings[0].values
                # Compare all pairs
                overlap_threshold = self.config.max_section_overlap
                section_names = list(section_embs.keys())
                for i in range(len(section_names)):
                    for j in range(i + 1, len(section_names)):
                        sim = self._cosine_sim(section_embs[section_names[i]], section_embs[section_names[j]])
                        if sim > overlap_threshold:
                            _log.warning(
                                "PRE-CRITIC: High cosine overlap %.2f between '%s' and '%s'",
                                sim, section_names[i], section_names[j],
                            )
                            # Flag the later section for rewrite
                            target = section_names[j]
                            failing_sections.append((target, [
                                f"Section '{target}' has {sim:.0%} cosine similarity with '{section_names[i]}'. "
                                f"This indicates heavy repetition. Remove redundant arguments — "
                                f"each section should make DIFFERENT points. "
                                f"If the same argument appears in both, keep it in the more appropriate "
                                f"section and delete it from this one."
                            ]))
            except Exception as exc:
                _log.warning("PRE-CRITIC: Section overlap check failed: %s", exc)

        # Hard gate: paper must cite at least 30 unique papers
        _MIN_UNIQUE_CITATIONS = 30
        if len(total_citations) < _MIN_UNIQUE_CITATIONS:
            failing_sections.append((
                "introduction",  # Force rewrite of largest body section to add more citations
                [f"Paper cites only {len(total_citations)} unique papers — minimum is {_MIN_UNIQUE_CITATIONS}. "
                 f"Add more citations from the evidence database using search_similar() and list_papers()."],
            ))
            _log.warning("PRE-CRITIC: Only %d unique citations — need %d+", len(total_citations), _MIN_UNIQUE_CITATIONS)

        # Hard gate: strip phantom citations — (Author, Year) not in DB
        import re as _cite_re
        db = self.tools.db
        session_id = self.tools.session_id
        if db and session_id:
            phantom_stripped = 0
            for section_name_s in expected:
                sf = section_files.get(section_name_s)
                if not sf:
                    continue
                content = sf.read_text(encoding="utf-8")
                cited = _extract_citations_from_text(content)
                if not cited:
                    continue
                unverified = []
                for author, year in cited:
                    if not _verify_citation_against_db(author, year, db, session_id):
                        unverified.append((author, year))
                if unverified:
                    new_content = content
                    for author, year in unverified:
                        # Strip parenthetical citations: (Author, Year)
                        new_content = _cite_re.sub(
                            rf'\(\s*{_cite_re.escape(author)}(?:\s+et\s+al\.?)?\s*,\s*{year}\s*\)',
                            '', new_content,
                        )
                        # Strip narrative citations: Author (Year)
                        new_content = _cite_re.sub(
                            rf'{_cite_re.escape(author)}(?:\s+et\s+al\.?)?\s*\(\s*{year}\s*\)',
                            '', new_content,
                        )
                    # Clean orphaned punctuation
                    new_content = _cite_re.sub(r'\(\s*;\s*', '(', new_content)
                    new_content = _cite_re.sub(r';\s*\)', ')', new_content)
                    new_content = _cite_re.sub(r'\(\s*\)', '', new_content)
                    new_content = _cite_re.sub(r'(?:[Aa]ccording)\s+to\s*,\s*', '', new_content)
                    new_content = _cite_re.sub(
                        r'(?:[Aa]s\s+)?(?:noted|identified|suggested|argued|proposed|demonstrated)\s+by\s*,\s*',
                        '', new_content,
                    )
                    new_content = _cite_re.sub(r'\s{2,}', ' ', new_content)
                    if new_content != content:
                        sf.write_text(new_content, encoding="utf-8")
                        phantom_stripped += len(unverified)
                        _log.info("PRE-CRITIC: Stripped %d phantom citations from %s: %s",
                                  len(unverified), section_name_s,
                                  [(a, y) for a, y in unverified[:5]])
            if phantom_stripped:
                _log.warning("PRE-CRITIC: Total phantom citations stripped: %d", phantom_stripped)
                if on_event:
                    on_event(StepEvent("text", data=f"Stripped {phantom_stripped} phantom citations not in DB", depth=0))
                # Re-count citations after stripping
                total_citations = set()
                for section_name_s2 in expected:
                    sf2 = section_files.get(section_name_s2)
                    if sf2:
                        total_citations.update(_extract_citations_from_text(sf2.read_text(encoding="utf-8")))

        # Strip (Author, Year) placeholders — template text never filled in
        placeholder_stripped = 0
        for section_name_p in expected:
            sf = section_files.get(section_name_p)
            if not sf:
                continue
            content = sf.read_text(encoding="utf-8")
            import re as _ph_re
            new_content = _ph_re.sub(r'\(Author,?\s*Year\)', '', content, flags=_ph_re.IGNORECASE)
            if new_content != content:
                sf.write_text(new_content, encoding="utf-8")
                count = len(_ph_re.findall(r'\(Author,?\s*Year\)', content, _ph_re.IGNORECASE))
                placeholder_stripped += count
                _log.info("PRE-CRITIC: Stripped %d (Author, Year) placeholders from %s", count, section_name_p)

        # Global coherence passes (programmatic, no LLM cost)
        prop_fixes = self._global_proposition_coherence(on_event)
        table_fixes = self._global_table_dedup(on_event)
        table_renum = self._global_table_renumber(on_event)
        temporal_fixes = self._global_temporal_consistency(topic, on_event)
        if prop_fixes or table_fixes or table_renum or temporal_fixes:
            _log.info("PRE-CRITIC: Global coherence fixes — %d proposition, %d table dedup, %d table renum, %d temporal",
                      prop_fixes, table_fixes, table_renum, temporal_fixes)
            # Re-scan word counts after table removal
            total_words = 0
            for section_name_iter in expected:
                sf = section_files.get(section_name_iter)
                if sf:
                    total_words += len(sf.read_text(encoding="utf-8").split())

        # Verify references file exists — if not, generate from DB metadata
        apa_file = ws / self.config.session_root_dir / "output" / "references_apa.txt"
        bib_file = ws / self.config.session_root_dir / "output" / "references.bib"
        if not apa_file.exists() or apa_file.stat().st_size < 50:
            _log.warning("PRE-CRITIC: References file missing or empty — generating from DB")
            if on_event:
                on_event(StepEvent("text", data="References missing — building from database", depth=0))
            # First try the full get_citations pipeline (matches in-text to DB + OpenAlex)
            try:
                self.tools.dispatch("get_citations", {})
            except Exception as exc:
                _log.error("PRE-CRITIC: get_citations failed: %s — falling back to DB-direct", exc)
            # If get_citations still didn't produce a file, build directly from DB
            if not apa_file.exists() or apa_file.stat().st_size < 50:
                _log.warning("PRE-CRITIC: get_citations produced no output — building APA from DB metadata")
                self._generate_references_from_db(
                    total_citations, sections_dir, apa_file, bib_file, db, session_id,
                )

        _log.info("PRE-CRITIC: total_words=%d, unique_citations=%d, failing_sections=%d/%d, phantoms_stripped=%d, placeholders_stripped=%d",
                   total_words, len(total_citations), len(failing_sections), len(expected),
                   phantom_stripped if db and session_id else 0, placeholder_stripped)

        if not failing_sections:
            _log.info("PRE-CRITIC: All sections pass programmatic checks — proceeding to output assembly")
            return

        # #8: Programmatic auto-fixes BEFORE sending to LLM rewrite
        import re as _pre_re
        fixed_programmatically = 0
        for section_name, issues in failing_sections[:]:
            section_file = section_files.get(section_name)
            if not section_file:
                continue
            content = section_file.read_text(encoding="utf-8")
            original_content = content

            # Auto-fix: strip duplicate gap statements (keep first, remove later ones)
            gap_issues = [i for i in issues if "Gap statement repeated" in i]
            if gap_issues and section_name not in ("introduction",):
                gap_pattern = r'(?:There (?:is|exists|remains) (?:a )?(?:significant |notable |critical )?gap|' \
                              r'[Ll]imited (?:research|attention|scholarship) (?:has |)(?:been |)(?:devoted|paid|given)|' \
                              r'[Ll]ittle is known|[Rr]emains (?:unclear|understudied|under-explored))[^.]*\.'
                matches = list(_pre_re.finditer(gap_pattern, content))
                if len(matches) > 1:
                    for m in reversed(matches[1:]):
                        content = content[:m.start()] + content[m.end():]
                    _log.info("PRE-CRITIC: Auto-stripped %d duplicate gap statements from %s", len(matches) - 1, section_name)

            # Auto-fix: strip citations from abstract
            if section_name == "abstract" and any("Abstract contains" in i for i in issues):
                content = _pre_re.sub(r'\s*\([A-Z][a-z]+(?:\s+et\s+al\.?)?,\s*\d{4}(?:\s*;\s*[A-Z][a-z]+(?:\s+et\s+al\.?)?,\s*\d{4})*\)', '', content)
                content = _pre_re.sub(r'[A-Z][a-z]+(?:\s+et\s+al\.?)?\s*\(\d{4}\)', '', content)
                content = _pre_re.sub(r'\s{2,}', ' ', content)
                _log.info("PRE-CRITIC: Auto-stripped citations from abstract")

            # Auto-fix: replace em-dashes and en-dashes with commas
            if any("Em/en-dash" in i for i in issues):
                content = _pre_re.sub(r'\s*[\u2014\u2013]\s*', ', ', content)
                content = _pre_re.sub(r',\s*,', ',', content)
                content = _pre_re.sub(r',\s*\.', '.', content)
                _log.info("PRE-CRITIC: Auto-replaced em/en-dashes in %s", section_name)

            # Auto-fix: strip future year citations
            if any("Future year citation" in i for i in issues):
                import datetime as _dt_fix
                future_year = _dt_fix.datetime.now().year + 2
                content = _pre_re.sub(
                    rf'\([A-Z][a-z]+(?:\s+et\s+al\.?)?,\s*(?:{future_year}\d*)\)', '', content,
                )
                content = _pre_re.sub(r'\s{2,}', ' ', content)
                _log.info("PRE-CRITIC: Auto-stripped future year citations from %s", section_name)

            # Auto-fix: replace banned paragraph openers with empty string
            if any("Banned paragraph opener" in i for i in issues):
                content = _pre_re.sub(
                    r'(?:^|\n)(\s*)(?:Furthermore|Additionally|Moreover)\s*[,;]\s*',
                    r'\n\1', content,
                )
                _log.info("PRE-CRITIC: Auto-stripped banned openers from %s", section_name)

            # Auto-fix: replace dual-reviewer claims with AI-automated language
            if any("Methodology claims human dual-reviewer" in i for i in issues):
                _dual_reviewer_patterns = [
                    (r'[Tt]wo independent reviewers (?:screened|assessed|evaluated) (?:all |the )?(?:titles|abstracts|records)(?:\s+and\s+(?:abstracts|titles))?(?:\s+for\s+\w+)?',
                     'Title and abstract screening was conducted using automated AI-assisted relevance scoring'),
                    (r'[Dd]ual[\s-]?reviewer screening was (?:performed|conducted|used)',
                     'Screening was conducted using automated relevance scoring'),
                    (r'[Dd]isagreements? (?:were|was) resolved (?:through|by|via) discussion',
                     'Papers scoring above the pre-specified relevance threshold were selected for full-text review'),
                    (r'[Ii]nter[\s-]?rater (?:reliability|agreement) was (?:assessed|calculated|measured)',
                     'Automated relevance scoring consistency was verified through threshold calibration'),
                ]
                for pat, replacement in _dual_reviewer_patterns:
                    content = _pre_re.sub(pat, replacement, content)
                _log.info("PRE-CRITIC: Auto-replaced dual-reviewer claims in %s", section_name)

            # Auto-fix: convert bullet lists to prose sentences
            if any("Bullet list detected" in i for i in issues):
                lines = content.split('\n')
                new_lines = []
                bullet_buffer = []
                for line in lines:
                    stripped = line.strip()
                    if _pre_re.match(r'^[-*\u2022]\s+', stripped):
                        # Remove bullet prefix and collect
                        bullet_text = _pre_re.sub(r'^[-*\u2022]\s+', '', stripped).strip()
                        if bullet_text and not bullet_text.endswith('.'):
                            bullet_text += '.'
                        bullet_buffer.append(bullet_text)
                    else:
                        if bullet_buffer:
                            # Flush bullet buffer as prose paragraph
                            new_lines.append(' '.join(bullet_buffer))
                            bullet_buffer = []
                        new_lines.append(line)
                if bullet_buffer:
                    new_lines.append(' '.join(bullet_buffer))
                content = '\n'.join(new_lines)
                _log.info("PRE-CRITIC: Auto-converted bullet lists to prose in %s", section_name)

            if content != original_content:
                section_file.write_text(content, encoding="utf-8")
                fixed_programmatically += 1
                # Re-check issues after fix
                new_issues = self._check_section_quality(section_name, sections_dir, context)
                if not new_issues:
                    failing_sections.remove((section_name, issues))
                    _log.info("PRE-CRITIC: Section '%s' fixed programmatically — no LLM rewrite needed", section_name)

        if fixed_programmatically:
            _log.info("PRE-CRITIC: Fixed %d sections programmatically", fixed_programmatically)

        if not failing_sections:
            _log.info("PRE-CRITIC: All issues fixed programmatically — no LLM rewrites needed")
            return

        # Auto-rewrite remaining failing sections with LLM (max 3 to avoid infinite loop)
        if on_event:
            on_event(StepEvent("text", data=f"Pre-critic: {len(failing_sections)} sections need LLM fixes", depth=0))

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

    def _global_proposition_coherence(self, on_event: StepCallback | None) -> int:
        """Programmatic: ensure propositions are numbered consistently and appear only in the propositions section.

        Returns number of fixes applied.
        """
        import re as _gpc_re
        ws = self.config.workspace
        sections_dir = ws / self.config.session_root_dir / "output" / "sections"
        if not sections_dir.exists():
            return 0

        # 1. Extract canonical propositions from the propositions section
        prop_file = sections_dir / "propositions.md"
        if not prop_file.exists():
            return 0

        prop_content = prop_file.read_text(encoding="utf-8")
        # Find all proposition labels: P1, P2, Proposition 1, Proposition 2, etc.
        canonical_props = _gpc_re.findall(
            r'(?:^|\n)\s*\*?\*?(?:Proposition\s+(\d+)|P(\d+))\s*[:.]\s*(.{10,200})',
            prop_content, _gpc_re.IGNORECASE,
        )
        # Build canonical set of proposition numbers
        canonical_nums = set()
        for m in canonical_props:
            num = m[0] or m[1]
            if num:
                canonical_nums.add(int(num))
        _log.info("COHERENCE: Found %d canonical propositions: %s", len(canonical_nums), sorted(canonical_nums))

        # 2. Renumber propositions in propositions.md to be sequential (P1, P2, P3, ...)
        fixes = 0
        if canonical_nums and canonical_nums != set(range(1, len(canonical_nums) + 1)):
            sorted_nums = sorted(canonical_nums)
            for new_num, old_num in enumerate(sorted_nums, 1):
                if new_num != old_num:
                    # Renumber in propositions section
                    prop_content = _gpc_re.sub(
                        rf'(Proposition\s+){old_num}(\s*[:.])' ,
                        rf'\g<1>{new_num}\2', prop_content,
                    )
                    prop_content = _gpc_re.sub(
                        rf'\bP{old_num}\b', f'P{new_num}', prop_content,
                    )
                    fixes += 1
            if fixes:
                prop_file.write_text(prop_content, encoding="utf-8")
                _log.info("COHERENCE: Renumbered %d propositions to sequential P1-%d", fixes, len(canonical_nums))

        # 3. Strip proposition DEFINITIONS from non-propositions sections
        # (Allow references like "as P1 suggests" but strip full definitions like "P1: <statement>")
        # Two patterns: line-start definitions AND inline definitions (mid-paragraph)
        _prop_def_line = _gpc_re.compile(
            r'\n\s*\*?\*?(?:Proposition\s+\d+|P\d+)\s*[:.].*?(?=\n\s*\*?\*?(?:Proposition\s+\d+|P\d+)\s*[:.])|\n\s*\*?\*?(?:Proposition\s+\d+|P\d+)\s*[:.][^\n]+',
            _gpc_re.IGNORECASE | _gpc_re.DOTALL,
        )
        # Inline: "**Proposition N: <text>**" mid-sentence (bold wrapped)
        _prop_def_inline = _gpc_re.compile(
            r'\*{1,2}(?:Proposition\s+\d+|P\d+)\s*[:.][^*\n]*\*{1,2}',
            _gpc_re.IGNORECASE,
        )
        for f in sections_dir.iterdir():
            if f.suffix == ".md" and f.stem not in ("propositions", "protocol", "synthesis_data", "writing_brief",
                                                     "advisory_report", "synthesis", "critic", "hypothesis", "brancher"):
                content = f.read_text(encoding="utf-8")
                # Count proposition definitions (not just references) — line-start OR inline
                definitions = _gpc_re.findall(
                    r'(?:^|\n|\*{1,2})\s*(?:Proposition\s+\d+|P\d+)\s*[:.]',
                    content, _gpc_re.IGNORECASE,
                )
                if len(definitions) > 0 and f.stem != "abstract":
                    # Strip proposition definitions, keep references
                    new_content = _prop_def_line.sub('', content)
                    new_content = _prop_def_inline.sub('', new_content)
                    if new_content != content:
                        f.write_text(new_content, encoding="utf-8")
                        fixes += 1
                        _log.info("COHERENCE: Stripped %d proposition definitions from %s", len(definitions), f.stem)

        return fixes

    def _global_table_dedup(self, on_event: StepCallback | None) -> int:
        """Programmatic: remove duplicate tables from non-canonical sections.

        Tables belong in theoretical_background (definitions/comparisons) and
        propositions (one summary table). Strip from framework, discussion, conclusion.
        Returns number of tables removed.
        """
        import re as _gtd_re
        ws = self.config.workspace
        sections_dir = ws / self.config.session_root_dir / "output" / "sections"
        if not sections_dir.exists():
            return 0

        # Sections where tables should be stripped entirely (except propositions summary)
        strip_sections = {"discussion", "conclusion"}
        # Sections where duplicate tables should be removed (check against theoretical_background)
        dedup_sections = {"framework"}

        # Load canonical tables from theoretical_background
        tb_file = sections_dir / "theoretical_background.md"
        canonical_table_words: list[set[str]] = []
        _STOP = {
            "the", "and", "of", "to", "in", "a", "is", "that", "for", "was", "on", "are", "with",
            "as", "this", "by", "from", "be", "have", "an", "has", "their", "been", "were", "|", "---",
        }
        if tb_file.exists():
            tb_content = tb_file.read_text(encoding="utf-8")
            for tbl in _gtd_re.findall(r'(\|[^\n]+\|\n(?:\|[^\n]+\|\n){2,})', tb_content):
                canonical_table_words.append(set(tbl.lower().split()) - _STOP)

        fixes = 0
        _table_block_pattern = _gtd_re.compile(r'(\|[^\n]+\|\n(?:\|[^\n]+\|\n){2,})')

        for f in sections_dir.iterdir():
            if f.suffix != ".md" or f.stem not in (strip_sections | dedup_sections | {"propositions"}):
                continue
            content = f.read_text(encoding="utf-8")
            tables = list(_table_block_pattern.finditer(content))
            if not tables:
                continue

            if f.stem in strip_sections:
                # Strip ALL tables from discussion/conclusion
                new_content = _table_block_pattern.sub('', content)
                if new_content != content:
                    f.write_text(new_content.strip() + "\n", encoding="utf-8")
                    fixes += len(tables)
                    _log.info("TABLE_DEDUP: Stripped %d tables from %s", len(tables), f.stem)

            elif f.stem in dedup_sections:
                # Remove tables that duplicate canonical tables
                removed = 0
                for tbl_match in reversed(tables):  # Reverse to preserve indices
                    tbl_words = set(tbl_match.group().lower().split()) - _STOP
                    for canon in canonical_table_words:
                        if tbl_words and canon:
                            overlap = len(tbl_words & canon) / min(len(tbl_words), len(canon))
                            if overlap > 0.30:
                                content = content[:tbl_match.start()] + content[tbl_match.end():]
                                removed += 1
                                break
                if removed:
                    f.write_text(content.strip() + "\n", encoding="utf-8")
                    fixes += removed
                    _log.info("TABLE_DEDUP: Removed %d duplicate tables from %s", removed, f.stem)

            elif f.stem == "propositions":
                # Propositions: keep only the LAST table (summary), strip all others
                if len(tables) > 1:
                    # Remove all tables except the last one
                    for tbl_match in reversed(tables[:-1]):
                        content = content[:tbl_match.start()] + content[tbl_match.end():]
                    f.write_text(content.strip() + "\n", encoding="utf-8")
                    fixes += len(tables) - 1
                    _log.info("TABLE_DEDUP: Stripped %d extra tables from propositions (kept summary)", len(tables) - 1)

        return fixes

    def _global_table_renumber(self, on_event: StepCallback | None) -> int:
        """Programmatic: assign sequential Table/Figure numbers across the entire paper.

        Scans sections in paper order, finds all table/figure definitions,
        and renumbers them sequentially (Table 1, Table 2, ...). Updates all
        references across all sections to match.
        Returns number of renumbering fixes applied.
        """
        import re as _rn_re
        ws = self.config.workspace
        sections_dir = ws / self.config.session_root_dir / "output" / "sections"
        if not sections_dir.exists():
            return 0

        # Paper section order (tables/figures should be numbered in this order)
        _ORDER = [
            "introduction", "theoretical_background", "methodology", "framework",
            "propositions", "discussion", "conclusion",
        ]

        # Load all section contents in order
        section_contents: dict[str, str] = {}
        for sec in _ORDER:
            f = sections_dir / f"{sec}.md"
            if f.exists():
                section_contents[sec] = f.read_text(encoding="utf-8")

        if not section_contents:
            return 0

        # Phase 1: Discover all table/figure definitions and their current numbers
        # Patterns: **Table N**, **Table N:**, **Table N.**, | Table N |
        table_def_pattern = _rn_re.compile(r'\*\*Table\s+(\d+)[.:*]')
        figure_def_pattern = _rn_re.compile(r'\*\*Figure\s+(\d+)[.:*]')

        # Build mapping: old_number -> new_number (in paper order)
        table_old_to_new: dict[str, str] = {}
        figure_old_to_new: dict[str, str] = {}
        table_counter = 1
        figure_counter = 1

        for sec in _ORDER:
            content = section_contents.get(sec, "")
            # Find table definitions in order of appearance
            for m in table_def_pattern.finditer(content):
                old_num = m.group(1)
                if old_num not in table_old_to_new:
                    table_old_to_new[old_num] = str(table_counter)
                    table_counter += 1
            for m in figure_def_pattern.finditer(content):
                old_num = m.group(1)
                if old_num not in figure_old_to_new:
                    figure_old_to_new[old_num] = str(figure_counter)
                    figure_counter += 1

        # Check if any renumbering is needed
        tables_need_fix = any(k != v for k, v in table_old_to_new.items())
        figures_need_fix = any(k != v for k, v in figure_old_to_new.items())
        # Also check for duplicate numbers (two tables with same number)
        all_table_nums = []
        for sec in _ORDER:
            content = section_contents.get(sec, "")
            all_table_nums.extend(table_def_pattern.findall(content))
        has_duplicates = len(all_table_nums) != len(set(all_table_nums))

        if not tables_need_fix and not figures_need_fix and not has_duplicates:
            return 0

        # If duplicates exist, rebuild mapping scanning all definitions in order
        if has_duplicates:
            table_old_to_new.clear()
            table_counter = 1
            for sec in _ORDER:
                content = section_contents.get(sec, "")
                for m in table_def_pattern.finditer(content):
                    # Use position-based key to handle duplicates
                    table_old_to_new[f"{sec}:{m.start()}:{m.group(1)}"] = str(table_counter)
                    table_counter += 1

            # For duplicates, do positional replacement per section
            fixes = 0
            for sec in _ORDER:
                content = section_contents.get(sec, "")
                new_content = content
                # Replace definitions in reverse order to preserve positions
                defs = list(table_def_pattern.finditer(content))
                offset = 0
                sec_table_counter = 0
                for m in defs:
                    key = f"{sec}:{m.start()}:{m.group(1)}"
                    new_num = table_old_to_new.get(key)
                    if new_num and m.group(1) != new_num:
                        # Replace this specific definition
                        old_text = m.group(0)
                        new_text = old_text.replace(f"Table {m.group(1)}", f"Table {new_num}")
                        start = m.start() + offset
                        end = m.end() + offset
                        new_content = new_content[:start] + new_text + new_content[end:]
                        offset += len(new_text) - len(old_text)
                        fixes += 1
                if new_content != content:
                    (sections_dir / f"{sec}.md").write_text(new_content, encoding="utf-8")
            _log.info("TABLE_RENUM: Fixed %d duplicate table numbers across sections", fixes)
            return fixes

        # Phase 2: Apply renumbering across ALL sections (including abstract for references)
        fixes = 0
        for sec in list(section_contents.keys()) + ["abstract"]:
            f = sections_dir / f"{sec}.md"
            if not f.exists():
                continue
            content = f.read_text(encoding="utf-8")
            new_content = content

            # Replace table numbers (definitions and references)
            # Sort by old number descending to avoid Table 1 → Table 2, then Table 12 → Table 22
            for old_num in sorted(table_old_to_new.keys(), key=int, reverse=True):
                new_num = table_old_to_new[old_num]
                if old_num != new_num:
                    # Use word boundary to avoid partial matches
                    new_content = _rn_re.sub(
                        rf'(Table\s+){old_num}\b',
                        rf'\g<1>{new_num}',
                        new_content,
                    )

            for old_num in sorted(figure_old_to_new.keys(), key=int, reverse=True):
                new_num = figure_old_to_new[old_num]
                if old_num != new_num:
                    new_content = _rn_re.sub(
                        rf'(Figure\s+){old_num}\b',
                        rf'\g<1>{new_num}',
                        new_content,
                    )

            if new_content != content:
                f.write_text(new_content, encoding="utf-8")
                fixes += 1

        if fixes:
            _log.info("TABLE_RENUM: Renumbered tables/figures across %d sections "
                      "(tables: %s, figures: %s)",
                      fixes,
                      {k: v for k, v in table_old_to_new.items() if k != v},
                      {k: v for k, v in figure_old_to_new.items() if k != v})
            if on_event:
                on_event(StepEvent("text", data=f"Renumbered tables/figures across {fixes} sections", depth=0))
        return fixes

    def _global_temporal_consistency(self, topic: str, on_event: StepCallback | None) -> int:
        """Programmatic: ensure temporal scope is consistent across all sections.

        Extracts date ranges from title/abstract/boundary conditions and flags
        contradictions. Auto-fixes where possible.
        Returns number of fixes applied.
        """
        import re as _tc_re
        ws = self.config.workspace
        sections_dir = ws / self.config.session_root_dir / "output" / "sections"
        if not sections_dir.exists():
            return 0

        # Extract canonical date range from topic (title)
        date_range_match = _tc_re.search(r'\((\d{4})[–\-](\d{4})\)', topic)
        if not date_range_match:
            # Try looser pattern
            date_range_match = _tc_re.search(r'(\d{4})[–\-](\d{4})', topic)
        if not date_range_match:
            return 0  # No date range in title — nothing to enforce

        canonical_start = date_range_match.group(1)
        canonical_end = date_range_match.group(2)
        canonical_range = f"{canonical_start}–{canonical_end}"
        _log.info("TEMPORAL: Canonical date range from title: %s", canonical_range)

        fixes = 0
        _ALL_SECTIONS = [
            "abstract", "introduction", "methodology", "theoretical_background",
            "framework", "propositions", "discussion", "conclusion",
        ]

        for sec in _ALL_SECTIONS:
            f = sections_dir / f"{sec}.md"
            if not f.exists():
                continue
            content = f.read_text(encoding="utf-8")

            # Find all date ranges in this section (YYYY–YYYY or YYYY-YYYY)
            ranges_found = _tc_re.findall(r'(\d{4})[–\-](\d{4})', content)
            new_content = content
            for start, end in ranges_found:
                # Check if this looks like it's trying to be the study's temporal scope
                # (not a citation date range like "2010-2015 data")
                # Heuristic: if start matches canonical start but end differs, it's a contradiction
                if start == canonical_start and end != canonical_end:
                    old_range = f"{start}–{end}"
                    old_range_dash = f"{start}-{end}"
                    new_content = new_content.replace(old_range, canonical_range)
                    new_content = new_content.replace(old_range_dash, canonical_range)
                    fixes += 1
                    _log.info("TEMPORAL: Fixed %s→%s in %s", f"{start}–{end}", canonical_range, sec)
                # Also check reversed case: end matches but start differs
                elif end == canonical_end and start != canonical_start:
                    old_range = f"{start}–{end}"
                    old_range_dash = f"{start}-{end}"
                    new_content = new_content.replace(old_range, canonical_range)
                    new_content = new_content.replace(old_range_dash, canonical_range)
                    fixes += 1
                    _log.info("TEMPORAL: Fixed %s→%s in %s", f"{start}–{end}", canonical_range, sec)

            if new_content != content:
                f.write_text(new_content, encoding="utf-8")

        if fixes:
            _log.info("TEMPORAL: Fixed %d date range inconsistencies (canonical: %s)", fixes, canonical_range)
            if on_event:
                on_event(StepEvent("text", data=f"Fixed {fixes} temporal scope inconsistencies → {canonical_range}", depth=0))
        return fixes

    def post_peer_review_gate(self, on_event: StepCallback | None = None) -> dict[str, Any]:
        """Programmatic quality gate that runs AFTER peer review Opus edits.

        Catches regressions introduced by the revision agent:
        - Word count bloat
        - Citation integrity violations
        - Proposition coherence breaks
        - Section overlap (embedding-based)
        - Reference quality (predatory DOI check)

        Returns dict of issues found and fixes applied.
        """
        from .tools.writing import _extract_citations_from_text, _verify_citation_against_db

        ws = self.config.workspace
        sections_dir = ws / self.config.session_root_dir / "output" / "sections"
        if not sections_dir.exists():
            return {"error": "no sections directory"}

        result: dict[str, Any] = {"issues": [], "fixes": 0}

        if on_event:
            on_event(StepEvent("subtask_start", data="Post-peer-review quality gate", depth=0))

        # 1. Run global proposition coherence
        prop_fixes = self._global_proposition_coherence(on_event)
        result["fixes"] += prop_fixes
        if prop_fixes:
            result["issues"].append(f"Proposition coherence: {prop_fixes} fixes applied")

        # 2. Run global table dedup
        table_fixes = self._global_table_dedup(on_event)
        result["fixes"] += table_fixes
        if table_fixes:
            result["issues"].append(f"Table dedup: {table_fixes} tables removed")

        # 3. Total word count check
        total_words = 0
        section_words: dict[str, int] = {}
        _paper_sections = {"abstract", "introduction", "methodology", "theoretical_background",
                           "framework", "propositions", "discussion", "conclusion",
                           "literature_review", "methods", "results"}
        for f in sections_dir.iterdir():
            if f.suffix == ".md" and f.stem in _paper_sections:
                wc = len(f.read_text(encoding="utf-8").split())
                total_words += wc
                section_words[f.stem] = wc

        _MAX_TOTAL = 12000
        if total_words > _MAX_TOTAL:
            overage = total_words - _MAX_TOTAL
            result["issues"].append(f"Word count: {total_words} words (limit {_MAX_TOTAL}, over by {overage})")
            _log.warning("POST-PEER-GATE: Total words %d exceeds %d by %d", total_words, _MAX_TOTAL, overage)

        # 4. Citation integrity scan — strip unverified citations programmatically
        import re as _ppg_re
        db = self.tools.db
        session_id = self.tools.session_id
        if db and session_id:
            total_verified = 0
            total_unverified = 0
            citations_stripped = 0
            for f in sections_dir.iterdir():
                if f.suffix == ".md" and f.stem in _paper_sections:
                    content = f.read_text(encoding="utf-8")
                    citations = _extract_citations_from_text(content)
                    unverified_in_section = []
                    for author, year in citations:
                        check = _verify_citation_against_db(author, year, db, session_id)
                        if check["verified"]:
                            total_verified += 1
                        else:
                            total_unverified += 1
                            unverified_in_section.append((author, year))
                    # Programmatic fix: strip unverified citations
                    if unverified_in_section:
                        new_content = content
                        for author, year in unverified_in_section:
                            esc_author = _ppg_re.escape(author)
                            esc_year = _ppg_re.escape(year)

                            # 1. Remove from multi-cite parenthetical: (; Author, Year) or (Author, Year;)
                            # Handles (Smith, 2020; Fake, 2019; Other, 2021) → (Smith, 2020; Other, 2021)
                            new_content = _ppg_re.sub(
                                rf';\s*{esc_author},?\s*{esc_year}', '', new_content,
                            )
                            new_content = _ppg_re.sub(
                                rf'{esc_author},?\s*{esc_year}\s*;\s*', '', new_content,
                            )

                            # 2. Remove standalone parenthetical: (Author, Year)
                            new_content = _ppg_re.sub(
                                rf'\s*\({esc_author},?\s*{esc_year}\)', '', new_content,
                            )

                            # 3. Remove narrative: Author (Year)
                            new_content = _ppg_re.sub(
                                rf'(?:\s+by\s+)?{esc_author}\s*\({esc_year}\)', '', new_content,
                            )

                        # Clean up artifacts: empty parens, double spaces, orphaned semicolons
                        new_content = _ppg_re.sub(r'\(\s*\)', '', new_content)  # ()
                        new_content = _ppg_re.sub(r'\(\s*;\s*', '(', new_content)  # (; ...
                        new_content = _ppg_re.sub(r'\s*;\s*\)', ')', new_content)  # ...; )
                        # Orphaned lead-in phrases left after narrative citation removal
                        new_content = _ppg_re.sub(r'(?:[Aa]ccording)\s+to\s*,\s*', '', new_content)
                        new_content = _ppg_re.sub(r'(?:[Aa]s\s+)?(?:noted|identified|suggested|argued|proposed|demonstrated)\s+by\s*,\s*', '', new_content)
                        new_content = _ppg_re.sub(r'\s{2,}', ' ', new_content)  # double spaces

                        if new_content != content:
                            stripped = len(content) - len(new_content)
                            f.write_text(new_content, encoding="utf-8")
                            citations_stripped += len(unverified_in_section)
                            _log.info("POST-PEER-GATE: Stripped %d unverified citations from %s (%d chars)",
                                       len(unverified_in_section), f.stem, stripped)
            integrity = total_verified / max(total_verified + total_unverified, 1)
            if total_unverified > 0:
                result["issues"].append(
                    f"Citation integrity: {total_unverified} unverified citations "
                    f"({integrity:.0%} verified) — stripped {citations_stripped} from sections"
                )
                result["fixes"] += citations_stripped

        # 5. Section overlap via embedding cosine similarity
        if hasattr(self, '_embed_client') and self._embed_client:
            try:
                body_sections = [s for s in _paper_sections if s not in ("abstract", "conclusion", "methodology")]
                section_embs: dict[str, list[float]] = {}
                for s in body_sections:
                    sf = sections_dir / f"{s}.md"
                    if sf.exists():
                        text = sf.read_text(encoding="utf-8")[:3000]
                        res = self._embed_client.models.embed_content(
                            model="gemini-embedding-001", contents=text,
                        )
                        if res.embeddings and len(res.embeddings) > 0:
                            section_embs[s] = res.embeddings[0].values
                names = list(section_embs.keys())
                import re as _olap_re
                for i in range(len(names)):
                    for j in range(i + 1, len(names)):
                        sim = self._cosine_sim(section_embs[names[i]], section_embs[names[j]])
                        if sim > self.config.max_section_overlap:
                            result["issues"].append(
                                f"Section overlap: '{names[i]}' and '{names[j]}' have {sim:.0%} cosine similarity"
                            )
                            # Programmatic dedup: remove shared n-grams from the later section
                            target = names[j]
                            target_file = sections_dir / f"{target}.md"
                            source_file = sections_dir / f"{names[i]}.md"
                            if target_file.exists() and source_file.exists():
                                source_text = source_file.read_text(encoding="utf-8")
                                target_text = target_file.read_text(encoding="utf-8")
                                # Build set of 5-grams from source section
                                source_words = source_text.lower().split()
                                source_ngrams = set()
                                for k in range(len(source_words) - 4):
                                    source_ngrams.add(" ".join(source_words[k:k+5]))
                                # Find sentences in target that share 5-grams with source
                                target_sentences = _olap_re.split(r'(?<=[.!?])\s+', target_text)
                                kept = []
                                removed = 0
                                for sent in target_sentences:
                                    sent_words = sent.lower().split()
                                    if len(sent_words) < 5:
                                        kept.append(sent)
                                        continue
                                    sent_ngrams = set()
                                    for k in range(len(sent_words) - 4):
                                        sent_ngrams.add(" ".join(sent_words[k:k+5]))
                                    overlap_ratio = len(sent_ngrams & source_ngrams) / max(len(sent_ngrams), 1)
                                    if overlap_ratio > 0.4:
                                        removed += 1
                                        _log.info("POST-PEER-GATE: Removing overlapping sentence from %s (%.0f%% 5-gram overlap with %s)",
                                                  target, overlap_ratio * 100, names[i])
                                    else:
                                        kept.append(sent)
                                if removed > 0:
                                    new_target = " ".join(kept)
                                    # Clean up double spaces
                                    new_target = _olap_re.sub(r'\s{2,}', ' ', new_target)
                                    target_file.write_text(new_target, encoding="utf-8")
                                    result["fixes"] += removed
                                    _log.info("POST-PEER-GATE: Removed %d overlapping sentences from %s", removed, target)
            except Exception as exc:
                _log.warning("POST-PEER-GATE: Embedding overlap check failed: %s", exc)

        # 6. Reference quality — strip predatory DOIs from bib file
        from .tools.writing import PREDATORY_DOI_PREFIXES
        ref_file = ws / self.config.session_root_dir / "output" / "references.bib"
        if ref_file.exists():
            bib_content = ref_file.read_text(encoding="utf-8")
            # Split into individual entries and filter
            import re as _bib_re
            entries = _bib_re.split(r'(?=@\w+\{)', bib_content)
            clean_entries = []
            predatory_removed = 0
            for entry in entries:
                entry = entry.strip()
                if not entry:
                    continue
                is_predatory = False
                for prefix in PREDATORY_DOI_PREFIXES:
                    if prefix.lower() in entry.lower():
                        is_predatory = True
                        predatory_removed += 1
                        _log.info("POST-PEER-GATE: Removed predatory DOI entry: %s", entry[:80])
                        break
                if not is_predatory:
                    clean_entries.append(entry)
            if predatory_removed > 0:
                ref_file.write_text("\n".join(clean_entries), encoding="utf-8")
                result["issues"].append(f"Reference quality: stripped {predatory_removed} predatory DOI entries from references.bib")
                result["fixes"] += predatory_removed
            # Also clean APA file
            apa_file = ws / self.config.session_root_dir / "output" / "references_apa.txt"
            if apa_file.exists():
                apa_content = apa_file.read_text(encoding="utf-8")
                apa_lines = apa_content.split("\n\n")
                clean_apa = []
                for line in apa_lines:
                    is_pred = any(prefix.lower() in line.lower() for prefix in PREDATORY_DOI_PREFIXES)
                    if not is_pred:
                        clean_apa.append(line)
                if len(clean_apa) < len(apa_lines):
                    apa_file.write_text("\n\n".join(clean_apa), encoding="utf-8")

        # 7. Duplicate references — remove exact and near-duplicate entries from APA file
        apa_file = ws / self.config.session_root_dir / "output" / "references_apa.txt"
        if apa_file.exists():
            import re as _dup_re
            apa_content = apa_file.read_text(encoding="utf-8")
            apa_entries = [e.strip() for e in apa_content.split("\n\n") if e.strip()]
            # Deduplicate by (first_author_surname, year, first_20_chars_of_title)
            seen: dict[str, str] = {}
            deduped: list[str] = []
            dup_count = 0
            for entry in apa_entries:
                m = _dup_re.match(r'^([^(]+?)\s*\((\d{4})\)\.\s*(.+)', entry)
                if m:
                    surname = m.group(1).split(",")[0].strip().lower()
                    year = m.group(2)
                    title_frag = _dup_re.sub(r'[^a-z0-9]', '', m.group(3)[:40].lower())
                    key = f"{surname}:{year}:{title_frag}"
                    if key in seen:
                        dup_count += 1
                        _log.info("POST-PEER-GATE: Removing duplicate reference: %s", entry[:80])
                        continue
                    seen[key] = entry
                deduped.append(entry)
            if dup_count > 0:
                apa_file.write_text("\n\n".join(deduped), encoding="utf-8")
                result["issues"].append(f"Duplicate references: removed {dup_count} duplicates")
                result["fixes"] += dup_count

        # 8. Sort references alphabetically (APA requirement)
        if apa_file.exists():
            apa_content = apa_file.read_text(encoding="utf-8")
            apa_entries = [e.strip() for e in apa_content.split("\n\n") if e.strip()]
            sorted_entries = sorted(apa_entries, key=lambda e: e.lower())
            if apa_entries != sorted_entries:
                apa_file.write_text("\n\n".join(sorted_entries), encoding="utf-8")
                _log.info("POST-PEER-GATE: Sorted reference list alphabetically")

        # 9. Same-author-same-year disambiguation (Smith, 2020a; Smith, 2020b)
        if apa_file.exists():
            import re as _disamb_re
            apa_content = apa_file.read_text(encoding="utf-8")
            apa_entries = [e.strip() for e in apa_content.split("\n\n") if e.strip()]
            # Group by (first_author_surname, year)
            author_year_groups: dict[str, list[int]] = {}
            for idx, entry in enumerate(apa_entries):
                m = _disamb_re.match(r'^([^(]+?)\s*\((\d{4})\)', entry)
                if m:
                    surname = m.group(1).split(",")[0].strip().lower()
                    year = m.group(2)
                    key = f"{surname}:{year}"
                    author_year_groups.setdefault(key, []).append(idx)
            disambiguated = 0
            for key, indices in author_year_groups.items():
                if len(indices) < 2:
                    continue
                # Need disambiguation: add a, b, c suffix
                for i, idx in enumerate(indices):
                    entry = apa_entries[idx]
                    suffix = chr(ord('a') + i)
                    m = _disamb_re.match(r'^([^(]+?)\s*\((\d{4})\)', entry)
                    if m:
                        old_year = f"({m.group(2)})"
                        new_year = f"({m.group(2)}{suffix})"
                        apa_entries[idx] = entry.replace(old_year, new_year, 1)
                        disambiguated += 1
            if disambiguated > 0:
                apa_file.write_text("\n\n".join(apa_entries), encoding="utf-8")
                result["issues"].append(f"Same-author-same-year: disambiguated {disambiguated} entries with a/b/c suffixes")
                result["fixes"] += disambiguated
                # Also update in-text citations to match
                for f in sections_dir.iterdir():
                    if f.suffix == ".md" and f.stem in _paper_sections:
                        content = f.read_text(encoding="utf-8")
                        original = content
                        for key, indices in author_year_groups.items():
                            if len(indices) < 2:
                                continue
                            surname_raw, year = key.split(":")
                            # For now just log — in-text disambiguation requires knowing which
                            # citation maps to which paper, which needs title matching
                            _log.info("POST-PEER-GATE: TODO — in-text disambiguation needed for %s (%s)", surname_raw, year)

        # 10. Em-dash / en-dash stripping — replace with commas across all sections
        import re as _dash_re
        dash_fixes = 0
        for f in sections_dir.iterdir():
            if f.suffix == ".md" and f.stem in _paper_sections:
                content = f.read_text(encoding="utf-8")
                new_content = _dash_re.sub(r'\s*[\u2014\u2013]\s*', ', ', content)
                new_content = _dash_re.sub(r',\s*,', ',', new_content)
                new_content = _dash_re.sub(r',\s*\.', '.', new_content)
                if new_content != content:
                    f.write_text(new_content, encoding="utf-8")
                    dash_fixes += 1
        if dash_fixes:
            result["issues"].append(f"Em/en-dash: replaced dashes with commas in {dash_fixes} sections")
            result["fixes"] += dash_fixes

        _log.info("POST-PEER-GATE: %d issues found, %d fixes applied", len(result["issues"]), result["fixes"])
        if on_event:
            on_event(StepEvent("text", data=f"Post-peer-review gate: {len(result['issues'])} issues, {result['fixes']} fixes", depth=0))
            on_event(StepEvent("subtask_end", data="Post-peer-review quality gate complete", depth=0))

        return result

    def _generate_references_from_db(
        self,
        in_text_citations: set[tuple[str, str]],
        sections_dir: Path,
        apa_file: Path,
        bib_file: Path,
        db: Any,
        session_id: int | None,
    ) -> None:
        """Build full APA + BibTeX references directly from DB paper metadata.

        This is the last-resort reference generator — produces real bibliographic
        entries from the title, authors, year, and DOI stored in the papers table.
        """
        if not db or not session_id:
            return
        from .tools.writing import _normalize_author
        import re as _ref_re

        # Get all papers from DB
        rows = db._conn.execute(
            "SELECT paper_id, title, authors, year, doi, journal_name FROM papers "
            "WHERE session_id = ? ORDER BY citation_count DESC",
            (session_id,),
        ).fetchall()
        if not rows:
            return

        all_papers = []
        for r in rows:
            p = dict(r)
            try:
                p["authors"] = json.loads(p.get("authors") or "[]")
            except (json.JSONDecodeError, TypeError):
                p["authors"] = []
            all_papers.append(p)

        # Match in-text citations to DB papers
        matched_papers: list[dict] = []
        matched_ids: set[int] = set()
        for author_frag, cite_year in in_text_citations:
            cite_tokens = _normalize_author(author_frag)
            for p in all_papers:
                if p["paper_id"] in matched_ids:
                    continue
                year = str(p.get("year", ""))
                if not (year.isdigit() and cite_year.isdigit() and abs(int(cite_year) - int(year)) <= 1):
                    continue
                authors_list = p.get("authors", [])
                for a in authors_list:
                    a_str = a if isinstance(a, str) else (a.get("name", "") or a.get("family", ""))
                    a_surname = a_str.strip().split()[-1].lower() if a_str.strip() else ""
                    paper_tokens = _normalize_author(a_str)
                    if any(ct == a_surname or ct in paper_tokens for ct in cite_tokens if len(ct) > 2):
                        matched_papers.append(p)
                        matched_ids.add(p["paper_id"])
                        break
                if p["paper_id"] in matched_ids:
                    break

        if not matched_papers:
            _log.warning("PRE-CRITIC: No DB papers matched in-text citations — cannot generate references")
            return

        # Generate APA 7th edition entries
        apa_entries = []
        bib_entries = []
        for p in sorted(matched_papers, key=lambda x: (
            (x["authors"][0] if isinstance(x["authors"][0], str) else x["authors"][0].get("name", ""))
            if x.get("authors") else "",
            x.get("year", 0),
        )):
            authors_list = p.get("authors", [])
            year = p.get("year", "n.d.")
            title = p.get("title", "Untitled")
            doi = p.get("doi", "")

            # APA author formatting
            apa_authors = []
            for i, a in enumerate(authors_list[:20]):
                name = a if isinstance(a, str) else (a.get("name", "") or a.get("family", ""))
                name = name.strip()
                if not name:
                    continue
                parts = name.split()
                # Skip corrupted entries (concatenated names from bad API parse)
                if len(parts) > 5:
                    continue
                if len(parts) >= 2:
                    surname = parts[-1]
                    initials = " ".join(f"{p[0]}." for p in parts[:-1] if p)
                    apa_authors.append(f"{surname}, {initials}")
                else:
                    apa_authors.append(name)

            if not apa_authors:
                continue

            if len(apa_authors) == 1:
                author_str = apa_authors[0]
            elif len(apa_authors) == 2:
                author_str = f"{apa_authors[0]}, & {apa_authors[1]}"
            elif len(apa_authors) <= 20:
                author_str = ", ".join(apa_authors[:-1]) + f", & {apa_authors[-1]}"
            else:
                author_str = ", ".join(apa_authors[:19]) + f", ... {apa_authors[-1]}"

            if doi:
                doi_clean = doi if doi.startswith("http") else f"https://doi.org/{doi}"
            else:
                doi_clean = ""
            doi_str = f" {doi_clean}" if doi_clean else ""
            journal_name = p.get("journal_name", "")
            journal_str = f" *{journal_name}*." if journal_name else ""
            apa_entries.append(f"{author_str} ({year}). {title}.{journal_str}{doi_str}")

            # BibTeX entry
            bib_authors = " and ".join(
                (a if isinstance(a, str) else a.get("name", "")).strip()
                for a in authors_list[:5]
                if (a if isinstance(a, str) else a.get("name", "")).strip()
            )
            key = f"paper_{p.get('paper_id', 0)}"
            bib = f"@article{{{key},\n"
            bib += f"  author = {{{bib_authors}}},\n"
            bib += f"  title = {{{title}}},\n"
            bib += f"  year = {{{year}}},\n"
            if journal_name:
                bib += f"  journal = {{{journal_name}}},\n"
            if doi:
                bib += f"  doi = {{{doi}}},\n"
            bib += "}\n"
            bib_entries.append(bib)

        if apa_entries:
            apa_file.parent.mkdir(parents=True, exist_ok=True)
            apa_file.write_text("\n\n".join(apa_entries), encoding="utf-8")
            bib_file.write_text("\n".join(bib_entries), encoding="utf-8")
            _log.info("PRE-CRITIC: Generated %d APA references + BibTeX from DB metadata", len(apa_entries))

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
            updates: list[str] = []
            params: list[Any] = []

            # 1) If paper exists in central DB at all, the DOI was valid enough to index it
            cp = central_db.get_paper_by_doi(doi)
            if cp:
                updates.append("doi_valid = 1")

            # 2) Check doi_validations for retraction + citation data
            cached = central_db.get_doi_validation(doi)
            if cached:
                updates.append("doi_valid = 1")
                # retraction_permanent=1 means the check result won't change
                if cached.get("retraction_permanent") or cached.get("retracted") is not None:
                    updates.append("retraction_checked = 1")
                cc = cached.get("citation_count", 0)
                if cc is not None and cc >= 0:
                    updates.append("citation_verified = 1")
                    updates.append("citation_count = ?")
                    params.append(cc)

            if updates:
                # Deduplicate (doi_valid may appear twice)
                seen: set[str] = set()
                deduped: list[str] = []
                deduped_params: list[Any] = []
                param_idx = 0
                for u in updates:
                    key = u.split("=")[0].strip()
                    if key in seen:
                        if "?" in u:
                            param_idx += 1
                        continue
                    seen.add(key)
                    deduped.append(u)
                    if "?" in u:
                        deduped_params.append(params[param_idx])
                        param_idx += 1

                deduped_params.append(row["paper_id"])
                db._conn.execute(
                    f"UPDATE papers SET {', '.join(deduped)} WHERE paper_id = ?",
                    deduped_params,
                )
                flagged += 1

        if flagged:
            db._conn.commit()
            _log.info("PIPELINE: Pre-populated verification flags for %d/%d papers from central DB", flagged, len(rows))

    def _sync_verification_to_central(self, central_db: Any, db: Any, session_id: int) -> None:
        """Bulk sync verified papers from session DB → central DB doi_validations.
        Ensures future runs never re-verify the same DOIs."""
        rows = db._conn.execute(
            "SELECT doi, citation_count FROM papers "
            "WHERE session_id = ? AND doi IS NOT NULL AND doi != '' "
            "AND (doi_valid = 1 OR retraction_checked = 1 OR citation_verified = 1)",
            (session_id,),
        ).fetchall()
        if not rows:
            return

        synced = 0
        for row in rows:
            doi = row["doi"].strip().lower()
            if not doi:
                continue
            try:
                existing = central_db.get_doi_validation(doi)
                if not existing:
                    central_db.store_doi_validation(
                        doi, retracted=False,
                        citation_count=row["citation_count"] or 0,
                    )
                    synced += 1
            except Exception:
                pass

        if synced:
            _log.info("PIPELINE VERIFY: Synced %d new doi_validations to central DB", synced)

    def _inject_top_tier_papers(
        self, central_db: Any, db: Any, session_id: int, topic: str,
        on_event: StepCallback | None,
    ) -> None:
        """Inject relevant AAA/AA papers from central DB into session for citation quality."""
        try:
            # Count current top-tier papers in session
            current_aaa = db._conn.execute(
                "SELECT COUNT(*) FROM papers WHERE session_id = ? AND journal_tier = 'AAA'",
                (session_id,),
            ).fetchone()[0]
            current_aa = db._conn.execute(
                "SELECT COUNT(*) FROM papers WHERE session_id = ? AND journal_tier = 'AA'",
                (session_id,),
            ).fetchone()[0]
            total_selected = db._conn.execute(
                "SELECT COUNT(*) FROM papers WHERE session_id = ? AND selected_for_deep_read = 1",
                (session_id,),
            ).fetchone()[0]

            _log.info("TOP-TIER INJECT: Current session has %d AAA + %d AA papers (%d selected)",
                      current_aaa, current_aa, total_selected)

            # Target: enough top-tier to reach 50% of citations
            # If we have 30 selected papers and need 50% AAA/AA, we need ~15 top-tier
            target_top_tier = max(10, int(total_selected * 0.5)) if total_selected > 0 else 15
            needed = target_top_tier - (current_aaa + current_aa)
            if needed <= 0:
                _log.info("TOP-TIER INJECT: Already have enough top-tier papers (%d/%d)",
                          current_aaa + current_aa, target_top_tier)
                return

            # Get topic embedding for relevance search
            _topic_emb = getattr(self, "_topic_emb", None)
            if not _topic_emb:
                # Generate topic embedding
                import os
                api_key = os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY")
                if not api_key:
                    return
                from google import genai
                client = genai.Client(api_key=api_key)
                emb_result = client.models.embed_content(
                    model="gemini-embedding-001", contents=topic[:500],
                )
                if not emb_result.embeddings:
                    return
                _topic_emb = emb_result.embeddings[0].values

            # Search central DB for relevant top-tier papers
            top_tier_rows = central_db._conn.execute(
                "SELECT paper_id, title, abstract, authors, year, doi, journal_name, journal_tier, "
                "citation_count, embedding FROM papers "
                "WHERE journal_tier IN ('AAA', 'AA') AND embedding IS NOT NULL "
                "ORDER BY citation_count DESC"
            ).fetchall()

            if not top_tier_rows:
                _log.info("TOP-TIER INJECT: No top-tier papers with embeddings in central DB")
                return

            # Score by cosine similarity to topic
            import numpy as np
            query_vec = np.array(_topic_emb, dtype=np.float32)
            query_norm = np.linalg.norm(query_vec)
            if query_norm == 0:
                return
            query_vec = query_vec / query_norm

            scored = []
            for r in top_tier_rows:
                try:
                    emb = json.loads(r["embedding"])
                    emb_vec = np.array(emb, dtype=np.float32)
                    emb_norm = np.linalg.norm(emb_vec)
                    if emb_norm == 0:
                        continue
                    cos_sim = float(np.dot(query_vec, emb_vec / emb_norm))
                    if cos_sim >= 0.35:  # Relevance threshold
                        scored.append((cos_sim, dict(r)))
                except Exception:
                    continue

            scored.sort(key=lambda x: -x[0])
            _log.info("TOP-TIER INJECT: %d relevant top-tier papers found (threshold 0.35)",
                      len(scored))

            # Get existing titles to avoid duplicates
            existing_titles = {r[0].lower() for r in db._conn.execute(
                "SELECT title FROM papers WHERE session_id = ?", (session_id,),
            ).fetchall()}

            # Inject top papers into session
            injected = 0
            for cos_sim, paper in scored[:needed * 2]:  # Fetch extra to account for dupes
                if injected >= needed:
                    break
                title = paper.get("title", "")
                if title.lower() in existing_titles:
                    continue

                from .db import classify_journal
                j_name, j_tier = classify_journal(paper.get("doi"))
                if not j_tier:
                    j_tier = paper.get("journal_tier")
                    j_name = paper.get("journal_name")

                db.store_papers(session_id, [{
                    "title": title,
                    "abstract": paper.get("abstract", ""),
                    "authors": paper.get("authors", "[]"),
                    "year": paper.get("year"),
                    "doi": paper.get("doi", ""),
                    "source": f"central_db_{j_tier}",
                    "url": "",
                    "citation_count": paper.get("citation_count", 0),
                }])
                # Auto-select for deep read (these are high-quality papers)
                db._conn.execute(
                    "UPDATE papers SET selected_for_deep_read = 1, journal_tier = ?, journal_name = ? "
                    "WHERE session_id = ? AND title = ?",
                    (j_tier, j_name, session_id, title),
                )
                existing_titles.add(title.lower())
                injected += 1

            if injected > 0:
                db._conn.commit()
                _log.info("TOP-TIER INJECT: Injected %d top-tier papers into session "
                          "(now %d AAA + %d AA + %d injected)",
                          injected, current_aaa, current_aa, injected)
                if on_event:
                    on_event(StepEvent("text",
                             data=f"Injected {injected} top-tier (AAA/AA) papers from central DB",
                             depth=0))

        except Exception as exc:
            _log.warning("TOP-TIER INJECT: Failed: %s", exc)

    def _preload_claims_from_central(self, central_db: Any, db: Any, session_id: int, topic: str = "") -> None:
        """Pre-load claims from central DB into session DB.

        Two strategies:
        1. DIRECT: Paper already fully extracted in central DB → load ALL its claims (topic-agnostic reuse)
        2. COSINE: No direct match → search central claims by topic embedding similarity
        """
        papers = db._conn.execute(
            "SELECT paper_id, doi, title FROM papers WHERE session_id = ? AND selected_for_deep_read = 1",
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

        loaded_direct = 0
        for paper in papers:
            if paper["paper_id"] in papers_with_claims:
                continue

            # Strategy 1: Direct match — paper was fully extracted before
            title = paper["title"] or ""
            doi = (paper["doi"] or "").strip().lower()

            central_claims = []
            if doi:
                central_claims = central_db.get_claims_for_paper(doi)
            if not central_claims and title:
                central_claims = central_db.get_claims_for_paper_by_title(title)

            if central_claims:
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
                loaded_direct += 1

        if loaded_direct:
            _log.info("PIPELINE: Pre-loaded claims for %d papers from central DB (direct match — skipping LLM deep read)", loaded_direct)

        # Strategy 2: Cosine similarity search — find relevant claims across ALL papers in central DB
        # This catches claims from papers not in this session but relevant to the topic
        try:
            if topic and central_db.claims_with_embeddings_count() > 0:
                api_key = os.getenv("GOOGLE_API_KEY") or (self.config.google_api_key if self.config else None)
                if api_key:
                    from google import genai
                    client = genai.Client(api_key=api_key)
                    result = client.models.embed_content(model="gemini-embedding-001", contents=topic)
                    if result.embeddings and len(result.embeddings) > 0:
                        topic_emb = result.embeddings[0].values
                        cosine_claims = central_db.search_claims_by_cosine(topic_emb, limit=100, min_cosine=0.55)
                        if cosine_claims:
                            _log.info("PIPELINE: Cosine search found %d relevant claims in central DB for topic '%s'",
                                      len(cosine_claims), topic[:60])
                            # Store cosine-matched claims into session DB (attach to best-matching paper)
                            loaded_cosine = 0
                            for c in cosine_claims:
                                # Find if we have the same paper in session
                                c_title = c.get("paper_title", "")
                                if not c_title:
                                    continue
                                match_row = db._conn.execute(
                                    "SELECT paper_id FROM papers WHERE session_id = ? AND title = ?",
                                    (session_id, c_title),
                                ).fetchone()
                                if not match_row:
                                    continue
                                pid = match_row["paper_id"]
                                if pid in papers_with_claims:
                                    continue
                                # Check if this exact claim already exists
                                existing = db._conn.execute(
                                    "SELECT claim_id FROM claims WHERE session_id = ? AND paper_id = ? AND claim_text = ?",
                                    (session_id, pid, c.get("claim_text", "")),
                                ).fetchone()
                                if existing:
                                    continue
                                db.store_claim(
                                    session_id=session_id, paper_id=pid,
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
                                loaded_cosine += 1
                            if loaded_cosine:
                                _log.info("PIPELINE: Pre-loaded %d claims via cosine similarity from central DB", loaded_cosine)
        except Exception as exc:
            _log.debug("PIPELINE: Cosine claim pre-loading failed: %s", exc)

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
        """Generate reference list, PRISMA diagram, and reconcile citations bidirectionally."""
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

        # Bidirectional reconciliation: text citations ↔ reference list
        self._reconcile_citations_and_references(context)

    def _reconcile_citations_and_references(self, context: ExternalContext) -> None:
        """Bidirectional reconciliation: every in-text citation gets a reference, every reference is cited."""
        from .tools.writing import _extract_citations_from_text, _normalize_author
        ws = self.config.workspace
        sections_dir = ws / self.config.session_root_dir / "output" / "sections"
        apa_file = ws / self.config.session_root_dir / "output" / "references_apa.txt"

        if not sections_dir.exists() or not apa_file.exists():
            _log.warning("RECONCILE: Missing sections or references file — skipping")
            return

        # 1. Collect ALL in-text citations from sections
        in_text: set[tuple[str, str]] = set()
        for f in sections_dir.iterdir():
            if f.suffix == ".md" and f.is_file() and f.stem not in ("writing_brief", "synthesis_data", "protocol"):
                in_text.update(_extract_citations_from_text(f.read_text(encoding="utf-8")))

        # 2. Parse reference list — extract (author_fragment, year) from each APA entry
        import re as _rre
        apa_text = apa_file.read_text(encoding="utf-8")
        ref_entries = [e.strip() for e in apa_text.split("\n\n") if e.strip()]
        ref_citations: list[tuple[str, str, str]] = []  # (surname, year, full_entry)
        for entry in ref_entries:
            # APA format: "Author, A. B. (Year). Title..."
            m = _rre.match(r'^([^(]+?)\s*\((\d{4})\)', entry)
            if m:
                author_part = m.group(1).strip().rstrip(",").rstrip("&").strip()
                # Get first author surname
                surname = author_part.split(",")[0].strip().split()[-1] if author_part else ""
                ref_citations.append((surname.lower(), m.group(2), entry))

        # 3. Forward check: in-text citations → reference list
        #    Find citations in text that have NO matching reference entry
        dangling_in_text: list[tuple[str, str]] = []
        for author_frag, year in in_text:
            cite_tokens = _normalize_author(author_frag)
            matched = False
            for ref_surname, ref_year, _ in ref_citations:
                if ref_year != year and not (ref_year.isdigit() and year.isdigit() and abs(int(ref_year) - int(year)) <= 1):
                    continue
                if any(ct == ref_surname or ref_surname.startswith(ct) or ct.startswith(ref_surname) for ct in cite_tokens if len(ct) > 2):
                    matched = True
                    break
            if not matched:
                dangling_in_text.append((author_frag, year))

        # 4. Reverse check: reference list → in-text citations
        #    Find references that are NEVER cited in the text
        uncited_refs: list[str] = []
        for ref_surname, ref_year, full_entry in ref_citations:
            cited = False
            for author_frag, year in in_text:
                if year != ref_year and not (ref_year.isdigit() and year.isdigit() and abs(int(ref_year) - int(year)) <= 1):
                    continue
                cite_tokens = _normalize_author(author_frag)
                if any(ct == ref_surname or ref_surname.startswith(ct) or ct.startswith(ref_surname) for ct in cite_tokens if len(ct) > 2):
                    cited = True
                    break
            if not cited:
                uncited_refs.append(full_entry)

        # 5. Report and fix
        _log.info("RECONCILE: %d in-text citations, %d reference entries", len(in_text), len(ref_citations))

        if dangling_in_text:
            _log.warning("RECONCILE: %d in-text citations have NO reference entry: %s",
                         len(dangling_in_text),
                         ", ".join(f"({a}, {y})" for a, y in dangling_in_text[:15]))
            # Try to find these papers in DB and add them to references
            db = self.tools.db
            session_id = self.tools.session_id
            if db and session_id:
                added = 0
                for author_frag, year in dangling_in_text:
                    from .tools.writing import _verify_citation_against_db
                    match = _verify_citation_against_db(author_frag, year, db, session_id)
                    if match.get("verified") and match.get("paper_id"):
                        p = db.get_paper(match["paper_id"])
                        if not p:
                            continue
                        try:
                            p["authors"] = json.loads(p.get("authors") or "[]")
                        except (json.JSONDecodeError, TypeError):
                            p["authors"] = []
                        authors_list = p.get("authors", [])
                        # Build proper APA 7th edition author format: Surname, I.
                        apa_authors = []
                        for a in authors_list[:20]:
                            name = a if isinstance(a, str) else (a.get("name", "") or a.get("family", ""))
                            name = name.strip()
                            if not name or len(name.split()) > 5:
                                continue
                            parts = name.split()
                            if len(parts) >= 2:
                                surname = parts[-1]
                                initials = " ".join(f"{p[0]}." for p in parts[:-1] if p)
                                apa_authors.append(f"{surname}, {initials}")
                            else:
                                apa_authors.append(name)
                        if not apa_authors:
                            continue
                        if len(apa_authors) == 1:
                            author_str = apa_authors[0]
                        elif len(apa_authors) == 2:
                            author_str = f"{apa_authors[0]}, & {apa_authors[1]}"
                        elif len(apa_authors) <= 20:
                            author_str = ", ".join(apa_authors[:-1]) + f", & {apa_authors[-1]}"
                        else:
                            author_str = ", ".join(apa_authors[:19]) + f", ... {apa_authors[-1]}"
                        doi = p.get("doi", "")
                        if doi:
                            doi_str = doi if doi.startswith("http") else f"https://doi.org/{doi}"
                            doi_part = f" {doi_str}"
                        else:
                            doi_part = ""
                        j_name = p.get("journal_name", "")
                        journal_part = f" *{j_name}*." if j_name else ""
                        new_entry = f"{author_str} ({p.get('year', 'n.d.')}). {p.get('title', 'Untitled')}.{journal_part}{doi_part}"
                        apa_text += f"\n\n{new_entry}"
                        added += 1
                if added:
                    # Re-sort and save
                    entries = [e.strip() for e in apa_text.split("\n\n") if e.strip()]
                    apa_file.write_text("\n\n".join(sorted(entries)), encoding="utf-8")
                    _log.info("RECONCILE: Added %d missing references from DB (proper APA format)", added)

        if uncited_refs:
            _log.warning("RECONCILE: %d references never cited in text — removing", len(uncited_refs))
            # Remove uncited references from the file
            remaining = [e for e in ref_entries if e not in uncited_refs]
            apa_file.write_text("\n\n".join(sorted(remaining)), encoding="utf-8")
            _log.info("RECONCILE: Removed %d phantom references, %d remain", len(uncited_refs), len(remaining))

            # Also clean BibTeX
            bib_file = ws / self.config.session_root_dir / "output" / "references.bib"
            if bib_file.exists():
                bib_text = bib_file.read_text(encoding="utf-8")
                # For each uncited ref, try to remove its BibTeX entry
                for entry in uncited_refs:
                    m = _rre.match(r'^([^(]+?)\s*\((\d{4})\)', entry)
                    if m:
                        # Find and remove the corresponding @article block
                        surname = m.group(1).split(",")[0].strip()
                        # Remove @article{...} block where author contains this surname
                        bib_text = _rre.sub(
                            rf'@article\{{[^}}]*?author\s*=\s*\{{[^}}]*?{_rre.escape(surname)}[^}}]*?\}}[^@]*?\}}\s*\n?',
                            '', bib_text, flags=_rre.DOTALL
                        )
                bib_file.write_text(bib_text.strip() + "\n", encoding="utf-8")

        # 6. Hallucination scrubber — strip in-text citations that have NO reference after all resolution attempts
        #    Re-check dangling citations against the (now updated) reference list
        if dangling_in_text:
            # Reload reference list after DB additions
            updated_apa = apa_file.read_text(encoding="utf-8")
            updated_refs: list[tuple[str, str]] = []
            for m in _rre.finditer(r'^([^(]+?)\s*\((\d{4})\)', updated_apa, _rre.MULTILINE):
                surname = m.group(1).split(",")[0].strip().split()[-1].lower() if m.group(1).strip() else ""
                updated_refs.append((surname, m.group(2)))

            still_dangling: list[tuple[str, str]] = []
            for author_frag, year in dangling_in_text:
                cite_tokens = _normalize_author(author_frag)
                resolved = False
                for ref_surname, ref_year in updated_refs:
                    if ref_year != year and not (ref_year.isdigit() and year.isdigit() and abs(int(ref_year) - int(year)) <= 1):
                        continue
                    if any(ct == ref_surname or ref_surname.startswith(ct) or ct.startswith(ref_surname) for ct in cite_tokens if len(ct) > 2):
                        resolved = True
                        break
                if not resolved:
                    still_dangling.append((author_frag, year))

            if still_dangling:
                _log.warning("RECONCILE HALLUCINATION: %d citations unresolvable — stripping from text: %s",
                             len(still_dangling),
                             ", ".join(f"({a}, {y})" for a, y in still_dangling[:15]))
                # Strip these citations from section files
                stripped_total = 0
                for f in sections_dir.iterdir():
                    if f.suffix == ".md" and f.is_file() and f.stem not in ("writing_brief", "synthesis_data", "protocol"):
                        content = f.read_text(encoding="utf-8")
                        original = content
                        for author_frag, year in still_dangling:
                            # Remove parenthetical: (Author, Year) or (Author & Other, Year)
                            content = _rre.sub(
                                rf'\(\s*{_rre.escape(author_frag)}(?:\s*(?:,|;)\s*{year}|,?\s*{year})\s*\)',
                                '', content
                            )
                            # Remove narrative: Author (Year) or Author et al. (Year)
                            content = _rre.sub(
                                rf'{_rre.escape(author_frag)}\s*\({year}\)',
                                '', content
                            )
                        # Clean up artifacts: double spaces, orphaned semicolons in citation groups
                        content = _rre.sub(r'\(\s*;\s*', '(', content)
                        content = _rre.sub(r';\s*\)', ')', content)
                        content = _rre.sub(r'\(\s*\)', '', content)
                        content = _rre.sub(r'  +', ' ', content)
                        if content != original:
                            f.write_text(content, encoding="utf-8")
                            stripped_total += 1
                _log.info("RECONCILE HALLUCINATION: Stripped unverified citations from %d section files", stripped_total)

        if not dangling_in_text and not uncited_refs:
            _log.info("RECONCILE: Perfect match — all in-text citations have references and vice versa")

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
            # Use primary model (not round-robin) for deep_read — round-robin breaks
            # conversational continuity as each step rotates to a different model
            active_model = self.model
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
                    _papers_done = _db._conn.execute(
                        "SELECT COUNT(DISTINCT paper_id) FROM claims WHERE session_id = ?", (_sid,)
                    ).fetchone()[0]
                    _MIN_PAPERS_HARD = 30
                    if _cc >= self.config.min_claims and _papers_done >= _MIN_PAPERS_HARD:
                        _log.info("DEEP_READ: Targets reached (%d claims from %d papers) — stopping at step %d",
                                  _cc, _papers_done, steps)
                        if on_event:
                            on_event(StepEvent("text", data=f"Claim target reached: {_cc} claims from {_papers_done} papers.", depth=depth))
                        return last_text or f"Claim target reached: {_cc} claims from {_papers_done} papers."

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
                # For list_papers loops in deep_read: redirect instead of stopping
                if "list_papers" in turn_sig and phase == "analyst_deep_read":
                    _log.info("Loop redirect: injecting read_paper instruction")
                    active_model.append_user_message(
                        conversation,
                        "DO NOT call list_papers again. You already have the list. "
                        "Pick the FIRST paper from the list and call: "
                        "read_paper(paper_id=<ID>, include_fulltext=true). "
                        "Then extract_claims for it. Process papers ONE BY ONE.",
                    )
                    _recent_tool_sigs.clear()  # Reset loop detection
                    continue
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
