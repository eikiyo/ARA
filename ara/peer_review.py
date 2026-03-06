# Location: ara/peer_review.py
# Purpose: Peer review pipeline — 4 AI reviewers, 3-round deliberation, surgical revision
# Functions: PeerReviewPipeline, run_peer_review
# Calls: model.py, db.py, central_db.py, output.py
# Imports: json, logging, pathlib, shutil

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from .config import ARAConfig
from .model import BaseModel, Conversation, ModelTurn, TokenUsage
from .output import generate_output

_log = logging.getLogger(__name__)

# ── 15 Scoring Attributes ─────────────────────────────────

REVIEW_ATTRIBUTES = [
    "Methodological Rigor",
    "Literature Coverage",
    "Theoretical Grounding",
    "Novelty & Originality",
    "Evidence Quality",
    "Statistical Validity",
    "Writing Clarity",
    "Logical Coherence",
    "Citation Accuracy",
    "Structural Completeness",
    "Reproducibility",
    "Limitations Awareness",
    "Practical Implications",
    "Ethical Considerations",
    "Publication Readiness",
]

# ── Reviewer Personas ──────────────────────────────────────

REVIEWER_PERSONAS = {
    "deep_methodologist": {
        "name": "Reviewer 1 (Deep Methodologist)",
        "model_key": "gemini_deep",
        "persona": (
            "You are a rigorous methodologist reviewing for a top-tier journal. "
            "You focus on research design, sampling methodology, analytical appropriateness, "
            "statistical validity, effect sizes, reproducibility, literature coverage, "
            "theoretical grounding, and citation accuracy. "
            "You are known for catching methodological flaws that other reviewers miss. "
            "You pay close attention to sample sizes, confidence intervals, and p-values. "
            "You also check whether key foundational works are cited and the theoretical framework is coherent. "
            "You are thorough but fair — you acknowledge strong methodology when you see it."
        ),
    },
    "structure_editor": {
        "name": "Reviewer 2 (Structure Editor)",
        "model_key": "claude_sonnet",
        "persona": (
            "You are a meticulous editor reviewing for a top-tier journal. "
            "You focus on writing clarity, logical coherence, structural completeness, "
            "and overall presentation quality. You check that arguments flow logically, "
            "that each section serves its purpose, that transitions are smooth, "
            "and that the paper reads well from start to finish. "
            "You have a keen eye for inconsistencies, redundancies, and unclear prose. "
            "You also verify that all required sections are present and adequately developed."
        ),
    },
    "senior_editor": {
        "name": "Reviewer 3 (Senior Editor)",
        "model_key": "claude_opus",
        "persona": (
            "You are the editor-in-chief of a top-tier journal, providing the final holistic assessment. "
            "You evaluate novelty and originality, practical implications, ethical considerations, "
            "and overall publication readiness. You synthesize concerns from all dimensions "
            "and provide the definitive recommendation. "
            "You are experienced at judging whether a paper makes a genuine contribution to the field "
            "and whether it meets the standards of the target journal. "
            "You are demanding but constructive — you always explain how to improve."
        ),
    },
}


def _detect_journal(topic: str) -> str:
    """Auto-detect appropriate top-tier journal based on topic keywords."""
    topic_lower = topic.lower()

    journal_map = [
        (["machine learning", "deep learning", "neural network", "ai ", "artificial intelligence"],
         "NeurIPS / ICML / JMLR"),
        (["natural language", "nlp", "text mining", "language model"],
         "ACL / EMNLP / Computational Linguistics"),
        (["computer vision", "image", "object detection"],
         "CVPR / ICCV / TPAMI"),
        (["software engineering", "code", "programming"],
         "IEEE TSE / ICSE / FSE"),
        (["blockchain", "cryptocurrency", "fintech", "financial technology"],
         "Journal of Financial Economics / Review of Financial Studies"),
        (["healthcare", "clinical", "medical", "patient"],
         "The Lancet / NEJM / BMJ"),
        (["psychology", "cognitive", "behavior", "mental"],
         "Psychological Review / Annual Review of Psychology"),
        (["education", "learning", "teaching", "pedagogy"],
         "Review of Educational Research / Educational Researcher"),
        (["management", "organization", "leadership"],
         "Academy of Management Review / Administrative Science Quarterly"),
        (["marketing", "consumer", "brand"],
         "Journal of Marketing / Journal of Consumer Research"),
        (["economics", "economic", "market"],
         "American Economic Review / Econometrica"),
        (["sociology", "social", "inequality"],
         "American Sociological Review / Annual Review of Sociology"),
        (["biology", "genetic", "molecular", "cell"],
         "Nature / Science / Cell"),
        (["physics", "quantum", "particle"],
         "Physical Review Letters / Nature Physics"),
        (["chemistry", "chemical", "synthesis"],
         "Journal of the American Chemical Society / Nature Chemistry"),
        (["environment", "climate", "sustainability", "ecology"],
         "Nature Climate Change / Environmental Science & Technology"),
    ]

    for keywords, journal in journal_map:
        if any(kw in topic_lower for kw in keywords):
            return journal

    return "Nature / Science (multidisciplinary top-tier)"


