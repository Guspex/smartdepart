"""Integration tests for User Story 2's 3-option departure flow (research.md §20):
every response returns an "ideal", "30min_earlier" and "60min_earlier" option, and the two
earlier-departure options always carry a waiting-place suggestion (or an explanation why
none was found) — no longer conditional on a delta threshold.

BpRouteOrchestrator's calls to BoIntegratedMlPredictor/BoHybridRagEngine/geocoding are
mocked so this test exercises the orchestration logic in isolation.
"""
import json
from unittest.mock import MagicMock, patch

from production.hosts.bp_route_orchestrator import BpRouteOrchestrator
from production.messages.schemas import (
    FarePredictionResultMessage,
    TripRequestMessage,
    WaitingPlaceResultMessage,
)


def _make_bp() -> BpRouteOrchestrator:
    return BpRouteOrchestrator(iris_host_object=MagicMock())


def _fare_for(candidate_time: str) -> FarePredictionResultMessage:
    return FarePredictionResultMessage(candidate_time=candidate_time, predicted_fare=20.0, ok=True)


def test_earlier_options_carry_waiting_place_when_one_is_found():
    bp = _make_bp()

    def fake_send_sync(target, request):
        if target == "BoIntegratedMlPredictor":
            return 1, _fare_for(request.candidate_time)
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

    options = json.loads(response.options_json)
    assert [o["label"] for o in options] == ["ideal", "30min_earlier", "60min_earlier"]

    ideal = options[0]
    assert ideal["wait_minutes"] == 0
    assert ideal["waiting_place"] is None

    for option in options[1:]:
        assert option["wait_minutes"] in (30, 60)
        assert option["waiting_place"]["name"] == "Cafe Central"


def test_no_place_found_still_returns_all_options():
    bp = _make_bp()

    def fake_send_sync(target, request):
        if target == "BoIntegratedMlPredictor":
            return 1, _fare_for(request.candidate_time)
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

    # FR-010: recommendations still returned even with no waiting place.
    assert response.error_code == ""
    options = json.loads(response.options_json)
    assert len(options) == 3
    for option in options[1:]:
        assert option["waiting_place"] is None
        assert option["waiting_place_unavailable_reason"] == "No nearby waiting place found within 1.0 km"


def test_unresolvable_location_returns_error_before_any_prediction():
    bp = _make_bp()
    bp.send_request_sync = MagicMock()
    with patch("production.hosts.bp_route_orchestrator.geocode", return_value=None):
        request = TripRequestMessage(session_id="s3", origin="nowhere", destination="B",
                                      target_time="18:00")
        status, response = bp.on_request(request)

    assert response.error_code == "location_not_found"
    bp.send_request_sync.assert_not_called()


def test_implausible_geocoded_distance_returns_error_before_any_prediction():
    """research.md §21: a vague query like "SENAI, São José" can resolve to a same-named
    place hundreds of km away in a different city — treat that as an unresolved location
    rather than computing a nonsensical multi-hour "trip"."""
    bp = _make_bp()
    bp.send_request_sync = MagicMock()
    with patch(
        "production.hosts.bp_route_orchestrator.geocode",
        side_effect=[(-27.60, -48.58), (-29.25, -51.52)],  # ~341 km apart
    ):
        request = TripRequestMessage(session_id="s4", origin="A", destination="SENAI, Sao Jose",
                                      target_time="18:30")
        status, response = bp.on_request(request)

    assert response.error_code == "distance_out_of_range"
    bp.send_request_sync.assert_not_called()
