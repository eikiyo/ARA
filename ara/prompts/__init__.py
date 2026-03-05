# Location: ara/prompts/__init__.py
# Purpose: Prompt registry — imports and exports all ARA phase prompts
# Functions: build_system_prompt, build_phase_prompt
# Calls: base.py, scout.py, analyst.py, verifier.py, hypothesis.py, brancher.py, critic.py, writer.py, manager.py
# Imports: All prompt modules

from __future__ import annotations

from .base import BASE_PROMPT
from .scout import SCOUT_PROMPT
from .analyst import ANALYST_TRIAGE_PROMPT, ANALYST_DEEP_READ_PROMPT
from .verifier import VERIFIER_PROMPT
from .hypothesis import HYPOTHESIS_PROMPT
from .brancher import BRANCHER_PROMPT
from .brancher_scout import BRANCHER_SCOUT_PROMPT
from .brancher_analyst import BRANCHER_ANALYST_PROMPT
from .critic import CRITIC_PROMPT
from .writer import WRITER_PROMPT
from .manager import MANAGER_PROMPT

# Phase prompt registry — maps phase names to their prompts
PHASE_PROMPTS = {
    "base": BASE_PROMPT,
    "scout": SCOUT_PROMPT,
    "analyst_triage": ANALYST_TRIAGE_PROMPT,
    "analyst_deep_read": ANALYST_DEEP_READ_PROMPT,
    "verifier": VERIFIER_PROMPT,
    "hypothesis": HYPOTHESIS_PROMPT,
    "brancher": BRANCHER_PROMPT,
    "brancher_scout": BRANCHER_SCOUT_PROMPT,
    "brancher_analyst": BRANCHER_ANALYST_PROMPT,
    "critic": CRITIC_PROMPT,
    "writer": WRITER_PROMPT,
    "manager": MANAGER_PROMPT,
}


def build_system_prompt(
    recursive: bool = True,
    acceptance_criteria: bool = True,
) -> str:
    """
    Build the system prompt for the ARA manager agent.

    Combines the manager prompt with optional recursive delegation
    and acceptance criteria sections.

    Args:
        recursive: Include section on recursive delegation via subtask/execute
        acceptance_criteria: Include section on acceptance criteria requirements

    Returns:
        Complete system prompt string for manager agent
    """
    sections = [MANAGER_PROMPT]

    if recursive:
        sections.append(_RECURSIVE_SECTION)

    if acceptance_criteria:
        sections.append(_ACCEPTANCE_SECTION)

    return "\n\n".join(sections)


def build_phase_prompt(phase_name: str) -> str:
    """
    Get the system prompt for a specific research phase.

    Args:
        phase_name: Name of the phase (scout, analyst_triage, analyst_deep_read,
                   verifier, hypothesis, brancher, critic, writer, manager, base)

    Returns:
        Phase-specific system prompt string

    Raises:
        ValueError: If phase_name is not recognized
    """
    phase_name = phase_name.lower().strip()

    if phase_name not in PHASE_PROMPTS:
        available = ", ".join(PHASE_PROMPTS.keys())
        raise ValueError(
            f"Unknown phase: {phase_name}. "
            f"Available phases: {available}"
        )

    return PHASE_PROMPTS[phase_name]


# Optional: Include base prompt in all system prompts
def build_phase_system_prompt(phase_name: str) -> str:
    """
    Build a complete system prompt for a phase, including base principles.

    Args:
        phase_name: Name of the research phase

    Returns:
        Combined prompt with base principles + phase-specific instructions
    """
    phase_prompt = build_phase_prompt(phase_name)
    return f"{BASE_PROMPT}\n\n{phase_prompt}"


_RECURSIVE_SECTION = """\
## Recursive Delegation
You can delegate work to child agents using `subtask` (same-tier model) or \
`execute` (cheapest model for simple tasks).

Guidelines:
- Each research phase should be a subtask
- Subtasks get their own step budget
- Cannot delegate to a higher-tier model
- Include clear acceptance_criteria for each subtask\
"""

_ACCEPTANCE_SECTION = """\
## Acceptance Criteria
When using subtask or execute, always provide specific acceptance_criteria. \
Results are automatically judged against these criteria.\
"""