def _build_review_prompt(
    paper_md: str, persona: str, journal: str, topic: str,
    attributes: list[str],
) -> str:
    attrs_text = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(attributes))
    return (
        f"{persona}\n\n"
        f"You are reviewing for: **{journal}**\n"
        f"Paper topic: {topic}\n\n"
        f"## Instructions\n"
        f"Read the paper below carefully and score it on ALL 15 attributes (1-100 each).\n"
        f"For each attribute, provide:\n"
        f"- A numeric score (1-100)\n"
        f"- Detailed feedback explaining the score\n"
        f"- Specific, actionable suggestions to reach a score of 95+\n\n"
        f"## Scoring Attributes\n{attrs_text}\n\n"
        f"## Output Format\n"
        f"Return your review as valid JSON with this exact structure:\n"
        f'{{"scores": {{"Methodological Rigor": {{"score": <int>, "feedback": "<text>", "improvement": "<text>"}}, ...}}}}\n\n'
        f"## Paper to Review\n\n{paper_md}"
    )


def _build_rebuttal_prompt(
    own_review: dict, all_reviews: list[dict], reviewer_name: str,
) -> str:
    others_text = ""
    for r in all_reviews:
        if r["reviewer"] == reviewer_name:
            continue
        others_text += f"\n### {r['reviewer']}\n"
        for attr, data in r["scores"].items():
            others_text += f"- {attr}: {data.get('score', 50)}/100 — {str(data.get('feedback', ''))[:150]}...\n"

    return (
        f"You previously reviewed a paper. Now review the other reviewers' assessments.\n\n"
        f"## Your Original Scores\n"
        + "\n".join(f"- {a}: {d['score']}/100" for a, d in own_review["scores"].items())
        + f"\n\n## Other Reviewers' Assessments\n{others_text}\n\n"
        f"## Instructions\n"
        f"For each attribute, state whether you:\n"
        f"- AGREE with the other reviewers' consensus\n"
        f"- DISAGREE (explain why, provide revised score if needed)\n"
        f"- PARTIALLY AGREE (nuance your position)\n\n"
        f"Return as JSON:\n"
        f'{{"rebuttals": {{"Methodological Rigor": {{"position": "AGREE|DISAGREE|PARTIALLY_AGREE", '
        f'"revised_score": <int_or_null>, "reasoning": "<text>"}}, ...}}}}'
    )


def _build_consensus_prompt(
    all_reviews: list[dict], all_rebuttals: list[dict], journal: str,
) -> str:
    reviews_text = ""
    for r in all_reviews:
        reviews_text += f"\n### {r['reviewer']}\n"
        for attr, data in r["scores"].items():
            reviews_text += f"- {attr}: {data.get('score', 50)}/100\n"

    rebuttals_text = ""
    for rb in all_rebuttals:
        rebuttals_text += f"\n### {rb['reviewer']}\n"
        for attr, data in rb.get("rebuttals", {}).items():
            pos = data.get("position", "N/A")
            revised = data.get("revised_score")
            rebuttals_text += f"- {attr}: {pos}"
            if revised is not None:
                rebuttals_text += f" (revised to {revised})"
            rebuttals_text += f" — {data.get('reasoning', '')[:100]}\n"

    return (
        f"You are the consensus moderator for a peer review panel at **{journal}**.\n\n"
        f"## Round 1 Scores\n{reviews_text}\n\n"
        f"## Round 2 Rebuttals\n{rebuttals_text}\n\n"
        f"## Instructions\n"
        f"Synthesize all reviewer feedback into final consensus scores.\n"
        f"For each attribute provide:\n"
        f"- Final consensus score (1-100) — weighted by reviewer expertise\n"
        f"- Unified feedback combining all reviewers' insights\n"
        f"- Specific improvement plan to reach 95+ on this attribute\n\n"
        f"Return as JSON:\n"
        f'{{"consensus": {{"Methodological Rigor": {{"score": <int>, "feedback": "<text>", '
        f'"improvement_plan": "<text>"}}, ...}}}}'
    )


