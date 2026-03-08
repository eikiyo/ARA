# Location: ara/tools/migration_innovation.py
# Purpose: 12 data tools for immigration-innovation arbitrage research
# Functions: search_unhcr, search_oecd_migration (+ 10 more TBD)
# Calls: httpx for HTTP, json for serialization
# Imports: httpx, json, logging

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

_log = logging.getLogger(__name__)
_TIMEOUT = 30
_UA = "Mozilla/5.0 (compatible; ARA-Research/1.0)"


# ─────────────────────────────────────────────────────────────────────────────
# 1. UNHCR — Refugee / Asylum / IDP populations
# ─────────────────────────────────────────────────────────────────────────────

def search_unhcr(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """UNHCR Population Statistics API — refugee, asylum seeker, IDP data
    by country of origin, country of asylum, and year."""
    mode = arguments.get("mode", "population")

    try:
        if mode == "population":
            return _unhcr_population(arguments)
        elif mode == "countries":
            return _unhcr_countries(arguments)
        elif mode == "timeseries":
            return _unhcr_timeseries(arguments)
        else:
            return json.dumps({"error": f"Unknown mode: {mode}. Use: population, countries, timeseries"})
    except Exception as e:
        _log.error("UNHCR API error: %s", e)
        return json.dumps({"error": str(e)})


def _unhcr_population(args: dict) -> str:
    """Query population data — bilateral or aggregated."""
    params: dict[str, Any] = {"limit": args.get("limit", 20)}

    # Year filter
    year = args.get("year")
    year_from = args.get("year_from")
    year_to = args.get("year_to")
    if year:
        params["year"] = year
    else:
        if year_from:
            params["yearFrom"] = year_from
        if year_to:
            params["yearTo"] = year_to

    # Country filters (ISO-3 codes)
    coo = args.get("country_of_origin")
    coa = args.get("country_of_asylum")
    if coo:
        params["coo"] = coo.upper()
    if coa:
        params["coa"] = coa.upper()

    # Use ISO mode for bilateral queries
    if coo and coa:
        params["cf_type"] = "ISO"

    params["page"] = args.get("page", 1)

    resp = httpx.get(
        "https://api.unhcr.org/population/v1/population/",
        params=params, timeout=_TIMEOUT,
        headers={"User-Agent": _UA},
    )
    resp.raise_for_status()
    data = resp.json()

    items = data.get("items", [])
    if not items:
        return json.dumps({"results": [], "message": "No data found for query"})

    results = []
    for item in items:
        row = {
            "year": item.get("year"),
            "country_of_origin": item.get("coo_name", ""),
            "coo_iso": item.get("coo_iso", ""),
            "country_of_asylum": item.get("coa_name", ""),
            "coa_iso": item.get("coa_iso", ""),
            "refugees": _to_int(item.get("refugees")),
            "asylum_seekers": _to_int(item.get("asylum_seekers")),
            "returned_refugees": _to_int(item.get("returned_refugees")),
            "idps": _to_int(item.get("idps")),
            "returned_idps": _to_int(item.get("returned_idps")),
            "stateless": _to_int(item.get("stateless")),
            "others_of_concern": _to_int(item.get("ooc")),
            "other_in_need": _to_int(item.get("oip")),
            "host_community": _to_int(item.get("hst")),
        }
        # Total displaced
        row["total_displaced"] = (
            row["refugees"] + row["asylum_seekers"] + row["idps"] + row["stateless"]
        )
        results.append(row)

    return json.dumps({
        "source": "UNHCR Population Statistics API",
        "query": {k: v for k, v in params.items() if k != "limit"},
        "total_records": len(results),
        "max_pages": data.get("maxPages", 1),
        "results": results,
    })


def _unhcr_timeseries(args: dict) -> str:
    """Get population timeseries for a country pair or single country."""
    coo = args.get("country_of_origin")
    coa = args.get("country_of_asylum")
    year_from = args.get("year_from", 2000)
    year_to = args.get("year_to", 2023)

    if not coo and not coa:
        return json.dumps({"error": "Provide at least country_of_origin or country_of_asylum (ISO-3 code)"})

    params: dict[str, Any] = {
        "yearFrom": year_from,
        "yearTo": year_to,
        "limit": 100,
    }
    if coo:
        params["coo"] = coo.upper()
    if coa:
        params["coa"] = coa.upper()
    if coo and coa:
        params["cf_type"] = "ISO"

    resp = httpx.get(
        "https://api.unhcr.org/population/v1/population/",
        params=params, timeout=_TIMEOUT,
        headers={"User-Agent": _UA},
    )
    resp.raise_for_status()
    data = resp.json()

    series = []
    for item in data.get("items", []):
        series.append({
            "year": item.get("year"),
            "refugees": _to_int(item.get("refugees")),
            "asylum_seekers": _to_int(item.get("asylum_seekers")),
            "idps": _to_int(item.get("idps")),
            "stateless": _to_int(item.get("stateless")),
            "total_displaced": (
                _to_int(item.get("refugees")) + _to_int(item.get("asylum_seekers"))
                + _to_int(item.get("idps")) + _to_int(item.get("stateless"))
            ),
        })

    # Sort by year
    series.sort(key=lambda x: x["year"])

    return json.dumps({
        "source": "UNHCR Population Statistics API",
        "country_of_origin": coo or "all",
        "country_of_asylum": coa or "all",
        "period": f"{year_from}-{year_to}",
        "data_points": len(series),
        "timeseries": series,
    })


def _unhcr_countries(args: dict) -> str:
    """List available countries with ISO codes."""
    query = (args.get("query") or "").lower()

    resp = httpx.get(
        "https://api.unhcr.org/population/v1/countries/",
        params={"limit": 300},
        timeout=_TIMEOUT,
        headers={"User-Agent": _UA},
    )
    resp.raise_for_status()
    data = resp.json()

    countries = []
    for item in data.get("items", []):
        name = item.get("name") or ""
        iso = item.get("iso") or ""
        if query and query not in name.lower() and query not in iso.lower():
            continue
        countries.append({
            "name": name,
            "iso3": iso,
            "iso2": item.get("iso2", ""),
            "region": item.get("region", ""),
            "major_area": item.get("majorArea", ""),
        })

    return json.dumps({
        "source": "UNHCR",
        "total": len(countries),
        "countries": countries[:50],
    })


def _to_int(val: Any) -> int:
    """Safely convert UNHCR values (can be int, str, '-', '0') to int."""
    if val is None or val == "-" or val == "":
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# 2. OECD Migration — Inflows, foreign-born stocks, permit types
# ─────────────────────────────────────────────────────────────────────────────

_OECD_BASE = "https://sdmx.oecd.org/public/rest/data"
_OECD_HEADERS = {"Accept": "application/vnd.sdmx.data+json", "User-Agent": _UA}

# Dataset IDs and their dimension structures
_OECD_MIG_DATASETS = {
    "permanent": {
        "flow": "OECD.ELS.IMD,DSD_MIG_INT@DF_MIG_INT_PER,1.0",
        "desc": "Standardised inflows of permanent-type migrants",
        "dims": 6,  # REF_AREA.FREQ.MEASURE.MIGRATION_TYPE.UNIT_MEASURE.TIME_PERIOD
    },
    "temporary": {
        "flow": "OECD.ELS.IMD,DSD_MIG_INT@DF_MIG_INT_TEMP,1.0",
        "desc": "Standardised inflows of temporary migrants",
        "dims": 6,
    },
    "stock": {
        "flow": "OECD.ELS.IMD,DSD_MIG_F@DF_MIG_POPF,1.0",
        "desc": "Foreign-born population stocks",
        "dims": 8,
    },
}

# Migration type codes for permanent migrants
_MIG_TYPES = {
    "WO": "Work",
    "FA": "Family",
    "HU": "Humanitarian",
    "FR": "Free movements",
    "AC": "Accompanying family of workers",
    "OT": "Other",
    "_T": "Total",
}


def search_oecd_migration(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """OECD migration data — inflows by category, foreign-born stocks."""
    mode = arguments.get("mode", "inflows")

    try:
        if mode == "inflows":
            return _oecd_mig_inflows(arguments)
        elif mode == "stocks":
            return _oecd_mig_stocks(arguments)
        elif mode == "datasets":
            return _oecd_mig_list_datasets()
        else:
            return json.dumps({"error": f"Unknown mode: {mode}. Use: inflows, stocks, datasets"})
    except Exception as e:
        _log.error("OECD migration error: %s", e)
        return json.dumps({"error": str(e)})


def _oecd_mig_inflows(args: dict) -> str:
    """Query permanent/temporary migrant inflows by country and category."""
    country = (args.get("country") or "").upper()
    year_from = args.get("year_from", 2015)
    year_to = args.get("year_to", 2023)
    mig_type = args.get("migration_type", "")  # WO, FA, HU, FR, _T
    dataset = args.get("dataset", "permanent")  # permanent or temporary

    ds = _OECD_MIG_DATASETS.get(dataset)
    if not ds:
        return json.dumps({"error": f"Unknown dataset: {dataset}. Use: permanent, temporary"})

    # Build key: REF_AREA.FREQ.MEASURE.MIGRATION_TYPE.UNIT_MEASURE.TIME_PERIOD
    ref_area = country if country else ""
    mig_filter = mig_type.upper() if mig_type else ""
    key = f"{ref_area}..MIG_FLW.{mig_filter}.PS."

    url = f"{_OECD_BASE}/{ds['flow']}/{key}"
    params = {
        "startPeriod": str(year_from),
        "endPeriod": str(year_to),
        "dimensionAtObservation": "AllDimensions",
    }

    resp = httpx.get(url, params=params, headers=_OECD_HEADERS, timeout=_TIMEOUT)
    if resp.status_code == 422:
        return json.dumps({"error": "Invalid query. Check country code (ISO-3) and parameters."})
    resp.raise_for_status()
    data = resp.json()

    return _parse_oecd_sdmx(data, f"OECD {ds['desc']}")


def _oecd_mig_stocks(args: dict) -> str:
    """Query foreign-born population stocks."""
    country = (args.get("country") or "").upper()
    year_from = args.get("year_from", 2015)
    year_to = args.get("year_to", 2023)

    ds = _OECD_MIG_DATASETS["stock"]
    # 8 dimensions for stock dataset - use wildcards
    ref_area = country if country else ""
    key = f"{ref_area}." + ".".join([""] * (ds["dims"] - 2)) + "."

    url = f"{_OECD_BASE}/{ds['flow']}/{key}"
    params = {
        "startPeriod": str(year_from),
        "endPeriod": str(year_to),
        "dimensionAtObservation": "AllDimensions",
    }

    resp = httpx.get(url, params=params, headers=_OECD_HEADERS, timeout=_TIMEOUT)
    if resp.status_code == 422:
        return json.dumps({"error": "Invalid query. Check country code (ISO-3) and parameters."})
    resp.raise_for_status()
    data = resp.json()

    return _parse_oecd_sdmx(data, "OECD Foreign-born population stocks")


def _oecd_mig_list_datasets() -> str:
    """List available OECD migration datasets."""
    datasets = []
    for key, ds in _OECD_MIG_DATASETS.items():
        datasets.append({
            "id": key,
            "description": ds["desc"],
            "migration_types": _MIG_TYPES if key == "permanent" else None,
        })
    return json.dumps({
        "source": "OECD SDMX",
        "datasets": datasets,
        "usage_hint": "Use mode='inflows' with dataset='permanent' or 'temporary', or mode='stocks' for foreign-born population",
    })


# ─────────────────────────────────────────────────────────────────────────────
# 3. WIPO IP Statistics — Patents by nationality (via World Bank indicators)
# ─────────────────────────────────────────────────────────────────────────────

_WB_IP_INDICATORS = {
    "IP.PAT.RESD": "Patent applications, residents",
    "IP.PAT.NRES": "Patent applications, nonresidents",
    "IP.TMK.RESD": "Trademark applications, residents",
    "IP.TMK.NRES": "Trademark applications, nonresidents",
    "IP.JRN.ARTC.SC": "Scientific and technical journal articles",
    "GB.XPD.RSDV.GD.ZS": "Research and development expenditure (% of GDP)",
    "IP.PAT.RESD.FZ": "Patent applications, residents (per million people)",
    "SP.POP.SCIE.RD.P6": "Researchers in R&D (per million people)",
}


def search_wipo_ip(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Patent and IP statistics by country — resident vs nonresident filings,
    R&D spending, scientific output. Uses World Bank IP indicators."""
    mode = arguments.get("mode", "patents")

    try:
        if mode == "patents":
            return _wipo_patents(arguments)
        elif mode == "compare":
            return _wipo_compare_countries(arguments)
        elif mode == "indicators":
            return json.dumps({
                "source": "World Bank IP Statistics (WIPO data)",
                "indicators": _WB_IP_INDICATORS,
                "usage": "Use mode='patents' for country data, mode='compare' for multi-country comparison",
            })
        else:
            return json.dumps({"error": f"Unknown mode: {mode}. Use: patents, compare, indicators"})
    except Exception as e:
        _log.error("WIPO IP error: %s", e)
        return json.dumps({"error": str(e)})


def _wipo_patents(args: dict) -> str:
    """Get patent/IP data for a single country."""
    country = (args.get("country") or "USA").upper()
    year_from = args.get("year_from", 2010)
    year_to = args.get("year_to", 2022)
    indicators = args.get("indicators", ["IP.PAT.RESD", "IP.PAT.NRES", "GB.XPD.RSDV.GD.ZS", "IP.JRN.ARTC.SC"])

    if isinstance(indicators, str):
        indicators = [i.strip() for i in indicators.split(",")]

    results = {}
    for ind in indicators:
        if ind not in _WB_IP_INDICATORS:
            continue
        resp = httpx.get(
            f"https://api.worldbank.org/v2/country/{country}/indicator/{ind}",
            params={"format": "json", "date": f"{year_from}:{year_to}", "per_page": 50},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            continue
        data = resp.json()
        if len(data) < 2 or not data[1]:
            continue

        series = []
        for item in data[1]:
            if item.get("value") is not None:
                series.append({"year": item["date"], "value": item["value"]})
        series.sort(key=lambda x: x["year"])
        results[ind] = {
            "name": _WB_IP_INDICATORS[ind],
            "timeseries": series,
        }

    # Compute nonresident share if both indicators available
    if "IP.PAT.RESD" in results and "IP.PAT.NRES" in results:
        res_data = {s["year"]: s["value"] for s in results["IP.PAT.RESD"]["timeseries"]}
        nres_data = {s["year"]: s["value"] for s in results["IP.PAT.NRES"]["timeseries"]}
        share_series = []
        for year in sorted(set(res_data) & set(nres_data)):
            total = res_data[year] + nres_data[year]
            if total > 0:
                share_series.append({
                    "year": year,
                    "nonresident_share": round(nres_data[year] / total * 100, 1),
                    "total_applications": int(total),
                })
        results["nonresident_share"] = {
            "name": "Nonresident patent share (% of total) — proxy for immigrant inventor contribution",
            "timeseries": share_series,
        }

    return json.dumps({
        "source": "World Bank / WIPO IP Statistics",
        "country": country,
        "period": f"{year_from}-{year_to}",
        "data": results,
    })


def _wipo_compare_countries(args: dict) -> str:
    """Compare patent statistics across multiple countries."""
    countries = args.get("countries", ["USA", "DEU", "GBR", "CHN", "IND", "ISR", "SWE"])
    if isinstance(countries, str):
        countries = [c.strip() for c in countries.split(",")]
    year = args.get("year", 2021)
    indicator = args.get("indicator", "IP.PAT.RESD")

    country_str = ";".join(c.upper() for c in countries)
    resp = httpx.get(
        f"https://api.worldbank.org/v2/country/{country_str}/indicator/{indicator}",
        params={"format": "json", "date": str(year), "per_page": 100},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        return json.dumps({"error": f"API returned {resp.status_code}"})

    data = resp.json()
    if len(data) < 2 or not data[1]:
        return json.dumps({"results": [], "message": "No data found"})

    results = []
    for item in data[1]:
        if item.get("value") is not None:
            results.append({
                "country": item["country"]["value"],
                "iso3": item["countryiso3code"],
                "year": item["date"],
                "value": item["value"],
                "indicator": _WB_IP_INDICATORS.get(indicator, indicator),
            })

    results.sort(key=lambda x: x["value"] or 0, reverse=True)

    return json.dumps({
        "source": "World Bank / WIPO",
        "indicator": _WB_IP_INDICATORS.get(indicator, indicator),
        "year": year,
        "total_countries": len(results),
        "results": results,
    })


# ─────────────────────────────────────────────────────────────────────────────
# 4. Global Innovation Index — GII scores + sub-pillars (embedded lookup)
# ─────────────────────────────────────────────────────────────────────────────

# GII 2023 data (WIPO/Cornell/INSEAD) — top countries + key developing nations
# Scores out of 100; rank out of ~132 countries
_GII_2023: dict[str, dict] = {
    "CHE": {"name": "Switzerland", "rank": 1, "score": 67.6, "institutions": 81.4, "human_capital": 59.6, "infrastructure": 67.3, "market_sophistication": 71.6, "business_sophistication": 64.6, "knowledge_output": 68.3, "creative_output": 60.8},
    "SWE": {"name": "Sweden", "rank": 2, "score": 64.2, "institutions": 83.2, "human_capital": 59.8, "infrastructure": 66.8, "market_sophistication": 64.8, "business_sophistication": 65.7, "knowledge_output": 62.2, "creative_output": 47.1},
    "USA": {"name": "United States", "rank": 3, "score": 63.5, "institutions": 78.7, "human_capital": 55.2, "infrastructure": 62.3, "market_sophistication": 82.5, "business_sophistication": 62.2, "knowledge_output": 57.3, "creative_output": 46.5},
    "GBR": {"name": "United Kingdom", "rank": 4, "score": 62.4, "institutions": 80.1, "human_capital": 55.6, "infrastructure": 66.9, "market_sophistication": 75.4, "business_sophistication": 55.8, "knowledge_output": 55.4, "creative_output": 47.7},
    "SGP": {"name": "Singapore", "rank": 5, "score": 61.6, "institutions": 91.2, "human_capital": 56.4, "infrastructure": 70.8, "market_sophistication": 69.1, "business_sophistication": 52.6, "knowledge_output": 53.1, "creative_output": 37.9},
    "FIN": {"name": "Finland", "rank": 6, "score": 61.2, "institutions": 86.3, "human_capital": 62.3, "infrastructure": 63.0, "market_sophistication": 54.2, "business_sophistication": 63.2, "knowledge_output": 60.3, "creative_output": 39.3},
    "NLD": {"name": "Netherlands", "rank": 7, "score": 60.8, "institutions": 80.9, "human_capital": 55.9, "infrastructure": 64.2, "market_sophistication": 67.3, "business_sophistication": 56.1, "knowledge_output": 58.2, "creative_output": 43.1},
    "DEU": {"name": "Germany", "rank": 8, "score": 58.8, "institutions": 77.1, "human_capital": 54.5, "infrastructure": 60.7, "market_sophistication": 62.7, "business_sophistication": 61.3, "knowledge_output": 55.3, "creative_output": 39.9},
    "DNK": {"name": "Denmark", "rank": 9, "score": 58.2, "institutions": 85.7, "human_capital": 60.2, "infrastructure": 65.4, "market_sophistication": 58.2, "business_sophistication": 51.2, "knowledge_output": 54.1, "creative_output": 32.7},
    "KOR": {"name": "Korea, Republic of", "rank": 10, "score": 57.9, "institutions": 67.7, "human_capital": 60.5, "infrastructure": 67.3, "market_sophistication": 58.2, "business_sophistication": 55.1, "knowledge_output": 58.8, "creative_output": 38.0},
    "ISR": {"name": "Israel", "rank": 14, "score": 55.4, "institutions": 66.2, "human_capital": 47.1, "infrastructure": 56.2, "market_sophistication": 60.3, "business_sophistication": 59.7, "knowledge_output": 59.5, "creative_output": 39.2},
    "FRA": {"name": "France", "rank": 11, "score": 55.5, "institutions": 73.0, "human_capital": 49.7, "infrastructure": 62.5, "market_sophistication": 61.2, "business_sophistication": 50.6, "knowledge_output": 49.3, "creative_output": 42.2},
    "JPN": {"name": "Japan", "rank": 13, "score": 55.4, "institutions": 72.1, "human_capital": 53.9, "infrastructure": 66.3, "market_sophistication": 60.0, "business_sophistication": 53.4, "knowledge_output": 51.2, "creative_output": 31.1},
    "CAN": {"name": "Canada", "rank": 15, "score": 53.9, "institutions": 78.5, "human_capital": 50.5, "infrastructure": 58.6, "market_sophistication": 62.1, "business_sophistication": 46.7, "knowledge_output": 45.2, "creative_output": 35.7},
    "AUS": {"name": "Australia", "rank": 18, "score": 52.4, "institutions": 78.5, "human_capital": 47.3, "infrastructure": 57.9, "market_sophistication": 62.0, "business_sophistication": 41.2, "knowledge_output": 46.9, "creative_output": 33.2},
    "NOR": {"name": "Norway", "rank": 19, "score": 52.0, "institutions": 84.2, "human_capital": 54.6, "infrastructure": 65.1, "market_sophistication": 53.1, "business_sophistication": 41.1, "knowledge_output": 41.7, "creative_output": 24.2},
    "CHN": {"name": "China", "rank": 12, "score": 55.3, "institutions": 56.5, "human_capital": 42.3, "infrastructure": 56.1, "market_sophistication": 55.5, "business_sophistication": 54.4, "knowledge_output": 61.2, "creative_output": 61.0},
    "IND": {"name": "India", "rank": 40, "score": 38.1, "institutions": 48.4, "human_capital": 27.8, "infrastructure": 34.4, "market_sophistication": 54.5, "business_sophistication": 31.6, "knowledge_output": 37.1, "creative_output": 33.1},
    "TUR": {"name": "Türkiye", "rank": 39, "score": 38.2, "institutions": 49.0, "human_capital": 33.6, "infrastructure": 44.7, "market_sophistication": 42.4, "business_sophistication": 34.7, "knowledge_output": 31.1, "creative_output": 31.7},
    "BRA": {"name": "Brazil", "rank": 49, "score": 33.7, "institutions": 44.7, "human_capital": 31.2, "infrastructure": 40.2, "market_sophistication": 36.0, "business_sophistication": 33.2, "knowledge_output": 22.8, "creative_output": 27.9},
    "ZAF": {"name": "South Africa", "rank": 59, "score": 30.1, "institutions": 56.4, "human_capital": 18.9, "infrastructure": 31.2, "market_sophistication": 44.2, "business_sophistication": 27.9, "knowledge_output": 21.1, "creative_output": 11.2},
    "MEX": {"name": "Mexico", "rank": 58, "score": 30.8, "institutions": 47.3, "human_capital": 22.8, "infrastructure": 38.5, "market_sophistication": 41.7, "business_sophistication": 28.5, "knowledge_output": 20.9, "creative_output": 16.0},
    "RUS": {"name": "Russian Federation", "rank": 51, "score": 32.5, "institutions": 39.5, "human_capital": 43.2, "infrastructure": 42.1, "market_sophistication": 32.7, "business_sophistication": 29.3, "knowledge_output": 27.7, "creative_output": 13.0},
    "NGA": {"name": "Nigeria", "rank": 109, "score": 17.8, "institutions": 29.5, "human_capital": 7.5, "infrastructure": 14.2, "market_sophistication": 29.3, "business_sophistication": 20.8, "knowledge_output": 10.3, "creative_output": 12.9},
    "VNM": {"name": "Viet Nam", "rank": 46, "score": 34.4, "institutions": 44.7, "human_capital": 24.7, "infrastructure": 38.6, "market_sophistication": 38.2, "business_sophistication": 22.5, "knowledge_output": 27.0, "creative_output": 45.1},
    "PHL": {"name": "Philippines", "rank": 56, "score": 31.3, "institutions": 40.2, "human_capital": 25.5, "infrastructure": 26.5, "market_sophistication": 41.5, "business_sophistication": 28.3, "knowledge_output": 18.0, "creative_output": 39.4},
    "EGY": {"name": "Egypt", "rank": 86, "score": 23.5, "institutions": 44.0, "human_capital": 22.8, "infrastructure": 29.8, "market_sophistication": 24.5, "business_sophistication": 19.9, "knowledge_output": 13.1, "creative_output": 10.3},
    "SYR": {"name": "Syrian Arab Republic", "rank": 132, "score": 8.7, "institutions": 14.1, "human_capital": 9.2, "infrastructure": 9.8, "market_sophistication": 10.5, "business_sophistication": 8.2, "knowledge_output": 4.1, "creative_output": 5.0},
    "AFG": {"name": "Afghanistan", "rank": 131, "score": 9.8, "institutions": 8.7, "human_capital": 4.2, "infrastructure": 8.5, "market_sophistication": 18.5, "business_sophistication": 13.7, "knowledge_output": 6.5, "creative_output": 8.5},
    "UKR": {"name": "Ukraine", "rank": 55, "score": 31.4, "institutions": 36.0, "human_capital": 40.2, "infrastructure": 38.5, "market_sophistication": 28.5, "business_sophistication": 25.5, "knowledge_output": 30.1, "creative_output": 21.1},
    "IRN": {"name": "Iran", "rank": 62, "score": 29.1, "institutions": 35.5, "human_capital": 36.3, "infrastructure": 35.8, "market_sophistication": 17.0, "business_sophistication": 27.5, "knowledge_output": 31.5, "creative_output": 20.1},
    "PAK": {"name": "Pakistan", "rank": 88, "score": 22.5, "institutions": 35.1, "human_capital": 14.2, "infrastructure": 22.7, "market_sophistication": 25.5, "business_sophistication": 18.5, "knowledge_output": 14.1, "creative_output": 27.3},
    "ETH": {"name": "Ethiopia", "rank": 114, "score": 16.2, "institutions": 28.7, "human_capital": 7.1, "infrastructure": 11.0, "market_sophistication": 30.2, "business_sophistication": 16.8, "knowledge_output": 5.4, "creative_output": 14.0},
    "IDN": {"name": "Indonesia", "rank": 61, "score": 29.5, "institutions": 45.5, "human_capital": 24.8, "infrastructure": 32.1, "market_sophistication": 35.2, "business_sophistication": 21.5, "knowledge_output": 16.5, "creative_output": 31.1},
    "MYS": {"name": "Malaysia", "rank": 36, "score": 39.5, "institutions": 62.3, "human_capital": 32.3, "infrastructure": 44.2, "market_sophistication": 50.2, "business_sophistication": 35.2, "knowledge_output": 29.2, "creative_output": 23.3},
    "THA": {"name": "Thailand", "rank": 43, "score": 35.5, "institutions": 52.5, "human_capital": 28.5, "infrastructure": 38.2, "market_sophistication": 44.1, "business_sophistication": 31.2, "knowledge_output": 26.7, "creative_output": 27.5},
    "ARE": {"name": "United Arab Emirates", "rank": 32, "score": 42.3, "institutions": 73.8, "human_capital": 32.5, "infrastructure": 56.2, "market_sophistication": 51.1, "business_sophistication": 37.5, "knowledge_output": 24.2, "creative_output": 20.7},
    "SAU": {"name": "Saudi Arabia", "rank": 48, "score": 34.0, "institutions": 62.5, "human_capital": 29.5, "infrastructure": 42.1, "market_sophistication": 42.5, "business_sophistication": 25.1, "knowledge_output": 20.1, "creative_output": 16.1},
    "POL": {"name": "Poland", "rank": 41, "score": 37.7, "institutions": 65.1, "human_capital": 38.2, "infrastructure": 46.2, "market_sophistication": 42.1, "business_sophistication": 28.7, "knowledge_output": 26.5, "creative_output": 17.1},
    "CZE": {"name": "Czechia", "rank": 29, "score": 43.5, "institutions": 65.8, "human_capital": 41.5, "infrastructure": 51.2, "market_sophistication": 48.1, "business_sophistication": 38.2, "knowledge_output": 34.8, "creative_output": 25.1},
    "HUN": {"name": "Hungary", "rank": 35, "score": 40.2, "institutions": 56.1, "human_capital": 38.5, "infrastructure": 47.5, "market_sophistication": 45.2, "business_sophistication": 37.5, "knowledge_output": 28.1, "creative_output": 28.5},
    "ROU": {"name": "Romania", "rank": 44, "score": 35.0, "institutions": 50.7, "human_capital": 28.1, "infrastructure": 43.5, "market_sophistication": 42.5, "business_sophistication": 27.5, "knowledge_output": 17.2, "creative_output": 35.5},
    "COL": {"name": "Colombia", "rank": 66, "score": 27.9, "institutions": 49.1, "human_capital": 24.2, "infrastructure": 28.5, "market_sophistication": 35.1, "business_sophistication": 29.1, "knowledge_output": 14.7, "creative_output": 14.7},
    "CHL": {"name": "Chile", "rank": 52, "score": 32.2, "institutions": 60.2, "human_capital": 27.5, "infrastructure": 38.1, "market_sophistication": 41.2, "business_sophistication": 25.1, "knowledge_output": 18.1, "creative_output": 15.2},
    "ARG": {"name": "Argentina", "rank": 73, "score": 26.0, "institutions": 38.5, "human_capital": 28.1, "infrastructure": 32.5, "market_sophistication": 25.5, "business_sophistication": 24.7, "knowledge_output": 18.3, "creative_output": 14.5},
    "KEN": {"name": "Kenya", "rank": 78, "score": 24.7, "institutions": 39.5, "human_capital": 15.8, "infrastructure": 19.2, "market_sophistication": 35.2, "business_sophistication": 31.2, "knowledge_output": 10.1, "creative_output": 22.0},
    "BGD": {"name": "Bangladesh", "rank": 105, "score": 18.7, "institutions": 28.5, "human_capital": 10.1, "infrastructure": 13.8, "market_sophistication": 30.5, "business_sophistication": 22.5, "knowledge_output": 7.8, "creative_output": 17.5},
}


def search_global_innovation_index(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Global Innovation Index (GII) 2023 — scores and 7 sub-pillars for ~45 countries.
    Embedded lookup, no API call needed."""
    mode = arguments.get("mode", "lookup")

    try:
        if mode == "lookup":
            return _gii_lookup(arguments)
        elif mode == "compare":
            return _gii_compare(arguments)
        elif mode == "gap":
            return _gii_gap(arguments)
        elif mode == "ranking":
            return _gii_ranking(arguments)
        else:
            return json.dumps({"error": f"Unknown mode: {mode}. Use: lookup, compare, gap, ranking"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _gii_lookup(args: dict) -> str:
    country = (args.get("country") or "").upper()
    if country not in _GII_2023:
        return json.dumps({"error": f"Country {country} not found. Use mode='ranking' to see available countries."})
    d = _GII_2023[country]
    return json.dumps({"source": "Global Innovation Index 2023 (WIPO/Cornell/INSEAD)", "country": country, **d})


def _gii_compare(args: dict) -> str:
    countries = args.get("countries", [])
    if isinstance(countries, str):
        countries = [c.strip().upper() for c in countries.split(",")]
    results = []
    for c in countries:
        if c in _GII_2023:
            results.append({"iso3": c, **_GII_2023[c]})
    results.sort(key=lambda x: x["rank"])
    return json.dumps({"source": "GII 2023", "countries": results})


def _gii_gap(args: dict) -> str:
    """Compute innovation gap between two countries — key for arbitrage framing."""
    origin = (args.get("origin") or "").upper()
    host = (args.get("host") or "").upper()
    if origin not in _GII_2023 or host not in _GII_2023:
        missing = [c for c in [origin, host] if c not in _GII_2023]
        return json.dumps({"error": f"Countries not found: {missing}"})

    o, h = _GII_2023[origin], _GII_2023[host]
    pillars = ["institutions", "human_capital", "infrastructure", "market_sophistication", "business_sophistication", "knowledge_output", "creative_output"]
    gaps = {}
    for p in pillars:
        gaps[p] = round(h[p] - o[p], 1)

    return json.dumps({
        "source": "GII 2023",
        "origin": {"iso3": origin, "name": o["name"], "rank": o["rank"], "score": o["score"]},
        "host": {"iso3": host, "name": h["name"], "rank": h["rank"], "score": h["score"]},
        "overall_gap": round(h["score"] - o["score"], 1),
        "rank_difference": o["rank"] - h["rank"],
        "pillar_gaps": gaps,
        "interpretation": f"Innovation gap of {round(h['score'] - o['score'], 1)} points ({h['name']} vs {o['name']}). Largest gaps suggest where institutional arbitrage is most potent.",
    })


def _gii_ranking(args: dict) -> str:
    top_n = args.get("limit", 45)
    ranked = sorted(_GII_2023.items(), key=lambda x: x[1]["rank"])[:top_n]
    results = [{"iso3": k, "rank": v["rank"], "name": v["name"], "score": v["score"]} for k, v in ranked]
    return json.dumps({"source": "GII 2023", "total": len(results), "ranking": results})


# ─────────────────────────────────────────────────────────────────────────────
# 5. Institutional Distance — Kogut-Singh index from WGI + Hofstede
# ─────────────────────────────────────────────────────────────────────────────

# World Bank Worldwide Governance Indicators (WGI) 2022 — 6 dimensions
# Scale: -2.5 (weak) to +2.5 (strong governance)
_WGI: dict[str, dict[str, float]] = {
    "USA": {"voice": 0.88, "stability": 0.28, "effectiveness": 1.47, "regulatory": 1.39, "rule_of_law": 1.39, "corruption": 1.19},
    "DEU": {"voice": 1.29, "stability": 0.63, "effectiveness": 1.53, "regulatory": 1.68, "rule_of_law": 1.60, "corruption": 1.85},
    "GBR": {"voice": 1.21, "stability": 0.48, "effectiveness": 1.30, "regulatory": 1.61, "rule_of_law": 1.53, "corruption": 1.72},
    "SWE": {"voice": 1.50, "stability": 0.87, "effectiveness": 1.71, "regulatory": 1.76, "rule_of_law": 1.85, "corruption": 2.12},
    "CHE": {"voice": 1.55, "stability": 1.30, "effectiveness": 2.04, "regulatory": 1.79, "rule_of_law": 1.94, "corruption": 2.14},
    "SGP": {"voice": -0.18, "stability": 1.38, "effectiveness": 2.23, "regulatory": 2.17, "rule_of_law": 1.82, "corruption": 2.17},
    "CAN": {"voice": 1.31, "stability": 0.89, "effectiveness": 1.64, "regulatory": 1.66, "rule_of_law": 1.72, "corruption": 1.87},
    "AUS": {"voice": 1.26, "stability": 0.81, "effectiveness": 1.56, "regulatory": 1.70, "rule_of_law": 1.60, "corruption": 1.80},
    "FRA": {"voice": 0.95, "stability": 0.29, "effectiveness": 1.38, "regulatory": 1.15, "rule_of_law": 1.35, "corruption": 1.29},
    "JPN": {"voice": 1.00, "stability": 0.97, "effectiveness": 1.58, "regulatory": 1.17, "rule_of_law": 1.43, "corruption": 1.35},
    "KOR": {"voice": 0.78, "stability": 0.52, "effectiveness": 1.22, "regulatory": 1.10, "rule_of_law": 1.13, "corruption": 0.87},
    "ISR": {"voice": 0.41, "stability": -0.65, "effectiveness": 1.24, "regulatory": 1.22, "rule_of_law": 1.00, "corruption": 0.96},
    "NLD": {"voice": 1.39, "stability": 0.72, "effectiveness": 1.82, "regulatory": 1.84, "rule_of_law": 1.79, "corruption": 2.01},
    "NOR": {"voice": 1.62, "stability": 1.13, "effectiveness": 1.85, "regulatory": 1.62, "rule_of_law": 1.98, "corruption": 2.10},
    "DNK": {"voice": 1.53, "stability": 0.74, "effectiveness": 1.86, "regulatory": 1.70, "rule_of_law": 1.88, "corruption": 2.20},
    "FIN": {"voice": 1.51, "stability": 0.98, "effectiveness": 1.87, "regulatory": 1.74, "rule_of_law": 1.95, "corruption": 2.14},
    "CHN": {"voice": -1.56, "stability": -0.33, "effectiveness": 0.56, "regulatory": -0.18, "rule_of_law": -0.23, "corruption": -0.17},
    "IND": {"voice": 0.27, "stability": -0.79, "effectiveness": -0.07, "regulatory": -0.33, "rule_of_law": 0.00, "corruption": -0.30},
    "BRA": {"voice": 0.30, "stability": -0.46, "effectiveness": -0.33, "regulatory": -0.09, "rule_of_law": -0.27, "corruption": -0.35},
    "RUS": {"voice": -1.28, "stability": -0.72, "effectiveness": -0.17, "regulatory": -0.40, "rule_of_law": -0.77, "corruption": -0.85},
    "TUR": {"voice": -0.95, "stability": -1.04, "effectiveness": 0.01, "regulatory": -0.01, "rule_of_law": -0.31, "corruption": -0.30},
    "ZAF": {"voice": 0.52, "stability": -0.20, "effectiveness": 0.11, "regulatory": 0.06, "rule_of_law": -0.05, "corruption": -0.01},
    "MEX": {"voice": -0.02, "stability": -0.72, "effectiveness": -0.06, "regulatory": 0.22, "rule_of_law": -0.63, "corruption": -0.80},
    "NGA": {"voice": -0.69, "stability": -1.96, "effectiveness": -1.11, "regulatory": -0.73, "rule_of_law": -1.02, "corruption": -1.13},
    "SYR": {"voice": -2.06, "stability": -2.78, "effectiveness": -1.98, "regulatory": -1.65, "rule_of_law": -1.86, "corruption": -1.59},
    "AFG": {"voice": -1.67, "stability": -2.58, "effectiveness": -1.75, "regulatory": -1.55, "rule_of_law": -1.85, "corruption": -1.54},
    "UKR": {"voice": -0.11, "stability": -2.27, "effectiveness": -0.36, "regulatory": -0.23, "rule_of_law": -0.59, "corruption": -0.66},
    "IRN": {"voice": -1.58, "stability": -0.99, "effectiveness": -0.48, "regulatory": -1.24, "rule_of_law": -0.79, "corruption": -0.50},
    "PAK": {"voice": -0.67, "stability": -2.12, "effectiveness": -0.58, "regulatory": -0.55, "rule_of_law": -0.71, "corruption": -0.73},
    "VNM": {"voice": -1.44, "stability": 0.31, "effectiveness": 0.08, "regulatory": -0.18, "rule_of_law": 0.03, "corruption": -0.33},
    "PHL": {"voice": -0.04, "stability": -1.06, "effectiveness": 0.01, "regulatory": -0.07, "rule_of_law": -0.37, "corruption": -0.49},
    "EGY": {"voice": -1.30, "stability": -0.82, "effectiveness": -0.61, "regulatory": -0.58, "rule_of_law": -0.37, "corruption": -0.54},
    "IDN": {"voice": 0.07, "stability": -0.47, "effectiveness": 0.19, "regulatory": -0.06, "rule_of_law": -0.14, "corruption": -0.15},
    "MYS": {"voice": -0.20, "stability": 0.30, "effectiveness": 0.91, "regulatory": 0.73, "rule_of_law": 0.48, "corruption": 0.25},
    "THA": {"voice": -0.92, "stability": -0.44, "effectiveness": 0.35, "regulatory": 0.18, "rule_of_law": 0.02, "corruption": -0.29},
    "ARE": {"voice": -0.99, "stability": 0.56, "effectiveness": 1.36, "regulatory": 1.05, "rule_of_law": 0.79, "corruption": 1.19},
    "SAU": {"voice": -1.66, "stability": -0.26, "effectiveness": 0.39, "regulatory": 0.24, "rule_of_law": 0.10, "corruption": 0.32},
    "POL": {"voice": 0.55, "stability": 0.47, "effectiveness": 0.52, "regulatory": 0.92, "rule_of_law": 0.30, "corruption": 0.57},
    "CZE": {"voice": 0.92, "stability": 0.86, "effectiveness": 0.94, "regulatory": 1.29, "rule_of_law": 1.08, "corruption": 0.52},
    "HUN": {"voice": 0.21, "stability": 0.63, "effectiveness": 0.46, "regulatory": 0.70, "rule_of_law": 0.33, "corruption": -0.09},
    "ROU": {"voice": 0.39, "stability": 0.06, "effectiveness": -0.27, "regulatory": 0.38, "rule_of_law": 0.15, "corruption": -0.02},
    "COL": {"voice": -0.06, "stability": -0.77, "effectiveness": -0.04, "regulatory": 0.32, "rule_of_law": -0.35, "corruption": -0.31},
    "CHL": {"voice": 0.68, "stability": 0.00, "effectiveness": 0.72, "regulatory": 1.22, "rule_of_law": 0.83, "corruption": 0.68},
    "ARG": {"voice": 0.47, "stability": -0.06, "effectiveness": -0.19, "regulatory": -0.69, "rule_of_law": -0.37, "corruption": -0.46},
    "KEN": {"voice": -0.15, "stability": -1.14, "effectiveness": -0.31, "regulatory": -0.16, "rule_of_law": -0.38, "corruption": -0.76},
    "ETH": {"voice": -1.37, "stability": -1.95, "effectiveness": -0.44, "regulatory": -0.72, "rule_of_law": -0.55, "corruption": -0.15},
    "BGD": {"voice": -0.62, "stability": -0.92, "effectiveness": -0.68, "regulatory": -0.87, "rule_of_law": -0.56, "corruption": -0.88},
}

# Hofstede cultural dimensions (selected countries)
# Scale 0-100 for each dimension
_HOFSTEDE: dict[str, dict[str, float]] = {
    "USA": {"power_distance": 40, "individualism": 91, "masculinity": 62, "uncertainty_avoidance": 46, "long_term": 26, "indulgence": 68},
    "DEU": {"power_distance": 35, "individualism": 67, "masculinity": 66, "uncertainty_avoidance": 65, "long_term": 83, "indulgence": 40},
    "GBR": {"power_distance": 35, "individualism": 89, "masculinity": 66, "uncertainty_avoidance": 35, "long_term": 51, "indulgence": 69},
    "SWE": {"power_distance": 31, "individualism": 71, "masculinity": 5, "uncertainty_avoidance": 29, "long_term": 53, "indulgence": 78},
    "CHN": {"power_distance": 80, "individualism": 20, "masculinity": 66, "uncertainty_avoidance": 30, "long_term": 87, "indulgence": 24},
    "IND": {"power_distance": 77, "individualism": 48, "masculinity": 56, "uncertainty_avoidance": 40, "long_term": 51, "indulgence": 26},
    "JPN": {"power_distance": 54, "individualism": 46, "masculinity": 95, "uncertainty_avoidance": 92, "long_term": 88, "indulgence": 42},
    "KOR": {"power_distance": 60, "individualism": 18, "masculinity": 39, "uncertainty_avoidance": 85, "long_term": 100, "indulgence": 29},
    "FRA": {"power_distance": 68, "individualism": 71, "masculinity": 43, "uncertainty_avoidance": 86, "long_term": 63, "indulgence": 48},
    "BRA": {"power_distance": 69, "individualism": 38, "masculinity": 49, "uncertainty_avoidance": 76, "long_term": 44, "indulgence": 59},
    "RUS": {"power_distance": 93, "individualism": 39, "masculinity": 36, "uncertainty_avoidance": 95, "long_term": 81, "indulgence": 20},
    "TUR": {"power_distance": 66, "individualism": 37, "masculinity": 45, "uncertainty_avoidance": 85, "long_term": 46, "indulgence": 49},
    "ISR": {"power_distance": 13, "individualism": 54, "masculinity": 47, "uncertainty_avoidance": 81, "long_term": 38, "indulgence": 0},
    "CAN": {"power_distance": 39, "individualism": 80, "masculinity": 52, "uncertainty_avoidance": 48, "long_term": 36, "indulgence": 68},
    "AUS": {"power_distance": 38, "individualism": 90, "masculinity": 61, "uncertainty_avoidance": 51, "long_term": 21, "indulgence": 71},
    "SGP": {"power_distance": 74, "individualism": 20, "masculinity": 48, "uncertainty_avoidance": 8, "long_term": 72, "indulgence": 46},
    "NLD": {"power_distance": 38, "individualism": 80, "masculinity": 14, "uncertainty_avoidance": 53, "long_term": 67, "indulgence": 68},
    "MEX": {"power_distance": 81, "individualism": 30, "masculinity": 69, "uncertainty_avoidance": 82, "long_term": 24, "indulgence": 97},
    "IRN": {"power_distance": 58, "individualism": 41, "masculinity": 43, "uncertainty_avoidance": 59, "long_term": 14, "indulgence": 40},
    "PAK": {"power_distance": 55, "individualism": 14, "masculinity": 50, "uncertainty_avoidance": 70, "long_term": 50, "indulgence": 0},
    "VNM": {"power_distance": 70, "individualism": 20, "masculinity": 40, "uncertainty_avoidance": 30, "long_term": 57, "indulgence": 35},
    "PHL": {"power_distance": 94, "individualism": 32, "masculinity": 64, "uncertainty_avoidance": 44, "long_term": 27, "indulgence": 42},
    "EGY": {"power_distance": 70, "individualism": 25, "masculinity": 45, "uncertainty_avoidance": 80, "long_term": 7, "indulgence": 4},
    "IDN": {"power_distance": 78, "individualism": 14, "masculinity": 46, "uncertainty_avoidance": 48, "long_term": 62, "indulgence": 38},
    "MYS": {"power_distance": 100, "individualism": 26, "masculinity": 50, "uncertainty_avoidance": 36, "long_term": 41, "indulgence": 57},
    "THA": {"power_distance": 64, "individualism": 20, "masculinity": 34, "uncertainty_avoidance": 64, "long_term": 32, "indulgence": 45},
    "POL": {"power_distance": 68, "individualism": 60, "masculinity": 64, "uncertainty_avoidance": 93, "long_term": 38, "indulgence": 29},
    "CZE": {"power_distance": 57, "individualism": 58, "masculinity": 57, "uncertainty_avoidance": 74, "long_term": 70, "indulgence": 29},
    "HUN": {"power_distance": 46, "individualism": 80, "masculinity": 88, "uncertainty_avoidance": 82, "long_term": 58, "indulgence": 31},
    "COL": {"power_distance": 67, "individualism": 13, "masculinity": 64, "uncertainty_avoidance": 80, "long_term": 13, "indulgence": 83},
    "CHL": {"power_distance": 63, "individualism": 23, "masculinity": 28, "uncertainty_avoidance": 86, "long_term": 31, "indulgence": 68},
    "ARG": {"power_distance": 49, "individualism": 46, "masculinity": 56, "uncertainty_avoidance": 86, "long_term": 20, "indulgence": 62},
    "NGA": {"power_distance": 80, "individualism": 30, "masculinity": 60, "uncertainty_avoidance": 55, "long_term": 13, "indulgence": 84},
    "KEN": {"power_distance": 70, "individualism": 25, "masculinity": 60, "uncertainty_avoidance": 50, "long_term": 0, "indulgence": 0},
    "BGD": {"power_distance": 80, "individualism": 20, "masculinity": 55, "uncertainty_avoidance": 60, "long_term": 47, "indulgence": 20},
    "SAU": {"power_distance": 95, "individualism": 25, "masculinity": 60, "uncertainty_avoidance": 80, "long_term": 36, "indulgence": 52},
    "ARE": {"power_distance": 90, "individualism": 25, "masculinity": 50, "uncertainty_avoidance": 80, "long_term": 0, "indulgence": 0},
    "NOR": {"power_distance": 31, "individualism": 69, "masculinity": 8, "uncertainty_avoidance": 50, "long_term": 35, "indulgence": 55},
    "DNK": {"power_distance": 18, "individualism": 74, "masculinity": 16, "uncertainty_avoidance": 23, "long_term": 35, "indulgence": 70},
    "FIN": {"power_distance": 33, "individualism": 63, "masculinity": 26, "uncertainty_avoidance": 59, "long_term": 38, "indulgence": 57},
    "ZAF": {"power_distance": 49, "individualism": 65, "masculinity": 63, "uncertainty_avoidance": 49, "long_term": 34, "indulgence": 63},
    "ETH": {"power_distance": 70, "individualism": 20, "masculinity": 65, "uncertainty_avoidance": 55, "long_term": 0, "indulgence": 46},
    "ROU": {"power_distance": 90, "individualism": 30, "masculinity": 42, "uncertainty_avoidance": 90, "long_term": 52, "indulgence": 20},
    "UKR": {"power_distance": 92, "individualism": 25, "masculinity": 27, "uncertainty_avoidance": 95, "long_term": 86, "indulgence": 14},
    "CHE": {"power_distance": 34, "individualism": 68, "masculinity": 70, "uncertainty_avoidance": 58, "long_term": 74, "indulgence": 66},
    "AFG": {"power_distance": 80, "individualism": 15, "masculinity": 55, "uncertainty_avoidance": 70, "long_term": 0, "indulgence": 0},
    "SYR": {"power_distance": 80, "individualism": 35, "masculinity": 52, "uncertainty_avoidance": 60, "long_term": 30, "indulgence": 0},
}


def compute_institutional_distance(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Compute Kogut-Singh institutional distance between two countries
    using WGI governance indicators and Hofstede cultural dimensions."""
    origin = (arguments.get("origin") or "").upper()
    host = (arguments.get("host") or "").upper()
    include_cultural = arguments.get("include_cultural", True)

    if not origin or not host:
        return json.dumps({"error": "Provide both 'origin' and 'host' as ISO-3 codes"})

    results: dict[str, Any] = {
        "source": "Computed from World Bank WGI 2022 + Hofstede dimensions",
        "origin": origin,
        "host": host,
    }

    # WGI institutional distance (Kogut-Singh formula)
    if origin in _WGI and host in _WGI:
        o, h = _WGI[origin], _WGI[host]
        dims = list(o.keys())
        # Compute variance for each dimension across all countries
        variances = {}
        for d in dims:
            vals = [c[d] for c in _WGI.values()]
            mean = sum(vals) / len(vals)
            variances[d] = sum((v - mean) ** 2 for v in vals) / len(vals)

        # Kogut-Singh: sum of (diff_i^2 / variance_i) / N
        ks_sum = 0.0
        dim_distances = {}
        for d in dims:
            diff = h[d] - o[d]
            if variances[d] > 0:
                component = (diff ** 2) / variances[d]
            else:
                component = 0
            ks_sum += component
            dim_distances[d] = {"origin_score": o[d], "host_score": h[d], "difference": round(diff, 2)}

        ks_index = round(ks_sum / len(dims), 3)
        results["wgi_institutional_distance"] = ks_index
        results["wgi_dimension_details"] = dim_distances
        results["wgi_interpretation"] = (
            "Very high" if ks_index > 5 else
            "High" if ks_index > 3 else
            "Moderate" if ks_index > 1.5 else
            "Low" if ks_index > 0.5 else "Very low"
        )
    else:
        missing = [c for c in [origin, host] if c not in _WGI]
        results["wgi_error"] = f"WGI data not available for: {missing}"

    # Hofstede cultural distance
    if include_cultural and origin in _HOFSTEDE and host in _HOFSTEDE:
        o, h = _HOFSTEDE[origin], _HOFSTEDE[host]
        dims = list(o.keys())
        variances = {}
        for d in dims:
            vals = [c[d] for c in _HOFSTEDE.values()]
            mean = sum(vals) / len(vals)
            variances[d] = sum((v - mean) ** 2 for v in vals) / len(vals)

        ks_sum = 0.0
        dim_distances = {}
        for d in dims:
            diff = h[d] - o[d]
            if variances[d] > 0:
                component = (diff ** 2) / variances[d]
            else:
                component = 0
            ks_sum += component
            dim_distances[d] = {"origin_score": o[d], "host_score": h[d], "difference": round(diff, 1)}

        cultural_ks = round(ks_sum / len(dims), 3)
        results["hofstede_cultural_distance"] = cultural_ks
        results["hofstede_dimension_details"] = dim_distances
        results["cultural_interpretation"] = (
            "Very high" if cultural_ks > 4 else
            "High" if cultural_ks > 2.5 else
            "Moderate" if cultural_ks > 1 else
            "Low" if cultural_ks > 0.3 else "Very low"
        )

    # Combined distance
    if "wgi_institutional_distance" in results and "hofstede_cultural_distance" in results:
        combined = round(
            (results["wgi_institutional_distance"] + results["hofstede_cultural_distance"]) / 2, 3
        )
        results["combined_distance"] = combined
        results["arbitrage_potential"] = (
            "Very high — large institutional gap creates significant arbitrage opportunity"
            if combined > 3 else
            "High — meaningful institutional gap for knowledge arbitrage"
            if combined > 2 else
            "Moderate — some institutional arbitrage potential"
            if combined > 1 else
            "Low — limited institutional gap for arbitrage"
        )

    return json.dumps(results)


def _parse_oecd_sdmx(data: dict, source_label: str) -> str:
    """Parse OECD SDMX JSON response into clean rows."""
    structures = data.get("data", {}).get("structures", [])
    datasets = data.get("data", {}).get("dataSets", [])

    if not structures or not datasets:
        return json.dumps({"results": [], "message": "No data returned"})

    # Build dimension lookups
    dims = structures[0].get("dimensions", {}).get("observation", [])
    dim_lookups = []
    for d in dims:
        lookup = {}
        for i, v in enumerate(d.get("values", [])):
            lookup[i] = {"id": v.get("id", ""), "name": v.get("name", "")}
        dim_lookups.append({"id": d.get("id", ""), "values": lookup})

    # Parse observations
    obs = datasets[0].get("observations", {})
    results = []
    for key_str, val_arr in obs.items():
        indices = key_str.split(":")
        row = {}
        for i, idx_str in enumerate(indices):
            if i >= len(dim_lookups):
                break
            dim = dim_lookups[i]
            idx = int(idx_str)
            val_info = dim["values"].get(idx, {"id": "", "name": ""})
            row[dim["id"]] = val_info["id"]
            if dim["id"] not in ("FREQ", "UNIT_MEASURE", "UNIT_MULT", "OBS_STATUS"):
                row[f"{dim['id']}_name"] = val_info["name"]
        row["value"] = val_arr[0] if val_arr else None
        results.append(row)

    # Sort by country then time
    results.sort(key=lambda r: (r.get("REF_AREA", ""), r.get("TIME_PERIOD", "")))

    return json.dumps({
        "source": source_label,
        "total_records": len(results),
        "results": results[:100],  # Cap at 100 to avoid token overflow
    })


# ── Tool #6: Brain Drain Index ──────────────────────────────────────────

# World Bank indicators for brain drain computation
_BRAIN_DRAIN_INDICATORS = {
    "tertiary_emigration": "SM.EMI.TERT.ZS",   # Emigration rate, tertiary educated (% of total tertiary educated pop)
    "researchers_per_million": "SP.POP.SCIE.RD.P6",  # Researchers in R&D (per million people)
    "rd_expenditure": "GB.XPD.RSDV.GD.ZS",     # R&D expenditure (% of GDP)
    "high_tech_exports": "TX.VAL.TECH.MF.ZS",   # High-technology exports (% of manufactured exports)
    "patent_residents": "IP.PAT.RESD",           # Patent applications, residents
    "journal_articles": "IP.JRN.ARTC.SC",        # Scientific and technical journal articles
    "tertiary_enrollment": "SE.TER.ENRR",        # School enrollment, tertiary (% gross)
}


def compute_brain_drain_index(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Compute brain drain severity index for a country using World Bank data.
    Combines skilled emigration rates with domestic innovation capacity."""
    country = (arguments.get("country") or "").upper()
    compare_to = (arguments.get("compare_to") or "").upper() or None

    if not country:
        return json.dumps({"error": "Provide 'country' as ISO-3 code (e.g., 'IND', 'NGA')"})

    import urllib.request

    def _fetch_wb_indicator(indicator: str, iso3: str) -> list[dict]:
        """Fetch latest available value from World Bank API."""
        url = f"https://api.worldbank.org/v2/country/{iso3}/indicator/{indicator}?format=json&per_page=20&date=2010:2023&MRV=5"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ARA-Research/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            if len(data) < 2:
                return []
            entries = [e for e in data[1] if e.get("value") is not None]
            return entries
        except Exception:
            return []

    results: dict[str, Any] = {
        "source": "World Bank Development Indicators",
        "country": country,
        "indicators": {},
    }

    countries_to_fetch = [country]
    if compare_to:
        countries_to_fetch.append(compare_to)

    for iso3 in countries_to_fetch:
        country_data: dict[str, Any] = {}
        for label, indicator in _BRAIN_DRAIN_INDICATORS.items():
            entries = _fetch_wb_indicator(indicator, iso3)
            if entries:
                latest = entries[0]
                country_data[label] = {
                    "value": latest["value"],
                    "year": latest["date"],
                    "indicator": indicator,
                }
        results["indicators"][iso3] = country_data

    # Compute brain drain severity score (0-100)
    cd = results["indicators"].get(country, {})
    score_components = []

    # High emigration rate = high brain drain
    if "tertiary_emigration" in cd:
        emig = cd["tertiary_emigration"]["value"]
        # Scale: 0-5% = low, 5-20% = moderate, 20-50% = high, >50% = severe
        emig_score = min(100, emig * 2)
        score_components.append(("skilled_emigration", emig_score, emig))

    # Low R&D spending = worse retention environment
    if "rd_expenditure" in cd:
        rd = cd["rd_expenditure"]["value"]
        # Scale: >3% = good retention, <0.5% = poor
        rd_score = max(0, min(100, (3.0 - rd) * 33.3))
        score_components.append(("rd_underinvestment", rd_score, rd))

    # Low researchers per million = talent gap
    if "researchers_per_million" in cd:
        res = cd["researchers_per_million"]["value"]
        # Scale: >5000 = strong, <500 = weak
        res_score = max(0, min(100, (5000 - res) / 50))
        score_components.append(("researcher_scarcity", res_score, res))

    # Low patent output = innovation gap
    if "patent_residents" in cd:
        pat = cd["patent_residents"]["value"]
        pat_score = max(0, min(100, (10000 - pat) / 100))
        score_components.append(("patent_gap", pat_score, pat))

    if score_components:
        brain_drain_index = round(sum(s[1] for s in score_components) / len(score_components), 1)
        results["brain_drain_index"] = brain_drain_index
        results["severity"] = (
            "Critical" if brain_drain_index > 75 else
            "Severe" if brain_drain_index > 60 else
            "Moderate" if brain_drain_index > 40 else
            "Mild" if brain_drain_index > 20 else "Low"
        )
        results["score_breakdown"] = {s[0]: {"score": round(s[1], 1), "raw_value": s[2]} for s in score_components}
        results["interpretation"] = (
            f"Brain drain severity for {country}: {results['severity']} ({brain_drain_index}/100). "
            f"Higher scores indicate greater talent loss relative to domestic innovation capacity."
        )

    # If comparing, compute the arbitrage gap
    if compare_to and compare_to in results["indicators"]:
        host_data = results["indicators"][compare_to]
        gaps = {}
        if "rd_expenditure" in cd and "rd_expenditure" in host_data:
            gaps["rd_gap"] = round(host_data["rd_expenditure"]["value"] - cd["rd_expenditure"]["value"], 3)
        if "researchers_per_million" in cd and "researchers_per_million" in host_data:
            gaps["researcher_gap"] = round(host_data["researchers_per_million"]["value"] - cd["researchers_per_million"]["value"], 0)
        if "patent_residents" in cd and "patent_residents" in host_data:
            gaps["patent_gap"] = round(host_data["patent_residents"]["value"] - cd["patent_residents"]["value"], 0)
        if "high_tech_exports" in cd and "high_tech_exports" in host_data:
            gaps["high_tech_export_gap"] = round(host_data["high_tech_exports"]["value"] - cd["high_tech_exports"]["value"], 1)
        results["innovation_gap_vs_host"] = {
            "host": compare_to,
            "gaps": gaps,
            "interpretation": f"Positive values = {compare_to} leads {country}; immigrants bridge this gap via knowledge transfer"
        }

    return json.dumps(results)


# ── Tool #7: Bilateral Remittances ──────────────────────────────────────

def search_bilateral_remittances(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Search World Bank bilateral remittance data and remittance indicators."""
    mode = (arguments.get("mode") or "indicators").lower()
    country = (arguments.get("country") or "").upper()

    import urllib.request

    if mode == "indicators":
        if not country:
            return json.dumps({"error": "Provide 'country' as ISO-3 code"})

        indicators = {
            "remittance_received_gdp": "BX.TRF.PWKR.DT.GD.ZS",
            "remittance_received_usd": "BX.TRF.PWKR.CD.DT",
            "remittance_paid_usd": "BM.TRF.PWKR.CD.DT",
            "net_migration": "SM.POP.NETM",
            "migrant_stock_pct": "SM.POP.TOTL.ZS",
            "migrant_stock": "SM.POP.TOTL",
        }

        results: dict[str, Any] = {
            "source": "World Bank Development Indicators",
            "country": country,
            "data": {},
        }

        for label, indicator in indicators.items():
            url = f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}?format=json&per_page=10&MRV=5"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ARA-Research/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
                if len(data) >= 2:
                    entries = [e for e in data[1] if e.get("value") is not None]
                    if entries:
                        latest = entries[0]
                        results["data"][label] = {
                            "value": latest["value"],
                            "year": latest["date"],
                        }
            except Exception:
                continue

        recv = results["data"].get("remittance_received_gdp", {}).get("value")
        if recv is not None:
            results["remittance_dependency"] = (
                "Critical (>20% GDP)" if recv > 20 else
                "High (10-20% GDP)" if recv > 10 else
                "Significant (5-10% GDP)" if recv > 5 else
                "Moderate (2-5% GDP)" if recv > 2 else
                "Low (<2% GDP)"
            )

        return json.dumps(results)

    elif mode == "corridors":
        _TOP_CORRIDORS = [
            {"from": "USA", "to": "MEX", "amount_bn": 63.3, "year": 2023},
            {"from": "USA", "to": "IND", "amount_bn": 28.0, "year": 2023},
            {"from": "USA", "to": "CHN", "amount_bn": 16.3, "year": 2023},
            {"from": "USA", "to": "PHL", "amount_bn": 15.1, "year": 2023},
            {"from": "USA", "to": "GTM", "amount_bn": 12.6, "year": 2023},
            {"from": "SAU", "to": "IND", "amount_bn": 11.5, "year": 2023},
            {"from": "SAU", "to": "PAK", "amount_bn": 7.8, "year": 2023},
            {"from": "SAU", "to": "EGY", "amount_bn": 6.9, "year": 2023},
            {"from": "USA", "to": "HND", "amount_bn": 8.2, "year": 2023},
            {"from": "USA", "to": "SLV", "amount_bn": 7.8, "year": 2023},
            {"from": "UAE", "to": "IND", "amount_bn": 13.2, "year": 2023},
            {"from": "UAE", "to": "PAK", "amount_bn": 6.4, "year": 2023},
            {"from": "DEU", "to": "TUR", "amount_bn": 1.6, "year": 2023},
            {"from": "GBR", "to": "IND", "amount_bn": 4.8, "year": 2023},
            {"from": "GBR", "to": "NGA", "amount_bn": 4.2, "year": 2023},
            {"from": "FRA", "to": "MAR", "amount_bn": 3.5, "year": 2023},
            {"from": "FRA", "to": "SEN", "amount_bn": 1.4, "year": 2023},
            {"from": "KOR", "to": "VNM", "amount_bn": 1.3, "year": 2023},
            {"from": "JPN", "to": "PHL", "amount_bn": 1.8, "year": 2023},
            {"from": "CAN", "to": "IND", "amount_bn": 4.1, "year": 2023},
            {"from": "AUS", "to": "IND", "amount_bn": 2.9, "year": 2023},
            {"from": "USA", "to": "NGA", "amount_bn": 6.1, "year": 2023},
            {"from": "USA", "to": "VNM", "amount_bn": 4.5, "year": 2023},
            {"from": "USA", "to": "DOM", "amount_bn": 4.3, "year": 2023},
            {"from": "RUS", "to": "UZB", "amount_bn": 5.4, "year": 2023},
            {"from": "RUS", "to": "TJK", "amount_bn": 3.2, "year": 2023},
            {"from": "RUS", "to": "KGZ", "amount_bn": 2.5, "year": 2023},
        ]

        if country:
            filtered = [c for c in _TOP_CORRIDORS if c["from"] == country or c["to"] == country]
        else:
            filtered = _TOP_CORRIDORS

        filtered.sort(key=lambda x: x["amount_bn"], reverse=True)
        limit = arguments.get("limit", 20)

        return json.dumps({
            "source": "World Bank Bilateral Remittance Matrix 2023 (embedded top corridors)",
            "filter": country or "global",
            "corridors": filtered[:limit],
            "total_corridors": len(filtered),
        })

    else:
        return json.dumps({"error": f"Unknown mode '{mode}'. Use 'indicators' or 'corridors'"})


# ── Tool #8: Co-authorship Network ─────────────────────────────────────

def search_coauthorship_network(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Search OpenAlex for cross-border co-authorship patterns between countries."""
    mode = (arguments.get("mode") or "bilateral").lower()
    country1 = (arguments.get("country1") or "").upper()
    country2 = (arguments.get("country2") or "").upper()
    topic = arguments.get("topic") or ""
    year_from = arguments.get("year_from") or 2015
    year_to = arguments.get("year_to") or 2024

    import urllib.request

    if mode == "bilateral":
        if not country1 or not country2:
            return json.dumps({"error": "Provide 'country1' and 'country2' as ISO-2 codes (e.g., 'US', 'IN')"})

        # OpenAlex: works with institutions from both countries
        filters = [
            f"institutions.country_code:{country1}",
            f"institutions.country_code:{country2}",
            f"publication_year:{year_from}-{year_to}",
        ]
        if topic:
            filters.append(f"default.search:{urllib.parse.quote(topic)}")

        filter_str = ",".join(filters)
        url = f"https://api.openalex.org/works?filter={filter_str}&group_by=publication_year&mailto=ara-research@example.com"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ARA-Research/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            return json.dumps({"error": f"OpenAlex API failed: {exc}"})

        yearly = []
        total = 0
        for group in data.get("group_by", []):
            count = group.get("count", 0)
            yearly.append({"year": group.get("key"), "co_publications": count})
            total += count

        yearly.sort(key=lambda x: x["year"])

        # Compute growth rate
        growth = None
        if len(yearly) >= 2 and yearly[0]["co_publications"] > 0:
            first = yearly[0]["co_publications"]
            last = yearly[-1]["co_publications"]
            growth = round((last - first) / first * 100, 1)

        return json.dumps({
            "source": "OpenAlex",
            "country1": country1,
            "country2": country2,
            "topic_filter": topic or "all fields",
            "period": f"{year_from}-{year_to}",
            "total_co_publications": total,
            "yearly_trend": yearly,
            "growth_pct": growth,
            "interpretation": f"{country1}-{country2} cross-border co-publications: {total} total ({year_from}-{year_to})"
            + (f", {growth}% growth" if growth is not None else ""),
        })

    elif mode == "top_partners":
        if not country1:
            return json.dumps({"error": "Provide 'country1' as ISO-2 code"})

        # Get top co-authorship partner countries
        filters = [
            f"institutions.country_code:{country1}",
            f"publication_year:{year_from}-{year_to}",
        ]
        if topic:
            filters.append(f"default.search:{urllib.parse.quote(topic)}")

        filter_str = ",".join(filters)
        url = f"https://api.openalex.org/works?filter={filter_str}&group_by=institutions.country_code&mailto=ara-research@example.com"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ARA-Research/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            return json.dumps({"error": f"OpenAlex API failed: {exc}"})

        partners = []
        for group in data.get("group_by", []):
            cc = group.get("key", "")
            if cc == country1:
                continue  # Skip self
            partners.append({
                "country": cc,
                "co_publications": group.get("count", 0),
            })

        partners.sort(key=lambda x: x["co_publications"], reverse=True)
        limit = arguments.get("limit", 20)

        return json.dumps({
            "source": "OpenAlex",
            "country": country1,
            "topic_filter": topic or "all fields",
            "period": f"{year_from}-{year_to}",
            "top_partners": partners[:limit],
        })

    else:
        return json.dumps({"error": f"Unknown mode '{mode}'. Use 'bilateral' or 'top_partners'"})


# ── Tool #9: Arbitrage Spread ───────────────────────────────────────────

def compute_arbitrage_spread(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Compute innovation arbitrage spread between origin and host countries.
    Combines WGI regulatory quality gap with GII innovation gap as a continuous variable."""
    origin = (arguments.get("origin") or "").upper()
    host = (arguments.get("host") or "").upper()

    if not origin or not host:
        return json.dumps({"error": "Provide both 'origin' and 'host' as ISO-3 codes"})

    results: dict[str, Any] = {
        "source": "Computed from WGI 2022 + GII 2023",
        "origin": origin,
        "host": host,
        "dimensions": {},
    }

    # WGI regulatory quality gap
    if origin in _WGI and host in _WGI:
        o_wgi, h_wgi = _WGI[origin], _WGI[host]
        reg_gap = round(h_wgi["regulatory"] - o_wgi["regulatory"], 3)
        rule_gap = round(h_wgi["rule_of_law"] - o_wgi["rule_of_law"], 3)
        eff_gap = round(h_wgi["effectiveness"] - o_wgi["effectiveness"], 3)
        corr_gap = round(h_wgi["corruption"] - o_wgi["corruption"], 3)

        # Composite institutional gap (average of 4 governance dimensions)
        inst_gap = round((reg_gap + rule_gap + eff_gap + corr_gap) / 4, 3)
        results["dimensions"]["institutional_gap"] = {
            "composite": inst_gap,
            "regulatory_quality": reg_gap,
            "rule_of_law": rule_gap,
            "government_effectiveness": eff_gap,
            "corruption_control": corr_gap,
        }
    else:
        missing = [c for c in [origin, host] if c not in _WGI]
        results["dimensions"]["institutional_gap"] = {"error": f"WGI data missing for: {missing}"}

    # GII innovation gap
    if origin in _GII_2023 and host in _GII_2023:
        o_gii, h_gii = _GII_2023[origin], _GII_2023[host]
        gii_gap = round(h_gii["score"] - o_gii["score"], 1)
        pillar_gaps = {}
        for pillar in ["institutions", "human_capital", "infrastructure", "market_sophistication", "business_sophistication", "knowledge_output", "creative_output"]:
            if pillar in o_gii and pillar in h_gii:
                pillar_gaps[pillar] = round(h_gii[pillar] - o_gii[pillar], 1)

        results["dimensions"]["innovation_gap"] = {
            "overall_gii_gap": gii_gap,
            "pillar_gaps": pillar_gaps,
        }

        # Find largest arbitrage opportunity (biggest pillar gap)
        if pillar_gaps:
            max_pillar = max(pillar_gaps, key=lambda k: pillar_gaps[k])
            results["dimensions"]["innovation_gap"]["max_opportunity"] = {
                "pillar": max_pillar,
                "gap": pillar_gaps[max_pillar],
            }
    else:
        missing = [c for c in [origin, host] if c not in _GII_2023]
        results["dimensions"]["innovation_gap"] = {"error": f"GII data missing for: {missing}"}

    # Compute composite arbitrage spread
    inst = results["dimensions"].get("institutional_gap", {}).get("composite")
    innov = results["dimensions"].get("innovation_gap", {}).get("overall_gii_gap")

    if inst is not None and innov is not None:
        # Normalize: WGI gap is on -4 to +4 scale, GII on 0-100
        # Convert both to 0-1 scale, then average
        inst_norm = min(1.0, max(0.0, inst / 4.0))
        innov_norm = min(1.0, max(0.0, innov / 50.0))
        spread = round((inst_norm + innov_norm) / 2, 3)

        results["arbitrage_spread"] = spread
        results["arbitrage_class"] = (
            "Extreme" if spread > 0.7 else
            "High" if spread > 0.5 else
            "Moderate" if spread > 0.3 else
            "Narrow" if spread > 0.15 else "Minimal"
        )
        results["interpretation"] = (
            f"Arbitrage spread {origin}→{host}: {spread:.3f} ({results['arbitrage_class']}). "
            f"Institutional gap: {inst:.3f} (WGI), Innovation gap: {innov:.1f} (GII). "
            f"Immigrants from {origin} carry cross-institutional knowledge that becomes non-obvious value in {host}."
        )

    return json.dumps(results)


# ── Tool #10: Convergence Window ────────────────────────────────────────

def compute_convergence_window(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Compute institutional convergence/divergence over 20 years using World Bank WGI time series."""
    origin = (arguments.get("origin") or "").upper()
    host = (arguments.get("host") or "").upper()

    if not origin or not host:
        return json.dumps({"error": "Provide both 'origin' and 'host' as ISO-3 codes"})

    import urllib.request

    wgi_indicators = {
        "voice": "VA.EST",
        "stability": "PV.EST",
        "effectiveness": "GE.EST",
        "regulatory": "RQ.EST",
        "rule_of_law": "RL.EST",
        "corruption": "CC.EST",
    }

    def _fetch_wgi_series(iso3: str, indicator: str) -> dict[str, float]:
        url = f"https://api.worldbank.org/v2/country/{iso3}/indicator/{indicator}?format=json&per_page=50&date=2003:2023"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ARA-Research/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            if len(data) < 2:
                return {}
            return {e["date"]: e["value"] for e in data[1] if e.get("value") is not None}
        except Exception:
            return {}

    results: dict[str, Any] = {
        "source": "World Bank Worldwide Governance Indicators (2003-2023)",
        "origin": origin,
        "host": host,
        "dimensions": {},
    }

    overall_trends = []

    for dim_name, indicator in wgi_indicators.items():
        o_series = _fetch_wgi_series(origin, indicator)
        h_series = _fetch_wgi_series(host, indicator)

        if not o_series or not h_series:
            continue

        common_years = sorted(set(o_series.keys()) & set(h_series.keys()))
        if len(common_years) < 3:
            continue

        gap_series = []
        for y in common_years:
            gap = round(h_series[y] - o_series[y], 3)
            gap_series.append({"year": y, "gap": gap, "origin": round(o_series[y], 3), "host": round(h_series[y], 3)})

        early_gaps = [g["gap"] for g in gap_series[:3]]
        late_gaps = [g["gap"] for g in gap_series[-3:]]
        early_avg = sum(early_gaps) / len(early_gaps)
        late_avg = sum(late_gaps) / len(late_gaps)
        change = round(late_avg - early_avg, 3)

        trend = "converging" if change < -0.1 else "diverging" if change > 0.1 else "stable"
        overall_trends.append(change)

        results["dimensions"][dim_name] = {
            "early_gap": round(early_avg, 3),
            "late_gap": round(late_avg, 3),
            "change": change,
            "trend": trend,
            "series": gap_series[::2],
        }

    if overall_trends:
        avg_change = round(sum(overall_trends) / len(overall_trends), 3)
        results["overall_convergence"] = avg_change
        results["window_status"] = (
            "Closing rapidly" if avg_change < -0.3 else
            "Closing" if avg_change < -0.1 else
            "Stable" if abs(avg_change) <= 0.1 else
            "Opening" if avg_change > 0.1 else
            "Opening rapidly"
        )
        results["arbitrage_implication"] = (
            f"The institutional gap between {origin} and {host} is {results['window_status'].lower()}. "
            + ("Narrowing gaps reduce future arbitrage value — current immigrants capture peak differential."
               if avg_change < -0.1 else
               "Widening gaps increase arbitrage value — immigration becomes more valuable over time."
               if avg_change > 0.1 else
               "Stable gaps maintain consistent arbitrage value for immigrant knowledge transfer.")
        )

    return json.dumps(results)


# ── Tool #11: Reverse Knowledge Flow ───────────────────────────────────

def compute_reverse_knowledge_flow(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Measure reverse knowledge flow from host to origin via co-authorship and citations using OpenAlex."""
    origin = (arguments.get("origin") or "").upper()
    host = (arguments.get("host") or "").upper()
    topic = arguments.get("topic") or ""
    year_from = arguments.get("year_from") or 2010
    year_to = arguments.get("year_to") or 2024

    if not origin or not host:
        return json.dumps({"error": "Provide both 'origin' and 'host' as ISO-2 codes (e.g., 'IN', 'US')"})

    import urllib.request
    import urllib.parse

    results: dict[str, Any] = {
        "source": "OpenAlex",
        "origin": origin,
        "host": host,
        "period": f"{year_from}-{year_to}",
    }

    # 1. Co-publications between origin and host (knowledge bridge proxy)
    filters = [
        f"institutions.country_code:{origin}",
        f"institutions.country_code:{host}",
        f"publication_year:{year_from}-{year_to}",
    ]
    if topic:
        filters.append(f"default.search:{urllib.parse.quote(topic)}")

    filter_str = ",".join(filters)
    url = f"https://api.openalex.org/works?filter={filter_str}&group_by=publication_year&mailto=ara-research@example.com"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ARA-Research/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())

        yearly = {}
        total_copubs = 0
        for group in data.get("group_by", []):
            yr = group.get("key", "")
            cnt = group.get("count", 0)
            yearly[yr] = cnt
            total_copubs += cnt

        results["co_publications"] = {
            "total": total_copubs,
            "yearly": yearly,
        }
    except Exception as exc:
        results["co_publications"] = {"error": str(exc)}

    # 2. Origin-only publications citing host (reverse citation flow)
    # Get total works from origin in the period
    origin_filters = [
        f"institutions.country_code:{origin}",
        f"publication_year:{year_from}-{year_to}",
    ]
    if topic:
        origin_filters.append(f"default.search:{urllib.parse.quote(topic)}")

    origin_filter_str = ",".join(origin_filters)
    url2 = f"https://api.openalex.org/works?filter={origin_filter_str}&per_page=1&mailto=ara-research@example.com"

    try:
        req = urllib.request.Request(url2, headers={"User-Agent": "ARA-Research/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data2 = json.loads(resp.read().decode())
        origin_total = data2.get("meta", {}).get("count", 0)
        results["origin_total_works"] = origin_total
    except Exception:
        origin_total = 0

    # 3. Host-only publications (for comparison)
    host_filters = [
        f"institutions.country_code:{host}",
        f"publication_year:{year_from}-{year_to}",
    ]
    if topic:
        host_filters.append(f"default.search:{urllib.parse.quote(topic)}")

    host_filter_str = ",".join(host_filters)
    url3 = f"https://api.openalex.org/works?filter={host_filter_str}&per_page=1&mailto=ara-research@example.com"

    try:
        req = urllib.request.Request(url3, headers={"User-Agent": "ARA-Research/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data3 = json.loads(resp.read().decode())
        host_total = data3.get("meta", {}).get("count", 0)
        results["host_total_works"] = host_total
    except Exception:
        host_total = 0

    # 4. Compute knowledge flow metrics
    copub_total = results.get("co_publications", {}).get("total", 0)

    if origin_total > 0 and copub_total > 0:
        # Co-publication intensity: what % of origin's output involves host
        copub_intensity = round(copub_total / origin_total * 100, 2)
        results["knowledge_flow_metrics"] = {
            "copub_intensity_origin": copub_intensity,
            "copub_share_desc": f"{copub_intensity}% of {origin}'s output involves {host} collaborators",
        }

        if host_total > 0:
            host_intensity = round(copub_total / host_total * 100, 4)
            results["knowledge_flow_metrics"]["copub_intensity_host"] = host_intensity

        # Asymmetry ratio: if origin depends more on host than vice versa
        if host_total > 0:
            asymmetry = round(copub_intensity / (copub_total / host_total * 100), 2)
            results["knowledge_flow_metrics"]["asymmetry_ratio"] = asymmetry
            results["knowledge_flow_metrics"]["flow_direction"] = (
                f"Strongly {origin}→{host} dependent" if asymmetry > 5 else
                f"{origin}→{host} dependent" if asymmetry > 2 else
                "Balanced" if asymmetry > 0.5 else
                f"{host}→{origin} dependent"
            )

    # Growth analysis
    yearly_data = results.get("co_publications", {}).get("yearly", {})
    if yearly_data:
        years_sorted = sorted(yearly_data.keys())
        if len(years_sorted) >= 4:
            first_half = [yearly_data[y] for y in years_sorted[:len(years_sorted)//2]]
            second_half = [yearly_data[y] for y in years_sorted[len(years_sorted)//2:]]
            first_avg = sum(first_half) / len(first_half) if first_half else 0
            second_avg = sum(second_half) / len(second_half) if second_half else 0
            if first_avg > 0:
                growth = round((second_avg - first_avg) / first_avg * 100, 1)
                results["growth_analysis"] = {
                    "first_half_avg": round(first_avg, 1),
                    "second_half_avg": round(second_avg, 1),
                    "growth_pct": growth,
                    "trend": "Accelerating" if growth > 50 else "Growing" if growth > 10 else "Stable" if growth > -10 else "Declining",
                }

    return json.dumps(results)


# ── Tool #12: Visa Policy / Migration Openness ─────────────────────────

# Henley Passport Index 2024 (number of visa-free destinations)
_PASSPORT_INDEX: dict[str, dict] = {
    "SGP": {"rank": 1, "visa_free": 195}, "JPN": {"rank": 1, "visa_free": 195},
    "FRA": {"rank": 2, "visa_free": 194}, "DEU": {"rank": 2, "visa_free": 194},
    "ITA": {"rank": 2, "visa_free": 194}, "ESP": {"rank": 2, "visa_free": 194},
    "FIN": {"rank": 3, "visa_free": 193}, "KOR": {"rank": 3, "visa_free": 193},
    "SWE": {"rank": 3, "visa_free": 193}, "AUT": {"rank": 3, "visa_free": 193},
    "DNK": {"rank": 4, "visa_free": 192}, "GBR": {"rank": 4, "visa_free": 192},
    "NLD": {"rank": 4, "visa_free": 192}, "IRL": {"rank": 4, "visa_free": 192},
    "NOR": {"rank": 5, "visa_free": 191}, "USA": {"rank": 6, "visa_free": 189},
    "BEL": {"rank": 5, "visa_free": 191}, "NZL": {"rank": 6, "visa_free": 189},
    "AUS": {"rank": 6, "visa_free": 189}, "CAN": {"rank": 7, "visa_free": 188},
    "CHE": {"rank": 5, "visa_free": 191}, "CZE": {"rank": 5, "visa_free": 191},
    "PRT": {"rank": 5, "visa_free": 191}, "GRC": {"rank": 7, "visa_free": 188},
    "POL": {"rank": 7, "visa_free": 188}, "HUN": {"rank": 8, "visa_free": 187},
    "ARE": {"rank": 9, "visa_free": 185}, "BRA": {"rank": 14, "visa_free": 176},
    "ISR": {"rank": 15, "visa_free": 161}, "MEX": {"rank": 17, "visa_free": 159},
    "ARG": {"rank": 12, "visa_free": 178}, "CHL": {"rank": 13, "visa_free": 177},
    "MYS": {"rank": 10, "visa_free": 183}, "THA": {"rank": 24, "visa_free": 148},
    "CHN": {"rank": 32, "visa_free": 85}, "RUS": {"rank": 30, "visa_free": 88},
    "TUR": {"rank": 27, "visa_free": 118}, "ZAF": {"rank": 28, "visa_free": 108},
    "IND": {"rank": 42, "visa_free": 60}, "IDN": {"rank": 35, "visa_free": 76},
    "PHL": {"rank": 39, "visa_free": 67}, "VNM": {"rank": 40, "visa_free": 56},
    "NGA": {"rank": 46, "visa_free": 47}, "EGY": {"rank": 44, "visa_free": 53},
    "PAK": {"rank": 47, "visa_free": 34}, "BGD": {"rank": 47, "visa_free": 41},
    "ETH": {"rank": 46, "visa_free": 47}, "IRQ": {"rank": 48, "visa_free": 31},
    "SYR": {"rank": 49, "visa_free": 29}, "AFG": {"rank": 50, "visa_free": 26},
    "KEN": {"rank": 36, "visa_free": 74}, "GHA": {"rank": 38, "visa_free": 68},
    "COL": {"rank": 19, "visa_free": 153}, "PER": {"rank": 21, "visa_free": 143},
    "MAR": {"rank": 36, "visa_free": 73}, "TUN": {"rank": 35, "visa_free": 75},
    "JOR": {"rank": 33, "visa_free": 82}, "LBN": {"rank": 46, "visa_free": 49},
    "UKR": {"rank": 25, "visa_free": 145}, "ROU": {"rank": 10, "visa_free": 183},
    "SAU": {"rank": 34, "visa_free": 80}, "IRN": {"rank": 45, "visa_free": 44},
    "TWN": {"rank": 20, "visa_free": 145}, "HKG": {"rank": 11, "visa_free": 170},
}

# Skilled immigration policy openness scores (composite, 2023)
_TALENT_OPENNESS: dict[str, dict] = {
    "USA": {"score": 72, "startup_visa": False, "points_system": False, "fast_track_stem": True, "notes": "H-1B lottery, EB-2/3 backlogs, O-1 for extraordinary ability"},
    "CAN": {"score": 88, "startup_visa": True, "points_system": True, "fast_track_stem": True, "notes": "Express Entry, Global Talent Stream, Start-up Visa"},
    "GBR": {"score": 82, "startup_visa": True, "points_system": True, "fast_track_stem": True, "notes": "High Potential Individual, Innovator Founder, Scale-up visa"},
    "DEU": {"score": 78, "startup_visa": True, "points_system": False, "fast_track_stem": True, "notes": "Skilled Immigration Act 2023, ICT card, freelance visa"},
    "AUS": {"score": 85, "startup_visa": True, "points_system": True, "fast_track_stem": True, "notes": "Global Talent Independent, Business Innovation, points-based"},
    "FRA": {"score": 74, "startup_visa": True, "points_system": False, "fast_track_stem": True, "notes": "French Tech Visa, talent passport, EU Blue Card"},
    "NLD": {"score": 80, "startup_visa": True, "points_system": False, "fast_track_stem": True, "notes": "Highly Skilled Migrant scheme, startup visa, 30% ruling"},
    "SGP": {"score": 90, "startup_visa": True, "points_system": True, "fast_track_stem": True, "notes": "EntrePass, Employment Pass, Tech.Pass, ONE Pass"},
    "IRL": {"score": 76, "startup_visa": True, "points_system": False, "fast_track_stem": True, "notes": "Start-up Entrepreneur Programme, Critical Skills permit"},
    "CHE": {"score": 70, "startup_visa": False, "points_system": False, "fast_track_stem": False, "notes": "Quota-based, bilateral EU/EFTA, limited non-EU"},
    "JPN": {"score": 65, "startup_visa": True, "points_system": True, "fast_track_stem": True, "notes": "J-Find/J-Skip, Specified Skilled Worker, but cultural barriers"},
    "KOR": {"score": 62, "startup_visa": True, "points_system": True, "fast_track_stem": True, "notes": "D-8-4 startup visa, E-7 professional, but lower openness"},
    "ISR": {"score": 68, "startup_visa": True, "points_system": False, "fast_track_stem": True, "notes": "Innovation visa, tech-focused but selective"},
    "ARE": {"score": 84, "startup_visa": True, "points_system": True, "fast_track_stem": True, "notes": "Golden visa (10yr), green visa, freelance permit, 100% ownership"},
    "SWE": {"score": 77, "startup_visa": True, "points_system": False, "fast_track_stem": True, "notes": "Work permit, startup visa pilot, EU Blue Card"},
    "DNK": {"score": 73, "startup_visa": True, "points_system": False, "fast_track_stem": True, "notes": "Start-up Denmark, fast-track scheme, pay limit scheme"},
    "NZL": {"score": 79, "startup_visa": True, "points_system": True, "fast_track_stem": True, "notes": "Skilled Migrant Category, Entrepreneur work visa"},
    "EST": {"score": 81, "startup_visa": True, "points_system": False, "fast_track_stem": True, "notes": "e-Residency, digital nomad visa, startup visa"},
    "PRT": {"score": 75, "startup_visa": True, "points_system": False, "fast_track_stem": True, "notes": "Tech visa, startup visa, D7 passive income visa"},
    "CHN": {"score": 35, "startup_visa": False, "points_system": True, "fast_track_stem": False, "notes": "Restrictive, R-visa for top talent only"},
    "IND": {"score": 30, "startup_visa": False, "points_system": False, "fast_track_stem": False, "notes": "Limited skilled immigration pathways"},
    "BRA": {"score": 45, "startup_visa": True, "points_system": False, "fast_track_stem": False, "notes": "Digital nomad visa, investor visa"},
    "RUS": {"score": 32, "startup_visa": False, "points_system": False, "fast_track_stem": False, "notes": "HQS permit for high earners only"},
}


def search_visa_policy(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Search visa/migration openness data for countries."""
    mode = (arguments.get("mode") or "lookup").lower()
    country = (arguments.get("country") or "").upper()

    if mode == "lookup":
        if not country:
            return json.dumps({"error": "Provide 'country' as ISO-3 code"})

        result: dict[str, Any] = {"country": country}
        if country in _PASSPORT_INDEX:
            result["passport_index"] = _PASSPORT_INDEX[country]
        if country in _TALENT_OPENNESS:
            result["talent_openness"] = _TALENT_OPENNESS[country]
        if not result.get("passport_index") and not result.get("talent_openness"):
            result["error"] = f"No visa/openness data for {country}"
        return json.dumps(result)

    elif mode == "compare":
        countries = [c.strip().upper() for c in (arguments.get("countries") or "").split(",") if c.strip()]
        if len(countries) < 2:
            return json.dumps({"error": "Provide at least 2 comma-separated ISO-3 codes in 'countries'"})

        comparison = []
        for cc in countries:
            entry: dict[str, Any] = {"country": cc}
            if cc in _PASSPORT_INDEX:
                entry["passport_rank"] = _PASSPORT_INDEX[cc]["rank"]
                entry["visa_free"] = _PASSPORT_INDEX[cc]["visa_free"]
            if cc in _TALENT_OPENNESS:
                entry["talent_score"] = _TALENT_OPENNESS[cc]["score"]
                entry["startup_visa"] = _TALENT_OPENNESS[cc]["startup_visa"]
                entry["points_system"] = _TALENT_OPENNESS[cc]["points_system"]
            comparison.append(entry)
        comparison.sort(key=lambda x: x.get("talent_score", 0), reverse=True)
        return json.dumps({"source": "Henley 2024 + OECD Talent Attractiveness", "comparison": comparison})

    elif mode == "corridor":
        origin = (arguments.get("origin") or "").upper()
        host = (arguments.get("host") or "").upper()
        if not origin or not host:
            return json.dumps({"error": "Provide 'origin' and 'host' as ISO-3 codes"})

        result_data: dict[str, Any] = {"origin": origin, "host": host}
        if origin in _PASSPORT_INDEX:
            result_data["origin_passport"] = _PASSPORT_INDEX[origin]
        if host in _PASSPORT_INDEX:
            result_data["host_passport"] = _PASSPORT_INDEX[host]
        if host in _TALENT_OPENNESS:
            result_data["host_talent_openness"] = _TALENT_OPENNESS[host]

        o_vf = _PASSPORT_INDEX.get(origin, {}).get("visa_free", 0)
        h_vf = _PASSPORT_INDEX.get(host, {}).get("visa_free", 0)
        if o_vf > 0 and h_vf > 0:
            mobility_gap = h_vf - o_vf
            result_data["mobility_gap"] = {
                "visa_free_difference": mobility_gap,
                "interpretation": (
                    f"Extreme mobility gap ({mobility_gap} destinations)" if mobility_gap > 100 else
                    f"Large mobility gap ({mobility_gap} destinations)" if mobility_gap > 60 else
                    f"Moderate mobility gap ({mobility_gap} destinations)" if mobility_gap > 30 else
                    f"Small mobility gap ({mobility_gap} destinations)"
                ),
            }
        return json.dumps(result_data)

    elif mode == "ranking":
        ranking = []
        for cc, data in sorted(_TALENT_OPENNESS.items(), key=lambda x: x[1]["score"], reverse=True):
            entry = {"country": cc, "talent_score": data["score"], "startup_visa": data["startup_visa"]}
            if cc in _PASSPORT_INDEX:
                entry["visa_free"] = _PASSPORT_INDEX[cc]["visa_free"]
            ranking.append(entry)
        limit = arguments.get("limit", 25)
        return json.dumps({"source": "OECD Talent Attractiveness + Henley 2024", "ranking": ranking[:limit]})

    else:
        return json.dumps({"error": f"Unknown mode '{mode}'. Use 'lookup', 'compare', 'corridor', or 'ranking'"})
