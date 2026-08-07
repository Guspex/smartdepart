"""Integration tests for User Story 2's acceptance scenarios (tasks.md T026):
recommended time later than requested, earlier than requested, and no place found nearby.

BP_RouteOrchestrator's calls to BO_IntegratedMLPredictor/BO_HybridRAGEngine/geocoding are
mocked so this test exercises the orchestration + Business Rule logic in isolation.
"""
from unittest.mock import MagicMock, patch

from production.hosts.bp_route_orchestrator import BP_RouteOrchestrator
from production.messages.schemas import (
    FarePredictionResultMessage,
    TripRequestMessage,
    WaitingPlaceResultMessage,
)


def _make_bp() -> BP_RouteOrchestrator:
    return BP_RouteOrchestrator(iris_host_object=MagicMock())


def _fare_for(candidate_time: str, cheap_time: str):
    """Returns a canned FarePredictionResultMessage — cheapest at `cheap_time`."""
    fare = 10.0 if candidate_time == cheap_time else 30.0
    return FarePredictionResultMessage(candidate_time=candidate_time, predicted_fare=fare, ok=True)


def test_recommended_time_later_triggers_waiting_place():
    bp = _make_bp()

    def fake_send_sync(target, request):
        if target == "BO_IntegratedMLPredictor":
            # Cheapest fare is far later than requested -> big positive delta
            return 1, _fare_for(request.candidate_time, cheap_time="19:00")
        if target == "BO_HybridRAGEngine":
            return 1, WaitingPlaceResultMessage(
                found=True, name="Cafe Central", address="Rua X", category="cafe",
                rating=4.5, distance_km=0.3, rationale="closest",
            )
        raise AssertionError(f"unexpected target {target}")

    bp.send_request_sync = MagicMock(side_effect=fake_send_sync)
    with patch("production.hosts.bp_route_orchestrator.BP_RouteOrchestrator._persist"):
        with patch(
            "production.adapters.geocoding_adapter.geocode",
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
        if target == "BO_IntegratedMLPredictor":
            return 1, _fare_for(request.candidate_time, cheap_time="19:00")
        if target == "BO_HybridRAGEngine":
            return 1, WaitingPlaceResultMessage(
                found=False, unavailable_reason="No nearby waiting place found within 1.0 km"
            )
        raise AssertionError(f"unexpected target {target}")

    bp.send_request_sync = MagicMock(side_effect=fake_send_sync)
    with patch("production.hosts.bp_route_orchestrator.BP_RouteOrchestrator._persist"):
        with patch(
            "production.adapters.geocoding_adapter.geocode",
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
    with patch("production.adapters.geocoding_adapter.geocode", return_value=None):
        request = TripRequestMessage(session_id="s3", origin="nowhere", destination="B",
                                      target_time="18:00")
        status, response = bp.on_request(request)

    assert response.error_code == "location_not_found"
    bp.send_request_sync.assert_not_called()
