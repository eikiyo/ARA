# Location: ara/tools/analysis.py
# Purpose: 10 analytical power tools for evidence synthesis and quality assurance
# Functions: detect_contradictions, build_citation_network, classify_methodology, aggregate_samples, meta_analyze, map_theories, analyze_temporal_trends, generate_evidence_table, check_claim_consistency, compute_kappa
# Calls: ara.db for data access, numpy/scipy for statistics
# Imports: json, logging, re, math, collections

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter, defaultdict
from typing import Any

import numpy as np

_log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_claims(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    db = ctx.get("db")
    sid = ctx.get("session_id")
    if db and sid:
        return db.get_claims(sid)
    return []


def _get_papers(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    db = ctx.get("db")
    sid = ctx.get("session_id")
    if db and sid:
        return db.get_papers(sid)
    return []


def _get_rob(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    db = ctx.get("db")
    sid = ctx.get("session_id")
    if db and sid:
        return db.get_risk_of_bias(sid)
    return []


def _get_grade(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    db = ctx.get("db")
    sid = ctx.get("session_id")
    if db and sid:
        return db.get_grade_evidence(sid)
    return []


def _parse_effect_size(es_str: str | None) -> float | None:
    """Extract numeric effect size from string like 'd = 0.45' or 'OR = 2.3'."""
    if not es_str:
        return None
    m = re.search(r'[-+]?\d*\.?\d+', str(es_str))
    return float(m.group()) if m else None


def _parse_sample_size(ss_str: str | None) -> int | None:
    """Extract numeric sample size from string like 'N=1,847' or 'n = 200'."""
    if not ss_str:
        return None
    cleaned = str(ss_str).replace(",", "").replace(" ", "")
    m = re.search(r'\d+', cleaned)
    return int(m.group()) if m else None


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    a_arr, b_arr = np.array(a), np.array(b)
    denom = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    if denom == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / denom)


# ─────────────────────────────────────────────────────────────────────────────
# 1. DETECT CONTRADICTIONS
# ─────────────────────────────────────────────────────────────────────────────

def detect_contradictions(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Find conflicting claims in the evidence base using effect direction and semantic similarity."""
    theme = arguments.get("theme", "")
    min_confidence = arguments.get("min_confidence", 0.5)

    claims = _get_claims(ctx)
    if not claims:
        return json.dumps({"contradictions": [], "error": "No claims in database"})

    # Filter by theme if provided
    if theme:
        theme_lower = theme.lower()
        claims = [c for c in claims if theme_lower in str(c.get("claim_text", "")).lower()
                  or theme_lower in str(c.get("theme", "")).lower()]

    # Filter by confidence
    claims = [c for c in claims if (c.get("confidence") or 0) >= min_confidence]

    # Extract effect direction signals from claims
    _POSITIVE = re.compile(r'\b(positive|increase|improve|enhance|higher|greater|promote|facilitate|benefit)\b', re.I)
    _NEGATIVE = re.compile(r'\b(negative|decrease|reduce|lower|inhibit|hinder|barrier|decline|worsen)\b', re.I)
    _NOSIG = re.compile(r'\b(no (significant |statistically )?(?:effect|difference|association|relationship|impact))\b', re.I)

    def _direction(claim_text: str) -> str:
        text = str(claim_text)
        pos = len(_POSITIVE.findall(text))
        neg = len(_NEGATIVE.findall(text))
        nosig = len(_NOSIG.findall(text))
        if nosig > 0:
            return "null"
        if pos > neg:
            return "positive"
        if neg > pos:
            return "negative"
        return "unclear"

    # Annotate claims with direction
    annotated = []
    for c in claims:
        d = _direction(c.get("claim_text", ""))
        if d != "unclear":
            annotated.append({**c, "_direction": d})

    # Find contradictions: claims about similar topics with opposite directions
    contradictions = []
    seen_pairs: set[tuple[int, int]] = set()

    for i, ca in enumerate(annotated):
        for j, cb in enumerate(annotated):
            if j <= i:
                continue
            # Must be from different papers
            if ca.get("paper_id") == cb.get("paper_id"):
                continue
            # Must have opposing directions
            if not ((ca["_direction"] == "positive" and cb["_direction"] == "negative") or
                    (ca["_direction"] == "negative" and cb["_direction"] == "positive") or
                    (ca["_direction"] == "null" and cb["_direction"] in ("positive", "negative")) or
                    (cb["_direction"] == "null" and ca["_direction"] in ("positive", "negative"))):
                continue

            pair_key = (min(ca.get("id", i), cb.get("id", j)), max(ca.get("id", i), cb.get("id", j)))
            if pair_key in seen_pairs:
                continue

            # Check semantic similarity via keyword overlap
            words_a = set(str(ca.get("claim_text", "")).lower().split())
            words_b = set(str(cb.get("claim_text", "")).lower().split())
            stopwords = {"the", "a", "an", "is", "are", "was", "were", "of", "in", "to", "and", "or", "that", "this", "for", "with", "on", "at", "by", "from", "not", "no", "but"}
            words_a -= stopwords
            words_b -= stopwords
            if not words_a or not words_b:
                continue
            overlap = len(words_a & words_b) / min(len(words_a), len(words_b))

            if overlap >= 0.25:  # At least 25% keyword overlap = same topic
                seen_pairs.add(pair_key)
                contradictions.append({
                    "claim_a": {
                        "id": ca.get("id"),
                        "paper_id": ca.get("paper_id"),
                        "text": ca.get("claim_text", "")[:200],
                        "direction": ca["_direction"],
                        "confidence": ca.get("confidence"),
                        "effect_size": ca.get("effect_size"),
                    },
                    "claim_b": {
                        "id": cb.get("id"),
                        "paper_id": cb.get("paper_id"),
                        "text": cb.get("claim_text", "")[:200],
                        "direction": cb["_direction"],
                        "confidence": cb.get("confidence"),
                        "effect_size": cb.get("effect_size"),
                    },
                    "overlap_score": round(overlap, 2),
                    "contradiction_type": "direction_conflict" if ca["_direction"] != "null" and cb["_direction"] != "null" else "null_vs_effect",
                    "shared_keywords": sorted(words_a & words_b)[:10],
                })

    # Sort by overlap (most similar = most likely real contradiction)
    contradictions.sort(key=lambda x: x["overlap_score"], reverse=True)

    return json.dumps({
        "contradictions": contradictions[:30],
        "total_found": len(contradictions),
        "claims_analyzed": len(annotated),
    })


# ─────────────────────────────────────────────────────────────────────────────
# 2. BUILD CITATION NETWORK
# ─────────────────────────────────────────────────────────────────────────────

def build_citation_network(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Analyze citation patterns: co-citation clusters, concentration, bridge papers."""
    claims = _get_claims(ctx)
    papers = _get_papers(ctx)

    if not papers:
        return json.dumps({"error": "No papers in database"})

    paper_map = {p.get("id"): p for p in papers}

    # Build co-citation matrix: papers cited together in similar themes
    # Group claims by theme/outcome
    theme_papers: dict[str, set[int]] = defaultdict(set)
    paper_claim_count: Counter[int] = Counter()

    for c in claims:
        pid = c.get("paper_id")
        if pid:
            paper_claim_count[pid] += 1
            theme = c.get("theme") or c.get("claim_type") or "general"
            theme_papers[theme].add(pid)

    # Co-citation: papers appearing in the same theme
    co_citation: Counter[tuple[int, int]] = Counter()
    for theme, pids in theme_papers.items():
        pids_list = sorted(pids)
        for i, a in enumerate(pids_list):
            for b in pids_list[i + 1:]:
                co_citation[(a, b)] += 1

    # Identify clusters via connected components
    # Simple: group papers that co-occur in 2+ themes
    adj: dict[int, set[int]] = defaultdict(set)
    for (a, b), count in co_citation.items():
        if count >= 2:
            adj[a].add(b)
            adj[b].add(a)

    clusters: list[list[int]] = []
    visited: set[int] = set()
    for pid in paper_map:
        if pid in visited:
            continue
        cluster = []
        stack = [pid]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            cluster.append(node)
            stack.extend(adj.get(node, set()) - visited)
        if len(cluster) > 1:
            clusters.append(sorted(cluster))

    # Bridge papers: connected to multiple clusters
    paper_cluster: dict[int, int] = {}
    for i, cluster in enumerate(clusters):
        for pid in cluster:
            paper_cluster[pid] = i

    bridge_papers = []
    for (a, b), count in co_citation.most_common(50):
        if paper_cluster.get(a) != paper_cluster.get(b) and paper_cluster.get(a) is not None:
            bridge_papers.append({"paper_a": a, "paper_b": b, "co_citation_count": count})

    # Citation concentration
    total_claims = sum(paper_claim_count.values()) or 1
    top5 = paper_claim_count.most_common(5)
    top5_pct = sum(c for _, c in top5) / total_claims * 100

    # Seminal papers (most claims)
    seminal = []
    for pid, count in paper_claim_count.most_common(10):
        p = paper_map.get(pid, {})
        seminal.append({
            "paper_id": pid,
            "title": (p.get("title") or "")[:100],
            "claims": count,
            "pct_of_evidence": round(count / total_claims * 100, 1),
            "year": p.get("year"),
        })

    return json.dumps({
        "seminal_papers": seminal,
        "clusters": [{"id": i, "size": len(c), "paper_ids": c[:20]} for i, c in enumerate(clusters[:10])],
        "bridge_papers": bridge_papers[:10],
        "citation_concentration": {
            "top5_papers_pct": round(top5_pct, 1),
            "risk": "HIGH" if top5_pct > 50 else "MODERATE" if top5_pct > 30 else "LOW",
        },
        "total_papers": len(papers),
        "papers_with_claims": len(paper_claim_count),
        "themes": len(theme_papers),
    })


# ─────────────────────────────────────────────────────────────────────────────
# 3. CLASSIFY METHODOLOGY
# ─────────────────────────────────────────────────────────────────────────────

_METHOD_PATTERNS: dict[str, list[str]] = {
    "RCT": [r'\brandomiz\w+ control\w+ trial\b', r'\bRCT\b', r'\brandom\w+ assign\w+\b', r'\bdouble.blind\b'],
    "quasi_experimental": [r'\bquasi.experiment\w+\b', r'\bnatural experiment\b', r'\bdifference.in.difference\b', r'\bregression discontinuity\b', r'\binstrumental variable\b'],
    "longitudinal_cohort": [r'\blongitudinal\b', r'\bcohort\b', r'\bprospective\b', r'\bfollow.up\b', r'\bpanel\s+data\b'],
    "cross_sectional": [r'\bcross.sectional\b', r'\bsurvey\b', r'\bquestionnaire\b', r'\bself.report\b'],
    "case_control": [r'\bcase.control\b', r'\bretrospective\b'],
    "case_study": [r'\bcase stud\w+\b', r'\bsingle case\b', r'\bin.depth analysis\b'],
    "qualitative": [r'\bqualitative\b', r'\bethnograph\w+\b', r'\bgrounded theory\b', r'\bthematic analysis\b', r'\binterview\w*\b', r'\bfocus group\b', r'\bphenomenolog\w+\b'],
    "mixed_methods": [r'\bmixed.method\w*\b', r'\bmulti.method\b'],
    "systematic_review": [r'\bsystematic review\b', r'\bPRISMA\b', r'\bmeta.analys\w+\b'],
    "meta_analysis": [r'\bmeta.analys\w+\b', r'\bforest plot\b', r'\bpooled\s+effect\b'],
    "computational": [r'\bmachine learning\b', r'\bsimulation\b', r'\bagent.based\b', r'\bcomputational\b', r'\bNLP\b', r'\bdeep learning\b'],
    "theoretical": [r'\bconceptual\s+(?:framework|model|paper)\b', r'\btheoretical\s+(?:framework|model|contribution)\b'],
}


def classify_methodology(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Auto-classify papers by research methodology using abstract/fulltext keyword analysis."""
    paper_id = arguments.get("paper_id")
    batch = arguments.get("batch", False)

    papers = _get_papers(ctx)
    if not papers:
        return json.dumps({"error": "No papers in database"})

    if paper_id:
        papers = [p for p in papers if p.get("id") == paper_id]

    claims = _get_claims(ctx)
    # Build paper_id -> study_designs from claims
    claim_designs: dict[int, list[str]] = defaultdict(list)
    for c in claims:
        if c.get("study_design"):
            claim_designs[c.get("paper_id", 0)].append(c["study_design"])

    results = []
    method_counts: Counter[str] = Counter()

    for p in papers:
        text = " ".join(filter(None, [
            p.get("title", ""),
            p.get("abstract", ""),
            " ".join(claim_designs.get(p.get("id", 0), [])),
        ])).lower()

        scores: dict[str, int] = {}
        for method, patterns in _METHOD_PATTERNS.items():
            count = sum(len(re.findall(pat, text, re.I)) for pat in patterns)
            if count > 0:
                scores[method] = count

        primary = max(scores, key=scores.get) if scores else "unclassified"
        confidence = min(1.0, max(scores.values()) / 3.0) if scores else 0.0
        method_counts[primary] += 1

        results.append({
            "paper_id": p.get("id"),
            "title": (p.get("title") or "")[:100],
            "primary_method": primary,
            "confidence": round(confidence, 2),
            "all_methods": sorted(scores.keys()),
            "year": p.get("year"),
        })

    # Summary
    total = len(results) or 1
    distribution = {k: {"count": v, "pct": round(v / total * 100, 1)} for k, v in method_counts.most_common()}

    return json.dumps({
        "classifications": results if paper_id or len(results) <= 50 else results[:50],
        "distribution": distribution,
        "total_papers": len(results),
        "diversity_index": round(len(method_counts) / len(_METHOD_PATTERNS) * 100, 1),
    })


# ─────────────────────────────────────────────────────────────────────────────
# 4. AGGREGATE SAMPLES
# ─────────────────────────────────────────────────────────────────────────────

_REGION_MAP: dict[str, str] = {
    "usa": "North America", "united states": "North America", "canada": "North America",
    "uk": "Europe", "united kingdom": "Europe", "england": "Europe", "germany": "Europe",
    "france": "Europe", "spain": "Europe", "italy": "Europe", "netherlands": "Europe",
    "sweden": "Europe", "norway": "Europe", "denmark": "Europe", "finland": "Europe",
    "switzerland": "Europe", "austria": "Europe", "belgium": "Europe", "portugal": "Europe",
    "china": "Asia-Pacific", "japan": "Asia-Pacific", "korea": "Asia-Pacific",
    "india": "Asia-Pacific", "australia": "Asia-Pacific", "singapore": "Asia-Pacific",
    "taiwan": "Asia-Pacific", "indonesia": "Asia-Pacific", "malaysia": "Asia-Pacific",
    "brazil": "Latin America", "mexico": "Latin America", "colombia": "Latin America",
    "argentina": "Latin America", "chile": "Latin America",
    "nigeria": "Africa", "south africa": "Africa", "kenya": "Africa", "ghana": "Africa",
    "egypt": "Middle East & North Africa", "iran": "Middle East & North Africa",
    "saudi arabia": "Middle East & North Africa", "turkey": "Middle East & North Africa",
}


def aggregate_samples(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Aggregate sample sizes, geographies, and populations across the evidence base."""
    theme = arguments.get("theme", "")

    claims = _get_claims(ctx)
    papers = _get_papers(ctx)

    if not claims:
        return json.dumps({"error": "No claims in database"})

    if theme:
        theme_lower = theme.lower()
        claims = [c for c in claims if theme_lower in str(c.get("claim_text", "")).lower()]

    paper_map = {p.get("id"): p for p in papers}

    # Aggregate sample sizes
    sample_sizes: list[int] = []
    regions: Counter[str] = Counter()
    populations: Counter[str] = Counter()
    designs: Counter[str] = Counter()
    paper_samples: dict[int, int] = {}

    for c in claims:
        n = _parse_sample_size(c.get("sample_size"))
        pid = c.get("paper_id")
        if n and pid and pid not in paper_samples:
            paper_samples[pid] = n
            sample_sizes.append(n)

        # Extract geography from paper metadata
        p = paper_map.get(pid, {})
        abstract = str(p.get("abstract", "")).lower() + " " + str(c.get("claim_text", "")).lower()
        for country, region in _REGION_MAP.items():
            if country in abstract:
                regions[region] += 1
                break

        if c.get("study_design"):
            designs[c["study_design"]] += 1

        if c.get("population"):
            populations[c["population"]] += 1

    total_n = sum(sample_sizes)
    total_studies = len(sample_sizes)

    result: dict[str, Any] = {
        "total_sample_size": total_n,
        "total_studies_with_n": total_studies,
        "sample_size_stats": {},
        "geographic_distribution": dict(regions.most_common()),
        "population_types": dict(populations.most_common(15)),
        "study_designs": dict(designs.most_common(10)),
    }

    if sample_sizes:
        arr = np.array(sample_sizes)
        result["sample_size_stats"] = {
            "mean": int(np.mean(arr)),
            "median": int(np.median(arr)),
            "min": int(np.min(arr)),
            "max": int(np.max(arr)),
            "std": int(np.std(arr)),
            "q25": int(np.percentile(arr, 25)),
            "q75": int(np.percentile(arr, 75)),
        }

    # WEIRD bias check
    weird_count = regions.get("North America", 0) + regions.get("Europe", 0) + regions.get("Asia-Pacific", 0)
    total_regional = sum(regions.values()) or 1
    result["weird_bias_pct"] = round(weird_count / total_regional * 100, 1) if regions else 0
    result["geographic_diversity"] = len(regions)

    return json.dumps(result)


# ─────────────────────────────────────────────────────────────────────────────
# 5. META-ANALYZE
# ─────────────────────────────────────────────────────────────────────────────

def meta_analyze(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Run basic meta-analysis on extracted effect sizes: pooled estimate, I², Q, Egger's test."""
    outcome = arguments.get("outcome", "")
    metric = arguments.get("metric", "auto")  # auto, cohens_d, odds_ratio, correlation

    claims = _get_claims(ctx)
    if not claims:
        return json.dumps({"error": "No claims in database"})

    # Filter claims with effect sizes
    if outcome:
        outcome_lower = outcome.lower()
        claims = [c for c in claims if outcome_lower in str(c.get("claim_text", "")).lower()
                  or outcome_lower in str(c.get("theme", "")).lower()]

    effects: list[dict[str, Any]] = []
    for c in claims:
        es = _parse_effect_size(c.get("effect_size"))
        if es is not None:
            n = _parse_sample_size(c.get("sample_size")) or 100  # default N for SE estimation
            effects.append({
                "paper_id": c.get("paper_id"),
                "claim_id": c.get("id"),
                "effect_size": es,
                "sample_size": n,
                "claim_text": (c.get("claim_text") or "")[:100],
            })

    if len(effects) < 2:
        return json.dumps({
            "error": f"Need >= 2 effect sizes, found {len(effects)}",
            "effects_found": effects,
            "recommendation": "Qualitative synthesis only — insufficient quantitative data for meta-analysis",
        })

    es_values = np.array([e["effect_size"] for e in effects])
    n_values = np.array([e["sample_size"] for e in effects])

    # Compute standard errors (approximation: SE ≈ 1/sqrt(N) for standardized effects)
    se_values = 1.0 / np.sqrt(n_values)

    # Inverse-variance weighted pooled estimate
    weights = 1.0 / (se_values ** 2)
    pooled = float(np.sum(weights * es_values) / np.sum(weights))
    pooled_se = float(1.0 / np.sqrt(np.sum(weights)))

    # Q statistic (heterogeneity)
    q_stat = float(np.sum(weights * (es_values - pooled) ** 2))
    df = len(effects) - 1
    q_pvalue = 1.0
    try:
        from scipy import stats as scipy_stats
        q_pvalue = float(1 - scipy_stats.chi2.cdf(q_stat, df))
    except ImportError:
        q_pvalue = 0.05 if q_stat > df * 2 else 0.5  # rough approximation

    # I² statistic
    i_squared = max(0, (q_stat - df) / q_stat * 100) if q_stat > 0 else 0

    # Egger's test (publication bias) — regression of ES on SE
    egger_bias = None
    egger_pvalue = None
    if len(effects) >= 5:
        try:
            from scipy import stats as scipy_stats
            slope, intercept, r_value, p_value, std_err = scipy_stats.linregress(se_values, es_values)
            egger_bias = round(float(intercept), 3)
            egger_pvalue = round(float(p_value), 4)
        except (ImportError, Exception):
            pass

    # Forest plot data
    forest = []
    for e in effects:
        se = 1.0 / math.sqrt(e["sample_size"])
        forest.append({
            "paper_id": e["paper_id"],
            "effect": round(e["effect_size"], 3),
            "ci_lower": round(e["effect_size"] - 1.96 * se, 3),
            "ci_upper": round(e["effect_size"] + 1.96 * se, 3),
            "weight": round(float(1.0 / se ** 2 / np.sum(weights) * 100), 1),
            "label": e["claim_text"][:60],
        })

    # Interpretation
    heterogeneity = "low" if i_squared < 25 else "moderate" if i_squared < 75 else "high"
    pub_bias = "undetected"
    if egger_pvalue is not None and egger_pvalue < 0.1:
        pub_bias = "detected (Egger's p < 0.1)"

    return json.dumps({
        "pooled_effect": round(pooled, 3),
        "pooled_se": round(pooled_se, 3),
        "pooled_ci": [round(pooled - 1.96 * pooled_se, 3), round(pooled + 1.96 * pooled_se, 3)],
        "n_studies": len(effects),
        "total_n": int(np.sum(n_values)),
        "heterogeneity": {
            "Q": round(q_stat, 2),
            "df": df,
            "Q_pvalue": round(q_pvalue, 4),
            "I_squared": round(i_squared, 1),
            "interpretation": heterogeneity,
        },
        "publication_bias": {
            "egger_intercept": egger_bias,
            "egger_pvalue": egger_pvalue,
            "interpretation": pub_bias,
        },
        "forest_plot": forest,
        "direction_consistency": round(float(np.sum(es_values > 0) / len(es_values) * 100), 1) if len(es_values) > 0 else 0,
        "effect_range": [round(float(np.min(es_values)), 3), round(float(np.max(es_values)), 3)],
        "median_effect": round(float(np.median(es_values)), 3),
    })


# ─────────────────────────────────────────────────────────────────────────────
# 6. MAP THEORIES
# ─────────────────────────────────────────────────────────────────────────────

_THEORY_PATTERNS: dict[str, list[str]] = {
    "Institutional Theory": [r'\binstitutional\s+(?:theory|perspective|framework|logic|void)\b', r'\bDiMaggio\b', r'\bPowell\b', r'\bScott\b', r'\bisomorphi\w+\b'],
    "Resource-Based View": [r'\bresource.based\s+view\b', r'\bRBV\b', r'\bBarney\b', r'\bVRIN\b', r'\bdynamic\s+capabilit\w+\b'],
    "Transaction Cost Economics": [r'\btransaction\s+cost\w*\b', r'\bTCE\b', r'\bWilliamson\b', r'\basset\s+specificity\b'],
    "Agency Theory": [r'\bagency\s+theory\b', r'\bprincipal.agent\b', r'\bmoral\s+hazard\b', r'\badverse\s+selection\b', r'\bJensen\b.*\bMeckling\b'],
    "Stakeholder Theory": [r'\bstakeholder\s+theory\b', r'\bFreeman\b.*\bstakeholder\b'],
    "Network Theory": [r'\bnetwork\s+theory\b', r'\bsocial\s+network\b', r'\bembeddedness\b', r'\bGranovetter\b', r'\bstructural\s+holes?\b'],
    "Signaling Theory": [r'\bsignaling\s+theory\b', r'\bSpence\b.*\bsignal\b', r'\binformation\s+asymmetr\w+\b'],
    "Technology Acceptance Model": [r'\bTAM\b', r'\btechnology\s+acceptance\b', r'\bDavis\b.*\bperceived\s+(?:ease|usefulness)\b', r'\bUTAUT\b'],
    "Diffusion of Innovations": [r'\bdiffusion\s+of\s+innovation\b', r'\bRogers\b.*\bdiffusion\b', r'\bearly\s+adopt\w+\b'],
    "Legitimacy Theory": [r'\blegitimacy\s+theory\b', r'\borganizational\s+legitimacy\b', r'\bSuchman\b'],
    "Knowledge-Based View": [r'\bknowledge.based\s+view\b', r'\bKBV\b', r'\btacit\s+knowledge\b', r'\bNonaka\b'],
    "Behavioral Economics": [r'\bbehavior\w+\s+economics?\b', r'\bnudge\b', r'\bprospect\s+theory\b', r'\bKahneman\b', r'\bbounded\s+rationality\b'],
    "Absorptive Capacity": [r'\babsorptive\s+capacity\b', r'\bCohen\b.*\bLevinthal\b'],
    "Upper Echelons Theory": [r'\bupper\s+echelons?\b', r'\bHambrick\b', r'\bTMT\b.*\b(?:composition|characteristics)\b'],
    "Contingency Theory": [r'\bcontingency\s+theory\b', r'\bfit\s+between\b.*\bstructure\b'],
    "Social Exchange Theory": [r'\bsocial\s+exchange\b', r'\breciprocity\b', r'\bBlau\b'],
    "Structuration Theory": [r'\bstructuration\b', r'\bGiddens\b', r'\bduality\s+of\s+structure\b'],
    "Critical Theory": [r'\bcritical\s+theory\b', r'\bpower\s+relations\b', r'\bhegemony\b', r'\bFoucault\b'],
    "Innovation Systems": [r'\binnovation\s+system\w*\b', r'\bnational\s+innovation\b', r'\btriple\s+helix\b'],
    "Human Capital Theory": [r'\bhuman\s+capital\b', r'\bBecker\b.*\bcapital\b'],
}


def map_theories(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Extract and map theoretical frameworks used across the corpus."""
    paper_id = arguments.get("paper_id")

    papers = _get_papers(ctx)
    claims = _get_claims(ctx)

    if not papers:
        return json.dumps({"error": "No papers in database"})

    if paper_id:
        papers = [p for p in papers if p.get("id") == paper_id]

    # Build text corpus per paper
    paper_claims: dict[int, list[str]] = defaultdict(list)
    for c in claims:
        pid = c.get("paper_id")
        if pid:
            paper_claims[pid].append(c.get("claim_text", ""))

    theory_papers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    paper_theories: dict[int, list[str]] = defaultdict(list)
    theory_counts: Counter[str] = Counter()

    for p in papers:
        text = " ".join(filter(None, [
            p.get("title", ""),
            p.get("abstract", ""),
        ] + paper_claims.get(p.get("id", 0), [])))

        for theory, patterns in _THEORY_PATTERNS.items():
            matches = sum(len(re.findall(pat, text, re.I)) for pat in patterns)
            if matches > 0:
                theory_counts[theory] += 1
                paper_theories[p.get("id", 0)].append(theory)
                theory_papers[theory].append({
                    "paper_id": p.get("id"),
                    "title": (p.get("title") or "")[:80],
                    "year": p.get("year"),
                    "match_strength": matches,
                })

    # Co-occurrence: which theories appear together
    cooccurrence: Counter[tuple[str, str]] = Counter()
    for pid, theories in paper_theories.items():
        for i, t1 in enumerate(theories):
            for t2 in theories[i + 1:]:
                pair = tuple(sorted([t1, t2]))
                cooccurrence[pair] += 1

    # Underused theories (in pattern list but rarely found)
    underused = [t for t in _THEORY_PATTERNS if theory_counts.get(t, 0) <= 1]

    return json.dumps({
        "theories": {t: {"count": c, "papers": theory_papers[t][:5]} for t, c in theory_counts.most_common()},
        "cooccurrence": [{"theories": list(pair), "count": count} for pair, count in cooccurrence.most_common(10)],
        "underused_theories": underused[:10],
        "papers_without_theory": sum(1 for p in papers if p.get("id") not in paper_theories),
        "total_papers": len(papers),
        "total_theories_found": len(theory_counts),
    })


# ─────────────────────────────────────────────────────────────────────────────
# 7. ANALYZE TEMPORAL TRENDS
# ─────────────────────────────────────────────────────────────────────────────

def analyze_temporal_trends(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Analyze publication trends, method evolution, and finding consistency over time."""
    theme = arguments.get("theme", "")

    papers = _get_papers(ctx)
    claims = _get_claims(ctx)

    if not papers:
        return json.dumps({"error": "No papers in database"})

    # Publication counts by year
    year_counts: Counter[int] = Counter()
    year_methods: dict[int, Counter[str]] = defaultdict(Counter)
    year_effects: dict[int, list[float]] = defaultdict(list)

    paper_years: dict[int, int] = {}
    for p in papers:
        y = p.get("year")
        if y and isinstance(y, int) and 1990 <= y <= 2030:
            year_counts[y] += 1
            paper_years[p.get("id", 0)] = y

    for c in claims:
        pid = c.get("paper_id")
        y = paper_years.get(pid)
        if not y:
            continue

        if theme and theme.lower() not in str(c.get("claim_text", "")).lower():
            continue

        if c.get("study_design"):
            year_methods[y][c["study_design"]] += 1

        es = _parse_effect_size(c.get("effect_size"))
        if es is not None:
            year_effects[y].append(es)

    # Build timeline
    all_years = sorted(year_counts.keys())
    timeline = []
    for y in all_years:
        entry: dict[str, Any] = {
            "year": y,
            "papers": year_counts[y],
        }
        if year_methods.get(y):
            entry["dominant_method"] = year_methods[y].most_common(1)[0][0]
        if year_effects.get(y):
            effects = year_effects[y]
            entry["mean_effect"] = round(sum(effects) / len(effects), 3)
            entry["n_effects"] = len(effects)
        timeline.append(entry)

    # Recency analysis
    total = len(papers) or 1
    recent_5yr = sum(1 for p in papers if (p.get("year") or 0) >= 2021)
    recent_10yr = sum(1 for p in papers if (p.get("year") or 0) >= 2016)

    # Method evolution
    early_methods: Counter[str] = Counter()
    recent_methods: Counter[str] = Counter()
    mid_year = (min(all_years) + max(all_years)) // 2 if all_years else 2020
    for y, methods in year_methods.items():
        if y <= mid_year:
            early_methods += methods
        else:
            recent_methods += methods

    return json.dumps({
        "timeline": timeline,
        "recency": {
            "last_5_years_pct": round(recent_5yr / total * 100, 1),
            "last_10_years_pct": round(recent_10yr / total * 100, 1),
            "oldest_year": min(all_years) if all_years else None,
            "newest_year": max(all_years) if all_years else None,
        },
        "method_evolution": {
            "early_period": dict(early_methods.most_common(5)),
            "recent_period": dict(recent_methods.most_common(5)),
            "split_year": mid_year,
        },
        "total_papers": len(papers),
        "papers_with_year": len(paper_years),
    })


# ─────────────────────────────────────────────────────────────────────────────
# 8. GENERATE EVIDENCE TABLE
# ─────────────────────────────────────────────────────────────────────────────

def generate_evidence_table(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Auto-generate structured evidence tables from DB data."""
    table_type = arguments.get("table_type", "study_characteristics")

    papers = _get_papers(ctx)
    claims = _get_claims(ctx)

    if table_type == "study_characteristics":
        paper_map: dict[int, dict[str, Any]] = {}
        for p in papers:
            pid = p.get("id")
            if pid:
                paper_map[pid] = p

        # Gather per-paper info from claims
        paper_data: dict[int, dict[str, Any]] = {}
        for c in claims:
            pid = c.get("paper_id")
            if pid and pid not in paper_data:
                p = paper_map.get(pid, {})
                authors = p.get("authors") or p.get("author") or ""
                if isinstance(authors, list):
                    first_author = authors[0] if authors else ""
                else:
                    first_author = str(authors).split(",")[0].strip()

                paper_data[pid] = {
                    "author": first_author,
                    "year": p.get("year"),
                    "title": (p.get("title") or "")[:80],
                    "design": c.get("study_design", ""),
                    "sample_size": c.get("sample_size", ""),
                    "population": c.get("population", ""),
                    "main_finding": (c.get("claim_text") or "")[:120],
                    "effect_size": c.get("effect_size", ""),
                    "source": p.get("source", ""),
                }

        rows = sorted(paper_data.values(), key=lambda x: x.get("year") or 0)
        return json.dumps({"table_type": "study_characteristics", "columns": ["author", "year", "design", "sample_size", "population", "main_finding", "effect_size"], "rows": rows})

    elif table_type == "grade_summary":
        grade_data = _get_grade(ctx)
        rows = []
        for g in grade_data:
            rows.append({
                "outcome": g.get("outcome", ""),
                "n_studies": g.get("n_studies", 0),
                "designs": g.get("study_designs", ""),
                "risk_of_bias": g.get("risk_of_bias_rating", ""),
                "inconsistency": g.get("inconsistency", ""),
                "indirectness": g.get("indirectness", ""),
                "imprecision": g.get("imprecision", ""),
                "pub_bias": g.get("publication_bias", ""),
                "certainty": g.get("certainty", ""),
                "direction": g.get("direction", ""),
            })
        return json.dumps({"table_type": "grade_summary", "columns": ["outcome", "n_studies", "designs", "risk_of_bias", "inconsistency", "indirectness", "imprecision", "pub_bias", "certainty", "direction"], "rows": rows})

    elif table_type == "rob_assessment":
        rob_data = _get_rob(ctx)
        paper_map2 = {p.get("id"): p for p in papers}
        rows = []
        for r in rob_data:
            p = paper_map2.get(r.get("paper_id"), {})
            authors = p.get("authors") or p.get("author") or ""
            first_author = authors[0] if isinstance(authors, list) and authors else str(authors).split(",")[0].strip()
            rows.append({
                "study": f"{first_author} ({p.get('year', '')})",
                "paper_id": r.get("paper_id"),
                "selection": r.get("selection_bias", "unclear"),
                "performance": r.get("performance_bias", "unclear"),
                "detection": r.get("detection_bias", "unclear"),
                "attrition": r.get("attrition_bias", "unclear"),
                "reporting": r.get("reporting_bias", "unclear"),
                "overall": r.get("overall_risk", "unclear"),
            })
        return json.dumps({"table_type": "rob_assessment", "columns": ["study", "selection", "performance", "detection", "attrition", "reporting", "overall"], "rows": rows})

    elif table_type == "effect_sizes":
        rows = []
        paper_map3 = {p.get("id"): p for p in papers}
        for c in claims:
            es = c.get("effect_size")
            if not es:
                continue
            p = paper_map3.get(c.get("paper_id"), {})
            rows.append({
                "study": f"{str(p.get('authors', '')).split(',')[0]} ({p.get('year', '')})",
                "outcome": (c.get("claim_text") or "")[:80],
                "effect_size": es,
                "ci": c.get("confidence_interval", ""),
                "p_value": c.get("p_value", ""),
                "sample_size": c.get("sample_size", ""),
                "design": c.get("study_design", ""),
            })
        return json.dumps({"table_type": "effect_sizes", "columns": ["study", "outcome", "effect_size", "ci", "p_value", "sample_size", "design"], "rows": rows})

    return json.dumps({"error": f"Unknown table_type: {table_type}. Use: study_characteristics, grade_summary, rob_assessment, effect_sizes"})


# ─────────────────────────────────────────────────────────────────────────────
# 9. CHECK CLAIM CONSISTENCY
# ─────────────────────────────────────────────────────────────────────────────

_CITE_PATTERN = re.compile(r'\(([A-Z][a-zà-ÿ]+(?:\s+(?:et\s+al\.?|&\s+[A-Z][a-zà-ÿ]+))?),?\s*(\d{4})\)')


def check_claim_consistency(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Cross-check written section text against actual DB claims to detect hallucinations and overclaiming."""
    section_text = arguments.get("section_text", "")
    section_name = arguments.get("section_name", "")

    if not section_text:
        return json.dumps({"error": "section_text required"})

    claims = _get_claims(ctx)
    papers = _get_papers(ctx)

    paper_map = {p.get("id"): p for p in papers}
    # Build lookup: (author_fragment, year) -> paper
    author_year_map: dict[tuple[str, int], dict[str, Any]] = {}
    for p in papers:
        authors = p.get("authors") or p.get("author") or ""
        if isinstance(authors, list):
            first = authors[0] if authors else ""
        else:
            first = str(authors).split(",")[0].strip()
        last_name = first.split()[-1] if first.split() else ""
        y = p.get("year")
        if last_name and y:
            author_year_map[(last_name.lower(), int(y))] = p

    # Extract citations from text
    citations_found = _CITE_PATTERN.findall(section_text)
    matched = []
    unmatched = []

    for author, year in citations_found:
        author_clean = author.replace(" et al.", "").replace(" et al", "").strip().split("&")[0].strip()
        last_name = author_clean.split()[-1].lower() if author_clean.split() else ""
        year_int = int(year)

        paper = author_year_map.get((last_name, year_int))
        if paper:
            matched.append({"citation": f"({author}, {year})", "paper_id": paper.get("id"), "title": (paper.get("title") or "")[:80]})
        else:
            unmatched.append({"citation": f"({author}, {year})", "status": "NOT_IN_DATABASE"})

    # Overclaiming detection
    _OVERCLAIM_PATTERNS = [
        (r'\bproves?\s+that\b', "causal_from_correlation", "Use 'suggests' or 'indicates'"),
        (r'\bdefinitively\s+(?:show|demonstrate|establish)\w*\b', "definitive_claim", "Remove 'definitively'"),
        (r'\ball\s+(?:evidence|studies|research)\s+(?:show|suggest|indicate)\b', "universal_claim", "Specify 'most' or 'the majority of'"),
        (r'\bthis\s+is\s+the\s+first\b', "first_claim", "Verify — is it actually the first?"),
        (r'\bclearly\s+(?:show|demonstrate|establish)\w*\b', "certainty_overclaim", "Remove 'clearly' or add hedging"),
        (r'\bcause[sd]?\b(?!.*\bcaution\b)', "causal_language", "Check if study design supports causal claims"),
    ]

    overclaims = []
    for pattern, issue_type, suggestion in _OVERCLAIM_PATTERNS:
        for m in re.finditer(pattern, section_text, re.I):
            start = max(0, m.start() - 50)
            end = min(len(section_text), m.end() + 50)
            overclaims.append({
                "type": issue_type,
                "text": section_text[start:end].strip(),
                "suggestion": suggestion,
            })

    return json.dumps({
        "section": section_name,
        "citations_found": len(citations_found),
        "matched_to_db": len(matched),
        "unmatched_phantom": unmatched,
        "phantom_count": len(unmatched),
        "overclaiming_flags": overclaims,
        "matched_details": matched[:20],
    })


# ─────────────────────────────────────────────────────────────────────────────
# 10. COMPUTE KAPPA
# ─────────────────────────────────────────────────────────────────────────────

def compute_kappa(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Compute inter-rater reliability (Cohen's kappa) for RoB assessments or triage ratings."""
    assessment_type = arguments.get("assessment_type", "risk_of_bias")

    if assessment_type == "risk_of_bias":
        rob_data = _get_rob(ctx)
        if len(rob_data) < 5:
            return json.dumps({"error": f"Need >= 5 RoB assessments, found {len(rob_data)}"})

        # Simulate dual review by splitting assessments and computing self-consistency
        # In practice: compare selection_bias vs detection_bias agreement patterns
        # For real kappa: need two independent raters — compute agreement across bias dimensions
        dimensions = ["selection_bias", "performance_bias", "detection_bias", "attrition_bias", "reporting_bias"]
        _RISK_LEVELS = {"low": 0, "unclear": 1, "moderate": 1, "high": 2}

        # Compute agreement between overall_risk and the majority of individual dimensions
        agreements = 0
        total = 0
        confusion: dict[tuple[str, str], int] = Counter()

        for r in rob_data:
            # Predicted overall from individual dimensions
            dim_scores = [_RISK_LEVELS.get(str(r.get(d, "unclear")).lower(), 1) for d in dimensions]
            median_score = sorted(dim_scores)[len(dim_scores) // 2]
            predicted = ["low", "unclear", "high"][median_score]
            actual = str(r.get("overall_risk", "unclear")).lower()
            if actual not in _RISK_LEVELS:
                actual = "unclear"
            confusion[(predicted, actual)] += 1
            if predicted == actual:
                agreements += 1
            total += 1

        # Cohen's kappa
        po = agreements / total if total > 0 else 0  # observed agreement
        # Expected agreement under independence
        cats = ["low", "unclear", "high"]
        pe = 0
        for cat in cats:
            p_pred = sum(confusion.get((cat, a), 0) for a in cats) / total if total > 0 else 0
            p_actual = sum(confusion.get((p, cat), 0) for p in cats) / total if total > 0 else 0
            pe += p_pred * p_actual

        kappa = (po - pe) / (1 - pe) if (1 - pe) > 0 else 0

        interpretation = "poor" if kappa < 0.2 else "fair" if kappa < 0.4 else "moderate" if kappa < 0.6 else "substantial" if kappa < 0.8 else "almost_perfect"

        return json.dumps({
            "assessment_type": "risk_of_bias",
            "method": "dimension_majority_vs_overall",
            "kappa": round(kappa, 3),
            "observed_agreement": round(po, 3),
            "expected_agreement": round(pe, 3),
            "interpretation": interpretation,
            "n_assessments": total,
            "confusion_matrix": {f"{p}->{a}": c for (p, a), c in confusion.items()},
            "note": "Computed as consistency between individual bias dimensions and overall rating. For true inter-rater kappa, run two independent assessment passes.",
        })

    elif assessment_type == "triage":
        papers = _get_papers(ctx)
        if not papers:
            return json.dumps({"error": "No papers in database"})

        # Compute rating distribution consistency
        scores = [p.get("relevance_score") or p.get("triage_score") for p in papers]
        scores = [s for s in scores if s is not None]

        if len(scores) < 10:
            return json.dumps({"error": f"Need >= 10 scored papers, found {len(scores)}"})

        arr = np.array(scores)
        # Report distribution stats as proxy for rating quality
        return json.dumps({
            "assessment_type": "triage",
            "n_scored": len(scores),
            "mean_score": round(float(np.mean(arr)), 3),
            "std_score": round(float(np.std(arr)), 3),
            "score_range": [round(float(np.min(arr)), 2), round(float(np.max(arr)), 2)],
            "above_threshold": int(np.sum(arr >= 0.6)),
            "below_threshold": int(np.sum(arr < 0.6)),
            "note": "Single-rater scores. For true kappa, run a second triage pass and compare.",
        })

    return json.dumps({"error": f"Unknown assessment_type: {assessment_type}. Use: risk_of_bias, triage"})
