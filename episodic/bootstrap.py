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
    FakePassageReranker,
)
from episodic.adapters.models.fastembed import (
    FastEmbedAlignedTextEncoder,
    FastEmbedEpisodeTextEncoder,
    FastEmbedImageEncoder,
    FastEmbedPassageReranker,
)
from episodic.adapters.qdrant.index import QdrantVectorIndex
from episodic.adapters.qdrant_edge.index import QdrantEdgeVectorIndex
from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.application.activity import ActivityLog
from episodic.application.indexing import IndexingWorker
from episodic.application.inspection import MemoryInspectionService
from episodic.application.asset_ingestion import AssetIngestionService
from episodic.application.captures import (
    CAPTURE_JOB_KIND,
    CAPTURE_STAGES,
    CaptureProcessingService,
    CaptureService,
)
from episodic.application.events import EventDerivationService
from episodic.application.jobs import JobRunner
from episodic.application.ingest import IngestService
from episodic.application.lifecycle import LifecycleService
from episodic.application.observation_extraction import ObservationExtractionService
from episodic.application.index_maintenance import (
    REINDEX_JOB_KIND,
    REINDEX_STAGES,
    IndexMaintenanceService,
    index_profile,
)
from episodic.application.passage_search import PassageSearchService
from episodic.application.episode_composition import DeterministicEpisodeComposer
from episodic.application.logical_graph import LogicalGraphService
from episodic.application.hybrid_retrieval import HybridRetrievalService
from episodic.application.retrieval import RetrievalService
from episodic.application.watch import InboxWatcher
from episodic.application.watch_config import WatchSettings, load_watch_settings
from episodic.application.windowing import WindowingService
from episodic.application.workflow import EpiGraphWorkflowService
from episodic.tools.memory import MemoryToolbox
from episodic.config import EpisodicConfig
from episodic.ports.encoders import (
    AlignedTextEncoder,
    EpisodeTextEncoder,
    ImageEncoder,
    PassageReranker,
)
from episodic.ports.repository import EpisodicRepository, EvidenceStore
from episodic.ports.vector_index import VectorIndex

logger = logging.getLogger(__name__)


def build_observation_extraction(
    config: EpisodicConfig,
    repository: EpisodicRepository,
    store: EvidenceStore | None,
) -> ObservationExtractionService:
    """Procesadores por modalidad; el vídeo se describe si hay PyAV y visión."""
    from episodic.processors import (
        AudioProcessor,
        DocumentProcessor,
        ImageProcessor,
        MockProcessor,
        TextProcessor,
        VideoProcessor,
    )

    processors = [
        TextProcessor(repository),
        DocumentProcessor(repository),
        AudioProcessor(repository),
        ImageProcessor(repository),
        VideoProcessor(repository),
        MockProcessor(repository),
    ]
    video_events = _build_video_event_processor(config, repository, store)
    if video_events is not None:
        # Registrado después: sustituye al procesador técnico de vídeo.
        processors.append(video_events)
    speech = _build_speech_processor(config, repository)
    # El habla se suma al procesador principal del audio y del vídeo: si el
    # servicio falla, el resto de la captura se procesa igual.
    return ObservationExtractionService(
        repository, processors, supplementary=[speech] if speech else []
    )


def build_speech_transcriber(config: EpisodicConfig):
    """Transcriptor configurado o ``None`` si el habla está desactivada."""
    if config.speech_backend == "none":
        return None
    if config.speech_backend == "fake":
        from episodic.adapters.speech.fake import FakeSpeechTranscriber

        return FakeSpeechTranscriber()
    from episodic.adapters.speech.openai_http import OpenAICompatibleTranscriber

    return OpenAICompatibleTranscriber(
        base_url=config.speech_url,
        model=config.speech_model,
        timeout_seconds=config.speech_timeout_seconds,
        api_key=config.speech_api_key,
        prompt=config.speech_prompt,
        temperature=config.speech_temperature,
    )


