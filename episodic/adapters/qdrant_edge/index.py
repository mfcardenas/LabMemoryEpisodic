"""VectorIndex persistente sobre Qdrant Edge 0.7.x."""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from qdrant_edge import (
    CountRequest,
    Distance,
    EdgeConfig,
    EdgeShard,
    EdgeVectorParams,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    Point,
    Query,
    QueryRequest,
    RangeFloat,
    UpdateOperation,
)

from episodic.domain.types import RepresentationSpace
from episodic.ports.vector_index import VectorCandidate

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _sanitize_point_id(raw_id: str) -> str:
    """Edge admite enteros o UUID; el dominio conserva además el ID original."""
    try:
        return str(uuid.UUID(str(raw_id)))
    except (ValueError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, str(raw_id)))


class QdrantEdgeVectorIndex:
    """Shard embebido con generaciones y activación mediante puntero atómico."""

    def __init__(
        self,
        root_dir: str | Path,
        collection_name: str = "episodic_memory",
        text_dim: int = 768,
        image_dim: int = 512,
        *,
        _generation_name: Optional[str] = None,
        _managed: bool = True,
    ) -> None:
        if not _SAFE_NAME.fullmatch(collection_name):
            raise ValueError("collection_name solo admite letras, números, punto, guion y guion bajo")

        self.collection_name = collection_name
        self.text_dim = text_dim
        self.image_dim = image_dim
        self.root_dir = Path(root_dir).resolve()
        self.logical_dir = (self.root_dir / collection_name).resolve()
        self.generations_dir = self.logical_dir / "generations"
        self.active_file = self.logical_dir / "ACTIVE"
        self._lock = threading.RLock()
        self._managed = _managed
        self._closed = False

        self.generations_dir.mkdir(parents=True, exist_ok=True)
        if _generation_name is not None:
            self.generation_name = _generation_name
            self.generation_dir = self._generation_path(_generation_name)
            self.shard = self._create_generation(self.generation_dir)
        else:
            self.generation_name, self.generation_dir, self.shard = self._open_active()

    def _config(self) -> EdgeConfig:
        return EdgeConfig(
            vectors={
                "text": EdgeVectorParams(size=self.text_dim, distance=Distance.Cosine),
                "image": EdgeVectorParams(size=self.image_dim, distance=Distance.Cosine),
            }
        )

    def _generation_path(self, generation_name: str) -> Path:
        if not _SAFE_NAME.fullmatch(generation_name):
            raise ValueError(f"Nombre de generación Edge no válido: {generation_name}")
        path = (self.generations_dir / generation_name).resolve()
        path.relative_to(self.generations_dir.resolve())
        return path

    def _write_active(self, generation_name: str) -> None:
        temp = self.logical_dir / f".ACTIVE.{uuid.uuid4().hex}.tmp"
        temp.write_text(generation_name, encoding="utf-8")
        os.replace(temp, self.active_file)

    def _open_active(self):
        if self.active_file.exists():
            generation_name = self.active_file.read_text(encoding="utf-8").strip()
            generation_dir = self._generation_path(generation_name)
            if not generation_dir.is_dir():
                raise RuntimeError(f"La generación Edge activa no existe: {generation_dir}")
            return generation_name, generation_dir, self._load_generation(generation_dir)

        generation_name = f"gen_{uuid.uuid4().hex[:12]}"
        generation_dir = self._generation_path(generation_name)
        shard = self._create_generation(generation_dir)
        self._write_active(generation_name)
        return generation_name, generation_dir, shard

    @staticmethod
    def _load_generation(generation_dir: Path) -> EdgeShard:
        try:
            return EdgeShard.load(str(generation_dir))
        except Exception as exc:
            message = str(exc)
            if "WouldBlock" in message or "failed to open WAL" in message:
                raise RuntimeError(
                    "El shard Qdrant Edge ya está abierto por otro proceso; "
                    "use un único propietario (CLI o API) para este directorio"
                ) from exc
            raise

    def _create_generation(self, generation_dir: Path) -> EdgeShard:
        if generation_dir.exists():
            raise RuntimeError(f"La generación Edge ya existe: {generation_dir}")
        generation_dir.mkdir(parents=True, exist_ok=False)
        shard = EdgeShard.create(str(generation_dir), self._config())
        schemas = {
            "edge_is_visible": PayloadSchemaType.Keyword,
            "source_ids": PayloadSchemaType.Keyword,
            "environment_ids": PayloadSchemaType.Keyword,
            "modalities": PayloadSchemaType.Keyword,
            "entity_ids": PayloadSchemaType.Keyword,
            "observation_types": PayloadSchemaType.Keyword,
            "episode_type": PayloadSchemaType.Keyword,
            "validation_states": PayloadSchemaType.Keyword,
            "max_confidence": PayloadSchemaType.Float,
            "episode_id": PayloadSchemaType.Keyword,
            "target_type": PayloadSchemaType.Keyword,
            "timeline_start_ms": PayloadSchemaType.Float,
            "timeline_filter_end_ms": PayloadSchemaType.Float,
            "edge_timeline_unknown": PayloadSchemaType.Keyword,
        }
        for field_name, schema in schemas.items():
            shard.update(UpdateOperation.create_field_index(field_name, schema))
        shard.flush()
        return shard

    def ensure_collection(self) -> None:
        if self._closed:
            raise RuntimeError("El shard Qdrant Edge está cerrado")

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self.shard.flush()
                self.shard.close()
                self._closed = True

    def upsert(
        self,
        point_id: str,
        target_id: str,
        vectors: Dict[str, List[float]],
        payload: Dict[str, Any],
    ) -> None:
        clean_id = _sanitize_point_id(point_id)
        point_payload = dict(payload)
        point_payload.update({"target_id": target_id, "raw_point_id": point_id})
        point_payload.setdefault("is_visible", True)
        point_payload["edge_is_visible"] = (
            "true" if point_payload.get("is_visible") is True else "false"
        )
        point_payload["edge_timeline_unknown"] = (
            "true" if point_payload.get("timeline_unknown") is True else "false"
        )
        with self._lock:
            # Edge 0.7.2 no reemplaza de forma completa todos los campos de un
            # punto existente mediante upsert; delete+upsert mantiene idempotencia.
            self.shard.update(UpdateOperation.delete_points([clean_id]))
            self.shard.update(
                UpdateOperation.upsert_points(
                    [Point(id=clean_id, vector=vectors, payload=point_payload)]
                )
            )
            self.shard.flush()

    def upsert_many(self, points: List[tuple]) -> None:
        """Varios puntos con una sola escritura y un solo flush."""
        if not points:
            return
        prepared = []
        for point_id, target_id, vectors, payload in points:
            point_payload = dict(payload)
            point_payload.update({"target_id": target_id, "raw_point_id": point_id})
            point_payload.setdefault("is_visible", True)
            point_payload["edge_is_visible"] = "true" if point_payload.get("is_visible") is True else "false"
            point_payload["edge_timeline_unknown"] = "true" if point_payload.get("timeline_unknown") is True else "false"
            prepared.append(Point(id=_sanitize_point_id(point_id), vector=vectors, payload=point_payload))
        with self._lock:
            self.shard.update(UpdateOperation.delete_points([item.id for item in prepared]))
            self.shard.update(UpdateOperation.upsert_points(prepared))
            self.shard.flush()

    def delete_points(self, point_ids: List[str]) -> None:
        if not point_ids:
            return
        with self._lock:
            self.shard.update(
                UpdateOperation.delete_points([_sanitize_point_id(point_id) for point_id in point_ids])
            )
            self.shard.flush()

    def _build_filter(self, filters: Optional[Dict[str, Any]]) -> Filter:
        must = [FieldCondition(key="edge_is_visible", match=MatchValue("true"))]
        if filters:
            for key in (
                "source_ids",
                "environment_ids",
                "modalities",
                "entity_ids",
                "observation_types",
                "validation_states",
            ):
                values = filters.get(key)
                if not values:
                    continue
                must.append(
                    FieldCondition(
                        key=key,
                        match=(
                            MatchAny(list(values))
                            if isinstance(values, (list, tuple))
                            else MatchValue(values)
                        ),
                    )
                )
            for key in ("episode_id", "target_type"):
                if filters.get(key):
                    must.append(FieldCondition(key=key, match=MatchValue(filters[key])))

            if filters.get("episode_type"):
                must.append(
                    FieldCondition(
                        key="episode_type", match=MatchValue(filters["episode_type"])
                    )
                )
            if filters.get("min_confidence") is not None:
                must.append(
                    FieldCondition(
                        key="max_confidence",
                        range=RangeFloat(gte=float(filters["min_confidence"])),
                    )
                )

            query_start = filters.get("timeline_query_start_ms")
            query_end = filters.get("timeline_query_end_ms")
            if query_start is not None and query_end is not None:
                must.extend(
                    [
                        FieldCondition(
                            key="edge_timeline_unknown",
                            match=MatchValue("false"),
                        ),
                        FieldCondition(
                            key="timeline_start_ms",
                            range=RangeFloat(lt=float(query_end)),
                        ),
                        FieldCondition(
                            key="timeline_filter_end_ms",
                            range=RangeFloat(gt=float(query_start)),
                        ),
                    ]
                )
        # Los pasajes comparten el vector "text" con los episodios: solo
        # aparecen cuando se piden expresamente (target_type="passage").
        must_not = []
        if not (filters and filters.get("target_type")):
            must_not.append(FieldCondition(key="target_type", match=MatchValue("passage")))
        for kind in (filters or {}).get("exclude_kinds") or ():
            must_not.append(FieldCondition(key="kind", match=MatchValue(kind)))
        return Filter(must=must, must_not=must_not or None)

    def search(
        self,
        query_vector: List[float],
        space: RepresentationSpace,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[VectorCandidate]:
        vector_name = "text" if space == RepresentationSpace.NOMIC_TEXT else "image"
        with self._lock:
            points = self.shard.query(
                QueryRequest(
                    query=Query.Nearest(query_vector, using=vector_name),
                    filter=self._build_filter(filters),
                    limit=limit,
                    with_payload=True,
                )
            )
        return [
            VectorCandidate(
                point_id=str(point.id),
                target_id=(point.payload or {}).get("target_id", str(point.id)),
                score=float(point.score),
                space=space,
                payload=point.payload or {},
                raw_score=float(point.score),
            )
            for point in points
        ]

    def search_hybrid(
        self,
        text_vector: Optional[List[float]],
        image_vector: Optional[List[float]],
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
        rrf_k: int = 60,
    ) -> List[VectorCandidate]:
        text_candidates = (
            self.search(text_vector, RepresentationSpace.NOMIC_TEXT, filters, limit * 2)
            if text_vector is not None
            else []
        )
        image_candidates = (
            self.search(image_vector, RepresentationSpace.CLIP_TEXT, filters, limit * 2)
            if image_vector is not None
            else []
        )
        if not image_candidates:
            return text_candidates[:limit]
        if not text_candidates:
            return image_candidates[:limit]

        scores: Dict[str, float] = {}
        best: Dict[str, VectorCandidate] = {}
        for candidates in (text_candidates, image_candidates):
            for rank, candidate in enumerate(candidates, start=1):
                key = candidate.payload.get("episode_id", candidate.target_id)
                scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
                if key not in best or candidate.score > best[key].score:
                    best[key] = candidate

        results = []
        for key in sorted(scores, key=scores.get, reverse=True)[:limit]:
            original = best[key]
            results.append(
                VectorCandidate(
                    point_id=original.point_id,
                    target_id=original.target_id,
                    score=scores[key],
                    space=original.space,
                    payload=original.payload,
                    raw_score=original.raw_score,
                )
            )
        return results

    def count(self) -> int:
        with self._lock:
            return int(self.shard.count(CountRequest(exact=True)))

    def create_staging_index(self) -> "QdrantEdgeVectorIndex":
        return QdrantEdgeVectorIndex(
            root_dir=self.root_dir,
            collection_name=self.collection_name,
            text_dim=self.text_dim,
            image_dim=self.image_dim,
            _generation_name=f"staging_{uuid.uuid4().hex[:12]}",
            _managed=False,
        )

    def activate_staging_index(self, staging: "QdrantEdgeVectorIndex") -> None:
        if not isinstance(staging, QdrantEdgeVectorIndex):
            raise TypeError("El índice staging no es Qdrant Edge")
        if staging.logical_dir != self.logical_dir or staging._managed:
            raise ValueError("El índice staging no pertenece a esta colección Edge")

        with self._lock:
            staging.shard.flush()
            staging.shard.close()
            staging._closed = True
            new_shard = self._load_generation(staging.generation_dir)
            old_shard = self.shard
            old_generation_dir = self.generation_dir
            try:
                self._write_active(staging.generation_name)
            except Exception:
                new_shard.close()
                raise

            self.shard = new_shard
            self.generation_name = staging.generation_name
            self.generation_dir = staging.generation_dir
            old_shard.close()
            shutil.rmtree(old_generation_dir)

    def discard_staging_index(self, staging: "QdrantEdgeVectorIndex") -> None:
        if not isinstance(staging, QdrantEdgeVectorIndex):
            raise TypeError("El índice staging no es Qdrant Edge")
        if staging.logical_dir != self.logical_dir:
            raise ValueError("El índice staging no pertenece a esta colección Edge")
        if staging.generation_dir == self.generation_dir:
            return
        staging.close()
        if staging.generation_dir.exists():
            staging.generation_dir.resolve().relative_to(self.generations_dir.resolve())
            shutil.rmtree(staging.generation_dir)

    def drop_and_recreate(self) -> None:
        staging = self.create_staging_index()
        try:
            self.activate_staging_index(staging)
        except Exception:
            self.discard_staging_index(staging)
            raise
