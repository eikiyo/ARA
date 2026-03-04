--
-- ARA (Autonomous Research Agent) PostgreSQL Schema
-- Location: Database schema definition for ARA system
-- Purpose: Full data model for task queue, research sessions, papers, claims, hypotheses, branches, and agent audit logging
-- Functions: Task dependency DAG, citation graph, claim-paper relationships, agent execution tracking
-- Calls: Used by Manager agent for task assignment, timeout detection, claim filtering, and session analytics
--

-- ============================================================================
-- ENUM TYPES
-- ============================================================================

CREATE TYPE task_status AS ENUM ('queued', 'claimed', 'running', 'done', 'failed', 'blocked');
CREATE TYPE agent_status AS ENUM ('idle', 'active', 'unhealthy', 'offline');
CREATE TYPE claim_verification_status AS ENUM ('unverified', 'pending', 'verified', 'contradicted', 'inconclusive');
CREATE TYPE hypothesis_status AS ENUM ('generated', 'supported', 'refuted', 'abandoned');
CREATE TYPE branch_type AS ENUM ('lateral', 'methodological', 'analogical', 'convergent');
CREATE TYPE agent_run_status AS ENUM ('success', 'failed', 'partial');

-- ============================================================================
-- AGENTS TABLE - Agent registration and heartbeat tracking
-- ============================================================================

CREATE TABLE agents (
  agent_id SERIAL PRIMARY KEY,
  agent_type VARCHAR(50) NOT NULL UNIQUE,
  description TEXT,
  status agent_status NOT NULL DEFAULT 'idle',
  last_heartbeat TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  max_concurrent_tasks INT NOT NULL DEFAULT 1,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE agents IS 'Agent registry: tracks Scout, Analyst, Verifier, Hypothesis Generator, Brancher, Critic, and Writer instances';
COMMENT ON COLUMN agents.agent_type IS 'Unique agent type: Scout, Analyst, Verifier, HypothesisGenerator, Brancher, Critic, Writer';
COMMENT ON COLUMN agents.last_heartbeat IS 'Last time agent reported alive; used to detect stale/crashed agents';
COMMENT ON COLUMN agents.max_concurrent_tasks IS 'Number of tasks this agent type can run in parallel';

CREATE INDEX idx_agents_status ON agents(status);
CREATE INDEX idx_agents_heartbeat ON agents(last_heartbeat);

-- ============================================================================
-- RESEARCH_SESSIONS TABLE - Top-level research run tracking
-- ============================================================================

CREATE TABLE research_sessions (
  session_id SERIAL PRIMARY KEY,
  user_id VARCHAR(255) NOT NULL,
  topic VARCHAR(500) NOT NULL,
  description TEXT,
  status VARCHAR(50) NOT NULL DEFAULT 'active',
  current_phase VARCHAR(50) NOT NULL DEFAULT 'scout',
  paper_type VARCHAR(50) NOT NULL DEFAULT 'research_article',
  citation_style VARCHAR(20) NOT NULL DEFAULT 'apa7',
  budget_cap DECIMAL(10,2) NOT NULL DEFAULT 5.00,
  budget_spent DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  deep_read_limit INT NOT NULL DEFAULT 100,
  enabled_sources TEXT[] NOT NULL DEFAULT ARRAY['semantic_scholar','arxiv','openalex','crossref','pubmed','core','dblp','europe_pmc','base','google_scholar'],
  total_papers_scraped INT NOT NULL DEFAULT 0,
  total_claims_extracted INT NOT NULL DEFAULT 0,
  total_hypotheses_generated INT NOT NULL DEFAULT 0,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT valid_phase CHECK (current_phase IN ('scout', 'analyst', 'verifier', 'hypothesis', 'brancher', 'critic', 'writer', 'completed', 'waiting_approval')),
  CONSTRAINT valid_paper_type CHECK (paper_type IN ('research_article', 'literature_review', 'systematic_review', 'meta_analysis', 'position_paper', 'case_study')),
  CONSTRAINT valid_citation_style CHECK (citation_style IN ('apa7', 'ieee', 'chicago', 'vancouver', 'harvard')),
  CONSTRAINT valid_budget CHECK (budget_cap > 0 AND budget_spent >= 0 AND budget_spent <= budget_cap * 1.1)
);

COMMENT ON TABLE research_sessions IS 'Tracks a complete research exploration: topic, scope, and aggregated statistics';
COMMENT ON COLUMN research_sessions.user_id IS 'User who initiated the research session';
COMMENT ON COLUMN research_sessions.topic IS 'Research topic or query (e.g., "CRISPR gene therapy safety")';
COMMENT ON COLUMN research_sessions.status IS 'Session state: active, completed, abandoned, failed';

CREATE INDEX idx_sessions_user_id ON research_sessions(user_id);
CREATE INDEX idx_sessions_created_at ON research_sessions(created_at DESC);
CREATE INDEX idx_sessions_status ON research_sessions(status);

-- ============================================================================
-- PAPERS TABLE - Scraped research papers with metadata
-- ============================================================================

CREATE TABLE papers (
  paper_id SERIAL PRIMARY KEY,
  session_id INT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  doi VARCHAR(255),
  title VARCHAR(1000) NOT NULL,
  authors TEXT[],
  abstract TEXT,
  source TEXT[] NOT NULL DEFAULT ARRAY['unknown'],
  publication_year INT,
  citation_count INT NOT NULL DEFAULT 0,
  retraction_status VARCHAR(50) NOT NULL DEFAULT 'none',
  retraction_reason TEXT,
  confidence_score DECIMAL(3,2) NOT NULL DEFAULT 1.0,
  relevance_score DECIMAL(3,2),
  full_text_available BOOLEAN NOT NULL DEFAULT false,
  deep_read_selected BOOLEAN NOT NULL DEFAULT false,
  embedding_id VARCHAR(255),
  url TEXT,
  pdf_url TEXT,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT valid_confidence CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0),
  CONSTRAINT valid_retraction_status CHECK (retraction_status IN ('none', 'retracted', 'withdrawn', 'flagged')),
  UNIQUE(session_id, doi)
);

