"""Adaptador Qdrant que implementa VectorIndex.

- Colección con vectores nombrados: "text" (768d) e "image" (512d).
- Filtros pre-topk en la consulta (visibilidad canónica, fuente, fechas).
- Fusión recíproca de rangos (RRF) para búsqueda multimodal.
- Reconstrucción atómica y mapping de IDs.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Union

from qdrant_client import QdrantClient, models

from episodic.domain.types import RepresentationSpace
from episodic.ports.vector_index import VectorCandidate

logger = logging.getLogger(__name__)


def _sanitize_point_id(raw_id: str) -> str:
    """Asegura que el point_id sea un UUID válido para Qdrant."""
    try:
        return str(uuid.UUID(raw_id))
    except (ValueError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, str(raw_id)))


class QdrantVectorIndex:
    """Implementación de VectorIndex sobre Qdrant (HTTP o in-memory)."""

    def __init__(
        self,
        client: Optional[QdrantClient] = None,
        host: str = "localhost",
        port: int = 6333,
        collection_name: str = "episodic_memory",
        text_dim: int = 768,
        image_dim: int = 512,
        use_memory: bool = False,
        use_alias: bool = True,
    ) -> None:
        self.collection_name = collection_name
        self.text_dim = text_dim
        self.image_dim = image_dim
        self.use_alias = use_alias

        if client is not None:
            self.client = client
        elif use_memory:
            self.client = QdrantClient(":memory:")
        else:
            self.client = QdrantClient(host=host, port=port)

        self.ensure_collection()

    def ensure_collection(self) -> None:
        """Crea la colección lógica y su primera generación si no existen."""
        collections = self.client.get_collections().collections
        existing_names = {c.name for c in collections}
        aliases = {item.alias_name: item.collection_name for item in self.client.get_aliases().aliases}

        if self.collection_name in aliases:
            self._ensure_payload_indexes(self.collection_name)
            return

        if self.collection_name in existing_names:
            # Compatibilidad con colecciones creadas antes del esquema de alias.
            self._ensure_payload_indexes(self.collection_name)
            return

        physical_name = self.collection_name
        if self.use_alias:
            physical_name = f"{self.collection_name}__gen_{uuid.uuid4().hex[:12]}"

        logger.info("Creando colección Qdrant: %s", physical_name)
        self._create_collection(physical_name)

        if self.use_alias:
            self.client.update_collection_aliases(
                change_aliases_operations=[
                    models.CreateAliasOperation(
                        create_alias=models.CreateAlias(
                            collection_name=physical_name,
                            alias_name=self.collection_name,
                        )
                    )
                ]
            )

        self._ensure_payload_indexes(self.collection_name)

    def _create_collection(self, collection_name: str) -> None:
        self.client.create_collection(
            collection_name=collection_name,
            vectors_config={
                "text": models.VectorParams(
                    size=self.text_dim,
                    distance=models.Distance.COSINE,
                ),
                "image": models.VectorParams(
                    size=self.image_dim,
                    distance=models.Distance.COSINE,
                ),
            },
        )

    def _ensure_payload_indexes(self, collection_name: str) -> None:
        """Crea índices de payload; Qdrant local los ignora de forma segura."""
        schemas = {
            "is_visible": models.PayloadSchemaType.BOOL,
            "source_ids": models.PayloadSchemaType.KEYWORD,
            "environment_ids": models.PayloadSchemaType.KEYWORD,
            "modalities": models.PayloadSchemaType.KEYWORD,
            "entity_ids": models.PayloadSchemaType.KEYWORD,
            "observation_types": models.PayloadSchemaType.KEYWORD,
            "episode_type": models.PayloadSchemaType.KEYWORD,
            "validation_states": models.PayloadSchemaType.KEYWORD,
            "max_confidence": models.PayloadSchemaType.FLOAT,
            "episode_id": models.PayloadSchemaType.KEYWORD,
            "target_type": models.PayloadSchemaType.KEYWORD,
            "kind": models.PayloadSchemaType.KEYWORD,
            "timeline_start_ms": models.PayloadSchemaType.INTEGER,
            "timeline_filter_end_ms": models.PayloadSchemaType.INTEGER,
            "timeline_unknown": models.PayloadSchemaType.BOOL,
        }
        for field_name, field_schema in schemas.items():
            try:
                self.client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=field_schema,
                )
            except Exception:
                # Puede existir ya o no estar soportado por el backend local.
                logger.debug("No se creó índice de payload %s", field_name, exc_info=True)

    def upsert(
        self,
        point_id: str,
        target_id: str,
        vectors: Dict[str, List[float]],
        payload: Dict[str, Any],
    ) -> None:
        """Inserta o actualiza un punto con vectores nombrados."""
        clean_id = _sanitize_point_id(point_id)
        point_payload = dict(payload)
        point_payload["target_id"] = target_id
        point_payload["raw_point_id"] = point_id

        # Asegurar que is_visible esté presente por defecto
        if "is_visible" not in point_payload:
            point_payload["is_visible"] = True

        point = models.PointStruct(
            id=clean_id,
            vector=vectors,
            payload=point_payload,
        )
        self.client.upsert(
            collection_name=self.collection_name,
            points=[point],
        )

    def upsert_many(self, points: List[tuple]) -> None:
        """Varios puntos en una petición: (point_id, target_id, vectors, payload).
        Un viaje HTTP por lote en vez de uno por punto (×20 al indexar)."""
        if not points:
            return
        structs = []
        for point_id, target_id, vectors, payload in points:
            point_payload = dict(payload)
            point_payload["target_id"] = target_id
            point_payload["raw_point_id"] = point_id
            point_payload.setdefault("is_visible", True)
            structs.append(models.PointStruct(id=_sanitize_point_id(point_id), vector=vectors, payload=point_payload))
        self.client.upsert(collection_name=self.collection_name, points=structs)

    def delete_points(self, point_ids: List[str]) -> None:
        """Elimina puntos por su lista de IDs."""
        if not point_ids:
            return
        clean_ids = [_sanitize_point_id(pid) for pid in point_ids]
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.PointIdsList(points=clean_ids),
        )

    def _build_filter(self, filters: Optional[Dict[str, Any]]) -> models.Filter:
        """Construye un filtro pre-topk de Qdrant."""
        must_conditions: List[models.Condition] = [
            # Siempre filtrar puntos no visibles canónicamente
            models.FieldCondition(
                key="is_visible",
                match=models.MatchValue(value=True),
            )
        ]

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
                if isinstance(values, (list, tuple)):
                    must_conditions.append(
                        models.FieldCondition(
                            key=key,
                            match=models.MatchAny(any=list(values)),
                        )
                    )
                else:
                    must_conditions.append(
                        models.FieldCondition(
                            key=key,
                            match=models.MatchValue(value=values),
                        )
                    )

            if "episode_id" in filters and filters["episode_id"]:
                must_conditions.append(
                    models.FieldCondition(
                        key="episode_id",
                        match=models.MatchValue(value=filters["episode_id"]),
                    )
                )

            if "target_type" in filters and filters["target_type"]:
                must_conditions.append(
                    models.FieldCondition(
                        key="target_type",
                        match=models.MatchValue(value=filters["target_type"]),
                    )
                )

            if filters.get("episode_type"):
                must_conditions.append(
                    models.FieldCondition(
                        key="episode_type",
                        match=models.MatchValue(value=filters["episode_type"]),
                    )
                )

            if filters.get("min_confidence") is not None:
                must_conditions.append(
                    models.FieldCondition(
                        key="max_confidence",
                        range=models.Range(gte=float(filters["min_confidence"])),
                    )
                )

            query_start = filters.get("timeline_query_start_ms")
            query_end = filters.get("timeline_query_end_ms")
            if query_start is not None and query_end is not None:
                # Solapamiento semiabierto: stored.start < query.end AND
                # query.start < stored.filter_end. Los puntos usan +1 ms solo
                # en filter_end, conservando start == end en el payload exacto.
                must_conditions.extend(
                    [
                        models.FieldCondition(
                            key="timeline_unknown",
                            match=models.MatchValue(value=False),
                        ),
                        models.FieldCondition(
                            key="timeline_start_ms",
                            range=models.Range(lt=query_end),
                        ),
                        models.FieldCondition(
                            key="timeline_filter_end_ms",
                            range=models.Range(gt=query_start),
                        ),
                    ]
                )

        # Los pasajes comparten el vector "text" con los episodios: solo
        # aparecen cuando se piden expresamente (target_type="passage").
        must_not: List[models.Condition] = []
        if not (filters and filters.get("target_type")):
            must_not.append(
                models.FieldCondition(key="target_type", match=models.MatchValue(value="passage"))
            )
        # Clases de pasaje que no se quieren (p. ej. los resúmenes, salvo petición).
        for kind in (filters or {}).get("exclude_kinds") or ():
            must_not.append(models.FieldCondition(key="kind", match=models.MatchValue(value=kind)))
        return models.Filter(must=must_conditions, must_not=must_not or None)

    def search(
        self,
        query_vector: List[float],
        space: RepresentationSpace,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[VectorCandidate]:
        """Búsqueda vectorial en el espacio nombrado correspondiente."""
        # Mapear RepresentationSpace al vector nombrado de Qdrant
        if space == RepresentationSpace.NOMIC_TEXT:
            vector_name = "text"
        else:
            vector_name = "image"

        qdrant_filter = self._build_filter(filters)

        try:
            # Compatibilidad con API query_points o search
            if hasattr(self.client, "query_points"):
                response = self.client.query_points(
                    collection_name=self.collection_name,
                    query=query_vector,
                    using=vector_name,
                    query_filter=qdrant_filter,
                    limit=limit,
                    with_payload=True,
                )
                scored_points = response.points
            else:
                scored_points = self.client.search(
                    collection_name=self.collection_name,
                    query_vector=(vector_name, query_vector),
                    query_filter=qdrant_filter,
                    limit=limit,
                    with_payload=True,
                )
        except Exception as e:
            logger.error("Error en búsqueda vectorial Qdrant: %s", e)
            return []

        candidates = []
        for p in scored_points:
            p_payload = p.payload or {}
            target_id = p_payload.get("target_id", str(p.id))
            score_val = float(p.score)
            candidates.append(
                VectorCandidate(
                    point_id=str(p.id),
                    target_id=target_id,
                    score=score_val,
                    space=space,
                    payload=p_payload,
                    raw_score=score_val,
                )
            )
        return candidates

    def search_hybrid(
        self,
        text_vector: Optional[List[float]],
        image_vector: Optional[List[float]],
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
        rrf_k: int = 60,
    ) -> List[VectorCandidate]:
        """Búsqueda multimodal con Reciprocal Rank Fusion (RRF).

        No suma scores brutos de Nomic y CLIP (espacios incomparables).
        Combina sus rankings preservando la trazabilidad de los scores originales.
        """
        text_candidates: List[VectorCandidate] = []
        image_candidates: List[VectorCandidate] = []

        if text_vector is not None:
            text_candidates = self.search(
                query_vector=text_vector,
                space=RepresentationSpace.NOMIC_TEXT,
                filters=filters,
                limit=limit * 2,
            )

        if image_vector is not None:
            image_candidates = self.search(
                query_vector=image_vector,
                space=RepresentationSpace.CLIP_TEXT,
                filters=filters,
                limit=limit * 2,
            )

        # Si solo hay una modalidad, devolvemos sus resultados directos
        if not image_candidates:
            return text_candidates[:limit]
        if not text_candidates:
            return image_candidates[:limit]

        # RRF Fusion agrupando por target_id (o episode_id si está en payload)
        # para evitar premiar un episodio con múltiples crops duplicados
        scores: Dict[str, float] = {}
        best_candidate: Dict[str, VectorCandidate] = {}

        for rank, c in enumerate(text_candidates, start=1):
            key = c.payload.get("episode_id", c.target_id)
            rrf_score = 1.0 / (rrf_k + rank)
            scores[key] = scores.get(key, 0.0) + rrf_score
            if key not in best_candidate or c.score > best_candidate[key].score:
                best_candidate[key] = c

        for rank, c in enumerate(image_candidates, start=1):
            key = c.payload.get("episode_id", c.target_id)
            rrf_score = 1.0 / (rrf_k + rank)
            scores[key] = scores.get(key, 0.0) + rrf_score
            if key not in best_candidate:
                best_candidate[key] = c

        # Ordenar por score RRF descendente
        sorted_keys = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)

        results: List[VectorCandidate] = []
        for key in sorted_keys[:limit]:
            orig = best_candidate[key]
            merged = VectorCandidate(
                point_id=orig.point_id,
                target_id=orig.target_id,
                score=scores[key],
                space=orig.space,
                payload=orig.payload,
                raw_score=orig.raw_score if orig.raw_score is not None else orig.score,
            )
            results.append(merged)

        return results

    def drop_and_recreate(self) -> None:
        """Compatibilidad: activa una generación nueva vacía mediante staging."""
        logger.warning("Recreando colección Qdrant mediante staging: %s", self.collection_name)
        staging = self.create_staging_index()
        try:
            self.activate_staging_index(staging)
        except Exception:
            self.discard_staging_index(staging)
            raise

    def create_staging_index(self) -> "QdrantVectorIndex":
        staging_name = f"{self.collection_name}__staging_{uuid.uuid4().hex[:12]}"
        return QdrantVectorIndex(
            client=self.client,
            collection_name=staging_name,
            text_dim=self.text_dim,
            image_dim=self.image_dim,
            use_alias=False,
        )

    def activate_staging_index(self, staging: "QdrantVectorIndex") -> None:
        if not isinstance(staging, QdrantVectorIndex) or staging.client is not self.client:
            raise TypeError("El índice staging no pertenece al mismo backend Qdrant")

        staging_name = staging.collection_name
        collection_names = {item.name for item in self.client.get_collections().collections}
        if staging_name not in collection_names:
            raise RuntimeError(f"No existe la colección staging: {staging_name}")

        alias_map = {
            item.alias_name: item.collection_name
            for item in self.client.get_aliases().aliases
        }
        previous_physical = alias_map.get(self.collection_name)

        if self.collection_name in collection_names:
            # Migración única desde el formato legacy (colección lógica directa).
            self.client.delete_collection(self.collection_name)
            operations = []
        else:
            operations = []
            if previous_physical:
                operations.append(
                    models.DeleteAliasOperation(
                        delete_alias=models.DeleteAlias(alias_name=self.collection_name)
                    )
                )

        operations.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(
                    collection_name=staging_name,
                    alias_name=self.collection_name,
                )
            )
        )
        self.client.update_collection_aliases(change_aliases_operations=operations)

        # El cambio de alias ya es visible; la generación anterior se elimina
        # después, nunca antes de verificar staging.
        if previous_physical and previous_physical != staging_name:
            self.client.delete_collection(previous_physical)

    def discard_staging_index(self, staging: "QdrantVectorIndex") -> None:
        if not isinstance(staging, QdrantVectorIndex) or staging.client is not self.client:
            raise TypeError("El índice staging no pertenece al mismo backend Qdrant")
        active_targets = {
            item.collection_name
            for item in self.client.get_aliases().aliases
            if item.alias_name == self.collection_name
        }
        if staging.collection_name in active_targets:
            return
        collection_names = {item.name for item in self.client.get_collections().collections}
        if staging.collection_name in collection_names:
            self.client.delete_collection(staging.collection_name)

    def count(self) -> int:
        """Devuelve el total de puntos en la colección."""
        try:
            res = self.client.count(self.collection_name)
            return res.count
        except Exception:
            return 0


def logical_collection_members(client: QdrantClient, collection_name: str) -> tuple[list[str], bool]:
    """Colecciones físicas que pertenecen a la colección lógica y si su alias existe.

    Son la propia colección (esquema anterior a los alias), sus generaciones
    ``__gen_*``, las ``__staging_*`` que quedaran a medias y la que apunte el
    alias. Ninguna otra colección del servidor se considera.
    """
    alias_targets = [
        item.collection_name for item in client.get_aliases().aliases if item.alias_name == collection_name
    ]
    prefixes = (f"{collection_name}__gen_", f"{collection_name}__staging_")
    members = [
        item.name
        for item in client.get_collections().collections
        if item.name == collection_name or item.name.startswith(prefixes)
    ]
    members += [name for name in alias_targets if name not in members]
    return sorted(members), bool(alias_targets)


def drop_logical_collection(client: QdrantClient, collection_name: str) -> list[str]:
    """Borra el alias y todas las colecciones físicas de la colección lógica."""
    members, has_alias = logical_collection_members(client, collection_name)
    if has_alias:
        client.update_collection_aliases(
            change_aliases_operations=[
                models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=collection_name))
            ]
        )
    for name in members:
        client.delete_collection(name)
    return members