def _build_revision_prompt(
    paper_md: str, consensus: dict, section_files: dict[str, str],
) -> str:
    feedback_text = ""
    for attr, data in consensus.items():
        score = data.get("score", 0)
        feedback_text += f"\n### {attr} ({score}/100)\n"
        feedback_text += f"Feedback: {data.get('feedback', '')}\n"
        feedback_text += f"Improvement plan: {data.get('improvement_plan', '')}\n"

    sections_list = "\n".join(f"- {name}" for name in section_files.keys())

    return (
        f"You are the revision agent. Your job is to make SURGICAL EDITS to the paper sections "
        f"to address peer review feedback. Do NOT rewrite entire sections — make targeted, precise fixes.\n\n"
        f"## Peer Review Consensus\n{feedback_text}\n\n"
        f"## Available Sections\n{sections_list}\n\n"
        f"## Instructions\n"
        f"For each section that needs changes, output the section name and the EXACT edits.\n"
        f"Use this JSON format:\n"
        f'{{"edits": [{{"section": "<section_name>", "find": "<exact text to find>", '
        f'"replace": "<replacement text>"}}, ...]}}\n\n'
        f"Rules:\n"
        f"- Only edit sections that the feedback specifically targets\n"
        f"- Keep edits minimal and precise — do not rewrite full paragraphs unless necessary\n"
        f"- Maintain citation accuracy — do not add phantom citations\n"
        f"- Preserve section structure and formatting\n"
        f"- Each 'find' string must be an EXACT substring of the current section content\n\n"
        f"## Current Paper\n\n{paper_md}"
    )


def _extract_json(text: str) -> dict:
    """Extract JSON from model response, handling markdown code blocks and truncation."""
    text = text.strip()
    # Strip markdown code fences
    if "```json" in text:
        text = text.split("```json", 1)[1]
        text = text.split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1]
        text = text.split("```", 1)[0]

    # Try to find JSON object
    start = text.find("{")
    if start < 0:
        _log.warning("No JSON object found in model response: %s...", text[:200])
        return {}

    # Try parsing from start — use bracket counting to find the right end
    candidate = text[start:]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Try rfind approach
    end = text.rfind("}") + 1
    if end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    # Truncated JSON — try to repair by closing open brackets/braces
    depth_brace = 0
    depth_bracket = 0
    in_string = False
    escape = False
    for i, ch in enumerate(candidate):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth_brace += 1
        elif ch == '}':
            depth_brace -= 1
        elif ch == '[':
            depth_bracket += 1
        elif ch == ']':
            depth_bracket -= 1

    # Close any unclosed structures
    if depth_brace > 0 or depth_bracket > 0:
        repair = candidate.rstrip().rstrip(',')
        # Close any open string
        if in_string:
            repair += '..."'
        repair += ']' * depth_bracket + '}' * depth_brace
        try:
            result = json.loads(repair)
            _log.info("Repaired truncated JSON (%d unclosed braces, %d unclosed brackets)",
                       depth_brace, depth_bracket)
            return result
        except json.JSONDecodeError:
            pass

    _log.warning("Failed to parse JSON from model response (%d chars): %s...", len(candidate), candidate[:200])
    return {}


