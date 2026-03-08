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

    # ── Novelty & gap analysis tools ────────────────────────────
    {
        "name": "score_novelty",
        "description": "Compute novelty score (0-1) for a research finding by comparing against papers in the database using word-frequency cosine similarity. 1.0 = maximally novel, 0.0 = fully redundant.",
        "parameters": {
            "type": "object",
            "properties": {
                "finding": {"type": "string", "description": "The research finding or claim to score for novelty"},
                "comparison_query": {"type": "string", "description": "Optional query to filter comparison papers (defaults to finding text)"},
            },
            "required": ["finding"],
        },
    },
    {
        "name": "identify_gaps",
        "description": "Analyze papers in the session to find knowledge gaps: underdeveloped concepts, temporal gaps (old research not revisited), and explicitly acknowledged limitations. Returns structured gap analysis.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Topic area to analyze for gaps"},
                "domain": {"type": "string", "description": "Optional research domain label"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "compute_effect_size",
        "description": "Calculate effect size metrics from reported statistics. Supports Cohen's d (from means/SDs or t-statistic), odds ratio, risk ratio, correlation r-to-d conversion, and eta-squared. Essential for JIBS-level quantitative reporting.",
        "parameters": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": ["cohens_d", "odds_ratio", "risk_ratio", "r_to_d", "eta_squared"], "description": "Effect size metric to compute"},
                "mean1": {"type": "number", "description": "Group 1 mean (for cohens_d)"},
                "mean2": {"type": "number", "description": "Group 2 mean (for cohens_d)"},
                "sd1": {"type": "number", "description": "Group 1 SD (for cohens_d)"},
                "sd2": {"type": "number", "description": "Group 2 SD (for cohens_d)"},
                "n1": {"type": "integer", "description": "Group 1 sample size"},
                "n2": {"type": "integer", "description": "Group 2 sample size"},
                "t_value": {"type": "number", "description": "T-statistic (alternative to means/SDs for cohens_d)"},
                "a": {"type": "integer", "description": "Cell a of 2x2 table (for odds_ratio/risk_ratio)"},
                "b": {"type": "integer", "description": "Cell b of 2x2 table"},
                "c": {"type": "integer", "description": "Cell c of 2x2 table"},
                "d_cell": {"type": "integer", "description": "Cell d of 2x2 table"},
                "r": {"type": "number", "description": "Correlation coefficient (for r_to_d)"},
                "ss_effect": {"type": "number", "description": "Sum of squares for effect (for eta_squared)"},
                "ss_total": {"type": "number", "description": "Total sum of squares (for eta_squared)"},
                "f_value": {"type": "number", "description": "F-statistic (alternative for eta_squared)"},
                "df_effect": {"type": "integer", "description": "Degrees of freedom for effect (for eta_squared from F)"},
                "df_error": {"type": "integer", "description": "Degrees of freedom for error (for eta_squared from F)"},
            },
            "required": ["metric"],
        },
    },
    {
        "name": "check_journal_ranking",
        "description": "Look up journal quality tier (AAA/AA/A/B/C) from a built-in ranking database of 800+ business/management/economics journals. Checks ABS, ABDC, and FT50 lists. Use to verify citation quality meets JIBS standards.",
        "parameters": {
            "type": "object",
            "properties": {
                "journal_name": {"type": "string", "description": "Journal name to look up (fuzzy matched)"},
                "doi": {"type": "string", "description": "DOI to extract journal from (alternative to journal_name)"},
            },
        },
    },

    # ── Tier 1 data tools (exchange rates, patents, WTO, transparency) ────
    {
        "name": "search_exchange_rates",
        "description": "Frankfurter API — ECB exchange rates for 30+ currencies. Modes: 'latest' (current rates), 'timeseries' (historical), 'currencies' (list available). Free, no auth.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["latest", "timeseries", "currencies"], "description": "Operation mode (default 'latest')"},
                "base": {"type": "string", "description": "Base currency ISO code (default 'USD')"},
                "symbols": {"type": "array", "items": {"type": "string"}, "description": "Target currencies, e.g. ['EUR', 'GBP', 'JPY']"},
                "start_date": {"type": "string", "description": "Start date for timeseries, e.g. '2020-01-01'"},
                "end_date": {"type": "string", "description": "End date for timeseries, e.g. '2024-01-01'"},
            },
            "required": [],
        },
    },
    {
        "name": "search_patents",
        "description": "PatentsView API — US patent data. Search by keyword or assignee. Returns patent number, title, abstract, date, assignee, inventors. Free, no auth.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for patent abstracts"},
                "assignee": {"type": "string", "description": "Company/organization name to search"},
                "start_date": {"type": "string", "description": "Patents filed after this date (default '2018-01-01')"},
                "limit": {"type": "integer", "description": "Max results (default 25)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_wto",
        "description": "WTO Timeseries API — trade statistics, tariffs, services trade. Modes: 'search' (find indicators), 'data' (get time series). Free, no auth.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["search", "data"], "description": "Operation mode (default 'search')"},
                "query": {"type": "string", "description": "Search query for indicators"},
                "indicator": {"type": "string", "description": "WTO indicator code for data mode"},
                "reporters": {"type": "array", "items": {"type": "string"}, "description": "Reporter country codes"},
                "start_year": {"type": "integer", "description": "Start year (default 2015)"},
                "end_year": {"type": "integer", "description": "End year (default 2023)"},
                "limit": {"type": "integer", "description": "Max search results (default 15)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_transparency",
        "description": "Transparency International CPI — Corruption Perceptions Index. Returns CPI scores (0-100) by country. Higher = less corrupt. Essential for institutional quality research.",
        "parameters": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "CPI year (default 2023)"},
                "countries": {"type": "array", "items": {"type": "string"}, "description": "ISO3 codes to filter (default: all)"},
            },
            "required": [],
        },
    },
    # ── Tier 2 data tools (SEC, UN SDG, WHO, ILO, Air Quality) ──
    {
        "name": "search_sec_edgar",
        "description": "SEC EDGAR — search US company filings (10-K, 10-Q, 8-K). Returns filing metadata and links. Free, no auth.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Full-text search query"},
                "company": {"type": "string", "description": "Company name to search"},
                "filing_type": {"type": "string", "description": "Filing type: 10-K, 10-Q, 8-K, DEF 14A"},
                "start_date": {"type": "string", "description": "Start date (default '2020-01-01')"},
                "limit": {"type": "integer", "description": "Max results (default 10)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_un_sdg",
        "description": "UN SDG API — Sustainable Development Goals indicators and data. Modes: 'goals' (list 17 SDGs), 'search' (find indicators), 'data' (get measurements). Free, no auth.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["goals", "search", "data"], "description": "Operation mode (default 'search')"},
                "query": {"type": "string", "description": "Search query for indicators"},
                "goal": {"type": "integer", "description": "SDG goal number (1-17) to filter"},
                "indicator": {"type": "string", "description": "Indicator code for data mode"},
                "countries": {"type": "array", "items": {"type": "string"}, "description": "Country codes for data mode"},
                "start_year": {"type": "integer", "description": "Start year (default 2015)"},
                "end_year": {"type": "integer", "description": "End year (default 2023)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_who",
        "description": "WHO Global Health Observatory API — health indicators (life expectancy, disease burden, health spending). Modes: 'search', 'data'. Free, no auth.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["search", "data"], "description": "Operation mode (default 'search')"},
                "query": {"type": "string", "description": "Search query for indicators"},
                "indicator": {"type": "string", "description": "WHO indicator code for data mode"},
                "countries": {"type": "array", "items": {"type": "string"}, "description": "ISO3 country codes"},
                "limit": {"type": "integer", "description": "Max search results (default 15)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_ilo",
        "description": "ILO STAT API — labour statistics (employment, wages, working conditions, social protection). Modes: 'search' (find dataflows), 'data' (get observations). Free, no auth.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["search", "data"], "description": "Operation mode (default 'search')"},
                "query": {"type": "string", "description": "Search query for dataflows"},
                "dataflow": {"type": "string", "description": "ILO dataflow ID for data mode"},
                "countries": {"type": "array", "items": {"type": "string"}, "description": "ISO3 country codes"},
                "start_year": {"type": "integer", "description": "Start year (default 2015)"},
                "end_year": {"type": "integer", "description": "End year (default 2023)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_air_quality",
        "description": "OpenAQ API — global air quality data (PM2.5, PM10, NO2, O3, SO2, CO). Modes: 'latest' (current readings), 'countries' (coverage list). Free, no auth.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["latest", "countries"], "description": "Operation mode (default 'latest')"},
                "country": {"type": "string", "description": "ISO2 country code"},
                "city": {"type": "string", "description": "City name"},
                "parameter": {"type": "string", "description": "Pollutant: pm25, pm10, no2, o3, so2, co (default 'pm25')"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": [],
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
    # ── Economic Data tools (7 IFC sources) ──────────────────────────
    {
        "name": "search_world_bank",
        "description": "World Bank Open Data API. Search 16,000+ economic indicators across 200+ countries. Modes: 'search' (find indicators), 'data' (get time series), 'snapshot' (quick country comparison).",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["search", "data", "snapshot"], "description": "Operation mode (default 'search')"},
                "query": {"type": "string", "description": "Search query for indicator mode"},
                "indicator_id": {"type": "string", "description": "World Bank indicator code, e.g. 'NY.GDP.PCAP.CD'"},
                "countries": {"type": "array", "items": {"type": "string"}, "description": "ISO2 codes or ['all'] (default ['all'])"},
                "start_year": {"type": "integer", "description": "Start year (default 2015)"},
                "end_year": {"type": "integer", "description": "End year (default 2023)"},
                "year": {"type": "integer", "description": "For snapshot mode (default 2022)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_fred",
        "description": "FRED (Federal Reserve Economic Data) API. Access 816,000+ US economic time series. Modes: 'search' (find series), 'data' (get observations with stats). Requires FRED_API_KEY env var.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["search", "data"], "description": "Operation mode (default 'search')"},
                "query": {"type": "string", "description": "Search query for series"},
                "series_id": {"type": "string", "description": "FRED series ID, e.g. 'GDP', 'UNRATE'"},
                "start_date": {"type": "string", "description": "Start date ISO format, e.g. '2015-01-01' (default '2015-01-01')"},
                "end_date": {"type": "string", "description": "End date ISO format, e.g. '2023-12-31' (default '2023-12-31')"},
                "limit": {"type": "integer", "description": "Max search results (default 10)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_imf",
        "description": "IMF DataMapper API. Access 133 macroeconomic indicators across 241 countries. Modes: 'search' (find indicators), 'data' (get time series). No auth required.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["search", "data"], "description": "Operation mode (default 'search')"},
                "query": {"type": "string", "description": "Search query for indicators"},
                "indicator_id": {"type": "string", "description": "IMF indicator code, e.g. 'NGDP_RPCH' (GDP growth), 'PCPIPCH' (inflation)"},
                "countries": {"type": "array", "items": {"type": "string"}, "description": "ISO3 country codes, e.g. ['USA', 'CHN']"},
                "start_year": {"type": "integer", "description": "Start year (default 2015)"},
                "end_year": {"type": "integer", "description": "End year (default 2023)"},
                "limit": {"type": "integer", "description": "Max search results (default 15)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_oecd",
        "description": "OECD SDMX API. Access 1,475+ datasets on FDI, trade, digital economy, STRI. Modes: 'list' (show curated datasets), 'search' (find dataflows), 'data' (query dataset).",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["list", "search", "data"], "description": "Operation mode (default 'list')"},
                "query": {"type": "string", "description": "Search query for dataflows"},
                "dataset_key": {"type": "string", "description": "Curated dataset key, e.g. 'fdi_flows', 'stri', 'digital_trade'"},
                "countries": {"type": "array", "items": {"type": "string"}, "description": "ISO3 country codes"},
                "start_period": {"type": "string", "description": "Start year, e.g. '2018' (default '2018')"},
                "end_period": {"type": "string", "description": "End year, e.g. '2023' (default '2023')"},
                "limit": {"type": "integer", "description": "Max search results (default 10)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_comtrade",
        "description": "UN Comtrade API. Bilateral trade flows between countries. Supports 30 major economies. Returns export/import values by commodity.",
        "parameters": {
            "type": "object",
            "properties": {
                "reporter": {"type": "string", "description": "ISO3 code of reporting country (required), e.g. 'USA'"},
                "partners": {"type": "array", "items": {"type": "string"}, "description": "ISO3 codes of partner countries, optional"},
                "flow": {"type": "string", "description": "Trade flow: 'X' (exports), 'M' (imports), 'X,M' (both, default)"},
                "year": {"type": "integer", "description": "Year (default 2022)"},
                "commodity": {"type": "string", "description": "HS code or 'TOTAL' for aggregate (default 'TOTAL')"},
            },
            "required": ["reporter"],
        },
    },
    {
        "name": "search_eurostat",
        "description": "Eurostat API. European Union economic, trade, and digital statistics. Modes: 'list' (show datasets), 'data' (query dataset).",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["list", "data"], "description": "Operation mode (default 'list')"},
                "dataset_key": {"type": "string", "description": "Curated dataset key, e.g. 'gdp', 'inflation', 'trade_goods'"},
                "countries": {"type": "array", "items": {"type": "string"}, "description": "Eurostat geo codes, e.g. ['DE', 'FR']. Default: 8 major EU countries"},
                "start_year": {"type": "integer", "description": "Start year (default 2018)"},
                "end_year": {"type": "integer", "description": "End year (default 2023)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_countries",
        "description": "REST Countries API. Get country metadata by ISO code or region. Returns population, currencies, languages, capital, borders, Gini coefficient.",
        "parameters": {
            "type": "object",
            "properties": {
                "codes": {"type": "array", "items": {"type": "string"}, "description": "ISO2 or ISO3 country codes, e.g. ['USA', 'CHN']"},
                "region": {"type": "string", "enum": ["Africa", "Americas", "Asia", "Europe", "Oceania"], "description": "Get all countries in region"},
            },
            "required": [],
        },
    },
    # ── Analysis power tools (10 evidence synthesis & QA tools) ──
    {
        "name": "detect_contradictions",
        "description": "Find conflicting claims in the evidence base. Identifies claim pairs with opposing effect directions on the same topic. Use in Brancher (contradiction map), Hypothesis (contradiction map), and Synthesis (tension documentation).",
        "parameters": {
            "type": "object",
            "properties": {
                "theme": {"type": "string", "description": "Optional theme to filter claims (e.g., 'trust', 'adoption')"},
                "min_confidence": {"type": "number", "description": "Minimum claim confidence to include (default 0.5)"},
            },
            "required": [],
        },
    },
    {
        "name": "build_citation_network",
        "description": "Analyze citation patterns: co-citation clusters, seminal papers, bridge papers, citation concentration risk. Use in Synthesis (citation allocation) and Paper Critic (citation diversity audit).",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "classify_methodology",
        "description": "Auto-classify papers by research methodology (RCT, cohort, cross-sectional, qualitative, mixed-methods, etc.) using abstract/claim keyword analysis. Returns distribution and diversity index.",
        "parameters": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "integer", "description": "Classify a single paper (optional — omit for batch)"},
                "batch": {"type": "boolean", "description": "Classify all papers (default true)"},
            },
            "required": [],
        },
    },
    {
        "name": "aggregate_samples",
        "description": "Aggregate sample sizes, geographies, and populations across the evidence base. Returns total N, regional distribution, WEIRD bias %, sample size statistics.",
        "parameters": {
            "type": "object",
            "properties": {
                "theme": {"type": "string", "description": "Optional theme to filter claims"},
            },
            "required": [],
        },
    },
    {
        "name": "meta_analyze",
        "description": "Run meta-analysis on extracted effect sizes: inverse-variance weighted pooled estimate, I² heterogeneity, Q statistic, Egger's publication bias test, forest plot data. Requires >= 2 claims with numeric effect sizes.",
        "parameters": {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "description": "Outcome theme to filter claims (e.g., 'financial inclusion')"},
                "metric": {"type": "string", "enum": ["auto", "cohens_d", "odds_ratio", "correlation"], "description": "Effect size metric (default 'auto')"},
            },
            "required": [],
        },
    },
    {
        "name": "map_theories",
        "description": "Extract and map theoretical frameworks (institutional theory, RBV, TAM, agency theory, etc.) used across the corpus. Returns theory-paper mapping, co-occurrence, and underused theories.",
        "parameters": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "integer", "description": "Map theories for a single paper (optional)"},
            },
            "required": [],
        },
    },
    {
        "name": "analyze_temporal_trends",
        "description": "Analyze publication trends, method evolution, and finding consistency over time. Returns timeline, recency stats, and method shifts between early and recent periods.",
        "parameters": {
            "type": "object",
            "properties": {
                "theme": {"type": "string", "description": "Optional theme to filter analysis"},
            },
            "required": [],
        },
    },
    {
        "name": "generate_evidence_table",
        "description": "Auto-generate structured evidence tables from DB data. Types: 'study_characteristics' (author, year, design, N, finding), 'grade_summary' (GRADE assessment), 'rob_assessment' (risk of bias), 'effect_sizes' (all reported effects).",
        "parameters": {
            "type": "object",
            "properties": {
                "table_type": {"type": "string", "enum": ["study_characteristics", "grade_summary", "rob_assessment", "effect_sizes"], "description": "Type of table to generate"},
            },
            "required": ["table_type"],
        },
    },
    {
        "name": "check_claim_consistency",
        "description": "Cross-check written section text against actual DB claims. Detects phantom citations (not in DB), overclaiming patterns ('proves', 'all evidence'), and ungrounded assertions. Use in Paper Critic and Writer phases.",
        "parameters": {
            "type": "object",
            "properties": {
                "section_text": {"type": "string", "description": "The written section text to check"},
                "section_name": {"type": "string", "description": "Section name (e.g., 'introduction', 'discussion')"},
            },
            "required": ["section_text"],
        },
    },
    {
        "name": "compute_kappa",
        "description": "Compute inter-rater reliability (Cohen's kappa) for RoB assessments or triage ratings. For RoB: compares individual bias dimensions against overall rating. Reports kappa, agreement, and interpretation.",
        "parameters": {
            "type": "object",
            "properties": {
                "assessment_type": {"type": "string", "enum": ["risk_of_bias", "triage"], "description": "Type of assessment to evaluate (default 'risk_of_bias')"},
            },
            "required": [],
        },
    },
    # ── Analysis power tools batch 2 (5 causal/quality tools) ──
    {
        "name": "extract_causal_chains",
        "description": "Extract causal mechanisms (X → M → Y) from claims using NLP pattern matching. Returns directed causal graph, mechanism types (mediator/moderator/antecedent/feedback), and construct role mapping. MANDATORY in Synthesis for causal model building.",
        "parameters": {
            "type": "object",
            "properties": {
                "theme": {"type": "string", "description": "Optional theme to filter claims (e.g., 'innovation', 'trust')"},
            },
            "required": [],
        },
    },
    {
        "name": "find_natural_experiments",
        "description": "Identify papers with causal identification strategies (RCT, natural experiment, DiD, IV, RDD, PSM, panel fixed effects). Returns papers ranked by causal inference strength (1-5) with strategy evidence. MANDATORY in Critic and Synthesis for evidence weighting.",
        "parameters": {
            "type": "object",
            "properties": {
                "min_strength": {"type": "integer", "description": "Minimum causal strength tier to include (0-5, default 0 = all)"},
            },
            "required": [],
        },
    },
    {
        "name": "score_construct_consistency",
        "description": "Check if key constructs are defined and used consistently across the corpus. Auto-detects key constructs or checks a specific one. Returns consistency scores, conflicting definitions, and usage samples. MANDATORY in Hypothesis and Synthesis for construct clarity.",
        "parameters": {
            "type": "object",
            "properties": {
                "construct": {"type": "string", "description": "Specific construct to check (e.g., 'institutional distance'). Omit for auto-detection."},
                "auto_detect": {"type": "boolean", "description": "Auto-detect key constructs from corpus (default true if no construct specified)"},
            },
            "required": [],
        },
    },
    {
        "name": "measure_argument_density",
        "description": "Measure citations-per-paragraph, data-points-per-section, and detect thin/filler passages. Returns paragraph-level analysis with flags for uncited passages and padding language. MANDATORY in Paper Critic for structural quality gate.",
        "parameters": {
            "type": "object",
            "properties": {
                "section_text": {"type": "string", "description": "The written section text to analyze"},
                "section_name": {"type": "string", "description": "Section name for density target lookup (e.g., 'literature_review', 'discussion')"},
            },
            "required": ["section_text"],
        },
    },
    {
        "name": "predict_reviewer_objections",
        "description": "Generate likely R1 reviewer objections based on evidence base analysis. Checks sample sizes, geographic coverage, methodological balance, evidence quality, recency, and journal-specific requirements. Returns objections ranked by severity with pre-built defenses.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Research topic (auto-detected from session if omitted)"},
                "target_journal": {"type": "string", "description": "Target journal name (e.g., 'Research Policy', 'Strategic Management Journal')"},
            },
            "required": [],
        },
    },
    # ── Migration & Innovation Arbitrage tools ────────────────────────
    {
        "name": "search_unhcr",
        "description": "UNHCR Population Statistics API — refugee, asylum seeker, IDP, and stateless populations by country of origin, country of asylum, and year. Supports bilateral queries (origin→asylum country pairs), timeseries, and country lookup. Free, no auth required.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["population", "timeseries", "countries"], "description": "Query mode: 'population' for snapshot data, 'timeseries' for trends over time, 'countries' to list available countries with ISO codes"},
                "country_of_origin": {"type": "string", "description": "ISO-3 code of origin country (e.g., 'SYR', 'AFG', 'UKR')"},
                "country_of_asylum": {"type": "string", "description": "ISO-3 code of asylum/host country (e.g., 'DEU', 'USA', 'TUR')"},
                "year": {"type": "integer", "description": "Single year filter (e.g., 2023)"},
                "year_from": {"type": "integer", "description": "Start year for range (e.g., 2000)"},
                "year_to": {"type": "integer", "description": "End year for range (e.g., 2023)"},
                "query": {"type": "string", "description": "Country name search (for mode=countries only)"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_oecd_migration",
        "description": "OECD migration statistics — standardised inflows of permanent/temporary migrants by category (work, family, humanitarian, free movement), and foreign-born population stocks. Covers 30+ OECD countries. Free, no auth.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["inflows", "stocks", "datasets"], "description": "Query mode: 'inflows' for migration flows by category, 'stocks' for foreign-born population, 'datasets' to list available datasets"},
                "country": {"type": "string", "description": "ISO-3 country code (e.g., 'USA', 'DEU', 'SWE')"},
                "year_from": {"type": "integer", "description": "Start year (default 2015)"},
                "year_to": {"type": "integer", "description": "End year (default 2023)"},
                "migration_type": {"type": "string", "description": "Filter by type: WO (work), FA (family), HU (humanitarian), FR (free movement), _T (total)"},
                "dataset": {"type": "string", "enum": ["permanent", "temporary"], "description": "Dataset: 'permanent' or 'temporary' migrants (default: permanent)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_wipo_ip",
        "description": "Patent and IP statistics by country — resident vs nonresident patent filings (proxy for immigrant inventor contribution), R&D expenditure, scientific output. Computes nonresident patent share automatically. Uses World Bank / WIPO data. Free, no auth.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["patents", "compare", "indicators"], "description": "Query mode: 'patents' for single country timeseries, 'compare' for cross-country comparison, 'indicators' to list available metrics"},
                "country": {"type": "string", "description": "ISO-3 country code for mode=patents (e.g., 'USA', 'DEU')"},
                "countries": {"type": "string", "description": "Comma-separated ISO-3 codes for mode=compare (e.g., 'USA,DEU,CHN,IND')"},
                "year_from": {"type": "integer", "description": "Start year (default 2010)"},
                "year_to": {"type": "integer", "description": "End year (default 2022)"},
                "year": {"type": "integer", "description": "Single year for mode=compare (default 2021)"},
                "indicator": {"type": "string", "description": "Specific indicator code for mode=compare (default: IP.PAT.RESD)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_global_innovation_index",
        "description": "Global Innovation Index (GII) 2023 scores and 7 sub-pillars (institutions, human capital, infrastructure, market sophistication, business sophistication, knowledge output, creative output) for ~45 countries. Supports gap analysis between origin-host pairs — critical for measuring institutional arbitrage potential. Embedded data, instant lookup.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["lookup", "compare", "gap", "ranking"], "description": "Query mode: 'lookup' single country, 'compare' multiple, 'gap' origin-host difference, 'ranking' full list"},
                "country": {"type": "string", "description": "ISO-3 code for mode=lookup (e.g., 'SYR', 'USA')"},
                "countries": {"type": "string", "description": "Comma-separated ISO-3 codes for mode=compare"},
                "origin": {"type": "string", "description": "Origin country ISO-3 for mode=gap (e.g., 'SYR')"},
                "host": {"type": "string", "description": "Host country ISO-3 for mode=gap (e.g., 'DEU')"},
                "limit": {"type": "integer", "description": "Number of countries for mode=ranking (default 45)"},
            },
            "required": [],
        },
    },
    {
        "name": "compute_institutional_distance",
        "description": "Compute Kogut-Singh institutional distance between two countries using World Governance Indicators (WGI) and Hofstede cultural dimensions. Returns composite distance score, dimension-level breakdown, and arbitrage potential interpretation. Embedded data for ~47 countries (WGI) and ~46 countries (Hofstede). Instant computation.",
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Origin country ISO-3 code (e.g., 'SYR', 'IND')"},
                "host": {"type": "string", "description": "Host country ISO-3 code (e.g., 'USA', 'DEU')"},
                "include_cultural": {"type": "boolean", "description": "Include Hofstede cultural dimensions (default true)"},
            },
            "required": ["origin", "host"],
        },
    },
    {
        "name": "compute_brain_drain_index",
        "description": "Compute brain drain severity index (0-100) for a country using World Bank indicators: tertiary emigration rate, R&D expenditure, researchers per million, patents, journal articles, high-tech exports. Optionally compare origin vs host to quantify innovation gaps that immigrants bridge.",
        "parameters": {
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "Country ISO-3 code to analyze (e.g., 'IND', 'NGA', 'SYR')"},
                "compare_to": {"type": "string", "description": "Optional host country ISO-3 for gap analysis (e.g., 'USA', 'DEU')"},
            },
            "required": ["country"],
        },
    },
    {
        "name": "search_bilateral_remittances",
        "description": "World Bank bilateral remittance data. Mode 'indicators': remittances received/paid (USD, %GDP), migrant stock, net migration for a country. Mode 'corridors': top global remittance corridors (embedded 2023 data) filterable by country. Critical for quantifying knowledge-transfer financial flows.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["indicators", "corridors"], "description": "Query mode (default: indicators)"},
                "country": {"type": "string", "description": "ISO-3 code (e.g., 'IND', 'MEX')"},
                "limit": {"type": "integer", "description": "Max corridors to return (default 20, mode=corridors only)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_coauthorship_network",
        "description": "Cross-border co-authorship analysis via OpenAlex. Mode 'bilateral': yearly co-publication counts between two countries with growth rate. Mode 'top_partners': ranked list of co-authorship partner countries. Filterable by topic and year range. Measures knowledge flow via scientific collaboration.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["bilateral", "top_partners"], "description": "Query mode (default: bilateral)"},
                "country1": {"type": "string", "description": "First country ISO-2 code (e.g., 'US', 'IN')"},
                "country2": {"type": "string", "description": "Second country ISO-2 for bilateral mode"},
                "topic": {"type": "string", "description": "Optional topic filter (e.g., 'innovation', 'entrepreneurship')"},
                "year_from": {"type": "integer", "description": "Start year (default 2015)"},
                "year_to": {"type": "integer", "description": "End year (default 2024)"},
                "limit": {"type": "integer", "description": "Max partners for top_partners mode (default 20)"},
            },
            "required": [],
        },
    },
    {
        "name": "compute_arbitrage_spread",
        "description": "Compute innovation arbitrage spread between origin and host countries. Combines WGI institutional gap (regulatory quality, rule of law, government effectiveness, corruption control) with GII innovation gap (7 pillars). Returns normalized 0-1 spread score and identifies largest pillar opportunity. Core metric for 'immigration as innovation arbitrage' thesis.",
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Origin country ISO-3 code (e.g., 'IND', 'NGA')"},
                "host": {"type": "string", "description": "Host country ISO-3 code (e.g., 'USA', 'DEU')"},
            },
            "required": ["origin", "host"],
        },
    },
    {
        "name": "compute_convergence_window",
        "description": "Track institutional convergence/divergence between origin and host countries over 20 years (2003-2023) using World Bank WGI time series. Shows whether institutional gaps are opening (increasing arbitrage value) or closing (diminishing window). Returns per-dimension trends and overall convergence score. Live API data.",
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Origin country ISO-3 code (e.g., 'IND', 'CHN')"},
                "host": {"type": "string", "description": "Host country ISO-3 code (e.g., 'USA', 'GBR')"},
            },
            "required": ["origin", "host"],
        },
    },
    {
        "name": "compute_reverse_knowledge_flow",
        "description": "Measure reverse knowledge flow between origin and host countries via OpenAlex co-publication analysis. Returns: total co-publications, yearly trends, co-publication intensity (% of each country's output), asymmetry ratio, and growth trajectory. Quantifies the knowledge bridge immigrants create.",
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Origin country ISO-2 code (e.g., 'IN', 'NG')"},
                "host": {"type": "string", "description": "Host country ISO-2 code (e.g., 'US', 'DE')"},
                "topic": {"type": "string", "description": "Optional topic filter (e.g., 'innovation')"},
                "year_from": {"type": "integer", "description": "Start year (default 2010)"},
                "year_to": {"type": "integer", "description": "End year (default 2024)"},
            },
            "required": ["origin", "host"],
        },
    },
    {
        "name": "search_visa_policy",
        "description": "Visa policy and migration openness data. Mode 'lookup': passport index + talent openness for a country. Mode 'compare': side-by-side comparison of multiple countries. Mode 'corridor': origin-host mobility gap analysis. Mode 'ranking': global talent openness ranking. Combines Henley Passport Index 2024 + OECD Talent Attractiveness scores. Embedded data, instant lookup.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["lookup", "compare", "corridor", "ranking"], "description": "Query mode (default: lookup)"},
                "country": {"type": "string", "description": "ISO-3 code for mode=lookup"},
                "countries": {"type": "string", "description": "Comma-separated ISO-3 codes for mode=compare"},
                "origin": {"type": "string", "description": "Origin ISO-3 for mode=corridor"},
                "host": {"type": "string", "description": "Host ISO-3 for mode=corridor"},
                "limit": {"type": "integer", "description": "Max results for mode=ranking (default 25)"},
            },
            "required": [],
        },
    },
]
