"""The 30-minute waiting-place trigger — the feature's central Business Rule.

Implementation note (research.md §8): the original SPECS-001 request asks to "Usar
Business Rules no projeto", which in InterSystems terms usually points at the formal
`Ens.Rule.RuleSet` Rule Editor artifact. That requires either the Management Portal GUI
or hand-authored Rule XML validated interactively in the Rule Editor — authoring it
blind, with no way to exercise the Rule Editor's live validation in this environment,
risks shipping a syntactically-plausible but broken rule. research.md §8 pre-approved a
plain Python fallback for exactly this situation. This module IS that fallback: a single,
isolated, independently-unit-testable function — not inlined into BP_RouteOrchestrator —
so it is still a discrete "Business Rule" component in spirit, and can be swapped for a
real `Ens.Rule.RuleSet` (called from BP_RouteOrchestrator via Embedded Python's
`iris.cls(...)`) later without touching orchestration logic.
"""
from __future__ import annotations

THRESHOLD_MINUTES = 30


def waiting_place_should_be_suggested(delta_minutes: int) -> bool:
    """FR-005/FR-006: trigger a waiting-place suggestion iff |delta| > 30 minutes.

    `delta_minutes` must already be the absolute difference between the requested and
    recommended times (direction-agnostic — spec Acceptance Scenario US2.2).
    """
    return abs(delta_minutes) > THRESHOLD_MINUTES
