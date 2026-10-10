"""Recorrido vertical canónico de EpiGraph.

Conecta servicios ya existentes sin introducir inferencias nuevas:
Asset -> ProcessingWindow -> Observation -> MicroEpisode -> Episode -> Qdrant.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from episodic.application.asset_ingestion import AssetIngestionService
from episodic.application.episode_composition import DeterministicEpisodeComposer
from episodic.application.indexing import IndexingWorker
from episodic.application.lifecycle import LifecycleService
from episodic.application.observation_extraction import ObservationExtractionService
from episodic.application.windowing import WindowingService
from episodic.domain.types import EvidenceMediaType, SourceType
from episodic.ports.repository import EpisodicRepository


@dataclass(frozen=True)
class WorkflowResult:
    asset_id: str
    duplicate: bool
    processing_window_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    processor_failures: tuple[str, ...]
    microepisode_ids: tuple[str, ...]
    episode_ids: tuple[str, ...]
    indexed_points: int


class EpiGraphWorkflowService:
    """Ejecuta el flujo completo y deja SQLite y Qdrant sincronizados."""

    def __init__(
        self,
        repository: EpisodicRepository,
        ingestion: AssetIngestionService,
        windowing: WindowingService,
        extraction: ObservationExtractionService,
        composition: DeterministicEpisodeComposer,
        lifecycle: LifecycleService,
        indexing_worker: IndexingWorker,
    ) -> None:
        self.repository = repository
        self.ingestion = ingestion
        self.windowing = windowing
        self.extraction = extraction
        self.composition = composition
        self.lifecycle = lifecycle
        self.indexing_worker = indexing_worker

    def ingest_and_process(
        self,
        file_path: str | Path,
        *,
        environment_id: str,
        source_id: str,
        source_type: SourceType | None = None,
        captured_at: datetime | None = None,
        window_duration_seconds: float = 60.0,
        overlap_seconds: float = 5.0,
        original_filename: str | None = None,
    ) -> WorkflowResult:
        ingested = self.ingestion.ingest_file(
            file_path,
            environment_id=environment_id,
            source_id=source_id,
            source_type=source_type,
            captured_at=captured_at,
            original_filename=original_filename,
        )
        asset = ingested.asset

        windows = self.repository.list_processing_windows_for_asset(asset.id)
        if (
            asset.media_type in {EvidenceMediaType.AUDIO, EvidenceMediaType.VIDEO}
            and asset.duration_seconds is not None
            and asset.duration_seconds > 0
            and not windows
        ):
            windows = self.windowing.create_windows(
                asset.id,
                duration_seconds=window_duration_seconds,
                overlap_seconds=overlap_seconds,
            ).windows

        extracted = self.extraction.process_asset(asset.id)
        composed = self.composition.compose(environment_id)

        # La proyección es reconstruible. Se activa una generación completa
        # para evitar que un episodio nuevo conviva con puntos obsoletos.
        self.lifecycle.rebuild_index(self.indexing_worker)

        return WorkflowResult(
            asset_id=asset.id,
            duplicate=ingested.duplicate,
            processing_window_ids=tuple(item.id for item in windows),
            observation_ids=tuple(item.id for item in extracted.observations),
            processor_failures=tuple(item.error for item in extracted.failures),
            microepisode_ids=tuple(item.id for item in composed.microepisodes),
            episode_ids=tuple(item.id for item in composed.episodes),
            indexed_points=self.indexing_worker.vector_index.count(),
        )
