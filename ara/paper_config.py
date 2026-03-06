# Location: ara/paper_config.py
# Purpose: Paper type definitions — phases, gates, tools, and requirements per paper type
# Functions: PAPER_TYPES, get_paper_config, get_supported_types
# Calls: N/A
# Imports: N/A

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PhaseConfig:
    """Configuration for a single pipeline phase within a paper type."""
    enabled: bool = True
    # "full", "light", "claims_only", "metadata_only"
    mode: str = "full"
    # Phase-specific notes for objective construction
    notes: str = ""


@dataclass(frozen=True)
class PaperTypeConfig:
    """Complete configuration for a paper type."""
    name: str
    label: str
    description: str
    supported: bool  # Whether ARA can currently execute this type

    # Reporting & methodology
    reporting_guideline: str = ""
    protocol_framework: str = ""
    quality_assessment: str = ""
    evidence_grading: str = ""
    synthesis_method: str = ""

    # Required outputs
    requires_prisma: bool = False
    requires_grade: bool = False
    requires_rob: bool = False
    requires_framework_diagram: bool = False
    requires_propositions: bool = False

    # Phase configuration
    phases: dict[str, PhaseConfig] = field(default_factory=dict)

    # Required sections (section_name → True if required)
    sections: tuple[str, ...] = (
        "abstract", "introduction", "literature_review",
        "methods", "results", "discussion", "conclusion",
    )

    # Section label overrides (e.g., "results" → "Propositions" for conceptual)
    section_labels: dict[str, str] = field(default_factory=dict)

    # Mandatory tables
    mandatory_tables: tuple[str, ...] = ()

    # Mandatory figures
    mandatory_figures: tuple[str, ...] = ()

    # Prompt overrides — which phases use alternative prompts
    # Maps phase_name → prompt_key in PHASE_PROMPTS / CONCEPTUAL_PHASE_PROMPTS
    prompt_overrides: dict[str, str] = field(default_factory=dict)

    # Deep read focus areas
    deep_read_focus: str = ""

    # Hypothesis generation focus
    hypothesis_focus: str = ""

    # What ARA cannot do for this type (shown to user)
    limitations: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Paper type definitions
# ---------------------------------------------------------------------------

_REVIEW = PaperTypeConfig(
    name="review",
    label="Systematic Literature Review",
    description="Synthesize all evidence on a specific question using reproducible methods",
    supported=True,
    reporting_guideline="PRISMA 2020 (27-item checklist)",
    protocol_framework="PROSPERO-style pre-registration",
    quality_assessment="JBI Critical Appraisal checklists (design-specific)",
    evidence_grading="GRADE (certainty of evidence per outcome)",
    synthesis_method="Narrative, thematic, or framework synthesis",
    requires_prisma=True,
    requires_grade=True,
    requires_rob=True,
    phases={
        "scout":      PhaseConfig(enabled=True, mode="full"),
        "snowball":   PhaseConfig(enabled=True, mode="full"),
        "protocol":   PhaseConfig(enabled=True, mode="full", notes="PROSPERO-style"),
        "verifier":   PhaseConfig(enabled=True, mode="full"),
        "triage":     PhaseConfig(enabled=True, mode="full"),
        "fetch_texts": PhaseConfig(enabled=True, mode="full"),
        "embed":      PhaseConfig(enabled=True, mode="full"),
        "deep_read":  PhaseConfig(enabled=True, mode="full"),
        "brancher":   PhaseConfig(enabled=True, mode="full"),
        "hypothesis": PhaseConfig(enabled=True, mode="full", notes="gap-focused"),
        "critic":     PhaseConfig(enabled=True, mode="full"),
        "synthesis":  PhaseConfig(enabled=True, mode="full"),
        "advisory_board": PhaseConfig(enabled=True, mode="full"),
        "writer":     PhaseConfig(enabled=True, mode="full"),
        "paper_critic": PhaseConfig(enabled=True, mode="full"),
    },
    mandatory_tables=(
        "study_characteristics", "risk_of_bias_summary",
        "evidence_synthesis_by_theme", "grade_summary_of_findings",
    ),
    mandatory_figures=("prisma_flow_diagram",),
    deep_read_focus=(
        "Extract structured claims with effect sizes, sample sizes, study designs. "
        "Assess risk of bias per study using JBI checklists."
    ),
    hypothesis_focus="Research gap identification and novel synthesis hypotheses",
)

