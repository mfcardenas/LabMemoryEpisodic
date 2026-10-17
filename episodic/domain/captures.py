"""Captura subida en conjunto y trabajos asíncronos que la procesan."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from episodic.domain.model import (
    DomainValidationError,
    SerializableDomainModel,
    validate_identifier,
    validate_optional_timestamp,
    validate_timestamp,
)
from episodic.domain.times import utc_now


def _new_id() -> str:
    return str(uuid.uuid4())


class CaptureStatus(str, Enum):
    RECEIVED = "received"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class CaptureTimeOrigin(str, Enum):
    """De dónde procede la hora de la captura; nunca se presenta como declarada si no lo es."""

    DECLARED = "declared"
    MEDIA_METADATA = "media_metadata"
    FILE_TIME = "file_time"  # fecha de modificación del fichero en la carpeta vigilada
    UPLOAD_TIME = "upload_time"


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StageState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Capture(SerializableDomainModel):
    """Archivos tomados juntos en un entorno; origen de exactamente un episodio."""

    id: str = field(default_factory=_new_id)
    environment_id: str = ""
    title: str = ""
    asset_ids: list[str] = field(default_factory=list)
    captured_at: datetime | None = None
    captured_at_origin: CaptureTimeOrigin = CaptureTimeOrigin.UPLOAD_TIME
    status: CaptureStatus = CaptureStatus.RECEIVED
    episode_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        validate_identifier(self.environment_id, "environment_id")
        if not self.title.strip():
            raise DomainValidationError("Capture.title es obligatorio")
        if not self.asset_ids:
            raise DomainValidationError("Una captura requiere al menos un activo")
        validate_optional_timestamp(self.captured_at, "captured_at")
        validate_timestamp(self.created_at, "created_at")


@dataclass
class JobStage(SerializableDomainModel):
    name: str = ""
    label: str = ""
    state: StageState = StageState.PENDING
    detail: str = ""


@dataclass
class Job(SerializableDomainModel):
    """Trabajo durable de la cola local; sus etapas son visibles para el usuario."""

    id: str = field(default_factory=_new_id)
    job_type: str = ""
    target_id: str = ""
    state: JobState = JobState.QUEUED
    stages: list[JobStage] = field(default_factory=list)
    error: str | None = None
    attempts: int = 0
    result: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        validate_identifier(self.job_type, "job_type")
        validate_identifier(self.target_id, "target_id")
        validate_timestamp(self.created_at, "created_at")
        validate_timestamp(self.updated_at, "updated_at")

    @property
    def progress(self) -> float:
        if not self.stages:
            return 1.0 if self.state == JobState.COMPLETED else 0.0
        done = sum(1 for stage in self.stages if stage.state == StageState.COMPLETED)
        return done / len(self.stages)
