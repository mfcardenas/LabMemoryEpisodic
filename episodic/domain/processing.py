"""Ejecuciones, ventanas y eventos del procesamiento determinista."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from episodic.domain.model import (
    DomainValidationError,
    SerializableDomainModel,
    validate_confidence,
    validate_identifier,
    validate_interval,
    validate_timestamp,
)
from episodic.domain.times import utc_now
from episodic.domain.types import ProcessingRunState


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class ProcessingRun(SerializableDomainModel):
    """Ejecución versionada de un productor sobre uno o varios activos."""

    id: str = field(default_factory=_new_id)
    environment_id: str = ""
    source_id: str | None = None
    asset_ids: list[str] = field(default_factory=list)
    producer: str = ""
    model_version: str = ""
    state: ProcessingRunState = ProcessingRunState.PENDING
    start_time: datetime | None = None
    end_time: datetime | None = None
    confidence: float | None = None
    created_at: datetime = field(default_factory=utc_now)
    parameters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        validate_identifier(self.environment_id, "environment_id")
        if self.source_id:
            validate_identifier(self.source_id, "source_id")
        if not self.producer.strip():
            raise DomainValidationError("ProcessingRun.producer es obligatorio")
        if not self.model_version.strip():
            raise DomainValidationError("ProcessingRun.model_version es obligatorio")
        validate_interval(self.start_time, self.end_time)
        validate_confidence(self.confidence)
        validate_timestamp(self.created_at, "created_at")


@dataclass
class ProcessingWindow(SerializableDomainModel):
    """Intervalo técnico de procesamiento; no es ni hereda de Episode."""

    id: str = field(default_factory=_new_id)
    processing_run_id: str = ""
    environment_id: str = ""
    source_id: str = ""
    asset_id: str = ""
    start_time: datetime | None = None
    end_time: datetime | None = None
    sequence_number: int = 0
    start_offset_seconds: float = 0.0
    end_offset_seconds: float = 0.0
    delta_time_seconds: float = 0.0
    overlap_seconds: float = 0.0
    producer: str = ""
    model_version: str = ""
    confidence: float | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        validate_identifier(self.processing_run_id, "processing_run_id")
        validate_identifier(self.environment_id, "environment_id")
        validate_identifier(self.source_id, "source_id")
        validate_identifier(self.asset_id, "asset_id")
        validate_interval(self.start_time, self.end_time, required=True)
        if self.sequence_number < 0:
            raise DomainValidationError("sequence_number no puede ser negativo")
        absolute_duration = (self.end_time - self.start_time).total_seconds()  # type: ignore[operator]
        if self.delta_time_seconds == 0.0 and absolute_duration > 0:
            self.delta_time_seconds = absolute_duration
        if self.end_offset_seconds == 0.0 and absolute_duration > 0:
            self.end_offset_seconds = self.start_offset_seconds + absolute_duration
        if self.start_offset_seconds < 0 or self.end_offset_seconds < self.start_offset_seconds:
            raise DomainValidationError("Los offsets de ProcessingWindow son inválidos")
        if self.delta_time_seconds <= 0:
            raise DomainValidationError("delta_time_seconds debe ser positivo")
        if self.overlap_seconds < 0 or self.overlap_seconds >= self.delta_time_seconds:
            raise DomainValidationError(
                "overlap_seconds debe ser no negativo y menor que delta_time_seconds"
            )
        relative_duration = self.end_offset_seconds - self.start_offset_seconds
        if abs(relative_duration - absolute_duration) > 1e-6:
            raise DomainValidationError(
                "El intervalo relativo y el intervalo absoluto deben tener igual duración"
            )
        if not self.producer.strip() or not self.model_version.strip():
            raise DomainValidationError("producer y model_version son obligatorios")
        validate_confidence(self.confidence)
        validate_timestamp(self.created_at, "created_at")


@dataclass
class Event(SerializableDomainModel):
    """Acción o cambio de estado detectado, distinto de una observación."""

    id: str = field(default_factory=_new_id)
    event_type: str = ""
    description: str = ""
    environment_id: str = ""
    source_id: str | None = None
    processing_run_id: str | None = None
    processing_window_id: str | None = None
    entity_ids: list[str] = field(default_factory=list)
    evidence_reference_ids: list[str] = field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    producer: str = ""
    model_version: str = ""
    confidence: float | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    # Localización dentro del activo y contenido legible (Fase C). Todo texto
    # generado conserva su procedencia en ``metadata["generation"]``.
    asset_id: str | None = None
    sequence_number: int = 0
    start_offset_seconds: float | None = None
    end_offset_seconds: float | None = None
    title: str = ""
    summary: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    keyframes: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        validate_identifier(self.environment_id, "environment_id")
        if not self.event_type.strip():
            raise DomainValidationError("Event.event_type es obligatorio")
        validate_interval(self.start_time, self.end_time, required=True)
        if not self.producer.strip() or not self.model_version.strip():
            raise DomainValidationError("producer y model_version son obligatorios")
        validate_confidence(self.confidence)
        validate_timestamp(self.created_at, "created_at")
