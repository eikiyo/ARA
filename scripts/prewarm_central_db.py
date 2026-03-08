# Location: scripts/prewarm_central_db.py
# Purpose: One-time script to pre-populate central DB with ~1000 papers across core research domains
# Functions: search, fetch fulltext, embed papers, extract claims, embed claims
# Calls: ara/tools/search.py, ara/central_db.py, google.genai
# Imports: json, time, logging, concurrent.futures, httpx

"""
Pre-warm ARA Central DB
=======================
Searches 6 topic clusters across multiple academic APIs, stores papers in central DB,
fetches full texts, generates embeddings, and extracts + embeds claims.

Usage:
    python scripts/prewarm_central_db.py

Requires:
    GOOGLE_API_KEY env var set (for embeddings + claim extraction via Gemini)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import threading
import concurrent.futures
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ara.central_db import CentralDB
from ara.tools.search import (
    search_openalex,
    search_semantic_scholar,
    search_crossref,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
_log = logging.getLogger("prewarm")

# ── Topic clusters ──────────────────────────────────────────────────────

TOPIC_CLUSTERS = {
    "fintech": [
        "fintech digital lending platform",
        "mobile payments financial inclusion",
        "neobank digital banking disruption",
        "regtech regulatory technology compliance",
        "open banking API financial services",
        "fintech adoption emerging markets",
        "peer-to-peer lending crowdfunding",
        "digital payment systems Southeast Asia",
        "fintech innovation financial intermediation",
        "blockchain decentralized finance DeFi",
    ],
    "entrepreneurship": [
        "entrepreneurial finance venture capital",
        "startup ecosystem innovation hub",
        "SME growth entrepreneurship developing countries",
        "entrepreneurial orientation firm performance",
        "social entrepreneurship impact investing",
        "technology entrepreneurship born-global firms",
        "entrepreneurship institutional environment",
        "corporate entrepreneurship intrapreneurship",
        "entrepreneurial intention theory planned behavior",
        "female entrepreneurship gender gap",
    ],
    "innovation": [
        "disruptive innovation technology adoption",
        "platform innovation digital ecosystem",
        "innovation ecosystem knowledge spillover",
        "open innovation collaboration R&D",
        "frugal innovation emerging economy",
        "digital transformation organizational innovation",
        "absorptive capacity innovation performance",
        "technology transfer innovation system",
        "sustainable innovation green technology",
        "innovation diffusion Rogers adoption",
    ],
    "ai_debt": [
        "artificial intelligence credit scoring",
        "machine learning lending decision",
        "AI risk assessment debt market",
        "algorithmic lending bias fairness",
        "NLP financial text analysis credit",
        "deep learning default prediction",
        "automated underwriting mortgage AI",
        "AI fintech credit risk management",
        "robo-advisor debt portfolio management",
        "explainable AI financial regulation",
    ],
    "debt_markets": [
        "corporate debt structure capital",
        "sovereign debt crisis restructuring",
        "bond market liquidity pricing",
        "credit market financial stability",
        "debt maturity structure firm",
        "financial crisis contagion banking",
        "microfinance debt developing countries",
        "green bond sustainable debt finance",
        "household debt financial vulnerability",
        "debt overhang investment firm",
    ],
    "nordic_swedish": [
        "Swedish fintech ecosystem Klarna",
        "Nordic banking digital transformation",
        "Scandinavian financial regulation innovation",
        "Nordic venture capital startup",
        "Swedish entrepreneurship innovation system",
        "Nordic welfare state financial inclusion",
        "Scandinavian corporate governance",
        "Swedish SME financing growth",
        "Nordic sustainable finance ESG",
        "Baltic fintech cross-border payments",
    ],
}

# ── Config ──────────────────────────────────────────────────────────────

TARGET_PER_CLUSTER = 300  # maximize coverage per cluster
PAPERS_PER_QUERY = 30     # papers per individual query
FROM_YEAR = 2014
MAX_FULLTEXT_CHARS = 80_000
EMBED_BATCH_SIZE = 20     # papers per embedding batch
CLAIM_EXTRACT_BATCH = 10  # papers per claim extraction batch


def _download_pdf_text(url: str) -> str | None:
    """Download a PDF/HTML link and extract text content."""
    import httpx
    try:
        resp = httpx.get(url, timeout=25, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ARA-Research/1.0)"
        })
        if resp.status_code != 200:
            return None

        content_type = resp.headers.get("content-type", "").lower()

        # HTML content — save as-is (strip tags later if needed)
        if "html" in content_type or "text" in content_type:
            text = resp.text[:MAX_FULLTEXT_CHARS]
            if len(text) > 500:
                return text

        # PDF content — try to extract text
        if "pdf" in content_type or url.lower().endswith(".pdf"):
            try:
                import io
                import subprocess
                # Try pdftotext (poppler) first
                result = subprocess.run(
                    ["pdftotext", "-", "-"],
                    input=resp.content, capture_output=True, timeout=30,
                )
                if result.returncode == 0:
                    text = result.stdout.decode("utf-8", errors="replace")[:MAX_FULLTEXT_CHARS]
                    if len(text) > 500:
                        return text
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            # Fallback: store raw PDF bytes won't help, skip
            return None

    except Exception:
        pass
    return None


def _search_serpapi_scholar(
    query: str, central_db: Any, limit: int = 20, from_year: int = 2014,
) -> list[dict]:
    """Search Google Scholar via SerpAPI. Downloads PDFs inline — zero wasted API calls.

    Returns papers with full_text populated when PDF links are available.
    Saves EVERYTHING SerpAPI returns: authors, citation counts, PDF links, snippets.
    """
    api_key = os.getenv("SERPAPI_API_KEY", "")
    if not api_key:
        return []

    import re as _re
    import httpx
    papers = []
    num_pages = min(limit // 10, 3)  # 3 pages = 30 results per query

    for page in range(num_pages):
        try:
            resp = httpx.get(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google_scholar",
                    "q": query,
                    "api_key": api_key,
                    "start": page * 10,
                    "num": 10,
                    "as_ylo": from_year,
                },
                timeout=20,
            )
            if resp.status_code != 200:
                _log.warning("  SerpAPI HTTP %d on page %d", resp.status_code, page)
                break
            data = resp.json()

            for item in data.get("organic_results", []):
                title = item.get("title", "")
                if not title:
                    continue
                snippet = item.get("snippet", "")

                # Extract structured authors
                pub_info = item.get("publication_info", {})
                authors = []
                for author_obj in pub_info.get("authors", []):
                    name = author_obj.get("name", "")
                    if name:
                        authors.append(name)
                # Fallback: parse from summary string
                if not authors and pub_info.get("summary"):
                    parts = pub_info["summary"].split(" - ")
                    if parts:
                        authors = [a.strip() for a in parts[0].split(",")
                                   if a.strip() and not a.strip().isdigit() and len(a.strip()) > 1]

                # Year
                year = None
                summary = pub_info.get("summary", "")
                year_match = _re.search(r'(20[012]\d|199\d)', summary)
                if year_match:
                    year = int(year_match.group())

                # DOI — check link and resources
                doi = None
                link = item.get("link", "")
                if "doi.org/" in link:
                    doi = link.split("doi.org/")[-1]

                # Citation count
                citation_count = 0
                cited_by = item.get("inline_links", {}).get("cited_by", {})
                if isinstance(cited_by, dict):
                    total = cited_by.get("total")
                    if isinstance(total, int):
                        citation_count = total
                    elif isinstance(total, str):
                        cc_match = _re.search(r'(\d+)', total)
                        if cc_match:
                            citation_count = int(cc_match.group(1))

                # PDF resources — download IMMEDIATELY
                full_text = None
                pdf_urls = []
                for res in item.get("resources", []):
                    res_link = res.get("link", "")
                    if res_link:
                        pdf_urls.append(res_link)

                # Also try the main link if it looks like a direct paper page
                if link and not any(x in link for x in ["scholar.google", "google.com/scholar"]):
                    pdf_urls.append(link)

                # Try downloading full text from each URL
                for pdf_url in pdf_urls:
                    text = _download_pdf_text(pdf_url)
                    if text:
                        full_text = text
                        _log.info("    Got fulltext from %s (%d chars)", pdf_url[:60], len(text))
                        break

                paper = {
                    "title": title,
                    "abstract": snippet,
                    "authors": authors,
                    "year": year,
                    "doi": doi,
                    "source": "serpapi_scholar",
                    "url": link,
                    "citation_count": citation_count,
                }

                # Store paper + fulltext in central DB immediately (don't lose anything)
                try:
                    result = central_db.store_papers([paper])
                    if full_text:
                        # Get the central paper_id to store fulltext
                        from ara.central_db import _title_hash
                        t_hash = _title_hash(title)
                        row = central_db._conn.execute(
                            "SELECT paper_id FROM papers WHERE title_hash = ?", (t_hash,)
                        ).fetchone()
                        if row:
                            central_db.store_fulltext(row["paper_id"], full_text)
                except Exception as exc:
                    _log.debug("  SerpAPI store failed: %s", exc)

                papers.append(paper)

        except Exception as exc:
            _log.warning("SerpAPI page %d failed: %s", page, exc)
            break
        time.sleep(1.5)  # Rate limit between pages

    return papers


def _search_openalex_direct(query: str, limit: int = 30, from_year: int = 2014) -> list[dict]:
    """Search OpenAlex directly without session context."""
    result_str = search_openalex(
        {"query": query, "limit": limit, "from_year": from_year},
        {},  # no session context needed
    )
    data = json.loads(result_str)
    return data.get("papers", [])


def _search_crossref_direct(query: str, limit: int = 20) -> list[dict]:
    """Search Crossref directly."""
    result_str = search_crossref(
        {"query": query, "limit": limit},
        {},
    )
    data = json.loads(result_str)
    return data.get("papers", [])


from ara.tools.fulltext import (
    _fetch_s2_batch, _fetch_openalex_batch, _fetch_epmc_batch,
    _fetch_pmc_batch, _fetch_unpaywall_batch, _fetch_crossref_tdm,
    _fetch_arxiv_batch, _fetch_biorxiv_batch, _fetch_doaj_batch,
    _fetch_zenodo_batch, _fetch_hal_batch, _fetch_dblp_batch,
    _fetch_base_batch, _fetch_ia_scholar_batch, _fetch_scielo_batch,
    _fetch_figshare_batch, _fetch_doi_direct, _fetch_ssrn_batch,
    _fetch_pubmed_abstracts, _fetch_core_batch, _UNPAYWALL_EMAILS,
)


def phase_1_search(central_db: CentralDB) -> dict[str, int]:
    """Search all topic clusters and store papers in central DB."""
    _log.info("=" * 60)
    _log.info("PHASE 1: Searching academic APIs for papers")
    _log.info("=" * 60)

    stats: dict[str, int] = {}

    for cluster_name, queries in TOPIC_CLUSTERS.items():
        _log.info("--- Cluster: %s (%d queries) ---", cluster_name, len(queries))
        cluster_papers: list[dict] = []
        seen_titles: set[str] = set()

        for qi, query in enumerate(queries):
            _log.info("  [%d/%d] Searching: %s", qi + 1, len(queries), query)

            # Search OpenAlex (best coverage, free)
            try:
                papers = _search_openalex_direct(query, limit=PAPERS_PER_QUERY, from_year=FROM_YEAR)
                for p in papers:
                    title_key = p.get("title", "").strip().lower()[:100]
                    if title_key and title_key not in seen_titles:
                        seen_titles.add(title_key)
                        cluster_papers.append(p)
            except Exception as exc:
                _log.warning("  OpenAlex failed for '%s': %s", query, exc)

            # Also try Crossref for diversity
            try:
                papers = _search_crossref_direct(query, limit=15)
                for p in papers:
                    title_key = p.get("title", "").strip().lower()[:100]
                    if title_key and title_key not in seen_titles:
                        seen_titles.add(title_key)
                        cluster_papers.append(p)
            except Exception as exc:
                _log.warning("  Crossref failed for '%s': %s", query, exc)

            # SerpAPI Google Scholar — best coverage, downloads PDFs inline
            try:
                papers = _search_serpapi_scholar(query, central_db, limit=30, from_year=FROM_YEAR)
                serp_new = 0
                for p in papers:
                    title_key = p.get("title", "").strip().lower()[:100]
                    if title_key and title_key not in seen_titles:
                        seen_titles.add(title_key)
                        cluster_papers.append(p)
                        serp_new += 1
                if serp_new:
                    _log.info("    SerpAPI: +%d new papers", serp_new)
            except Exception as exc:
                _log.warning("  SerpAPI failed for '%s': %s", query, exc)

            # Rate limit between queries
            time.sleep(1.5)

            # Stop if we have enough for this cluster
            if len(cluster_papers) >= TARGET_PER_CLUSTER:
                break

        # Store in central DB
        if cluster_papers:
            result = central_db.store_papers(cluster_papers[:TARGET_PER_CLUSTER])
            stored = result.get("stored", 0)
            skipped = result.get("skipped", 0)
            stats[cluster_name] = len(cluster_papers[:TARGET_PER_CLUSTER])
            _log.info("  Cluster %s: %d papers found, %d stored, %d already existed",
                       cluster_name, len(cluster_papers), stored, skipped)

        # Cool down between clusters
        time.sleep(3)

    total = sum(stats.values())
    _log.info("PHASE 1 COMPLETE: %d papers across %d clusters", total, len(stats))
    return stats


def phase_2_fulltext(central_db: CentralDB) -> int:
    """Fetch full texts using all 20 sources in parallel (same as ARA pipeline)."""
    _log.info("=" * 60)
    _log.info("PHASE 2: Fetching full texts (20-source parallel race)")
    _log.info("=" * 60)

    # Get papers with DOIs but no full text
    rows = central_db._conn.execute(
        "SELECT paper_id, doi FROM papers WHERE doi IS NOT NULL AND full_text IS NULL "
        "ORDER BY citation_count DESC LIMIT 1200"
    ).fetchall()

    if not rows:
        _log.info("All papers already have full text or no DOIs available")
        return 0

    doi_to_pid: dict[str, int] = {}
    dois: list[str] = []
    for r in rows:
        doi = r["doi"].strip().lower()
        if doi.startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/"):]
        doi_to_pid[doi] = r["paper_id"]
        dois.append(doi)

    _log.info("Racing 20 sources for %d papers without full text", len(dois))

    # Thread-safe results
    found: dict[str, str] = {}
    found_lock = threading.Lock()
    source_stats: dict[str, int] = {}

    def _run_source(name: str, func, *func_args):
        try:
            results = func(*func_args)
            count = 0
            with found_lock:
                for doi, text in results.items():
                    doi_lower = doi.lower()
                    if doi_lower not in found and doi_lower in doi_to_pid:
                        found[doi_lower] = text
                        count += 1
                source_stats[name] = count
            _log.info("  Source %s: +%d texts (total: %d/%d)", name, count, len(found), len(dois))
        except Exception as exc:
            _log.warning("  Source %s failed: %s", name, exc)
            source_stats[name] = 0

    # Credential for CORE
    core_key = ""
    try:
        creds = json.loads(open(os.path.expanduser("~/.ara/credentials.json")).read())
        core_key = creds.get("core_api_key", "")
    except Exception:
        pass

    # Build source list — all 20 sources race in parallel
    sources = [
        ("semantic_scholar", _fetch_s2_batch, dois),
        ("openalex", _fetch_openalex_batch, dois),
        ("europe_pmc", _fetch_epmc_batch, dois),
        ("pmc", _fetch_pmc_batch, dois),
        ("unpaywall", _fetch_unpaywall_batch, dois, _UNPAYWALL_EMAILS[0]),
        ("crossref_tdm", _fetch_crossref_tdm, dois),
        ("arxiv", _fetch_arxiv_batch, dois),
        ("biorxiv", _fetch_biorxiv_batch, dois),
        ("doaj", _fetch_doaj_batch, dois),
        ("zenodo", _fetch_zenodo_batch, dois),
        ("hal", _fetch_hal_batch, dois),
        ("dblp", _fetch_dblp_batch, dois),
        ("base", _fetch_base_batch, dois),
        ("ia_scholar", _fetch_ia_scholar_batch, dois),
        ("scielo", _fetch_scielo_batch, dois),
        ("figshare", _fetch_figshare_batch, dois),
        ("doi_direct", _fetch_doi_direct, dois),
        ("ssrn", _fetch_ssrn_batch, dois),
        ("pubmed_abstracts", _fetch_pubmed_abstracts, dois),
    ]
    if core_key:
        sources.append(("core", _fetch_core_batch, dois, core_key))

    _RACE_TIMEOUT = 600  # 10 min for ~1000 DOIs

    threads = []
    for source_args in sources:
        name = source_args[0]
        func = source_args[1]
        args_rest = source_args[2:]
        t = threading.Thread(target=_run_source, args=(name, func, *args_rest), daemon=True)
        threads.append(t)

    _log.info("Launching %d sources in parallel", len(threads))
    start_time = time.monotonic()

    for t in threads:
        t.start()

    for t in threads:
        remaining_time = _RACE_TIMEOUT - (time.monotonic() - start_time)
        if remaining_time <= 0:
            break
        t.join(timeout=max(remaining_time, 1))

    alive = sum(1 for t in threads if t.is_alive())
    if alive:
        _log.warning("  %d sources still running after timeout — moving on", alive)

    # Store all found texts in central DB
    stored = 0
    for doi, text in found.items():
        pid = doi_to_pid.get(doi)
        if pid:
            try:
                central_db.store_fulltext(pid, text[:MAX_FULLTEXT_CHARS])
                stored += 1
            except Exception:
                pass

    elapsed = time.monotonic() - start_time
    _log.info("PHASE 2 COMPLETE in %.1fs: %d/%d full texts fetched", elapsed, stored, len(dois))
    _log.info("  Source breakdown: %s", json.dumps(source_stats, indent=2))
    return stored


def phase_3_embed_papers(central_db: CentralDB) -> int:
    """Generate embeddings for all un-embedded papers."""
    _log.info("=" * 60)
    _log.info("PHASE 3: Embedding papers")
    _log.info("=" * 60)

    api_key = os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        _log.error("No GOOGLE_API_KEY set — cannot embed papers")
        return 0

    from google import genai
    client = genai.Client(api_key=api_key)

    # Get un-embedded papers
    paper_ids = central_db.get_unembedded_paper_ids(limit=1200)
    if not paper_ids:
        _log.info("All papers already have embeddings")
        return 0

    _log.info("Embedding %d papers", len(paper_ids))
    embedded = 0

    for i, pid in enumerate(paper_ids):
        row = central_db._conn.execute(
            "SELECT title, abstract, authors FROM papers WHERE paper_id = ?", (pid,)
        ).fetchone()
        if not row:
            continue

        title = row["title"] or ""
        abstract = row["abstract"] or ""
        text = f"{title}. {abstract}"[:3000]
        if not text.strip():
            continue

        try:
            result = client.models.embed_content(model="gemini-embedding-001", contents=text)
            if result.embeddings and len(result.embeddings) > 0:
                central_db.store_embedding(pid, result.embeddings[0].values)
                embedded += 1
        except Exception as exc:
            _log.warning("Embedding failed for paper %d: %s", pid, exc)
            time.sleep(2)  # Back off on error

        if (i + 1) % 50 == 0:
            _log.info("  Embedded %d/%d papers", embedded, i + 1)
            time.sleep(1)

    _log.info("PHASE 3 COMPLETE: %d/%d papers embedded", embedded, len(paper_ids))
    return embedded


def phase_4_extract_claims(central_db: CentralDB) -> int:
    """Extract claims from papers with full text using Gemini."""
    _log.info("=" * 60)
    _log.info("PHASE 4: Extracting claims (topic-agnostic)")
    _log.info("=" * 60)

    api_key = os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        _log.error("No GOOGLE_API_KEY set — cannot extract claims")
        return 0

    from google import genai
    client = genai.Client(api_key=api_key)

    # Get papers with full text that haven't been fully extracted
    rows = central_db._conn.execute(
        "SELECT p.paper_id, p.title, p.abstract, p.doi, p.full_text "
        "FROM papers p "
        "WHERE p.full_text IS NOT NULL AND p.full_text != '' "
        "AND p.paper_id NOT IN ("
        "  SELECT DISTINCT c.paper_title_hash FROM claims c WHERE c.fully_extracted = 1"
        ") "
        "ORDER BY p.citation_count DESC LIMIT 500"
    ).fetchall()

    # Filter out papers already fully extracted (by title hash)
    papers_to_extract = []
    for row in rows:
        title = row["title"] or ""
        if not central_db.is_paper_fully_extracted(title):
            papers_to_extract.append(dict(row))

    if not papers_to_extract:
        _log.info("All papers with full text already have claims extracted")
        return 0

    _log.info("Extracting claims from %d papers", len(papers_to_extract))

    _EXTRACT_PROMPT = """You are a research analyst. Extract ALL findings, theories, methods, limitations, and gaps from this paper.
