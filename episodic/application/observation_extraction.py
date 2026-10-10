"""Selección y ejecución tolerante a fallos de procesadores normalizados."""
from __future__ import annotations

from dataclasses import dataclass, field

from episodic.domain.entities import Observation
from episodic.domain.provenance import Asset
from episodic.domain.types import EvidenceMediaType
from episodic.ports.processors import ObservationProcessor, ProcessorExecutionError, ProcessorTarget
from episodic.ports.repository import EpisodicRepository
from episodic.processors import (
    AudioProcessor,
    DocumentProcessor,
    ImageProcessor,
    MockProcessor,
    TextProcessor,
    VideoProcessor,
)


@dataclass(frozen=True)
class ProcessorFailure:
    processor: str
    target_id: str
    error: str


@dataclass
class ExtractionResult:
    observations: list[Observation] = field(default_factory=list)
    failures: list[ProcessorFailure] = field(default_factory=list)


class ObservationExtractionService:
    """Orquesta procesadores sin convertir un fallo en borrado del Asset."""

    def __init__(
        self,
        repository: EpisodicRepository,
        processors: list[ObservationProcessor] | None = None,
    ) -> None:
        self.repository = repository
        configured = processors or [
            TextProcessor(repository),
            DocumentProcessor(repository),
            AudioProcessor(repository),
            ImageProcessor(repository),
            VideoProcessor(repository),
            MockProcessor(repository),
        ]
        self.processors = {processor.name: processor for processor in configured}
        self.default_by_modality = {
            modality: processor
            for processor in configured
            if processor.name != "mock-processor"
            for modality in processor.supported_modalities
        }

    def process_asset(
        self,
        asset_id: str,
        *,
        processor_name: str | None = None,
    ) -> ExtractionResult:
        asset = self.repository.get_asset(asset_id)
        if asset is None:
            raise ValueError(f"Asset inexistente: {asset_id}")
        targets: list[ProcessorTarget] = [asset]
        if asset.media_type in {EvidenceMediaType.AUDIO, EvidenceMediaType.VIDEO}:
            windows = self.repository.list_processing_windows_for_asset(asset.id)
            if windows:
                targets = list(windows)

        result = ExtractionResult()
        for target in targets:
            processor = self._select(asset.media_type, processor_name)
            try:
                result.observations.extend(processor.process(target))
            except ProcessorExecutionError as exc:
                result.failures.append(
                    ProcessorFailure(
                        processor=processor.name,
                        target_id=target.id,
                        error=str(exc),
                    )
                )
        return result

    def process_target(
        self,
        target: ProcessorTarget,
        *,
        processor_name: str | None = None,
    ) -> ExtractionResult:
        asset = target if isinstance(target, Asset) else self.repository.get_asset(target.asset_id)
        if asset is None:
            raise ValueError("No se pudo resolver el Asset del objetivo")
        processor = self._select(asset.media_type, processor_name)
        try:
            return ExtractionResult(observations=processor.process(target))
        except ProcessorExecutionError as exc:
            return ExtractionResult(
                failures=[
                    ProcessorFailure(
                        processor=processor.name,
                        target_id=target.id,
                        error=str(exc),
                    )
                ]
            )

    def _select(
        self,
        modality: EvidenceMediaType,
        processor_name: str | None,
    ) -> ObservationProcessor:
        if processor_name:
            processor = self.processors.get(processor_name)
            if processor is None:
                raise ValueError(f"Procesador desconocido: {processor_name}")
            return processor
        processor = self.default_by_modality.get(modality)
        if processor is None:
            raise ValueError(f"No hay procesador para modalidad {modality.value}")
        return processor
