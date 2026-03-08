# Location: ara/tools/novelty.py
# Purpose: Epistemic tools for novelty scoring and gap analysis in research literature
# Functions: score_novelty, identify_gaps
# Calls: sqlite3 for database queries, re for text processing
# Imports: json, logging, re, sqlite3, string, collections

from __future__ import annotations

import json
import logging
import re
import sqlite3
import string
from collections import Counter
from typing import Any

_log = logging.getLogger(__name__)


# ── Common Stop Words (English) ──────────────────────────────────────────
_STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "can", "this", "that", "these",
    "those", "i", "you", "he", "she", "it", "we", "they", "what", "which",
    "who", "whom", "whose", "when", "where", "why", "how", "not", "no",
    "yes", "as", "if", "into", "through", "during", "before", "after",
    "above", "below", "between", "under", "over", "out", "off", "up", "down",
    "all", "each", "every", "both", "few", "more", "most", "other", "some",
    "such", "only", "own", "same", "so", "than", "too", "very", "just",
    "about", "also", "just", "very", "good", "bad", "new", "old", "first",
    "last", "long", "short", "high", "low", "best", "worst", "us", "im",
}


def _tokenize_text(text: str) -> list[str]:
    """Extract lowercase word tokens from text, filtering stop words and short words."""
    if not text:
        return []

    # Convert to lowercase and remove punctuation except hyphens
    text = text.lower()
    # Split on whitespace and punctuation (but keep hyphens for compound words)
    words = re.findall(r"\b[\w'-]+\b", text)

    # Filter: stop words, short words (<4 chars), and non-alphanumeric-heavy tokens
    filtered = []
    for word in words:
        word = word.strip("'-")  # Clean trailing punctuation
        if (len(word) >= 4 and
            word not in _STOP_WORDS and
            sum(1 for c in word if c.isalpha()) >= 2):  # At least 2 letters
            filtered.append(word)

    return filtered


def _compute_tfidf_vector(text: str) -> dict[str, float]:
    """Build a simple TF vector from text (normalized by token count)."""
    tokens = _tokenize_text(text)
    if not tokens:
        return {}

    counts = Counter(tokens)
    total = sum(counts.values())
    return {token: count / total for token, count in counts.items()}


def _cosine_similarity(vec1: dict[str, float], vec2: dict[str, float]) -> float:
    """Compute cosine similarity between two TF vectors."""
    if not vec1 or not vec2:
        return 0.0

    # Dot product
    dot = sum(vec1.get(term, 0) * vec2.get(term, 0) for term in vec1.keys() if term in vec2)

    # Magnitudes
    mag1 = sum(v * v for v in vec1.values()) ** 0.5
    mag2 = sum(v * v for v in vec2.values()) ** 0.5

    if mag1 == 0 or mag2 == 0:
        return 0.0

    return dot / (mag1 * mag2)


