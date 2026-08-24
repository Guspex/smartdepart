"""Unit tests for the "nº" abbreviation fix (research.md §18): Nominatim can't parse the
Brazilian "nº"/"n°" abbreviation for "número" and fails to resolve addresses containing it,
even when every other token is correct. `requests.get` is mocked so these run offline.
"""
from unittest.mock import MagicMock, patch

from production.adapters.geocoding_adapter import geocode


def _mock_response(results):
    response = MagicMock()
    response.json.return_value = results
    response.raise_for_status.return_value = None
    return response


@patch("production.adapters.geocoding_adapter.time.sleep")
@patch("production.adapters.geocoding_adapter.requests.get")
def test_numero_abbreviation_is_stripped_before_querying(mock_get, _mock_sleep):
    mock_get.return_value = _mock_response([{"lat": "-27.618", "lon": "-48.647"}])

    geocode("Rodovia BR 101 nº km 211, 7235 - Distrito Industrial, Sao Jose - SC")

    query_text = mock_get.call_args.kwargs["params"]["q"]
    assert "nº" not in query_text
    assert "km 211" in query_text


@patch("production.adapters.geocoding_adapter.time.sleep")
@patch("production.adapters.geocoding_adapter.requests.get")
def test_numero_abbreviation_variants_are_stripped(mock_get, _mock_sleep):
    mock_get.return_value = _mock_response([{"lat": "-27.618", "lon": "-48.647"}])

    for variant, expected in [
        ("nº 141", "Rua Exemplo, 141, Florianopolis"),
        ("n° 141", "Rua Exemplo, 141, Florianopolis"),
        ("N° 141", "Rua Exemplo, 141, Florianopolis"),
        ("n.º 141", "Rua Exemplo, 141, Florianopolis"),
    ]:
        mock_get.reset_mock()
        geocode(f"Rua Exemplo, {variant}, Florianopolis")
        query_text = mock_get.call_args.kwargs["params"]["q"]
        assert query_text == expected, f"for variant {variant!r}, got {query_text!r}"


@patch("production.adapters.geocoding_adapter.time.sleep")
@patch("production.adapters.geocoding_adapter.requests.get")
def test_address_without_numero_abbreviation_is_unchanged(mock_get, _mock_sleep):
    mock_get.return_value = _mock_response([{"lat": "-23.564", "lon": "-46.651"}])

    geocode("Av. Paulista, 1000, Sao Paulo")

    query_text = mock_get.call_args.kwargs["params"]["q"]
    assert query_text == "Av. Paulista, 1000, Sao Paulo"