COMMENT ON TABLE papers IS 'Research papers scraped from various sources (arXiv, PubMed, Semantic Scholar, etc.)';
COMMENT ON COLUMN papers.doi IS 'Digital Object Identifier; NULL if paper has no DOI (e.g., preprints)';
COMMENT ON COLUMN papers.source IS 'Array of sources where this paper was found (e.g., {arXiv, Semantic Scholar})';
COMMENT ON COLUMN papers.retraction_status IS 'none, retracted, withdrawn, or flagged; Verifier checks regularly';
COMMENT ON COLUMN papers.confidence_score IS 'Scout''s confidence in paper relevance (0.0-1.0)';
COMMENT ON COLUMN papers.embedding_id IS 'Reference to vector DB embedding for semantic search';

CREATE INDEX idx_papers_session_id ON papers(session_id);
CREATE INDEX idx_papers_doi ON papers(doi);
CREATE INDEX idx_papers_retraction ON papers(retraction_status) WHERE retraction_status != 'none';
CREATE INDEX idx_papers_confidence ON papers(confidence_score DESC);
CREATE INDEX idx_papers_created_at ON papers(created_at DESC);
CREATE INDEX idx_papers_embedding_id ON papers(embedding_id);

-- ============================================================================
-- PAPER_CITATIONS TABLE - Citation graph (which paper cites which)
-- ============================================================================

CREATE TABLE paper_citations (
  citation_id SERIAL PRIMARY KEY,
  source_paper_id INT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  target_paper_id INT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  session_id INT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT no_self_citation CHECK (source_paper_id != target_paper_id),
  UNIQUE(source_paper_id, target_paper_id)
);

