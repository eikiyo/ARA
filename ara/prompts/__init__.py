# Location: ara/prompts/__init__.py
# Purpose: Prompt registry — build system prompts per phase
# Functions: build_system_prompt, build_phase_prompt, PHASE_PROMPTS
# Calls: All prompt modules
# Imports: N/A

from __future__ import annotations

from typing import Any

from .base import BASE_PROMPT
from .manager import MANAGER_PROMPT
from .scout import SCOUT_PROMPT
from .analyst import ANALYST_TRIAGE_PROMPT, ANALYST_DEEP_READ_PROMPT
from .verifier import VERIFIER_PROMPT
from .hypothesis import HYPOTHESIS_PROMPT
from .brancher import BRANCHER_PROMPT
from .critic import CRITIC_PROMPT, PAPER_CRITIC_PROMPT
from .writer import WRITER_PROMPT
from .synthesis import SYNTHESIS_PROMPT
from .protocol import PROTOCOL_PROMPT
from .advisory_board import ADVISORY_BOARD_PROMPT
from .conceptual import (
    CONCEPTUAL_WRITER_PROMPT, CONCEPTUAL_SYNTHESIS_PROMPT,
    CONCEPTUAL_CRITIC_PROMPT, CONCEPTUAL_HYPOTHESIS_PROMPT,
)

# Default prompts — systematic literature review
PHASE_PROMPTS: dict[str, str] = {
    "manager": MANAGER_PROMPT,
    "scout": SCOUT_PROMPT,
    "analyst_triage": ANALYST_TRIAGE_PROMPT,
    "analyst_deep_read": ANALYST_DEEP_READ_PROMPT,
    "verifier": VERIFIER_PROMPT,
    "hypothesis": HYPOTHESIS_PROMPT,
    "brancher": BRANCHER_PROMPT,
    "critic": CRITIC_PROMPT,
    "synthesis": SYNTHESIS_PROMPT,
    "paper_critic": PAPER_CRITIC_PROMPT,
    "writer": WRITER_PROMPT,
    "protocol": PROTOCOL_PROMPT,
    "advisory_board": ADVISORY_BOARD_PROMPT,
}

# Conceptual paper prompt overrides — only the phases that differ
CONCEPTUAL_PHASE_PROMPTS: dict[str, str] = {
    "hypothesis": CONCEPTUAL_HYPOTHESIS_PROMPT,
    "synthesis": CONCEPTUAL_SYNTHESIS_PROMPT,
    "writer": CONCEPTUAL_WRITER_PROMPT,
    "paper_critic": CONCEPTUAL_CRITIC_PROMPT,
}

_RECURSIVE_SECTION = """
## Delegation Tools

You have two delegation tools:
- **subtask(objective, acceptance_criteria, prompt)**: Delegate a sub-objective to a child agent with its own context window. Use for complex, multi-step work (e.g., each research phase). The child runs autonomously and returns results.
- **execute(objective)**: Run a quick sub-task without depth tracking. Use for simple lookups or summaries.

**SERIAL EXECUTION**: Call exactly ONE tool per response. Wait for the result before calling the next. Never batch multiple tool calls.

When delegating, be specific about:
1. What to accomplish (objective)
2. How to judge success (acceptance_criteria)
3. Which phase prompt to use (prompt)
"""


def build_system_prompt(
    topic: str = "",
    paper_type: str = "research_article",
    rules: list[dict[str, Any]] | None = None,
    include_delegation: bool = True,
) -> str:
    parts = [BASE_PROMPT]
    if include_delegation:
        parts.append(_RECURSIVE_SECTION)

    parts.append(MANAGER_PROMPT)

    if topic:
        parts.append(f"\n## Current Research\n- **Topic:** {topic}\n- **Paper type:** {paper_type}\n")

    if rules:
        rules_text = "\n## Active Rules\n"
        for r in rules:
            rules_text += f"- {r.get('rule_type', 'RULE').upper()}: {r.get('rule_text', '')}\n"
        parts.append(rules_text)

    return "\n\n".join(parts)


def build_phase_prompt(phase: str, paper_type: str = "review") -> str:
    # For conceptual papers, check if this phase has a conceptual override
    if paper_type == "conceptual" and phase in CONCEPTUAL_PHASE_PROMPTS:
        prompt = CONCEPTUAL_PHASE_PROMPTS[phase]
    else:
        prompt = PHASE_PROMPTS.get(phase)
    if not prompt:
        available = ", ".join(sorted(PHASE_PROMPTS.keys()))
        raise ValueError(f"Unknown phase '{phase}'. Available: {available}")
    return BASE_PROMPT + "\n\n" + prompt


def build_phase_system_prompt(
    phase: str,
    topic: str = "",
    rules: list[dict[str, Any]] | None = None,
    paper_type: str = "review",
) -> str:
    parts = [build_phase_prompt(phase, paper_type=paper_type)]

    paper_type_label = "Conceptual/Theoretical Paper" if paper_type == "conceptual" else "Systematic Literature Review"
    if topic:
        parts.append(f"\n## Current Research\n- **Topic:** {topic}\n- **Paper Type:** {paper_type_label}\n")

    if rules:
        rules_text = "\n## Active Rules\n"
        for r in rules:
            rules_text += f"- {r.get('rule_type', 'RULE').upper()}: {r.get('rule_text', '')}\n"
        parts.append(rules_text)

    return "\n\n".join(parts)
