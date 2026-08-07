"""BoHybridRagEngine — hybrid (vector + keyword) search over the WaitingPlace
collection (constitution Principle III, NON-NEGOTIABLE; research.md §5).

Combines a `VECTOR_COSINE` semantic search with an iFind keyword search
(`%FIND search_index(...)` — see research.md §5 for why this is used instead of the
`%CONTAINS(...)` function-call form, which does not work against a DDL-created
`%iFind.Index.Basic` index) using a 0.6/0.4 weighted score, filtered to the
origin-proximity radius (FR-009).

Class name is underscore-free PascalCase (`BoHybridRagEngine`) — IRIS 2026.1 Build 234U was
found to silently truncate brand-new class names at the first underscore during compilation
(research.md §12); this naming avoids that bug entirely.
"""
from __future__ import annotations

import math
import os
import sys
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
from production.messages.schemas import WaitingPlaceResultMessage  # noqa: E402
from production.observability.telemetry import timed_event  # noqa: E402

iris_package_name = "UberRoute"

_VECTOR_WEIGHT = 0.6
_KEYWORD_WEIGHT = 0.4
_VECTOR_CANDIDATES = 10
_EMBEDDING_DIM = 384

_model = None  # lazy-loaded sentence-transformers singleton (research.md §3)


def _get_embedding_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer("all-MiniLM-L6-v2")
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
                    "vector_score": round(float(vector_score), 4),
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
    def _rationale(best: dict, candidates: list[dict]) -> str:
        """User Story 3: explain why this place was chosen over other candidates."""
        signal = "semantic match" if best["vector_score"] >= best["keyword_score"] else "keyword match"
        parts = [f"{best['distance_km']:.1f} km away", f"strongest signal: {signal}"]
        if best["rating"]:
            parts.append(f"rated {best['rating']:.1f}")
        if len(candidates) > 1:
            parts.append(f"ranked above {len(candidates) - 1} other nearby option(s)")
        return "; ".join(parts)
