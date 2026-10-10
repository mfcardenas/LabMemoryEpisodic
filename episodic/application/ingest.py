"""Servicio de ingesta idempotente desde manifiesto (IngestService).

- Lee e interpreta el manifiesto estructurado de episodios y evidencias.
- Copia y almacena atómicamente archivos multimedia en el EvidenceStore.
- Garantiza idempotencia: importar dos veces no crea duplicados.
- Registra trabajos pendientes en la tabla outbox (representations) para indexación desacoplada.
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from episodic.domain.entities import (
    Annotation,
    CaptureSession,
    Entity,
    Episode,
    Evidence,
    Observation,
    Representation,
    Source,
)
from episodic.domain.times import Interval, TimeSpec, utc_now
from episodic.domain.types import (
    EvidenceMediaType,
    EvidenceState,
    IndexingState,
    ProvenanceKind,
    RepresentationSpace,
    SourceType,
)
from episodic.ports.repository import EpisodicRepository, EvidenceStore

logger = logging.getLogger(__name__)


class IngestService:
    """Servicio de aplicación para ingesta de datos episódicos."""

    def __init__(
        self,
        repository: EpisodicRepository,
        evidence_store: EvidenceStore,
        nomic_model_id: str = "nomic-ai/nomic-embed-text-v1.5",
        clip_vision_model_id: str = "Qdrant/clip-ViT-B-32-vision",
    ) -> None:
        self.repository = repository
        self.evidence_store = evidence_store
        self.nomic_model_id = nomic_model_id
        self.clip_vision_model_id = clip_vision_model_id

    def ingest_manifest(
        self,
        manifest_data: Union[str, Path, Dict[str, Any]],
        base_dir: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """Importa un manifiesto completo de forma transaccional e idempotente."""
        if isinstance(manifest_data, (str, Path)):
            manifest_path = Path(manifest_data)
            if base_dir is None:
                base_dir = manifest_path.parent
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = manifest_data
            if base_dir is None:
                base_dir = Path.cwd()

        base_dir = Path(base_dir).resolve()
        stats = {
            "sources": 0,
            "sessions": 0,
            "episodes": 0,
            "evidence": 0,
            "observations": 0,
            "annotations": 0,
            "entities": 0,
            "representations_queued": 0,
            "skipped_duplicates": 0,
        }

        self.repository.begin_transaction()
        try:
            # 1. Sources
            for s_data in data.get("sources", []):
                s_id = s_data.get("id")
                existing = self.repository.get_source(s_id) if s_id else None
                if not existing:
                    src = Source(
                        id=s_id or str(uuid.uuid4()),
                        name=s_data.get("name", "Fuente sin nombre"),
                        source_type=SourceType(s_data.get("source_type", SourceType.CAMERA.value)),
                        scope=s_data.get("scope", ""),
                        clock_source=s_data.get("clock_source", "system"),
                        metadata=s_data.get("metadata", {}),
                    )
                    self.repository.save_source(src)
                    stats["sources"] += 1

            # 2. Capture Sessions
            for sess_data in data.get("sessions", []):
                sess_id = sess_data.get("id")
                existing = self.repository.get_session(sess_id) if sess_id else None
                if not existing:
                    sess = CaptureSession(
                        id=sess_id or str(uuid.uuid4()),
                        source_id=sess_data.get("source_id", ""),
                        external_id=sess_data.get("external_id"),
                        started_at=TimeSpec.from_dict(sess_data.get("started_at")),
                        ended_at=TimeSpec.from_dict(sess_data.get("ended_at")),
                        metadata=sess_data.get("metadata", {}),
                    )
                    self.repository.save_session(sess)
                    stats["sessions"] += 1

            # 3. Entities
            for ent_data in data.get("entities", []):
                ent_id = ent_data.get("id")
                existing = self.repository.get_entity(ent_id) if ent_id else None
                if not existing:
                    ent = Entity(
                        id=ent_id or str(uuid.uuid4()),
                        label=ent_data.get("label", ""),
                        entity_type=ent_data.get("entity_type", "object"),
                        description=ent_data.get("description", ""),
                        created_by=ent_data.get("created_by", "system"),
                        provenance_kind=ProvenanceKind(
                            ent_data.get("provenance_kind", ProvenanceKind.HUMAN_DECLARATION.value)
                        ),
                        metadata=ent_data.get("metadata", {}),
                    )
                    self.repository.save_entity(ent)
                    stats["entities"] += 1

            # 4. Episodes & Evidence
            for ep_data in data.get("episodes", []):
                external_id = ep_data.get("external_id")
                existing_ep = None
                if external_id:
                    existing_ep = self.repository.find_episode_by_external_id(external_id)

                if existing_ep:
                    logger.debug("Episodio ya existe (idempotencia): %s", external_id)
                    stats["skipped_duplicates"] += 1
                    continue

                ep_id = ep_data.get("id")
                source_ids = ep_data.get("source_ids", [])
                session_id = ep_data.get("session_id")
                first_source = source_ids[0] if source_ids else "default"

                evidence_ids: List[str] = []
                # Ingestar evidencias adjuntas al episodio
                for ev_data in ep_data.get("evidence", []):
                    ext_cap_id = ev_data.get("external_capture_id")
                    ev_src_id = ev_data.get("source_id", first_source)
                    ev_sess_id = ev_data.get("session_id", session_id or "default_sess")

                    # Idempotencia por clave compuesta (source_id, session_id, external_capture_id)
                    existing_ev = None
                    if ext_cap_id:
                        existing_ev = self.repository.find_evidence_by_key(
                            ev_src_id, ev_sess_id, ext_cap_id
                        )

                    if existing_ev:
                        evidence_ids.append(existing_ev.id)
                        stats["skipped_duplicates"] += 1
                        continue

                    # Almacenar archivo en EvidenceStore
                    rel_file = ev_data.get("file_path") or ev_data.get("filename")
                    internal_uri = ""
                    content_hash = ""
                    file_size = 0

                    if rel_file:
                        abs_file = (base_dir / rel_file).resolve()
                        if abs_file.exists():
                            internal_uri, content_hash, file_size = self.evidence_store.store_file(abs_file)
                        else:
                            # Si es un recurso sintético o simulado que no está en disco
                            synthetic_content = ev_data.get("content", f"Synthetic media for {rel_file}".encode())
                            if isinstance(synthetic_content, str):
                                synthetic_content = synthetic_content.encode("utf-8")
                            internal_uri, content_hash = self.evidence_store.store(synthetic_content, rel_file)
                            file_size = len(synthetic_content)

                    media_type_str = ev_data.get("media_type", "image").lower()
                    try:
                        media_type = EvidenceMediaType(media_type_str)
                    except ValueError:
                        media_type = EvidenceMediaType.IMAGE

                    ev = Evidence(
                        id=ev_data.get("id") or str(uuid.uuid4()),
                        session_id=ev_sess_id,
                        source_id=ev_src_id,
                        media_type=media_type,
                        original_filename=rel_file,
                        internal_uri=internal_uri,
                        content_hash=content_hash,
                        file_size_bytes=file_size,
                        capture_time=TimeSpec.from_dict(ev_data.get("capture_time")),
                        state=EvidenceState.ACTIVE,
                        external_capture_id=ext_cap_id,
                        metadata=ev_data.get("metadata", {}),
                    )
                    self.repository.save_evidence(ev)
                    evidence_ids.append(ev.id)
                    stats["evidence"] += 1

                    # Si es imagen, encolar representación visual en outbox
                    if media_type == EvidenceMediaType.IMAGE and internal_uri:
                        rep_img = Representation(
                            target_type="evidence",
                            target_id=ev.id,
                            space=RepresentationSpace.CLIP_VISION,
                            model_id=self.clip_vision_model_id,
                            model_revision="v1",
                            dimension=512,
                            indexing_state=IndexingState.PENDING,
                        )
                        self.repository.save_representation(rep_img)
                        stats["representations_queued"] += 1

                # 1. Crear y persistir el episodio primero (para satisfacer FK de observaciones y anotaciones)
                episode_id = ep_data.get("id") or str(uuid.uuid4())

                indexable_text = ep_data.get("indexable_text")
                if not indexable_text:
                    obs_texts = [
                        obs_data.get("content", "") for obs_data in ep_data.get("observations", [])
                    ]
                    indexable_text = f"{ep_data.get('title', '')}. " + " ".join(obs_texts)

                episode = Episode(
                    id=episode_id,
                    title=ep_data.get("title", ""),
                    external_id=external_id,
                    interval=Interval.from_dict(ep_data.get("interval")),
                    source_ids=source_ids,
                    location=ep_data.get("location", ""),
                    tags=ep_data.get("tags", []),
                    evidence_ids=evidence_ids,
                    observation_ids=[],
                    indexable_text=indexable_text.strip(),
                    is_visible=ep_data.get("is_visible", True),
                    session_id=session_id,
                    metadata=ep_data.get("metadata", {}),
                )
                self.repository.save_episode(episode)
                stats["episodes"] += 1

                # 2. Ingestar observaciones del episodio
                observation_ids: List[str] = []
                for obs_data in ep_data.get("observations", []):
                    prov_str = obs_data.get("provenance_kind", ProvenanceKind.VISUAL_DETECTION.value)
                    try:
                        prov_kind = ProvenanceKind(prov_str)
                    except ValueError:
                        prov_kind = ProvenanceKind.VISUAL_DETECTION

                    obs = Observation(
                        id=obs_data.get("id") or str(uuid.uuid4()),
                        episode_id=episode.id,
                        content=obs_data.get("content", ""),
                        provenance_kind=prov_kind,
                        author=obs_data.get("author", "detector"),
                        tool_version=obs_data.get("tool_version"),
                        confidence=obs_data.get("confidence"),
                        evidence_ids=obs_data.get("evidence_ids", evidence_ids),
                        entity_id=obs_data.get("entity_id"),
                        capture_time=TimeSpec.from_dict(obs_data.get("capture_time")),
                        metadata=obs_data.get("metadata", {}),
                    )
                    self.repository.save_observation(obs)
                    observation_ids.append(obs.id)
                    stats["observations"] += 1

                # Actualizar observation_ids en el episodio
                episode.observation_ids = observation_ids
                self.repository.save_episode(episode)

                # Ingestar anotaciones si existen
                for ann_data in ep_data.get("annotations", []):
                    ann = Annotation(
                        id=ann_data.get("id") or str(uuid.uuid4()),
                        episode_id=episode.id,
                        revision_number=ann_data.get("revision_number", 1),
                        content=ann_data.get("content", ""),
                        provenance_kind=ProvenanceKind(
                            ann_data.get("provenance_kind", ProvenanceKind.HUMAN_DECLARATION.value)
                        ),
                        author=ann_data.get("author", "admin"),
                        tool_version=ann_data.get("tool_version"),
                        evidence_ids=ann_data.get("evidence_ids", []),
                        supersedes_id=ann_data.get("supersedes_id"),
                        metadata=ann_data.get("metadata", {}),
                    )
                    self.repository.save_annotation(ann)
                    stats["annotations"] += 1

                # Encolar representación de texto del episodio en outbox
                if episode.indexable_text:
                    rep_txt = Representation(
                        target_type="episode",
                        target_id=episode.id,
                        space=RepresentationSpace.NOMIC_TEXT,
                        model_id=self.nomic_model_id,
                        model_revision="v1.5",
                        dimension=768,
                        indexing_state=IndexingState.PENDING,
                    )
                    self.repository.save_representation(rep_txt)
                    stats["representations_queued"] += 1

            self.repository.commit_transaction()
            logger.info("Ingesta completada con éxito: %s", stats)
            return stats

        except Exception as e:
            self.repository.rollback_transaction()
            logger.error("Error durante la ingesta, transacción revertida: %s", e)
            raise
