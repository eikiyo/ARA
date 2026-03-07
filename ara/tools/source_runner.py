# Location: ara/tools/source_runner.py
# Purpose: Generic source runner — executes searches against any source defined in sources.json
# Functions: SourceRegistry, SourceRunner, run_all_registry_sources
# Calls: sources.json, tools/http.py
# Imports: json, logging, os, pathlib, time, re

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_SOURCES_FILE = Path(__file__).parent / "sources.json"


class SourceRegistry:
    """Load and manage source definitions from sources.json."""

    def __init__(self, path: Path | None = None):
        self._path = path or _SOURCES_FILE
        self._sources: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, "r") as f:
                raw = json.load(f)
            self._sources = {k: v for k, v in raw.items() if not k.startswith("_")}
            _log.info("SourceRegistry: loaded %d sources from %s", len(self._sources), self._path)
        except Exception as exc:
            _log.error("SourceRegistry: failed to load %s: %s", self._path, exc)

    def enabled_for(self, phase: str) -> list[tuple[str, dict]]:
        """Return sources enabled for a given phase, sorted by priority."""
        results = []
        for key, src in self._sources.items():
            if not src.get("enabled", False):
                continue
            if phase in src.get("phases", []):
                results.append((key, src))
        results.sort(key=lambda x: x[1].get("priority", 99))
        return results

    def get(self, key: str) -> dict | None:
        return self._sources.get(key)

    def all_enabled(self) -> list[tuple[str, dict]]:
        return [(k, v) for k, v in self._sources.items() if v.get("enabled", False)]


def _resolve_field(obj: Any, path: str) -> Any:
    """Resolve a dot-path field from a nested dict/list.

    Supports:
      - "field.subfield" — nested access
      - "field[0]" — list index
      - "field[].name" — collect name from each item in list
      - "field[:4]" — string slice
      - "field[?type=doi].id" — filter list by key=value, extract field
    """
    if not path or obj is None:
        return None

    parts = re.split(r'\.(?![^\[]*\])', path)  # split on dots not inside brackets
    current = obj

    for part in parts:
        if current is None:
            return None

        # Handle [?key=val].field  — filter
        filter_match = re.match(r'(\w+)\[\?(\w+)=(\w+)\]\.(\w+)', part)
        if filter_match:
            field, fkey, fval, extract = filter_match.groups()
            current = current.get(field) if isinstance(current, dict) else current
            if isinstance(current, list):
                for item in current:
                    if isinstance(item, dict) and str(item.get(fkey)) == fval:
                        current = item.get(extract)
                        break
                else:
                    current = None
            continue

        # Handle field[] — collect from list
        if part.endswith('[]'):
            field = part[:-2]
            current = current.get(field) if isinstance(current, dict) else current
            # If next part exists, it will collect from items
            if isinstance(current, list):
                continue
            return current

        # Handle field[].subfield — collect subfield from each list item
        collect_match = re.match(r'(\w+)\[\]', part)
        if collect_match:
            field = collect_match.group(1)
            current = current.get(field) if isinstance(current, dict) else current
            continue

        # Handle field[0] — index access
        idx_match = re.match(r'(\w+)\[(\d+)\]', part)
        if idx_match:
            field, idx = idx_match.group(1), int(idx_match.group(2))
            current = current.get(field) if isinstance(current, dict) else current
            if isinstance(current, list) and len(current) > idx:
                current = current[idx]
            else:
                current = None
            continue

        # Handle [:4] — string slice
        slice_match = re.match(r'(\w+)\[:(\d+)\]', part)
        if slice_match:
            field, end = slice_match.group(1), int(slice_match.group(2))
            current = current.get(field) if isinstance(current, dict) else current
            if isinstance(current, str):
                current = current[:end]
            continue

        # Simple field access
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            # Collect field from each dict in list
            current = [item.get(part) for item in current if isinstance(item, dict)]
            # Flatten single values
            current = [v for v in current if v is not None]
        else:
            return None

    return current


