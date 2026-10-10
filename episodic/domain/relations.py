"""Relaciones verificables e hipótesis deliberadamente separadas."""
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
    validate_timestamp,
)
from episodic.domain.times import utc_now
from episodic.domain.types import (
    HypothesisState,
    LogicalRelationType,
    TransitionMode,
    ValidationState,
)


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Relation(SerializableDomainModel):
    """Vínculo tipado con procedencia, evidencias y validación explícita."""

    id: str = field(default_factory=_new_id)
    relation_type: str = ""
    origin_type: str = ""
    origin_id: str = ""
    destination_type: str = ""
    destination_id: str = ""
    method: str = ""
    evidence_reference_ids: list[str] = field(default_factory=list)
    validation_state: ValidationState = ValidationState.PROPOSED
    producer: str = ""
    model_version: str = ""
    confidence: float | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        raw_type = (
            self.relation_type.value
            if isinstance(self.relation_type, LogicalRelationType)
            else str(self.relation_type)
        )
        normalized_type = raw_type.strip().upper()
        try:
            relation_type = LogicalRelationType(normalized_type)
        except ValueError as exc:
            raise DomainValidationError(
                f"Tipo de relación no soportado: {self.relation_type}"
            ) from exc
        self.relation_type = relation_type.value
        for name, value in (
            ("relation_type", self.relation_type),
            ("origin_type", self.origin_type),
            ("origin_id", self.origin_id),
            ("destination_type", self.destination_type),
            ("destination_id", self.destination_id),
            ("method", self.method),
            ("producer", self.producer),
            ("model_version", self.model_version),
        ):
            if not value.strip():
                raise DomainValidationError(f"Relation.{name} es obligatorio")
        if relation_type == LogicalRelationType.POSSIBLY_SAME_ENTITY_AS:
            raise DomainValidationError(
                "POSSIBLY_SAME_ENTITY_AS debe representarse como Hypothesis"
            )
        if relation_type == LogicalRelationType.SAME_ENTITY_AS:
            if self.origin_type != "entity" or self.destination_type != "entity":
                raise DomainValidationError(
                    "SAME_ENTITY_AS solo puede enlazar entidades"
                )
            if self.validation_state != ValidationState.VALIDATED:
                raise DomainValidationError(
                    "SAME_ENTITY_AS exige una decisión validada"
                )
        if relation_type == LogicalRelationType.MOVED_TO:
            try:
                TransitionMode(self.metadata.get("transition_mode", ""))
            except ValueError as exc:
                raise DomainValidationError(
                    "MOVED_TO requiere transition_mode=observed|inferred"
                ) from exc
        derived = (
            relation_type == LogicalRelationType.DERIVED_FROM
            or self.metadata.get("derivation") == "inferred"
            or self.metadata.get("transition_mode") == TransitionMode.INFERRED.value
        )
        if derived and not self.evidence_reference_ids:
            raise DomainValidationError(
                "Una relación derivada requiere referencias de evidencia"
            )
        gap = self.metadata.get("observation_gap")
        if gap is not None:
            _validate_observation_gap(gap)
        validate_confidence(self.confidence)
        validate_timestamp(self.created_at, "created_at")


def _validate_observation_gap(gap: Any) -> None:
    if not isinstance(gap, dict):
        raise DomainValidationError("observation_gap debe ser un objeto")
    try:
        start = datetime.fromisoformat(gap["start_time"])
        end = datetime.fromisoformat(gap["end_time"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainValidationError(
            "observation_gap requiere start_time y end_time ISO-8601"
        ) from exc
    validate_timestamp(start, "observation_gap.start_time")
    validate_timestamp(end, "observation_gap.end_time")
    if end < start:
        raise DomainValidationError(
            "observation_gap.end_time no puede ser anterior a start_time"
        )
    duration = gap.get("duration_seconds")
    if duration is not None and abs(float(duration) - (end - start).total_seconds()) > 1e-6:
        raise DomainValidationError("observation_gap.duration_seconds es inconsistente")


@dataclass
class Hypothesis(SerializableDomainModel):
    """Interpretación pendiente; nunca se persiste como Relation validada."""

    id: str = field(default_factory=_new_id)
    statement: str = ""
    subject_type: str = ""
    subject_id: str = ""
    object_type: str | None = None
    object_id: str | None = None
    evidence_reference_ids: list[str] = field(default_factory=list)
    state: HypothesisState = HypothesisState.OPEN
    producer: str = ""
    model_version: str = ""
    confidence: float | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        validate_identifier(self.subject_id, "subject_id")
        if not self.statement.strip():
            raise DomainValidationError("Hypothesis.statement es obligatorio")
        if not self.subject_type.strip():
            raise DomainValidationError("Hypothesis.subject_type es obligatorio")
        if (self.object_type is None) != (self.object_id is None):
            raise DomainValidationError("object_type y object_id deben declararse juntos")
        if not self.producer.strip() or not self.model_version.strip():
            raise DomainValidationError("producer y model_version son obligatorios")
        validate_confidence(self.confidence)
        validate_timestamp(self.created_at, "created_at")
