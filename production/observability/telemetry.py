"""Structured logging + lightweight telemetry hooks shared by every interoperability host.

Every pyprod host calls `log_event(...)` (directly, or via the `timed_event` context
manager) at its key operations: request received, external call made, error raised,
Business Rule outcome — per constitution Principle V (Observability by Default).
Events are written to the IRIS Management Portal via IRISLog and also counted/timed
in-process so an OpenTelemetry-compatible exporter can scrape them without any change
to host code.
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from typing import Any, Optional

from intersystems_pyprod import IRISLog

_lock = threading.Lock()
_counters: dict[str, int] = defaultdict(int)
_durations_ms: dict[str, list[float]] = defaultdict(list)


def log_event(
    host: str,
    event: str,
    session_id: str = "",
    outcome: str = "ok",
    duration_ms: Optional[float] = None,
    **fields: Any,
) -> None:
    """Emit one structured log line and update in-process counters/histograms.

    `event` values used across this production: "request_received",
    "integratedml_call", "rag_call", "rule_outcome", "persisted", "error".
    """
    payload = {
        "host": host,
        "event": event,
        "session_id": session_id,
        "outcome": outcome,
        "duration_ms": duration_ms,
        **fields,
    }
    message = json.dumps(payload, default=str)

    if outcome == "error":
        IRISLog.Error(message)
    else:
        IRISLog.Info(message)

    with _lock:
        _counters[f"{host}.{event}.{outcome}"] += 1
        if duration_ms is not None:
            _durations_ms[f"{host}.{event}"].append(duration_ms)


def get_metrics_snapshot() -> dict[str, Any]:
    """Return current in-process counters/histograms for a metrics exporter to scrape."""
    with _lock:
        return {
            "counters": dict(_counters),
            "duration_ms_samples": {k: list(v) for k, v in _durations_ms.items()},
        }


class timed_event:
    """Context manager: times a block, then calls log_event on exit.

    Usage::

        with timed_event("BP_RouteOrchestrator", "integratedml_call", session_id=sid):
            ...  # do the work; raising inside marks outcome="error" automatically
    """

    def __init__(self, host: str, event: str, session_id: str = "", **fields: Any):
        self.host = host
        self.event = event
        self.session_id = session_id
        self.fields = fields
        self.outcome = "ok"
        self._start = 0.0

    def __enter__(self) -> "timed_event":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        duration_ms = (time.perf_counter() - self._start) * 1000
        if exc_type is not None:
            self.outcome = "error"
            self.fields["error_message"] = str(exc)
        log_event(
            self.host,
            self.event,
            session_id=self.session_id,
            outcome=self.outcome,
            duration_ms=duration_ms,
            **self.fields,
        )
        return False