def score_novelty(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """
    Compute novelty score for a research finding by comparing against existing literature.

    Novelty is measured as 1.0 - max_similarity, where similarity is computed
    using TF-based cosine similarity against top papers in the session database.

    Parameters:
        finding (str): The research finding or claim to score for novelty
        comparison_query (str): Optional query to find comparison papers (defaults to first 100 chars)

    Returns:
        JSON string with novelty score (0.0-1.0), assessment, paper count, and similar papers
    """
    finding = arguments.get("finding", "").strip()
    if not finding:
        return json.dumps({"error": "finding parameter is required and cannot be empty"})

    # Use first 100 chars of finding as query if not provided
    comparison_query = arguments.get("comparison_query", "").strip()
    if not comparison_query:
        comparison_query = finding[:100]

    # Get database connection
    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if not db or not session_id:
        return json.dumps({"error": "session_id or db not available in context"})

    try:
        # Fetch top 20 papers by citation count with abstracts
        rows = db._conn.execute(
            """
            SELECT paper_id, title, abstract, citation_count
            FROM papers
            WHERE session_id = ? AND abstract IS NOT NULL AND abstract != ''
            ORDER BY citation_count DESC LIMIT 20
            """,
            (session_id,)
        ).fetchall()

        if not rows:
            return json.dumps({
                "novelty_score": 1.0,
                "assessment": "high",
                "papers_compared": 0,
                "note": "No papers with abstracts found in session database"
            })

        # Compute TF vector for the finding
        finding_vec = _compute_tfidf_vector(finding)
        if not finding_vec:
            return json.dumps({
                "novelty_score": 0.5,
                "assessment": "unknown",
                "papers_compared": 0,
                "note": "Finding text does not contain substantive terms for comparison"
            })

        # Compare against each paper's title + abstract
        similarities = []
        most_similar_papers = []

        for row in rows:
            paper_id, title, abstract, citation_count = row
            combined_text = f"{title} {abstract}" if abstract else title
            paper_vec = _compute_tfidf_vector(combined_text)

            if paper_vec:
                sim = _cosine_similarity(finding_vec, paper_vec)
                similarities.append(sim)
                most_similar_papers.append({
                    "paper_id": paper_id,
                    "similarity": round(sim, 3),
                    "citations": citation_count or 0
                })

        if not similarities:
            return json.dumps({
                "novelty_score": 1.0,
                "assessment": "high",
                "papers_compared": len(rows),
                "note": "No comparable papers found"
            })

        # Sort by similarity descending and take top 5
        most_similar_papers.sort(key=lambda x: x["similarity"], reverse=True)
        top_similar = most_similar_papers[:5]

        # Novelty = 1.0 - max(similarities)
        max_similarity = max(similarities)
        novelty_score = 1.0 - max_similarity
        avg_similarity = sum(similarities) / len(similarities)

        # Assessment categories
        if novelty_score >= 0.8:
            assessment = "high"
        elif novelty_score >= 0.5:
            assessment = "moderate"
        else:
            assessment = "low"

        return json.dumps({
            "novelty_score": round(novelty_score, 3),
            "assessment": assessment,
            "papers_compared": len(rows),
            "max_similarity": round(max_similarity, 3),
            "avg_similarity": round(avg_similarity, 3),
            "most_similar_papers": top_similar
        })

    except Exception as exc:
        _log.exception("Error in score_novelty: %s", exc)
        return json.dumps({"error": f"Database error: {str(exc)}"})


def identify_gaps(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """
    Analyze recent literature to find research gaps, contradictions, and weak evidence areas.

    Performs programmatic gap analysis by identifying:
    - Terms mentioned in few papers but highly cited (potential weak spots)
    - Temporal gaps (concepts from old papers not recently revisited)
    - Methodological gaps (papers mentioning limitations, future research, gaps)

    Parameters:
        query (str): Search query to filter papers (required)
        domain (str): Optional domain label for gap categorization

    Returns:
        Plain text report with 2-5 identified gaps and supporting paper references
    """
    query = arguments.get("query", "").strip()
    domain = arguments.get("domain", "").strip()

    if not query:
        return json.dumps({"error": "query parameter is required"})

    # Get database connection
    db = ctx.get("db")
    session_id = ctx.get("session_id")
    if not db or not session_id:
        return json.dumps({"error": "session_id or db not available in context"})

    try:
        # Fetch papers from session DB, ordered by citation count
        rows = db._conn.execute(
            """
            SELECT paper_id, title, abstract, year, authors, citation_count
            FROM papers
            WHERE session_id = ?
            AND abstract IS NOT NULL
            AND abstract != ''
            ORDER BY citation_count DESC LIMIT 15
            """,
            (session_id,)
        ).fetchall()

        if not rows:
            return (
                "Gap Analysis Report\n"
                "===================\n\n"
                "No papers found in database. Unable to perform gap analysis.\n"
            )

        # Filter papers by query (case-insensitive match in title or abstract)
        query_lower = query.lower()
        matching_papers = []
        for row in rows:
            paper_id, title, abstract, year, authors, citations = row
            combined = f"{title} {abstract}".lower()
            if query_lower in combined:
                matching_papers.append({
                    "id": paper_id,
                    "title": title,
                    "abstract": abstract,
                    "year": year,
                    "authors": authors,
                    "citations": citations or 0
                })

        if len(matching_papers) < 3:
            return (
                f"Gap Analysis Report\n"
                f"===================\n\n"
                f"Query '{query}' matched only {len(matching_papers)} paper(s). "
                f"Please use a broader search term.\n"
            )

        # ── Gap Analysis: Term Frequency Analysis ────────────────────────
        all_tokens = []
        token_paper_count = Counter()  # How many papers mention each term
        year_tokens = {}  # year → [tokens]

        for paper in matching_papers:
            tokens = _tokenize_text(f"{paper['title']} {paper['abstract']}")
            all_tokens.extend(tokens)
            for token in set(tokens):  # Count each term once per paper
                token_paper_count[token] += 1

            year = paper["year"] or 2000
            if year not in year_tokens:
                year_tokens[year] = []
            year_tokens[year].extend(tokens)

        # ── Gap 1: Rare but Cited Terms (potential weak spots) ────────────
        gap1_terms = [
            (term, token_paper_count[term], sum(1 for p in matching_papers if term.lower() in f"{p['title']} {p['abstract']}".lower()))
            for term in set(all_tokens)
            if 1 <= token_paper_count[term] <= 2  # Mentioned in 1-2 papers only
        ]
        gap1_terms.sort(key=lambda x: (x[2], -x[1]), reverse=True)

        gap1_report = ""
        if gap1_terms:
            gap1_report = (
                "Gap 1: Underdeveloped Concepts (mentioned in <3 papers)\n"
                "───────────────────────────────────────────────────────\n"
            )
            for term, count, papers_with_term in gap1_terms[:3]:
                gap1_report += f"  • '{term}' appears in {papers_with_term} paper(s), cited {count} times total\n"
            gap1_report += "\n"

        # ── Gap 2: Temporal Gaps ──────────────────────────────────────────
        sorted_years = sorted(year_tokens.keys())
        temporal_gap = ""

        if sorted_years and len(sorted_years) >= 2:
            oldest_year = sorted_years[0]
            newest_year = sorted_years[-1]
            year_gap = newest_year - oldest_year

            if year_gap >= 5:
                temporal_gap = (
                    f"Gap 2: Temporal Research Gap\n"
                    f"──────────────────────────────\n"
                    f"  Latest research on '{query}' is from {newest_year}, but foundational work dates to {oldest_year}.\n"
                    f"  {year_gap}-year span suggests potential gap in recent empirical validation.\n\n"
                )

        # ── Gap 3: Methodological Gaps ────────────────────────────────────
        methodological_keywords = {
            "future": "Future research directions",
            "limitation": "Methodological limitations",
            "gap": "Explicitly acknowledged gap",
            "unexplored": "Unexplored territory",
            "needed": "Explicitly needed research"
        }

        gap3_papers = []
        for paper in matching_papers:
            abstract_lower = (paper["abstract"] or "").lower()
            for keyword in methodological_keywords.keys():
                if keyword in abstract_lower:
                    gap3_papers.append({
                        "title": paper["title"],
                        "keyword": methodological_keywords[keyword]
                    })
                    break

        gap3_report = ""
        if gap3_papers:
            gap3_report = (
                "Gap 3: Explicitly Acknowledged Methodological Gaps\n"
                "────────────────────────────────────────────────────\n"
            )
            for item in gap3_papers[:3]:
                gap3_report += f"  • {item['keyword']} (from: {item['title'][:60]}...)\n"
            gap3_report += "\n"

        # ── Compose Report ────────────────────────────────────────────────
        report = f"Gap Analysis Report: {query}\n"
        if domain:
            report += f"Domain: {domain}\n"
        report += f"Papers Analyzed: {len(matching_papers)}\n"
        report += "=" * 60 + "\n\n"

        report += gap1_report
        report += temporal_gap
        report += gap3_report

        if not (gap1_report or temporal_gap or gap3_report):
            report += (
                "No significant gaps detected in the current literature set.\n"
                "The evidence base appears well-developed and recently active.\n"
            )

        return report

    except Exception as exc:
        _log.exception("Error in identify_gaps: %s", exc)
        return f"Gap Analysis Error: {str(exc)}\n"


# ─────────────────────────────────────────────────────────────────────────────
# EFFECT SIZE CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────

def compute_effect_size(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Compute effect size metrics from reported statistics. JIBS requires
    explicit effect size reporting — this tool calculates Cohen's d, odds ratio,
    risk ratio, r-to-d conversion, and eta-squared from raw numbers."""
    import math

    metric = arguments.get("metric", "")
    if not metric:
        return json.dumps({"error": "metric parameter is required (cohens_d, odds_ratio, risk_ratio, r_to_d, eta_squared)"})

    try:
        if metric == "cohens_d":
            t_val = arguments.get("t_value")
            n1 = arguments.get("n1")
            n2 = arguments.get("n2")

            if t_val is not None and n1 and n2:
                # From t-statistic
                d = t_val * math.sqrt((1/n1) + (1/n2))
                se = math.sqrt((1/n1) + (1/n2) + (d**2 / (2*(n1+n2))))
            else:
                m1 = arguments.get("mean1")
                m2 = arguments.get("mean2")
                sd1 = arguments.get("sd1")
                sd2 = arguments.get("sd2")
                if m1 is None or m2 is None or sd1 is None or sd2 is None:
                    return json.dumps({"error": "cohens_d needs (mean1, mean2, sd1, sd2) or (t_value, n1, n2)"})
                n1 = n1 or 30
                n2 = n2 or 30
                pooled_sd = math.sqrt(((n1-1)*sd1**2 + (n2-1)*sd2**2) / (n1+n2-2))
                if pooled_sd == 0:
                    return json.dumps({"error": "Pooled SD is zero — cannot compute effect size"})
                d = (m1 - m2) / pooled_sd
                se = math.sqrt((1/n1) + (1/n2) + (d**2 / (2*(n1+n2))))

            # Interpretation (Cohen 1988)
            abs_d = abs(d)
            if abs_d < 0.2:
                interp = "negligible"
            elif abs_d < 0.5:
                interp = "small"
            elif abs_d < 0.8:
                interp = "medium"
            else:
                interp = "large"

            return json.dumps({
                "metric": "Cohen's d",
                "value": round(d, 4),
                "se": round(se, 4),
                "ci_95": [round(d - 1.96*se, 4), round(d + 1.96*se, 4)],
                "interpretation": interp,
                "reference": "Cohen (1988): small=0.2, medium=0.5, large=0.8",
            })

        elif metric == "odds_ratio":
            a = arguments.get("a")
            b = arguments.get("b")
            c = arguments.get("c")
            d_cell = arguments.get("d_cell")
            if not all(v is not None and v > 0 for v in [a, b, c, d_cell]):
                return json.dumps({"error": "odds_ratio needs a, b, c, d_cell (all > 0) from 2x2 table"})
            oratio = (a * d_cell) / (b * c)
            log_or = math.log(oratio)
            se_log = math.sqrt(1/a + 1/b + 1/c + 1/d_cell)
            ci_lower = math.exp(log_or - 1.96 * se_log)
            ci_upper = math.exp(log_or + 1.96 * se_log)
            return json.dumps({
                "metric": "Odds Ratio",
                "value": round(oratio, 4),
                "log_OR": round(log_or, 4),
                "se_log_OR": round(se_log, 4),
                "ci_95": [round(ci_lower, 4), round(ci_upper, 4)],
                "interpretation": "OR>1 = higher odds in group 1; OR<1 = lower odds in group 1; OR=1 = no difference",
            })

        elif metric == "risk_ratio":
            a = arguments.get("a")
            b = arguments.get("b")
            c = arguments.get("c")
            d_cell = arguments.get("d_cell")
            if not all(v is not None and v >= 0 for v in [a, b, c, d_cell]):
                return json.dumps({"error": "risk_ratio needs a, b, c, d_cell from 2x2 table"})
            risk1 = a / (a + b) if (a + b) > 0 else 0
            risk2 = c / (c + d_cell) if (c + d_cell) > 0 else 0
            if risk2 == 0:
                return json.dumps({"error": "Control group risk is zero — cannot compute risk ratio"})
            rr = risk1 / risk2
            log_rr = math.log(rr) if rr > 0 else 0
            se_log = math.sqrt(1/a - 1/(a+b) + 1/c - 1/(c+d_cell)) if a > 0 and c > 0 else 0
            ci_lower = math.exp(log_rr - 1.96 * se_log) if se_log > 0 else rr
            ci_upper = math.exp(log_rr + 1.96 * se_log) if se_log > 0 else rr
            return json.dumps({
                "metric": "Risk Ratio (Relative Risk)",
                "value": round(rr, 4),
                "risk_group1": round(risk1, 4),
                "risk_group2": round(risk2, 4),
                "ci_95": [round(ci_lower, 4), round(ci_upper, 4)],
                "interpretation": "RR>1 = higher risk in group 1; RR<1 = lower risk; RR=1 = equal risk",
            })

        elif metric == "r_to_d":
            r = arguments.get("r")
            if r is None:
                return json.dumps({"error": "r_to_d needs r (correlation coefficient)"})
            if abs(r) >= 1:
                return json.dumps({"error": "r must be between -1 and 1 (exclusive)"})
            d = (2 * r) / math.sqrt(1 - r**2)
            abs_d = abs(d)
            if abs_d < 0.2:
                interp = "negligible"
            elif abs_d < 0.5:
                interp = "small"
            elif abs_d < 0.8:
                interp = "medium"
            else:
                interp = "large"
            return json.dumps({
                "metric": "Cohen's d (from r)",
                "r": r,
                "d": round(d, 4),
                "interpretation": interp,
                "formula": "d = 2r / sqrt(1 - r^2)",
            })

        elif metric == "eta_squared":
            ss_effect = arguments.get("ss_effect")
            ss_total = arguments.get("ss_total")
            f_val = arguments.get("f_value")
            df_effect = arguments.get("df_effect")
            df_error = arguments.get("df_error")

            if ss_effect is not None and ss_total is not None and ss_total > 0:
                eta2 = ss_effect / ss_total
            elif f_val is not None and df_effect is not None and df_error is not None:
                eta2 = (f_val * df_effect) / (f_val * df_effect + df_error)
            else:
                return json.dumps({"error": "eta_squared needs (ss_effect, ss_total) or (f_value, df_effect, df_error)"})

            if eta2 < 0.01:
                interp = "negligible"
            elif eta2 < 0.06:
                interp = "small"
            elif eta2 < 0.14:
                interp = "medium"
            else:
                interp = "large"

            return json.dumps({
                "metric": "Eta-squared",
                "value": round(eta2, 4),
                "interpretation": interp,
                "reference": "Cohen (1988): small=0.01, medium=0.06, large=0.14",
            })

        else:
            return json.dumps({"error": f"Unknown metric: {metric}. Use: cohens_d, odds_ratio, risk_ratio, r_to_d, eta_squared"})

    except Exception as exc:
        _log.exception("Error in compute_effect_size: %s", exc)
        return json.dumps({"error": f"Computation error: {str(exc)}"})


# ─────────────────────────────────────────────────────────────────────────────
# JOURNAL RANKING LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

# Top business/management/economics journals with tiers
# AAA = FT50 + ABS 4*; AA = ABS 4; A = ABS 3; B = ABS 2
_JOURNAL_RANKINGS: dict[str, str] = {
    # AAA tier — FT50 + ABS 4*
    "academy of management journal": "AAA", "academy of management review": "AAA",
    "administrative science quarterly": "AAA", "american economic review": "AAA",
    "econometrica": "AAA", "entrepreneurship theory and practice": "AAA",
    "harvard business review": "AAA", "human relations": "AAA",
    "human resource management": "AAA", "information systems research": "AAA",
    "journal of accounting and economics": "AAA", "journal of accounting research": "AAA",
    "journal of applied psychology": "AAA", "journal of business ethics": "AAA",
    "journal of business venturing": "AAA", "journal of consumer psychology": "AAA",
    "journal of consumer research": "AAA", "journal of finance": "AAA",
    "journal of financial and quantitative analysis": "AAA",
    "journal of financial economics": "AAA", "journal of international business studies": "AAA",
    "journal of management": "AAA", "journal of management studies": "AAA",
    "journal of marketing": "AAA", "journal of marketing research": "AAA",
    "journal of operations management": "AAA", "journal of political economy": "AAA",
    "journal of the academy of marketing science": "AAA",
    "management science": "AAA", "manufacturing and service operations management": "AAA",
    "marketing science": "AAA", "mis quarterly": "AAA",
    "operations research": "AAA", "organization science": "AAA",
    "organization studies": "AAA", "organizational behavior and human decision processes": "AAA",
    "production and operations management": "AAA",
    "quarterly journal of economics": "AAA", "research policy": "AAA",
    "review of accounting studies": "AAA", "review of economic studies": "AAA",
    "review of financial studies": "AAA", "review of finance": "AAA",
    "sloan management review": "AAA", "strategic entrepreneurship journal": "AAA",
    "strategic management journal": "AAA", "the accounting review": "AAA",
    # AA tier — ABS 4
    "british journal of management": "AA", "business strategy and the environment": "AA",
    "california management review": "AA", "contemporary accounting research": "AA",
    "decision sciences": "AA", "european journal of operational research": "AA",
    "global strategy journal": "AA", "international business review": "AA",
    "international journal of management reviews": "AA",
    "journal of business research": "AA", "journal of corporate finance": "AA",
    "journal of economic theory": "AA", "journal of financial intermediation": "AA",
    "journal of international economics": "AA",
    "journal of international management": "AA", "journal of management inquiry": "AA",
    "journal of monetary economics": "AA", "journal of money credit and banking": "AA",
    "journal of organizational behavior": "AA", "journal of product innovation management": "AA",
    "journal of public economics": "AA", "journal of retailing": "AA",
    "journal of service research": "AA", "journal of strategic information systems": "AA",
    "journal of supply chain management": "AA", "journal of world business": "AA",
    "leadership quarterly": "AA", "long range planning": "AA",
    "omega": "AA", "personnel psychology": "AA",
    "rand journal of economics": "AA", "small business economics": "AA",
    "strategic organization": "AA", "technovation": "AA",
    "world development": "AA",
    # A tier — ABS 3 (selected)
    "asia pacific journal of management": "A", "business ethics quarterly": "A",
    "corporate governance an international review": "A",
    "european management journal": "A", "european management review": "A",
    "family business review": "A", "group and organization management": "A",
    "human resource management journal": "A", "human resource management review": "A",
    "industrial and corporate change": "A", "industrial marketing management": "A",
    "international journal of human resource management": "A",
    "international journal of operations and production management": "A",
    "international journal of research in marketing": "A",
    "international marketing review": "A", "international small business journal": "A",
    "journal of banking and finance": "A", "journal of business logistics": "A",
    "journal of cross cultural psychology": "A", "journal of economic behavior and organization": "A",
    "journal of economics and management strategy": "A",
    "journal of entrepreneurship theory and practice": "A",
    "journal of industrial economics": "A", "journal of information technology": "A",
    "journal of international financial markets institutions and money": "A",
    "journal of knowledge management": "A", "journal of management information systems": "A",
    "journal of occupational and organizational psychology": "A",
    "journal of purchasing and supply management": "A",
    "journal of small business management": "A", "journal of technology transfer": "A",
    "management accounting research": "A", "management international review": "A",
    "management learning": "A", "new technology work and employment": "A",
    "public administration review": "A", "r and d management": "A",
    "supply chain management an international journal": "A",
    "technological forecasting and social change": "A",
    "work employment and society": "A",
}


def _normalize_journal(name: str) -> str:
    """Normalize journal name for fuzzy matching."""
    name = name.lower().strip()
    # Remove common prefixes/suffixes
    for prefix in ("the ", "a "):
        if name.startswith(prefix):
            name = name[len(prefix):]
    # Remove punctuation and extra spaces
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def check_journal_ranking(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Look up journal quality tier from built-in ranking database."""
    journal_name = arguments.get("journal_name", "").strip()
    doi = arguments.get("doi", "").strip()

    if not journal_name and not doi:
        return json.dumps({"error": "Provide journal_name or doi"})

    # If DOI provided, try to extract journal from DB
    if doi and not journal_name:
        db = ctx.get("db")
        session_id = ctx.get("session_id")
        if db and session_id:
            try:
                row = db._conn.execute(
                    "SELECT title FROM papers WHERE session_id = ? AND doi = ? LIMIT 1",
                    (session_id, doi),
                ).fetchone()
                if row:
                    journal_name = row[0]  # Fallback to title if no journal field
            except Exception:
                pass
        if not journal_name:
            return json.dumps({"error": f"Could not find journal for DOI: {doi}"})

    normalized = _normalize_journal(journal_name)

    # Exact match
    if normalized in _JOURNAL_RANKINGS:
        return json.dumps({
            "journal": journal_name,
            "tier": _JOURNAL_RANKINGS[normalized],
            "match": "exact",
        })

    # Fuzzy match — find best substring match
    best_match = None
    best_score = 0
    for known_name, tier in _JOURNAL_RANKINGS.items():
        # Check if the known name is contained in the query or vice versa
        if known_name in normalized or normalized in known_name:
            score = len(known_name)
            if score > best_score:
                best_score = score
                best_match = (known_name, tier)

    if best_match:
        return json.dumps({
            "journal": journal_name,
            "matched_to": best_match[0],
            "tier": best_match[1],
            "match": "fuzzy",
        })

    # Token overlap match
    query_tokens = set(normalized.split())
    best_overlap = 0
    best_match = None
    for known_name, tier in _JOURNAL_RANKINGS.items():
        known_tokens = set(known_name.split())
        overlap = len(query_tokens & known_tokens)
        total = len(query_tokens | known_tokens)
        if total > 0 and overlap / total > 0.5 and overlap > best_overlap:
            best_overlap = overlap
            best_match = (known_name, tier)

    if best_match:
        return json.dumps({
            "journal": journal_name,
            "matched_to": best_match[0],
            "tier": best_match[1],
            "match": "token_overlap",
        })

    return json.dumps({
        "journal": journal_name,
        "tier": "unranked",
        "match": "none",
        "note": "Journal not found in ABS/ABDC/FT50 ranking database. May be unranked or use a different name.",
    })