COMMENT ON TABLE paper_citations IS 'Directed acyclic graph of paper citations; used by Brancher to find cross-domain connections';
COMMENT ON COLUMN paper_citations.source_paper_id IS 'Paper that cites (the citing paper)';
COMMENT ON COLUMN paper_citations.target_paper_id IS 'Paper being cited (the cited paper)';

CREATE INDEX idx_citations_source ON paper_citations(source_paper_id);
CREATE INDEX idx_citations_target ON paper_citations(target_paper_id);
CREATE INDEX idx_citations_session ON paper_citations(session_id);

-- ============================================================================
-- CLAIMS TABLE - Extracted factual claims from papers
-- ============================================================================

CREATE TABLE claims (
  claim_id SERIAL PRIMARY KEY,
  session_id INT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  primary_source_paper_id INT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  claim_text TEXT NOT NULL,
  verification_status claim_verification_status NOT NULL DEFAULT 'unverified',
  confidence_score DECIMAL(3,2) NOT NULL DEFAULT 0.5,
  supporting_papers_count INT NOT NULL DEFAULT 0,
  contradicting_papers_count INT NOT NULL DEFAULT 0,
  verification_method VARCHAR(50),
  verified_by_agent VARCHAR(50),
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT valid_confidence CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0)
);

COMMENT ON TABLE claims IS 'Atomic factual claims extracted by Analyst from papers; verified by Verifier';
COMMENT ON COLUMN claims.primary_source_paper_id IS 'Paper where claim was originally extracted from';
COMMENT ON COLUMN claims.claim_text IS 'The actual claim statement (e.g., "CRISPR can edit human germline cells")';
COMMENT ON COLUMN claims.verification_status IS 'unverified, pending, verified, contradicted, or inconclusive';
COMMENT ON COLUMN claims.supporting_papers_count IS 'Count of papers that support this claim (incremented by Verifier)';
COMMENT ON COLUMN claims.contradicting_papers_count IS 'Count of papers that contradict this claim (incremented by Verifier)';
COMMENT ON COLUMN claims.verification_method IS 'Method used: citation_count, expert_consensus, controlled_refutation, etc.';

CREATE INDEX idx_claims_session_id ON claims(session_id);
CREATE INDEX idx_claims_primary_source ON claims(primary_source_paper_id);
CREATE INDEX idx_claims_verification_status ON claims(verification_status);
CREATE INDEX idx_claims_confidence ON claims(confidence_score DESC);
CREATE INDEX idx_claims_created_at ON claims(created_at DESC);
-- Manager query: "give me all claims for session X with confidence > 0.7"
CREATE INDEX idx_claims_session_confidence ON claims(session_id, confidence_score DESC) WHERE verification_status != 'unverified';

-- ============================================================================
-- CLAIM_PAPERS TABLE - Many-to-many: claims linked to multiple papers
-- ============================================================================

CREATE TABLE claim_papers (
  claim_paper_id SERIAL PRIMARY KEY,
  claim_id INT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
  paper_id INT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  session_id INT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  relationship_type VARCHAR(50) NOT NULL DEFAULT 'supports',
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT valid_relationship CHECK (relationship_type IN ('supports', 'contradicts', 'elaborates', 'context')),
  UNIQUE(claim_id, paper_id, relationship_type)
);

COMMENT ON TABLE claim_papers IS 'Junction table: links a claim to all papers that mention it (support, contradict, or elaborate)';
COMMENT ON COLUMN claim_papers.relationship_type IS 'How paper relates to claim: supports, contradicts, elaborates, context';

CREATE INDEX idx_claim_papers_claim_id ON claim_papers(claim_id);
CREATE INDEX idx_claim_papers_paper_id ON claim_papers(paper_id);
CREATE INDEX idx_claim_papers_session_id ON claim_papers(session_id);
CREATE INDEX idx_claim_papers_relationship ON claim_papers(relationship_type);

-- ============================================================================
-- HYPOTHESES TABLE - Generated research hypotheses
-- ============================================================================