def _extract_paper(item: dict, field_map: dict) -> dict:
    """Extract paper fields from a raw API response item using field_map."""
    paper: dict[str, Any] = {}

    title = _resolve_field(item, field_map.get("title"))
    if not title:
        return {}
    if isinstance(title, list):
        title = title[0] if title else ""
    paper["title"] = str(title).strip()

    abstract = _resolve_field(item, field_map.get("abstract"))
    if isinstance(abstract, list):
        abstract = abstract[0] if abstract else ""
    paper["abstract"] = str(abstract).strip() if abstract else ""

    authors_raw = _resolve_field(item, field_map.get("authors"))
    if isinstance(authors_raw, list):
        paper["authors"] = ", ".join(str(a) for a in authors_raw[:20])
    elif isinstance(authors_raw, str):
        paper["authors"] = authors_raw
    else:
        paper["authors"] = ""

    year_raw = _resolve_field(item, field_map.get("year"))
    if year_raw:
        try:
            paper["year"] = int(str(year_raw)[:4])
        except (ValueError, TypeError):
            paper["year"] = None
    else:
        paper["year"] = None

    doi = _resolve_field(item, field_map.get("doi"))
    if isinstance(doi, list):
        doi = doi[0] if doi else None
    if doi:
        doi = str(doi).strip()
        # Normalize DOI — strip URL prefix
        if doi.startswith("https://doi.org/"):
            doi = doi[16:]
        elif doi.startswith("http://doi.org/"):
            doi = doi[15:]
    paper["doi"] = doi

    url = _resolve_field(item, field_map.get("url"))
    if isinstance(url, list):
        url = url[0] if url else None
    paper["url"] = str(url) if url else None

    return paper


