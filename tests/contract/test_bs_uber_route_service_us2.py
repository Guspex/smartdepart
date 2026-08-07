"""Contract tests for the "delta > 30 min" response shapes (tasks.md T025):
waiting place found, and waiting place unavailable — contracts/bs_uber_route_service.md.
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
        error_code="",
        trip_request_id=2,
        recommended_time="19:20",
        estimated_fare=19.50,
        delta_minutes=50,
        waiting_place_suggested=True,
        waiting_place_name="Cafe Central",
        waiting_place_address="Rua Augusta, 500",
        waiting_place_category="cafe",
        waiting_place_rating=4.6,
        waiting_place_distance_km=0.4,
        waiting_place_rationale="Closest highly-rated match",
        waiting_place_unavailable_reason="",
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
        error_code="",
        trip_request_id=3,
        recommended_time="19:20",
        estimated_fare=19.50,
        delta_minutes=50,
        waiting_place_suggested=True,
        waiting_place_name="",
        waiting_place_address="",
        waiting_place_category="",
        waiting_place_rating=0.0,
        waiting_place_distance_km=0.0,
        waiting_place_rationale="",
        waiting_place_unavailable_reason="No nearby waiting place found within 1.0 km",
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