CREATE TABLE hypotheses (
  hypothesis_id SERIAL PRIMARY KEY,
  session_id INT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  hypothesis_text TEXT NOT NULL,
  status hypothesis_status NOT NULL DEFAULT 'generated',
  rank INT,
  overall_score DECIMAL(3,2) NOT NULL DEFAULT 0.5,
  strength TEXT,
  weakness TEXT,
  supporting_claims TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  generated_by_agent VARCHAR(50) NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT valid_overall_score CHECK (overall_score >= 0.0 AND overall_score <= 1.0)
);

COMMENT ON TABLE hypotheses IS 'Novel research hypotheses generated by HypothesisGenerator and refined by Critic';
COMMENT ON COLUMN hypotheses.hypothesis_text IS 'Full hypothesis statement (e.g., "CRISPR targeting can be improved by dual-strand offset design")';
COMMENT ON COLUMN hypotheses.rank IS 'Rank among all hypotheses in this session (1 = best)';
COMMENT ON COLUMN hypotheses.overall_score IS 'Weighted average of all dimension scores (0.0-1.0)';
COMMENT ON COLUMN hypotheses.strength IS 'Key strengths of this hypothesis';
COMMENT ON COLUMN hypotheses.weakness IS 'Key weaknesses of this hypothesis';
COMMENT ON COLUMN hypotheses.supporting_claims IS 'Array of claim_ids that support this hypothesis';
COMMENT ON COLUMN hypotheses.generated_by_agent IS 'Agent that generated this hypothesis (usually HypothesisGenerator)';

-- ============================================================================
-- HYPOTHESIS_SCORES TABLE - Multi-dimensional scoring by Critic
-- ============================================================================

CREATE TABLE hypothesis_scores (
  score_id SERIAL PRIMARY KEY,
  hypothesis_id INT NOT NULL REFERENCES hypotheses(hypothesis_id) ON DELETE CASCADE,
  dimension VARCHAR(50) NOT NULL,
  score DECIMAL(3,2) NOT NULL,
  scored_by VARCHAR(50) NOT NULL,
  iteration INT NOT NULL DEFAULT 1,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT valid_score CHECK (score >= 0.0 AND score <= 1.0),
  UNIQUE(hypothesis_id, dimension, iteration)
);

COMMENT ON TABLE hypothesis_scores IS 'Per-dimension scores for hypotheses. Supports any number of dimensions.';
COMMENT ON COLUMN hypothesis_scores.dimension IS 'Scoring dimension: novelty, evidence_strength, feasibility, coherence, cross_domain_support, methodology_fit, impact_potential, reproducibility, etc.';
COMMENT ON COLUMN hypothesis_scores.scored_by IS 'Agent that scored this dimension (usually Critic or HypothesisGenerator)';
COMMENT ON COLUMN hypothesis_scores.iteration IS 'Which Critic iteration produced this score (1, 2, or 3)';

CREATE INDEX idx_hyp_scores_hypothesis ON hypothesis_scores(hypothesis_id);
CREATE INDEX idx_hyp_scores_dimension ON hypothesis_scores(dimension);

CREATE INDEX idx_hypotheses_session_id ON hypotheses(session_id);
CREATE INDEX idx_hypotheses_status ON hypotheses(status);
CREATE INDEX idx_hypotheses_overall_score ON hypotheses(overall_score DESC);
CREATE INDEX idx_hypotheses_rank ON hypotheses(session_id, rank);
CREATE INDEX idx_hypotheses_created_at ON hypotheses(created_at DESC);

-- ============================================================================
-- BRANCH_MAP TABLE - Cross-domain connections found by Brancher
-- ============================================================================