_SCOPING = PaperTypeConfig(
    name="scoping",
    label="Scoping Review",
    description="Map the extent and nature of evidence on a broad topic, identify gaps",
    supported=True,
    reporting_guideline="PRISMA-ScR (Scoping Reviews extension)",
    protocol_framework="Arksey & O'Malley (2005), updated by Levac et al. (2010)",
    quality_assessment="Not required (key difference from SLR)",
    evidence_grading="Not required",
    synthesis_method="Charting and descriptive mapping",
    requires_prisma=True,  # PRISMA-ScR variant
    requires_grade=False,
    requires_rob=False,
    phases={
        "scout":      PhaseConfig(enabled=True, mode="full"),
        "snowball":   PhaseConfig(enabled=True, mode="full"),
        "protocol":   PhaseConfig(enabled=True, mode="light", notes="Optional, PCC framework"),
        "verifier":   PhaseConfig(enabled=True, mode="full"),
        "triage":     PhaseConfig(enabled=True, mode="full"),
        "fetch_texts": PhaseConfig(enabled=True, mode="full"),
        "embed":      PhaseConfig(enabled=True, mode="full"),
        "deep_read":  PhaseConfig(enabled=True, mode="claims_only",
                                  notes="No RoB assessment, charting focus"),
        "brancher":   PhaseConfig(enabled=True, mode="full"),
        "hypothesis": PhaseConfig(enabled=True, mode="full", notes="Gap mapping focus"),
        "critic":     PhaseConfig(enabled=True, mode="light"),
        "synthesis":  PhaseConfig(enabled=True, mode="full",
                                  notes="Charting table format, gap matrix"),
        "advisory_board": PhaseConfig(enabled=True, mode="full"),
        "writer":     PhaseConfig(enabled=True, mode="full"),
        "paper_critic": PhaseConfig(enabled=True, mode="full"),
    },
    sections=(
        "abstract", "introduction", "literature_review",
        "methods", "results", "discussion", "conclusion",
    ),
    mandatory_tables=(
        "charting_table", "descriptive_summary_of_evidence",
    ),
    mandatory_figures=("prisma_scr_flow", "evidence_map", "gap_matrix"),
    deep_read_focus=(
        "Extract claims for charting: population, concept, context, key findings. "
        "No risk of bias assessment required."
    ),
    hypothesis_focus="Evidence gap mapping and research agenda development",
)

_META_ANALYSIS = PaperTypeConfig(
    name="meta_analysis",
    label="Meta-Analysis",
    description="Statistically pool quantitative results across studies",
    supported=False,
    reporting_guideline="PRISMA 2020 + PRISMA for Meta-Analyses",
    protocol_framework="PROSPERO mandatory",
    quality_assessment="Cochrane RoB 2 (RCTs), ROBINS-I (non-randomized)",
    evidence_grading="GRADE mandatory",
    synthesis_method="Statistical pooling (fixed/random effects)",
    requires_prisma=True,
    requires_grade=True,
    requires_rob=True,
    phases={
        "scout":      PhaseConfig(enabled=True, mode="full"),
        "snowball":   PhaseConfig(enabled=True, mode="full"),
        "protocol":   PhaseConfig(enabled=True, mode="full", notes="PROSPERO mandatory"),
        "verifier":   PhaseConfig(enabled=True, mode="full"),
        "triage":     PhaseConfig(enabled=True, mode="full",
                                  notes="Strict — need extractable effect sizes"),
        "fetch_texts": PhaseConfig(enabled=True, mode="full"),
        "embed":      PhaseConfig(enabled=True, mode="full"),
        "deep_read":  PhaseConfig(enabled=True, mode="full",
                                  notes="Must extract effect sizes, SDs, sample sizes"),
        "brancher":   PhaseConfig(enabled=True, mode="light",
                                  notes="Same field, moderator-focused"),
        "hypothesis": PhaseConfig(enabled=True, mode="full",
                                  notes="Effect moderators and subgroup hypotheses"),
        "critic":     PhaseConfig(enabled=True, mode="full",
                                  notes="Statistical feasibility check"),
        "synthesis":  PhaseConfig(enabled=True, mode="full",
                                  notes="Effect size tables, forest plot data"),
        "advisory_board": PhaseConfig(enabled=True, mode="full"),
        "writer":     PhaseConfig(enabled=True, mode="full"),
        "paper_critic": PhaseConfig(enabled=True, mode="full",
                                    notes="Includes statistical audit"),
    },
    mandatory_tables=(
        "study_characteristics_with_effect_sizes",
        "subgroup_analysis", "sensitivity_analysis", "grade_summary",
    ),
    mandatory_figures=(
        "forest_plot", "funnel_plot", "prisma_flow",
    ),
    deep_read_focus=(
        "Extract quantitative data: effect sizes (Cohen's d, OR, RR, Hedges' g), "
        "confidence intervals, sample sizes, standard deviations. "
        "Assess risk of bias using Cochrane RoB 2 / ROBINS-I."
    ),
    hypothesis_focus="Effect moderators, subgroup differences, publication bias",
    limitations=(
        "ARA cannot compute statistical pooling (forest plots, I-squared, pooled effects). "
        "Requires R/metafor integration for statistical computation engine.",
    ),
)

