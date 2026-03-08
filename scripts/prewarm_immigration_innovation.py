# Location: scripts/prewarm_immigration_innovation.py
# Purpose: SerpAPI harvest for "Immigration as Innovation Arbitrage" paper — AAA/AA journals
# Functions: main() orchestrates journal-specific + topic queries via search_and_store
# Calls: ara/central_db.py, scripts/prewarm_serp_extra.py (search_and_store)
# Imports: json, time, logging, os, sys

"""
Immigration × Innovation Arbitrage — Literature Harvester
==========================================================
Targeted SerpAPI search for the paper:
  "Immigration as Innovation Arbitrage: How Cross-Institutional Agents
   Generate Non-Obvious Value in Host Economies"

Target journals: Research Policy (primary), Strategic Management Journal
Tier focus: AAA and AA journals with institutional theory + innovation + migration

Theoretical pillars:
  1. Institutional theory (varieties of capitalism, institutional distance/voids)
  2. Immigration & innovation (immigrant entrepreneurship, patents, STEM)
  3. Knowledge spillovers & brain circulation
  4. Innovation arbitrage (recombinant knowledge, non-obvious value)
  5. Human capital mobility & diaspora networks

Budget: ~200 SerpAPI calls (3 pages each = ~600 results)

Usage:
    SERPAPI_API_KEY=... python scripts/prewarm_immigration_innovation.py
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
_log = logging.getLogger("immig_innov")

# ══════════════════════════════════════════════════════════════════════════
# AAA JOURNAL QUERIES — Top-tier targets
# ══════════════════════════════════════════════════════════════════════════

AAA_JOURNAL_QUERIES = [
    # ── Primary target: Research Policy ──
    ("source:\"Research Policy\"", [
        "immigration innovation entrepreneurship",
        "immigrant inventor patent knowledge spillover",
        "skilled migration innovation system",
        "institutional distance innovation transfer",
        "diaspora network knowledge transfer innovation",
        "brain drain brain gain innovation policy",
        "immigration R&D productivity host country",
        "cross-border knowledge recombination innovation",
    ]),

    # ── Primary target: Strategic Management Journal ──
    ("source:\"Strategic Management Journal\"", [
        "immigrant entrepreneurship institutional theory",
        "human capital mobility innovation competitive advantage",
        "institutional arbitrage strategy",
        "knowledge recombination diverse teams innovation",
        "cross-border knowledge transfer multinational",
        "dynamic capabilities immigrant founder",
    ]),

    # ── Academy of Management Journal ──
    ("source:\"Academy of Management Journal\"", [
        "immigrant entrepreneurship institutional environment",
        "cultural diversity team innovation performance",
        "knowledge spillover immigrant worker",
        "institutional theory entrepreneurship migration",
        "human capital heterogeneity innovation",
    ]),

    # ── Academy of Management Review ──
    ("source:\"Academy of Management Review\"", [
        "institutional theory entrepreneurship",
        "knowledge recombination theory innovation",
        "institutional arbitrage value creation",
        "immigrant identity entrepreneurial process",
    ]),

    # ── Management Science ──
    ("source:\"Management Science\"", [
        "immigrant inventor patent citation impact",
        "team diversity innovation breakthrough",
        "human capital migration productivity",
        "knowledge transfer institutional distance",
    ]),

    # ── Organization Science ──
    ("source:\"Organization Science\"", [
        "immigrant founder organizational innovation",
        "knowledge diversity recombination novelty",
        "institutional complexity immigrant entrepreneur",
        "cross-cultural team knowledge creation",
    ]),

    # ── Journal of International Business Studies ──
    ("source:\"Journal of International Business Studies\"", [
        "immigration entrepreneurship host country innovation",
        "institutional distance knowledge transfer migration",
        "diaspora network foreign direct investment innovation",
        "brain circulation returnee entrepreneur",
        "immigrant multinational enterprise knowledge",
        "cross-institutional arbitrage international business",
    ]),

    # ── American Economic Review ──
    ("source:\"American Economic Review\"", [
        "immigration innovation patents economic growth",
        "high-skilled immigration productivity",
        "immigrant entrepreneur job creation",
        "brain drain developing countries innovation",
    ]),

    # ── Quarterly Journal of Economics ──
    ("source:\"Quarterly Journal of Economics\"", [
        "immigration innovation patents",
        "skilled migration economic impact",
        "immigrant inventor contribution",
    ]),

    # ── Journal of Political Economy ──
    ("source:\"Journal of Political Economy\"", [
        "immigration human capital innovation",
        "skilled migration knowledge production",
        "immigrant entrepreneurship economic growth",
    ]),

    # ── Journal of Finance / JFE / RFS (innovation finance angle) ──
    ("source:\"Journal of Financial Economics\"", [
        "immigrant CEO innovation firm value",
        "venture capital immigrant founder",
        "cultural diversity board innovation",
    ]),

    # ── Journal of Management ──
    ("source:\"Journal of Management\"", [
        "immigrant entrepreneurship institutional theory",
        "human capital diversity innovation management",
        "knowledge transfer migration organizational",
    ]),

    # ── Administrative Science Quarterly ──
    ("source:\"Administrative Science Quarterly\"", [
        "immigrant founder organizational innovation",
        "diversity knowledge creation breakthrough",
        "institutional theory entrepreneurship",
    ]),

    # ── Journal of Economic Literature / Perspectives ──
    ("source:\"Journal of Economic Perspectives\"", [
        "immigration innovation economic impact survey",
        "high-skilled immigration policy brain drain",
        "immigrant entrepreneurship economic contribution",
    ]),

    # ── Review of Economic Studies ──
    ("source:\"Review of Economic Studies\"", [
        "immigration innovation productivity causal",
        "skilled migration knowledge production function",
    ]),

    # ── Econometrica ──
    ("source:\"Econometrica\"", [
        "immigration human capital production function",
        "migration selection innovation",
    ]),
]

# ══════════════════════════════════════════════════════════════════════════
# AA JOURNAL QUERIES — Deep coverage
# ══════════════════════════════════════════════════════════════════════════

AA_JOURNAL_QUERIES = [
    # ── Journal of Business Venturing ──
    ("source:\"Journal of Business Venturing\"", [
        "immigrant entrepreneurship opportunity recognition",
        "diaspora entrepreneur transnational venture",
        "institutional void immigrant founder",
        "refugee entrepreneur innovation resilience",
        "returnee entrepreneur knowledge transfer",
    ]),

    # ── Entrepreneurship Theory and Practice ──
    ("source:\"Entrepreneurship Theory and Practice\"", [
        "immigrant entrepreneurship theory",
        "ethnic entrepreneurship institutional context",
        "transnational entrepreneur diaspora network",
        "immigrant founder innovation performance",
        "institutional theory migrant entrepreneur",
    ]),

    # ── Small Business Economics ──
    ("source:\"Small Business Economics\"", [
        "immigrant self-employment entrepreneurship",
        "immigrant business innovation host economy",
        "ethnic entrepreneur knowledge spillover",
        "migration entrepreneurship innovation policy",
        "high-skilled immigrant startup",
    ]),

    # ── Industrial and Corporate Change ──
    ("source:\"Industrial and Corporate Change\"", [
        "immigration innovation system evolutionary",
        "knowledge recombination immigrant inventor",
        "institutional change entrepreneurship migration",
        "technological diversification immigrant",
    ]),

    # ── Technovation ──
    ("source:\"Technovation\"", [
        "immigrant innovation technology entrepreneurship",
        "diaspora knowledge network technology transfer",
        "migration technology adoption host country",
    ]),

    # ── Technological Forecasting and Social Change ──
    ("source:\"Technological Forecasting and Social Change\"", [
        "immigration innovation ecosystem",
        "skilled migration technology development",
        "immigrant entrepreneur digital innovation",
        "brain circulation technology transfer",
    ]),

    # ── Journal of Management Studies ──
    ("source:\"Journal of Management Studies\"", [
        "immigrant entrepreneur institutional theory",
        "cultural diversity organizational innovation",
        "transnational knowledge transfer management",
        "institutional arbitrage multinational",
    ]),

    # ── World Development ──
    ("source:\"World Development\"", [
        "migration innovation developing countries",
        "brain drain brain gain development",
        "diaspora remittance innovation investment",
        "return migration knowledge transfer development",
        "skilled emigration innovation capacity",
    ]),

    # ── Journal of Development Economics ──
    ("source:\"Journal of Development Economics\"", [
        "skilled migration brain drain innovation",
        "return migration human capital",
        "diaspora knowledge transfer development",
        "immigration productivity developing country",
    ]),

    # ── Journal of Economic Geography ──
    ("source:\"Journal of Economic Geography\"", [
        "immigrant cluster innovation agglomeration",
        "skilled migration regional innovation system",
        "ethnic enclave knowledge spillover",
        "immigration geography innovation hotspot",
    ]),

    # ── Regional Studies ──
    ("source:\"Regional Studies\"", [
        "immigration regional innovation system",
        "immigrant entrepreneur regional development",
        "skilled migration innovation cluster",
    ]),

    # ── International Business Review ──
    ("source:\"International Business Review\"", [
        "immigrant entrepreneur internationalization",
        "diaspora network cross-border business",
        "institutional distance immigrant firm",
        "returnee entrepreneur international knowledge",
    ]),

    # ── Journal of World Business ──
    ("source:\"Journal of World Business\"", [
        "immigrant entrepreneurship global value chain",
        "diaspora FDI knowledge spillover",
        "institutional arbitrage cross-border",
    ]),

    # ── Global Strategy Journal ──
    ("source:\"Global Strategy Journal\"", [
        "immigrant founder global strategy",
        "institutional arbitrage global innovation",
        "migration knowledge recombination global",
    ]),

    # ── Long Range Planning ──
    ("source:\"Long Range Planning\"", [
        "immigrant entrepreneur strategic innovation",
        "knowledge diversity strategic renewal",
        "institutional theory innovation strategy",
    ]),

    # ── Journal of Business Research ──
    ("source:\"Journal of Business Research\"", [
        "immigrant entrepreneurship innovation performance",
        "cultural diversity innovation business",
        "institutional context migrant entrepreneur",
        "diaspora network business innovation",
    ]),

    # ── Journal of Labor Economics ──
    ("source:\"Journal of Labor Economics\"", [
        "immigration labor market innovation",
        "high-skilled immigration wage productivity",
        "immigrant inventor labor mobility",
    ]),

    # ── Journal of International Economics ──
    ("source:\"Journal of International Economics\"", [
        "immigration trade innovation link",
        "skilled migration knowledge flow international",
        "brain drain innovation international",
    ]),

    # ── Journal of Business Ethics ──
    ("source:\"Journal of Business Ethics\"", [
        "immigrant entrepreneurship social value",
        "ethical dimensions immigration innovation",
        "institutional void migrant entrepreneur ethics",
    ]),

    # ── Journal of Economic Behavior and Organization ──
    ("source:\"Journal of Economic Behavior and Organization\"", [
        "immigration entrepreneurship institutional incentives",
        "immigrant innovation behavior economic",
        "cultural distance entrepreneurship institution",
    ]),

    # ── European Economic Review ──
    ("source:\"European Economic Review\"", [
        "immigration innovation Europe",
        "skilled migration productivity European",
        "immigrant entrepreneur European economy",
    ]),

    # ── Journal of Urban Economics ──
    ("source:\"Journal of Urban Economics\"", [
        "immigration urban innovation cluster",
        "immigrant entrepreneur city innovation",
        "skilled migration urban productivity",
    ]),

    # ── Review of Economics and Statistics ──
    ("source:\"Review of Economics and Statistics\"", [
        "immigration innovation patent productivity",
        "high-skilled immigrant economic impact",
        "immigrant entrepreneur employment creation",
    ]),

    # ── Journal of International Migration and Integration ──
    ("source:\"Journal of International Migration and Integration\"", [
        "immigrant entrepreneurship innovation host country",
        "skilled migration integration economic contribution",
        "immigrant human capital utilization innovation",
        "migration integration policy entrepreneurship",
    ]),

    # ── International Migration Review ──
    ("source:\"International Migration Review\"", [
        "immigrant entrepreneurship assimilation economic",
        "skilled immigration brain waste innovation",
        "transnational immigrant entrepreneur network",
        "migration theory human capital selection",
        "immigration second generation entrepreneurship",
    ]),

    # ── Journal of Ethnic and Migration Studies ──
    ("source:\"Journal of Ethnic and Migration Studies\"", [
        "immigrant entrepreneur innovation ethnic economy",
        "skilled migration knowledge transfer host",
        "super-diversity immigrant entrepreneurship",
        "migration entrepreneurship institutional context",
        "transnational migrant innovation network",
    ]),

    # ── Population and Development Review ──
    ("source:\"Population and Development Review\"", [
        "skilled migration innovation development",
        "brain drain innovation developing countries",
    ]),

    # ── Science / Nature (high-impact empirical) ──
    ("source:\"Science\"", [
        "immigration innovation scientific productivity",
        "immigrant scientist research impact",
        "diversity innovation team science",
    ]),
    ("source:\"Nature\"", [
        "immigration science innovation talent",
        "foreign-born researcher scientific breakthrough",
    ]),
    ("source:\"Nature Human Behaviour\"", [
        "cultural diversity innovation creativity",
        "immigration policy scientific productivity",
    ]),

    # ── NBER Working Papers (influential pre-prints) ──
    ("source:\"NBER\"", [
        "immigration innovation patent economic growth",
        "H-1B visa innovation firm productivity",
        "immigrant entrepreneur startup job creation",
        "skilled immigration crowding out complementarity",
    ]),

    # ── Deeper on key AA journals ──
    ("source:\"Journal of Business Venturing\"", [
        "immigrant opportunity recognition institutional context",
        "necessity vs opportunity immigrant entrepreneur",
        "transnational venture diaspora innovation bridge",
    ]),
    ("source:\"Entrepreneurship Theory and Practice\"", [
        "immigrant entrepreneurship mixed embeddedness",
        "refugee entrepreneurship innovation resilience",
        "immigrant women entrepreneurship innovation",
    ]),
    ("source:\"Research Policy\"", [
        "immigration innovation system national",
        "immigrant inventor knowledge recombination patent",
        "skilled migration R&D productivity firm",
        "visa policy innovation restriction brain drain",
        "diaspora knowledge network innovation transfer",
        "foreign-born inventor breakthrough patent",
    ]),
    ("source:\"Strategic Management Journal\"", [
        "immigrant CEO strategic innovation firm",
        "cultural diversity top management team innovation",
        "institutional arbitrage competitive advantage",
        "knowledge heterogeneity recombination value creation",
    ]),
]

# ══════════════════════════════════════════════════════════════════════════
# TOPIC QUERIES (no journal filter) — cast a wider net
# ══════════════════════════════════════════════════════════════════════════

TOPIC_QUERIES = [
    # ── Core framing ──
    "immigration innovation arbitrage institutional",
    "immigrant inventor patent breakthrough non-obvious",
    "cross-institutional knowledge recombination immigration",
    "immigration innovation value creation host economy",
    "immigrant entrepreneur institutional void arbitrage",
    "immigration as arbitrage economic institutions",
    "immigrant bridging institutional distance innovation",

    # ── Empirical patterns ──
    "immigrant founder startup innovation Silicon Valley",
    "H-1B visa innovation patent citation",
    "STEM immigration innovation United States",
    "immigrant entrepreneur technology sector contribution",
    "foreign-born inventor patent Nobel Prize",
    "immigrant share patent citation high impact",
    "foreign-born CEO firm innovation R&D",
    "immigrant founder unicorn venture capital",
    "immigrant engineer technology cluster",

    # ── Institutional theory + migration ──
    "varieties of capitalism immigration innovation",
    "institutional distance immigrant entrepreneurship",
    "institutional complementarity migration innovation system",
    "regulatory arbitrage immigrant entrepreneur",
    "national innovation system immigration policy",
    "institutional logics migrant entrepreneurship",
    "institutional entrepreneurship immigrant founder",
    "comparative institutional advantage migration",

    # ── Knowledge spillovers & recombination ──
    "immigrant knowledge spillover innovation regional",
    "diverse team knowledge recombination breakthrough innovation",
    "cross-border knowledge flow immigration innovation",
    "bicultural immigrant bridging knowledge novelty",
    "cognitive diversity immigrant team innovation",
    "recombinant innovation immigrant diverse background",
    "knowledge brokerage immigrant cross-domain",
    "atypical knowledge combination immigrant patent",
    "boundary spanning immigrant innovation organization",
    "structural hole bridging immigrant inventor",

    # ── Brain circulation & diaspora ──
    "brain circulation innovation developing country",
    "diaspora innovation network homeland",
    "return migration innovation entrepreneurship",
    "transnational immigrant entrepreneur knowledge bridge",
    "diaspora FDI knowledge spillover home country",
    "returnee entrepreneur innovation China India",
    "scientific diaspora collaboration network innovation",
    "circular migration innovation transfer mechanism",
    "highly skilled diaspora technology transfer",

    # ── Policy ──
    "immigration policy innovation economic growth",
    "skilled immigration policy STEM innovation",
    "startup visa immigrant entrepreneur policy",
    "immigration restriction innovation decline",
    "immigration reform innovation patent impact",
    "visa policy skilled worker innovation outcome",
    "talent mobility policy innovation competitiveness",
    "immigration quota restriction innovation loss",

    # ── Mechanisms & theory ──
    "immigrant cultural bridging innovation mechanism",
    "institutional translation immigrant entrepreneur",
    "non-obvious combination immigrant inventor patent",
    "immigrant outsider advantage innovation",
    "liability of foreignness immigrant entrepreneur",
    "immigrant social capital innovation network",
    "mixed embeddedness immigrant entrepreneur innovation",
    "human capital theory immigration innovation",
    "Schumpeterian immigration creative destruction",
    "immigrant alertness entrepreneurial opportunity",

    # ── Host economy impact ──
    "immigration economic impact host country productivity",
    "immigrant contribution GDP innovation employment",
    "high-skilled immigration wage effect native worker",
    "immigration crowding out complementarity native innovation",
    "immigrant entrepreneur job creation local economy",
    "immigration innovation spillover agglomeration effect",
    "foreign talent firm productivity innovation output",

    # ── Country/region specific empirical ──
    "immigrant innovation Canada points-based system",
    "immigration innovation United Kingdom startup",
    "immigrant entrepreneur Germany innovation policy",
    "immigration innovation Australia skilled migration",
    "immigrant entrepreneur Israel startup nation",
    "Indian diaspora innovation Silicon Valley Bangalore",
    "Chinese returnee entrepreneur innovation Beijing",
    "immigration innovation European Union mobility",
    "immigrant inventor patent Japan Korea",
    "immigration innovation Nordic countries Sweden",

    # ── Sector-specific ──
    "immigrant entrepreneur biotech pharmaceutical innovation",
    "immigrant founder fintech digital platform",
    "immigrant inventor semiconductor technology",
    "foreign-born scientist university research innovation",
    "immigrant entrepreneur AI machine learning startup",
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

    total_new = 0
    total_texts = 0
    total_serp = 0
    max_serp = int(os.getenv("MAX_SERP_CALLS", "200"))

    # Track completed queries to allow safe resume
    done_file = Path.home() / ".ara" / "immig_innov_harvest_done.json"
    done_queries: set[str] = set()
    if done_file.exists():
        try:
            done_queries = set(json.loads(done_file.read_text(encoding="utf-8")))
            _log.info("Loaded %d previously completed queries — will skip them", len(done_queries))
        except Exception:
            pass

    # ── Phase 1: AAA journal-specific queries ──
    _log.info("=== PHASE 1: AAA Journal Queries ===")
    for journal_prefix, queries in AAA_JOURNAL_QUERIES:
        for q in queries:
            if total_serp >= max_serp:
                break
            full_query = f"{journal_prefix} {q}"
            if full_query in done_queries:
                _log.info("  SKIP (done): %s", full_query[:80])
                continue

            _log.info("[serp %d] %s", total_serp, full_query)
            s = search_and_store(central_db, full_query, api_key)
            total_new += s["new"]
            total_texts += s["fulltexts"]
            total_serp += s["serp_calls"]
            _log.info("  +%d new, +%d texts (total serp: %d)", s["new"], s["fulltexts"], total_serp)

            done_queries.add(full_query)
            done_file.parent.mkdir(parents=True, exist_ok=True)
            done_file.write_text(json.dumps(sorted(done_queries), indent=2), encoding="utf-8")
            time.sleep(1.5)
        if total_serp >= max_serp:
            break

    # ── Phase 2: AA journal-specific queries ──
    if total_serp < max_serp:
        _log.info("=== PHASE 2: AA Journal Queries ===")
        for journal_prefix, queries in AA_JOURNAL_QUERIES:
            for q in queries:
                if total_serp >= max_serp:
                    break
                full_query = f"{journal_prefix} {q}"
                if full_query in done_queries:
                    _log.info("  SKIP (done): %s", full_query[:80])
                    continue

                _log.info("[serp %d] %s", total_serp, full_query)
                s = search_and_store(central_db, full_query, api_key)
                total_new += s["new"]
                total_texts += s["fulltexts"]
                total_serp += s["serp_calls"]
                _log.info("  +%d new, +%d texts (total serp: %d)", s["new"], s["fulltexts"], total_serp)

                done_queries.add(full_query)
                done_file.write_text(json.dumps(sorted(done_queries), indent=2), encoding="utf-8")
                time.sleep(1.5)
            if total_serp >= max_serp:
                break

    # ── Phase 3: Broad topic queries (no journal filter) ──
    if total_serp < max_serp:
        _log.info("=== PHASE 3: Broad Topic Queries ===")
        for q in TOPIC_QUERIES:
            if total_serp >= max_serp:
                break
            if q in done_queries:
                _log.info("  SKIP (done): %s", q[:80])
                continue

            _log.info("[serp %d] %s", total_serp, q)
            s = search_and_store(central_db, q, api_key)
            total_new += s["new"]
            total_texts += s["fulltexts"]
            total_serp += s["serp_calls"]
            _log.info("  +%d new, +%d texts (total serp: %d)", s["new"], s["fulltexts"], total_serp)

            done_queries.add(q)
            done_file.write_text(json.dumps(sorted(done_queries), indent=2), encoding="utf-8")
            time.sleep(1.5)

    # ── Backfill journal tiers ──
    _log.info("Backfilling journal tiers...")
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
    _log.info("Classified %d papers by journal tier", classified)

    # ── Final report ──
    after = central_db.stats()
    _log.info("=" * 60)
    _log.info("DONE: +%d new papers, +%d fulltexts, %d SerpAPI calls used", total_new, total_texts, total_serp)
    _log.info("Central DB: %d papers (+%d), %d texts (+%d)",
              after["total_papers"], after["total_papers"] - before["total_papers"],
              after["with_fulltext"], after["with_fulltext"] - before["with_fulltext"])


if __name__ == "__main__":
    main()