CREATE TABLE branch_map (
  branch_id SERIAL PRIMARY KEY,
  session_id INT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  source_hypothesis_id INT NOT NULL REFERENCES hypotheses(hypothesis_id) ON DELETE CASCADE,
  target_domain VARCHAR(255) NOT NULL,
  branch_type branch_type NOT NULL,
  branch_confidence DECIMAL(3,2) NOT NULL DEFAULT 0.5,
  finding TEXT NOT NULL,
  papers_found INT NOT NULL DEFAULT 0,
  relevant_papers TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT valid_branch_confidence CHECK (branch_confidence >= 0.0 AND branch_confidence <= 1.0)
);

COMMENT ON TABLE branch_map IS 'Cross-domain branches explored by Brancher agent; maps hypotheses to new research areas';
COMMENT ON COLUMN branch_map.source_hypothesis_id IS 'Hypothesis from which this branch was discovered';
COMMENT ON COLUMN branch_map.target_domain IS 'New domain/field the branch explores (e.g., "neurotoxicology", "plant genetics")';
COMMENT ON COLUMN branch_map.branch_type IS 'lateral (same field, different angle), methodological (apply new method), analogical (apply from other domain), convergent (multiple domains support)';
COMMENT ON COLUMN branch_map.branch_confidence IS 'Brancher''s confidence that this cross-domain connection is meaningful (0.0-1.0)';
COMMENT ON COLUMN branch_map.finding IS 'Description of what was found in the target domain';
COMMENT ON COLUMN branch_map.relevant_papers IS 'Array of paper_ids found in target domain';

CREATE INDEX idx_branches_session_id ON branch_map(session_id);
CREATE INDEX idx_branches_hypothesis_id ON branch_map(source_hypothesis_id);
CREATE INDEX idx_branches_target_domain ON branch_map(target_domain);
CREATE INDEX idx_branches_type ON branch_map(branch_type);
CREATE INDEX idx_branches_confidence ON branch_map(branch_confidence DESC);

-- ============================================================================
-- TASK_QUEUE TABLE - Task DAG with dependencies, optimistic locking, and agent claiming
-- ============================================================================

CREATE TABLE task_queue (
  task_id SERIAL PRIMARY KEY,
  session_id INT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  task_type VARCHAR(50) NOT NULL,
  status task_status NOT NULL DEFAULT 'queued',
  assigned_agent_id INT REFERENCES agents(agent_id) ON DELETE SET NULL,
  priority INT NOT NULL DEFAULT 5,
  retry_count INT NOT NULL DEFAULT 0,
  max_retries INT NOT NULL DEFAULT 3,
  input_payload JSONB NOT NULL,
  output_payload JSONB,
  error_message TEXT,
  claimed_at TIMESTAMP WITH TIME ZONE,
  started_at TIMESTAMP WITH TIME ZONE,
  finished_at TIMESTAMP WITH TIME ZONE,
  version INT NOT NULL DEFAULT 1,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT valid_retry_count CHECK (retry_count >= 0 AND retry_count <= max_retries),
  CONSTRAINT valid_priority CHECK (priority >= 1 AND priority <= 10),
  CONSTRAINT valid_status_transitions CHECK (
    CASE 
      WHEN status = 'queued' THEN true
      WHEN status = 'claimed' THEN claimed_at IS NOT NULL
      WHEN status = 'running' THEN started_at IS NOT NULL AND claimed_at IS NOT NULL
      WHEN status = 'done' THEN finished_at IS NOT NULL AND output_payload IS NOT NULL
      WHEN status = 'failed' THEN finished_at IS NOT NULL AND error_message IS NOT NULL
      WHEN status = 'blocked' THEN true
      ELSE false
    END
  )
);

