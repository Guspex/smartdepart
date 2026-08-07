"""Integration tests for User Story 2's acceptance scenarios (tasks.md T026):
recommended time later than requested, earlier than requested, and no place found nearby.

BpRouteOrchestrator's calls to BoIntegratedMlPredictor/BoHybridRagEngine/geocoding are
mocked so this test exercises the orchestration + Business Rule logic in isolation.
"""
from unittest.mock import MagicMock, patch

from production.hosts.bp_route_orchestrator import BpRouteOrchestrator
from production.messages.schemas import (
    FarePredictionResultMessage,
    TripRequestMessage,
    WaitingPlaceResultMessage,
)


def _make_bp() -> BpRouteOrchestrator:
    return BpRouteOrchestrator(iris_host_object=MagicMock())


def _fare_favoring_latest_candidate(candidate_time: str) -> FarePredictionResultMessage:
    """Cheapest fare goes to whichever candidate is latest in the day — baseline-agnostic
    (BpRouteOrchestrator scans candidates around a computed naive departure, not around
    the raw target_time, so tests can't assume a specific absolute candidate string)."""
    hour, minute = (int(p) for p in candidate_time.split(":"))
    minutes_of_day = hour * 60 + minute
    fare = 30.0 - (minutes_of_day * 0.01)
    return FarePredictionResultMessage(candidate_time=candidate_time, predicted_fare=fare, ok=True)


def test_recommended_time_later_triggers_waiting_place():
    bp = _make_bp()

    def fake_send_sync(target, request):
        if target == "BoIntegratedMlPredictor":
            # Cheapest fare is the latest candidate (largest positive offset scanned,
            # +60 min) -> a big positive delta from the naive departure baseline.
            return 1, _fare_favoring_latest_candidate(request.candidate_time)
        if target == "BoHybridRagEngine":
            return 1, WaitingPlaceResultMessage(
                found=True, name="Cafe Central", address="Rua X", category="cafe",
                rating=4.5, distance_km=0.3, rationale="closest",
            )
        raise AssertionError(f"unexpected target {target}")

    bp.send_request_sync = MagicMock(side_effect=fake_send_sync)
    with patch("production.hosts.bp_route_orchestrator.BpRouteOrchestrator._persist"):
        with patch(
            "production.hosts.bp_route_orchestrator.geocode",
            side_effect=[(-23.55, -46.66), (-23.56, -46.65)],
        ):
            request = TripRequestMessage(session_id="s1", origin="A", destination="B",
                                          target_time="18:00")
            status, response = bp.on_request(request)

    assert response.waiting_place_suggested is True
    assert response.waiting_place_name == "Cafe Central"


def test_no_place_found_still_returns_recommendation():
    bp = _make_bp()

    def fake_send_sync(target, request):
        if target == "BoIntegratedMlPredictor":
            return 1, _fare_favoring_latest_candidate(request.candidate_time)
        if target == "BoHybridRagEngine":
            return 1, WaitingPlaceResultMessage(
                found=False, unavailable_reason="No nearby waiting place found within 1.0 km"
            )
        raise AssertionError(f"unexpected target {target}")

    bp.send_request_sync = MagicMock(side_effect=fake_send_sync)
    with patch("production.hosts.bp_route_orchestrator.BpRouteOrchestrator._persist"):
        with patch(
            "production.hosts.bp_route_orchestrator.geocode",
            side_effect=[(-23.55, -46.66), (-23.56, -46.65)],
        ):
            request = TripRequestMessage(session_id="s2", origin="A", destination="B",
                                          target_time="18:00")
            status, response = bp.on_request(request)

    # FR-010: recommendation still returned even with no waiting place.
    assert response.error_code == ""
    assert response.recommended_time != ""
    assert response.waiting_place_suggested is True
    assert response.waiting_place_name == ""
    assert response.waiting_place_unavailable_reason == "No nearby waiting place found within 1.0 km"


def test_unresolvable_location_returns_error_before_any_prediction():
    bp = _make_bp()
    bp.send_request_sync = MagicMock()
    with patch("production.hosts.bp_route_orchestrator.geocode", return_value=None):
        request = TripRequestMessage(session_id="s3", origin="nowhere", destination="B",
                                      target_time="18:00")
        status, response = bp.on_request(request)

    assert response.error_code == "location_not_found"
    bp.send_request_sync.assert_not_called()
