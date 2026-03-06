# Location: ara/tools/http.py
# Purpose: Rate-limited HTTP client with per-domain throttling and exponential backoff
# Functions: rate_limited_get, rate_limited_head
# Calls: httpx
# Imports: httpx, time, threading, logging, collections

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Any

import httpx

_log = logging.getLogger(__name__)

# Per-domain rate limits (requests per minute)
_DOMAIN_LIMITS: dict[str, int] = {
    "api.semanticscholar.org": 10,   # 1 req/6s — S2 is the tightest
    "api.crossref.org": 30,
    "api.openalex.org": 60,
    "api.unpaywall.org": 30,
    "doi.org": 30,
    "eutils.ncbi.nlm.nih.gov": 10,   # NCBI: 10/s with key, 3/s without
    "core.ac.uk": 20,
    "dblp.org": 20,
    "www.ebi.ac.uk": 20,
}
_DEFAULT_RPM = 30  # Default for unknown domains

# Track last request time per domain
_domain_timestamps: dict[str, list[float]] = defaultdict(list)
_domain_lock = threading.Lock()

# Shared client
_client: httpx.Client | None = None
_client_lock = threading.Lock()

_MAX_RETRIES = 5
_TIMEOUT = 30


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=_TIMEOUT,
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                )
    return _client


def _extract_domain(url: str) -> str:
    """Extract domain from URL for rate limiting."""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc
    except Exception:
        return "unknown"


def _wait_for_slot(domain: str) -> None:
    """Block until we have a rate limit slot for this domain."""
    rpm = _DOMAIN_LIMITS.get(domain, _DEFAULT_RPM)
    min_interval = 60.0 / rpm  # Minimum seconds between requests

    with _domain_lock:
        now = time.monotonic()
        timestamps = _domain_timestamps[domain]

        # Prune timestamps older than 60 seconds
        cutoff = now - 60.0
        _domain_timestamps[domain] = [t for t in timestamps if t > cutoff]
        timestamps = _domain_timestamps[domain]

        if timestamps:
            # Enforce minimum interval since last request
            elapsed = now - timestamps[-1]
            if elapsed < min_interval:
                wait = min_interval - elapsed
                _log.debug("Rate limit: waiting %.1fs for %s", wait, domain)
                time.sleep(wait)

            # If we've hit the RPM limit, wait until the oldest one expires
            if len(timestamps) >= rpm:
                wait = timestamps[0] - cutoff
                if wait > 0:
                    _log.debug("Rate limit: RPM cap, waiting %.1fs for %s", wait, domain)
                    time.sleep(wait)

        _domain_timestamps[domain].append(time.monotonic())


def rate_limited_get(
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = _TIMEOUT,
) -> httpx.Response:
    """HTTP GET with per-domain rate limiting and exponential backoff on 429."""
    domain = _extract_domain(url)
    client = _get_client()

    for attempt in range(_MAX_RETRIES):
        _wait_for_slot(domain)
        try:
            resp = client.get(url, headers=headers, params=params, timeout=timeout)

            if resp.status_code == 429:
                # Parse Retry-After header if present
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = min(float(retry_after), 120)
                    except ValueError:
                        wait = min(10 * (2 ** attempt), 120)
                else:
                    wait = min(10 * (2 ** attempt), 120)
                _log.warning("429 from %s, backing off %ds (attempt %d/%d)", domain, int(wait), attempt + 1, _MAX_RETRIES)
                time.sleep(wait)
                continue

            if resp.status_code == 503:
                wait = min(5 * (2 ** attempt), 60)
                _log.warning("503 from %s, backing off %ds", domain, int(wait))
                time.sleep(wait)
                continue

            return resp

        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            wait = min(5 * (2 ** attempt), 60)
            _log.warning("Connection error on %s (attempt %d): %s, retrying in %ds", domain, attempt + 1, exc, int(wait))
            time.sleep(wait)
        except Exception as exc:
            _log.warning("HTTP error on %s (attempt %d): %s", domain, attempt + 1, exc)
            if attempt < _MAX_RETRIES - 1:
                time.sleep(3 * (attempt + 1))
            else:
                raise

    # All retries exhausted — return a synthetic 429 response
    _log.error("All %d retries exhausted for %s", _MAX_RETRIES, url[:100])
    return httpx.Response(status_code=429, text="Rate limit retries exhausted")


def rate_limited_head(
    url: str,
    timeout: int = _TIMEOUT,
) -> httpx.Response:
    """HTTP HEAD with per-domain rate limiting and exponential backoff."""
    domain = _extract_domain(url)
    client = _get_client()

    for attempt in range(_MAX_RETRIES):
        _wait_for_slot(domain)
        try:
            resp = client.head(url, timeout=timeout, follow_redirects=True)
            if resp.status_code == 429:
                wait = min(10 * (2 ** attempt), 120)
                _log.warning("429 HEAD from %s, backing off %ds", domain, int(wait))
                time.sleep(wait)
                continue
            return resp
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            wait = min(5 * (2 ** attempt), 60)
            _log.warning("Connection error HEAD on %s (attempt %d): %s", domain, attempt + 1, exc)
            time.sleep(wait)
        except Exception as exc:
            _log.warning("HTTP HEAD error on %s (attempt %d): %s", domain, attempt + 1, exc)
            if attempt < _MAX_RETRIES - 1:
                time.sleep(3 * (attempt + 1))
            else:
                raise

    return httpx.Response(status_code=429, text="Rate limit retries exhausted")