COMMENT ON TABLE task_queue IS 'Core task queue: DAG of work items assigned to agents, with optimistic locking for concurrent claiming';
COMMENT ON COLUMN task_queue.task_type IS 'Agent task type: scout_scrape, analyst_triage, analyst_deep_read, verifier_check, hypothesis_generate, brancher_explore, critic_review, writer_synthesize';
COMMENT ON COLUMN task_queue.status IS 'queued (ready), claimed (agent claimed), running (in progress), done (success), failed (error), blocked (waiting for dependencies)';
COMMENT ON COLUMN task_queue.assigned_agent_id IS 'Agent currently working on this task; NULL until claimed';
COMMENT ON COLUMN task_queue.priority IS 'Priority level (1-10); Manager pulls high-priority queued tasks first';
COMMENT ON COLUMN task_queue.input_payload IS 'JSONB task input: parameters, paper IDs, claim IDs, etc.; structure varies by task_type';
COMMENT ON COLUMN task_queue.output_payload IS 'JSONB task output: results, generated_paper_ids, hypotheses, etc.';
COMMENT ON COLUMN task_queue.version IS 'Optimistic lock version; agent must match version when marking task complete to prevent race conditions';
COMMENT ON COLUMN task_queue.claimed_at IS 'Timestamp when agent claimed the task (prevents duplicate claiming)';

CREATE INDEX idx_tasks_session_id ON task_queue(session_id);
CREATE INDEX idx_tasks_status ON task_queue(status);
CREATE INDEX idx_tasks_assigned_agent ON task_queue(assigned_agent_id);
CREATE INDEX idx_tasks_created_at ON task_queue(created_at DESC);
-- Manager query 1: "give me all queued tasks whose dependencies are all done"
CREATE INDEX idx_tasks_queued ON task_queue(session_id, status, priority DESC) WHERE status = 'queued';
-- Manager query 2: "give me all running tasks that have been running > 5 minutes"
CREATE INDEX idx_tasks_running_timeout ON task_queue(started_at) WHERE status = 'running';

-- ============================================================================
-- TASK_DEPENDENCIES TABLE - DAG edges (which tasks block which)
-- ============================================================================

CREATE TABLE task_dependencies (
  dependency_id SERIAL PRIMARY KEY,
  task_id INT NOT NULL REFERENCES task_queue(task_id) ON DELETE CASCADE,
  depends_on_task_id INT NOT NULL REFERENCES task_queue(task_id) ON DELETE CASCADE,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT no_self_dependency CHECK (task_id != depends_on_task_id),
  UNIQUE(task_id, depends_on_task_id)
);

COMMENT ON TABLE task_dependencies IS 'DAG edges: task X depends_on task Y (Y must complete before X can run)';
COMMENT ON COLUMN task_dependencies.task_id IS 'Task that is waiting (blocked)';
COMMENT ON COLUMN task_dependencies.depends_on_task_id IS 'Task that must complete first (blocker)';

CREATE INDEX idx_deps_task_id ON task_dependencies(task_id);
CREATE INDEX idx_deps_depends_on ON task_dependencies(depends_on_task_id);
-- Manager query 1: "give me all queued tasks whose dependencies are all done"
-- Subquery: SELECT task_id FROM task_dependencies WHERE status != 'done' GROUP BY task_id (find tasks with incomplete blockers)
CREATE INDEX idx_deps_for_ready_check ON task_dependencies(depends_on_task_id, task_id);

-- ============================================================================
-- AGENT_RUNS TABLE - Audit log of every agent execution
-- ============================================================================

CREATE TABLE agent_runs (
  agent_run_id SERIAL PRIMARY KEY,
  session_id INT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  task_id INT NOT NULL REFERENCES task_queue(task_id) ON DELETE CASCADE,
  agent_id INT NOT NULL REFERENCES agents(agent_id) ON DELETE RESTRICT,
  agent_type VARCHAR(50) NOT NULL,
  status agent_run_status NOT NULL DEFAULT 'success',
  tokens_used INT NOT NULL DEFAULT 0,
  cost DECIMAL(10,6) NOT NULL DEFAULT 0.0,
  error_message TEXT,
  started_at TIMESTAMP WITH TIME ZONE NOT NULL,
  finished_at TIMESTAMP WITH TIME ZONE NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT valid_duration CHECK (finished_at >= started_at),
  CONSTRAINT valid_cost CHECK (cost >= 0.0)
);

