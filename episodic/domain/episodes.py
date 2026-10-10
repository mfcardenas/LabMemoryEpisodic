"""Composición episódica por encima de observaciones y eventos."""
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


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class MicroEpisode(SerializableDomainModel):
    """Secuencia coherente de observaciones y eventos."""

    id: str = field(default_factory=_new_id)
    title: str = ""
    environment_id: str = ""
    source_ids: list[str] = field(default_factory=list)
    observation_ids: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    evidence_reference_ids: list[str] = field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    producer: str = ""
    model_version: str = ""
    confidence: float | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        validate_identifier(self.environment_id, "environment_id")
        if not self.title.strip():
            raise DomainValidationError("MicroEpisode.title es obligatorio")
        if not self.observation_ids and not self.event_ids:
            raise DomainValidationError(
                "Un MicroEpisode requiere al menos una observación o evento"
            )
        validate_interval(self.start_time, self.end_time, required=True)
        if not self.producer.strip() or not self.model_version.strip():
            raise DomainValidationError("producer y model_version son obligatorios")
        validate_confidence(self.confidence)
        validate_timestamp(self.created_at, "created_at")
