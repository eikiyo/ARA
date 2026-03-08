# Location: ara/tools/defs.py
# Purpose: Tool JSON schema definitions for LLM function calling
# Functions: TOOL_DEFINITIONS
# Calls: N/A
# Imports: N/A

from __future__ import annotations

from typing import Any

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    # ── Search tools (9 APIs) ───────────────────────────────────
    {
        "name": "search_semantic_scholar",
        "description": "Search Semantic Scholar for academic papers. Returns papers with abstracts, citation counts, and fields of study.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
                "year_range": {"type": "string", "description": "Year filter, e.g. '2020-2024'"},
                "fields_of_study": {"type": "string", "description": "Comma-separated fields, e.g. 'Computer Science,Biology'"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_arxiv",
        "description": "Search arXiv for preprints. Returns papers with abstracts and categories.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
                "category": {"type": "string", "description": "arXiv category, e.g. 'cs.AI'"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_crossref",
        "description": "Search CrossRef for papers with DOIs, metadata, and references.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
                "from_year": {"type": "integer", "description": "Papers published from this year"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_openalex",
        "description": "Search OpenAlex for papers with concepts, institutions, and open access status.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
                "from_year": {"type": "integer", "description": "Papers published from this year"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_pubmed",
        "description": "Search PubMed for biomedical papers with MeSH terms.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_core",
        "description": "Search CORE for open access papers with full text links.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_dblp",
        "description": "Search DBLP for computer science papers with venue and author info.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_europe_pmc",
        "description": "Search Europe PMC for biomedical and life science papers.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_base",
        "description": "Search BASE (Bielefeld Academic Search Engine) for broad academic content.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": ["query"],
        },
    },

    # ── Batch search ─────────────────────────────────────────────
    {
        "name": "search_all",
        "description": "Search ALL 9 academic APIs in parallel with a single call. Returns combined results with per-source counts. Use this instead of calling individual search tools.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results per API (default 20)"},
            },
            "required": ["query"],
        },
    },

    # ── Paper tools ─────────────────────────────────────────────
    {
        "name": "list_papers",
        "description": "List papers in the session with metadata (title, abstract snippet, authors, year, citations, relevance_score, selected_for_deep_read). Sorted by relevance then citations. Use for triage/ranking.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max papers to return (default 200, max 200)"},
                "offset": {"type": "integer", "description": "Skip first N papers (for pagination)"},
                "compact": {"type": "boolean", "description": "If true, omit abstracts to save tokens"},
                "selected_only": {"type": "boolean", "description": "If true, only return papers selected for deep reading"},
                "needs_claims": {"type": "boolean", "description": "If true, exclude papers that already have extracted claims"},
            },
        },
    },
    {
        "name": "fetch_fulltext",
        "description": "Fetch full text of a paper via Unpaywall (by DOI). Caches PDF locally.",
        "parameters": {
            "type": "object",
            "properties": {
                "doi": {"type": "string", "description": "Paper DOI"},
            },
            "required": ["doi"],
        },
    },
    {
        "name": "read_paper",
        "description": "Read a paper's metadata and abstract from the local database. By default returns metadata + abstract only. Set include_fulltext=true to also get cached full text (use sparingly — large output).",
        "parameters": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "integer", "description": "Paper ID from database"},
                "include_fulltext": {"type": "boolean", "description": "Include full text if cached (default false, use sparingly)"},
            },
            "required": ["paper_id"],
        },
    },
    {
        "name": "list_claims",
        "description": "List all extracted claims with paper metadata (author, year, title). Returns claim_text, claim_type, effect_size, p_value, sample_size, study_design, population. Use to ground writing in actual extracted evidence.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "search_similar",
        "description": "Search for similar papers in the current session by keyword matching.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Query text to find similar papers"},
                "limit": {"type": "integer", "description": "Max results (default 10)"},
            },
            "required": ["text"],
        },
    },

    {
        "name": "search_evidence",
        "description": "MMR-diversified evidence search across 115K vectors (claims + full-text chunks). "
                       "Returns diverse, relevant evidence from MULTIPLE papers — avoids concentration from a single source. "
                       "Use this for writing to get grounded claims with effect sizes, p-values, and full-text excerpts.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Query text describing the evidence you need"},
                "limit": {"type": "integer", "description": "Max claims to return (default 15)"},
                "include_chunks": {"type": "boolean", "description": "Include full-text excerpts (default true)"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "rate_papers",
        "description": "Batch-rate papers for relevance and select them for deep reading. Call with a list of paper ratings.",
        "parameters": {
            "type": "object",
            "properties": {
                "ratings": {
                    "type": "array",
                    "description": "List of {paper_id, relevance_score (0-1), selected (true/false)}",
                    "items": {
                        "type": "object",
                        "properties": {
                            "paper_id": {"type": "integer"},
                            "relevance_score": {"type": "number"},
                            "selected": {"type": "boolean"},
                        },
                        "required": ["paper_id", "relevance_score", "selected"],
                    },
                },
            },
            "required": ["ratings"],
        },
    },

    # ── Verification tools ──────────────────────────────────────
    {
        "name": "check_retraction",
        "description": "Check if a paper has been retracted via CrossRef.",
        "parameters": {
            "type": "object",
            "properties": {
                "doi": {"type": "string", "description": "Paper DOI to check"},
            },
            "required": ["doi"],
        },
    },
    {
        "name": "get_citation_count",
        "description": "Get citation count and influential citations from Semantic Scholar.",
        "parameters": {
            "type": "object",
            "properties": {
                "doi": {"type": "string", "description": "Paper DOI"},
            },
            "required": ["doi"],
        },
    },
    {
        "name": "validate_doi",
        "description": "Validate that a DOI resolves correctly.",
        "parameters": {
            "type": "object",
            "properties": {
                "doi": {"type": "string", "description": "DOI to validate"},
            },
            "required": ["doi"],
        },
    },

    # ── Research tools ──────────────────────────────────────────
    {
        "name": "extract_claims",
        "description": "Extract and store claims from a paper. Call with just paper_id to get paper content, then call again with paper_id and claims list to store.",
        "parameters": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "integer", "description": "Paper ID to extract claims from"},
                "claims": {
                    "type": "array",
                    "description": "List of extracted claims to store. Each: {claim_text, claim_type, confidence, supporting_quotes, section, sample_size, effect_size, p_value, confidence_interval, study_design, population, country, year_range}",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim_text": {"type": "string"},
                            "claim_type": {"type": "string", "description": "finding/method/limitation/gap"},
                            "confidence": {"type": "number"},
                            "supporting_quotes": {"type": "array", "items": {"type": "string"}},
                            "section": {"type": "string"},
                            "sample_size": {"type": "string", "description": "e.g. N=1234"},
                            "effect_size": {"type": "string", "description": "e.g. OR=2.3, Cohen's d=0.45"},
                            "p_value": {"type": "string", "description": "e.g. p<0.001"},
                            "confidence_interval": {"type": "string", "description": "e.g. 95% CI: 1.2-3.4"},
                            "study_design": {"type": "string", "description": "e.g. cross-sectional, RCT, cohort"},
                            "population": {"type": "string", "description": "e.g. adults aged 30-65"},
                            "country": {"type": "string", "description": "e.g. Sweden, USA"},
                            "year_range": {"type": "string", "description": "e.g. 2010-2018"},
                        },
                    },
                },
            },
            "required": ["paper_id"],
        },
    },
    {
        "name": "score_hypothesis",
        "description": "Score a hypothesis. Call with just hypothesis to get scoring instructions, then call again with scores dict to store.",
        "parameters": {
            "type": "object",
            "properties": {
                "hypothesis": {"type": "string", "description": "The hypothesis to score"},
                "context": {"type": "string", "description": "Supporting evidence and claims context"},
                "scores": {
                    "type": "object",
                    "description": "Scores to store: {novelty, feasibility, evidence_strength, methodology_fit, impact, reproducibility} each 0.0-1.0",
                    "properties": {
                        "novelty": {"type": "number"},
                        "feasibility": {"type": "number"},
                        "evidence_strength": {"type": "number"},
                        "methodology_fit": {"type": "number"},
                        "impact": {"type": "number"},
                        "reproducibility": {"type": "number"},
                    },
                },
            },
            "required": ["hypothesis"],
        },
    },
    {
        "name": "branch_search",
        "description": "Cross-domain search for a hypothesis. Searches adjacent fields to prevent tunnel vision.",
        "parameters": {
            "type": "object",
            "properties": {
                "hypothesis": {"type": "string", "description": "The hypothesis to explore"},
                "branch_type": {
                    "type": "string",
                    "description": "Type: lateral, methodological, analogical, convergent, contrarian, temporal, geographic",
                },
                "domain_hint": {"type": "string", "description": "Adjacent domain to search in"},
            },
            "required": ["hypothesis", "branch_type"],
        },
    },

    # ── Claim verification ─────────────────────────────────────
    {
        "name": "verify_claim",
        "description": "Cross-check a claim against its source paper. Returns the paper's abstract and any stored supporting quotes for manual verification.",
        "parameters": {
            "type": "object",
            "properties": {
                "claim_id": {"type": "integer", "description": "Claim ID to verify"},
            },
            "required": ["claim_id"],
        },
    },

    # ── Risk of Bias & GRADE ────────────────────────────────────
    {
        "name": "assess_risk_of_bias",
        "description": "Assess risk of bias for a paper using JBI Critical Appraisal framework. Store structured bias ratings per domain.",
        "parameters": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "integer", "description": "Paper ID to assess"},
                "framework": {"type": "string", "description": "Assessment framework (default: JBI)"},
                "selection_bias": {"type": "string", "description": "low / moderate / high / unclear"},
                "performance_bias": {"type": "string", "description": "low / moderate / high / unclear"},
                "detection_bias": {"type": "string", "description": "low / moderate / high / unclear"},
                "attrition_bias": {"type": "string", "description": "low / moderate / high / unclear"},
                "reporting_bias": {"type": "string", "description": "low / moderate / high / unclear"},
                "overall_risk": {"type": "string", "description": "low / moderate / high / unclear"},
                "notes": {"type": "string", "description": "Brief justification for ratings"},
            },
            "required": ["paper_id", "overall_risk"],
        },
    },
    {
        "name": "rate_grade_evidence",
        "description": "Rate the certainty of evidence for a specific outcome using GRADE framework. Call once per outcome/theme.",
        "parameters": {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "description": "The outcome or theme being rated"},
                "n_studies": {"type": "integer", "description": "Number of studies contributing to this outcome"},
                "study_designs": {"type": "string", "description": "Predominant study designs (e.g., 'RCT, cohort')"},
                "risk_of_bias_rating": {"type": "string", "description": "not serious / serious / very serious"},
                "inconsistency": {"type": "string", "description": "not serious / serious / very serious"},
                "indirectness": {"type": "string", "description": "not serious / serious / very serious"},
                "imprecision": {"type": "string", "description": "not serious / serious / very serious"},
                "publication_bias": {"type": "string", "description": "undetected / strongly suspected"},
                "effect_size_range": {"type": "string", "description": "Range of effect sizes (e.g., 'OR 1.2-3.4')"},
                "certainty": {"type": "string", "description": "high / moderate / low / very low"},
                "direction": {"type": "string", "description": "Direction of effect (e.g., 'positive association', 'no effect')"},
                "notes": {"type": "string", "description": "Justification for GRADE rating"},
            },
            "required": ["outcome", "certainty"],
        },
    },
    {
        "name": "get_risk_of_bias_table",
        "description": "Retrieve all risk of bias assessments for the current session as a structured table.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_grade_table",
        "description": "Retrieve all GRADE evidence ratings for the current session as a structured table.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    # ── Writing tools ───────────────────────────────────────────
    {
        "name": "write_section",
        "description": "Save a written section of the research paper. Pass the fully written text as content.",
        "parameters": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "description": "Section name: abstract, introduction, methods, results, discussion, conclusion"},
                "content": {"type": "string", "description": "The fully written section text with citations"},
                "citations": {"type": "string", "description": "JSON array of paper_ids cited in this section"},
            },
            "required": ["section", "content"],
        },
    },
    {
        "name": "get_citations",
        "description": "Get all citations for the session in BibTeX format.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    # ── Quality tools ────────────────────────────────────────────
    {
        "name": "generate_quality_audit",
        "description": "Generate a comprehensive quality scorecard for the paper. Checks word counts, citation counts, table presence, section completeness, and PRISMA data.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "generate_prisma_diagram",
        "description": "Generate a PRISMA flow diagram from search/screening data. Returns ASCII art for markdown and SVG for HTML.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "validate_all_citations",
        "description": "Scan all written sections and verify every (Author, Year) citation against the paper database. Returns integrity report.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    # ── Fulltext tools ───────────────────────────────────────────
    {
        "name": "batch_fetch_fulltext",
        "description": "Batch fetch full text from 6 sources in parallel: Semantic Scholar (500/batch), CORE (10/batch), OpenAlex (50/batch), Europe PMC (20/batch), PubMed Central (20/batch), Unpaywall (1/call). Stores results in DB.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    # ── Embedding tools ─────────────────────────────────────────
    {
        "name": "batch_embed_papers",
        "description": "Embed all un-embedded papers in the session using Gemini embedding-001. Embeds title + abstract + authors + introduction + methods + conclusion from full text. Enables semantic similarity search via search_similar.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    {
        "name": "snowball_references",
        "description": "Pull references of top-cited papers from Semantic Scholar to enrich the corpus. This performs 'snowball sampling' — finding papers cited BY your existing papers.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many top papers to snowball from (default 20)"},
                "refs_per_paper": {"type": "integer", "description": "Max references per paper (default 10)"},
            },
        },
    },

    # ── Pipeline tools ──────────────────────────────────────────
    {
        "name": "request_approval",
        "description": "Pause for human approval before proceeding to the next phase. Shows results summary and waits for user decision.",
        "parameters": {
            "type": "object",
            "properties": {
                "phase": {"type": "string", "description": "Current phase name (scout, triage, deep_read, verifier, hypothesis, brancher, critic, writer_outline, writer_draft)"},
                "summary": {"type": "string", "description": "Markdown summary of results to show the user"},
                "data": {"type": "string", "description": "JSON string of full gate data for review file"},
            },
            "required": ["phase", "summary"],
        },
    },
    {
        "name": "get_rules",
        "description": "Get active Rule Gate rules for the current session.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "track_cost",
        "description": "Track LLM token usage and cost for budget management.",
        "parameters": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name"},
                "input_tokens": {"type": "integer", "description": "Input tokens used"},
                "output_tokens": {"type": "integer", "description": "Output tokens used"},
            },
            "required": ["model", "input_tokens", "output_tokens"],
        },
    },
    {
        "name": "embed_text",
        "description": "Generate an embedding vector for text. Uses Gemini embedding API.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to embed"},
            },
            "required": ["text"],
        },
    },
]
