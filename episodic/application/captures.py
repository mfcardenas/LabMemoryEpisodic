"""Captura: varios archivos subidos juntos que forman un único episodio.

El registro es síncrono y barato (inspección, hash y custodia), de modo que
los errores del usuario se devuelven en la propia petición. El procesamiento
(ventanas, observaciones, episodio e indexación) es costoso y lo ejecuta la
cola de trabajos por etapas.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from episodic.adapters.files.metadata import FileInspection, LocalFileInspector
from episodic.application.asset_ingestion import AssetIngestionService
from episodic.application.episode_composition import DeterministicEpisodeComposer
from episodic.application.events import EventDerivationService
from episodic.application.indexing import IndexingWorker
from episodic.application.lifecycle import LifecycleService
from episodic.application.observation_extraction import ObservationExtractionService
from episodic.application.windowing import WindowingService
from episodic.domain.captures import (
    Capture,
    CaptureStatus,
    CaptureTimeOrigin,
    StageState,
)
from episodic.domain.times import utc_now
from episodic.domain.types import EvidenceMediaType, SourceType
from episodic.ports.repository import EpisodicRepository

logger = logging.getLogger(__name__)

CAPTURE_JOB_KIND = "process_capture"
CAPTURE_STAGES: tuple[tuple[str, str], ...] = (
    ("windows", "Ventanas temporales"),
    ("observations", "Observaciones"),
    ("events", "Eventos"),
    ("episode", "Episodio"),
    ("index", "Indexación"),
)

_DEFAULT_SOURCE_SUFFIX = {
    EvidenceMediaType.VIDEO: "camara",
    EvidenceMediaType.AUDIO: "microfono",
    EvidenceMediaType.IMAGE: "fotos",
    EvidenceMediaType.DOCUMENT: "documentos",
    EvidenceMediaType.TEXT: "documentos",
}


_CAPTURE_KEY_NAMESPACE = uuid.UUID("5d0c7a8e-3f61-4c1b-9b8e-6a2f0d4e7c11")


class CaptureConflictError(ValueError):
    """Un archivo de la captura ya pertenece a otra captura, o la captura ya existe."""


def capture_id_for_key(capture_key: str) -> str:
    """Identificador estable de una captura declarada por su herramienta de origen."""
    return str(uuid.uuid5(_CAPTURE_KEY_NAMESPACE, capture_key))


@dataclass(frozen=True)
class CaptureFile:
    """Un fichero de la captura.

    ``source_id``/``source_type`` identifican el dispositivo que lo produjo y
    ``captured_at`` el instante en que ese dispositivo empezó a grabar; sin
    ellos se usan la fuente por defecto del tipo de fichero y la hora de la
    captura.
    """

    path: Path
    original_filename: str
    source_id: str | None = None
    source_type: SourceType | None = None
    captured_at: datetime | None = None


@dataclass(frozen=True)
class CaptureProcessingResult:
    capture_id: str
    episode_id: str
    processing_window_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    microepisode_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    processor_failures: tuple[str, ...]
    indexed_points: int
    removed_episode_ids: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict:
        return {
            "capture_id": self.capture_id,
            "episode_id": self.episode_id,
            "processing_window_ids": list(self.processing_window_ids),
            "observation_ids": list(self.observation_ids),
            "microepisode_ids": list(self.microepisode_ids),
            "event_ids": list(self.event_ids),
            "processor_failures": list(self.processor_failures),
            "indexed_points": self.indexed_points,
        }


StageCallback = Callable[[str, StageState, str], None]


class CaptureService:
    """Registra los originales de una captura y la deja lista para procesar."""

    def __init__(
        self,
        repository: EpisodicRepository,
        ingestion: AssetIngestionService,
        inspector: LocalFileInspector | None = None,
    ) -> None:
        self.repository = repository
        self.ingestion = ingestion
        self.inspector = inspector or ingestion.inspector

    def register(
        self,
        files: Sequence[CaptureFile],
        *,
        environment_id: str,
        title: str | None = None,
        captured_at: datetime | None = None,
        window_duration_seconds: float = 60.0,
        overlap_seconds: float = 5.0,
        audio_offset_seconds: float = 0.0,
        origin: dict | None = None,
        file_time: datetime | None = None,
        capture_key: str | None = None,
        extra_metadata: dict | None = None,
    ) -> Capture:
        """``audio_offset_seconds``: segundos que hay que sumar a los tiempos de
        un audio grabado aparte para alinearlo con el vídeo (negativo si el
        audio empezó a grabarse antes).

        ``origin`` describe de dónde llega la captura (subida, CLI, carpeta
        vigilada) y queda en sus metadatos. ``file_time`` es la hora de los
        ficheros en disco: se usa si no hay hora declarada ni del vídeo, antes
        que la hora de registro.

        ``capture_key`` es el nombre que la herramienta de origen da a la
        captura (``<entorno>_<epoch>``): fija su identificador y el
        ``external_id`` de su episodio, y una clave repetida es un conflicto.
        """
        if not files:
            raise ValueError("Una captura requiere al menos un archivo")
        environment_id = environment_id.strip()
        capture_id = capture_id_for_key(capture_key) if capture_key else None
        if capture_id and self.repository.get_capture(capture_id) is not None:
            raise CaptureConflictError(f"La captura {capture_key} ya existe")
        for item in files:
            if item.captured_at is not None and item.captured_at.tzinfo is None:
                raise ValueError("La hora de cada fichero debe incluir zona horaria")
        inspections = [self.inspector.inspect(item.path) for item in files]
        self._reject_foreign_duplicates(files, inspections)

        resolved_time, time_origin = _resolve_capture_time(captured_at, inspections, file_time)
        asset_ids: list[str] = []
        for item, inspection in zip(files, inspections):
            source_id = (item.source_id or "").strip() or _default_source_id(
                environment_id, inspection.media_type
            )
            ingested = self.ingestion.ingest_file(
                item.path,
                environment_id=environment_id,
                source_id=source_id,
                source_type=item.source_type,
                captured_at=item.captured_at or resolved_time,
                original_filename=item.original_filename,
            )
            asset_ids.append(ingested.asset.id)

        identity = {"id": capture_id} if capture_id else {}
        capture = Capture(
            **identity,
            environment_id=environment_id,
            title=(title or "").strip() or _default_title(files),
            asset_ids=list(dict.fromkeys(asset_ids)),
            captured_at=resolved_time,
            captured_at_origin=time_origin,
            metadata={
                "processing": {
                    "window_duration_seconds": window_duration_seconds,
                    "overlap_seconds": overlap_seconds,
                    "audio_offset_seconds": audio_offset_seconds,
                },
                "origin": dict(origin or {"kind": "upload"}),
                **({"capture_key": capture_key} if capture_key else {}),
                **(extra_metadata or {}),
            },
        )
        self.repository.begin_transaction()
        try:
            self.repository.save_capture(capture)
            for asset_id in capture.asset_ids:
                asset = self.repository.get_asset(asset_id)
                if asset is not None:
                    self.repository.save_asset(
                        replace(asset, metadata={**asset.metadata, "capture_id": capture.id})
                    )
            self.repository.commit_transaction()
        except Exception:
            self.repository.rollback_transaction()
            raise
        return capture

    def _reject_foreign_duplicates(
        self, files: Sequence[CaptureFile], inspections: Sequence[FileInspection]
    ) -> None:
        for item, inspection in zip(files, inspections):
            existing = self.repository.find_asset_by_hash(inspection.content_hash)
            if existing is not None and existing.metadata.get("capture_id"):
                raise CaptureConflictError(
                    f"'{item.original_filename}' ya pertenece a la captura "
                    f"{existing.metadata['capture_id']}"
                )


class CaptureProcessingService:
    """Ventanas -> observaciones -> episodio -> indexación incremental."""

    def __init__(
        self,
        repository: EpisodicRepository,
        windowing: WindowingService,
        extraction: ObservationExtractionService,
        events: EventDerivationService,
        composition: DeterministicEpisodeComposer,
        lifecycle: LifecycleService,
        indexing_worker: IndexingWorker,
    ) -> None:
        self.repository = repository
        self.windowing = windowing
        self.extraction = extraction
        self.events = events
        self.composition = composition
        self.lifecycle = lifecycle
        self.indexing_worker = indexing_worker

    def process(
        self, capture_id: str, on_stage: StageCallback | None = None
    ) -> CaptureProcessingResult:
        report = on_stage or (lambda _name, _state, _detail: None)
        capture = self.repository.get_capture(capture_id)
        if capture is None:
            raise ValueError(f"Captura inexistente: {capture_id}")
        self.repository.save_capture(replace(capture, status=CaptureStatus.PROCESSING))
        try:
            result = self._run(capture, report)
        except Exception:
            current = self.repository.get_capture(capture_id) or capture
            self.repository.save_capture(replace(current, status=CaptureStatus.FAILED))
            raise
        current = self.repository.get_capture(capture_id) or capture
        self.repository.save_capture(
            replace(current, status=CaptureStatus.READY, episode_id=result.episode_id)
        )
        return result

    def _run(self, capture: Capture, report: StageCallback) -> CaptureProcessingResult:
        processing = capture.metadata.get("processing", {})
        assets = [
            asset
            for asset_id in capture.asset_ids
            if (asset := self.repository.get_asset(asset_id)) is not None
        ]

        report("windows", StageState.RUNNING, "")
        window_ids: list[str] = []
        for asset in assets:
            windows = self.repository.list_processing_windows_for_asset(asset.id)
            if (
                not windows
                and asset.media_type in {EvidenceMediaType.AUDIO, EvidenceMediaType.VIDEO}
                and asset.duration_seconds
            ):
                windows = self.windowing.create_windows(
                    asset.id,
                    duration_seconds=float(processing.get("window_duration_seconds", 60.0)),
                    overlap_seconds=float(processing.get("overlap_seconds", 5.0)),
                ).windows
            window_ids.extend(item.id for item in windows)
        report("windows", StageState.COMPLETED, f"{len(window_ids)} ventana(s)")

        report("observations", StageState.RUNNING, "")
        observation_ids: list[str] = []
        failures: list[str] = []
        for index, asset in enumerate(assets, start=1):
            report(
                "observations",
                StageState.RUNNING,
                f"{asset.original_filename or asset.id} ({index}/{len(assets)})",
            )
            extracted = self.extraction.process_asset(asset.id)
            observation_ids.extend(item.id for item in extracted.observations)
            failures.extend(item.error for item in extracted.failures)
        if not observation_ids:
            detail = "; ".join(failures) or "ningún procesador produjo observaciones"
            report("observations", StageState.FAILED, detail)
            # ValueError: fallo determinista, reintentar no cambia el resultado.
            raise ValueError(f"La captura no produjo observaciones: {detail}")
        report(
            "observations",
            StageState.COMPLETED,
            f"{len(observation_ids)} observación(es)"
            + (f", {len(failures)} fallo(s)" if failures else ""),
        )

        report("events", StageState.RUNNING, "")
        events = self.events.derive_for_assets(
            capture.asset_ids,
            audio_offset_seconds=float(processing.get("audio_offset_seconds", 0.0)),
        )
        report(
            "events",
            StageState.COMPLETED,
            f"{len(events)} evento(s)" if events else "sin vídeo descrito: no hay eventos",
        )

        report("episode", StageState.RUNNING, "")
        composed = self.composition.compose_capture(capture, events)
        episode = composed.episodes[0]
        report(
            "episode",
            StageState.COMPLETED,
            f"{len(composed.microepisodes)} microepisodio(s)",
        )

        report("index", StageState.RUNNING, "")
        self.lifecycle.index_episodes(
            self.indexing_worker,
            [episode.id],
            removed_episode_ids=composed.removed_episode_ids,
        )
        indexed = [
            rep
            for rep in self.repository.list_representations_for_target(episode.id)
            if rep.indexing_state.value == "indexed"
        ]
        report("index", StageState.COMPLETED, f"{len(indexed)} representación(es)")

        return CaptureProcessingResult(
            capture_id=capture.id,
            episode_id=episode.id,
            processing_window_ids=tuple(window_ids),
            observation_ids=tuple(observation_ids),
            microepisode_ids=tuple(item.id for item in composed.microepisodes),
            event_ids=tuple(item.id for item in events),
            processor_failures=tuple(failures),
            indexed_points=len(indexed),
            removed_episode_ids=composed.removed_episode_ids,
        )


def _resolve_capture_time(
    declared: datetime | None,
    inspections: Sequence[FileInspection],
    file_time: datetime | None = None,
) -> tuple[datetime, CaptureTimeOrigin]:
    if declared is not None:
        if declared.tzinfo is None:
            raise ValueError("La hora de captura debe incluir zona horaria")
        return declared, CaptureTimeOrigin.DECLARED
    for inspection in inspections:
        raw = inspection.metadata.get("creation_time")
        if raw:
            return datetime.fromisoformat(raw), CaptureTimeOrigin.MEDIA_METADATA
    if file_time is not None:
        if file_time.tzinfo is None:
            raise ValueError("La hora de los ficheros debe incluir zona horaria")
        return file_time, CaptureTimeOrigin.FILE_TIME
    # Sin hora conocida: todos los archivos comparten la hora de subida para
    # alinearse entre sí, y el origen queda registrado como tal.
    return utc_now(), CaptureTimeOrigin.UPLOAD_TIME


def _default_source_id(environment_id: str, media_type: EvidenceMediaType) -> str:
    return f"{environment_id}.{_DEFAULT_SOURCE_SUFFIX[media_type]}"


def _default_title(files: Sequence[CaptureFile]) -> str:
    names = [Path(item.original_filename).stem for item in files]
    if len(names) == 1:
        return names[0]
    return f"{names[0]} (+{len(names) - 1} archivo(s))"