_CONCEPTUAL = PaperTypeConfig(
    name="conceptual",
    label="Conceptual / Theoretical Paper",
    description="Develop new theoretical frameworks, typologies, or propositions",
    supported=True,
    reporting_guideline="Jabareen (2009), Whetten (1989) for framework-building methodology",
    protocol_framework="Not applicable",
    quality_assessment="Not applicable (building theory, not assessing evidence)",
    evidence_grading="Not applicable",
    synthesis_method="Theoretical integration and framework construction",
    requires_prisma=False,
    requires_grade=False,
    requires_rob=False,
    requires_framework_diagram=True,
    requires_propositions=True,
    phases={
        "scout":      PhaseConfig(enabled=True, mode="full"),
        "snowball":   PhaseConfig(enabled=True, mode="light"),
        "protocol":   PhaseConfig(enabled=True, mode="light",
                                  notes="Research protocol, not PROSPERO"),
        "verifier":   PhaseConfig(enabled=True, mode="light"),
        "triage":     PhaseConfig(enabled=True, mode="full"),
        "fetch_texts": PhaseConfig(enabled=True, mode="full"),
        "embed":      PhaseConfig(enabled=True, mode="full"),
        "deep_read":  PhaseConfig(enabled=True, mode="claims_only",
                                  notes="No RoB, focus on theoretical arguments and constructs"),
        "brancher":   PhaseConfig(enabled=True, mode="full",
                                  notes="Critical for cross-domain theory building"),
        "hypothesis": PhaseConfig(enabled=True, mode="full",
                                  notes="Framework candidates: typology, process model, multi-level"),
        "critic":     PhaseConfig(enabled=True, mode="full"),
        "synthesis":  PhaseConfig(enabled=True, mode="full",
                                  notes="Framework architecture, construct definitions"),
        "advisory_board": PhaseConfig(enabled=True, mode="full"),
        "writer":     PhaseConfig(enabled=True, mode="full"),
        "paper_critic": PhaseConfig(enabled=True, mode="full"),
    },
    sections=(
        "abstract", "introduction", "theoretical_background",
        "framework", "propositions", "discussion", "conclusion",
    ),
    section_labels={
        "literature_review": "Theoretical Background",
        "methods": "Framework Development",
        "results": "Propositions",
    },
    mandatory_tables=(
        "construct_definition_table", "proposition_summary_table",
        "comparison_with_existing_frameworks",
    ),
    mandatory_figures=("theoretical_framework_diagram",),
    prompt_overrides={
        "hypothesis": "hypothesis",  # Uses CONCEPTUAL_HYPOTHESIS_PROMPT via paper_type
        "synthesis": "synthesis",
        "writer": "writer",
        "paper_critic": "paper_critic",
    },
    deep_read_focus=(
        "Extract theoretical arguments, key constructs, definitions, "
        "boundary conditions, and empirical evidence supporting/challenging theories. "
        "Use claim_type: 'theory' for arguments, 'finding' for evidence, 'gap' for gaps."
    ),
    hypothesis_focus="Framework candidates: typology, process model, multi-level framework",
)

