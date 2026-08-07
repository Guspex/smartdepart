"""Unit tests for the arrival-deadline -> naive-departure math in BP_RouteOrchestrator,
added when target_time was clarified to mean "when the rider needs to arrive" rather than
a departure time.
"""
from unittest.mock import patch

from production.hosts.bp_route_orchestrator import (
    _add_minutes,
    _estimate_travel_minutes,
    _format_hhmm,
)


def test_estimate_travel_minutes_scales_with_distance():
    with patch("production.hosts.bp_route_orchestrator._congestion_factor", return_value=1.0):
        short = _estimate_travel_minutes(distance_km=5.0, hour=10, day_of_week=1)
        long = _estimate_travel_minutes(distance_km=20.0, hour=10, day_of_week=1)
    assert long > short


def test_estimate_travel_minutes_scales_with_congestion():
    with patch("production.hosts.bp_route_orchestrator._congestion_factor", side_effect=[1.0, 2.0]):
        light = _estimate_travel_minutes(distance_km=10.0, hour=10, day_of_week=1)
    with patch("production.hosts.bp_route_orchestrator._congestion_factor", return_value=2.0):
        heavy = _estimate_travel_minutes(distance_km=10.0, hour=10, day_of_week=1)
    assert heavy > light


def test_naive_departure_is_arrival_minus_travel_time():
    arrival = (14, 0)  # need to arrive at 14:00
    with patch("production.hosts.bp_route_orchestrator._congestion_factor", return_value=1.0):
        duration = _estimate_travel_minutes(distance_km=10.0, hour=14, day_of_week=1)
    naive_departure = _add_minutes(arrival[0], arrival[1], -duration)
    # Leaving `duration` minutes before 14:00 should land you back at 14:00.
    arrived_back = _add_minutes(naive_departure[0], naive_departure[1], duration)
    assert _format_hhmm(*arrived_back) == "14:00"
    # And the naive departure must be strictly before the arrival deadline.
    assert naive_departure[0] * 60 + naive_departure[1] < 14 * 60
