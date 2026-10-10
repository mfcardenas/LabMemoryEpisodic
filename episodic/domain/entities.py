"""Entidades del dominio episódico.

Todas son dataclasses con UUID str como ID global.
No importan Qdrant, FastEmbed, OpenCV ni HTTP.
Cada entidad conserva procedencia, tiempos y estado.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from episodic.domain.model import (
    SerializableDomainModel,
    validate_confidence,
    validate_identifier,
    validate_interval,
    validate_timestamp,
)
from episodic.domain.times import Interval, TimeSpec, utc_now
from episodic.domain.types import (
    EvidenceMediaType,
    EvidenceState,
    IndexingState,
    ProvenanceKind,
    RepresentationSpace,
    SearchIntent,
    SourceType,
    ValidationState,
    Verdict,
)


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Source — fuente de captura
# ---------------------------------------------------------------------------

@dataclass
class Source(SerializableDomainModel):
    """Fuente de captura: cámara, micrófono, conjunto de fotos, etc."""
    id: str = field(default_factory=_new_id)
    environment_id: str = "env-legacy-default"
    name: str = ""
    source_type: SourceType = SourceType.CAMERA
    scope: str = ""                    # e.g. "Entrada principal"
    clock_source: str = "system"       # reloj asociado
    metadata: Dict[str, Any] = field(default_factory=dict)
    producer: str = "ingest"
    model_version: Optional[str] = None
    confidence: Optional[float] = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        validate_identifier(self.environment_id, "environment_id")
        if not self.name.strip():
            raise ValueError("Source.name es obligatorio")
        validate_confidence(self.confidence)
        validate_timestamp(self.created_at, "created_at")


# ---------------------------------------------------------------------------
# CaptureSession — contexto de ingesta
# ---------------------------------------------------------------------------

@dataclass
class CaptureSession:
    """Sesión de captura: agrupa evidencias de una misma ingesta."""
    id: str = field(default_factory=_new_id)
    source_id: str = ""
    external_id: Optional[str] = None  # ID de la sesión en el sistema origen
    started_at: Optional[TimeSpec] = None
    ended_at: Optional[TimeSpec] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    ingested_at: datetime = field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Evidence — medio verificable
# ---------------------------------------------------------------------------

@dataclass
class Evidence:
    """Medio verificable: imagen, audio, vídeo, texto con hash y URI interna."""
    id: str = field(default_factory=_new_id)
    session_id: str = ""
    source_id: str = ""
    media_type: EvidenceMediaType = EvidenceMediaType.IMAGE
    original_filename: Optional[str] = None
    internal_uri: str = ""              # ruta relativa en el almacén
    content_hash: str = ""              # SHA-256 del blob
    file_size_bytes: int = 0
    capture_time: Optional[TimeSpec] = None
    ingested_at: datetime = field(default_factory=utc_now)
    state: EvidenceState = EvidenceState.ACTIVE
    # Clave de idempotencia: (source_id, session_id, external_capture_id)
    external_capture_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Observation — dato observado, declarado o derivado
# ---------------------------------------------------------------------------

@dataclass
class Observation(SerializableDomainModel):
    """Dato observado con procedencia completa.

    Una detección visual, una declaración humana y una descripción
    generada tienen orígenes distintos — ProvenanceKind lo registra.
    """
    id: str = field(default_factory=_new_id)
    episode_id: Optional[str] = None
    content: str = ""                   # texto de la observación
    description: str = ""
    modality: str = "unknown"
    observation_type: str = "legacy_observation"
    validation_state: ValidationState = ValidationState.PROPOSED
    attributes: Dict[str, Any] = field(default_factory=dict)
    provenance_kind: ProvenanceKind = ProvenanceKind.HUMAN_DECLARATION
    author: str = ""                    # humano o herramienta
    tool_version: Optional[str] = None
    confidence: Optional[float] = None  # score de la herramienta, no probabilidad
    evidence_ids: List[str] = field(default_factory=list)
    entity_id: Optional[str] = None     # entidad asociada, si la hay
    capture_time: Optional[TimeSpec] = None
    environment_id: Optional[str] = None
    source_id: Optional[str] = None
    asset_id: Optional[str] = None
    processing_run_id: Optional[str] = None
    processing_window_id: Optional[str] = None
    evidence_reference_ids: List[str] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    producer: str = ""
    model_version: Optional[str] = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        if not self.content.strip() and self.description.strip():
            self.content = self.description
        if not self.description.strip() and self.content.strip():
            self.description = self.content
        if not self.content.strip():
            raise ValueError("Observation.content es obligatorio")
        if not self.modality.strip():
            raise ValueError("Observation.modality es obligatorio")
        if not self.observation_type.strip():
            raise ValueError("Observation.observation_type es obligatorio")
        if not self.producer:
            self.producer = self.author or self.provenance_kind.value
        if self.model_version is None:
            self.model_version = self.tool_version
        if self.capture_time and self.capture_time.absolute and self.start_time is None:
            self.start_time = self.capture_time.absolute
            self.end_time = self.capture_time.absolute
        validate_interval(self.start_time, self.end_time)
        validate_confidence(self.confidence)
        validate_timestamp(self.created_at, "created_at")

    @property
    def observation_id(self) -> str:
        """Alias de contrato externo; el ID canónico sigue siendo ``id``."""
        return self.id

    @property
    def evidence_references(self) -> List[str]:
        return self.evidence_reference_ids

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        payload["observation_id"] = self.id
        payload["evidence_references"] = list(self.evidence_reference_ids)
        return payload


# ---------------------------------------------------------------------------
# Episode — unidad temporal/contextual
# ---------------------------------------------------------------------------

@dataclass
class Episode(SerializableDomainModel):
    """Unidad temporal/contextual con observaciones y evidencias vinculadas."""
    id: str = field(default_factory=_new_id)
    title: str = ""
    external_id: Optional[str] = None   # e.g. "ATLAS-EP-001"
    interval: Optional[Interval] = None
    source_ids: List[str] = field(default_factory=list)
    location: str = ""
    tags: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    observation_ids: List[str] = field(default_factory=list)
    indexable_text: str = ""            # texto principal para embeddings
    created_at: datetime = field(default_factory=utc_now)
    is_visible: bool = True             # visibilidad canónica
    session_id: Optional[str] = None
    environment_ids: List[str] = field(default_factory=list)
    microepisode_ids: List[str] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    producer: str = "legacy_composer"
    model_version: Optional[str] = "v1"
    confidence: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        if self.interval and self.start_time is None and self.end_time is None:
            if self.interval.start and self.interval.end:
                self.start_time = self.interval.start.absolute
                self.end_time = self.interval.end.absolute
        elif self.interval is None and self.start_time is not None and self.end_time is not None:
            self.interval = Interval(
                start=TimeSpec.from_utc(self.start_time, clock_source="episode"),
                end=TimeSpec.from_utc(self.end_time, clock_source="episode"),
            )
        validate_interval(self.start_time, self.end_time)
        validate_confidence(self.confidence)
        validate_timestamp(self.created_at, "created_at")


# ---------------------------------------------------------------------------
# Annotation — enriquecimiento versionado
# ---------------------------------------------------------------------------

@dataclass
class Annotation:
    """Enriquecimiento versionado: no reemplaza el original.

    Cada anotación es una revisión nueva. La versión anterior y su
    procedencia permanecen accesibles.
    """
    id: str = field(default_factory=_new_id)
    episode_id: str = ""
    revision_number: int = 1
    content: str = ""
    provenance_kind: ProvenanceKind = ProvenanceKind.HUMAN_DECLARATION
    author: str = ""
    tool_version: Optional[str] = None
    evidence_ids: List[str] = field(default_factory=list)
    supersedes_id: Optional[str] = None  # ID de la anotación previa
    created_at: datetime = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Entity — identidad mínima
# ---------------------------------------------------------------------------

@dataclass
class Entity(SerializableDomainModel):
    """Identidad persistente.

    Un track local no es una entidad persistente.
    Una etiqueta repetida no es una identidad.
    Dos entidades con la misma etiqueta siguen separadas.
    """
    id: str = field(default_factory=_new_id)
    label: str = ""
    entity_type: str = ""               # "person", "object", etc.
    description: str = ""
    created_by: str = ""                # quién declaró la identidad
    provenance_kind: ProvenanceKind = ProvenanceKind.HUMAN_DECLARATION
    environment_ids: List[str] = field(default_factory=list)
    producer: str = "human"
    model_version: Optional[str] = None
    confidence: Optional[float] = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        if not self.label.strip():
            raise ValueError("Entity.label es obligatorio")
        validate_confidence(self.confidence)
        validate_timestamp(self.created_at, "created_at")


# ---------------------------------------------------------------------------
# Representation — embedding versionado
# ---------------------------------------------------------------------------

@dataclass
class Representation:
    """Embedding almacenado con modelo, espacio, revisión y registro.

    No mezclar representaciones de encoders diferentes aunque tengan
    la misma dimensión.  Cambiar un modelo crea otro espacio.
    """
    id: str = field(default_factory=_new_id)
    target_type: str = ""               # "episode", "evidence"
    target_id: str = ""                 # ID del episodio o evidencia
    space: RepresentationSpace = RepresentationSpace.NOMIC_TEXT
    model_id: str = ""                  # e.g. "nomic-ai/nomic-embed-text-v1.5"
    model_revision: str = ""            # checksum o tag
    dimension: int = 0
    vector: Optional[List[float]] = None  # en tránsito; no se persiste largo plazo
    indexing_state: IndexingState = IndexingState.PENDING
    qdrant_point_id: Optional[str] = None  # mapping a ID de Qdrant
    created_at: datetime = field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# QueryRun — registro de consulta
# ---------------------------------------------------------------------------

@dataclass
class QueryResult:
    """Un candidato individual dentro de un QueryRun."""
    episode_id: str = ""
    score: float = 0.0
    rank: int = 0
    space: Optional[RepresentationSpace] = None
    original_score: float = 0.0         # score bruto del encoder
    evidence_ids: List[str] = field(default_factory=list)
    is_direct_match: bool = True        # vs. contexto del episodio
    verdict: Verdict = Verdict.CANDIDATE
    reasons: List[str] = field(default_factory=list)


@dataclass
class QueryRun:
    """Consulta completa con candidatos, decisión y referencias."""
    id: str = field(default_factory=_new_id)
    intent: SearchIntent = SearchIntent.SEMANTIC_SEARCH
    text_query: Optional[str] = None
    image_query_evidence_id: Optional[str] = None
    source_filter: Optional[List[str]] = None
    interval_filter: Optional[Interval] = None
    entity_filter: Optional[str] = None
    limit: int = 10
    results: List[QueryResult] = field(default_factory=list)
    overall_verdict: Verdict = Verdict.NO_MATCH
    reasons: List[str] = field(default_factory=list)
    executed_at: datetime = field(default_factory=utc_now)
    duration_ms: float = 0.0
    config: Dict[str, Any] = field(default_factory=dict)  # umbrales usados
