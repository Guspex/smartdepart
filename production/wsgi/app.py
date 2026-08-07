"""WSGI entrypoint for the frontend (`GET /`) and `POST /api/uber-route/recommend`
(contracts/bs_uber_route_service.md).

Deployed as an IRIS-native WSGI Web Application (IRIS 2024.2+, research.md §2). On each
API request, injects the validated JSON payload into the running production via
`director.create_business_service(...).process_input(...)` — BsUberRouteService is
adapterless, so this WSGI callable *is* its inbound adapter (constitution Principle I:
"expose its interface via the WSGI protocol").

The frontend (`static/index.html`) is a single self-contained page — no build step, no
framework — served directly from disk. It only talks to `/api/uber-route/recommend`.
"""
from __future__ import annotations

import json
import os
import uuid

from intersystems_pyprod import director

_SERVICE_CLASS = "UberRoute.BsUberRouteService"
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

_STATUS_BY_ERROR_CODE = {
    "invalid_request": "400 Bad Request",
    "location_not_found": "422 Unprocessable Entity",
    "prediction_unavailable": "503 Service Unavailable",
}


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD")

    if method == "GET" and path in ("/", "/index.html"):
        return _serve_index(start_response)

    if path != "/api/uber-route/recommend":
        return _respond(start_response, "404 Not Found",
                         {"error": "not_found", "message": f"No route for {path}"})

    if method != "POST":
        return _respond(start_response, "405 Method Not Allowed",
                         {"error": "method_not_allowed", "message": "Use POST"})

    try:
        length = int(environ.get("CONTENT_LENGTH", 0) or 0)
        body = environ["wsgi.input"].read(length) if length else b""
        payload = json.loads(body or b"{}")
    except (ValueError, TypeError):
        return _respond(start_response, "400 Bad Request",
                         {"error": "invalid_request", "message": "Request body must be valid JSON"})

    payload["session_id"] = str(uuid.uuid4())

    status, service = director.create_business_service(_SERVICE_CLASS)
    if not status:
        return _respond(start_response, "503 Service Unavailable",
                         {"error": "service_unavailable",
                          "message": "Could not reach BsUberRouteService"})

    _, response = service.process_input(payload)

    if getattr(response, "error_code", ""):
        http_status = _STATUS_BY_ERROR_CODE.get(response.error_code, "400 Bad Request")
        return _respond(start_response, http_status,
                         {"error": response.error_code, "message": response.error_message})

    body_dict = {
        "trip_request_id": getattr(response, "trip_request_id", 0),
        "recommended_time": response.recommended_time,
        "estimated_arrival_time": response.estimated_arrival_time,
        "estimated_fare": response.estimated_fare,
        "delta_minutes": response.delta_minutes,
        "waiting_place_suggested": response.waiting_place_suggested,
    }
    if response.waiting_place_suggested:
        if response.waiting_place_name:
            body_dict["waiting_place"] = {
                "name": response.waiting_place_name,
                "address": response.waiting_place_address,
                "category": response.waiting_place_category,
                "rating": response.waiting_place_rating,
                "distance_km": response.waiting_place_distance_km,
                "rationale": response.waiting_place_rationale,
            }
        else:
            body_dict["waiting_place"] = None
            body_dict["waiting_place_unavailable_reason"] = response.waiting_place_unavailable_reason
    else:
        body_dict["waiting_place"] = None

    return _respond(start_response, "200 OK", body_dict)


def _respond(start_response, status: str, body_dict: dict):
    body = json.dumps(body_dict).encode("utf-8")
    start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
    return [body]


def _serve_index(start_response):
    index_path = os.path.join(_STATIC_DIR, "index.html")
    try:
        with open(index_path, "rb") as f:
            body = f.read()
    except OSError:
        return _respond(start_response, "500 Internal Server Error",
                         {"error": "static_missing", "message": "index.html not found"})
    start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"),
                               ("Content-Length", str(len(body)))])
    return [body]
