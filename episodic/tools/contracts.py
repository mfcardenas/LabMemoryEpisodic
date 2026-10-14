"""Entradas y salidas tipadas para la API de herramientas de memoria."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from episodic.domain.types import ValidationState


class ToolInput(BaseModel):
    """Base estricta: una herramienta no acepta parametros silenciosos."""

    model_config = ConfigDict(extra="forbid")


class IntervalToolInput(ToolInput):
    start_time: datetime | None = None
    end_time: datetime | None = None

    @model_validator(mode="after")
    def validate_interval(self):
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start_time y end_time deben declararse juntos")
        if self.start_time is not None:
            if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
                raise ValueError("start_time y end_time deben incluir zona horaria")
            if self.start_time >= self.end_time:
                raise ValueError("start_time debe ser anterior a end_time")
        return self


class SearchObservationsInput(IntervalToolInput):
    query: str | None = None
    environment_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    modalities: list[str] = Field(default_factory=list)
    entity_id: str | None = None
    observation_types: list[str] = Field(default_factory=list)
    min_confidence: float | None = Field(None, ge=0, le=1)
    validation_states: list[ValidationState] = Field(default_factory=list)
    limit: int = Field(10, ge=1, le=100)


class SearchEpisodesInput(SearchObservationsInput):
    episode_types: list[str] = Field(default_factory=list)
    expand_relations: bool = False
    relation_types: list[str] = Field(default_factory=list)


class GetEpisodeInput(ToolInput):
    episode_id: str = Field(min_length=1)
    include_relations: bool = True
    include_hypotheses: bool = True


class GetEntityTimelineInput(IntervalToolInput):
    entity_id: str = Field(min_length=1)
    validation_states: list[ValidationState] = Field(default_factory=list)
    include_hypotheses: bool = True
    limit: int = Field(100, ge=1, le=1000)


class GetLastConfirmedObservationInput(ToolInput):
    entity_id: str = Field(min_length=1)


class ExpandRelationsInput(ToolInput):
    node_type: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    relation_types: list[str] = Field(default_factory=list)
    validation_states: list[ValidationState] = Field(default_factory=list)
    max_depth: int = Field(1, ge=1, le=5)
    include_hypotheses: bool = True


class GetSupportingEvidenceInput(ToolInput):
    target_type: str = Field(min_length=1)
    target_id: str = Field(min_length=1)


class FindContradictionsInput(ToolInput):
    node_type: str | None = None
    node_id: str | None = None
    entity_id: str | None = None

    @model_validator(mode="after")
    def validate_node(self):
        if (self.node_type is None) != (self.node_id is None):
            raise ValueError("node_type y node_id deben declararse juntos")
        if self.entity_id and self.node_id:
            raise ValueError("use entity_id o node_type/node_id, no ambos")
        return self


class CompareEntitiesInput(ToolInput):
    entity_a_id: str = Field(min_length=1)
    entity_b_id: str = Field(min_length=1)
    include_observations: bool = True

    @model_validator(mode="after")
    def different_entities(self):
        if self.entity_a_id == self.entity_b_id:
            raise ValueError("compare_entities requiere dos entidades distintas")
        return self


class SearchDocumentsInput(IntervalToolInput):
    query: str | None = None
    environment_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    min_confidence: float | None = Field(None, ge=0, le=1)
    validation_states: list[ValidationState] = Field(default_factory=list)
    limit: int = Field(10, ge=1, le=100)


class GetEnvironmentTopologyInput(ToolInput):
    environment_ids: list[str] = Field(default_factory=list)
    include_inferred: bool = True
    include_hypotheses: bool = True


# -- Herramientas para agentes (Fase A1): compactas, paginadas, sin barridos --


class SearchMomentsInput(IntervalToolInput):
    """Momentos (eventos, habla suelta, fragmentos de documento) que responden a
    la pregunta; la pregunta se entiende antes (tiempo, intención, ambientes)."""

    query: str = Field(min_length=1)
    environment_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    modalities: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list, description="video_event, speech_segment, document_text_fragment…")
    max_episodes: int = Field(5, ge=1, le=50)
    per_episode: int = Field(3, ge=1, le=20)
    neighbors: int | None = Field(None, ge=0, le=5, description="eventos antes/después de cada momento; omitido: según la pregunta")
    include_summaries: bool = False
    max_chars: int = Field(600, ge=80, le=8000, description="caracteres de texto por momento")


class GetEventInput(ToolInput):
    event_id: str = Field(min_length=1)
    max_chars: int = Field(4000, ge=80, le=20000)


class GetEventNeighborsInput(ToolInput):
    event_id: str = Field(min_length=1)
    before: int = Field(2, ge=0, le=20)
    after: int = Field(2, ge=0, le=20)
    max_chars: int = Field(400, ge=80, le=8000)


class GetEpisodeSummaryInput(ToolInput):
    episode_id: str = Field(min_length=1)
    include_revisions: bool = False


class BrowseMemoryInput(ToolInput):
    """Navegación de arriba abajo: environments → days → episodes → events."""

    level: Literal["environments", "days", "episodes", "events"]
    environment_id: str | None = None
    day: str | None = Field(None, description="AAAA-MM-DD en la zona horaria del servidor (niveles episodes)")
    episode_id: str | None = Field(None, description="necesario en el nivel events")
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.level in {"days", "episodes"} and not self.environment_id:
            raise ValueError(f"el nivel {self.level} necesita environment_id")
        if self.level == "events" and not self.episode_id:
            raise ValueError("el nivel events necesita episode_id")
        return self


class GetDocumentPassagesInput(ToolInput):
    episode_id: str = Field(min_length=1)
    query: str | None = None
    limit: int = Field(5, ge=1, le=50)
    max_chars: int = Field(800, ge=80, le=8000)


class MomentRecord(BaseModel):
    """Un momento citable: pasaje con su posición en el episodio y en el vídeo."""

    record_kind: Literal["fact"] = "fact"
    id: str
    kind: str
    event_id: str | None = None
    observation_id: str | None = None
    episode_id: str
    episode_external_id: str | None = None
    episode_title: str = ""
    environment_id: str | None = None
    source_id: str | None = None
    title: str = ""
    text: str = ""
    snippet: str = ""
    start_time: datetime | None = None
    end_time: datetime | None = None
    start_offset_seconds: float | None = None
    end_offset_seconds: float | None = None
    page_number: int | None = None
    sequence_number: int | None = None
    scores: dict[str, float | int | None] = Field(default_factory=dict)
    kept_by: str | None = None
    actions: list[str] = Field(default_factory=list)
    actors: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    visible_text: list[str] = Field(default_factory=list)
    speech: list[dict[str, Any]] = Field(default_factory=list)
    keyframes: int = 0
    model: str | None = None
    before: list["MomentRecord"] = Field(default_factory=list)
    after: list["MomentRecord"] = Field(default_factory=list)


class SummaryRecord(BaseModel):
    """Texto generado por el modelo (nunca un hecho observado)."""

    record_kind: Literal["generated"] = "generated"
    id: str
    scope: str
    scope_key: str
    revision: int
    is_current: bool
    title: str
    summary: str
    narrative: str
    highlights: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    model_id: str
    created_at: datetime


class BrowseItemRecord(BaseModel):
    record_kind: Literal["fact"] = "fact"
    level: str
    id: str
    label: str
    external_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    count: int | None = None
    summary_title: str | None = None
    summary_text: str | None = None
    offset_seconds: float | None = None


class EvidenceToolRecord(BaseModel):
    record_kind: Literal["evidence"] = "evidence"
    id: str
    asset_id: str
    reference_kind: str
    locator: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    producer: str
    model_version: str | None = None


class ObservationToolRecord(BaseModel):
    record_kind: Literal["fact"] = "fact"
    id: str
    episode_id: str | None = None
    entity_id: str | None = None
    environment_id: str | None = None
    source_id: str | None = None
    asset_id: str | None = None
    modality: str
    observation_type: str
    description: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    start_time: datetime | None = None
    end_time: datetime | None = None
    validation_state: ValidationState
    confidence: float | None = None
    evidence_reference_ids: list[str] = Field(default_factory=list)
    semantic_score: float | None = None


class EpisodeToolRecord(BaseModel):
    record_kind: Literal["fact"] = "fact"
    id: str
    title: str
    episode_type: str
    environment_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    confidence: float | None = None
    evidence_reference_ids: list[str] = Field(default_factory=list)
    scores: dict[str, float | None] = Field(default_factory=dict)


class EntityToolRecord(BaseModel):
    record_kind: Literal["fact"] = "fact"
    id: str
    label: str
    entity_type: str
    description: str
    environment_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None


class RelationToolRecord(BaseModel):
    record_kind: Literal["fact"] = "fact"
    id: str
    relation_type: str
    origin_type: str
    origin_id: str
    destination_type: str
    destination_id: str
    validation_state: ValidationState
    is_validated: bool
    method: str
    confidence: float | None = None
    evidence_reference_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HypothesisToolRecord(BaseModel):
    record_kind: Literal["hypothesis"] = "hypothesis"
    id: str
    statement: str
    subject_type: str
    subject_id: str
    object_type: str | None = None
    object_id: str | None = None
    state: str
    confidence: float | None = None
    evidence_reference_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentToolRecord(BaseModel):
    record_kind: Literal["fact"] = "fact"
    id: str
    observation_id: str
    asset_id: str
    environment_id: str | None = None
    source_id: str | None = None
    text: str
    page_numbers: list[int] = Field(default_factory=list)
    text_fragments: list[dict[str, Any]] = Field(default_factory=list)
    evidence_reference_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None
    relevance_score: float | None = None


class TopologySourceRecord(BaseModel):
    record_kind: Literal["fact"] = "fact"
    id: str
    environment_id: str
    name: str
    source_type: str
    confidence: float | None = None


class EnvironmentTopologyRecord(BaseModel):
    record_kind: Literal["fact"] = "fact"
    id: str
    name: str
    description: str
    source_ids: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    episode_ids: list[str] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None


class EntityComparisonRecord(BaseModel):
    record_kind: Literal["fact"] = "fact"
    entity_a_id: str
    entity_b_id: str
    identity_status: Literal["validated_same", "hypothesized_same", "undetermined"]
    shared_environment_ids: list[str] = Field(default_factory=list)
    entity_a_only_environment_ids: list[str] = Field(default_factory=list)
    entity_b_only_environment_ids: list[str] = Field(default_factory=list)
    latest_observation_a_id: str | None = None
    latest_observation_b_id: str | None = None
    temporal_overlap: bool
    supporting_relation_ids: list[str] = Field(default_factory=list)
    supporting_hypothesis_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None


class MemoryToolOutput(BaseModel):
    """Sobre estable consumible por codigo determinista o agentes futuros."""

    tool: str
    result_ids: list[str] = Field(default_factory=list)
    observations: list[ObservationToolRecord] = Field(default_factory=list)
    episodes: list[EpisodeToolRecord] = Field(default_factory=list)
    entities: list[EntityToolRecord] = Field(default_factory=list)
    relations: list[RelationToolRecord] = Field(default_factory=list)
    hypotheses: list[HypothesisToolRecord] = Field(default_factory=list)
    evidence: list[EvidenceToolRecord] = Field(default_factory=list)
    documents: list[DocumentToolRecord] = Field(default_factory=list)
    environments: list[EnvironmentTopologyRecord] = Field(default_factory=list)
    sources: list[TopologySourceRecord] = Field(default_factory=list)
    comparisons: list[EntityComparisonRecord] = Field(default_factory=list)
    moments: list[MomentRecord] = Field(default_factory=list)
    summaries: list[SummaryRecord] = Field(default_factory=list)
    browse: list[BrowseItemRecord] = Field(default_factory=list)
    confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolDescriptor(BaseModel):
    name: str
    description: str
    read_only: Literal[True] = True
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
