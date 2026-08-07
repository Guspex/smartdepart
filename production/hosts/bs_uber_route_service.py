"""BsUberRouteService — validates the inbound trip-request payload and forwards it to
BpRouteOrchestrator (constitution Principle I). Adapterless: fed by
production/wsgi/app.py via `director.create_business_service(...).process_input(...)`
(research.md §2), not by an inbound adapter.

Class name is underscore-free PascalCase (`BsUberRouteService`, not `BS_UberRouteService`)
— IRIS 2026.1 Build 234U was found to silently truncate brand-new class names at the first
underscore during compilation (research.md §12); this naming avoids that bug entirely.
"""
from __future__ import annotations

import os
import sys

from intersystems_pyprod import BusinessService

# See research.md §14: pyprod's generated OnInit only adds this file's own
# directory to sys.path, not the project root needed for `from production.X.Y
# import ...`. Add it explicitly so every deferred import below resolves.
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
from production.messages.schemas import RouteRecommendationMessage, TripRequestMessage  # noqa: E402
from production.observability.telemetry import log_event  # noqa: E402

iris_package_name = "UberRoute"

MAX_PAYLOAD_BYTES = 4096
ALLOWED_FIELDS = {"origin", "destination", "target_time"}


class BsUberRouteService(BusinessService):
    def on_process_input(self, input):
        """`input` is the parsed request dict handed in by production/wsgi/app.py."""
        error = self._validate(input)
        if error:
            log_event("BsUberRouteService", "request_received", outcome="error",
                       error_message=error)
            return self.OKStatus(), RouteRecommendationMessage(
                error_code="invalid_request", error_message=error
            )

        session_id = input.get("session_id", "")
        request = TripRequestMessage(
            session_id=session_id,
            origin=input["origin"],
            destination=input["destination"],
            target_time=input["target_time"],
        )

        status, response = self.send_request_sync("BpRouteOrchestrator", request)
        return status, response

    @staticmethod
    def _validate(input: dict) -> str:
        """FR-002 (required fields) + input hardening (tasks.md T044)."""
        if not isinstance(input, dict):
            return "request body must be a JSON object"

        import json

        if len(json.dumps(input)) > MAX_PAYLOAD_BYTES:
            return f"payload exceeds {MAX_PAYLOAD_BYTES} bytes"

        unexpected = set(input.keys()) - ALLOWED_FIELDS - {"session_id"}
        if unexpected:
            return f"unexpected field(s): {', '.join(sorted(unexpected))}"

        missing = [f for f in ("origin", "destination", "target_time") if not input.get(f)]
        if missing:
            return f"missing required field(s): {', '.join(missing)}"

        target_time = input["target_time"]
        parts = target_time.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            return "target_time must be HH:MM"
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return "target_time must be HH:MM"

        return ""
