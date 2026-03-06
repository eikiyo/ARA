# DESIGN: Peer Review Pipeline

## Overview
Post-pipeline peer review system with 4 AI reviewers, 3-round structured deliberation, surgical revision by Opus 4.6, and improvement verification.

## Architecture

### Models & Personas
| Reviewer | Model | Persona | Strength |
|----------|-------|---------|----------|
| R1 | gemini-3.1-pro-preview | Deep Methodologist | Deep analytical reasoning, methodology rigor, statistical validity |
| R2 | gemini-3-flash-preview | Literature Expert | Literature breadth, theoretical grounding, citation quality |
| R3 | Claude Sonnet 4.6 | Structure Editor | Writing clarity, logical flow, consistency, formatting |
| R4 | Claude Opus 4.6 | Senior Editor | Holistic assessment, novelty evaluation, impact, publishability |

**Revision Agent**: Claude Opus 4.6 — surgical edits to .md sections + regenerates HTML/index

### Deliberation Protocol (3 Rounds)
1. **Round 1 — Independent Review**: Each reviewer scores 15 attributes (1-100) + provides verbose feedback
2. **Round 2 — Rebuttals**: Each reviewer reads all other reviews, responds with agreements/disagreements
3. **Round 3 — Consensus**: Final consensus scores + unified improvement recommendations

### 15 Scoring Attributes (1-100)
| # | Attribute | Description |
|---|-----------|-------------|
| 1 | Methodological Rigor | Research design, sampling, analysis appropriateness |
| 2 | Literature Coverage | Breadth/depth of literature review, key works cited |
| 3 | Theoretical Grounding | Theory integration, framework coherence |
| 4 | Novelty & Originality | New contribution beyond existing knowledge |
| 5 | Evidence Quality | Strength/reliability of supporting evidence |
| 6 | Statistical Validity | Appropriate stats, effect sizes, confidence intervals |
| 7 | Writing Clarity | Prose quality, readability, jargon appropriateness |
| 8 | Logical Coherence | Argument flow, internal consistency |
| 9 | Citation Accuracy | Correct attribution, no phantom refs, APA compliance |
| 10 | Structural Completeness | All required sections present and adequate |
| 11 | Reproducibility | Enough detail to replicate study |
| 12 | Limitations Awareness | Honest acknowledgment of weaknesses |
| 13 | Practical Implications | Real-world applicability of findings |
| 14 | Ethical Considerations | Bias awareness, ethical research practices |
| 15 | Publication Readiness | Overall readiness for target journal |

### Journal Tier
Dynamic — determined by paper topic/category. Configurable via `ARA_PEER_REVIEW_JOURNAL` env var. Default: auto-detect top-tier journal for the field.

### Improvement Check
- All 15 attributes must be non-decreasing (no regression)
- Average score must increase
- If both conditions met → `output_final/`
- If not → `output_final/` + `PeerReviewRejectionFeedback.md`

### Output Structure
```
output_draft/    ← renamed from output/ (original pipeline output)
output_final/    ← revised paper + peer review artifacts
  paper.md
  paper.html
  index.html
  references.bib
  quality_audit.json
  peer_review_round1.json
  peer_review_round2.json
  peer_review_consensus.json
  peer_review_summary.md
  PeerReviewRejectionFeedback.md  (only if not improved)
```

### Budget
$5 USD cap for entire peer review pipeline.

### Config
- `ARA_PEER_REVIEW_ENABLED` (default: true)
- `ARA_PEER_REVIEW_BUDGET` (default: 5.0)
- `ARA_PEER_REVIEW_JOURNAL` (default: auto)
- `--no-peer-review` CLI flag

### DB Schema (session DB)
```sql
CREATE TABLE IF NOT EXISTS peer_review_scores (
    score_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    round INTEGER NOT NULL,
    reviewer TEXT NOT NULL,
    attribute TEXT NOT NULL,
    score INTEGER NOT NULL,
    feedback TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS peer_review_consensus (
    consensus_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    cycle INTEGER NOT NULL DEFAULT 1,
    attribute TEXT NOT NULL,
    score INTEGER NOT NULL,
    feedback TEXT,
    improvement_plan TEXT,
    created_at TEXT NOT NULL
);
```

### Central DB Extension
```sql
CREATE TABLE IF NOT EXISTS peer_review_results (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_topic TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    scores_json TEXT NOT NULL,
    improved INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
```

## Files Modified
- `ara/config.py` — peer review config fields
- `ara/model.py` — AnthropicModel class
- `ara/credentials.py` — Anthropic API key loading
- `ara/builder.py` — build peer review models
- `ara/db.py` — peer review tables
- `ara/central_db.py` — peer review results table
- `ara/runtime.py` — wire peer review after output generation
- `ara/output.py` — output_draft rename + output_final generation
- `ara/__main__.py` — --no-peer-review flag
- `pyproject.toml` — anthropic dependency

## Files Created
- `ara/peer_review.py` — Main peer review engine (~500 lines)
