"""Overpass API (OpenStreetMap) adapter — real, live nearby cafes/bakeries/restaurants/
coworking spaces for any coordinate, replacing a fixed seed dataset that only covered
São Paulo (research.md §22).

Isolated in its own module — per the constitution's Data & External Integration
Standards, calls to public APIs must be isolated behind a dedicated adapter so the
external dependency can be mocked, rate-limited, or swapped without touching
orchestration logic (same pattern as geocoding_adapter.py, research.md §9).

Free, keyless, same OpenStreetMap data source already used for geocoding (Nominatim) —
deliberately not the Google Places API, which needs a paid, billed API key this project
has no account for.
"""
from __future__ import annotations

import time
from typing import Optional

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "uber-route-coffee-agent/1.0 (SPECS-001 demo)"
_MIN_INTERVAL_SECONDS = 1.0  # be a considerate keyless/free-tier caller, mirrors geocoding_adapter

# OSM tag values that map to "somewhere comfortable to wait" categories.
_AMENITY_CATEGORIES = {
    "cafe": "cafe",
    "restaurant": "restaurant",
    "fast_food": "restaurant",
    "bar": "cafe",
}
_SHOP_CATEGORIES = {
    "bakery": "bakery",
    "coffee": "cafe",
}
_OFFICE_CATEGORIES = {
    "coworking": "coworking",
}

_last_call_at = 0.0


def warm_up(timeout: float = 4.0) -> None:
    """Best-effort pre-warm of the outbound HTTPS connection to Overpass. The first call
    from a freshly-started container measured ~10.5s (cold DNS/TLS) vs ~1.2s on every
    later call — call this once at host startup instead of paying that cost on the first
    live request (research.md §22). Never raises.
    """
    try:
        requests.get(OVERPASS_URL, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    except requests.RequestException:
        pass


def find_nearby_places(
    lat: float, lng: float, radius_km: float, limit: int = 10, timeout: float = 4.0
) -> list[dict]:
    """Query real nearby cafes/bakeries/restaurants/coworking spaces around (lat, lng).

    Returns a list of dicts with name/address/category/lat/lng/description — the same
    shape `BoHybridRagEngine` upserts into `UberRoute.WaitingPlace` before running its
    existing hybrid vector+keyword search unchanged (research.md §22). Returns an empty
    list (never raises) on any network/parsing failure — a live-lookup outage should
    degrade to "no waiting place found", not fail the whole request.
    """
    global _last_call_at
    radius_m = max(1, round(radius_km * 1000))

    elapsed = time.monotonic() - _last_call_at
    if elapsed < _MIN_INTERVAL_SECONDS:
        time.sleep(_MIN_INTERVAL_SECONDS - elapsed)

    query = f"""
    [out:json][timeout:{int(timeout)}];
    (
      node["amenity"~"^(cafe|restaurant|fast_food|bar)$"](around:{radius_m},{lat},{lng});
      node["shop"~"^(bakery|coffee)$"](around:{radius_m},{lat},{lng});
      node["office"="coworking"](around:{radius_m},{lat},{lng});
    );
    out body {limit};
    """

    try:
        response = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        _last_call_at = time.monotonic()
        response.raise_for_status()
        elements = response.json().get("elements", [])
    except (requests.RequestException, ValueError):
        return []

    places = []
    for element in elements:
        place = _to_place(element)
        if place is not None:
            places.append(place)
    return places


def _to_place(element: dict) -> Optional[dict]:
    tags = element.get("tags") or {}
    name = tags.get("name")
    if not name:
        return None  # unnamed POIs aren't useful suggestions

    category = (
        _AMENITY_CATEGORIES.get(tags.get("amenity"))
        or _SHOP_CATEGORIES.get(tags.get("shop"))
        or _OFFICE_CATEGORIES.get(tags.get("office"))
        or "cafe"
    )

    address_parts = [
        tags.get("addr:street"),
        tags.get("addr:housenumber"),
        tags.get("addr:suburb") or tags.get("addr:city"),
    ]
    address = ", ".join(p for p in address_parts if p) or "Address not available"

    description_parts = [f"{category.capitalize()} named {name}"]
    if tags.get("cuisine"):
        description_parts.append(f"cuisine: {tags['cuisine']}")
    if tags.get("internet_access") in ("wlan", "yes"):
        description_parts.append("has wifi")
    if tags.get("outdoor_seating") == "yes":
        description_parts.append("outdoor seating available")

    return {
        "name": name,
        "address": address,
        "category": category,
        "lat": element.get("lat"),
        "lng": element.get("lon"),
        "rating": None,  # OSM has no rating data; left null rather than fabricated
        "description": ", ".join(description_parts) + ".",
    }
