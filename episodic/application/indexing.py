"""Trabajador de indexación desacoplada basado en outbox (IndexingWorker).

- Consume representaciones pendientes de la tabla SQLite `representations`.
- Genera embeddings usando los encoders correspondientes (sin mezclar espacios).
- Proyecta los vectores y metadatos en Qdrant.
- Marca el estado de cada representación (COMPLETED, FAILED) de forma atómica.
"""
from __future__ import annotations

import logging
import json
from typing import Optional

from episodic.domain.types import EvidenceState, IndexingState, RepresentationSpace
from episodic.application.temporal_index import timeline_payload
from episodic.ports.encoders import EpisodeTextEncoder, ImageEncoder
from episodic.ports.repository import EpisodicRepository, EvidenceStore
from episodic.ports.vector_index import VectorIndex

logger = logging.getLogger(__name__)


def episode_index_text(repository: EpisodicRepository, episode) -> str:
    """Construye la representacion textual derivada sin sustituir evidencias."""
    parts = [episode.title, episode.indexable_text]
    for observation in repository.list_observations_for_episode(episode.id):
        parts.append(observation.description or observation.content)
        attributes = {
            key: value
            for key, value in observation.attributes.items()
            if key not in {"keyframes", "generation"}
        }
        if attributes:
            parts.append(
                json.dumps(
                    attributes,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
    return "\n".join(part.strip() for part in parts if part and part.strip())


class IndexingWorker:
    """Procesador del outbox para indexación vectorial."""

    def __init__(
        self,
        repository: EpisodicRepository,
        evidence_store: EvidenceStore,
        vector_index: VectorIndex,
        text_encoder: EpisodeTextEncoder,
        image_encoder: Optional[ImageEncoder] = None,
    ) -> None:
        self.repository = repository
        self.evidence_store = evidence_store
        self.vector_index = vector_index
        self.text_encoder = text_encoder
        self.image_encoder = image_encoder

    def process_pending_outbox(self, batch_size: int = 50, raise_on_error: bool = False) -> int:
        """Procesa un lote de representaciones pendientes en el outbox."""
        pending = self.repository.list_pending_representations(limit=batch_size)
        if not pending:
            return 0

        logger.info("Procesando outbox de indexación: %d tareas pendientes", len(pending))
        processed_count = 0

        for rep in pending:
            try:
                # 1. Representación de texto de episodio (Nomic)
                if rep.space == RepresentationSpace.NOMIC_TEXT and rep.target_type == "episode":
                    episode = self.repository.get_episode(rep.target_id)
                    if not episode or not episode.is_visible:
                        logger.warning("Episodio objetivo %s no está visible, marcando STALE", rep.target_id)
                        self.repository.update_representation_state(rep.id, IndexingState.STALE)
                        processed_count += 1
                        continue

                    observations = self.repository.list_observations_for_episode(episode.id)
                    text = episode_index_text(self.repository, episode)
                    if not text:
                        text = "Episodio sin contenido textual"

                    vectors = self.text_encoder.embed_documents([text])
                    if not vectors:
                        raise ValueError("El encoder de texto devolvió un vector vacío")

                    vector = vectors[0]
                    point_id = f"ep_{episode.id}"
                    payload = {
                        "target_type": "episode",
                        "episode_id": episode.id,
                        "external_id": episode.external_id,
                        "title": episode.title,
                        "source_ids": episode.source_ids,
                        "tags": episode.tags,
                        "location": episode.location,
                        "environment_ids": episode.environment_ids,
                        "modalities": sorted({item.modality for item in observations}),
                        "entity_ids": sorted(
                            {item.entity_id for item in observations if item.entity_id}
                        ),
                        "observation_types": sorted(
                            {item.observation_type for item in observations}
                        ),
                        "episode_type": str(
                            episode.metadata.get("episode_type", "generic")
                        ),
                        "validation_states": sorted(
                            {item.validation_state.value for item in observations}
                        ),
                        "max_confidence": max(
                            (
                                value
                                for value in [
                                    episode.confidence,
                                    *(item.confidence for item in observations),
                                ]
                                if value is not None
                            ),
                            default=0.0,
                        ),
                        "observation_ids": [item.id for item in observations],
                        "evidence_reference_ids": list(
                            dict.fromkeys(
                                reference_id
                                for item in observations
                                for reference_id in item.evidence_reference_ids
                            )
                        ),
                        "is_visible": episode.is_visible,
                        "created_at": episode.created_at.isoformat(),
                    }
                    payload.update(timeline_payload(interval=episode.interval))

                    self.vector_index.upsert(
                        point_id=point_id,
                        target_id=episode.id,
                        vectors={"text": vector},
                        payload=payload,
                    )
                    self.repository.update_representation_state(
                        rep.id, IndexingState.INDEXED, qdrant_point_id=point_id
                    )
                    processed_count += 1

                # 2. Representación visual de evidencia (CLIP vision)
                elif rep.space == RepresentationSpace.CLIP_VISION and rep.target_type == "evidence":
                    if self.image_encoder is None:
                        logger.warning("ImageEncoder no configurado, posponiendo evidencia %s", rep.target_id)
                        continue

                    evidence = self.repository.get_evidence(rep.target_id)
                    if not evidence or evidence.state != EvidenceState.ACTIVE:
                        logger.warning("Evidencia objetivo %s no está activa, marcando STALE", rep.target_id)
                        self.repository.update_representation_state(rep.id, IndexingState.STALE)
                        processed_count += 1
                        continue

                    abs_path = self.evidence_store.resolve_path(evidence.internal_uri)
                    if not abs_path.exists():
                        logger.warning("Archivo de imagen no existe: %s, marcando FAILED", abs_path)
                        self.repository.update_representation_state(rep.id, IndexingState.FAILED)
                        processed_count += 1
                        continue

                    vectors = self.image_encoder.embed_images([str(abs_path)])
                    if not vectors:
                        raise ValueError("El encoder visual devolvió un vector vacío")

                    vector = vectors[0]
                    point_id = f"ev_{evidence.id}"
                    payload = {
                        "target_type": "evidence",
                        "evidence_id": evidence.id,
                        "source_id": evidence.source_id,
                        "source_ids": [evidence.source_id],
                        "session_id": evidence.session_id,
                        "is_visible": True,
                        "created_at": evidence.ingested_at.isoformat(),
                        "external_capture_id": evidence.external_capture_id,
                    }
                    episode_ids = self.repository.list_episode_ids_for_evidence(evidence.id)
                    payload["episode_ids"] = episode_ids
                    if len(episode_ids) == 1:
                        payload["episode_id"] = episode_ids[0]
                    linked_timelines = []
                    for episode_id in episode_ids:
                        linked_episode = self.repository.get_episode(episode_id)
                        if linked_episode is not None:
                            linked_timelines.append(
                                {
                                    "episode_id": episode_id,
                                    **timeline_payload(interval=linked_episode.interval),
                                }
                            )
                    payload["episode_timelines"] = linked_timelines

                    evidence_timeline = timeline_payload(instant=evidence.capture_time)
                    if not evidence_timeline["timeline_unknown"]:
                        payload["timeline_origin"] = "evidence_capture"
                        payload.update(evidence_timeline)
                    elif len(linked_timelines) == 1:
                        payload["timeline_origin"] = "episode_interval"
                        payload.update(
                            {
                                key: value
                                for key, value in linked_timelines[0].items()
                                if key != "episode_id"
                            }
                        )
                    else:
                        payload["timeline_origin"] = "unknown"
                        payload.update(evidence_timeline)

                    self.vector_index.upsert(
                        point_id=point_id,
                        target_id=evidence.id,
                        vectors={"image": vector},
                        payload=payload,
                    )
                    self.repository.update_representation_state(
                        rep.id, IndexingState.INDEXED, qdrant_point_id=point_id
                    )
                    processed_count += 1

                else:
                    logger.warning("Espacio o target no soportado: %s / %s", rep.space, rep.target_type)
                    self.repository.update_representation_state(rep.id, IndexingState.FAILED)

            except Exception as e:
                logger.error("Error indexando representación %s: %s", rep.id, e, exc_info=True)
                self.repository.update_representation_state(rep.id, IndexingState.FAILED)
                processed_count += 1
                if raise_on_error:
                    raise RuntimeError(f"Falló la representación {rep.id}") from e

        return processed_count
