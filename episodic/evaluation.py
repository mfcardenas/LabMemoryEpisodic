"""Métricas puras para evaluar la recuperación sobre corpus etiquetados."""

from __future__ import annotations

from typing import Iterable, Optional, Sequence


def recall_at_k(
    expected_ids: Iterable[str],
    retrieved_ids: Sequence[str],
    k: int,
) -> Optional[float]:
    """Calcula recall@k; una consulta negativa no tiene recall definido."""
    if k <= 0:
        raise ValueError("k debe ser mayor que cero")

    expected = set(expected_ids)
    if not expected:
        return None

    retrieved = set(retrieved_ids[:k])
    return len(expected & retrieved) / len(expected)


def precision_at_k(
    expected_ids: Iterable[str],
    retrieved_ids: Sequence[str],
    k: int,
) -> Optional[float]:
    """Calcula precision@k; las consultas negativas se evalúan por veredicto."""
    if k <= 0:
        raise ValueError("k debe ser mayor que cero")

    expected = set(expected_ids)
    if not expected:
        return None

    retrieved = list(dict.fromkeys(retrieved_ids[:k]))
    if not retrieved:
        return 0.0
    return len(expected & set(retrieved)) / len(retrieved)


def mean_defined(values: Iterable[Optional[float]]) -> Optional[float]:
    """Media aritmética ignorando métricas no definidas."""
    defined = [value for value in values if value is not None]
    if not defined:
        return None
    return sum(defined) / len(defined)


def reciprocal_rank(
    expected_ids: Iterable[str], retrieved_ids: Sequence[str]
) -> Optional[float]:
    """Reciproco del primer resultado relevante; indefinido para consultas negativas."""
    expected = set(expected_ids)
    if not expected:
        return None
    for rank, item_id in enumerate(retrieved_ids, start=1):
        if item_id in expected:
            return 1.0 / rank
    return 0.0


def mean_reciprocal_rank(values: Iterable[Optional[float]]) -> Optional[float]:
    """Media de reciprocal-rank ignorando consultas sin positivos esperados."""
    return mean_defined(values)


def latest_event_accuracy(expected_id: str, actual_id: Optional[str]) -> float:
    """Exactitud binaria de la ultima observacion recuperada."""
    return 1.0 if actual_id == expected_id else 0.0


def results_without_evidence(has_evidence: Iterable[bool]) -> int:
    """Numero de resultados sin evidencia verificable."""
    return sum(not value for value in has_evidence)