def _build_speech_processor(config, repository):
    from episodic.adapters.video.decoder import video_decoding_available

    transcriber = build_speech_transcriber(config)
    if transcriber is None:
        return None
    if not video_decoding_available():
        logger.warning(
            "PyAV no está instalado: el audio no se transcribirá (instale el extra [video])"
        )
        return None
    from episodic.adapters.audio.extract import PyAVAudioExtractor
    from episodic.processors.speech import SpeechTranscriptionProcessor

    return SpeechTranscriptionProcessor(
        repository,
        PyAVAudioExtractor(sample_rate_hz=config.speech_sample_rate_hz),
        transcriber,
        language=config.speech_language,
    )


def _build_video_event_processor(config, repository, store):
    from episodic.adapters.video.decoder import PyAVFrameSampler, video_decoding_available

    if config.vision_backend == "none" or store is None:
        return None
    if not video_decoding_available():
        logger.warning(
            "PyAV no está instalado: el vídeo solo producirá intervalos técnicos "
            "(instale el extra [video])"
        )
        return None
    from episodic.application.video_segmentation import SceneSegmenter, SegmentationConfig
    from episodic.processors.video_events import VideoEventProcessor

    if config.vision_backend == "fake":
        from episodic.adapters.models.fake_vision import FakeVisionDescriber

        describer = FakeVisionDescriber()
    else:
        from episodic.adapters.models.ollama_vision import OllamaVisionDescriber

        describer = OllamaVisionDescriber(
            model=config.vision_model,
            base_url=config.ollama_url,
            timeout_seconds=config.vision_timeout_seconds,
            temperature=config.vision_temperature,
            think=config.vision_think,
        )
    return VideoEventProcessor(
        repository,
        store,
        PyAVFrameSampler(
            sample_fps=config.video_sample_fps,
            max_side=config.video_frame_max_side,
            jpeg_quality=config.video_jpeg_quality,
        ),
        SceneSegmenter(
            SegmentationConfig(
                min_segment_seconds=config.video_min_segment_seconds,
                max_segment_seconds=config.video_max_segment_seconds,
                scene_change_threshold=config.video_scene_threshold,
                max_keyframes=config.video_max_keyframes,
            )
        ),
        describer,
        language=config.description_language,
    )


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
    activity_log: ActivityLog
    passage_search: Optional[PassageSearchService] = None
    index_maintenance: Optional[IndexMaintenanceService] = None
    watcher: Optional[InboxWatcher] = None

    def close(self) -> None:
        """Cierra conexiones persistentes de forma limpia."""
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher.repository.close()
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
        observation_extraction_service=build_observation_extraction(config, repository, store),
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
        try:
            vector_index = QdrantVectorIndex(
                host=config.qdrant_host,
                port=config.qdrant_port,
                collection_name=config.qdrant_collection,
                text_dim=config.nomic_dim,
                image_dim=config.clip_dim,
            )
        except Exception as exc:
            repo.close()
            raise RuntimeError(
                f"No se puede usar Qdrant en {config.qdrant_host}:{config.qdrant_port} "
                f"(colección {config.qdrant_collection}): {exc}. Arranque el contenedor "
                "o revise EPIGRAPH_QDRANT_HOST / EPIGRAPH_QDRANT_PORT."
            ) from exc
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
            document_prefix=config.nomic_document_prefix,
            query_prefix=config.nomic_query_prefix,
            batch_size=config.encoder_batch_size,
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

    reranker: Optional[PassageReranker] = None
    if config.rerank_model:
        reranker = (
            FakePassageReranker()
            if use_fake_encoders
            else FastEmbedPassageReranker(
                model_id=config.rerank_model,
                cache_dir=str(config.model_cache_path),
                threads=config.rerank_threads or None,
            )
        )
    passage_search_svc = PassageSearchService(
        repository=repo,
        vector_index=vector_index,
        text_encoder=text_encoder,
        reranker=reranker,
        candidates=config.passage_candidates,
        rerank_candidates=config.rerank_candidates,
        rerank_max_chars=config.rerank_max_chars,
        rerank_min_score=config.rerank_min_score,
        agreement_rank=config.passage_agreement_rank,
        max_episodes=config.passage_max_episodes,
        per_episode=config.passage_per_episode,
        rrf_k=config.rrf_k,
        timezone=_query_timezone(config),
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
    observation_extraction_svc = build_observation_extraction(config, repo, store)
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
    watch_path = config.watch_config_path
    watcher = (
        create_inbox_watcher(config, store, job_runner, load_watch_settings(watch_path))
        if watch_path is not None
        else None
    )

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
        activity_log=ActivityLog(repo, config.activity_log_max_rows, config.activity_log_max_passes),
        passage_search=passage_search_svc,
        index_maintenance=IndexMaintenanceService(
            repo, vector_index, lifecycle_svc, indexing_worker, _index_profile(config)
        ),
        watcher=watcher,
    )