_BIBLIOMETRIC = PaperTypeConfig(
    name="bibliometric",
    label="Bibliometric / Scientometric Analysis",
    description="Map the intellectual structure of a research field using publication metadata",
    supported=False,
    reporting_guideline="Donthu et al. (2021), Zupic & Cater (2015)",
    protocol_framework="Not standard, reproducible methodology section required",
    quality_assessment="Not applicable (analyzing metadata)",
    evidence_grading="Not applicable",
    synthesis_method="Co-citation, bibliographic coupling, keyword co-occurrence",
    requires_prisma=False,
    requires_grade=False,
    requires_rob=False,
    phases={
        "scout":      PhaseConfig(enabled=True, mode="full"),
        "snowball":   PhaseConfig(enabled=True, mode="full"),
        "protocol":   PhaseConfig(enabled=False),
        "verifier":   PhaseConfig(enabled=True, mode="full"),
        "triage":     PhaseConfig(enabled=True, mode="full"),
        "fetch_texts": PhaseConfig(enabled=True, mode="full"),
        "embed":      PhaseConfig(enabled=True, mode="full"),
        "deep_read":  PhaseConfig(enabled=True, mode="metadata_only",
                                  notes="Citation counts, author affiliations, keywords"),
        "brancher":   PhaseConfig(enabled=False),
        "hypothesis": PhaseConfig(enabled=True, mode="light",
                                  notes="Research agenda from bibliometric gaps"),
        "critic":     PhaseConfig(enabled=True, mode="light"),
        "synthesis":  PhaseConfig(enabled=True, mode="full",
                                  notes="Network analysis summary"),
        "advisory_board": PhaseConfig(enabled=True, mode="full"),
        "writer":     PhaseConfig(enabled=True, mode="full"),
        "paper_critic": PhaseConfig(enabled=True, mode="full"),
    },
    mandatory_tables=(
        "top_cited_papers", "top_journals", "top_authors", "keyword_frequency",
    ),
    mandatory_figures=(
        "publication_trend", "co_citation_network",
        "keyword_co_occurrence", "geographic_distribution",
    ),
    deep_read_focus="Metadata extraction: citation counts, author affiliations, keywords",
    hypothesis_focus="Research agenda development from bibliometric clusters and gaps",
    limitations=(
        "ARA cannot compute co-citation matrices, network analysis, or burst detection. "
        "Requires Bibliometrix/R or Python networkx integration.",
    ),
)

