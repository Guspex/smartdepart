"""Contract tests for the "delta > 30 min" response shapes (tasks.md T025):
waiting place found, and waiting place unavailable — contracts/bs_uber_route_service.md.

Mock response objects use PascalCase attributes (ErrorCode, RecommendedTime, ...), matching
what `service.process_input()` actually returns live: the raw IRIS-side message object, not
the Python-side snake_case RouteRecommendationMessage (see research.md §14).
"""
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from production.wsgi.app import application


def _call(body: dict):
    body_bytes = json.dumps(body).encode("utf-8")
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/api/uber-route/recommend",
        "CONTENT_LENGTH": str(len(body_bytes)),
        "wsgi.input": BytesIO(body_bytes),
    }
    captured = {}
    result = application(environ, lambda s, h: captured.update(status=s))
    return captured["status"], json.loads(b"".join(result).decode("utf-8"))


def _stub_service(response):
    return "1", SimpleNamespace(process_input=lambda payload: ("1", response))


def test_delta_gt_30_min_with_waiting_place_found():
    response = SimpleNamespace(
        ErrorCode="",
        TripRequestId=2,
        RecommendedTime="19:20",
        EstimatedArrivalTime="19:50",
        EstimatedFare=19.50,
        DeltaMinutes=50,
        WaitingPlaceSuggested=True,
        WaitingPlaceName="Cafe Central",
        WaitingPlaceAddress="Rua Augusta, 500",
        WaitingPlaceCategory="cafe",
        WaitingPlaceRating=4.6,
        WaitingPlaceDistanceKm=0.4,
        WaitingPlaceRationale="Closest highly-rated match",
        WaitingPlaceUnavailableReason="",
    )
    with patch("production.wsgi.app.director.create_business_service",
               return_value=_stub_service(response)):
        status, payload = _call(
            {"origin": "A", "destination": "B", "target_time": "18:30"}
        )
    assert status.startswith("200")
    assert payload["waiting_place_suggested"] is True
    assert payload["waiting_place"]["name"] == "Cafe Central"
    assert payload["waiting_place"]["rationale"] == "Closest highly-rated match"


def test_delta_gt_30_min_but_no_waiting_place_available():
    response = SimpleNamespace(
        ErrorCode="",
        TripRequestId=3,
        RecommendedTime="19:20",
        EstimatedArrivalTime="19:50",
        EstimatedFare=19.50,
        DeltaMinutes=50,
        WaitingPlaceSuggested=True,
        WaitingPlaceName="",
        WaitingPlaceAddress="",
        WaitingPlaceCategory="",
        WaitingPlaceRating=0.0,
        WaitingPlaceDistanceKm=0.0,
        WaitingPlaceRationale="",
        WaitingPlaceUnavailableReason="No nearby waiting place found within 1.0 km",
    )
    with patch("production.wsgi.app.director.create_business_service",
               return_value=_stub_service(response)):
        status, payload = _call(
            {"origin": "A", "destination": "B", "target_time": "18:30"}
        )
    assert status.startswith("200")
    assert payload["waiting_place_suggested"] is True
    assert payload["waiting_place"] is None
    assert payload["waiting_place_unavailable_reason"] == "No nearby waiting place found within 1.0 km"
