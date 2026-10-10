import pytest

from episodic.evaluation import (
    latest_event_accuracy,
    mean_defined,
    mean_reciprocal_rank,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    results_without_evidence,
)


def test_recall_at_k_uses_unique_expected_matches() -> None:
    assert recall_at_k(["EP-1", "EP-2"], ["EP-2", "EP-X", "EP-2"], 3) == 0.5


def test_recall_is_undefined_for_negative_query() -> None:
    assert recall_at_k([], ["EP-X"], 10) is None
    assert precision_at_k([], ["EP-X"], 10) is None
    assert mean_defined([None, 0.5, 1.0]) == 0.75


def test_recall_rejects_non_positive_k() -> None:
    with pytest.raises(ValueError, match="mayor que cero"):
        recall_at_k(["EP-1"], ["EP-1"], 0)


def test_precision_at_k_uses_the_returned_prefix() -> None:
    assert precision_at_k(["EP-1", "EP-2"], ["EP-1", "EP-X", "EP-2"], 2) == 0.5
    assert precision_at_k(["EP-1"], [], 5) == 0.0


def test_phase7_metrics_are_explicit_and_independent() -> None:
    assert reciprocal_rank(["EP-2"], ["EP-X", "EP-2"]) == 0.5
    assert mean_reciprocal_rank([1.0, 0.5, None]) == 0.75
    assert latest_event_accuracy("OBS-2", "OBS-2") == 1.0
    assert latest_event_accuracy("OBS-2", "OBS-1") == 0.0
    assert results_without_evidence([True, False, True, False]) == 2
