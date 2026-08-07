"""Contract tests for POST /api/uber-route/recommend — the 200 (delta<=30min), 400, and
422 response shapes from contracts/bs_uber_route_service.md (tasks.md T015).

`director.create_business_service` is mocked so these tests don't require a running IRIS
production — they verify production/wsgi/app.py's HTTP-layer contract in isolation.

Mock response objects use PascalCase attributes (ErrorCode, RecommendedTime, ...), matching
what `service.process_input()` actually returns live: the raw IRIS-side message object, not
the Python-side snake_case RouteRecommendationMessage (see research.md §14).
"""
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from production.wsgi.app import application


def _call(body: dict, method: str = "POST"):
    body_bytes = json.dumps(body).encode("utf-8") if body is not None else b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": "/api/uber-route/recommend",
        "CONTENT_LENGTH": str(len(body_bytes)),
        "wsgi.input": BytesIO(body_bytes),
    }
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    result = application(environ, start_response)
    payload = json.loads(b"".join(result).decode("utf-8"))
    return captured["status"], payload


def _stub_service(response):
    service = SimpleNamespace(process_input=lambda payload: ("1", response))
    return "1", service


def test_get_root_serves_the_frontend_html():
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/"}
    captured = {}
    result = application(environ, lambda s, h: captured.update(status=s, headers=h))
    body = b"".join(result).decode("utf-8")
    assert captured["status"].startswith("200")
    assert any(name == "Content-Type" and "text/html" in value for name, value in captured["headers"])
    assert "<form" in body


def test_wrong_method_returns_405():
    status, payload = _call({"origin": "A", "destination": "B", "target_time": "18:00"},
                             method="GET")
    assert status.startswith("405")


def test_malformed_json_returns_400():
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/api/uber-route/recommend",
        "CONTENT_LENGTH": "9",
        "wsgi.input": BytesIO(b"not-json!"),
    }
    captured = {}
    result = application(environ, lambda s, h: captured.update(status=s))
    payload = json.loads(b"".join(result).decode("utf-8"))
    assert captured["status"].startswith("400")
    assert payload["error"] == "invalid_request"


def test_validation_error_from_service_returns_400():
    response = SimpleNamespace(
        ErrorCode="invalid_request", ErrorMessage="missing required field(s): origin"
    )
    with patch("production.wsgi.app.director.create_business_service",
               return_value=_stub_service(response)):
        status, payload = _call({"destination": "B", "target_time": "18:00"})
    assert status.startswith("400")
    assert payload["error"] == "invalid_request"


def test_location_not_found_returns_422():
    response = SimpleNamespace(
        ErrorCode="location_not_found", ErrorMessage="Could not resolve 'origin'"
    )
    with patch("production.wsgi.app.director.create_business_service",
               return_value=_stub_service(response)):
        status, payload = _call(
            {"origin": "nowhere", "destination": "B", "target_time": "18:00"}
        )
    assert status.startswith("422")
    assert payload["error"] == "location_not_found"


def test_delta_leq_30_min_returns_200_with_no_waiting_place():
    response = SimpleNamespace(
        ErrorCode="",
        TripRequestId=1,
        RecommendedTime="18:05",
        EstimatedArrivalTime="18:30",
        EstimatedFare=27.90,
        DeltaMinutes=5,
        WaitingPlaceSuggested=False,
        WaitingPlaceName="",
        WaitingPlaceAddress="",
        WaitingPlaceCategory="",
        WaitingPlaceRating=0.0,
        WaitingPlaceDistanceKm=0.0,
        WaitingPlaceRationale="",
        WaitingPlaceUnavailableReason="",
    )
    with patch("production.wsgi.app.director.create_business_service",
               return_value=_stub_service(response)):
        status, payload = _call(
            {"origin": "A", "destination": "B", "target_time": "18:00"}
        )
    assert status.startswith("200")
    assert payload["waiting_place_suggested"] is False
    assert payload["waiting_place"] is None
    assert payload["recommended_time"] == "18:05"
    assert payload["estimated_fare"] == 27.90
