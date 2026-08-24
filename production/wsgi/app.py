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

# director.create_business_service() takes the production's config ITEM name
# (as declared in production.py's ServiceItem `name=`), not the fully-qualified
# ObjectScript class name — passing "UberRoute.BsUberRouteService" here fails
# with `ErrBusinessDispatchNameNotRegistered` (verified live; see research.md §14).
_SERVICE_NAME = "BsUberRouteService"
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

    status, service = director.create_business_service(_SERVICE_NAME)
    if not status:
        return _respond(start_response, "503 Service Unavailable",
                         {"error": "service_unavailable",
                          "message": "Could not reach BsUberRouteService"})

    _, response = service.process_input(payload)

    # `service.process_input()` (director._AdapterlessService) returns the raw
    # IRIS-side message object, not the Python-side RouteRecommendationMessage
    # — it does not go through pyprod's _createmessage() conversion. The raw
    # object's properties are PascalCase (how pyprod projects Column fields
    # into ObjectScript, e.g. `options_json` -> `OptionsJson`), not
    # the snake_case names used on the Python side. Verified live against
    # IRIS 2025.3 (see research.md §14) — using snake_case here silently
    # returned None/empty for every field.
    if getattr(response, "ErrorCode", ""):
        error_code = response.ErrorCode
        http_status = _STATUS_BY_ERROR_CODE.get(error_code, "400 Bad Request")
        return _respond(start_response, http_status,
                         {"error": error_code, "message": response.ErrorMessage})

    # options_json (research.md §20) is a plain %VarString field holding a JSON-serialized
    # list of the three departure options — see message schema docstring for why it's a
    # JSON string rather than a nested message (pyprod's JsonSerialize requires every
    # field to be a JSON-native scalar).
    body_dict = {
        "trip_request_id": getattr(response, "TripRequestId", 0),
        "options": json.loads(response.OptionsJson or "[]"),
    }

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
