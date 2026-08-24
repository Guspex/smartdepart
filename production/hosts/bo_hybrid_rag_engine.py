"""BoHybridRagEngine — hybrid (vector + keyword) search over the WaitingPlace
collection (constitution Principle III, NON-NEGOTIABLE; research.md §5, §22).

Combines a `VECTOR_COSINE` semantic search with an iFind keyword search
(`%FIND search_index(...)` — see research.md §5 for why this is used instead of the
`%CONTAINS(...)` function-call form, which does not work against a DDL-created
`%iFind.Index.Basic` index) using a 0.6/0.4 weighted score, filtered to the
origin-proximity radius (FR-009).

Before running that search, it fetches real, live nearby places from the Overpass API
(`overpass_adapter`) and upserts any not already present into `WaitingPlace` — this covers
any city, not just wherever a static seed dataset happened to include (research.md §22),
while keeping the actual retrieval mechanics (embedding, hybrid vector+keyword ranking) as
required by the constitution, over IRIS's native vector search.

Class name is underscore-free PascalCase (`BoHybridRagEngine`) — IRIS 2026.1 Build 234U was
found to silently truncate brand-new class names at the first underscore during compilation
(research.md §12); this naming avoids that bug entirely.
"""
from __future__ import annotations

import math
import os
import sys
import time
from typing import Optional

import iris
from intersystems_pyprod import BusinessOperation

# See research.md §14: pyprod's generated OnInit only adds this file's own
# directory to sys.path, not the project root needed for `from production.X.Y
# import ...`. Add it explicitly so every deferred import below resolves.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Imported at module level, before any message can arrive — see research.md
# §14 (deferred imports left intersystems_pyprod's message registry empty,
# breaking incoming-message conversion).
from production.adapters.overpass_adapter import find_nearby_places, warm_up  # noqa: E402
from production.messages.schemas import WaitingPlaceResultMessage  # noqa: E402
from production.observability.telemetry import timed_event  # noqa: E402

iris_package_name = "UberRoute"

_VECTOR_WEIGHT = 0.6
_KEYWORD_WEIGHT = 0.4
_VECTOR_CANDIDATES = 10
_EMBEDDING_DIM = 384

# A single trip request calls this host once per earlier-departure option (30min, 60min) —
# both with the *same* origin. Without this cache, each call re-fetches Overpass and
# re-embeds every candidate, doubling the request's latency for no new data, and pushed
# the second call over IRIS's dead-job threshold (research.md §22). Keyed by origin
# rounded to ~110m, valid for 5 minutes -- generous enough to cover one trip request's
# two lookups, short enough that a new nearby place added to OSM shows up soon.
_LIVE_SYNC_CACHE_TTL_SECONDS = 300.0
_live_sync_cache: dict = {}

# Cap on how many new Overpass candidates get embedded+indexed per sync (research.md §22)
# -- each is a real, blocking embedding call, and a handful of real options is already
# enough for a meaningful hybrid-search result.
_MAX_NEW_CANDIDATES_PER_SYNC = 3

_model = None  # lazy-loaded sentence-transformers singleton (research.md §3)


def _get_embedding_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        # backend="onnx" (not the "torch" default) -- research.md §22: the default PyTorch
        # backend reliably segfaulted (signal 11) the IRIS worker job mid-encode, every
        # time, confirmed via /usr/irissys/mgr/messages.log. ONNX Runtime uses a different
        # C-extension/threading model that doesn't hit whatever incompatibility that is.
        # model_kwargs pins one specific ONNX file -- without it, the library warns about
        # multiple candidate .onnx files bundled in the model repo and picks one arbitrarily.
        _model = SentenceTransformer(
            "all-MiniLM-L6-v2", backend="onnx", model_kwargs={"file_name": "onnx/model.onnx"}
        )
    return _model


