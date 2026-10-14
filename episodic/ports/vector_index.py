"""Contrato del índice vectorial derivado.

Qdrant mantiene una proyección reconstruible.
Un punto pendiente de borrado no vuelve a aparecer como evidencia.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from episodic.domain.types import RepresentationSpace


@dataclass
class VectorCandidate:
    """Resultado de una búsqueda vectorial (pre-reranking)."""
    point_id: str
    target_id: str          # episode_id o evidence_id
    score: float
    space: RepresentationSpace
    payload: Dict[str, Any]
    raw_score: Optional[float] = None


class VectorIndex(Protocol):
    """Índice vectorial derivado: Qdrant como proyección reconstruible."""

    def ensure_collection(self) -> None:
        """Crear la colección con vectores nombrados si no existe."""
        ...

    def upsert(
        self,
        point_id: str,
        target_id: str,
        vectors: Dict[str, List[float]],
        payload: Dict[str, Any],
    ) -> None:
        """Insertar o actualizar un punto."""
        ...

    def upsert_many(self, points: List[tuple]) -> None:
        """Varios puntos en una escritura: (point_id, target_id, vectors, payload)."""
        ...

    def delete_points(self, point_ids: List[str]) -> None: ...

    def search(
        self,
        query_vector: List[float],
        space: RepresentationSpace,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[VectorCandidate]: ...

    def search_hybrid(
        self,
        text_vector: Optional[List[float]],
        image_vector: Optional[List[float]],
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
        rrf_k: int = 60,
    ) -> List[VectorCandidate]:
        """Búsqueda multimodal con RRF entre espacios nombrados."""
        ...

    def drop_and_recreate(self) -> None:
        """Para reconstrucción: borrar y recrear la colección."""
        ...

    def create_staging_index(self) -> "VectorIndex":
        """Crear una proyección vacía y aislada para reconstrucción."""
        ...

    def activate_staging_index(self, staging: "VectorIndex") -> None:
        """Activar staging después de verificarlo."""
        ...

    def discard_staging_index(self, staging: "VectorIndex") -> None:
        """Eliminar una proyección staging fallida sin tocar la activa."""
        ...

    def count(self) -> int: ...
