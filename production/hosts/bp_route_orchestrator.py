"""BpRouteOrchestrator — orchestrates the IntegratedML fare prediction for each departure
option and the hybrid RAG waiting-place lookup for the two earlier-departure options.

Class name is underscore-free PascalCase (`BpRouteOrchestrator`) — IRIS 2026.1 Build 234U
was found to silently truncate brand-new class names at the first underscore during
compilation (research.md §12); this naming avoids that bug entirely.

`target_time` is the rider's **arrival deadline** (e.g. "I need to be at my meeting by
14:00"), not a departure time. This host works backwards from it: it estimates a naive
departure time (arrival minus estimated travel time, using UberRoute.TrafficWeatherReference
congestion for that hour), then returns three fixed departure options anchored to that naive
time — leave then ("ideal"), 30 minutes earlier, or 60 minutes earlier — each independently
priced by `FarePredictor`, so the rider can directly compare "leave now for X" against
"leave 30/60 min early, wait somewhere, pay Y" (research.md §20; this replaced an earlier
single-recommendation design that auto-picked one "cheapest nearby" time — see research.md
§7/§8/§16 for that design's history).

Owns the request/response shape defined in contracts/bs_uber_route_service.md: it always
returns all three options (FR-003), and the two earlier-departure options always carry a
waiting-place suggestion (or an explanation why none was found) — waiting is the whole point
of choosing them, so this is no longer conditional on a delta threshold.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime
from typing import Optional

import iris
from intersystems_pyprod import BusinessProcess

# pyprod's generated OnInit only adds this file's own directory
# (production/hosts/) to sys.path, not the project root — so the
# `from production.X.Y import ...` package-qualified imports used
# throughout this file need the root added explicitly, or every host
# job fails at first message with ModuleNotFoundError: No module named
# 'production' (found live against IRIS 2025.3; see research.md §14).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Imported at module level (not deferred inside a method), and specifically
# BEFORE any message can arrive: pyprod's _createmessage() looks up incoming
# IRIS message objects in intersystems_pyprod's internal
# _ProductionMessage_registry, which a message class only joins once its
# module has been imported in this process. A deferred import here left the
# registry empty until the first call, causing every request's message
# object to fail conversion with `AttributeError: Property session_id not
# found` (found live against IRIS 2025.3; see research.md §14).
from production.adapters.geocoding_adapter import geocode  # noqa: E402
from production.messages.schemas import (  # noqa: E402
    FarePredictionQueryMessage,
    RouteRecommendationMessage,
    WaitingPlaceQueryMessage,
)
from production.observability.telemetry import log_event, timed_event  # noqa: E402

iris_package_name = "UberRoute"

# The three fixed options offered relative to the naive departure time (research.md §20):
# leave right when you'd naively need to, or leave 30/60 minutes earlier and wait somewhere.
_OPTIONS = [
    ("ideal", 0),
    ("30min_earlier", -30),
    ("60min_earlier", -60),
]

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
        # to leave to arrive right on time?" — every option's offset is measured from
        # this, not from the arrival time itself.
        naive_duration = _estimate_travel_minutes(distance_km, arrival[0], day_of_week)
        naive_departure = _add_minutes(arrival[0], arrival[1], -naive_duration)

        options = []
        last_error = ""
        for label, offset in _OPTIONS:
            option, error = self._build_option(
                label, offset, naive_departure, distance_km, day_of_week,
                origin_coords, request.origin, session_id,
            )
            if option is not None:
                options.append(option)
            else:
                last_error = error

        if not options:
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

        log_event("BpRouteOrchestrator", "rule_outcome", session_id=session_id,
                   options_returned=len(options))

        response = RouteRecommendationMessage(options_json=json.dumps(options))
        self._persist(request, response, options, session_id)
        return self.OKStatus(), response

    def _build_option(
        self, label, offset_minutes, naive_departure, distance_km, day_of_week,
        origin_coords, origin_text, session_id,
    ):
        """Price one departure option and, for the two earlier-departure options, attach a
        waiting-place suggestion. Returns (option_dict, None) on success or (None,
        error_message) if the fare prediction itself failed."""
        candidate = _add_minutes(naive_departure[0], naive_departure[1], offset_minutes)
        candidate_str = _format_hhmm(*candidate)

        query = FarePredictionQueryMessage(
            session_id=session_id,
            candidate_time=candidate_str,
            day_of_week=day_of_week,
            distance_km=distance_km,
        )
        status, result = self.send_request_sync("BoIntegratedMlPredictor", query)
        if not result.ok:
            return None, result.error_message

        duration = _estimate_travel_minutes(distance_km, candidate[0], day_of_week)
        arrival_time = _add_minutes(candidate[0], candidate[1], duration)

        option = {
            "label": label,
            "wait_minutes": abs(offset_minutes),
            "departure_time": candidate_str,
            "arrival_time": _format_hhmm(*arrival_time),
            "estimated_fare": round(result.predicted_fare, 2),
            "waiting_place": None,
            "waiting_place_unavailable_reason": None,
        }

        if offset_minutes != 0:
            place_query = WaitingPlaceQueryMessage(
                session_id=session_id,
                origin_lat=origin_coords[0],
                origin_lng=origin_coords[1],
                query_text=f"place to wait near {origin_text}",
                radius_km=_WAITING_PLACE_RADIUS_KM,
            )
            with timed_event("BpRouteOrchestrator", "rag_call", session_id=session_id,
                              option=label):
                status, place = self.send_request_sync("BoHybridRagEngine", place_query)

            if place.found:
                option["waiting_place"] = {
                    "name": place.name,
                    "address": place.address,
                    "category": place.category,
                    "rating": place.rating,
                    "distance_km": place.distance_km,
                    "rationale": place.rationale,
                }
            else:
                option["waiting_place_unavailable_reason"] = (
                    place.unavailable_reason or "No nearby waiting place found"
                )

        return option, None

    @staticmethod
    def _persist(request, response, options: list, session_id: str) -> None:
        """FR-012: record the request and key decision points (Constitution Principle V).

        `UberRoute.RouteRecommendation` predates the 3-option redesign (research.md §20)
        and only has columns for a single time/fare/delta — rather than a schema migration,
        it keeps recording just the "ideal" (0-offset) option for simple SQL querying, while
        `UberRoute.RequestLog.Payload` (already JSON, per constitution Principle II) carries
        every option in full.
        """
        with timed_event("BpRouteOrchestrator", "persisted", session_id=session_id) as ev:
            try:
                trip_stmt = iris.sql.prepare(
                    "INSERT INTO UberRoute.TripRequest "
                    "(Origin, Destination, TargetTime) VALUES (?, ?, ?)"
                )
                trip_stmt.execute(request.origin, request.destination, request.target_time)

                # LAST_IDENTITY() reliably returns '' in this environment (research.md §16)
                # rather than the row just inserted — degrades to 0, matching how
                # response.trip_request_id already defaults, rather than failing the request.
                id_rs = iris.sql.exec("SELECT LAST_IDENTITY()")
                trip_request_id = 0
                for row in id_rs:
                    trip_request_id = int(row[0]) if row[0] not in (None, "") else 0

                ideal = next((o for o in options if o["label"] == "ideal"), None)
                if ideal is not None:
                    rec_stmt = iris.sql.prepare(
                        "INSERT INTO UberRoute.RouteRecommendation "
                        "(TripRequestID, RecommendedTime, EstimatedFare, DeltaMinutes, "
                        "WaitingPlaceTriggered) VALUES (?, ?, ?, ?, ?)"
                    )
                    rec_stmt.execute(
                        trip_request_id,
                        ideal["departure_time"],
                        ideal["estimated_fare"],
                        0,
                        1 if any(o["waiting_place"] for o in options) else 0,
                    )

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
                        "response": {"options": options},
                    }
                )
                log_stmt.execute(session_id, payload)
            except Exception as exc:  # noqa: BLE001 — persistence must not fail the request
                ev.outcome = "error"
                log_event("BpRouteOrchestrator", "error", session_id=session_id,
                           outcome="error", error_message=str(exc))
