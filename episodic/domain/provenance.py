"""Procedencia primaria: ambientes, activos y referencias de evidencia."""
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
from episodic.domain.types import EvidenceMediaType, EvidenceReferenceKind


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Environment(SerializableDomainModel):
    """Espacio físico o lógico en el que operan una o varias fuentes."""

    id: str = field(default_factory=_new_id)
    name: str = ""
    description: str = ""
    producer: str = "human"
    model_version: str | None = None
    confidence: float | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        if not self.name.strip():
            raise DomainValidationError("Environment.name es obligatorio")
        validate_confidence(self.confidence)
        validate_timestamp(self.created_at, "created_at")


@dataclass
class Asset(SerializableDomainModel):
    """Archivo original o referencia inmutable a una fuente."""

    id: str = field(default_factory=_new_id)
    environment_id: str = ""
    source_id: str = ""
    uri: str = ""
    media_type: EvidenceMediaType = EvidenceMediaType.IMAGE
    mime_type: str = "application/octet-stream"
    content_hash: str = ""
    original_filename: str | None = None
    size_bytes: int | None = None
    duration_seconds: float | None = None
    captured_at: datetime | None = None
    producer: str = "ingest"
    model_version: str | None = None
    confidence: float | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        validate_identifier(self.environment_id, "environment_id")
        validate_identifier(self.source_id, "source_id")
        if not self.uri.strip():
            raise DomainValidationError("Asset.uri es obligatorio")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise DomainValidationError("size_bytes no puede ser negativo")
        if not self.mime_type.strip():
            raise DomainValidationError("mime_type es obligatorio")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise DomainValidationError("duration_seconds no puede ser negativo")
        if self.captured_at is not None:
            validate_timestamp(self.captured_at, "captured_at")
        validate_confidence(self.confidence)
        validate_timestamp(self.created_at, "created_at")


@dataclass
class EvidenceReference(SerializableDomainModel):
    """Referencia exacta y verificable dentro de un activo."""

    id: str = field(default_factory=_new_id)
    asset_id: str = ""
    reference_kind: EvidenceReferenceKind = EvidenceReferenceKind.FILE
    start_time: datetime | None = None
    end_time: datetime | None = None
    start_offset_seconds: float | None = None
    end_offset_seconds: float | None = None
    frame_number: int | None = None
    region: dict[str, float] | None = None
    page_number: int | None = None
    text_start: int | None = None
    text_end: int | None = None
    text_quote: str | None = None
    producer: str = "system"
    model_version: str | None = None
    confidence: float | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        validate_identifier(self.asset_id, "asset_id")
        validate_interval(self.start_time, self.end_time)
        validate_confidence(self.confidence)
        validate_timestamp(self.created_at, "created_at")

        if self.reference_kind == EvidenceReferenceKind.AUDIOVISUAL_INTERVAL:
            absolute = self.start_time is not None and self.end_time is not None
            relative = (
                self.start_offset_seconds is not None
                and self.end_offset_seconds is not None
            )
            if not absolute and not relative:
                raise DomainValidationError(
                    "Una evidencia audiovisual requiere intervalo absoluto o relativo"
                )
            if relative and self.end_offset_seconds < self.start_offset_seconds:
                raise DomainValidationError("El intervalo audiovisual relativo es inválido")
        elif self.reference_kind == EvidenceReferenceKind.FRAME:
            if self.frame_number is None or self.frame_number < 0:
                raise DomainValidationError("Una evidencia de fotograma requiere frame_number >= 0")
        elif self.reference_kind == EvidenceReferenceKind.REGION:
            required = {"x", "y", "width", "height"}
            if not self.region or not required.issubset(self.region):
                raise DomainValidationError("Una región requiere x, y, width y height")
            if self.region["width"] <= 0 or self.region["height"] <= 0:
                raise DomainValidationError("La región debe tener área positiva")
        elif self.reference_kind == EvidenceReferenceKind.PAGE:
            if self.page_number is None or self.page_number < 1:
                raise DomainValidationError("Una evidencia de página requiere page_number >= 1")
        elif self.reference_kind == EvidenceReferenceKind.TEXT_FRAGMENT:
            offsets = self.text_start is not None and self.text_end is not None
            if not offsets and not self.text_quote:
                raise DomainValidationError(
                    "Un fragmento textual requiere offsets o una cita localizada"
                )
            if offsets and (self.text_start < 0 or self.text_end < self.text_start):
                raise DomainValidationError("Los offsets del fragmento textual son inválidos")
