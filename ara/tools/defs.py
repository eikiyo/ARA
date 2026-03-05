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

    # ── Paper tools ─────────────────────────────────────────────
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
        "description": "Read a paper's metadata, abstract, and full text (if cached) from the local database.",
        "parameters": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "integer", "description": "Paper ID from database"},
            },
            "required": ["paper_id"],
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
                    "description": "List of extracted claims to store. Each: {claim_text, claim_type, confidence, supporting_quotes, section}",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim_text": {"type": "string"},
                            "claim_type": {"type": "string", "description": "finding/method/limitation/gap"},
                            "confidence": {"type": "number"},
                            "supporting_quotes": {"type": "array", "items": {"type": "string"}},
                            "section": {"type": "string"},
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
