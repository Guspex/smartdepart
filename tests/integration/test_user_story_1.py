"""Integration test for User Story 1's full happy path: a valid trip request produces a
recommended time + fare (tasks.md T016). BpRouteOrchestrator is reached through
BsUberRouteService.send_request_sync, which is mocked here so the test doesn't require a
running IRIS production — it verifies the BS -> BP hand-off contract in isolation.
"""
from unittest.mock import MagicMock

from production.hosts.bs_uber_route_service import BsUberRouteService
from production.messages.schemas import RouteRecommendationMessage, TripRequestMessage


def _make_service() -> BsUberRouteService:
    service = BsUberRouteService(iris_host_object=MagicMock())
    return service


def test_valid_request_is_forwarded_to_bp_route_orchestrator():
    service = _make_service()
    expected_response = RouteRecommendationMessage(
        options_json='[{"label": "ideal", "wait_minutes": 0, "departure_time": "18:05", '
                     '"arrival_time": "18:20", "estimated_fare": 27.90, '
                     '"waiting_place": null, "waiting_place_unavailable_reason": null}]',
    )
    service.send_request_sync = MagicMock(return_value=(1, expected_response))

    status, response = service.on_process_input(
        {"origin": "Av. Paulista, 1000", "destination": "Rua Augusta, 500", "target_time": "18:00"}
    )

    assert status == service.OKStatus()
    assert response is expected_response
    service.send_request_sync.assert_called_once()
    target, sent_request = service.send_request_sync.call_args[0]
    assert target == "BpRouteOrchestrator"
    assert isinstance(sent_request, TripRequestMessage)
    assert sent_request.origin == "Av. Paulista, 1000"
    assert sent_request.destination == "Rua Augusta, 500"
    assert sent_request.target_time == "18:00"


def test_invalid_request_never_reaches_bp_route_orchestrator():
    service = _make_service()
    service.send_request_sync = MagicMock()

    status, response = service.on_process_input(
        {"origin": "", "destination": "Rua Augusta, 500", "target_time": "18:00"}
    )

    assert response.error_code == "invalid_request"
    service.send_request_sync.assert_not_called()
