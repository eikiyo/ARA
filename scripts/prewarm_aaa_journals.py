# Location: scripts/prewarm_aaa_journals.py
# Purpose: Targeted SerpAPI search for AAA/AA journal papers — build top-tier citation pool
# Functions: search_and_store (from prewarm_serp_extra.py), journal-specific queries
# Calls: ara/central_db.py, serpapi
# Imports: json, time, logging, os, sys

"""
AAA/AA Journal Paper Harvester
==============================
Uses SerpAPI to search Google Scholar for papers specifically from
top-tier (AAA/AA) journals relevant to fintech, financial crises,
platform firms, and South/Southeast Asian economies.

Strategy: "source:journal_name" + topic keywords → forces results
from specific journals. 3 pages per query = 30 results.

Budget: ~150 SerpAPI calls → ~450 results → ~200-300 new papers

Usage:
    SERPAPI_API_KEY=... python scripts/prewarm_aaa_journals.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.prewarm_serp_extra import search_and_store
from ara.central_db import CentralDB

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_log = logging.getLogger("aaa_harvest")

# ── AAA JOURNALS × TOPIC QUERIES ──
# Each tuple: (journal_source_prefix, [queries])
# The journal name is prepended to each query for Google Scholar filtering

AAA_JOURNAL_QUERIES = [
    # ── Finance (core for fintech/crisis topic) ──
    ("source:\"Journal of Financial Economics\"", [
        "fintech financial crisis technology",
        "platform firms digital finance emerging markets",
        "credit risk financial innovation",
        "financial intermediation technology disruption",
    ]),
    ("source:\"Review of Financial Studies\"", [
        "fintech lending digital finance",
        "financial crisis innovation technology",
        "platform economy financial markets",
        "credit market disruption emerging economies",
    ]),
    ("source:\"Journal of Finance\"", [
        "fintech financial innovation",
        "financial crisis technology portfolio",
        "digital finance platform",
        "emerging market financial development",
    ]),

    # ── Strategy & Management (core for platform/portfolio) ──
    ("source:\"Strategic Management Journal\"", [
        "technology portfolio crisis strategy",
        "platform firms strategic response",
        "digital transformation financial services",
        "dynamic capabilities technology",
    ]),
    ("source:\"Academy of Management Journal\"", [
        "technology innovation financial crisis",
        "platform ecosystem digital transformation",
        "organizational resilience crisis response",
    ]),
    ("source:\"Academy of Management Review\"", [
        "digital platform theory",
        "technology portfolio management",
        "organizational response financial crisis",
    ]),
    ("source:\"Management Science\"", [
        "fintech credit lending technology",
        "platform competition digital markets",
        "financial innovation risk management",
    ]),
    ("source:\"Organization Science\"", [
        "platform ecosystem dynamics",
        "digital transformation organizational",
        "technology strategy innovation",
    ]),
    ("source:\"Journal of Management\"", [
        "digital innovation platform firms",
        "technology strategy financial services",
        "crisis management organizational resilience",
    ]),
    ("source:\"Journal of Management Studies\"", [
        "fintech digital innovation",
        "platform strategy emerging markets",
        "institutional theory technology adoption",
    ]),

    # ── International Business (core for South/SE Asia) ──
    ("source:\"Journal of International Business Studies\"", [
        "fintech emerging markets Asia",
        "digital platform internationalization",
        "financial crisis emerging economy technology",
        "multinational enterprise digital transformation",
    ]),
    ("source:\"Journal of World Business\"", [
        "fintech developing countries Asia",
        "digital platform emerging market",
        "financial inclusion technology Southeast Asia",
    ]),
    ("source:\"Global Strategy Journal\"", [
        "digital platform global strategy",
        "fintech internationalization emerging markets",
        "technology portfolio multinational",
    ]),

    # ── Innovation & Technology (core for technology portfolios) ──
    ("source:\"Research Policy\"", [
        "fintech innovation financial technology",
        "technology portfolio R&D strategy",
        "digital platform innovation ecosystem",
        "financial crisis innovation response",
    ]),
    ("source:\"Journal of Business Venturing\"", [
        "fintech entrepreneurship venture",
        "digital platform startup emerging market",
        "financial crisis entrepreneurial response",
    ]),

    # ── Economics (macro context for financial crises) ──
    ("source:\"American Economic Review\"", [
        "fintech financial inclusion",
        "financial crisis technology innovation",
        "digital finance developing countries",
    ]),
    ("source:\"Quarterly Journal of Economics\"", [
        "financial crisis technology",
        "digital finance inclusion",
        "platform economy innovation",
    ]),

    # ── Information Systems ──
    ("source:\"Information Systems Research\"", [
        "fintech platform digital innovation",
        "financial technology adoption",
        "digital platform business model",
    ]),
]

# ── AA JOURNALS (high relevance to fintech topic) ──
# Expanded with more journals and deeper query coverage for 600-call budget
AA_JOURNAL_QUERIES = [
    # ── Finance & Banking (core) ──
    ("source:\"Journal of Banking and Finance\"", [
        "fintech financial crisis lending",
        "digital banking technology portfolio",
        "financial innovation emerging markets Asia",
        "credit risk fintech platform",
        "mobile banking adoption developing countries",
        "financial crisis bank technology response",
    ]),
    ("source:\"Journal of Corporate Finance\"", [
        "fintech corporate strategy",
        "financial crisis firm technology investment",
        "digital transformation financial firms",
        "venture capital fintech funding crisis",
        "corporate innovation technology portfolio",
    ]),
    ("source:\"Review of Finance\"", [
        "fintech lending credit",
        "financial crisis technology",
        "digital finance inclusion",
        "peer-to-peer lending platform",
        "financial innovation regulation",
    ]),
    ("source:\"Journal of Financial Intermediation\"", [
        "fintech financial intermediation",
        "digital lending platform",
        "financial crisis credit technology",
        "disintermediation banking technology",
        "payment systems innovation",
    ]),
    ("source:\"Journal of Money Credit and Banking\"", [
        "fintech monetary policy",
        "digital currency financial stability",
        "mobile money developing economy",
        "credit market technology disruption",
    ]),
    ("source:\"Journal of Financial Stability\"", [
        "fintech systemic risk",
        "financial crisis digital transformation",
        "regulatory technology financial stability",
        "platform risk financial system",
    ]),
    ("source:\"European Financial Management\"", [
        "fintech innovation Europe",
        "digital banking transformation",
        "financial crisis technology adoption",
    ]),

    # ── Technology & Innovation (core) ──
    ("source:\"Technological Forecasting and Social Change\"", [
        "fintech financial technology innovation",
        "digital platform emerging economy",
        "financial crisis technology adoption Southeast Asia",
        "blockchain fintech application",
        "digital transformation financial services",
        "technology portfolio management innovation",
    ]),
    ("source:\"Technovation\"", [
        "fintech innovation developing countries",
        "digital platform technology strategy",
        "financial technology emerging markets",
        "technology entrepreneurship financial",
        "innovation ecosystem fintech",
    ]),
    ("source:\"Technology Analysis and Strategic Management\"", [
        "fintech strategy digital innovation",
        "technology portfolio crisis management",
        "platform business model technology",
    ]),
    ("source:\"Industrial and Corporate Change\"", [
        "digital transformation industry disruption",
        "technology innovation financial crisis",
        "platform ecosystem evolution",
    ]),
    ("source:\"R&D Management\"", [
        "technology portfolio R&D strategy",
        "innovation management crisis response",
        "digital technology investment",
    ]),

    # ── International Business & Development ──
    ("source:\"World Development\"", [
        "fintech financial inclusion developing countries",
        "digital finance Asia Africa",
        "mobile money financial crisis",
        "digital payment poverty reduction",
        "technology adoption emerging economy",
    ]),
    ("source:\"Journal of Development Economics\"", [
        "mobile money financial inclusion",
        "digital finance developing countries",
        "technology adoption poverty",
    ]),
    ("source:\"International Business Review\"", [
        "fintech internationalization emerging markets",
        "digital platform cross-border",
        "multinational enterprise technology strategy",
        "financial crisis international business",
    ]),
    ("source:\"Asia Pacific Journal of Management\"", [
        "fintech Asia digital platform",
        "financial crisis Southeast Asia technology",
        "platform economy ASEAN",
    ]),
    ("source:\"Emerging Markets Review\"", [
        "fintech emerging market innovation",
        "financial crisis technology response",
        "digital finance inclusion Asia",
    ]),

    # ── Strategy & Management ──
    ("source:\"Long Range Planning\"", [
        "fintech strategy digital transformation",
        "platform business model innovation",
        "strategic response financial crisis",
        "technology portfolio corporate strategy",
    ]),
    ("source:\"British Journal of Management\"", [
        "digital transformation strategy",
        "platform ecosystem management",
        "organizational resilience technology",
    ]),
    ("source:\"Journal of Business Research\"", [
        "fintech digital innovation consumer",
        "platform business model disruption",
        "technology adoption financial services",
        "financial crisis organizational response",
    ]),

    # ── Information Systems ──
    ("source:\"International Journal of Information Management\"", [
        "fintech digital innovation",
        "platform ecosystem technology",
        "digital transformation financial services",
        "mobile payment adoption technology",
    ]),
    ("source:\"Electronic Commerce Research and Applications\"", [
        "fintech platform digital payment",
        "mobile banking technology adoption",
        "digital financial services",
    ]),
    ("source:\"Information and Management\"", [
        "fintech adoption digital innovation",
        "platform technology financial services",
        "digital transformation organizational",
    ]),

    # ── Ethics & Governance ──
    ("source:\"Journal of Business Ethics\"", [
        "fintech ethics financial inclusion",
        "digital platform responsible innovation",
        "algorithmic bias lending credit",
        "financial technology governance",
    ]),

    # ── Entrepreneurship ──
    ("source:\"Small Business Economics\"", [
        "fintech entrepreneurship SME",
        "digital platform small business",
        "financial crisis SME technology",
        "crowdfunding platform innovation",
    ]),
    ("source:\"Entrepreneurship Theory and Practice\"", [
        "fintech entrepreneurship digital",
        "platform ecosystem startup",
        "financial crisis entrepreneurial response",
    ]),
]


def main():
    api_key = os.getenv("SERPAPI_API_KEY", "")
    if not api_key:
        _log.error("Set SERPAPI_API_KEY environment variable")
        sys.exit(1)

    central_db = CentralDB()
    before = central_db.stats()
    _log.info("Central DB before: %d papers, %d with fulltext, %d claims",
              before["total_papers"], before["with_fulltext"], before["total_claims"])

    # Count existing top-tier
    aaa_before = central_db._conn.execute(
        "SELECT COUNT(*) FROM papers WHERE journal_tier = 'AAA'"
    ).fetchone()[0]
    aa_before = central_db._conn.execute(
        "SELECT COUNT(*) FROM papers WHERE journal_tier = 'AA'"
    ).fetchone()[0]
    _log.info("Top-tier before: %d AAA + %d AA = %d", aaa_before, aa_before, aaa_before + aa_before)

    total_new = 0
    total_texts = 0
    total_serp = 0
    max_serp = int(os.getenv("MAX_SERP_CALLS", "600"))

    # Track completed queries to avoid re-spending SerpAPI budget on reruns
    done_file = Path.home() / ".ara" / "aaa_harvest_done.json"
    done_queries: set[str] = set()
    if done_file.exists():
        try:
            done_queries = set(json.loads(done_file.read_text(encoding="utf-8")))
            _log.info("Loaded %d previously completed queries — will skip them", len(done_queries))
        except Exception:
            pass

    # AA first (priority for deeper coverage), then AAA
    all_queries = AA_JOURNAL_QUERIES + AAA_JOURNAL_QUERIES
    query_idx = 0
    skipped_queries = 0

    for journal_prefix, queries in all_queries:
        for q in queries:
            if total_serp >= max_serp:
                _log.warning("SerpAPI budget limit reached (%d/%d calls)", total_serp, max_serp)
                break

            query_idx += 1
            full_query = f"{journal_prefix} {q}"

            if full_query in done_queries:
                skipped_queries += 1
                _log.info("[%d] SKIP (already done): %s", query_idx, full_query)
                continue

            _log.info("[%d] %s", query_idx, full_query)

            s = search_and_store(central_db, full_query, api_key)
            total_new += s["new"]
            total_texts += s["fulltexts"]
            total_serp += s["serp_calls"]
            _log.info("  +%d new, +%d texts (%d serp calls total)", s["new"], s["fulltexts"], total_serp)

            # Mark query as done
            done_queries.add(full_query)
            done_file.write_text(json.dumps(sorted(done_queries), indent=2), encoding="utf-8")

            time.sleep(1.5)

        if total_serp >= max_serp:
            break

    _log.info("Skipped %d previously completed queries", skipped_queries)

    # Backfill journal tiers for newly added papers
    _log.info("Backfilling journal tiers for new papers...")
    from ara.db import classify_journal
    rows = central_db._conn.execute(
        "SELECT paper_id, doi FROM papers WHERE journal_tier IS NULL AND doi IS NOT NULL AND doi != ''"
    ).fetchall()
    classified = 0
    for r in rows:
        j_name, j_tier = classify_journal(r["doi"])
        if j_tier:
            central_db._conn.execute(
                "UPDATE papers SET journal_tier = ?, journal_name = ? WHERE paper_id = ?",
                (j_tier, j_name, r["paper_id"]),
            )
            classified += 1
    central_db._conn.commit()
    _log.info("Classified %d new papers by journal tier", classified)

    # Final counts
    after = central_db.stats()
    aaa_after = central_db._conn.execute(
        "SELECT COUNT(*) FROM papers WHERE journal_tier = 'AAA'"
    ).fetchone()[0]
    aa_after = central_db._conn.execute(
        "SELECT COUNT(*) FROM papers WHERE journal_tier = 'AA'"
    ).fetchone()[0]

    _log.info("=" * 60)
    _log.info("DONE: +%d new papers, +%d fulltexts, %d SerpAPI calls", total_new, total_texts, total_serp)
    _log.info("Central DB: %d papers (+%d), %d texts (+%d)",
              after["total_papers"], after["total_papers"] - before["total_papers"],
              after["with_fulltext"], after["with_fulltext"] - before["with_fulltext"])
    _log.info("Top-tier: %d AAA (+%d), %d AA (+%d) = %d total (+%d)",
              aaa_after, aaa_after - aaa_before,
              aa_after, aa_after - aa_before,
              aaa_after + aa_after, (aaa_after + aa_after) - (aaa_before + aa_before))


if __name__ == "__main__":
    main()
