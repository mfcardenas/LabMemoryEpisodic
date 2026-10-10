"""Servicio de ciclo de vida episódico (LifecycleService).

- Anotaciones versionadas: cada enriquecimiento es una revisión inmutable con supersedes_id.
- Eliminación con visibilidad canónica y purga segura de blobs huérfanos.
- Reconstrucción no destructiva de la proyección vectorial en Qdrant.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Sequence

from episodic.application.indexing import IndexingWorker
from episodic.domain.entities import Annotation, Representation
from episodic.domain.types import (
    EvidenceState,
    IndexingState,
    ProvenanceKind,
    RepresentationSpace,
)
from episodic.ports.repository import EpisodicRepository, EvidenceStore
from episodic.ports.vector_index import VectorIndex

logger = logging.getLogger(__name__)


class LifecycleService:
    """Servicio de aplicación para ciclo de vida, anotaciones y mantenimiento."""

    def __init__(
        self,
        repository: EpisodicRepository,
        evidence_store: EvidenceStore,
        vector_index: VectorIndex,
        nomic_model_id: str = "nomic-ai/nomic-embed-text-v1.5",
        clip_vision_model_id: str = "Qdrant/clip-ViT-B-32-vision",
    ) -> None:
        self.repository = repository
        self.evidence_store = evidence_store
        self.vector_index = vector_index
        self.nomic_model_id = nomic_model_id
        self.clip_vision_model_id = clip_vision_model_id

    def add_annotation(
        self,
        episode_id: str,
        content: str,
        author: str = "user",
        provenance_kind: ProvenanceKind = ProvenanceKind.HUMAN_DECLARATION,
        evidence_ids: Optional[List[str]] = None,
        tool_version: Optional[str] = None,
        reindex: bool = True,
    ) -> Annotation:
        """Crea una nueva anotación versionada para un episodio.

        No sobreescribe la anotación anterior; incrementa revision_number y
        apunta a supersedes_id.
        """
        episode = self.repository.get_episode(episode_id)
        if not episode:
            raise ValueError(f"Episodio no encontrado: {episode_id}")

        latest = self.repository.get_latest_annotation(episode_id)
        revision = (latest.revision_number + 1) if latest else 1
        supersedes = latest.id if latest else None

        ann = Annotation(
            episode_id=episode_id,
            revision_number=revision,
            content=content,
            provenance_kind=provenance_kind,
            author=author,
            tool_version=tool_version,
            evidence_ids=evidence_ids or [],
            supersedes_id=supersedes,
        )
        self.repository.save_annotation(ann)
        logger.info(
            "Anotación rev %d creada para episodio %s (supersedes: %s)",
            revision,
            episode_id,
            supersedes,
        )

        if reindex:
            # Enriquecer el texto indexable del episodio y encolar reindexación
            updated_text = f"{episode.indexable_text} [Anotación rev{revision}: {content}]"
            episode.indexable_text = updated_text
            self.repository.save_episode(episode)

            rep = Representation(
                target_type="episode",
                target_id=episode_id,
                space=RepresentationSpace.NOMIC_TEXT,
                model_id=self.nomic_model_id,
                model_revision="v1.5",
                dimension=768,
                indexing_state=IndexingState.PENDING,
            )
            self.repository.save_representation(rep)

        return ann

    def delete_episode(self, episode_id: str) -> bool:
        """Elimina lógicamente un episodio y purga medios que hayan quedado huérfanos."""
        episode = self.repository.get_episode(episode_id)
        if not episode:
            return False

        # 1. Cambiar visibilidad canónica en SQLite
        self.repository.set_episode_visibility(episode_id, False)

        # 2. Eliminar punto de Qdrant para que no aparezca en búsquedas
        try:
            self.vector_index.delete_points([f"ep_{episode_id}"])
        except Exception as e:
            logger.warning("Error eliminando punto de Qdrant para episodio %s: %s", episode_id, e)

        # 3. Comprobar si las evidencias asociadas quedan huérfanas
        for ev_id in episode.evidence_ids:
            ev = self.repository.get_evidence(ev_id)
            if ev and ev.state == EvidenceState.ACTIVE:
                # Comprobar si quedan referencias en otros episodios
                # En un diseño riguroso comprobamos si algún episodio visible aún la usa
                episodes_with_ev, _ = self.repository.list_episodes(limit=1000)
                still_used = any(
                    ev_id in ep.evidence_ids
                    for ep in episodes_with_ev
                    if ep.id != episode_id and ep.is_visible
                )
                if not still_used:
                    self.repository.update_evidence_state(ev_id, EvidenceState.DELETED)
                    # Purgar también punto visual en Qdrant si existía
                    self.vector_index.delete_points([f"ev_{ev_id}"])
                    # Purgar archivo físico si no tiene más referencias
                    self.evidence_store.delete_if_unreferenced(ev.internal_uri, ev.content_hash)

        logger.info("Episodio %s eliminado canónicamente y puntos removidos de Qdrant", episode_id)
        return True

    def index_episodes(
        self,
        worker: IndexingWorker,
        episode_ids: Sequence[str],
        removed_episode_ids: Sequence[str] = (),
    ) -> int:
        """Indexación incremental por outbox: solo los episodios afectados.

        Sustituye a la reconstrucción completa tras cada captura. Los puntos de
        episodios retirados se borran y sus representaciones quedan STALE.
        """
        if removed_episode_ids:
            self.vector_index.delete_points([f"ep_{item}" for item in removed_episode_ids])
            for episode_id in removed_episode_ids:
                for rep in self.repository.list_representations_for_target(episode_id):
                    self.repository.update_representation_state(rep.id, IndexingState.STALE)
        for episode_id in episode_ids:
            self._queue_representation(self._episode_text_representation(episode_id))

        total = 0
        while processed := worker.process_pending_outbox(batch_size=50):
            total += processed
        return total

    def _episode_text_representation(self, episode_id: str) -> Representation:
        return Representation(
            target_type="episode",
            target_id=episode_id,
            space=RepresentationSpace.NOMIC_TEXT,
            model_id=self.nomic_model_id,
            model_revision="v1.5",
            dimension=768,
            indexing_state=IndexingState.PENDING,
        )

    def _queue_representation(self, rep: Representation) -> None:
        """Reutiliza la representación compatible o crea una nueva en el outbox."""
        compatible = [
            current
            for current in self.repository.list_representations_for_target(rep.target_id)
            if current.target_type == rep.target_type
            and current.space == rep.space
            and current.model_id == rep.model_id
            and current.model_revision == rep.model_revision
            and current.dimension == rep.dimension
        ]
        if compatible:
            selected = compatible[-1]
            self.repository.update_representation_state(selected.id, IndexingState.PENDING)
            for duplicate in compatible[:-1]:
                self.repository.update_representation_state(duplicate.id, IndexingState.STALE)
        else:
            self.repository.save_representation(rep)

    def rebuild_index(self, worker: IndexingWorker) -> int:
        """Reconstruye la proyección sin borrar primero el índice operativo.

        Los puntos existentes permanecen disponibles si un encoder o un upsert
        falla. Las representaciones compatibles se reutilizan para no inflar el
        outbox en cada ejecución.
        """
        logger.info("Iniciando reconstrucción no destructiva de la proyección vectorial...")

        # 1. Obtener todos los episodios visibles mediante paginación.
        episodes = []
        cursor = None
        while True:
            page, next_cursor = self.repository.list_episodes(limit=100, cursor=cursor)
            episodes.extend(page)
            if not next_cursor:
                break
            cursor = next_cursor

        # 2. Reutilizar o crear una representación por target/espacio/modelo.
        queued_keys = set()

        def queue_representation(rep: Representation) -> None:
            key = (rep.target_type, rep.target_id, rep.space, rep.model_id, rep.model_revision)
            if key in queued_keys:
                return
            queued_keys.add(key)
            self._queue_representation(rep)

        for ep in episodes:
            queue_representation(self._episode_text_representation(ep.id))

            # Encolar evidencias de imagen
            for ev_id in ep.evidence_ids:
                ev = self.repository.get_evidence(ev_id)
                if ev and ev.state == EvidenceState.ACTIVE:
                    rep_img = Representation(
                        target_type="evidence",
                        target_id=ev.id,
                        space=RepresentationSpace.CLIP_VISION,
                        model_id=self.clip_vision_model_id,
                        model_revision="v1",
                        dimension=512,
                        indexing_state=IndexingState.PENDING,
                    )
                    queue_representation(rep_img)

        # 3. Construir una generación aislada. El worker original conserva sus
        # encoders, pero ningún upsert alcanza la colección activa.
        staging_index = self.vector_index.create_staging_index()
        staging_worker = IndexingWorker(
            repository=worker.repository,
            evidence_store=worker.evidence_store,
            vector_index=staging_index,
            text_encoder=worker.text_encoder,
            image_encoder=worker.image_encoder,
        )
        total_indexed = 0
        activated = False
        try:
            while True:
                processed = staging_worker.process_pending_outbox(
                    batch_size=50,
                    raise_on_error=True,
                )
                if processed == 0:
                    break
                total_indexed += processed

            expected_points = len(queued_keys)
            actual_points = staging_index.count()
            if actual_points != expected_points:
                raise RuntimeError(
                    f"Reconstrucción incompleta: se esperaban {expected_points} puntos y hay {actual_points}"
                )

            self.vector_index.activate_staging_index(staging_index)
            activated = True
        finally:
            if not activated:
                self.vector_index.discard_staging_index(staging_index)

        logger.info(
            "Reconstrucción no destructiva completada: %d trabajos, %d puntos verificados",
            total_indexed,
            self.vector_index.count(),
        )
        return total_indexed
