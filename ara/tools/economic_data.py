# Location: ara/tools/economic_data.py
# Purpose: 16 data source tools (World Bank, FRED, IMF, OECD, Comtrade, Eurostat, REST Countries, Exchange Rates, Patents, WTO, CPI, SEC, UN SDG, WHO, ILO, Air Quality)
# Functions: search_world_bank, search_fred, search_imf, search_oecd, search_comtrade, search_eurostat, search_countries, search_exchange_rates, search_patents, search_wto, search_transparency, search_sec_edgar, search_un_sdg, search_who, search_ilo, search_air_quality
# Calls: httpx for HTTP, json for serialization
# Imports: httpx, json, os, logging

from __future__ import annotations

import json
import logging
import os
from typing import Any
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx

_log = logging.getLogger(__name__)
_TIMEOUT = 30
_USER_AGENT = "Mozilla/5.0 (compatible; ARA-Research/1.0; +https://github.com)"

# Cache for credentials loaded from ~/.ara/credentials.json
_CRED_CACHE: dict[str, str] | None = None


def _get_key(env_var: str, cred_field: str) -> str:
    """Load API key from env var first, then ~/.ara/credentials.json fallback."""
    val = os.getenv(env_var, "")
    if val:
        return val
    global _CRED_CACHE
    if _CRED_CACHE is None:
        cred_path = Path.home() / ".ara" / "credentials.json"
        try:
            _CRED_CACHE = json.loads(cred_path.read_text("utf-8")) if cred_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            _CRED_CACHE = {}
    return _CRED_CACHE.get(cred_field, "")


# ─────────────────────────────────────────────────────────────────────────────
# 1. WORLD BANK
# ─────────────────────────────────────────────────────────────────────────────

