"""BpRouteOrchestrator — orchestrates the IntegratedML fare prediction and (when the
Business Rule fires) the hybrid RAG waiting-place lookup for one trip request.

Class name is underscore-free PascalCase (`BpRouteOrchestrator`) — IRIS 2026.1 Build 234U
was found to silently truncate brand-new class names at the first underscore during
compilation (research.md §12); this naming avoids that bug entirely.

`target_time` is the rider's **arrival deadline** (e.g. "I need to be at my meeting by
14:00"), not a departure time. This host works backwards from it: it estimates a naive
departure time (arrival minus estimated travel time, using UberRoute.TrafficWeatherReference
congestion for that hour), then scans nearby candidate departure times — each with its own
congestion-adjusted travel-time estimate — picking whichever is cheapest per
`FarePredictor`. `delta_minutes` is the gap between that chosen departure and the naive
one: a large gap means the rider must leave much earlier (or later) than they'd naively
expect, which is exactly when a waiting place is useful (FR-005/FR-006).

Owns the request/response shape defined in contracts/bs_uber_route_service.md: it always
returns a time/fare recommendation (FR-003), and only attaches a waiting-place suggestion
when `business_rules.waiting_place_should_be_suggested()` says so (FR-005/FR-006).
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

import iris
from intersystems_pyprod import BusinessProcess

iris_package_name = "UberRoute"

# Minutes offset from the naive departure time to try as candidate departure times.
_CANDIDATE_OFFSETS_MINUTES = [0, -30, -15, 15, 30, 45, 60]

# Waiting-place lookup radius (spec Assumptions: ~1 km comfortable walking distance).
_WAITING_PLACE_RADIUS_KM = 1.0

# Baseline urban travel speed used to turn distance into a travel-time estimate, before
# the TrafficWeatherReference congestion factor is applied (research.md; no live traffic
# API is used, per the constitution's Data & External Integration Standards).
_BASE_SPEED_KMH = 25.0
_DEFAULT_CONGESTION_FACTOR = 1.0


def _parse_hhmm(value: str) -> Optional[tuple[int, int]]:
    try:
        hour_str, minute_str = value.split(":")
        hour, minute = int(hour_str), int(minute_str)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return hour, minute
    except (ValueError, AttributeError):
        return None


def _add_minutes(hour: int, minute: int, offset: int) -> tuple[int, int]:
    total = (hour * 60 + minute + offset) % (24 * 60)
    if total < 0:
        total += 24 * 60
    return total // 60, total % 60


def _format_hhmm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _minutes_between(a: tuple[int, int], b: tuple[int, int]) -> int:
    a_total = a[0] * 60 + a[1]
    b_total = b[0] * 60 + b[1]
    return a_total - b_total


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return 2 * r_km * math.asin(math.sqrt(a))


def _congestion_factor(hour: int, day_of_week: int) -> float:
    """Reads UberRoute.TrafficWeatherReference (the Foreign Table / native fallback
    populated by sql/003_foreign_tables.sql or sql/003b) for this hour/day slot. Falls
    back to a neutral 1.0 if no row matches or the table isn't reachable — a missing
    traffic reading should degrade the duration estimate, not fail the whole request."""
    try:
        rs = iris.sql.exec(
            "SELECT CongestionFactor FROM UberRoute.TrafficWeatherReference "
            "WHERE HourOfDay = ? AND DayOfWeek = ?",
            hour,
            day_of_week,
        )
        for row in rs:
            if row[0] is not None:
                return float(row[0])
    except Exception:  # noqa: BLE001 — traffic reference is an enhancement, not a hard dependency
        pass
    return _DEFAULT_CONGESTION_FACTOR


def _estimate_travel_minutes(distance_km: float, hour: int, day_of_week: int) -> int:
    congestion = _congestion_factor(hour, day_of_week)
    hours = (distance_km / _BASE_SPEED_KMH) * congestion
    return max(1, round(hours * 60))


class BpRouteOrchestrator(BusinessProcess):
    def on_request(self, request):
        from production.adapters.geocoding_adapter import geocode
        from production.hosts.business_rules import waiting_place_should_be_suggested
        from production.messages.schemas import (
            FarePredictionQueryMessage,
            RouteRecommendationMessage,
            WaitingPlaceQueryMessage,
        )
        from production.observability.telemetry import log_event, timed_event

        session_id = request.session_id
        log_event("BpRouteOrchestrator", "request_received", session_id=session_id,
                   origin=request.origin, destination=request.destination,
                   target_time=request.target_time)

        arrival = _parse_hhmm(request.target_time)
        if arrival is None:
            response = RouteRecommendationMessage(
                error_code="invalid_request", error_message="target_time must be HH:MM"
            )
            return self.OKStatus(), response

        with timed_event("BpRouteOrchestrator", "geocode_call", session_id=session_id):
            origin_coords = geocode(request.origin)
            destination_coords = geocode(request.destination)

        if origin_coords is None or destination_coords is None:
            unresolved = "origin" if origin_coords is None else "destination"
            log_event("BpRouteOrchestrator", "rule_outcome", session_id=session_id,
                       outcome="error", reason=f"could not resolve {unresolved}")
            response = RouteRecommendationMessage(
                error_code="location_not_found",
                error_message=f"Could not resolve '{unresolved}' to a known location",
            )
            return self.OKStatus(), response

        distance_km = _haversine_km(
            origin_coords[0], origin_coords[1], destination_coords[0], destination_coords[1]
        )
        day_of_week = datetime.now().isoweekday()

        # Naive departure: "if traffic at my arrival hour is typical, when would I need
        # to leave to arrive right on time?" — the baseline candidate offsets are scanned
        # around this, not around the arrival time itself.
        naive_duration = _estimate_travel_minutes(distance_km, arrival[0], day_of_week)
        naive_departure = _add_minutes(arrival[0], arrival[1], -naive_duration)

        best_time: Optional[tuple[int, int]] = None
        best_fare: Optional[float] = None
        last_error = ""
        for offset in _CANDIDATE_OFFSETS_MINUTES:
            candidate = _add_minutes(naive_departure[0], naive_departure[1], offset)
            candidate_str = _format_hhmm(*candidate)
            query = FarePredictionQueryMessage(
                session_id=session_id,
                candidate_time=candidate_str,
                day_of_week=day_of_week,
                distance_km=distance_km,
            )
            status, result = self.send_request_sync("BoIntegratedMlPredictor", query)
            if not result.ok:
                last_error = result.error_message
                continue
            if best_fare is None or result.predicted_fare < best_fare:
                best_fare = result.predicted_fare
                best_time = candidate

        if best_time is None or best_fare is None:
            log_event("BpRouteOrchestrator", "rule_outcome", session_id=session_id,
                       outcome="error", reason="no fare prediction available")
            response = RouteRecommendationMessage(
                error_code="prediction_unavailable",
                error_message=(
                    "Could not compute a fare/time recommendation right now: "
                    f"{last_error or 'IntegratedML prediction failed'}"
                ),
            )
            return self.OKStatus(), response

        delta_minutes = abs(_minutes_between(best_time, naive_departure))
        triggered = waiting_place_should_be_suggested(delta_minutes)
        log_event("BpRouteOrchestrator", "rule_outcome", session_id=session_id,
                   delta_minutes=delta_minutes, waiting_place_triggered=triggered)

        chosen_duration = _estimate_travel_minutes(distance_km, best_time[0], day_of_week)
        estimated_arrival = _add_minutes(best_time[0], best_time[1], chosen_duration)

        response = RouteRecommendationMessage(
            recommended_time=_format_hhmm(*best_time),
            estimated_arrival_time=_format_hhmm(*estimated_arrival),
            estimated_fare=round(best_fare, 2),
            delta_minutes=delta_minutes,
            waiting_place_suggested=triggered,
        )

        if triggered:
            place_query = WaitingPlaceQueryMessage(
                session_id=session_id,
                origin_lat=origin_coords[0],
                origin_lng=origin_coords[1],
                query_text=f"place to wait near {request.origin}",
                radius_km=_WAITING_PLACE_RADIUS_KM,
            )
            with timed_event("BpRouteOrchestrator", "rag_call", session_id=session_id):
                status, place = self.send_request_sync("BoHybridRagEngine", place_query)

            if place.found:
                response.waiting_place_name = place.name
                response.waiting_place_address = place.address
                response.waiting_place_category = place.category
                response.waiting_place_rating = place.rating
                response.waiting_place_distance_km = place.distance_km
                response.waiting_place_rationale = place.rationale
            else:
                response.waiting_place_unavailable_reason = (
                    place.unavailable_reason or "No nearby waiting place found"
                )

        self._persist(request, response, session_id)
        return self.OKStatus(), response

    @staticmethod
    def _persist(request, response, session_id: str) -> None:
        """FR-012: record the request and key decision points (Constitution Principle V)."""
        from production.observability.telemetry import log_event, timed_event

        with timed_event("BpRouteOrchestrator", "persisted", session_id=session_id) as ev:
            try:
                trip_stmt = iris.sql.prepare(
                    "INSERT INTO UberRoute.TripRequest "
                    "(Origin, Destination, TargetTime) VALUES (?, ?, ?)"
                )
                trip_stmt.execute(request.origin, request.destination, request.target_time)

                id_rs = iris.sql.exec("SELECT LAST_IDENTITY()")
                trip_request_id = 0
                for row in id_rs:
                    trip_request_id = int(row[0])

                rec_stmt = iris.sql.prepare(
                    "INSERT INTO UberRoute.RouteRecommendation "
                    "(TripRequestID, RecommendedTime, EstimatedFare, DeltaMinutes, "
                    "WaitingPlaceTriggered) VALUES (?, ?, ?, ?, ?)"
                )
                rec_stmt.execute(
                    trip_request_id,
                    response.recommended_time,
                    response.estimated_fare,
                    response.delta_minutes,
                    1 if response.waiting_place_suggested else 0,
                )

                import json

                log_stmt = iris.sql.prepare(
                    "INSERT INTO UberRoute.RequestLog (SessionID, Payload) VALUES (?, ?)"
                )
                payload = json.dumps(
                    {
                        "request": {
                            "origin": request.origin,
                            "destination": request.destination,
                            "target_time": request.target_time,
                        },
                        "response": {
                            "recommended_time": response.recommended_time,
                            "estimated_fare": response.estimated_fare,
                            "delta_minutes": response.delta_minutes,
                            "waiting_place_suggested": response.waiting_place_suggested,
                        },
                    }
                )
                log_stmt.execute(session_id, payload)
            except Exception as exc:  # noqa: BLE001 — persistence must not fail the request
                ev.outcome = "error"
                log_event("BpRouteOrchestrator", "error", session_id=session_id,
                           outcome="error", error_message=str(exc))