_EMPIRICAL_QUANT = PaperTypeConfig(
    name="empirical_quant",
    label="Empirical Quantitative",
    description="Test hypotheses using measurable data (survey, experiment, secondary dataset)",
    supported=False,
    reporting_guideline="STROBE (observational), CONSORT (RCTs), CHERRIES (online surveys)",
    protocol_framework="Pre-registration recommended (AsPredicted, OSF)",
    quality_assessment="Not applicable (you ARE the study)",
    evidence_grading="Not applicable",
    synthesis_method="Statistical hypothesis testing",
    requires_prisma=False,
    requires_grade=False,
    requires_rob=False,
    phases={
        "scout":      PhaseConfig(enabled=True, mode="full"),
        "snowball":   PhaseConfig(enabled=True, mode="light"),
        "protocol":   PhaseConfig(enabled=True, mode="light",
                                  notes="Study design protocol"),
        "verifier":   PhaseConfig(enabled=True, mode="light"),
        "triage":     PhaseConfig(enabled=True, mode="full"),
        "fetch_texts": PhaseConfig(enabled=True, mode="full"),
        "embed":      PhaseConfig(enabled=True, mode="full"),
        "deep_read":  PhaseConfig(enabled=True, mode="full",
                                  notes="Claims + hypothesis support/refutation"),
        "brancher":   PhaseConfig(enabled=True, mode="full"),
        "hypothesis": PhaseConfig(enabled=True, mode="full",
                                  notes="Testable H1-Hn with statistical predictions"),
        "critic":     PhaseConfig(enabled=True, mode="full"),
        "synthesis":  PhaseConfig(enabled=True, mode="full",
                                  notes="Argument + research model architecture"),
        "advisory_board": PhaseConfig(enabled=True, mode="full"),
        "writer":     PhaseConfig(enabled=True, mode="full",
                                  notes="Pre-data sections only unless stats output provided"),
        "paper_critic": PhaseConfig(enabled=True, mode="full"),
    },
    sections=(
        "abstract", "introduction", "literature_review", "hypotheses",
        "methods", "results", "discussion", "conclusion",
    ),
    section_labels={"results": "Results"},
    mandatory_tables=(
        "descriptive_statistics", "correlation_matrix",
        "construct_reliability", "hypothesis_testing_results",
    ),
    mandatory_figures=("research_model_with_paths",),
    deep_read_focus=(
        "Extract claims and hypotheses from existing literature. "
        "Focus on theoretical arguments supporting hypothesis development."
    ),
    hypothesis_focus="Testable hypotheses (H1, H2...) with statistical predictions",
    limitations=(
        "ARA cannot collect primary data (surveys, experiments). "
        "Can design study, develop hypotheses, write lit review + methods. "
        "Can write results/discussion if given statistical output as input.",
    ),
)

_EMPIRICAL_QUAL = PaperTypeConfig(
    name="empirical_qual",
    label="Empirical Qualitative",
    description="Explore phenomena through interviews, case studies, observation",
    supported=False,
    reporting_guideline="COREQ (interviews), SRQR (qualitative research)",
    protocol_framework="Not standard but increasingly expected",
    quality_assessment="Lincoln & Guba (1985) trustworthiness criteria",
    evidence_grading="Not applicable",
    synthesis_method="Thematic analysis, grounded theory, case analysis",
    requires_prisma=False,
    requires_grade=False,
    requires_rob=False,
    phases={
        "scout":      PhaseConfig(enabled=True, mode="full"),
        "snowball":   PhaseConfig(enabled=True, mode="light"),
        "protocol":   PhaseConfig(enabled=False),
        "verifier":   PhaseConfig(enabled=True, mode="light"),
        "triage":     PhaseConfig(enabled=True, mode="full"),
        "fetch_texts": PhaseConfig(enabled=True, mode="full"),
        "embed":      PhaseConfig(enabled=True, mode="full"),
        "deep_read":  PhaseConfig(enabled=True, mode="full",
                                  notes="Claims + themes from existing literature"),
        "brancher":   PhaseConfig(enabled=True, mode="full"),
        "hypothesis": PhaseConfig(enabled=True, mode="full",
                                  notes="Exploratory research questions"),
        "critic":     PhaseConfig(enabled=True, mode="full"),
        "synthesis":  PhaseConfig(enabled=True, mode="full",
                                  notes="Theme structure from literature"),
        "advisory_board": PhaseConfig(enabled=True, mode="full"),
        "writer":     PhaseConfig(enabled=True, mode="full",
                                  notes="Pre-data sections only unless transcripts provided"),
        "paper_critic": PhaseConfig(enabled=True, mode="full"),
    },
    sections=(
        "abstract", "introduction", "literature_review",
        "methodology", "findings", "discussion", "conclusion",
    ),
    section_labels={
        "methods": "Methodology",
        "results": "Findings",
    },
    mandatory_tables=(
        "participant_characteristics", "coding_structure",
    ),
    mandatory_figures=("data_structure_gioia",),
    deep_read_focus=(
        "Extract theoretical arguments, themes, and constructs. "
        "Focus on identifying gaps for qualitative exploration."
    ),
    hypothesis_focus="Exploratory research questions, not testable hypotheses",
    limitations=(
        "ARA cannot conduct interviews or observation. "
        "Can design study (interview protocol, case selection), build theoretical framework, "
        "write lit review. If given transcripts, could assist with thematic coding.",
    ),
)

