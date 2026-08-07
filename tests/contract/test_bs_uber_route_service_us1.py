"""Contract tests for POST /api/uber-route/recommend — the 200 (delta<=30min), 400, and
422 response shapes from contracts/bs_uber_route_service.md (tasks.md T015).

`director.create_business_service` is mocked so these tests don't require a running IRIS
production — they verify production/wsgi/app.py's HTTP-layer contract in isolation.
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


def test_wrong_method_returns_405():
    status, payload = _call({"origin": "A", "destination": "B", "target_time": "18:00"},
                             method="GET")
    assert status.startswith("405")


def test_malformed_json_returns_400():
    environ = {
        "REQUEST_METHOD": "POST",
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
        error_code="invalid_request", error_message="missing required field(s): origin"
    )
    with patch("production.wsgi.app.director.create_business_service",
               return_value=_stub_service(response)):
        status, payload = _call({"destination": "B", "target_time": "18:00"})
    assert status.startswith("400")
    assert payload["error"] == "invalid_request"


def test_location_not_found_returns_422():
    response = SimpleNamespace(
        error_code="location_not_found", error_message="Could not resolve 'origin'"
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
        error_code="",
        trip_request_id=1,
        recommended_time="18:05",
        estimated_fare=27.90,
        delta_minutes=5,
        waiting_place_suggested=False,
        waiting_place_name="",
        waiting_place_address="",
        waiting_place_category="",
        waiting_place_rating=0.0,
        waiting_place_distance_km=0.0,
        waiting_place_rationale="",
        waiting_place_unavailable_reason="",
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