class PeerReviewPipeline:
    """4-reviewer, 3-round peer review pipeline with surgical revision."""

    def __init__(
        self,
        models: dict[str, BaseModel],
        config: ARAConfig,
        db: Any = None,
        session_id: int | None = None,
        central_db: Any = None,
        on_event: Callable | None = None,
    ):
        self.models = models  # keys: gemini_deep, gemini_pro, claude_sonnet, claude_opus
        self.config = config
        self.db = db
        self.session_id = session_id
        self.central_db = central_db
        self.on_event = on_event
        self._total_tokens = TokenUsage()
        self._cost_usd = 0.0

    def _emit(self, msg: str) -> None:
        _log.info("PEER_REVIEW: %s", msg)
        if self.on_event:
            from .engine import StepEvent
            self.on_event(StepEvent("text", data=msg, depth=0))

    def _check_budget(self) -> bool:
        return self._cost_usd < self.config.peer_review_budget

    def _estimate_cost(self, usage: TokenUsage, model_name: str) -> float:
        """Rough cost estimation per model."""
        costs = {
            "claude-opus-4-6": (15.0, 75.0),  # per 1M tokens (input, output)
            "claude-sonnet-4-6": (3.0, 15.0),
            "gemini-3.1-pro-preview": (1.25, 10.0),
            "gemini-3-flash-preview": (0.15, 0.60),
            "gemini-2.5-pro": (1.25, 10.0),
            "gemini-2.5-flash": (0.15, 0.60),
            "gemini-3.1-flash-lite-preview": (0.10, 0.40),
        }
        # Default for unknown models
        rates = costs.get(model_name, (2.0, 10.0))
        cost = (usage.input_tokens * rates[0] + usage.output_tokens * rates[1]) / 1_000_000
        return cost

    def _generate(self, model_key: str, prompt: str) -> tuple[str, TokenUsage]:
        """Generate text from a model, tracking cost."""
        model = self.models.get(model_key)
        if not model:
            _log.error("Model %s not available for peer review", model_key)
            return "", TokenUsage()

        conv = model.create_conversation(system_prompt="", tool_defs=[])
        model.append_user_message(conv, prompt)
        turn = model.generate(conv)

        usage = turn.usage or TokenUsage()
        self._total_tokens.input_tokens += usage.input_tokens
        self._total_tokens.output_tokens += usage.output_tokens
        cost = self._estimate_cost(usage, model.model)
        self._cost_usd += cost

        return turn.text, usage

    def _checkpoint_path(self) -> Path:
        """Path for peer review checkpoint file."""
        ws = self.config.workspace
        return ws / self.config.session_root_dir / "output" / "peer_review" / "_checkpoint.json"

    def _save_checkpoint(self, stage: str, data: dict) -> None:
        """Save peer review checkpoint for resume capability."""
        cp = self._checkpoint_path()
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"stage": stage, **data}, indent=2, default=str), encoding="utf-8")
        _log.info("PEER_REVIEW: Checkpoint saved — stage=%s", stage)

    def _load_checkpoint(self) -> dict | None:
        """Load peer review checkpoint if it exists."""
        cp = self._checkpoint_path()
        if cp.exists():
            try:
                data = json.loads(cp.read_text(encoding="utf-8"))
                _log.info("PEER_REVIEW: Checkpoint found — stage=%s", data.get("stage"))
                return data
            except Exception:
                return None
        return None

    def _clear_checkpoint(self) -> None:
        """Remove checkpoint after successful completion."""
        cp = self._checkpoint_path()
        if cp.exists():
            cp.unlink()

    def run(self, topic: str, paper_type: str) -> dict[str, Any]:
        """Execute the full peer review pipeline.

        Returns: {"improved": bool, "cycle1_scores": {...}, "cycle2_scores": {...}, ...}
        """
        ws = self.config.workspace
        output_dir = ws / "output"

        if not output_dir.exists():
            return {"error": "No output directory found — run main pipeline first"}

        paper_md_path = output_dir / "paper.md"
        if not paper_md_path.exists():
            return {"error": "No paper.md found in output/"}

        # Detect journal
        journal = self.config.peer_review_journal
        if journal == "auto":
            journal = _detect_journal(topic)
        self._emit(f"Target journal: {journal}")

        paper_md = paper_md_path.read_text(encoding="utf-8")
        sections_dir = ws / self.config.session_root_dir / "output" / "sections"

        # ── Check for checkpoint to resume ─────────────────────
        checkpoint = self._load_checkpoint()
        cycle1_consensus = None
        cycle1_scores = {}

        if checkpoint and checkpoint.get("stage") in ("cycle1_done", "revision_done", "cycle2_done"):
            # Restore cycle 1 consensus from saved round 3 JSON
            c1r3 = ws / self.config.session_root_dir / "output" / "peer_review" / "cycle1_round3.json"
            if c1r3.exists():
                try:
                    cycle1_consensus = json.loads(c1r3.read_text(encoding="utf-8"))
                    cycle1_scores = {attr: data["score"] for attr, data in cycle1_consensus.items()}
                    cycle1_avg = sum(cycle1_scores.values()) / len(cycle1_scores) if cycle1_scores else 0
                    self._emit(f"Resuming — Cycle 1 scores restored (avg {cycle1_avg:.1f}/100)")
                except Exception as exc:
                    _log.warning("PEER_REVIEW: Failed to restore cycle1 checkpoint: %s", exc)
                    checkpoint = None

        # ── Cycle 1: Review the draft ─────────────────────────
        if cycle1_consensus is None:
            self._emit("Starting Peer Review Cycle 1...")
            cycle1_consensus = self._run_review_cycle(paper_md, topic, journal, cycle=1)
            if not cycle1_consensus:
                return {"error": "Peer review cycle 1 failed to produce consensus"}

            cycle1_scores = {attr: data["score"] for attr, data in cycle1_consensus.items()}
            cycle1_avg = sum(cycle1_scores.values()) / len(cycle1_scores) if cycle1_scores else 0
            self._emit(f"Cycle 1 average score: {cycle1_avg:.1f}/100")

            # Store in central DB
            if self.central_db:
                self.central_db.store_peer_review_result(
                    session_topic=topic, cycle=1, scores=cycle1_scores,
                    average_score=cycle1_avg, improved=False,
                )

            self._save_checkpoint("cycle1_done", {"cycle1_avg": cycle1_avg})

        # ── Revision by Opus 4.6 ──────────────────────────────
        skip_revision = checkpoint and checkpoint.get("stage") in ("revision_done", "cycle2_done")
        if not skip_revision:
            if not self._check_budget():
                self._emit(f"Budget exhausted (${self._cost_usd:.2f}/${self.config.peer_review_budget:.2f}). Skipping revision.")
                self._clear_checkpoint()
                return self._finalize(topic, paper_type, cycle1_consensus, None, improved=False)

            self._emit("Opus 4.6 making surgical edits...")
            self._apply_revisions(cycle1_consensus, sections_dir, paper_md)

            # Regenerate output with revised sections
            self._regenerate_output(topic, paper_type)
            self._save_checkpoint("revision_done", {"cycle1_avg": sum(cycle1_scores.values()) / len(cycle1_scores) if cycle1_scores else 0})
        else:
            self._emit("Resuming — skipping revision (already applied)")

        # ── Cycle 2: Review the revision ───────────────────────
        if not self._check_budget():
            self._emit(f"Budget exhausted after revision (${self._cost_usd:.2f}). Skipping cycle 2.")
            self._clear_checkpoint()
            return self._finalize(topic, paper_type, cycle1_consensus, None, improved=True)

        revised_md_path = ws / "output" / "paper.md"
        revised_md = revised_md_path.read_text(encoding="utf-8") if revised_md_path.exists() else paper_md

        self._emit("Starting Peer Review Cycle 2...")
        cycle2_consensus = self._run_review_cycle(revised_md, topic, journal, cycle=2)
        if not cycle2_consensus:
            self._clear_checkpoint()
            return self._finalize(topic, paper_type, cycle1_consensus, None, improved=True)

        cycle2_scores = {attr: data["score"] for attr, data in cycle2_consensus.items()}
        cycle2_avg = sum(cycle2_scores.values()) / len(cycle2_scores) if cycle2_scores else 0
        self._emit(f"Cycle 2 average score: {cycle2_avg:.1f}/100")

        # Store in central DB
        if self.central_db:
            improved = self._check_improvement(cycle1_scores, cycle2_scores)
            self.central_db.store_peer_review_result(
                session_topic=topic, cycle=2, scores=cycle2_scores,
                average_score=cycle2_avg, improved=improved,
            )

        # ── Improvement check ──────────────────────────────────
        improved = self._check_improvement(cycle1_scores, cycle2_scores)
        self._emit(f"Improvement check: {'PASSED' if improved else 'FAILED'}")
        self._emit(f"Total peer review cost: ${self._cost_usd:.2f}")

        self._clear_checkpoint()
        return self._finalize(topic, paper_type, cycle1_consensus, cycle2_consensus, improved)

    def _run_review_cycle(
        self, paper_md: str, topic: str, journal: str, cycle: int,
    ) -> dict[str, Any] | None:
        """Run one full review cycle (3 rounds)."""

        # ── Round 1: Independent reviews ───────────────────────
        self._emit(f"  Round 1: Independent reviews (cycle {cycle})...")
        reviews: list[dict] = []

        for reviewer_id, persona_info in REVIEWER_PERSONAS.items():
            if not self._check_budget():
                self._emit(f"  Budget limit reached at {persona_info['name']}")
                break

            self._emit(f"    {persona_info['name']} reviewing...")
            prompt = _build_review_prompt(
                paper_md, persona_info["persona"], journal, topic, REVIEW_ATTRIBUTES,
            )
            response_text, usage = self._generate(persona_info["model_key"], prompt)
            parsed = _extract_json(response_text)
            scores = parsed.get("scores", {})

            # Validate and fill missing attributes
            for attr in REVIEW_ATTRIBUTES:
                if attr not in scores:
                    scores[attr] = {"score": 50, "feedback": "Not evaluated", "improvement": ""}
                elif not isinstance(scores[attr], dict):
                    scores[attr] = {"score": int(scores[attr]) if scores[attr] else 50, "feedback": "", "improvement": ""}

            review = {
                "reviewer": persona_info["name"],
                "model_key": persona_info["model_key"],
                "scores": scores,
            }
            reviews.append(review)

            # Store individual scores in DB
            if self.db and self.session_id:
                for attr, data in scores.items():
                    self.db.store_peer_review_score(
                        session_id=self.session_id, cycle=cycle, round_num=1,
                        reviewer=persona_info["name"], attribute=attr,
                        score=data.get("score", 50),
                        feedback=data.get("feedback", ""),
                    )

        if not reviews:
            return None

        # Save round 1
        self._save_round_json(cycle, 1, reviews)

        # ── Round 2: Rebuttals ─────────────────────────────────
        if not self._check_budget():
            return self._synthesize_consensus(reviews, [], cycle, journal)

        self._emit(f"  Round 2: Rebuttals (cycle {cycle})...")
        rebuttals: list[dict] = []

        for review in reviews:
            if not self._check_budget():
                break
            reviewer_name = review["reviewer"]
            model_key = review["model_key"]
            self._emit(f"    {reviewer_name} responding to other reviewers...")

            prompt = _build_rebuttal_prompt(review, reviews, reviewer_name)
            response_text, usage = self._generate(model_key, prompt)
            parsed = _extract_json(response_text)

            rebuttal = {
                "reviewer": reviewer_name,
                "rebuttals": parsed.get("rebuttals", {}),
            }
            rebuttals.append(rebuttal)

            # Store rebuttal scores
            if self.db and self.session_id:
                for attr, data in rebuttal["rebuttals"].items():
                    revised = data.get("revised_score")
                    if revised is not None:
                        self.db.store_peer_review_score(
                            session_id=self.session_id, cycle=cycle, round_num=2,
                            reviewer=reviewer_name, attribute=attr,
                            score=revised,
                            feedback=data.get("reasoning", ""),
                        )

        # Save round 2
        self._save_round_json(cycle, 2, rebuttals)

        # ── Round 3: Consensus ─────────────────────────────────
        return self._synthesize_consensus(reviews, rebuttals, cycle, journal)

    def _synthesize_consensus(
        self, reviews: list[dict], rebuttals: list[dict],
        cycle: int, journal: str,
    ) -> dict[str, Any]:
        """Round 3: Use senior editor (Opus) to build consensus."""
        self._emit(f"  Round 3: Consensus synthesis (cycle {cycle})...")

        prompt = _build_consensus_prompt(reviews, rebuttals, journal)
        response_text, usage = self._generate("claude_opus", prompt)
        parsed = _extract_json(response_text)
        consensus = parsed.get("consensus", {})

        # Fill missing attributes with averaged scores from round 1
        for attr in REVIEW_ATTRIBUTES:
            if attr not in consensus:
                scores = [r["scores"].get(attr, {}).get("score", 50) for r in reviews]
                avg = sum(scores) // len(scores) if scores else 50
                consensus[attr] = {
                    "score": avg,
                    "feedback": "Averaged from individual reviews",
                    "improvement_plan": "",
                }

        # Store consensus in DB
        if self.db and self.session_id:
            for attr, data in consensus.items():
                self.db.store_peer_review_consensus(
                    session_id=self.session_id, cycle=cycle,
                    attribute=attr,
                    score=data.get("score", 50),
                    feedback=data.get("feedback", ""),
                    improvement_plan=data.get("improvement_plan", ""),
                )

        # Save round 3
        self._save_round_json(cycle, 3, consensus)

        return consensus

    def _apply_revisions(
        self, consensus: dict, sections_dir: Path, paper_md: str,
    ) -> None:
        """Use Opus to make surgical edits — one section at a time to avoid truncation."""
        if not sections_dir.exists():
            self._emit("  No sections directory found — cannot apply revisions")
            return

        # Load section files
        section_files: dict[str, str] = {}
        for f in sections_dir.iterdir():
            if f.suffix == ".md" and f.is_file():
                section_files[f.stem] = f.read_text(encoding="utf-8")

        if not section_files:
            self._emit("  No section files found")
            return

        # Build feedback text once
        feedback_text = ""
        for attr, data in consensus.items():
            score = data.get("score", 0)
            feedback_text += f"\n### {attr} ({score}/100)\n"
            feedback_text += f"Feedback: {data.get('feedback', '')}\n"
            feedback_text += f"Improvement plan: {data.get('improvement_plan', '')}\n"

        total_applied = 0
        total_edits = 0

        # Process each section independently — keeps JSON responses small
        for section_name, content in section_files.items():
            if not self._check_budget():
                self._emit(f"  Budget limit reached at section {section_name}")
                break

            # Skip synthesis_data, writing_brief, protocol — not paper sections
            if section_name in ("synthesis_data", "writing_brief", "protocol"):
                continue

            prompt = (
                f"You are the revision agent. Make SURGICAL EDITS to the '{section_name}' section "
                f"to address peer review feedback.\n\n"
                f"## Peer Review Consensus\n{feedback_text}\n\n"
                f"## Current Section: {section_name}\n\n{content}\n\n"
                f"## Instructions\n"
                f"Output a JSON with edits for THIS section only:\n"
                f'{{"edits": [{{"find": "<exact text to find>", "replace": "<replacement text>"}}, ...]}}\n\n'
                f"Rules:\n"
                f"- Only address feedback relevant to this section\n"
                f"- Keep edits minimal and precise — do not rewrite full paragraphs unless necessary\n"
                f"- Maintain citation accuracy — do not add phantom citations\n"
                f"- Each 'find' string must be an EXACT substring of the current section content\n"
                f"- If no changes needed for this section, return {{\"edits\": []}}"
            )

            response_text, usage = self._generate("claude_opus", prompt)
            parsed = _extract_json(response_text)
            edits = parsed.get("edits", [])

            section_applied = 0
            for edit in edits:
                find_text = edit.get("find", "")
                replace_text = edit.get("replace", "")

                if not find_text or find_text == replace_text:
                    continue

                if find_text in content:
                    content = content.replace(find_text, replace_text, 1)
                    section_applied += 1
                else:
                    _log.warning("Revision 'find' text not found in section %s: %s...", section_name, find_text[:80])

            if section_applied > 0:
                (sections_dir / f"{section_name}.md").write_text(content, encoding="utf-8")
                section_files[section_name] = content

            total_applied += section_applied
            total_edits += len(edits)
            if edits:
                _log.info("PEER_REVIEW: Section %s — applied %d/%d edits", section_name, section_applied, len(edits))

        self._emit(f"  Applied {total_applied}/{total_edits} surgical edits across sections")

    def _regenerate_output(self, topic: str, paper_type: str) -> None:
        """Regenerate paper.md, paper.html, index.html from revised sections."""
        ws = self.config.workspace
        ara_output = ws / self.config.session_root_dir / "output"
        sections_dir = ara_output / "sections"
        output_dir = ws / "output"

        bib_path = ara_output / "references.bib"
        apa_path = ara_output / "references_apa.txt"
        prisma_svg = ara_output / "prisma.svg"
        prisma_ascii = ara_output / "prisma_ascii.md"
        quality_audit = output_dir / "quality_audit.json"

        try:
            generate_output(
                output_dir=output_dir,
                sections_dir=sections_dir,
                bib_path=bib_path if bib_path.exists() else None,
                topic=topic,
                paper_type=paper_type,
                apa_path=apa_path if apa_path.exists() else None,
                prisma_svg_path=prisma_svg if prisma_svg.exists() else None,
                prisma_ascii_path=prisma_ascii if prisma_ascii.exists() else None,
                quality_audit_path=quality_audit if quality_audit.exists() else None,
            )
            self._emit("  Regenerated output files")
        except Exception as exc:
            _log.error("Failed to regenerate output: %s", exc)

    def _check_improvement(
        self, cycle1: dict[str, int], cycle2: dict[str, int],
    ) -> bool:
        """Check: all attributes non-decreasing AND average increases."""
        if not cycle1 or not cycle2:
            return False

        # Non-regression check
        for attr in REVIEW_ATTRIBUTES:
            s1 = cycle1.get(attr, 0)
            s2 = cycle2.get(attr, 0)
            if s2 < s1:
                _log.info("Regression on %s: %d -> %d", attr, s1, s2)
                return False

        # Average increase check
        avg1 = sum(cycle1.values()) / len(cycle1)
        avg2 = sum(cycle2.values()) / len(cycle2)
        return avg2 > avg1

    def _finalize(
        self, topic: str, paper_type: str,
        cycle1_consensus: dict, cycle2_consensus: dict | None,
        improved: bool,
    ) -> dict[str, Any]:
        """Rename output → output_draft, create output_final."""
        ws = self.config.workspace
        output_dir = ws / "output"
        output_draft = ws / "output_draft"
        output_final = ws / "output_final"

        # Rename output → output_draft (backup original)
        if output_dir.exists():
            if output_draft.exists():
                shutil.rmtree(output_draft)
            shutil.copytree(output_dir, output_draft)
            self._emit(f"  Backed up draft to {output_draft}")

        # output_final gets the current (possibly revised) output
        if output_final.exists():
            shutil.rmtree(output_final)
        if output_dir.exists():
            shutil.copytree(output_dir, output_final)

        # Write peer review artifacts to output_final
        self._write_peer_review_artifacts(output_final, cycle1_consensus, cycle2_consensus, improved, topic)

        result = {
            "improved": improved,
            "cycle1_avg": sum(d["score"] for d in cycle1_consensus.values()) / len(cycle1_consensus) if cycle1_consensus else 0,
            "total_cost_usd": round(self._cost_usd, 2),
            "output_draft": str(output_draft),
            "output_final": str(output_final),
        }
        if cycle2_consensus:
            result["cycle2_avg"] = sum(d["score"] for d in cycle2_consensus.values()) / len(cycle2_consensus)

        return result

    def _write_peer_review_artifacts(
        self, output_dir: Path, cycle1: dict, cycle2: dict | None,
        improved: bool, topic: str,
    ) -> None:
        """Write peer review JSONs and summary to output directory."""
        output_dir.mkdir(parents=True, exist_ok=True)

        # Consensus JSONs
        (output_dir / "peer_review_cycle1.json").write_text(
            json.dumps(cycle1, indent=2, default=str), encoding="utf-8",
        )
        if cycle2:
            (output_dir / "peer_review_cycle2.json").write_text(
                json.dumps(cycle2, indent=2, default=str), encoding="utf-8",
            )

        # Summary markdown
        summary = self._build_summary_md(cycle1, cycle2, improved, topic)
        (output_dir / "peer_review_summary.md").write_text(summary, encoding="utf-8")

        # Rejection feedback if not improved
        if not improved:
            rejection = self._build_rejection_md(cycle1, cycle2)
            (output_dir / "PeerReviewRejectionFeedback.md").write_text(rejection, encoding="utf-8")

    def _build_summary_md(
        self, cycle1: dict, cycle2: dict | None, improved: bool, topic: str,
    ) -> str:
        parts = [
            f"# Peer Review Summary\n",
            f"**Topic**: {topic}\n",
            f"**Result**: {'IMPROVED' if improved else 'NOT IMPROVED'}\n",
            f"**Cost**: ${self._cost_usd:.2f}\n\n",
            "## Cycle 1 Consensus Scores\n",
            "| Attribute | Score | Feedback |",
            "|-----------|-------|----------|",
        ]
        for attr in REVIEW_ATTRIBUTES:
            data = cycle1.get(attr, {})
            score = data.get("score", "N/A")
            feedback = data.get("feedback", "")[:100]
            parts.append(f"| {attr} | {score}/100 | {feedback} |")

        c1_avg = sum(d.get("score", 0) for d in cycle1.values()) / len(cycle1) if cycle1 else 0
        parts.append(f"\n**Average**: {c1_avg:.1f}/100\n")

        if cycle2:
            parts.extend([
                "\n## Cycle 2 Consensus Scores (Post-Revision)\n",
                "| Attribute | Score | Change |",
                "|-----------|-------|--------|",
            ])
            for attr in REVIEW_ATTRIBUTES:
                d1 = cycle1.get(attr, {}).get("score", 0)
                d2 = cycle2.get(attr, {}).get("score", 0)
                change = d2 - d1
                sign = "+" if change > 0 else ""
                parts.append(f"| {attr} | {d2}/100 | {sign}{change} |")

            c2_avg = sum(d.get("score", 0) for d in cycle2.values()) / len(cycle2) if cycle2 else 0
            parts.append(f"\n**Average**: {c2_avg:.1f}/100 (change: {'+' if c2_avg > c1_avg else ''}{c2_avg - c1_avg:.1f})\n")

        return "\n".join(parts)

    def _build_rejection_md(self, cycle1: dict, cycle2: dict | None) -> str:
        parts = [
            "# Peer Review Rejection Feedback\n",
            "The paper did not show sufficient improvement after revision.\n",
            "## Attributes that Regressed or Stagnated\n",
        ]
        latest = cycle2 or cycle1
        for attr in REVIEW_ATTRIBUTES:
            d1 = cycle1.get(attr, {}).get("score", 0)
            d2 = latest.get(attr, {}).get("score", 0) if cycle2 else d1
            if d2 <= d1 and cycle2:
                plan = cycle1.get(attr, {}).get("improvement_plan", "")
                parts.append(f"\n### {attr} ({d1} -> {d2})")
                parts.append(f"Improvement plan: {plan}\n")

        parts.append("\n## Recommended Actions\n")
        for attr in REVIEW_ATTRIBUTES:
            plan = latest.get(attr, {}).get("improvement_plan", "")
            if plan:
                parts.append(f"- **{attr}**: {plan}")

        return "\n".join(parts)

    def _save_round_json(self, cycle: int, round_num: int, data: Any) -> None:
        """Save round data as JSON to ara_data/output/."""
        ws = self.config.workspace
        pr_dir = ws / self.config.session_root_dir / "output" / "peer_review"
        pr_dir.mkdir(parents=True, exist_ok=True)
        filename = f"cycle{cycle}_round{round_num}.json"
        (pr_dir / filename).write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8",
        )


