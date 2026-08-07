"""Unit tests for BS_UberRouteService payload validation (tasks.md T017; FR-002, T044)."""
from production.hosts.bs_uber_route_service import BS_UberRouteService


def test_valid_payload_passes():
    error = BS_UberRouteService._validate(
        {"origin": "A", "destination": "B", "target_time": "18:30"}
    )
    assert error == ""


def test_missing_origin_is_rejected():
    error = BS_UberRouteService._validate({"destination": "B", "target_time": "18:30"})
    assert "origin" in error


def test_missing_multiple_fields_are_reported():
    error = BS_UberRouteService._validate({"origin": "A"})
    assert "destination" in error
    assert "target_time" in error


def test_malformed_target_time_is_rejected():
    error = BS_UberRouteService._validate(
        {"origin": "A", "destination": "B", "target_time": "not-a-time"}
    )
    assert "target_time" in error


def test_out_of_range_target_time_is_rejected():
    error = BS_UberRouteService._validate(
        {"origin": "A", "destination": "B", "target_time": "25:99"}
    )
    assert "target_time" in error


def test_unexpected_field_is_rejected():
    error = BS_UberRouteService._validate(
        {"origin": "A", "destination": "B", "target_time": "18:30", "extra": "nope"}
    )
    assert "extra" in error


def test_session_id_is_allowed_without_error():
    error = BS_UberRouteService._validate(
        {"origin": "A", "destination": "B", "target_time": "18:30", "session_id": "abc"}
    )
    assert error == ""


def test_oversized_payload_is_rejected():
    error = BS_UberRouteService._validate(
        {"origin": "A" * 5000, "destination": "B", "target_time": "18:30"}
    )
    assert "exceeds" in error
