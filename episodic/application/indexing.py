"""Trabajador de indexación desacoplada basado en outbox (IndexingWorker).

- Consume representaciones pendientes de la tabla SQLite `representations`.
- Genera embeddings usando los encoders correspondientes (sin mezclar espacios).
- Proyecta los vectores y metadatos en Qdrant.
- Marca el estado de cada representación (COMPLETED, FAILED) de forma atómica.
"""
from __future__ import annotations

import logging
import json
import time
from typing import Optional

from episodic.domain.passages import episode_passages, passage_point_id
from episodic.domain.times import Interval, TimeSpec
from episodic.domain.types import EvidenceState, IndexingState, RepresentationSpace
from episodic.application.progress import note
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
        passage_batch_size: int = 64,
    ) -> None:
        self.repository = repository
        self.evidence_store = evidence_store
        self.vector_index = vector_index
        self.text_encoder = text_encoder
        self.image_encoder = image_encoder
        # Pasajes codificados y escritos por lote (un episodio largo son cientos).
        self.passage_batch_size = max(1, passage_batch_size)
        # Puntos escritos: la reconstrucción comprueba que todos llegaron.
        self.points_written = 0

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

                    note(f"codificando el texto del episodio con {_model_name(self.text_encoder)}")
                    started = time.monotonic()
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
                    self.points_written += 1
                    self.repository.update_representation_state(
                        rep.id, IndexingState.INDEXED, qdrant_point_id=point_id
                    )
                    note(f"episodio codificado e indexado en {time.monotonic() - started:.1f} s")
                    processed_count += 1

                # 1b. Pasajes del episodio (Nomic + texto completo)
                elif rep.space == RepresentationSpace.NOMIC_TEXT and rep.target_type == PASSAGES_TARGET:
                    episode = self.repository.get_episode(rep.target_id)
                    if not episode or not episode.is_visible:
                        self.vector_index.delete_points(
                            [passage_point_id(item) for item in self.repository.delete_episode_passages(rep.target_id)]
                        )
                        self.repository.update_representation_state(rep.id, IndexingState.STALE)
                        processed_count += 1
                        continue
                    count = self._index_passages(episode)
                    self.repository.update_representation_state(
                        rep.id, IndexingState.INDEXED, qdrant_point_id=f"passages:{count}"
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

                    note(f"codificando la imagen {abs_path.name} con {_model_name(self.image_encoder)}")
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
                    self.points_written += 1
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


    def _index_passages(self, episode) -> int:
        """Un punto y una fila de texto completo por pasaje del episodio: sus
        eventos y las observaciones con contenido que ningún evento recoge."""
        current_summary = self.repository.get_current_summary("episode", episode.id)
        passages = episode_passages(
            episode,
            self.repository.list_events(episode.id),
            self.repository.list_observations_for_episode(episode.id),
            [current_summary] if current_summary is not None else [],
        )
        removed = self.repository.replace_episode_passages(episode.id, passages)
        if removed:
            self.vector_index.delete_points([passage_point_id(item) for item in removed])
        if not passages:
            return 0

        note(f"codificando {len(passages)} pasaje(s) con {_model_name(self.text_encoder)}")
        started = time.monotonic()
        for start in range(0, len(passages), self.passage_batch_size):
            batch = passages[start:start + self.passage_batch_size]
            vectors = self.text_encoder.embed_documents([item.text for item in batch])
            if len(vectors) != len(batch):
                raise ValueError("El encoder de texto no devolvió un vector por pasaje")
            self.vector_index.upsert_many([
                (passage.point_id, passage.id, {"text": vector}, passage_payload(passage, episode))
                for passage, vector in zip(batch, vectors)
            ])
            self.points_written += len(batch)
            if len(passages) > self.passage_batch_size:
                note(f"{min(start + self.passage_batch_size, len(passages))}/{len(passages)} pasajes")
        note(f"{len(passages)} pasaje(s) indexados en {time.monotonic() - started:.1f} s")
        return len(passages)


def passage_payload(passage, episode) -> dict:
    """Carga útil del punto: lo que se filtra en Qdrant antes del top-k."""
    interval = None
    if passage.start_time is not None:
        interval = Interval(
            start=TimeSpec.from_utc(passage.start_time),
            end=TimeSpec.from_utc(passage.end_time or passage.start_time),
        )
    payload = {
        "target_type": "passage",
        "episode_id": episode.id,
        "passage_id": passage.id,
        "kind": passage.kind,
        "event_id": passage.event_id,
        "observation_id": passage.observation_id,
        "sequence_number": passage.sequence_number,
        "observation_types": [passage.kind],
        "modalities": [passage.modality] if passage.modality else [],
        "source_ids": [passage.source_id] if passage.source_id else [],
        "environment_ids": [passage.environment_id] if passage.environment_id else list(episode.environment_ids),
        "is_visible": True,
    }
    payload.update(timeline_payload(interval=interval))
    return payload


# Representación que agrupa los pasajes de un episodio (target_id = episodio).
PASSAGES_TARGET = "episode_passages"


def _model_name(encoder: object) -> str:
    info = getattr(encoder, "info", None)
    return getattr(info, "model_id", None) or type(encoder).__name__