COMMENT ON TABLE agent_runs IS 'Audit log: every agent execution, token usage, cost, and result status for observability and cost tracking';
COMMENT ON COLUMN agent_runs.agent_type IS 'Denormalized agent type for fast filtering (matches agents.agent_type)';
COMMENT ON COLUMN agent_runs.tokens_used IS 'Total tokens consumed in this agent run (LLM calls + embeddings)';
COMMENT ON COLUMN agent_runs.cost IS 'Total cost in USD for this agent run';
COMMENT ON COLUMN agent_runs.status IS 'success, failed, or partial (partial = task done but with warnings)';

CREATE INDEX idx_runs_session_id ON agent_runs(session_id);
CREATE INDEX idx_runs_task_id ON agent_runs(task_id);
CREATE INDEX idx_runs_agent_id ON agent_runs(agent_id);
CREATE INDEX idx_runs_agent_type ON agent_runs(agent_type);
CREATE INDEX idx_runs_status ON agent_runs(status);
CREATE INDEX idx_runs_created_at ON agent_runs(created_at DESC);
-- Observability: find expensive runs, agent performance trends
CREATE INDEX idx_runs_cost_desc ON agent_runs(cost DESC);
CREATE INDEX idx_runs_started_at ON agent_runs(started_at DESC);

-- ============================================================================
-- CONSTRAINTS & FOREIGN KEYS (summary)
-- ============================================================================

-- Note: All session_id columns create hierarchical scoping: 
-- Every row belongs to exactly one session, enabling session-level isolation and deletion.

-- ============================================================================
-- MATERIALIZED VIEWS (Optional, for faster Manager queries)
-- ============================================================================

-- View: Ready-to-run tasks (queued + all dependencies done)
CREATE VIEW ready_tasks AS
SELECT t.task_id, t.session_id, t.task_type, t.priority
FROM task_queue t
WHERE t.status = 'queued'
  AND NOT EXISTS (
    SELECT 1 FROM task_dependencies td
    WHERE td.task_id = t.task_id
      AND td.depends_on_task_id IN (
        SELECT task_id FROM task_queue WHERE status != 'done'
      )
  )
ORDER BY t.session_id, t.priority DESC, t.created_at ASC;

COMMENT ON VIEW ready_tasks IS 'Tasks ready for agent assignment: queued AND all dependencies are done. Manager queries this frequently.';

-- View: Stale running tasks (> 5 minutes)
CREATE VIEW stale_tasks AS
SELECT t.task_id, t.session_id, t.task_type, t.assigned_agent_id, 
       EXTRACT(EPOCH FROM (NOW() - t.started_at)) AS elapsed_seconds
FROM task_queue t
WHERE t.status = 'running'
  AND t.started_at < NOW() - INTERVAL '5 minutes'
ORDER BY elapsed_seconds DESC;

COMMENT ON VIEW stale_tasks IS 'Tasks running > 5 min (possible timeout/crash). Manager checks this for recovery.';

-- View: Session quality summary (useful for Writer)
CREATE VIEW session_claim_summary AS
SELECT 
  s.session_id,
  s.user_id,
  s.topic,
  COUNT(DISTINCT c.claim_id) AS total_claims,
  COUNT(DISTINCT CASE WHEN c.verification_status = 'verified' THEN c.claim_id END) AS verified_claims,
  COUNT(DISTINCT CASE WHEN c.verification_status = 'unverified' THEN c.claim_id END) AS unverified_claims,
  COUNT(DISTINCT CASE WHEN c.verification_status = 'contradicted' THEN c.claim_id END) AS contradicted_claims,
  AVG(c.confidence_score) AS avg_claim_confidence,
  COUNT(DISTINCT p.paper_id) AS total_papers,
  COUNT(DISTINCT CASE WHEN p.retraction_status = 'none' THEN p.paper_id END) AS valid_papers
