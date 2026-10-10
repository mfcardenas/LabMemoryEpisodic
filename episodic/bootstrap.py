"""Contenedor de inyección de dependencias (bootstrap.py).

Compone y cablea repositorios, adaptadores de modelos, índices y servicios de aplicación.
Tanto la CLI como la API HTTP y los tests comparten esta misma composición.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from episodic.adapters.media.store import FilesystemEvidenceStore
from episodic.adapters.files.metadata import LocalFileInspector
from episodic.adapters.models.fake import (
    FakeAlignedTextEncoder,
    FakeEpisodeTextEncoder,
    FakeImageEncoder,
)
from episodic.adapters.models.fastembed import (
    FastEmbedAlignedTextEncoder,
    FastEmbedEpisodeTextEncoder,
    FastEmbedImageEncoder,
)
from episodic.adapters.qdrant.index import QdrantVectorIndex
from episodic.adapters.qdrant_edge.index import QdrantEdgeVectorIndex
from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.application.indexing import IndexingWorker
from episodic.application.inspection import MemoryInspectionService
from episodic.application.asset_ingestion import AssetIngestionService
from episodic.application.captures import (
    CAPTURE_JOB_KIND,
    CaptureProcessingService,
    CaptureService,
)
from episodic.application.jobs import JobRunner
from episodic.application.ingest import IngestService
from episodic.application.lifecycle import LifecycleService
from episodic.application.observation_extraction import ObservationExtractionService
from episodic.application.episode_composition import DeterministicEpisodeComposer
from episodic.application.logical_graph import LogicalGraphService
from episodic.application.hybrid_retrieval import HybridRetrievalService
from episodic.application.retrieval import RetrievalService
from episodic.application.windowing import WindowingService
from episodic.application.workflow import EpiGraphWorkflowService
from episodic.tools.memory import MemoryToolbox
from episodic.config import EpisodicConfig
from episodic.ports.encoders import (
    AlignedTextEncoder,
    EpisodeTextEncoder,
    ImageEncoder,
)
from episodic.ports.repository import EpisodicRepository, EvidenceStore
from episodic.ports.vector_index import VectorIndex

logger = logging.getLogger(__name__)


@dataclass
class AppContainer:
    """Contenedor de dependencias configurado."""
    config: EpisodicConfig
    repository: EpisodicRepository
    evidence_store: EvidenceStore
    vector_index: VectorIndex
    text_encoder: EpisodeTextEncoder
    image_encoder: ImageEncoder
    aligned_text_encoder: AlignedTextEncoder
    ingest_service: IngestService
    indexing_worker: IndexingWorker
    retrieval_service: RetrievalService
    lifecycle_service: LifecycleService
    asset_ingestion_service: AssetIngestionService
    windowing_service: WindowingService
    observation_extraction_service: ObservationExtractionService
    episode_composition_service: DeterministicEpisodeComposer
    logical_graph_service: LogicalGraphService
    hybrid_retrieval_service: HybridRetrievalService
    memory_tools: MemoryToolbox
    workflow_service: EpiGraphWorkflowService
    inspection_service: MemoryInspectionService
    capture_service: CaptureService
    job_runner: JobRunner

    def close(self) -> None:
        """Cierra conexiones persistentes de forma limpia."""
        self.job_runner.stop()
        if hasattr(self.job_runner.repository, "close"):
            self.job_runner.repository.close()
        if hasattr(self.repository, "close"):
            self.repository.close()
        if hasattr(self.vector_index, "close"):
            self.vector_index.close()


@dataclass
class IngestionContainer:
    """Composición mínima para Fase 3, sin arrancar índice ni modelos."""

    config: EpisodicConfig
    repository: EpisodicRepository
    asset_ingestion_service: AssetIngestionService
    windowing_service: WindowingService
    observation_extraction_service: ObservationExtractionService
    episode_composition_service: DeterministicEpisodeComposer
    logical_graph_service: LogicalGraphService

    def close(self) -> None:
        if hasattr(self.repository, "close"):
            self.repository.close()


def create_ingestion_container(
    config: Optional[EpisodicConfig] = None,
) -> IngestionContainer:
    """Compone exclusivamente SQLite, inspección de archivos y ventanas."""
    if config is None:
        config = EpisodicConfig()
    config.data_path.mkdir(parents=True, exist_ok=True)
    config.media_dir.mkdir(parents=True, exist_ok=True)
    repository = SqliteEpisodicRepository(config.sqlite_path)
    store = FilesystemEvidenceStore(
        media_dir=config.media_dir,
        ref_checker=repository.count_active_references_for_hash,
    )
    return IngestionContainer(
        config=config,
        repository=repository,
        asset_ingestion_service=AssetIngestionService(
            repository, LocalFileInspector(), managed_store=store
        ),
        windowing_service=WindowingService(repository),
        observation_extraction_service=ObservationExtractionService(repository),
        episode_composition_service=DeterministicEpisodeComposer(repository),
        logical_graph_service=LogicalGraphService(repository),
    )


def create_app_container(
    config: Optional[EpisodicConfig] = None,
    use_fake_encoders: bool = False,
    use_memory_qdrant: bool = False,
) -> AppContainer:
    """Crea e inicializa el grafo completo de dependencias."""
    if config is None:
        config = EpisodicConfig()

    config.ensure_dirs()

    # 1. Repositorio SQLite
    repo = SqliteEpisodicRepository(config.sqlite_path)

    # 2. Almacén de medios
    store = FilesystemEvidenceStore(
        media_dir=config.media_dir,
        ref_checker=repo.count_active_references_for_hash,
    )

    # 3. Índice Vectorial Qdrant
    if use_memory_qdrant:
        vector_index: VectorIndex = QdrantVectorIndex(
            collection_name=config.qdrant_collection,
            text_dim=config.nomic_dim,
            image_dim=config.clip_dim,
            use_memory=True,
        )
    elif config.vector_backend == "edge":
        vector_index = QdrantEdgeVectorIndex(
            root_dir=config.qdrant_edge_dir,
            collection_name=config.qdrant_collection,
            text_dim=config.nomic_dim,
            image_dim=config.clip_dim,
        )
    elif config.vector_backend == "http":
        vector_index = QdrantVectorIndex(
            host=config.qdrant_host,
            port=config.qdrant_port,
            collection_name=config.qdrant_collection,
            text_dim=config.nomic_dim,
            image_dim=config.clip_dim,
        )
    else:
        raise ValueError(f"Backend vectorial no soportado: {config.vector_backend}")

    # 4. Encoders
    if use_fake_encoders:
        text_encoder: EpisodeTextEncoder = FakeEpisodeTextEncoder(config.nomic_dim)
        image_encoder: ImageEncoder = FakeImageEncoder(config.clip_dim)
        aligned_encoder: AlignedTextEncoder = FakeAlignedTextEncoder(config.clip_dim)
    else:
        text_encoder = FastEmbedEpisodeTextEncoder(
            model_id=config.nomic_model,
            dimension=config.nomic_dim,
            cache_dir=str(config.model_cache_path),
            threads=config.encoder_threads,
        )
        image_encoder = FastEmbedImageEncoder(
            model_id=config.clip_vision_model,
            dimension=config.clip_dim,
            cache_dir=str(config.model_cache_path),
            threads=config.encoder_threads,
        )
        aligned_encoder = FastEmbedAlignedTextEncoder(
            model_id=config.clip_text_model,
            dimension=config.clip_dim,
            cache_dir=str(config.model_cache_path),
            threads=config.encoder_threads,
        )

    # 5. Servicios de aplicación
    ingest_svc = IngestService(
        repository=repo,
        evidence_store=store,
        nomic_model_id=config.nomic_model,
        clip_vision_model_id=config.clip_vision_model,
    )

    indexing_worker = IndexingWorker(
        repository=repo,
        evidence_store=store,
        vector_index=vector_index,
        text_encoder=text_encoder,
        image_encoder=image_encoder,
    )

    retrieval_svc = RetrievalService(
        repository=repo,
        vector_index=vector_index,
        text_encoder=text_encoder,
        aligned_text_encoder=aligned_encoder,
        image_encoder=image_encoder,
        default_text_threshold=config.default_text_threshold,
        default_image_threshold=config.default_image_threshold,
        rrf_k=config.rrf_k,
    )

    logical_graph_svc = LogicalGraphService(repo)
    hybrid_retrieval_svc = HybridRetrievalService(
        repository=repo,
        semantic_retrieval=retrieval_svc,
        logical_graph=logical_graph_svc,
    )
    memory_tools = MemoryToolbox(
        repository=repo,
        hybrid_retrieval=hybrid_retrieval_svc,
        logical_graph=logical_graph_svc,
    )

    lifecycle_svc = LifecycleService(
        repository=repo,
        evidence_store=store,
        vector_index=vector_index,
        nomic_model_id=config.nomic_model,
        clip_vision_model_id=config.clip_vision_model,
    )
    asset_ingestion_svc = AssetIngestionService(
        repo, LocalFileInspector(), managed_store=store
    )
    windowing_svc = WindowingService(repo)
    observation_extraction_svc = ObservationExtractionService(repo)
    episode_composition_svc = DeterministicEpisodeComposer(repo)
    workflow_svc = EpiGraphWorkflowService(
        repository=repo,
        ingestion=asset_ingestion_svc,
        windowing=windowing_svc,
        extraction=observation_extraction_svc,
        composition=episode_composition_svc,
        lifecycle=lifecycle_svc,
        indexing_worker=indexing_worker,
    )
    inspection_svc = MemoryInspectionService(repo)
    capture_svc = CaptureService(repo, asset_ingestion_svc)
    job_runner = _create_job_runner(config, store, vector_index, text_encoder, image_encoder)

    return AppContainer(
        config=config,
        repository=repo,
        evidence_store=store,
        vector_index=vector_index,
        text_encoder=text_encoder,
        image_encoder=image_encoder,
        aligned_text_encoder=aligned_encoder,
        ingest_service=ingest_svc,
        indexing_worker=indexing_worker,
        retrieval_service=retrieval_svc,
        lifecycle_service=lifecycle_svc,
        asset_ingestion_service=asset_ingestion_svc,
        windowing_service=windowing_svc,
        observation_extraction_service=observation_extraction_svc,
        episode_composition_service=episode_composition_svc,
        logical_graph_service=logical_graph_svc,
        hybrid_retrieval_service=hybrid_retrieval_svc,
        memory_tools=memory_tools,
        workflow_service=workflow_svc,
        inspection_service=inspection_svc,
        capture_service=capture_svc,
        job_runner=job_runner,
    )


def _create_job_runner(
    config: EpisodicConfig,
    store: EvidenceStore,
    vector_index: VectorIndex,
    text_encoder: EpisodeTextEncoder,
    image_encoder: ImageEncoder,
) -> JobRunner:
    """Servicios del hilo de trabajo sobre una conexión SQLite propia.

    Comparten el almacén de medios, el índice (protegido por su lock) y los
    encoders ya cargados; nunca la conexión de las peticiones HTTP.
    """
    repo = SqliteEpisodicRepository(config.sqlite_path)
    worker = IndexingWorker(
        repository=repo,
        evidence_store=store,
        vector_index=vector_index,
        text_encoder=text_encoder,
        image_encoder=image_encoder,
    )
    processing = CaptureProcessingService(
        repository=repo,
        windowing=WindowingService(repo),
        extraction=ObservationExtractionService(repo),
        composition=DeterministicEpisodeComposer(repo),
        lifecycle=LifecycleService(
            repository=repo,
            evidence_store=store,
            vector_index=vector_index,
            nomic_model_id=config.nomic_model,
            clip_vision_model_id=config.clip_vision_model,
        ),
        indexing_worker=worker,
    )

    def process_capture(job, report):
        return processing.process(job.target_id, on_stage=report).to_dict()

    return JobRunner(repo, {CAPTURE_JOB_KIND: process_capture})