def _embed(text: str) -> str:
    """Return a comma-separated-string embedding vector, ready for TO_VECTOR(?, DOUBLE, 384)."""
    vector = _get_embedding_model().encode(text)
    return ",".join(f"{v:.6f}" for v in vector)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return 2 * r_km * math.asin(math.sqrt(a))


class BoHybridRagEngine(BusinessOperation):
    def __init__(self, iris_host_object):
        super().__init__(iris_host_object)
        # Pre-warm the embedding model AND the Overpass HTTPS connection at job startup
        # (pyprod calls __init__ from OnInit, before any message can arrive) rather than
        # lazily on the first live request. Both are one-time-slow, every-time-after-fast:
        # loading sentence-transformers cold takes ~12s (torch import overhead dominates,
        # not the actual weight load), and the first outbound HTTPS call to Overpass from
        # this container took ~10.5s (cold DNS/TLS) vs ~1.2s on every later call. Either
        # alone was already long enough that IRIS's Ens.MonitorService marked the worker
        # job "dead" mid-request; doing both cold on the same live request compounded it
        # (research.md §22).
        _get_embedding_model()
        warm_up()

    def on_message(self, request):
        session_id = getattr(request, "session_id", "")
        with timed_event("BoHybridRagEngine", "rag_call", session_id=session_id,
                          query_text=request.query_text) as ev:
            try:
                best = self._search(
                    request.query_text, request.origin_lat, request.origin_lng, request.radius_km
                )
            except Exception as exc:  # noqa: BLE001
                ev.outcome = "error"
                return self.OKStatus(), WaitingPlaceResultMessage(
                    found=False, unavailable_reason=f"RAG lookup failed: {exc}"
                )

        if best is None:
            return self.OKStatus(), WaitingPlaceResultMessage(
                found=False,
                unavailable_reason=f"No nearby waiting place found within {request.radius_km:.1f} km",
            )

        return self.OKStatus(), WaitingPlaceResultMessage(
            found=True,
            name=best["name"],
            address=best["address"],
            category=best["category"],
            rating=best["rating"],
            distance_km=best["distance_km"],
            rationale=best["rationale"],
            vector_score=best["vector_score"],
            keyword_score=best["keyword_score"],
        )

    def _search(
        self, query_text: str, origin_lat: float, origin_lng: float, radius_km: float
    ) -> Optional[dict]:
        self._sync_live_candidates(origin_lat, origin_lng, radius_km)

        query_vector = _embed(query_text)

        vector_stmt = iris.sql.prepare(
            f"SELECT TOP {_VECTOR_CANDIDATES} ID, Name, Address, Category, Lat, Lng, Rating, "
            "VECTOR_COSINE(Embedding, TO_VECTOR(?, DOUBLE, 384)) AS VectorScore "
            "FROM UberRoute.WaitingPlace ORDER BY VectorScore DESC"
        )
        vector_rows = list(vector_stmt.execute(query_vector))

        keyword_ids: set[int] = set()
        first_term = query_text.split()[0] if query_text.split() else ""
        if first_term:
            try:
                keyword_stmt = iris.sql.prepare(
                    "SELECT %ID FROM UberRoute.WaitingPlace "
                    "WHERE %ID %FIND search_index(SearchableTextIdx, ?)"
                )
                keyword_ids = {row[0] for row in keyword_stmt.execute(first_term)}
            except Exception:  # noqa: BLE001 — keyword search is an enhancement, not a hard dependency
                keyword_ids = set()

        candidates = []
        for row in vector_rows:
            place_id, name, address, category, lat, lng, rating, vector_score = row
            # VECTOR_COSINE(...) comes back as an ObjectScript numeric string (e.g.
            # ".0059100573841869051897"), not a Python float -- research.md §22.
            vector_score = float(vector_score)
            if lat is None or lng is None:
                continue
            distance_km = _haversine_km(origin_lat, origin_lng, lat, lng)
            if distance_km > radius_km:
                continue
            keyword_score = 1.0 if place_id in keyword_ids else 0.0
            final_score = _VECTOR_WEIGHT * vector_score + _KEYWORD_WEIGHT * keyword_score
            candidates.append(
                {
                    "id": place_id,
                    "name": name,
                    "address": address,
                    "category": category or "",
                    "rating": float(rating) if rating is not None else 0.0,
                    "distance_km": round(distance_km, 2),
                    "vector_score": round(vector_score, 4),
                    "keyword_score": keyword_score,
                    "final_score": final_score,
                }
            )

        if not candidates:
            return None

        candidates.sort(key=lambda c: c["final_score"], reverse=True)
        best = candidates[0]
        best["rationale"] = self._rationale(best, candidates)
        return best

    @staticmethod
    def _sync_live_candidates(origin_lat: float, origin_lng: float, radius_km: float) -> None:
        """Fetch real nearby places from Overpass (research.md §22) and insert any not
        already in `WaitingPlace`, so the hybrid search below has real candidates for
        wherever the rider actually is, not just wherever the seed dataset covered.

        Never raises — a live-lookup outage should fall back to whatever's already
        indexed (seed data, or a previous request's cached candidates for this area),
        not fail the whole waiting-place lookup.
        """
        cache_key = (round(origin_lat, 3), round(origin_lng, 3))  # ~110m grid
        last_synced = _live_sync_cache.get(cache_key)
        if last_synced is not None and (time.monotonic() - last_synced) < _LIVE_SYNC_CACHE_TTL_SECONDS:
            return  # one trip request calls this per earlier-departure option (research.md §22)
        _live_sync_cache[cache_key] = time.monotonic()

        try:
            places = find_nearby_places(origin_lat, origin_lng, radius_km)
        except Exception:  # noqa: BLE001
            return

        # Each new candidate costs one embedding call — IRIS's job monitor marked this
        # worker "dead" mid-sync when indexing all ~10 Overpass results in one pass, even
        # though the code itself was correct and would have finished (research.md §22).
        # A handful of real candidates is enough for a meaningful hybrid-search result;
        # capping the embed/insert work keeps one request's total latency well under
        # whatever threshold the monitor enforces.
        new_candidates_indexed = 0
        for place in places:
            if new_candidates_indexed >= _MAX_NEW_CANDIDATES_PER_SYNC:
                break
            if place.get("lat") is None or place.get("lng") is None:
                continue
            try:
                exists_stmt = iris.sql.prepare(
                    "SELECT TOP 1 %ID FROM UberRoute.WaitingPlace WHERE Name = ? AND Address = ?"
                )
                already_indexed = list(exists_stmt.execute(place["name"], place["address"]))
                if already_indexed:
                    continue

                description = place.get("description", "")
                header = f"{place['name']} — {place['address']} ({place['category']})"
                searchable_text = f"{place['name']} {place['address']} {place['category']} {description}"
                embedding = _embed(f"{header} {description}".strip())

                insert_stmt = iris.sql.prepare(
                    "INSERT INTO UberRoute.WaitingPlace "
                    "(Name, Address, Category, Lat, Lng, Rating, Description, SearchableText, "
                    "Embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, TO_VECTOR(?, DOUBLE, 384))"
                )
                insert_stmt.execute(
                    place["name"], place["address"], place["category"],
                    place["lat"], place["lng"], place.get("rating"),
                    description, searchable_text, embedding,
                )
                new_candidates_indexed += 1
            except Exception:  # noqa: BLE001 — one bad candidate must not block the others
                continue

    @staticmethod
    def _rationale(best: dict, candidates: list[dict]) -> str:
        """User Story 3: explain why this place was chosen over other candidates."""
        signal = "semantic match" if best["vector_score"] >= best["keyword_score"] else "keyword match"
        parts = [f"{best['distance_km']:.1f} km away", f"strongest signal: {signal}"]
        if best["rating"]:
            parts.append(f"rated {best['rating']:.1f}")
        if len(candidates) > 1:
            parts.append(f"ranked above {len(candidates) - 1} other nearby option(s)")
        return "; ".join(parts)