def _query_timezone(config: EpisodicConfig):
    """Zona horaria de las expresiones temporales de las preguntas."""
    from datetime import datetime

    if config.query_timezone:
        from zoneinfo import ZoneInfo

        return ZoneInfo(config.query_timezone)
    return datetime.now().astimezone().tzinfo


def _index_profile(config: EpisodicConfig) -> dict:
    return index_profile(
        text_model=config.nomic_model,
        document_prefix=config.nomic_document_prefix,
        image_model=config.clip_vision_model,
    )


def submit_reindex(container: "AppContainer", trigger: str):
    """Encola el reindexado en la cola de trabajos (la misma que las capturas)."""
    return container.job_runner.submit(REINDEX_JOB_KIND, trigger, REINDEX_STAGES)


def create_inbox_watcher(
    config: EpisodicConfig,
    store: EvidenceStore,
    job_runner: JobRunner,
    settings: WatchSettings,
    **kwargs,
) -> InboxWatcher:
    """Vigilante sobre una conexión SQLite propia; encola en ``job_runner``."""
    repo = SqliteEpisodicRepository(config.sqlite_path)
    capture_service = CaptureService(
        repo, AssetIngestionService(repo, LocalFileInspector(), managed_store=store)
    )
    return InboxWatcher(
        repo,
        capture_service,
        settings,
        submit=lambda capture_id: job_runner.submit(CAPTURE_JOB_KIND, capture_id, CAPTURE_STAGES),
        activity=ActivityLog(repo, config.activity_log_max_rows, config.activity_log_max_passes),
        **kwargs,
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
    lifecycle = LifecycleService(
        repository=repo,
        evidence_store=store,
        vector_index=vector_index,
        nomic_model_id=config.nomic_model,
        clip_vision_model_id=config.clip_vision_model,
    )
    maintenance = IndexMaintenanceService(repo, vector_index, lifecycle, worker, _index_profile(config))
    processing = CaptureProcessingService(
        repository=repo,
        windowing=WindowingService(repo),
        extraction=build_observation_extraction(config, repo, store),
        events=EventDerivationService(repo),
        composition=DeterministicEpisodeComposer(repo),
        lifecycle=lifecycle,
        indexing_worker=worker,
    )

    def process_capture(job, report):
        return processing.process(job.target_id, on_stage=report).to_dict()

    def describe(job):
        if job.job_type == REINDEX_JOB_KIND:
            return f"reindexado de la memoria ({job.target_id})"
        capture = repo.get_capture(job.target_id) if job.job_type == CAPTURE_JOB_KIND else None
        return f"captura «{capture.title}»" if capture else f"{job.job_type} {job.target_id}"

    return JobRunner(
        repo,
        {CAPTURE_JOB_KIND: process_capture, REINDEX_JOB_KIND: maintenance.run},
        activity=ActivityLog(repo, config.activity_log_max_rows, config.activity_log_max_passes),
        describe=describe,
    )
