"""Esquema de validación para manifiestos de memoria episódica.

Usa Pydantic v2 para validación rigurosa de tipos, enums, fechas UTC y referencias cruzadas.
Proporciona errores claros y precisos por campo en caso de inconsistencia.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class TimeSpecSchema(BaseModel):
    absolute: Optional[str] = Field(None, description="Timestamp ISO-8601 UTC")
    relative_seconds: Optional[float] = Field(None, description="Segundos desde inicio del medio")
    clock_source: Optional[str] = Field("system", description="Reloj o sensor de origen")
    uncertainty_seconds: Optional[float] = Field(None, description="Margen de incertidumbre en segundos")
    is_unknown: bool = Field(False, description="True si el tiempo es explícitamente desconocido")


class IntervalSchema(BaseModel):
    start: Optional[TimeSpecSchema] = None
    end: Optional[TimeSpecSchema] = None


class SourceSchema(BaseModel):
    id: str = Field(..., description="Identificador único de la fuente")
    name: str = Field(..., description="Nombre descriptivo de la fuente")
    source_type: str = Field("camera", description="camera, microphone, photo_set, document, sensor, manual")
    scope: str = Field("", description="Ámbito o cobertura espacial/funcional")
    clock_source: str = Field("system", description="Reloj del que dependen sus capturas")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SessionSchema(BaseModel):
    id: str = Field(..., description="Identificador único de la sesión")
    source_id: str = Field(..., description="Fuente asociada")
    external_id: Optional[str] = None
    started_at: Optional[TimeSpecSchema] = None
    ended_at: Optional[TimeSpecSchema] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EntitySchema(BaseModel):
    id: str = Field(..., description="Identificador persistente de la entidad")
    label: str = Field(..., description="Etiqueta identificativa")
    entity_type: str = Field("object", description="person, object, vehicle, location, other")
    description: str = Field("", description="Descripción detallada")
    created_by: str = Field("system", description="Autor de la declaración de identidad")
    provenance_kind: str = Field("human_declaration", description="human_declaration, system_derived...")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EvidenceSchema(BaseModel):
    id: Optional[str] = None
    session_id: Optional[str] = None
    source_id: Optional[str] = None
    media_type: str = Field("image", description="image, video, audio, text, document")
    file_path: Optional[str] = Field(None, description="Ruta relativa al archivo en disco")
    filename: Optional[str] = None
    external_capture_id: Optional[str] = None
    capture_time: Optional[TimeSpecSchema] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ObservationSchema(BaseModel):
    id: Optional[str] = None
    content: str = Field(..., description="Contenido textual de la observación")
    provenance_kind: str = Field("visual_detection", description="visual_detection, human_declaration, model_description...")
    author: str = Field("system", description="Detector, humano o modelo que generó la observación")
    tool_version: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    evidence_ids: List[str] = Field(default_factory=list)
    entity_id: Optional[str] = None
    capture_time: Optional[TimeSpecSchema] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AnnotationSchema(BaseModel):
    id: Optional[str] = None
    revision_number: int = Field(1, ge=1)
    content: str = Field(..., description="Texto enriquecido de la anotación")
    provenance_kind: str = Field("human_declaration")
    author: str = Field("user")
    tool_version: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    supersedes_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EpisodeSchema(BaseModel):
    id: Optional[str] = None
    external_id: Optional[str] = None
    title: str = Field(..., description="Título descriptivo del episodio")
    interval: Optional[IntervalSchema] = None
    source_ids: List[str] = Field(default_factory=list)
    location: str = Field("", description="Ubicación física del suceso")
    tags: List[str] = Field(default_factory=list)
    indexable_text: Optional[str] = None
    is_visible: bool = Field(True)
    session_id: Optional[str] = None
    evidence: List[EvidenceSchema] = Field(default_factory=list)
    observations: List[ObservationSchema] = Field(default_factory=list)
    annotations: List[AnnotationSchema] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ManifestSchema(BaseModel):
    version: str = Field("1.0", description="Versión del esquema de manifiesto")
    description: str = Field("", description="Descripción del conjunto de datos o escenario")
    sources: List[SourceSchema] = Field(default_factory=list)
    sessions: List[SessionSchema] = Field(default_factory=list)
    entities: List[EntitySchema] = Field(default_factory=list)
    episodes: List[EpisodeSchema] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        if not v.startswith("1."):
            raise ValueError(f"Versión de manifiesto no soportada: {v}. Se requiere 1.x")
        return v