For EACH claim, provide a JSON object with:
- claim_text: The core claim or finding (1-3 sentences)
- claim_type: One of "finding", "theory", "method", "limitation", "gap"
- confidence: 0.0-1.0 (how strongly the paper supports this claim)
- supporting_quotes: List of 1-2 exact quotes from the text
- section: Which section this came from (e.g., "abstract", "introduction", "results", "discussion")
- sample_size: If applicable
- effect_size: If applicable
- p_value: If applicable
- study_design: If applicable (e.g., "RCT", "survey", "case study", "meta-analysis")
- population: If applicable
- country: If mentioned
- year_range: If a time period is studied

Extract EVERYTHING — not just findings related to one topic. Include theoretical arguments, methodological contributions, boundary conditions, and research gaps.

Return a JSON array of claim objects. Return ONLY the JSON array, no other text.

Paper title: {title}
Paper text:
{text}"""

    total_claims = 0
    papers_done = 0

    for i, paper in enumerate(papers_to_extract):
        title = paper["title"]
        full_text = paper["full_text"] or ""
        abstract = paper["abstract"] or ""

        # Use full text if available, fall back to abstract
        text = full_text[:20000] if len(full_text) > 200 else abstract
        if not text or len(text) < 100:
            continue

        prompt = _EXTRACT_PROMPT.format(title=title, text=text)

        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
            )
            response_text = response.text.strip()

            # Parse JSON from response
            # Handle markdown code blocks
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            claims = json.loads(response_text)
            if not isinstance(claims, list):
                claims = [claims]

            # Store claims in central DB
            central_claims = []
            for c in claims:
                if not isinstance(c, dict) or not c.get("claim_text"):
                    continue
                central_claims.append({
                    "paper_title": title,
                    "paper_doi": paper.get("doi", ""),
                    "claim_text": c["claim_text"],
                    "claim_type": c.get("claim_type", "finding"),
                    "confidence": c.get("confidence", 0.5),
                    "supporting_quotes": json.dumps(c.get("supporting_quotes", [])),
                    "section": c.get("section", ""),
                    "sample_size": c.get("sample_size", ""),
                    "effect_size": c.get("effect_size", ""),
                    "p_value": c.get("p_value", ""),
                    "confidence_interval": c.get("confidence_interval", ""),
                    "study_design": c.get("study_design", ""),
                    "population": c.get("population", ""),
                    "country": c.get("country", ""),
                    "year_range": c.get("year_range", ""),
                })

            if central_claims:
                result = central_db.store_claims(central_claims, session_topic="prewarm")
                stored = result.get("stored", 0)
                total_claims += stored
                central_db.mark_paper_fully_extracted(title)
                papers_done += 1

        except json.JSONDecodeError:
            _log.debug("Failed to parse claims JSON for '%s'", title[:60])
        except Exception as exc:
            _log.warning("Claim extraction failed for '%s': %s", title[:60], exc)
            time.sleep(3)  # Back off on error

        if (i + 1) % 10 == 0:
            _log.info("  Progress: %d/%d papers, %d claims extracted", i + 1, len(papers_to_extract), total_claims)
            time.sleep(2)  # Cool down

    _log.info("PHASE 4 COMPLETE: %d claims from %d papers", total_claims, papers_done)
    return total_claims


def phase_5_embed_claims(central_db: CentralDB) -> int:
    """Generate embeddings for all un-embedded claims."""
    _log.info("=" * 60)
    _log.info("PHASE 5: Embedding claims")
    _log.info("=" * 60)

    api_key = os.getenv("ARA_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        _log.error("No GOOGLE_API_KEY set — cannot embed claims")
        return 0

    from google import genai
    client = genai.Client(api_key=api_key)

    # Get claims without embeddings
    rows = central_db._conn.execute(
        "SELECT claim_id, claim_text FROM claims WHERE embedding IS NULL"
    ).fetchall()

    if not rows:
        _log.info("All claims already have embeddings")
        return 0

    _log.info("Embedding %d claims", len(rows))
    embedded = 0
    _BATCH = 50

    for i in range(0, len(rows), _BATCH):
        batch = rows[i:i + _BATCH]
        texts = [r["claim_text"][:500] for r in batch]

        try:
            result = client.models.embed_content(model="gemini-embedding-001", contents=texts)
            if result.embeddings:
                for claim_row, emb_obj in zip(batch, result.embeddings):
                    central_db.store_claim_embedding(claim_row["claim_id"], emb_obj.values)
                    embedded += 1
        except Exception as exc:
            _log.warning("Claim embedding batch failed: %s", exc)
            time.sleep(3)

        if (i + _BATCH) % 200 == 0:
            _log.info("  Embedded %d/%d claims", embedded, len(rows))
            time.sleep(1)

    _log.info("PHASE 5 COMPLETE: %d/%d claims embedded", embedded, len(rows))
    return embedded


def main():
    _log.info("=" * 60)
    _log.info("ARA CENTRAL DB PRE-WARM")
    _log.info("=" * 60)

    # Check API key — try env vars first, then credential store
    from ara.credentials import load_api_key
    api_key = load_api_key()
    if not api_key:
        _log.error("No Google API key found. Set GOOGLE_API_KEY env var or run 'python -m ara' to configure.")
        sys.exit(1)
    # Make it available to all functions via env
    os.environ["GOOGLE_API_KEY"] = api_key
    _log.info("API key loaded (%s...)", api_key[:8])

    central_db = CentralDB()
    stats = central_db.stats()
    _log.info("Central DB before: %d papers, %d claims, %d with embeddings",
              stats.get("total_papers", 0), stats.get("total_claims", 0),
              stats.get("with_embedding", 0))

    start = time.time()

    # Allow skipping phases via --skip-to=N
    skip_to = 1
    for arg in sys.argv[1:]:
        if arg.startswith("--skip-to="):
            skip_to = int(arg.split("=")[1])

    search_stats = {}
    fulltext_count = 0
    embed_count = 0
    claim_count = 0
    claim_embed_count = 0

    # Phase 1: Search and store papers
    if skip_to <= 1:
        search_stats = phase_1_search(central_db)

    # Phase 2: Fetch full texts
    if skip_to <= 2:
        fulltext_count = phase_2_fulltext(central_db)

    # Phase 3: Embed papers
    if skip_to <= 3:
        embed_count = phase_3_embed_papers(central_db)

    # Phase 4: Extract claims
    if skip_to <= 4:
        claim_count = phase_4_extract_claims(central_db)

    # Phase 5: Embed claims
    if skip_to <= 5:
        claim_embed_count = phase_5_embed_claims(central_db)

    elapsed = time.time() - start
    stats_after = central_db.stats()

    _log.info("=" * 60)
    _log.info("PRE-WARM COMPLETE in %.1f minutes", elapsed / 60)
    _log.info("=" * 60)
    _log.info("Papers:     %d total (%d new)", stats_after.get("total_papers", 0), stats_after.get("total_papers", 0) - stats.get("total_papers", 0))
    _log.info("Full texts: %d fetched this run", fulltext_count)
    _log.info("Embeddings: %d papers embedded", embed_count)
    _log.info("Claims:     %d extracted from papers", claim_count)
    _log.info("Claim emb:  %d claims embedded", claim_embed_count)
    _log.info("Central DB: %s", central_db._path)


if __name__ == "__main__":
    main()