class SourceRunner:
    """Execute a search against a single source defined in the registry."""

    def __init__(self, key: str, config: dict):
        self.key = key
        self.config = config
        self.name = config.get("name", key)
        self._last_call = 0.0
        self._rpm = config.get("rate_limit_rpm", 60)

    def _rate_wait(self) -> None:
        """Wait if needed to respect rate limit."""
        if self._rpm <= 0:
            return
        min_interval = 60.0 / self._rpm
        elapsed = time.time() - self._last_call
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_call = time.time()

    def _get_auth(self) -> str | None:
        env_var = self.config.get("auth_env")
        if not env_var:
            return None
        return os.getenv(env_var)

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Run a search and return normalized paper dicts."""
        from .http import rate_limited_get

        src = self.config
        src_type = src.get("type", "rest_get")
        url_mode = src.get("url_mode", "query")
        base_url = src["base_url"]
        field_map = src.get("field_map", {})
        response_path = src.get("response_path", "")

        self._rate_wait()

        try:
            if src_type == "rest_get":
                return self._search_get(query, limit, base_url, url_mode, src, field_map, response_path)
            elif src_type == "rest_post":
                return self._search_post(query, limit, base_url, src, field_map, response_path)
            else:
                _log.warning("Source %s: unsupported type %s", self.key, src_type)
                return []
        except Exception as exc:
            _log.warning("Source %s search failed: %s", self.key, exc)
            return []

    def _search_get(
        self, query: str, limit: int, base_url: str, url_mode: str,
        src: dict, field_map: dict, response_path: str,
    ) -> list[dict]:
        from .http import rate_limited_get

        params = {}
        for k, v in src.get("params", {}).items():
            if isinstance(v, str):
                v = v.replace("{limit}", str(limit)).replace("{query}", query)
                auth = self._get_auth()
                if auth:
                    v = v.replace("{auth}", auth)
            params[k] = v

        headers = dict(src.get("headers", {}))
        auth = self._get_auth()
        if auth:
            headers = {k: v.replace("{auth}", auth) for k, v in headers.items()}

        # Build URL
        if url_mode == "path":
            url = base_url.replace("{query}", query)
        elif url_mode == "path_doi":
            # Used for DOI lookups — caller handles
            return []
        elif url_mode == "custom_biorxiv":
            return self._search_biorxiv(query, limit, base_url, field_map, response_path)
        else:
            url = base_url
            qp = src.get("query_param", "q")
            if qp:
                params[qp] = query

        resp = rate_limited_get(url, params=params, headers=headers or None, timeout=15, max_retries=1)
        if resp is None:
            return []

        data = resp.json()
        items = self._extract_response(data, response_path)
        return self._normalize_items(items, field_map, limit)

    def _search_post(
        self, query: str, limit: int, base_url: str,
        src: dict, field_map: dict, response_path: str,
    ) -> list[dict]:
        import requests

        body = json.loads(json.dumps(src.get("post_body", {})))
        body = self._replace_placeholders(body, query, limit)

        headers = dict(src.get("headers", {}))
        auth = self._get_auth()
        if auth:
            headers = {k: v.replace("{auth}", auth) for k, v in headers.items()}

        resp = requests.post(base_url, json=body, headers=headers, timeout=15)
        resp.raise_for_status()

        data = resp.json()
        items = self._extract_response(data, response_path)
        return self._normalize_items(items, field_map, limit)

    def _search_biorxiv(
        self, query: str, limit: int, base_url: str,
        field_map: dict, response_path: str,
    ) -> list[dict]:
        """bioRxiv/medRxiv uses date-range URL, not query params. We search recent 30 days."""
        from .http import rate_limited_get
        from datetime import datetime, timedelta

        end = datetime.now()
        start = end - timedelta(days=90)
        url = f"{base_url}/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"

        resp = rate_limited_get(url, timeout=15, max_retries=1)
        if resp is None:
            return []

        data = resp.json()
        items = self._extract_response(data, response_path)

        # Filter by query keywords since bioRxiv doesn't support search
        query_terms = [t.strip().lower().strip('"') for t in query.split() if len(t) > 3]
        filtered = []
        for item in items:
            title = str(item.get("title", "")).lower()
            abstract = str(item.get("abstract", "")).lower()
            text = title + " " + abstract
            if any(term in text for term in query_terms):
                filtered.append(item)

        return self._normalize_items(filtered, field_map, limit)

    def _extract_response(self, data: Any, response_path: str) -> list:
        """Navigate response_path to get the list of items."""
        if not response_path:
            return data if isinstance(data, list) else []

        current = data
        for key in response_path.split("."):
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return []
        return current if isinstance(current, list) else []

    def _normalize_items(self, items: list, field_map: dict, limit: int) -> list[dict]:
        """Extract and normalize papers from raw items."""
        papers = []
        for item in items[:limit * 2]:  # over-fetch in case some are empty
            paper = _extract_paper(item, field_map)
            if paper and paper.get("title"):
                paper["source"] = self.key
                papers.append(paper)
            if len(papers) >= limit:
                break
        return papers

    def _replace_placeholders(self, obj: Any, query: str, limit: int) -> Any:
        """Recursively replace {query} and {limit} in a dict/list."""
        if isinstance(obj, str):
            obj = obj.replace("{query}", query).replace("{limit}", str(limit))
            auth = self._get_auth()
            if auth:
                obj = obj.replace("{auth}", auth)
            return obj
        elif isinstance(obj, dict):
            return {k: self._replace_placeholders(v, query, limit) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._replace_placeholders(v, query, limit) for v in obj]
        return obj


# ── Global registry singleton ──
_registry: SourceRegistry | None = None


def get_registry() -> SourceRegistry:
    global _registry
    if _registry is None:
        _registry = SourceRegistry()
    return _registry


def run_registry_sources(query: str, limit: int = 20, phase: str = "search") -> list[dict]:
    """Run all enabled registry sources for a given phase, return combined papers."""
    registry = get_registry()
    sources = registry.enabled_for(phase)

    if not sources:
        return []

    all_papers: list[dict] = []
    seen_titles: set[str] = set()

    for key, config in sources:
        try:
            runner = SourceRunner(key, config)
            papers = runner.search(query, limit=limit)
            # Deduplicate by title
            for p in papers:
                title_key = p.get("title", "").lower().strip()[:80]
                if title_key and title_key not in seen_titles:
                    seen_titles.add(title_key)
                    all_papers.append(p)
            if papers:
                _log.info("Registry source %s: found %d papers", key, len(papers))
        except Exception as exc:
            _log.warning("Registry source %s failed: %s", key, exc)

    _log.info("Registry sources total: %d unique papers from %d sources", len(all_papers), len(sources))
    return all_papers