def build_peer_review_models(config: ARAConfig) -> dict[str, BaseModel] | None:
    """Build all 4 reviewer models. Returns None if keys are missing."""
    from .model import GeminiModel, AnthropicModel

    models: dict[str, BaseModel] = {}

    # Gemini models need Google API key
    if not config.google_api_key:
        _log.warning("Peer review: Google API key not available — Gemini reviewers disabled")
        return None

    try:
        models["gemini_deep"] = GeminiModel(model="gemini-3.1-pro-preview", api_key=config.google_api_key)
        models["gemini_pro"] = GeminiModel(model="gemini-3-flash-preview", api_key=config.google_api_key)
    except Exception as exc:
        _log.error("Failed to create Gemini peer review models: %s", exc)
        return None

    # Anthropic models need Anthropic API key
    if not config.anthropic_api_key:
        _log.warning("Peer review: Anthropic API key not available — Claude reviewers disabled")
        return None

    try:
        models["claude_sonnet"] = AnthropicModel(model="claude-sonnet-4-6", api_key=config.anthropic_api_key)
        models["claude_opus"] = AnthropicModel(model="claude-opus-4-6", api_key=config.anthropic_api_key)
    except Exception as exc:
        _log.error("Failed to create Anthropic peer review models: %s", exc)
        return None

    return models