def search_world_bank(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """World Bank API — 16,000+ indicators, 200+ countries, free, no auth."""
    mode = arguments.get("mode", "search")

    try:
        if mode == "search":
            return _wb_search_indicators(arguments)
        elif mode == "data":
            return _wb_get_indicator_data(arguments)
        elif mode == "snapshot":
            return _wb_get_country_snapshot(arguments)
        else:
            return json.dumps({"error": f"Unknown mode: {mode}"})
    except Exception as e:
        _log.error(f"World Bank error: {e}")
        return json.dumps({"error": str(e)})


def _wb_search_indicators(args: dict[str, Any]) -> str:
    """Search World Bank indicators by keyword."""
    query = args.get("query", "")
    limit = min(args.get("limit", 15), 100)

    if not query:
        return json.dumps({"indicators": [], "error": "query required"})

    url = "https://api.worldbank.org/v2/indicator"
    params = {
        "format": "json",
        "per_page": limit,
        "source": "2",
        "search": query,
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            data = resp.json()

        if not isinstance(data, list) or len(data) < 2:
            return json.dumps({"indicators": [], "total": 0, "query": query})

        meta = data[0]
        indicators = []
        for ind in data[1] or []:
            indicators.append({
                "id": ind.get("id", ""),
                "name": ind.get("name", ""),
                "sourceNote": (ind.get("sourceNote") or "")[:200],
            })

        return json.dumps({
            "indicators": indicators,
            "total": meta.get("total", len(indicators)),
            "query": query,
        })
    except Exception as e:
        _log.warning(f"World Bank search failed: {e}")
        return json.dumps({"indicators": [], "error": str(e)})


def _wb_get_indicator_data(args: dict[str, Any]) -> str:
    """Get World Bank indicator data across countries and years."""
    indicator_id = args.get("indicator_id", "")
    countries = args.get("countries", ["all"])
    start_year = args.get("start_year", 2015)
    end_year = args.get("end_year", 2023)

    if not indicator_id:
        return json.dumps({"data": [], "error": "indicator_id required"})

    is_all = "all" in countries
    country_param = "all" if is_all else ";".join(countries)
    per_page = 200 if is_all else 500

    url = f"https://api.worldbank.org/v2/country/{country_param}/indicator/{quote(indicator_id)}"
    params = {
        "format": "json",
        "date": f"{start_year}:{end_year}",
        "per_page": per_page,
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            raw = resp.json()

        if not isinstance(raw, list) or len(raw) < 2:
            return json.dumps({
                "data": [],
                "indicator": indicator_id,
                "yearRange": f"{start_year}-{end_year}",
            })

        points = []
        for d in raw[1] or []:
            if d.get("value") is not None:
                points.append({
                    "country": (d.get("country") or {}).get("value", "Unknown"),
                    "countryCode": d.get("countryiso3code") or (d.get("country") or {}).get("id", ""),
                    "year": int(d.get("date", 0)),
                    "value": d.get("value"),
                    "indicator": (d.get("indicator") or {}).get("id", indicator_id),
                    "indicatorName": (d.get("indicator") or {}).get("value", ""),
                })

        return json.dumps({
            "data": points,
            "indicator": indicator_id,
            "countries": countries,
            "yearRange": f"{start_year}-{end_year}",
        })
    except Exception as e:
        _log.warning(f"World Bank data fetch failed: {e}")
        return json.dumps({"data": [], "error": str(e)})


def _wb_get_country_snapshot(args: dict[str, Any]) -> str:
    """Quick country comparison: GDP/capita, FDI, trade, internet."""
    countries = args.get("countries", [])
    year = args.get("year", 2022)

    if not countries:
        return json.dumps({"error": "countries required"})

    indicators = [
        ("NY.GDP.PCAP.CD", "GDP/capita (USD)"),
        ("BX.KLT.DINV.WD.GD.ZS", "FDI net inflows (% GDP)"),
        ("NE.TRD.GNFS.ZS", "Trade (% GDP)"),
        ("IT.NET.USER.ZS", "Internet users (%)"),
    ]

    lines = []
    for ind_id, label in indicators:
        result_str = _wb_get_indicator_data({
            "indicator_id": ind_id,
            "countries": countries,
            "start_year": year,
            "end_year": year,
        })
        try:
            result = json.loads(result_str)
            if result.get("data"):
                entries = [
                    f"{d['country']}: {d['value']:.2f}" if isinstance(d['value'], (int, float)) else f"{d['country']}: N/A"
                    for d in result["data"]
                ]
                lines.append(f"{label}: {' | '.join(entries)}")
        except Exception:
            pass

    if lines:
        return "\n".join(lines)
    return f"No data available for {', '.join(countries)} in {year}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. FRED
# ─────────────────────────────────────────────────────────────────────────────

def search_fred(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """FRED API — 816,000+ time series, free with API key."""
    mode = arguments.get("mode", "search")

    try:
        if mode == "search":
            return _fred_search_series(arguments)
        elif mode == "data":
            return _fred_get_series_data(arguments)
        else:
            return json.dumps({"error": f"Unknown mode: {mode}"})
    except Exception as e:
        _log.error(f"FRED error: {e}")
        return json.dumps({"error": str(e)})


def _fred_search_series(args: dict[str, Any]) -> str:
    """Search FRED series by keyword."""
    query = args.get("query", "")
    limit = min(args.get("limit", 10), 100)
    api_key = _get_key("FRED_API_KEY", "fred_api_key")

    if not query:
        return json.dumps({"series": [], "error": "query required"})
    if not api_key:
        return json.dumps({"series": [], "error": "FRED_API_KEY env var not set"})

    url = "https://api.stlouisfed.org/fred/series/search"
    params = {
        "search_text": query,
        "limit": limit,
        "api_key": api_key,
        "file_type": "json",
        "order_by": "popularity",
        "sort_order": "desc",
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            data = resp.json()

        series = []
        for s in data.get("seriess", []):
            series.append({
                "id": s.get("id", ""),
                "title": s.get("title", ""),
                "frequency": s.get("frequency", ""),
                "units": s.get("units", ""),
                "seasonalAdjustment": s.get("seasonal_adjustment", ""),
                "lastUpdated": s.get("last_updated", ""),
                "notes": (s.get("notes") or "")[:200],
            })

        return json.dumps({
            "series": series,
            "total": data.get("count", len(series)),
            "query": query,
        })
    except Exception as e:
        _log.warning(f"FRED search failed: {e}")
        return json.dumps({"series": [], "error": str(e)})


def _fred_get_series_data(args: dict[str, Any]) -> str:
    """Get FRED series observations with statistics."""
    series_id = args.get("series_id", "")
    start_date = args.get("start_date", "2015-01-01")
    end_date = args.get("end_date", "2023-12-31")
    api_key = _get_key("FRED_API_KEY", "fred_api_key")

    if not series_id:
        return json.dumps({"error": "series_id required"})
    if not api_key:
        return json.dumps({"error": "FRED_API_KEY env var not set"})

    base_url = "https://api.stlouisfed.org/fred"
    info_params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
    }
    obs_params = {
        "series_id": series_id,
        "observation_start": start_date,
        "observation_end": end_date,
        "api_key": api_key,
        "file_type": "json",
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            info_resp = client.get(f"{base_url}/series", params=info_params, headers={"User-Agent": _USER_AGENT})
            obs_resp = client.get(f"{base_url}/series/observations", params=obs_params, headers={"User-Agent": _USER_AGENT})
            info_resp.raise_for_status()
            obs_resp.raise_for_status()
            info_data = info_resp.json()
            obs_data = obs_resp.json()

        title = series_id
        units = ""
        s = (info_data.get("seriess") or [{}])[0]
        if s:
            title = s.get("title", series_id)
            units = s.get("units", "")

        observations = []
        for obs in obs_data.get("observations", []):
            val_str = obs.get("value", ".")
            val = None if val_str == "." else float(val_str)
            observations.append({
                "date": obs.get("date", ""),
                "value": val,
            })

        # Calculate stats
        valid_obs = [o for o in observations if o["value"] is not None]
        if valid_obs:
            values = [o["value"] for o in valid_obs]
            latest = valid_obs[-1] if valid_obs else None
            earliest = valid_obs[0] if valid_obs else None
            min_val = min(values)
            max_val = max(values)
            avg_val = sum(values) / len(values)

            lines = [
                f"{title} ({series_id})",
                f"Units: {units}",
                f"Period: {earliest['date']} to {latest['date']} ({len(valid_obs)} observations)",
                f"Latest: {latest['value']} ({latest['date']})",
                f"Range: {min_val:.2f} — {max_val:.2f}",
                f"Average: {avg_val:.2f}",
            ]
            recent = valid_obs[-5:] if len(valid_obs) >= 5 else valid_obs
            recent_str = ", ".join(f"{o['date']}={o['value']}" for o in recent)
            lines.append(f"Recent values: {recent_str}")
            return "\n".join(lines)
        else:
            return f"{title} ({series_id}): No data available"
    except Exception as e:
        _log.warning(f"FRED data fetch failed: {e}")
        return json.dumps({"error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# 3. IMF
# ─────────────────────────────────────────────────────────────────────────────

def search_imf(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """IMF DataMapper API — 133 indicators, 241 countries, free, no auth."""
    mode = arguments.get("mode", "search")

    try:
        if mode == "search":
            return _imf_search_indicators(arguments)
        elif mode == "data":
            return _imf_get_indicator_data(arguments)
        else:
            return json.dumps({"error": f"Unknown mode: {mode}"})
    except Exception as e:
        _log.error(f"IMF error: {e}")
        return json.dumps({"error": str(e)})


def _imf_search_indicators(args: dict[str, Any]) -> str:
    """Search IMF indicators by keyword."""
    query = args.get("query", "")
    limit = min(args.get("limit", 15), 133)

    if not query:
        return json.dumps({"indicators": [], "error": "query required"})

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                "https://www.imf.org/external/datamapper/api/v1/indicators",
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()

        indicators_map = data.get("indicators", {})
        q = query.lower()
        matches = []
        for ind_id, info in indicators_map.items():
            label = str(info.get("label", "")).lower()
            description = str(info.get("description", "")).lower()
            if q in label or q in description or q in ind_id.lower():
                matches.append({
                    "id": ind_id,
                    "label": info.get("label", ind_id),
                    "description": (info.get("description") or "")[:300],
                    "unit": info.get("unit", ""),
                    "dataset": info.get("dataset", ""),
                })
                if len(matches) >= limit:
                    break

        return json.dumps({"indicators": matches, "query": query})
    except Exception as e:
        _log.warning(f"IMF search failed: {e}")
        return json.dumps({"indicators": [], "error": str(e)})


def _imf_get_indicator_data(args: dict[str, Any]) -> str:
    """Get IMF indicator data across countries and years."""
    indicator_id = args.get("indicator_id", "")
    countries = args.get("countries", [])
    start_year = args.get("start_year", 2015)
    end_year = args.get("end_year", 2023)

    if not indicator_id:
        return json.dumps({"data": [], "error": "indicator_id required"})

    years = [str(y) for y in range(start_year, end_year + 1)]
    periods_param = ",".join(years)

    url = f"https://www.imf.org/external/datamapper/api/v1/{quote(indicator_id)}"
    params = {"periods": periods_param}

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            data = resp.json()

        # Load countries map
        countries_resp = client.get(
            "https://www.imf.org/external/datamapper/api/v1/countries",
            headers={"User-Agent": _USER_AGENT},
        )
        countries_resp.raise_for_status()
        countries_data = countries_resp.json()
        countries_map = {code: info.get("label", code) for code, info in countries_data.get("countries", {}).items()}

        values = data.get("values", {}).get(indicator_id, {})
        results = []
        for code, year_data in values.items():
            # Filter to requested countries if specified
            if countries and code not in countries:
                continue
            for year, value in year_data.items():
                if isinstance(value, (int, float)):
                    results.append({
                        "country": countries_map.get(code, code),
                        "countryCode": code,
                        "year": year,
                        "value": value,
                    })

        results.sort(key=lambda x: (x["countryCode"], x["year"]))
        return json.dumps({"data": results, "indicator": indicator_id})
    except Exception as e:
        _log.warning(f"IMF data fetch failed: {e}")
        return json.dumps({"data": [], "error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# 4. OECD
# ─────────────────────────────────────────────────────────────────────────────

_OECD_CURATED = {
    "fdi_flows": {
        "agency": "OECD.DAF.INV",
        "dsd": "DSD_FDI",
        "flow": "DF_FDI_FLOW_AGGR",
        "name": "FDI Flows (main aggregates)",
    },
    "fdi_positions": {
        "agency": "OECD.DAF.INV",
        "dsd": "DSD_FDI",
        "flow": "DF_FDI_POS_AGGR",
        "name": "FDI Positions (main aggregates)",
    },
    "fdi_restrictiveness": {
        "agency": "OECD.DAF.INV",
        "dsd": "DSD_FDIRRI_SCORES",
        "flow": "DF_FDIRRI_SCORES",
        "name": "FDI Regulatory Restrictiveness Index",
    },
    "stri": {
        "agency": "OECD.TAD.TPD",
        "dsd": "DSD_STRI",
        "flow": "DF_STRI_MAIN",
        "name": "Services Trade Restrictiveness Index",
    },
    "digital_stri": {
        "agency": "OECD.TAD.TPD",
        "dsd": "DSD_STRI",
        "flow": "DF_STRI_DIGITAL",
        "name": "Digital Services Trade Restrictiveness Index",
    },
    "digital_trade": {
        "agency": "OECD.STI.DEP",
        "dsd": "DSD_DIGITAL_TRADE",
        "flow": "DF_DIGITAL_TRADE",
        "name": "Digital Trade",
    },
    "trade_services": {
        "agency": "OECD.SDD.TPS",
        "dsd": "DSD_BOP",
        "flow": "DF_TIS",
        "name": "International Trade in Services",
    },
    "trade_goods": {
        "agency": "OECD.SDD.TPS",
        "dsd": "DSD_IMTS",
        "flow": "DF_IMTS",
        "name": "International Merchandise Trade Statistics",
    },
    "indigo": {
        "agency": "OECD.TAD.TPD",
        "dsd": "DSD_INDIGO",
        "flow": "DF_INDIGO",
        "name": "Digital Trade Integration and Openness Index",
    },
}


def search_oecd(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """OECD SDMX API — FDI, trade, digital economy, STRI datasets."""
    mode = arguments.get("mode", "list")

    try:
        if mode == "list":
            return _oecd_list_curated()
        elif mode == "search":
            return _oecd_search_dataflows(arguments)
        elif mode == "data":
            return _oecd_query_data(arguments)
        else:
            return json.dumps({"error": f"Unknown mode: {mode}"})
    except Exception as e:
        _log.error(f"OECD error: {e}")
        return json.dumps({"error": str(e)})


def _oecd_list_curated() -> str:
    """List curated OECD datasets."""
    datasets = [
        {"id": k, "name": v["name"]} for k, v in _OECD_CURATED.items()
    ]
    return json.dumps({"datasets": datasets})


def _oecd_search_dataflows(args: dict[str, Any]) -> str:
    """Search OECD dataflows (1,475+ datasets)."""
    query = args.get("query", "")
    limit = min(args.get("limit", 10), 100)

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                "https://sdmx.oecd.org/public/rest/dataflow/*",
                headers={
                    "Accept": "application/vnd.sdmx.structure+json",
                    "User-Agent": _USER_AGENT,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        flows = data.get("data", {}).get("dataflows", [])
        dataflows = []
        for f in flows:
            name = f.get("name") if isinstance(f.get("name"), str) else f.get("name", {}).get("en", "")
            dataflows.append({
                "id": f.get("id", ""),
                "agencyId": f.get("agencyID", ""),
                "name": name,
            })

        if query:
            q = query.lower()
            dataflows = [f for f in dataflows if q in f["name"].lower() or q in f["id"].lower()]

        return json.dumps({"dataflows": dataflows[:limit]})
    except Exception as e:
        _log.warning(f"OECD dataflow search failed: {e}")
        return json.dumps({"dataflows": [], "error": str(e)})


def _oecd_query_data(args: dict[str, Any]) -> str:
    """Query OECD curated dataset."""
    dataset_key = args.get("dataset_key", "")
    countries = args.get("countries", [])
    start_period = args.get("start_period", "2018")
    end_period = args.get("end_period", "2023")

    if not dataset_key or dataset_key not in _OECD_CURATED:
        return json.dumps({
            "observations": 0,
            "data": [],
            "error": f"Unknown dataset: {dataset_key}. Available: {', '.join(_OECD_CURATED.keys())}",
        })

    ds = _OECD_CURATED[dataset_key]

    try:
        # For simplicity, build a basic query without full dimension parsing
        country_filter = "+".join(countries) if countries else ""
        key = country_filter if country_filter else "."

        url = f"https://sdmx.oecd.org/public/rest/data/{ds['agency']},{ds['dsd']}@{ds['flow']}/{key}"
        params = {
            "startPeriod": start_period,
            "endPeriod": end_period,
            "dimensionAtObservation": "AllDimensions",
        }

        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                url,
                params=params,
                headers={
                    "Accept": "application/vnd.sdmx.data+json",
                    "User-Agent": _USER_AGENT,
                },
            )
            resp.raise_for_status()
            result = resp.json()

        datasets = result.get("data", {}).get("dataSets", [])
        if not datasets:
            return json.dumps({"observations": 0, "data": []})

        obs = datasets[0].get("observations", {})
        dim_defs = result.get("data", {}).get("structure", {}).get("dimensions", {}).get("observation", [])

        parsed = []
        for key_str, val in obs.items():
            indices = key_str.split(":")
            record = {"value": val[0] if isinstance(val, list) else val}
            for i, idx_str in enumerate(indices):
                if i < len(dim_defs):
                    dim_values = dim_defs[i].get("values", [])
                    try:
                        idx = int(idx_str)
                        if idx < len(dim_values):
                            record[dim_defs[i].get("id", f"dim{i}")] = dim_values[idx].get("id", "")
                    except (ValueError, IndexError):
                        pass
            parsed.append(record)

        return json.dumps({"observations": len(parsed), "data": parsed})
    except Exception as e:
        _log.warning(f"OECD data query failed: {e}")
        return json.dumps({"observations": 0, "data": [], "error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# 5. COMTRADE
# ─────────────────────────────────────────────────────────────────────────────

_COMTRADE_COUNTRIES = {
    "USA": 842,
    "CHN": 156,
    "DEU": 276,
    "JPN": 392,
    "GBR": 826,
    "FRA": 250,
    "IND": 356,
    "ITA": 380,
    "CAN": 124,
    "KOR": 410,
    "BRA": 76,
    "AUS": 36,
    "MEX": 484,
    "NLD": 528,
    "CHE": 756,
    "SGP": 702,
    "ARE": 784,
    "SAU": 682,
    "IDN": 360,
    "TUR": 792,
    "ZAF": 710,
    "RUS": 643,
    "NGA": 566,
    "SWE": 752,
    "NOR": 578,
    "POL": 616,
    "ESP": 724,
    "THA": 764,
    "VNM": 704,
    "MYS": 458,
}

_COMTRADE_COUNTRIES_NAMES = {v: k for k, v in _COMTRADE_COUNTRIES.items()}

_FLOW_NAMES = {
    "X": "Exports",
    "M": "Imports",
    "RX": "Re-exports",
    "RM": "Re-imports",
}


def search_comtrade(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """UN Comtrade API — bilateral trade flows, free preview endpoint."""
    try:
        reporter = arguments.get("reporter", "")
        partners = arguments.get("partners", [])
        flow = arguments.get("flow", "X,M")
        year = arguments.get("year", 2022)
        commodity = arguments.get("commodity", "TOTAL")

        if not reporter:
            return json.dumps({"error": "reporter (ISO3) required"})

        reporter_code = _COMTRADE_COUNTRIES.get(reporter.upper())
        if not reporter_code:
            return json.dumps({
                "error": f"Unknown country: {reporter}. Available: {', '.join(sorted(_COMTRADE_COUNTRIES.keys()))}",
            })

        partner_codes = []
        for p in partners:
            code = _COMTRADE_COUNTRIES.get(p.upper())
            if code:
                partner_codes.append(str(code))

        partner_param = ",".join(partner_codes) if partner_codes else "0"  # 0 = World

        url = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
        params = {
            "reporterCode": reporter_code,
            "period": year,
            "partnerCode": partner_param,
            "flowCode": flow,
            "cmdCode": commodity,
        }

        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            data = resp.json()

        records = []
        for r in data.get("data", []):
            reporter_name = _COMTRADE_COUNTRIES_NAMES.get(r.get("reporterCode"), reporter)
            partner_name = _COMTRADE_COUNTRIES_NAMES.get(r.get("partnerCode"), f"Partner({r.get('partnerCode')})")
            flow_name = _FLOW_NAMES.get(r.get("flowCode"), r.get("flowDesc", r.get("flowCode")))

            records.append({
                "reporter": reporter_name,
                "reporterCode": reporter,
                "partner": partner_name,
                "partnerCode": r.get("partnerCode"),
                "flow": flow_name,
                "year": r.get("refYear"),
                "valueFOB": r.get("fobvalue"),
                "valueCIF": r.get("cifvalue"),
                "primaryValue": r.get("primaryValue", 0),
                "commodity": r.get("cmdDesc", commodity),
            })

        return json.dumps({"records": records, "reporter": reporter})
    except Exception as e:
        _log.warning(f"Comtrade fetch failed: {e}")
        return json.dumps({"records": [], "error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# 6. EUROSTAT
# ─────────────────────────────────────────────────────────────────────────────

_EUROSTAT_CURATED = {
    "gdp": {
        "code": "nama_10_gdp",
        "name": "GDP and main components",
        "fixedParams": "unit=CP_MEUR&na_item=B1GQ",
    },
    "gdp_per_capita": {
        "code": "nama_10_pc",
        "name": "GDP per capita",
        "fixedParams": "unit=CP_EUR_HAB&na_item=B1GQ",
    },
    "trade_goods": {
        "code": "ext_tec01",
        "name": "Extra-EU trade by partner",
        "fixedParams": "sitc06=TOTAL&stk_flow=BAL&unit=MIO_EUR",
    },
    "trade_services": {
        "code": "bop_its6_det",
        "name": "International trade in services",
        "fixedParams": "bop_item=S&stk_flow=BAL&currency=MIO_EUR",
    },
    "fdi_positions": {
        "code": "bop_fdi6_pos",
        "name": "FDI positions",
        "fixedParams": "currency=MIO_EUR&stk_flow=NET&bop_item=T_FA_F",
    },
    "inflation": {
        "code": "prc_hicp_aind",
        "name": "HICP - annual average inflation",
        "fixedParams": "unit=RCH_A_AVG&coicop=CP00",
    },
    "unemployment": {
        "code": "une_rt_a",
        "name": "Unemployment rate - annual",
        "fixedParams": "unit=PC_ACT&sex=T&age=Y15-74",
    },
    "ict_enterprises": {
        "code": "isoc_eb_ics",
        "name": "ICT usage in enterprises",
        "fixedParams": "unit=PC_ENT&sizen_r2=10_C10_S951_XK",
    },
    "internet_use": {
        "code": "isoc_ci_ifp_iu",
        "name": "Internet use by individuals",
        "fixedParams": "unit=PC_IND&ind_type=IND_TOTAL",
    },
    "ecommerce": {
        "code": "isoc_ec_ibuy",
        "name": "E-commerce by individuals",
        "fixedParams": "unit=PC_IND&ind_type=IND_TOTAL",
    },
}


def search_eurostat(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Eurostat API — EU economic, trade, and digital economy statistics."""
    mode = arguments.get("mode", "list")

    try:
        if mode == "list":
            return _eurostat_list_datasets()
        elif mode == "data":
            return _eurostat_get_data(arguments)
        else:
            return json.dumps({"error": f"Unknown mode: {mode}"})
    except Exception as e:
        _log.error(f"Eurostat error: {e}")
        return json.dumps({"error": str(e)})


def _eurostat_list_datasets() -> str:
    """List curated Eurostat datasets."""
    datasets = [
        {"id": k, "name": v["name"]} for k, v in _EUROSTAT_CURATED.items()
    ]
    return json.dumps({"datasets": datasets})


def _eurostat_get_data(args: dict[str, Any]) -> str:
    """Get Eurostat dataset."""
    dataset_key = args.get("dataset_key", "")
    countries = args.get("countries", [])
    start_year = args.get("start_year", 2018)
    end_year = args.get("end_year", 2023)

    if not dataset_key or dataset_key not in _EUROSTAT_CURATED:
        return json.dumps({
            "data": [],
            "error": f"Unknown dataset: {dataset_key}. Available: {', '.join(_EUROSTAT_CURATED.keys())}",
        })

    ds = _EUROSTAT_CURATED[dataset_key]
    geos = countries if countries else ["DE", "FR", "IT", "ES", "NL", "SE", "PL", "IE"]

    geo_params = "&".join(f"geo={g}" for g in geos)
    time_params = "&".join(f"time={y}" for y in range(start_year, end_year + 1))

    url = f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{ds['code']}"
    params_str = f"{geo_params}&{time_params}&freq=A&{ds['fixedParams']}&lang=en"

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(f"{url}?{params_str}", headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            data = resp.json()

        values_dict = data.get("value", {})
        dims = data.get("dimension", {})

        geo_idx = dims.get("geo", {}).get("category", {}).get("index", {})
        time_idx = dims.get("time", {}).get("category", {}).get("index", {})
        geo_labels = dims.get("geo", {}).get("category", {}).get("label", {})

        idx_to_geo = {v: k for k, v in geo_idx.items()}
        idx_to_time = {v: k for k, v in time_idx.items()}

        results = []
        for flat_idx, value in values_dict.items():
            try:
                idx = int(flat_idx)
                # For simple case, assume 2D indexing
                geo_pos = idx % len(geo_idx)
                time_pos = idx // len(geo_idx)

                geo_code = next((k for k, v in geo_idx.items() if v == geo_pos), None)
                time_code = next((k for k, v in time_idx.items() if v == time_pos), None)

                if geo_code and time_code:
                    results.append({
                        "country": geo_labels.get(geo_code, geo_code),
                        "countryCode": geo_code,
                        "year": time_code,
                        "value": value,
                    })
            except (ValueError, TypeError):
                pass

        results.sort(key=lambda x: (x["countryCode"], x["year"]))
        return json.dumps({"data": results, "dataset": dataset_key})
    except Exception as e:
        _log.warning(f"Eurostat data fetch failed: {e}")
        return json.dumps({"data": [], "error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# 7. REST COUNTRIES
# ─────────────────────────────────────────────────────────────────────────────

def search_countries(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """REST Countries API — country metadata by ISO code or region."""
    codes = arguments.get("codes", [])
    region = arguments.get("region", "")

    try:
        if codes:
            return _rest_countries_by_code(codes)
        elif region:
            return _rest_countries_by_region(region)
        else:
            return json.dumps({"error": "codes or region required"})
    except Exception as e:
        _log.error(f"REST Countries error: {e}")
        return json.dumps({"error": str(e)})


def _rest_countries_by_code(codes: list[str]) -> str:
    """Get countries by ISO code."""
    url = "https://restcountries.com/v3.1/alpha"
    params = {
        "codes": ",".join(codes),
        "fields": "name,cca2,cca3,region,subregion,population,gini,currencies,languages,borders,capital",
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            data = resp.json()

        countries = []
        for c in data:
            countries.append({
                "name": c.get("name", {}).get("common", "Unknown"),
                "iso2": c.get("cca2", ""),
                "iso3": c.get("cca3", ""),
                "region": c.get("region", ""),
                "subregion": c.get("subregion", ""),
                "population": c.get("population", 0),
                "gini": c.get("gini", {}),
                "currencies": list((c.get("currencies") or {}).keys()),
                "languages": list((c.get("languages") or {}).values()),
                "borders": c.get("borders", []),
                "capital": (c.get("capital") or [None])[0],
            })

        return json.dumps({"countries": countries})
    except Exception as e:
        _log.warning(f"REST Countries by code failed: {e}")
        return json.dumps({"countries": [], "error": str(e)})


def _rest_countries_by_region(region: str) -> str:
    """Get all countries in a region."""
    url = f"https://restcountries.com/v3.1/region/{quote(region)}"
    params = {
        "fields": "name,cca2,cca3,region,subregion,population,gini,currencies,languages,borders,capital",
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            data = resp.json()

        countries = []
        for c in data:
            countries.append({
                "name": c.get("name", {}).get("common", "Unknown"),
                "iso2": c.get("cca2", ""),
                "iso3": c.get("cca3", ""),
                "region": c.get("region", ""),
                "subregion": c.get("subregion", ""),
                "population": c.get("population", 0),
                "gini": c.get("gini", {}),
                "currencies": list((c.get("currencies") or {}).keys()),
                "languages": list((c.get("languages") or {}).values()),
                "borders": c.get("borders", []),
                "capital": (c.get("capital") or [None])[0],
            })

        # Sort by population descending
        countries.sort(key=lambda x: x["population"], reverse=True)
        return json.dumps({"countries": countries, "region": region})
    except Exception as e:
        _log.warning(f"REST Countries by region failed: {e}")
        return json.dumps({"countries": [], "error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# 8. EXCHANGE RATES (Frankfurter API — free, no auth)
# ─────────────────────────────────────────────────────────────────────────────

def search_exchange_rates(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Frankfurter API — ECB exchange rates, 30+ currencies, free, no auth."""
    mode = arguments.get("mode", "latest")

    try:
        if mode == "latest":
            return _fx_latest(arguments)
        elif mode == "timeseries":
            return _fx_timeseries(arguments)
        elif mode == "currencies":
            return _fx_currencies()
        else:
            return json.dumps({"error": f"Unknown mode: {mode}. Use: latest, timeseries, currencies"})
    except Exception as e:
        _log.error(f"Exchange rates error: {e}")
        return json.dumps({"error": str(e)})


def _fx_latest(args: dict[str, Any]) -> str:
    """Get latest exchange rates."""
    base = args.get("base", "USD")
    symbols = args.get("symbols", [])

    url = "https://api.frankfurter.app/latest"
    params: dict[str, str] = {"from": base}
    if symbols:
        params["to"] = ",".join(symbols)

    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
        resp.raise_for_status()
        data = resp.json()

    return json.dumps({
        "base": data.get("base", base),
        "date": data.get("date", ""),
        "rates": data.get("rates", {}),
    })


def _fx_timeseries(args: dict[str, Any]) -> str:
    """Get historical exchange rate series."""
    base = args.get("base", "USD")
    symbols = args.get("symbols", [])
    start_date = args.get("start_date", "2020-01-01")
    end_date = args.get("end_date", "2024-01-01")

    url = f"https://api.frankfurter.app/{start_date}..{end_date}"
    params: dict[str, str] = {"from": base}
    if symbols:
        params["to"] = ",".join(symbols)

    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
        resp.raise_for_status()
        data = resp.json()

    rates = data.get("rates", {})
    # Flatten for analysis
    points = []
    for date_str, rate_map in sorted(rates.items()):
        for currency, rate in rate_map.items():
            points.append({"date": date_str, "currency": currency, "rate": rate})

    return json.dumps({
        "base": data.get("base", base),
        "start_date": data.get("start_date", start_date),
        "end_date": data.get("end_date", end_date),
        "data_points": len(points),
        "data": points[:200],  # Cap at 200 to avoid token explosion
    })


def _fx_currencies() -> str:
    """List available currencies."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.get("https://api.frankfurter.app/currencies", headers={"User-Agent": _USER_AGENT})
        resp.raise_for_status()
        return json.dumps({"currencies": resp.json()})


# ─────────────────────────────────────────────────────────────────────────────
# 9. PATENTS (PatentsView v1 API — free with API key from patentsview.org)
# ─────────────────────────────────────────────────────────────────────────────

def search_patents(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """PatentsView API v1 — US patent data. Requires PATENTSVIEW_API_KEY env var
    (free from https://patentsview.org/apis/purpose)."""
    api_key = _get_key("PATENTSVIEW_API_KEY", "patentsview_api_key")
    query = arguments.get("query", "")
    assignee = arguments.get("assignee", "")
    start_date = arguments.get("start_date", "2018-01-01")
    limit = min(arguments.get("limit", 10), 50)

    if not query and not assignee:
        return json.dumps({"error": "query or assignee required"})

    if not api_key:
        return json.dumps({
            "error": "PATENTSVIEW_API_KEY env var not set. Get a free key at https://patentsview.org/apis/purpose",
            "note": "Patent search unavailable without API key.",
        })

    try:
        # Build PatentsView v1 query (POST-based)
        conditions = []
        if query:
            conditions.append({"_text_any": {"patent_abstract": query}})
        if assignee:
            conditions.append({"_text_any": {"assignees.assignee_organization": assignee}})
        if start_date:
            conditions.append({"_gte": {"patent_date": start_date}})

        q = conditions[0] if len(conditions) == 1 else {"_and": conditions}

        url = "https://search.patentsview.org/api/v1/patent/"
        payload = {
            "q": q,
            "f": [
                "patent_id", "patent_title", "patent_abstract",
                "patent_date", "patent_type",
                "assignees.assignee_organization", "assignees.assignee_country",
            ],
            "o": {"size": limit},
        }

        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(url, json=payload, headers={
                "User-Agent": _USER_AGENT,
                "X-Api-Key": api_key,
                "Content-Type": "application/json",
            })
            resp.raise_for_status()
            data = resp.json()

        patents = []
        for p in data.get("patents", []):
            assignees = p.get("assignees", [{}])
            patents.append({
                "number": p.get("patent_id", ""),
                "title": p.get("patent_title", ""),
                "abstract": (p.get("patent_abstract") or "")[:300],
                "date": p.get("patent_date", ""),
                "type": p.get("patent_type", ""),
                "assignee": assignees[0].get("assignee_organization", "") if assignees else "",
                "country": assignees[0].get("assignee_country", "") if assignees else "",
            })

        return json.dumps({
            "patents": patents,
            "total": data.get("total_patent_count", len(patents)),
            "query": query or assignee,
        })
    except Exception as e:
        _log.warning(f"PatentsView search failed: {e}")
        return json.dumps({"patents": [], "error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# 10. WTO (World Trade Organization Stats — requires WTO_API_KEY)
# ─────────────────────────────────────────────────────────────────────────────

def search_wto(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """WTO Timeseries API — trade statistics, tariffs, services trade.
    Requires WTO_API_KEY env var (free from https://apiportal.wto.org/)."""
    api_key = _get_key("WTO_API_KEY", "wto_api_key")
    if not api_key:
        return json.dumps({"error": "WTO_API_KEY env var not set. Get a free key at https://apiportal.wto.org/"})

    mode = arguments.get("mode", "search")

    try:
        if mode == "search":
            return _wto_search_indicators(arguments, api_key)
        elif mode == "data":
            return _wto_get_data(arguments, api_key)
        else:
            return json.dumps({"error": f"Unknown mode: {mode}"})
    except Exception as e:
        _log.error(f"WTO error: {e}")
        return json.dumps({"error": str(e)})


def _wto_search_indicators(args: dict[str, Any], api_key: str) -> str:
    """Search WTO indicators."""
    query = args.get("query", "")
    limit = min(args.get("limit", 15), 100)

    if not query:
        return json.dumps({"indicators": [], "error": "query required"})

    url = "https://api.wto.org/timeseries/v1/indicators"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, headers={
                "User-Agent": _USER_AGENT,
                "Ocp-Apim-Subscription-Key": api_key,
            })
            resp.raise_for_status()
            data = resp.json()

        q = query.lower()
        matches = []
        for ind in data if isinstance(data, list) else []:
            name = str(ind.get("name", "")).lower()
            desc = str(ind.get("description", "")).lower()
            code = str(ind.get("code", "")).lower()
            if q in name or q in desc or q in code:
                matches.append({
                    "code": ind.get("code", ""),
                    "name": ind.get("name", ""),
                    "description": (ind.get("description") or "")[:200],
                    "categoryCode": ind.get("categoryCode", ""),
                })
                if len(matches) >= limit:
                    break

        return json.dumps({"indicators": matches, "query": query})
    except Exception as e:
        _log.warning(f"WTO search failed: {e}")
        return json.dumps({"indicators": [], "error": str(e)})


def _wto_get_data(args: dict[str, Any], api_key: str) -> str:
    """Get WTO indicator data."""
    indicator = args.get("indicator", "")
    reporters = args.get("reporters", [])
    start_year = args.get("start_year", 2015)
    end_year = args.get("end_year", 2023)

    if not indicator:
        return json.dumps({"data": [], "error": "indicator code required"})

    reporter_param = ",".join(reporters) if reporters else ""
    url = "https://api.wto.org/timeseries/v1/data"
    params: dict[str, Any] = {
        "i": indicator,
        "ps": f"{start_year}-{end_year}",
    }
    if reporter_param:
        params["r"] = reporter_param

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers={
                "User-Agent": _USER_AGENT,
                "Ocp-Apim-Subscription-Key": api_key,
            })
            resp.raise_for_status()
            data = resp.json()

        results = []
        dataset = data.get("Dataset", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        for d in dataset[:100]:
            results.append({
                "reporter": d.get("ReportingEconomyCode", d.get("reportingEconomy", "")),
                "partner": d.get("PartnerEconomyCode", d.get("partnerEconomy", "")),
                "year": d.get("Year", d.get("year", "")),
                "value": d.get("Value", d.get("value")),
                "indicator": d.get("IndicatorCode", indicator),
            })

        return json.dumps({"data": results, "indicator": indicator})
    except Exception as e:
        _log.warning(f"WTO data fetch failed: {e}")
        return json.dumps({"data": [], "error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# 11. TRANSPARENCY INTERNATIONAL (CPI — free, no auth)
# ─────────────────────────────────────────────────────────────────────────────

def search_transparency(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Transparency International CPI — Corruption Perceptions Index, free.
    Returns CPI scores (0-100) by country and year from GitHub dataset."""
    year = arguments.get("year", 2023)
    countries = arguments.get("countries", [])

    try:
        # CPI data is available from TI's public GitHub/API
        url = f"https://images.transparencycdn.org/images/CPI{year}_GlobalResults&Trends.csv"

        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": _USER_AGENT})

            if resp.status_code != 200:
                # Fallback: return curated CPI data for major economies
                return _cpi_fallback(year, countries)

            lines = resp.text.strip().split("\n")

        if len(lines) < 2:
            return _cpi_fallback(year, countries)

        header = [h.strip().strip('"') for h in lines[0].split(",")]
        results = []
        for line in lines[1:]:
            fields = [f.strip().strip('"') for f in line.split(",")]
            if len(fields) < 3:
                continue
            record = dict(zip(header, fields))
            iso3 = record.get("ISO3", record.get("iso3", ""))
            country_name = record.get("Country / Territory", record.get("country", ""))

            if countries and iso3.upper() not in [c.upper() for c in countries]:
                continue

            score_key = next((k for k in record if str(year) in k and "score" in k.lower()), None)
            score = record.get(score_key, record.get("CPI Score", "")) if score_key else record.get("CPI Score", "")

            results.append({
                "country": country_name,
                "iso3": iso3,
                "cpi_score": int(score) if str(score).isdigit() else score,
                "year": year,
            })

        if not results:
            return _cpi_fallback(year, countries)

        results.sort(key=lambda x: x.get("cpi_score", 0) if isinstance(x.get("cpi_score"), (int, float)) else 0, reverse=True)
        return json.dumps({"data": results[:50], "year": year, "source": "Transparency International CPI"})
    except Exception as e:
        _log.warning(f"Transparency CPI fetch failed: {e}")
        return _cpi_fallback(year, countries)


def _cpi_fallback(year: int, countries: list[str]) -> str:
    """Fallback CPI data for major economies (2023 scores)."""
    _CPI_2023 = {
        "DNK": ("Denmark", 90), "FIN": ("Finland", 87), "NZL": ("New Zealand", 85),
        "NOR": ("Norway", 84), "SGP": ("Singapore", 83), "SWE": ("Sweden", 82),
        "CHE": ("Switzerland", 82), "NLD": ("Netherlands", 79), "DEU": ("Germany", 78),
        "IRL": ("Ireland", 77), "GBR": ("United Kingdom", 71), "JPN": ("Japan", 73),
        "USA": ("United States", 69), "FRA": ("France", 71), "KOR": ("South Korea", 63),
        "ESP": ("Spain", 60), "ITA": ("Italy", 56), "SAU": ("Saudi Arabia", 52),
        "CHN": ("China", 42), "IND": ("India", 39), "TUR": ("Turkey", 34),
        "BRA": ("Brazil", 36), "MEX": ("Mexico", 31), "RUS": ("Russia", 26),
        "NGA": ("Nigeria", 25), "ZAF": ("South Africa", 41),
    }
    results = []
    for iso3, (name, score) in _CPI_2023.items():
        if countries and iso3 not in [c.upper() for c in countries]:
            continue
        results.append({"country": name, "iso3": iso3, "cpi_score": score, "year": year, "source": "fallback"})
    results.sort(key=lambda x: x["cpi_score"], reverse=True)
    return json.dumps({"data": results, "year": year, "source": "Transparency International CPI (cached)"})


# ─────────────────────────────────────────────────────────────────────────────
# 12. SEC EDGAR (SEC filings — free, no auth, requires User-Agent)
# ─────────────────────────────────────────────────────────────────────────────

def search_sec_edgar(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """SEC EDGAR full-text search — 10-K, 10-Q, 8-K, DEF 14A filings."""
    query = arguments.get("query", "")
    company = arguments.get("company", "")
    filing_type = arguments.get("filing_type", "")
    start_date = arguments.get("start_date", "2020-01-01")
    limit = min(arguments.get("limit", 10), 50)

    if not query and not company:
        return json.dumps({"error": "query or company required"})

    try:
        url = "https://efts.sec.gov/LATEST/search-index"
        params: dict[str, Any] = {
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": "2024-12-31",
        }
        if query:
            params["q"] = query
        if company:
            params["q"] = f'"{company}"' if not query else f'{query} "{company}"'
        if filing_type:
            params["forms"] = filing_type

        # SEC requires identifying User-Agent
        headers = {"User-Agent": "ARA-Research/1.0 (research@ara.ai)"}

        # Use EDGAR full-text search
        search_url = "https://efts.sec.gov/LATEST/search-index"
        params2 = {
            "q": params.get("q", query),
            "dateRange": "custom",
            "startdt": start_date,
        }
        if filing_type:
            params2["forms"] = filing_type

        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                "https://efts.sec.gov/LATEST/search-index",
                params=params2,
                headers=headers,
            )

            if resp.status_code != 200:
                # Fallback to company search
                return _edgar_company_search(company or query, headers)

            data = resp.json()

        filings = []
        for hit in data.get("hits", {}).get("hits", [])[:limit]:
            src = hit.get("_source", {})
            filings.append({
                "company": src.get("display_names", [""])[0] if src.get("display_names") else "",
                "filing_type": src.get("form_type", ""),
                "date": src.get("file_date", ""),
                "description": (src.get("display_date_filed", "") + " " + src.get("form_type", "")).strip(),
                "url": f"https://www.sec.gov/Archives/{src.get('file_name', '')}",
            })

        return json.dumps({"filings": filings, "total": data.get("hits", {}).get("total", {}).get("value", 0)})
    except Exception as e:
        _log.warning(f"SEC EDGAR search failed: {e}")
        return _edgar_company_search(company or query, {"User-Agent": "ARA-Research/1.0 (research@ara.ai)"})


def _edgar_company_search(query: str, headers: dict[str, str]) -> str:
    """Fallback: search companies by name via EDGAR EFTS full-text search."""
    try:
        url = "https://efts.sec.gov/LATEST/search-index"
        params = {"q": query, "dateRange": "custom", "startdt": "2020-01-01"}

        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers=headers)

            if resp.status_code != 200:
                # Second fallback — just return a useful link
                return json.dumps({
                    "filings": [],
                    "note": f"EDGAR search for '{query}'. Use the EDGAR website for detailed filings.",
                    "url": f"https://efts.sec.gov/LATEST/search-index?q={quote(query)}",
                })

            data = resp.json()

        filings = []
        for hit in data.get("hits", {}).get("hits", [])[:10]:
            src = hit.get("_source", {})
            filings.append({
                "company": (src.get("display_names") or [""])[0],
                "filing_type": src.get("form_type", ""),
                "date": src.get("file_date", ""),
                "url": f"https://www.sec.gov/Archives/{src.get('file_name', '')}",
            })

        return json.dumps({
            "filings": filings,
            "total": data.get("hits", {}).get("total", {}).get("value", 0),
        })
    except Exception as e:
        return json.dumps({"filings": [], "error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# 13. UN SDG (Sustainable Development Goals — free, no auth)
# ─────────────────────────────────────────────────────────────────────────────

def search_un_sdg(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """UN SDG API — Sustainable Development Goals indicators and data."""
    mode = arguments.get("mode", "search")

    try:
        if mode == "search":
            return _sdg_search(arguments)
        elif mode == "data":
            return _sdg_get_data(arguments)
        elif mode == "goals":
            return _sdg_list_goals()
        else:
            return json.dumps({"error": f"Unknown mode: {mode}"})
    except Exception as e:
        _log.error(f"UN SDG error: {e}")
        return json.dumps({"error": str(e)})


def _sdg_list_goals() -> str:
    """List all 17 SDGs."""
    goals = [
        {"id": 1, "name": "No Poverty"}, {"id": 2, "name": "Zero Hunger"},
        {"id": 3, "name": "Good Health and Well-being"}, {"id": 4, "name": "Quality Education"},
        {"id": 5, "name": "Gender Equality"}, {"id": 6, "name": "Clean Water and Sanitation"},
        {"id": 7, "name": "Affordable and Clean Energy"}, {"id": 8, "name": "Decent Work and Economic Growth"},
        {"id": 9, "name": "Industry, Innovation and Infrastructure"}, {"id": 10, "name": "Reduced Inequalities"},
        {"id": 11, "name": "Sustainable Cities and Communities"}, {"id": 12, "name": "Responsible Consumption and Production"},
        {"id": 13, "name": "Climate Action"}, {"id": 14, "name": "Life Below Water"},
        {"id": 15, "name": "Life on Land"}, {"id": 16, "name": "Peace, Justice and Strong Institutions"},
        {"id": 17, "name": "Partnerships for the Goals"},
    ]
    return json.dumps({"goals": goals})


def _sdg_search(args: dict[str, Any]) -> str:
    """Search SDG indicators."""
    query = args.get("query", "")
    goal = args.get("goal")

    url = "https://unstats.un.org/sdgapi/v1/sdg/Indicator/List"
    params: dict[str, Any] = {}
    if goal:
        url = f"https://unstats.un.org/sdgapi/v1/sdg/Goal/{goal}/Target/List?includechildren=true"

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            data = resp.json()

        indicators = []
        q = query.lower() if query else ""
        for ind in data if isinstance(data, list) else []:
            desc = str(ind.get("description", "")).lower()
            code = str(ind.get("code", "")).lower()
            if not q or q in desc or q in code:
                indicators.append({
                    "code": ind.get("code", ""),
                    "description": ind.get("description", ""),
                    "goal": ind.get("goal", ""),
                    "target": ind.get("target", ""),
                })
                if len(indicators) >= 15:
                    break

        return json.dumps({"indicators": indicators, "query": query})
    except Exception as e:
        _log.warning(f"UN SDG search failed: {e}")
        return json.dumps({"indicators": [], "error": str(e)})


def _sdg_get_data(args: dict[str, Any]) -> str:
    """Get SDG indicator data."""
    indicator = args.get("indicator", "")
    countries = args.get("countries", [])
    start_year = args.get("start_year", 2015)
    end_year = args.get("end_year", 2023)

    if not indicator:
        return json.dumps({"data": [], "error": "indicator code required"})

    url = f"https://unstats.un.org/sdgapi/v1/sdg/Indicator/Data"
    params: dict[str, Any] = {
        "indicator": indicator,
        "timePeriodStart": start_year,
        "timePeriodEnd": end_year,
        "pageSize": 100,
    }
    if countries:
        params["areaCode"] = ",".join(countries)

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            data = resp.json()

        results = []
        for d in data.get("data", []) if isinstance(data, dict) else []:
            results.append({
                "country": d.get("geoAreaName", ""),
                "countryCode": d.get("geoAreaCode", ""),
                "year": d.get("timePeriodStart", ""),
                "value": d.get("value"),
                "indicator": indicator,
            })

        return json.dumps({"data": results, "indicator": indicator})
    except Exception as e:
        _log.warning(f"UN SDG data fetch failed: {e}")
        return json.dumps({"data": [], "error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# 15. WHO GHO (Global Health Observatory — free, no auth)
# ─────────────────────────────────────────────────────────────────────────────

def search_who(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """WHO GHO API — Global Health Observatory indicators and data."""
    mode = arguments.get("mode", "search")

    try:
        if mode == "search":
            return _who_search(arguments)
        elif mode == "data":
            return _who_get_data(arguments)
        else:
            return json.dumps({"error": f"Unknown mode: {mode}"})
    except Exception as e:
        _log.error(f"WHO error: {e}")
        return json.dumps({"error": str(e)})


def _who_search(args: dict[str, Any]) -> str:
    """Search WHO GHO indicators."""
    query = args.get("query", "")
    limit = min(args.get("limit", 15), 100)

    if not query:
        return json.dumps({"indicators": [], "error": "query required"})

    url = "https://ghoapi.azureedge.net/api/Indicator"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            data = resp.json()

        q = query.lower()
        matches = []
        for ind in data.get("value", []):
            name = str(ind.get("IndicatorName", "")).lower()
            code = str(ind.get("IndicatorCode", "")).lower()
            if q in name or q in code:
                matches.append({
                    "code": ind.get("IndicatorCode", ""),
                    "name": ind.get("IndicatorName", ""),
                    "language": ind.get("Language", ""),
                })
                if len(matches) >= limit:
                    break

        return json.dumps({"indicators": matches, "query": query})
    except Exception as e:
        _log.warning(f"WHO search failed: {e}")
        return json.dumps({"indicators": [], "error": str(e)})


def _who_get_data(args: dict[str, Any]) -> str:
    """Get WHO GHO indicator data."""
    indicator = args.get("indicator", "")
    countries = args.get("countries", [])

    if not indicator:
        return json.dumps({"data": [], "error": "indicator code required"})

    url = f"https://ghoapi.azureedge.net/api/{quote(indicator)}"
    filters = []
    if countries:
        country_filter = " or ".join(f"SpatialDim eq '{c}'" for c in countries)
        filters.append(f"({country_filter})")

    params = {}
    if filters:
        params["$filter"] = " and ".join(filters)

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            data = resp.json()

        results = []
        for d in data.get("value", [])[:100]:
            results.append({
                "country": d.get("SpatialDim", ""),
                "year": d.get("TimeDim", ""),
                "value": d.get("NumericValue"),
                "dim1": d.get("Dim1", ""),
                "indicator": indicator,
            })

        return json.dumps({"data": results, "indicator": indicator})
    except Exception as e:
        _log.warning(f"WHO data fetch failed: {e}")
        return json.dumps({"data": [], "error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# 16. ILO (International Labour Organization — free, no auth)
# ─────────────────────────────────────────────────────────────────────────────

def search_ilo(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """ILO STAT API — labour statistics (employment, wages, working conditions)."""
    mode = arguments.get("mode", "search")

    try:
        if mode == "search":
            return _ilo_search(arguments)
        elif mode == "data":
            return _ilo_get_data(arguments)
        else:
            return json.dumps({"error": f"Unknown mode: {mode}"})
    except Exception as e:
        _log.error(f"ILO error: {e}")
        return json.dumps({"error": str(e)})


def _ilo_search(args: dict[str, Any]) -> str:
    """Search ILO indicator collections."""
    query = args.get("query", "")

    if not query:
        return json.dumps({"indicators": [], "error": "query required"})

    url = "https://sdmx.ilo.org/rest/dataflow/ILO"
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(
                url,
                headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()

        # ILO SDMX v2 format uses "references" dict
        refs = data.get("references", {})
        if not refs:
            # Try v1 format
            flows = data.get("data", {}).get("dataflows", [])
            refs = {f.get("id", ""): f for f in flows}

        q = query.lower()
        matches = []
        for key, f in refs.items():
            name = f.get("name", "") if isinstance(f.get("name"), str) else (f.get("name") or {}).get("en", str(f.get("name", "")))
            fid = f.get("id", key)
            if q in str(name).lower() or q in fid.lower():
                matches.append({
                    "id": fid,
                    "name": name if isinstance(name, str) else str(name),
                })
                if len(matches) >= 15:
                    break

        return json.dumps({"dataflows": matches, "query": query})
    except Exception as e:
        _log.warning(f"ILO search failed: {e}")
        return json.dumps({"dataflows": [], "error": str(e)})


def _ilo_get_data(args: dict[str, Any]) -> str:
    """Get ILO data."""
    dataflow = args.get("dataflow", "")
    countries = args.get("countries", [])
    start_year = args.get("start_year", 2015)
    end_year = args.get("end_year", 2023)

    if not dataflow:
        return json.dumps({"data": [], "error": "dataflow ID required"})

    country_filter = "+".join(countries) if countries else ""
    key = country_filter if country_filter else "."

    url = f"https://sdmx.ilo.org/rest/data/ILO,{dataflow},./{key}"
    params = {
        "startPeriod": str(start_year),
        "endPeriod": str(end_year),
        "dimensionAtObservation": "AllDimensions",
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                url,
                params=params,
                headers={"Accept": "application/vnd.sdmx.data+json", "User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()

        datasets = data.get("data", {}).get("dataSets", [])
        if not datasets:
            return json.dumps({"data": [], "dataflow": dataflow})

        obs = datasets[0].get("observations", {})
        return json.dumps({
            "data_points": len(obs),
            "dataflow": dataflow,
            "note": f"Retrieved {len(obs)} observations. Use SDMX structure for dimension mapping.",
        })
    except Exception as e:
        _log.warning(f"ILO data fetch failed: {e}")
        return json.dumps({"data": [], "error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# 17. AIR QUALITY (OpenAQ — free, no auth)
# ─────────────────────────────────────────────────────────────────────────────

def search_air_quality(arguments: dict[str, Any], ctx: dict[str, Any]) -> str:
    """OpenAQ API v3 — global air quality data (PM2.5, PM10, NO2, O3, SO2, CO).
    Requires OPENAQ_API_KEY env var (free from https://explore.openaq.org/)."""
    api_key = _get_key("OPENAQ_API_KEY", "openaq_api_key")
    if not api_key:
        return json.dumps({"error": "OPENAQ_API_KEY env var not set. Get a free key at https://explore.openaq.org/"})

    mode = arguments.get("mode", "latest")

    try:
        if mode == "latest":
            return _openaq_latest(arguments, api_key)
        elif mode == "countries":
            return _openaq_countries(api_key)
        else:
            return json.dumps({"error": f"Unknown mode: {mode}"})
    except Exception as e:
        _log.error(f"OpenAQ error: {e}")
        return json.dumps({"error": str(e)})


def _openaq_resolve_country(code: str, api_key: str) -> int | None:
    """Resolve ISO-2 country code to OpenAQ country ID."""
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get("https://api.openaq.org/v3/countries", params={"limit": 200},
                              headers={"X-API-Key": api_key})
            resp.raise_for_status()
            for c in resp.json().get("results", []):
                if c.get("code", "").upper() == code.upper():
                    return c["id"]
    except Exception:
        pass
    return None


def _openaq_latest(args: dict[str, Any], api_key: str) -> str:
    """Get latest air quality measurements via OpenAQ v3 API."""
    country = args.get("country", "")
    parameter = args.get("parameter", "pm25")
    limit = min(args.get("limit", 20), 100)

    # Map common parameter names to v3 parameter IDs
    _PARAM_MAP = {"pm25": 2, "pm10": 1, "o3": 3, "no2": 5, "so2": 8, "co": 7}
    param_id = _PARAM_MAP.get(parameter)

    url = "https://api.openaq.org/v3/locations"
    params: dict[str, Any] = {"limit": limit}
    if country:
        cid = _openaq_resolve_country(country, api_key)
        if cid:
            params["countries_id"] = cid
    if param_id:
        params["parameters_id"] = param_id

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers={
                "User-Agent": _USER_AGENT,
                "X-API-Key": api_key,
            })
            resp.raise_for_status()
            data = resp.json()

        results = []
        for loc in data.get("results", []):
            for sensor in loc.get("sensors", []):
                param_info = sensor.get("parameter", {})
                summary = sensor.get("summary", {})
                results.append({
                    "location": loc.get("name", ""),
                    "city": loc.get("locality", ""),
                    "country": (loc.get("country") or {}).get("code", ""),
                    "parameter": param_info.get("name", parameter),
                    "value": summary.get("avg"),
                    "min": summary.get("min"),
                    "max": summary.get("max"),
                    "unit": param_info.get("units", ""),
                    "lastUpdated": (loc.get("datetimeLast") or {}).get("utc", ""),
                })

        return json.dumps({"measurements": results, "parameter": parameter})
    except Exception as e:
        _log.warning(f"OpenAQ latest failed: {e}")
        return json.dumps({"measurements": [], "error": str(e)})


def _openaq_countries(api_key: str) -> str:
    """List countries with air quality data via OpenAQ v3."""
    url = "https://api.openaq.org/v3/countries"
    params: dict[str, Any] = {"limit": 200}

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params, headers={
                "User-Agent": _USER_AGENT,
                "X-API-Key": api_key,
            })
            resp.raise_for_status()
            data = resp.json()

        countries = []
        for c in data.get("results", [])[:50]:
            countries.append({
                "code": c.get("code", ""),
                "name": c.get("name", ""),
                "locations": c.get("locationsCount", 0),
            })

        return json.dumps({"countries": countries})
    except Exception as e:
        _log.warning(f"OpenAQ countries failed: {e}")
        return json.dumps({"countries": [], "error": str(e)})
