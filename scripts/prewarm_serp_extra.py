# Location: scripts/prewarm_serp_extra.py
# Purpose: Use remaining SerpAPI budget on adjacent topics not yet in central DB
# Functions: SerpAPI Google Scholar search + PDF download
# Calls: ara/central_db.py, ara/tools/fulltext.py
# Imports: json, time, logging, httpx, os

"""
SerpAPI Extra Coverage
======================
Uses remaining SerpAPI budget (~150 calls) on adjacent/uncovered topics
to maximize new unique papers in the central DB.

Focus: Topics adjacent to fintech/entrepreneurship/innovation/debt/Nordic
that are NOT already well-covered.

Usage:
    SERPAPI_API_KEY=... python scripts/prewarm_serp_extra.py
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ara.central_db import CentralDB, _title_hash

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_log = logging.getLogger("serp_extra")

MAX_FULLTEXT_CHARS = 80_000

# Adjacent topics NOT covered by the main prewarm clusters
# Goal: max NEW papers per query — avoid overlap with existing fintech/entrepreneurship/innovation/debt/Nordic
EXTRA_QUERIES = [
    # ── Swedish/Nordic policy & institutions ──
    "Swedish innovation policy technology strategy",
    "Sweden digitalization policy government",
    "Nordic industrial policy SME support",
    "Swedish business policy entrepreneurship regulation",
    "Vinnova innovation agency Sweden funding",
    "Swedish startup policy incubator accelerator",
    "Nordic competition policy digital markets",
    "Sweden tax policy entrepreneurship investment",
    "Scandinavian labor market policy technology displacement",
    "Swedish trade policy export SME internationalization",

    # ── MNE / Multinational enterprises ──
    "multinational enterprise subsidiary knowledge transfer",
    "MNE technology spillover host country",
    "multinational corporation digital transformation strategy",
    "MNE entry mode emerging market",
    "multinational enterprise innovation subsidiary R&D",
    "MNE corporate social responsibility developing country",
    "multinational firm platform economy gig work",
    "MNE supply chain resilience disruption",
    "multinational enterprise institutional theory distance",
    "Swedish multinational firm internationalization",
    "Nordic MNE foreign direct investment",
    "MNE fintech digital banking cross-border",

    # ── Technology policy & digital governance ──
    "AI regulation policy European Union",
    "digital sovereignty data governance Europe",
    "central bank digital currency CBDC policy",
    "cryptocurrency regulation policy framework",
    "platform regulation antitrust Big Tech",
    "data protection GDPR innovation impact",
    "open data policy government digital",
    "cybersecurity policy financial sector",

    # ── Financial inclusion & development ──
    "financial inclusion mobile money Africa Asia",
    "microfinance digital transformation technology",
    "impact investing social enterprise measurement",
    "ESG fintech sustainable investment platform",
    "climate finance green fintech carbon",
    "remittance digital cross-border payment",
    "unbanked population financial technology access",

    # ── Institutional theory & business environment ──
    "institutional theory entrepreneurship emerging economy",
    "varieties of capitalism innovation system",
    "regulatory sandbox fintech experimentation",
    "business ecosystem platform strategy",
    "dynamic capabilities digital transformation",
    "resource-based view digital innovation",
    "transaction cost economics platform intermediation",
    "agency theory fintech governance",

    # ── Labor market & future of work ──
    "gig economy platform work regulation",
    "AI automation employment displacement",
    "future of work financial services banking",
    "remote work digital nomad entrepreneurship",
    "skills gap technology workforce training",
    "human capital fintech talent",

    # ── Emerging tech in finance ──
    "embedded finance banking-as-a-service",
    "buy now pay later BNPL consumer",
    "insurtech digital insurance innovation",
    "wealthtech robo-advisor retail investor",
    "quantum computing financial risk",
    "natural language processing financial analysis",
]


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
        if "html" in content_type or "text" in content_type:
            text = resp.text[:MAX_FULLTEXT_CHARS]
            if len(text) > 500:
                return text
        if "pdf" in content_type or url.lower().endswith(".pdf"):
            try:
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
    except Exception:
        pass
    return None


def search_and_store(central_db: CentralDB, query: str, api_key: str) -> dict:
    """One SerpAPI query → 3 pages → store papers + download PDFs."""
    import re
    import httpx

    stats = {"papers": 0, "new": 0, "fulltexts": 0, "serp_calls": 0}

    for page in range(3):  # 3 pages × 10 = 30 results
        try:
            resp = httpx.get(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google_scholar",
                    "q": query,
                    "api_key": api_key,
                    "start": page * 10,
                    "num": 10,
                    "as_ylo": 2019,  # Last 5-6 years — more relevant
                },
                timeout=20,
            )
            stats["serp_calls"] += 1
            if resp.status_code != 200:
                break
            data = resp.json()

            for item in data.get("organic_results", []):
                title = item.get("title", "")
                if not title:
                    continue

                snippet = item.get("snippet", "")
                pub_info = item.get("publication_info", {})

                # Authors
                authors = []
                for a in pub_info.get("authors", []):
                    if a.get("name"):
                        authors.append(a["name"])
                if not authors and pub_info.get("summary"):
                    parts = pub_info["summary"].split(" - ")
                    if parts:
                        authors = [a.strip() for a in parts[0].split(",")
                                   if a.strip() and not a.strip().isdigit() and len(a.strip()) > 1]

                # Year
                year = None
                summary = pub_info.get("summary", "")
                ym = re.search(r'(20[012]\d|199\d)', summary)
                if ym:
                    year = int(ym.group())

                # DOI
                doi = None
                link = item.get("link", "")
                if "doi.org/" in link:
                    doi = link.split("doi.org/")[-1]

                # Citations
                citation_count = 0
                cited_by = item.get("inline_links", {}).get("cited_by", {})
                if isinstance(cited_by, dict):
                    total = cited_by.get("total")
                    if isinstance(total, int):
                        citation_count = total
                    elif isinstance(total, str):
                        m = re.search(r'(\d+)', total)
                        if m:
                            citation_count = int(m.group(1))

                stats["papers"] += 1

                # Check if already in DB
                t_hash = _title_hash(title)
                existing = central_db._conn.execute(
                    "SELECT paper_id FROM papers WHERE title_hash = ?", (t_hash,)
                ).fetchone()
                if existing:
                    # Still try to add fulltext if missing
                    has_text = central_db._conn.execute(
                        "SELECT full_text IS NOT NULL FROM papers WHERE paper_id = ?",
                        (existing["paper_id"],)
                    ).fetchone()
                    if has_text and not has_text[0]:
                        # Try PDF download for existing paper
                        for res in item.get("resources", []):
                            text = _download_pdf_text(res.get("link", ""))
                            if text:
                                central_db.store_fulltext(existing["paper_id"], text)
                                stats["fulltexts"] += 1
                                break
                    continue

                # New paper — store it
                paper = {
                    "title": title, "abstract": snippet, "authors": authors,
                    "year": year, "doi": doi, "source": "serpapi_scholar",
                    "url": link, "citation_count": citation_count,
                }
                result = central_db.store_papers([paper])
                if result.get("stored", 0) > 0:
                    stats["new"] += 1

                    # Download PDF
                    pdf_urls = [r.get("link", "") for r in item.get("resources", []) if r.get("link")]
                    if link and "scholar.google" not in link:
                        pdf_urls.append(link)

                    row = central_db._conn.execute(
                        "SELECT paper_id FROM papers WHERE title_hash = ?", (t_hash,)
                    ).fetchone()
                    if row:
                        for pdf_url in pdf_urls:
                            text = _download_pdf_text(pdf_url)
                            if text:
                                central_db.store_fulltext(row["paper_id"], text)
                                stats["fulltexts"] += 1
                                break

        except Exception as exc:
            _log.warning("SerpAPI error: %s", exc)
            break
        time.sleep(1.5)

    return stats


def main():
    api_key = os.getenv("SERPAPI_API_KEY", "")
    if not api_key:
        _log.error("Set SERPAPI_API_KEY")
        sys.exit(1)

    central_db = CentralDB()
    before = central_db.stats()
    _log.info("Central DB: %d papers, %d texts, %d claims",
              before["total_papers"], before["with_fulltext"], before["total_claims"])

    total_new = 0
    total_texts = 0
    total_serp = 0

    for i, query in enumerate(EXTRA_QUERIES):
        _log.info("[%d/%d] %s", i + 1, len(EXTRA_QUERIES), query)
        s = search_and_store(central_db, query, api_key)
        total_new += s["new"]
        total_texts += s["fulltexts"]
        total_serp += s["serp_calls"]
        _log.info("  +%d new papers, +%d fulltexts (%d serp calls)", s["new"], s["fulltexts"], s["serp_calls"])

        # Budget guard — stop if we've used too many
        if total_serp >= 140:
            _log.warning("Approaching SerpAPI budget limit (%d calls) — stopping", total_serp)
            break

        time.sleep(1)

    after = central_db.stats()
    _log.info("=" * 60)
    _log.info("DONE: +%d new papers, +%d fulltexts, %d SerpAPI calls", total_new, total_texts, total_serp)
    _log.info("Central DB: %d papers (+%d), %d texts (+%d)",
              after["total_papers"], after["total_papers"] - before["total_papers"],
              after["with_fulltext"], after["with_fulltext"] - before["with_fulltext"])


if __name__ == "__main__":
    main()
