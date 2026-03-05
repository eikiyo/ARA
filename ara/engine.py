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
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import ARAConfig
from .model import (
    BaseModel, Conversation, ModelTurn, ToolCall, ToolResult,
    ModelError, RateLimitError, TokenUsage,
)
from .prompts import build_system_prompt, build_phase_system_prompt, PHASE_PROMPTS
from .tools import ARATools, PHASE_TOOLS

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

    _PIPELINE_PHASES: list[dict[str, str | None]] = [
        {
            "name": "scout",
            "prompt": "scout",
            "objective": (
                "Scout phase: Conduct exhaustive multi-round search across all 9 academic APIs "
                "for papers on: {topic}. Use search_all() with query reformulation across 6 rounds "
                "(primary terms, synonyms, broader scope, narrower/specific, cross-disciplinary, "
                "methodological). Target: 300+ unique papers from 4+ sources. "
                "After searching, call request_approval with a summary."
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
                "Draft a PROSPERO-style pre-registration protocol for the systematic review on: {topic}. "
                "Include: PICO framework, search strategy, inclusion/exclusion criteria, "
                "quality assessment framework (JBI), data extraction protocol, synthesis approach (GRADE). "
                "Save using write_section(section='protocol', content=...). "
                "Call request_approval with protocol summary."
            ),
        },
        {
            "name": "verifier",
            "prompt": "verifier",
            "objective": (
                "Verification phase: Call list_papers() to get all papers with DOIs. "
                "Verify credibility of top 100 papers by citation count. "
                "Use validate_doi, check_retraction, get_citation_count for each. "
                "Flag retracted or suspicious papers. Call request_approval with summary."
            ),
        },
        {
            "name": "triage",
            "prompt": "analyst_triage",
            "objective": (
                "Triage phase: Call list_papers() to get ALL papers. "
                "Rank each by relevance to: {topic}. Score 0-1. "
                "Select top 80-120 for deep reading ensuring diversity of perspectives, "
                "methods, and geography. Exclude retracted papers. "
                "Call request_approval with the ranking."
            ),
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
                "Deep read phase: Extract structured claims AND quantitative data from papers. "
                "Process: for each paper, (1) call read_paper to get content, "
                "(2) call extract_claims with a list of claim objects. "
                "EACH claim object MUST have: claim_text (string), claim_type (finding/method/limitation/gap), "
                "confidence (0-1), supporting_quotes (list of strings). "
                "ALSO extract when available: sample_size, effect_size, p_value, confidence_interval, "
                "study_design, population, country, year_range. "
                "Target: 150+ claims from 50+ papers. Extract 3-5 claims per paper. "
                "Call request_approval when done."
            ),
        },
        {
            "name": "brancher",
            "prompt": "brancher",
            "objective": (
                "Branch search: Based on findings about {topic}, conduct cross-domain searches "
                "using 4 branch types: lateral (adjacent fields), methodological (alternative methods), "
                "analogical (similar problems in other domains), convergent (independent confirmation). "
                "Store new papers found. Call request_approval with branch map."
            ),
        },
        {
            "name": "hypothesis",
            "prompt": "hypothesis",
            "objective": (
                "Generate research hypotheses from verified claims, gaps, and cross-domain findings. "
                "Score each on: novelty, feasibility, evidence_strength, methodology_fit, impact, reproducibility. "
                "For the top hypothesis, also specify: methodology framework (PRISMA/GRADE), "
                "analysis approach (thematic/narrative synthesis), quality assessment framework (JBI/Newcastle-Ottawa). "
                "Generate at least 5 hypotheses. Call request_approval."
            ),
        },
        {
            "name": "critic",
            "prompt": "critic",
            "objective": (
                "Critically evaluate the top hypothesis across 8 dimensions. "
                "Consider evidence strength, methodology fit, and cross-domain support. "
                "Decide: APPROVE or REJECT with detailed feedback and specific revision suggestions."
            ),
        },
        {
            "name": "synthesis",
            "prompt": "synthesis",
            "objective": (
                "Prepare ALL structured data the writer needs. Build these 7 outputs: "
                "(1) Study characteristics table with exact author names, (2) Evidence synthesis "
                "table with GRADE ratings per outcome, (3) Risk of bias assessment table, "
                "(4) PRISMA flow numbers, (5) Citation map organized by theme with (Author, Year) "
                "and effect sizes, (6) Structural causal model notes, (7) Inclusion/exclusion criteria table. "
                "Use EXACT author names from list_papers(compact=true). "
                "Call request_approval with all data."
            ),
        },
    ]

    _WRITER_SECTIONS = [
        ("abstract", (
            "Write the structured abstract (250-300 words). Format with labels: "
            "Background (2-3 sentences), Objective (1 sentence), Methods (2-3 sentences), "
            "Results (3-4 sentences with numbers), Conclusion (2-3 sentences). "
            "First call list_papers to see available papers. Use (Author, Year) citations."
        )),
        ("introduction", (
            "Write the introduction (800+ words). Include: opening hook, background with 8+ citations, "
            "clear research gap, numbered research questions (RQ1, RQ2...), statement of contribution, "
            "paper structure outline. Use search_similar to find relevant papers for each theme."
        )),
        ("literature_review", (
            "Write the literature review (1500+ words). THEMATIC organization (NOT paper-by-paper). "
            "At least 3 major themes. Cross-reference: 'While X found..., Y contradicted...'. "
            "Include comparison table (Author/Year, Design, Sample, Finding, Limitation). "
            "20+ unique citations. Use search_similar for each theme."
        )),
        ("methods", (
            "Write the methods section (1000+ words). Include: search strategy (all 9 APIs, date ranges, "
            "Boolean strings), inclusion/exclusion criteria TABLE, PRISMA screening process with exact numbers, "
            "quality assessment framework (JBI/Newcastle-Ottawa), data extraction protocol, "
            "analysis approach (thematic/narrative synthesis), methodology limitations."
        )),
        ("results", (
            "Write the results section (1200+ words). Include: PRISMA flow numbers, "
            "study characteristics TABLE, risk of bias assessment summary, "
            "thematic results (by research question, NOT by paper), "
            "GRADE evidence certainty TABLE (outcome, certainty, justification), "
            "effect sizes with CIs where available, heterogeneity reporting."
        )),
        ("discussion", (
            "Write the discussion (1000+ words). ALL subsections required: "
            "Summary of key findings (with GRADE certainty), causal inference analysis "
            "(direction, confounders, natural experiments, effect modification), "
            "comparison with 3+ existing reviews, theoretical integration with testable predictions, "
            "limitations (search, language bias, publication bias, generalizability), "
            "policy/practice implications, 3+ future research hypotheses with methodologies."
        )),
        ("conclusion", (
            "Write the conclusion (400+ words). Include: main contributions, "
            "key takeaways for researchers/policymakers/practitioners, "
            "limitations acknowledgment, 3+ specific future research questions, "
            "closing statement on broader significance."
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
        for phase_def in self._PIPELINE_PHASES:
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
                elif name == "snowball":
                    self._pipeline_snowball(topic, on_event)
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

            if db and session_id:
                db.save_phase_checkpoint(session_id, name)
            if on_event:
                on_event(StepEvent("subtask_end", data=f"Phase {name} complete", depth=0))

        # Writer — section by section
        if "writer" not in completed and not self.cancel_flag.is_set():
            self._pipeline_writer(topic, paper_type, context, on_event)
            if db and session_id:
                db.save_phase_checkpoint(session_id, "writer")

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
        objective = phase_def["objective"].format(topic=topic, paper_type=paper_type)

        # Reset search state for search phases
        if prompt_name in ("scout", "brancher"):
            from .tools.search import reset_search_all_state
            reset_search_all_state()

        system_prompt = build_phase_system_prompt(
            phase=prompt_name,
            topic=topic,
            rules=context.rules,
        )

        result = self._solve_recursive(
            objective=objective,
            context=context,
            depth=self.config.max_depth,  # No subtask — direct tool use only
            on_event=on_event,
            system_prompt_override=system_prompt,
            phase=prompt_name,
        )
        _log.info("PIPELINE: Phase %s completed — %d chars", phase_def["name"], len(result))
        return result

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
        result = self.tools.dispatch("snowball_references", {"limit": 20, "refs_per_paper": 10})
        _log.info("PIPELINE: Snowball result: %s", result[:200])
        if on_event:
            on_event(StepEvent("tool_result", data=result[:200], tool_name="snowball_references", depth=0))

    def _pipeline_writer(
        self, topic: str, paper_type: str,
        context: ExternalContext, on_event: StepCallback | None,
    ) -> None:
        """Run writer phase — one subtask per section for reliability."""
        _log.info("PIPELINE: Starting writer phase — %d sections", len(self._WRITER_SECTIONS))

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
        )

        for section_name, instruction in self._WRITER_SECTIONS:
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
                f"Use write_section(section='{section_name}', content=YOUR_TEXT) to save. "
                f"Do NOT use markdown headers at the start — the system adds them. "
                f"Only cite papers from the database — call list_papers first if needed."
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
            if on_event:
                on_event(StepEvent("subtask_end", data=f"Section {section_name} done", depth=0))

    def _pipeline_paper_critic(
        self, topic: str, paper_type: str,
        context: ExternalContext, on_event: StepCallback | None,
    ) -> None:
        """Run paper critic with revision loop (max 3 cycles)."""
        system_prompt = build_phase_system_prompt(
            phase="paper_critic", topic=topic, rules=context.rules,
        )

        for cycle in range(self.config.paper_critic_max_revisions + 1):
            if self.cancel_flag.is_set():
                break

            _log.info("PIPELINE CRITIC: Cycle %d/%d", cycle + 1, self.config.paper_critic_max_revisions + 1)
            if on_event:
                on_event(StepEvent("subtask_start", data=f"Paper critic cycle {cycle + 1}", depth=0))

            objective = (
                "Evaluate the complete paper draft against Nature/Lancet systematic review standards. "
                "Call generate_quality_audit to get the scorecard. Score 10 dimensions. "
                "Check ALL minimum thresholds (60+ citations, 6000+ words, 2+ tables, "
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
            )
            revision_objective = (
                f"Revise the paper based on critic feedback. Issues found: {result[:2000]}. "
                f"Rewrite only the sections that need improvement. Use write_section to save each. "
                f"Maintain existing quality — do NOT shorten sections."
            )
            self._solve_recursive(
                objective=revision_objective,
                context=context,
                depth=self.config.max_depth,
                on_event=on_event,
                system_prompt_override=writer_prompt,
                phase="writer",
            )

    def _pipeline_references(self, context: ExternalContext, on_event: StepCallback | None) -> None:
        """Generate reference list and PRISMA diagram."""
        _log.info("PIPELINE: Generating references and PRISMA")
        try:
            self.tools.dispatch("get_citations", {})
            self.tools.dispatch("generate_prisma_diagram", {})
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
    ) -> str:
        if depth > self.config.max_depth:
            _log.warning("MAX DEPTH %d reached — returning error", self.config.max_depth)
            return json.dumps({"error": f"Max depth {self.config.max_depth} reached"})

        # Select active model based on phase
        active_model = self.writer_model if phase in ("writer", "paper_critic", "synthesis", "protocol") else self.model
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

        while steps < self.config.max_steps_per_call:
            if self.cancel_flag.is_set():
                _log.info("  CANCELLED at step %d", steps)
                return last_text or "[Cancelled]"

            elapsed = time.time() - start_time
            if elapsed > self.config.max_solve_seconds:
                _log.warning("  TIMEOUT at step %d after %ds", steps, int(elapsed))
                return last_text or json.dumps({"error": f"Timeout after {int(elapsed)}s"})

            steps += 1
            _log.info("  step %d/%d | depth=%d | phase=%s | elapsed=%.0fs",
                       steps, self.config.max_steps_per_call, depth, phase or "manager", elapsed)

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
                capped_calls, context, depth, on_event, _result_cache,
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
        if result_cache is not None:
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

        summary = "\n\n".join(summary_parts) if summary_parts else "Previous context condensed — continue from current state."
        model.condense_conversation(conversation, summary)