_MIXED_METHODS = PaperTypeConfig(
    name="mixed_methods",
    label="Mixed Methods",
    description="Combine quantitative and qualitative approaches in a single study",
    supported=False,
    reporting_guideline="GRAMMS or MMAT (Mixed Methods Appraisal Tool)",
    protocol_framework="Recommended",
    quality_assessment="MMAT for quality appraisal",
    evidence_grading="Not applicable",
    synthesis_method="Integration: merging, connecting, embedding, or explaining",
    requires_prisma=False,
    requires_grade=False,
    requires_rob=False,
    phases={
        "scout":      PhaseConfig(enabled=True, mode="full"),
        "snowball":   PhaseConfig(enabled=True, mode="light"),
        "protocol":   PhaseConfig(enabled=True, mode="light",
                                  notes="Mixed methods design protocol"),
        "verifier":   PhaseConfig(enabled=True, mode="light"),
        "triage":     PhaseConfig(enabled=True, mode="full"),
        "fetch_texts": PhaseConfig(enabled=True, mode="full"),
        "embed":      PhaseConfig(enabled=True, mode="full"),
        "deep_read":  PhaseConfig(enabled=True, mode="full",
                                  notes="Claims from both quant and qual literature"),
        "brancher":   PhaseConfig(enabled=True, mode="full"),
        "hypothesis": PhaseConfig(enabled=True, mode="full",
                                  notes="Both testable hypotheses and exploratory RQs"),
        "critic":     PhaseConfig(enabled=True, mode="full"),
        "synthesis":  PhaseConfig(enabled=True, mode="full",
                                  notes="Integration matrix, joint display plan"),
        "advisory_board": PhaseConfig(enabled=True, mode="full"),
        "writer":     PhaseConfig(enabled=True, mode="full",
                                  notes="Lit review + design framework only"),
        "paper_critic": PhaseConfig(enabled=True, mode="full"),
    },
    sections=(
        "abstract", "introduction", "literature_review",
        "methodology", "results", "discussion", "conclusion",
    ),
    mandatory_tables=("joint_display_table",),
    mandatory_figures=("mixed_methods_design_flowchart",),
    deep_read_focus=(
        "Extract claims from both quantitative and qualitative literature. "
        "Identify integration opportunities across methods."
    ),
    hypothesis_focus="Both testable hypotheses (quant strand) and exploratory RQs (qual strand)",
    limitations=(
        "ARA cannot execute either empirical strand. "
        "Can design both strands and write integration framework.",
    ),
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PAPER_TYPES: dict[str, PaperTypeConfig] = {
    "review": _REVIEW,
    "scoping": _SCOPING,
    "meta_analysis": _META_ANALYSIS,
    "conceptual": _CONCEPTUAL,
    "bibliometric": _BIBLIOMETRIC,
    "empirical_quant": _EMPIRICAL_QUANT,
    "empirical_qual": _EMPIRICAL_QUAL,
    "mixed_methods": _MIXED_METHODS,
}


def get_paper_config(paper_type: str) -> PaperTypeConfig:
    """Get configuration for a paper type. Falls back to 'review' if unknown."""
    cfg = PAPER_TYPES.get(paper_type)
    if cfg is None:
        import logging
        logging.getLogger(__name__).warning(
            "Unknown paper type '%s', falling back to 'review'", paper_type
        )
        return PAPER_TYPES["review"]
    return cfg


def get_supported_types() -> list[str]:
    """Return paper types ARA can currently execute."""
    return [name for name, cfg in PAPER_TYPES.items() if cfg.supported]


def is_phase_enabled(paper_type: str, phase_name: str) -> bool:
    """Check if a phase is enabled for the given paper type."""
    cfg = get_paper_config(paper_type)
    phase = cfg.phases.get(phase_name)
    if phase is None:
        return True  # Unknown phases default to enabled
    return phase.enabled


def get_phase_mode(paper_type: str, phase_name: str) -> str:
    """Get the mode (full/light/claims_only/metadata_only) for a phase."""
    cfg = get_paper_config(paper_type)
    phase = cfg.phases.get(phase_name)
    if phase is None:
        return "full"
    return phase.mode
