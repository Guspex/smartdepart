"""Unit tests for the 30-minute waiting-place Business Rule (tasks.md T027; FR-005/FR-006).

Verifies the bidirectional |delta| > 30 behavior from spec.md User Story 2, Acceptance
Scenarios 1 and 2.
"""
from production.hosts.business_rules import waiting_place_should_be_suggested


def test_no_trigger_when_delta_is_zero():
    assert waiting_place_should_be_suggested(0) is False


def test_no_trigger_at_exactly_30_minutes():
    assert waiting_place_should_be_suggested(30) is False


def test_triggers_just_over_30_minutes_later():
    assert waiting_place_should_be_suggested(31) is True


def test_triggers_just_over_30_minutes_earlier():
    # Direction-agnostic: the rule takes an already-absolute delta (BpRouteOrchestrator
    # computes abs() before calling this), so -31 represents "31 minutes earlier".
    assert waiting_place_should_be_suggested(-31) is True


def test_no_trigger_at_exactly_minus_30_minutes():
    assert waiting_place_should_be_suggested(-30) is False


def test_triggers_far_outside_window():
    assert waiting_place_should_be_suggested(120) is True