FROM research_sessions s
LEFT JOIN claims c ON s.session_id = c.session_id
LEFT JOIN papers p ON s.session_id = p.session_id
GROUP BY s.session_id, s.user_id, s.topic;

COMMENT ON VIEW session_claim_summary IS 'Session-level aggregate: claim counts, verification rates, paper counts. Used by Manager and Writer for sanity checks.';

-- ============================================================================
-- RULE_GATE TABLE - Natural language rules that constrain agent behavior
-- ============================================================================

CREATE TABLE rule_gate (
  rule_id SERIAL PRIMARY KEY,
  session_id INT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  rule_text TEXT NOT NULL,
  rule_type VARCHAR(20) NOT NULL DEFAULT 'exclude',
  created_by VARCHAR(50) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT valid_rule_type CHECK (rule_type IN ('include', 'exclude', 'constraint', 'methodology')),
  CONSTRAINT valid_created_by CHECK (created_by IN ('user', 'manager'))
);

COMMENT ON TABLE rule_gate IS 'Natural language rules that all agents must follow. Include and exclude directives.';
COMMENT ON COLUMN rule_gate.rule_text IS 'Natural language rule (e.g., "Do not use paper sources from Australia")';
COMMENT ON COLUMN rule_gate.rule_type IS 'include (positive directive), exclude (negative), constraint (hard limit), methodology (preference)';
COMMENT ON COLUMN rule_gate.created_by IS 'user (manually added) or manager (auto-generated from patterns)';
COMMENT ON COLUMN rule_gate.is_active IS 'Can be deactivated without deletion';

CREATE INDEX idx_rule_gate_session ON rule_gate(session_id);
CREATE INDEX idx_rule_gate_active ON rule_gate(session_id, is_active) WHERE is_active = true;

-- ============================================================================
-- APPROVAL_GATES TABLE - Tracks user approvals/rejections at each phase
-- ============================================================================

CREATE TABLE approval_gates (
  gate_id SERIAL PRIMARY KEY,
  session_id INT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  phase VARCHAR(50) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  action VARCHAR(20),
  user_comments TEXT,
  gate_data JSONB NOT NULL,
  resolved_at TIMESTAMP WITH TIME ZONE,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT valid_phase CHECK (phase IN ('scout', 'analyst_triage', 'analyst', 'verifier', 'hypothesis', 'brancher', 'critic', 'writer', 'budget_exceeded')),
  CONSTRAINT valid_status CHECK (status IN ('pending', 'approved', 'rejected', 'edited', 'reverted')),
  CONSTRAINT valid_action CHECK (action IS NULL OR action IN ('approve', 'reject', 'edit', 'revert'))
);

COMMENT ON TABLE approval_gates IS 'Tracks every approval gate interaction. One row per phase completion.';
COMMENT ON COLUMN approval_gates.gate_data IS 'JSONB snapshot of phase results shown to user at approval time';
COMMENT ON COLUMN approval_gates.user_comments IS 'User feedback when rejecting, editing, or reverting';

CREATE INDEX idx_gates_session ON approval_gates(session_id);
CREATE INDEX idx_gates_pending ON approval_gates(session_id, status) WHERE status = 'pending';

-- ============================================================================
-- SAMPLE ENUM POPULATION (for reference)
-- ============================================================================

-- INSERT INTO agents (agent_type, description, status, max_concurrent_tasks) VALUES
-- ('Scout', 'Scrapes papers from multiple sources', 'active', 4),
-- ('Analyst', 'Extracts claims from papers', 'active', 2),
-- ('Verifier', 'Verifies claims against other papers', 'active', 2),
-- ('HypothesisGenerator', 'Generates novel hypotheses from claims', 'active', 1),
-- ('Brancher', 'Finds cross-domain connections', 'active', 1),
-- ('Critic', 'Reviews and refines hypotheses', 'active', 1),
-- ('Writer', 'Synthesizes results into a report', 'active', 1);

-- ============================================================================
-- END SCHEMA
-- ============================================================================

