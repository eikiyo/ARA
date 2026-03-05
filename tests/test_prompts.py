# Location: tests/test_prompts.py
# Purpose: Tests for prompt building
# Functions: test_prompt_registry, test_build_system_prompt
# Calls: ara.prompts
# Imports: pytest

from ara.prompts import (
    PHASE_PROMPTS, build_system_prompt, build_phase_prompt,
    build_phase_system_prompt,
)


def test_all_phases_registered():
    expected = [
        "manager", "scout", "analyst_triage", "analyst_deep_read",
        "verifier", "hypothesis", "brancher", "critic", "writer",
    ]
    for phase in expected:
        assert phase in PHASE_PROMPTS, f"Phase '{phase}' not in PHASE_PROMPTS"


def test_phase_prompts_not_empty():
    for name, prompt in PHASE_PROMPTS.items():
        assert len(prompt) > 50, f"Phase '{name}' prompt is too short"


def test_build_system_prompt():
    prompt = build_system_prompt(topic="AI in healthcare", paper_type="research_article")
    assert "ARA" in prompt
    assert "AI in healthcare" in prompt
    assert "research_article" in prompt


def test_build_system_prompt_with_rules():
    rules = [
        {"rule_type": "constraint", "rule_text": "Only papers after 2020"},
        {"rule_type": "exclude", "rule_text": "No predatory journals"},
    ]
    prompt = build_system_prompt(topic="Test", rules=rules)
    assert "Only papers after 2020" in prompt
    assert "No predatory journals" in prompt


def test_build_phase_prompt():
    prompt = build_phase_prompt("scout")
    assert "Scout" in prompt
    assert "ARA" in prompt  # Should include base prompt


def test_build_phase_prompt_invalid():
    import pytest
    with pytest.raises(ValueError, match="Unknown phase"):
        build_phase_prompt("nonexistent_phase")


def test_build_phase_system_prompt():
    prompt = build_phase_system_prompt("verifier", topic="Test topic")
    assert "Verifier" in prompt
    assert "Test topic" in prompt
