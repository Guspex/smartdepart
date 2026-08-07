"""Integration test for User Story 3: when multiple candidate waiting places exist, the
chosen one's rationale explains why it was picked over the others (tasks.md T037; spec.md
User Story 3 Acceptance Scenario 1).
"""
from production.hosts.bo_hybrid_rag_engine import BoHybridRagEngine


def test_rationale_differentiates_from_other_candidates():
    candidates = [
        {"id": 1, "name": "Cafe Central", "distance_km": 0.3, "rating": 4.6,
         "vector_score": 0.9, "keyword_score": 1.0, "final_score": 0.94},
        {"id": 2, "name": "Coworking Paulista", "distance_km": 0.8, "rating": 4.2,
         "vector_score": 0.7, "keyword_score": 0.0, "final_score": 0.42},
    ]
    best = candidates[0]

    rationale = BoHybridRagEngine._rationale(best, candidates)

    assert "0.3 km" in rationale
    assert "4.6" in rationale
    assert "1 other" in rationale


def test_rationale_is_non_empty_with_a_single_candidate():
    candidates = [
        {"id": 1, "name": "Cafe Central", "distance_km": 0.3, "rating": 4.6,
         "vector_score": 0.9, "keyword_score": 0.0, "final_score": 0.54},
    ]
    rationale = BoHybridRagEngine._rationale(candidates[0], candidates)
    assert rationale
    assert "0.3 km" in rationale
