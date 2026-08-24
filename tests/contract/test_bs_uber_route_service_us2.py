"""Contract tests for the earlier-departure options' waiting-place shapes (tasks.md T025;
research.md §20): waiting place found, and waiting place unavailable — both are now
per-option fields inside the 3-option response, not a single top-level
waiting_place_suggested flag gated by a 30-minute delta.

Mock response objects use PascalCase attributes (ErrorCode, OptionsJson, ...), matching
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


def test_earlier_option_with_waiting_place_found():
    options = [
        {"label": "ideal", "wait_minutes": 0, "departure_time": "19:20",
         "arrival_time": "19:50", "estimated_fare": 19.50,
         "waiting_place": None, "waiting_place_unavailable_reason": None},
        {"label": "30min_earlier", "wait_minutes": 30, "departure_time": "18:50",
         "arrival_time": "19:20", "estimated_fare": 17.20,
         "waiting_place": {
             "name": "Cafe Central", "address": "Rua Augusta, 500", "category": "cafe",
             "rating": 4.6, "distance_km": 0.4,
             "rationale": "Closest highly-rated match",
         },
         "waiting_place_unavailable_reason": None},
    ]
    response = SimpleNamespace(ErrorCode="", TripRequestId=2, OptionsJson=json.dumps(options))
    with patch("production.wsgi.app.director.create_business_service",
               return_value=_stub_service(response)):
        status, payload = _call(
            {"origin": "A", "destination": "B", "target_time": "18:30"}
        )
    assert status.startswith("200")
    earlier = payload["options"][1]
    assert earlier["waiting_place"]["name"] == "Cafe Central"
    assert earlier["waiting_place"]["rationale"] == "Closest highly-rated match"


def test_earlier_option_with_no_waiting_place_available():
    options = [
        {"label": "30min_earlier", "wait_minutes": 30, "departure_time": "18:50",
         "arrival_time": "19:20", "estimated_fare": 17.20,
         "waiting_place": None,
         "waiting_place_unavailable_reason": "No nearby waiting place found within 1.0 km"},
    ]
    response = SimpleNamespace(ErrorCode="", TripRequestId=3, OptionsJson=json.dumps(options))
    with patch("production.wsgi.app.director.create_business_service",
               return_value=_stub_service(response)):
        status, payload = _call(
            {"origin": "A", "destination": "B", "target_time": "18:30"}
        )
    assert status.startswith("200")
    option = payload["options"][0]
    assert option["waiting_place"] is None
    assert option["waiting_place_unavailable_reason"] == "No nearby waiting place found within 1.0 km"
