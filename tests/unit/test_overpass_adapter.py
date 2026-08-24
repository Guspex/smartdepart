"""Unit tests for the Overpass API adapter (research.md §22): live nearby-place lookup
that replaces relying only on a fixed seed dataset. `requests.post` is mocked so these
run offline.
"""
from unittest.mock import MagicMock, patch

import requests

from production.adapters.overpass_adapter import find_nearby_places, warm_up


def _mock_response(elements):
    response = MagicMock()
    response.json.return_value = {"elements": elements}
    response.raise_for_status.return_value = None
    return response


@patch("production.adapters.overpass_adapter.time.sleep")
@patch("production.adapters.overpass_adapter.requests.post")
def test_parses_cafe_and_bakery_elements(mock_post, _mock_sleep):
    mock_post.return_value = _mock_response([
        {
            "lat": -27.5975, "lon": -48.5835,
            "tags": {"amenity": "cafe", "name": "Cafe X", "addr:street": "Rua Y",
                      "addr:housenumber": "10", "internet_access": "wlan"},
        },
        {
            "lat": -27.5980, "lon": -48.5840,
            "tags": {"shop": "bakery", "name": "Padaria Z"},
        },
    ])

    places = find_nearby_places(-27.599, -48.584, radius_km=1.0)

    assert len(places) == 2
    assert places[0]["name"] == "Cafe X"
    assert places[0]["category"] == "cafe"
    assert "wifi" in places[0]["description"]
    assert places[1]["name"] == "Padaria Z"
    assert places[1]["category"] == "bakery"


@patch("production.adapters.overpass_adapter.time.sleep")
@patch("production.adapters.overpass_adapter.requests.post")
def test_unnamed_elements_are_skipped(mock_post, _mock_sleep):
    mock_post.return_value = _mock_response([
        {"lat": -27.5, "lon": -48.5, "tags": {"amenity": "cafe"}},  # no "name"
    ])

    places = find_nearby_places(-27.599, -48.584, radius_km=1.0)

    assert places == []


@patch("production.adapters.overpass_adapter.time.sleep")
@patch("production.adapters.overpass_adapter.requests.post")
def test_network_failure_returns_empty_list_not_an_exception(mock_post, _mock_sleep):
    mock_post.side_effect = requests.exceptions.ConnectionError("boom")

    places = find_nearby_places(-27.599, -48.584, radius_km=1.0)

    assert places == []


@patch("production.adapters.overpass_adapter.requests.get")
def test_warm_up_never_raises_on_network_failure(mock_get):
    mock_get.side_effect = requests.exceptions.Timeout("boom")

    warm_up()  # must not raise
