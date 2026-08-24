"""Nominatim (OpenStreetMap) geocoding adapter.

Isolated in its own module — per the constitution's Data & External Integration
Standards, calls to public APIs must be isolated behind a dedicated adapter so the
external dependency can be mocked, rate-limited, or swapped without touching
orchestration logic (research.md §9).
"""
from __future__ import annotations

import re
import time
from typing import Optional

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "uber-route-coffee-agent/1.0 (SPECS-001 demo)"
_MIN_INTERVAL_SECONDS = 1.0  # Nominatim usage policy: max 1 request/second, keyless

# Brazilian addresses commonly abbreviate "número" as "nº"/"n°"/"n.º" before a house
# or km number (e.g. "Rodovia BR 101 nº km 211"). Nominatim's tokenizer doesn't
# recognize this abbreviation and fails to resolve the whole address as a result —
# confirmed live: the same address resolves correctly with "nº " stripped, and fails
# (returns no results) with it present. Strip it before querying.
_NUMERO_ABBREVIATION = re.compile(r"\bn\.?[°º]\.?\s*", re.IGNORECASE)

_last_call_at = 0.0


def geocode(location_text: str, timeout: float = 5.0) -> Optional[tuple[float, float]]:
    """Resolve free-text location to (lat, lng); returns None if it can't be resolved.

    Callers (BpRouteOrchestrator) must treat None as "location not found" and map it
    to the 422 `location_not_found` contract response (FR-011).
    """
    global _last_call_at
    if not location_text or not location_text.strip():
        return None

    query_text = _NUMERO_ABBREVIATION.sub("", location_text)

    elapsed = time.monotonic() - _last_call_at
    if elapsed < _MIN_INTERVAL_SECONDS:
        time.sleep(_MIN_INTERVAL_SECONDS - elapsed)

    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": query_text, "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        _last_call_at = time.monotonic()
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, ValueError):
        return None

    if not results:
        return None

    try:
        return float(results[0]["lat"]), float(results[0]["lon"])
    except (KeyError, ValueError, TypeError):
        return None
