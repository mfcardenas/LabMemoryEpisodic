"""Modelos Pydantic para la API HTTP del sistema episódico."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, model_validator


class SearchRequest(BaseModel):
    text_query: Optional[str] = Field(None, description="Texto de búsqueda")
    image_evidence_id: Optional[str] = Field(
        None,
        description="ID de una evidencia de imagen ya almacenada que se usa como consulta",
    )
    intent: str = Field("semantic_search", description="semantic_search, interval_search, last_observation, timeline")
    source_filter: Optional[List[str]] = Field(None, description="Filtrar por IDs de fuente")
    start_time: Optional[datetime] = Field(None, description="Inicio de intervalo ISO UTC")
    end_time: Optional[datetime] = Field(None, description="Fin de intervalo ISO UTC")
    entity_filter: Optional[str] = Field(None, description="Filtrar por ID o etiqueta de entidad")
    environment_filter: Optional[List[str]] = None
    modality_filter: Optional[List[str]] = None
    observation_type_filter: Optional[List[str]] = None
    episode_type_filter: Optional[List[str]] = None
    min_confidence: Optional[float] = Field(None, ge=0, le=1)
    validation_state_filter: Optional[List[str]] = None
    expand_relations: bool = False
    relation_type_filter: Optional[List[str]] = None
    require_unique_resolution: bool = Field(
        False,
        description="La consulta exige decidir una única correspondencia",
    )
    limit: int = Field(10, ge=1, le=100)

    @model_validator(mode="after")
    def validate_interval(self) -> "SearchRequest":
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start_time y end_time deben enviarse juntos")
        if self.start_time is None:
            return self
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("start_time y end_time deben incluir zona horaria")
        if self.start_time >= self.end_time:
            raise ValueError("start_time debe ser anterior a end_time")
        return self


class SummaryRegenerateRequest(BaseModel):
    """Pide una revisión nueva del resumen de un episodio, un día o un entorno."""

    scope: Literal["episode", "day", "environment"]
    key: str = Field(..., min_length=1, description="id del episodio, «<entorno>|AAAA-MM-DD» o id del entorno")


class PassageSearchRequest(BaseModel):
    """Búsqueda por pasajes: los momentos que responden, agrupados por episodio."""

    text_query: str = Field(..., min_length=1, description="Pregunta o términos a buscar")
    environment_filter: Optional[List[str]] = None
    source_filter: Optional[List[str]] = None
    modality_filter: Optional[List[str]] = None
    observation_type_filter: Optional[List[str]] = None
    start_time: Optional[datetime] = Field(None, description="Inicio de intervalo ISO UTC")
    end_time: Optional[datetime] = Field(None, description="Fin de intervalo ISO UTC")
    max_episodes: Optional[int] = Field(None, ge=1, le=100)
    per_episode: Optional[int] = Field(None, ge=1, le=50)
    rerank: bool = Field(True, description="Reordenar con el cross-encoder configurado")
    rerank_min_score: Optional[float] = Field(
        None,
        description="Umbral del reordenador solo para esta consulta; null explícito: sin umbral; "
        "omitido: el de EPIGRAPH_RERANK_MIN_SCORE",
    )
    agreement_rank: Optional[int] = Field(
        None,
        ge=0,
        le=1000,
        description="Se conserva bajo el umbral si ambas vías lo ponen entre sus N primeros; 0: nunca; "
        "omitido: EPIGRAPH_PASSAGE_AGREEMENT_RANK",
    )
    neighbors: Optional[int] = Field(
        None,
        ge=0,
        le=10,
        description="Eventos anterior y siguiente que acompañan a cada resultado; omitido: los que "
        "sugiera la pregunta (2 si pregunta por antes/después)",
    )
    include_summaries: bool = Field(
        False, description="Incluir los resúmenes de episodio (texto generado) como pasajes candidatos"
    )

    @model_validator(mode="after")
    def validate_interval(self) -> "PassageSearchRequest":
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start_time y end_time deben enviarse juntos")
        if self.start_time is not None and self.start_time >= self.end_time:
            raise ValueError("start_time debe ser anterior a end_time")
        return self


class AnnotationCreateRequest(BaseModel):
    content: str = Field(..., description="Contenido de la anotación")
    author: str = Field("user", description="Autor del enriquecimiento")
    provenance_kind: str = Field("human_declaration", description="human_declaration, system_derived")
    evidence_ids: List[str] = Field(default_factory=list)
    reindex: bool = Field(True, description="¿Reindexar el episodio con la nueva información?")


class QueryResultDTO(BaseModel):
    episode_id: str
    external_id: Optional[str] = None
    title: str = ""
    score: float
    rank: int
    space: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    verdict: str
    reasons: List[str] = Field(default_factory=list)
    timeline_start: Optional[datetime] = None
    timeline_end: Optional[datetime] = None
    timeline_kind: str = "unknown"
    timeline_clock_source: Optional[str] = None


class QueryRunResponse(BaseModel):
    id: str
    intent: str
    text_query: Optional[str] = None
    overall_verdict: str
    reasons: List[str] = Field(default_factory=list)
    results: List[QueryResultDTO] = Field(default_factory=list)
    duration_ms: float = 0.0


class RetrievalScoresDTO(BaseModel):
    structured: Optional[float] = None
    semantic: Optional[float] = None
    temporal: Optional[float] = None
    relational: Optional[float] = None


class RelationExpansionDTO(BaseModel):
    relation: Dict[str, Any]
    evidence_references: List[Dict[str, Any]] = Field(default_factory=list)
    is_validated: bool


class HybridMemoryResultDTO(BaseModel):
    rank: int
    episode: Dict[str, Any]
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_references: List[Dict[str, Any]] = Field(default_factory=list)
    relation_expansions: List[RelationExpansionDTO] = Field(default_factory=list)
    scores: RetrievalScoresDTO
    match_kinds: List[str] = Field(default_factory=list)
    is_direct_match: bool
    has_evidence: bool


class HybridQueryRunResponse(BaseModel):
    text_query: Optional[str] = None
    results: List[HybridMemoryResultDTO] = Field(default_factory=list)
    duration_ms: float
    results_without_evidence: int
    expanded_relations: bool
    relation_types: List[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    episodes_count: int
    sources_count: int
    entities_count: int
    qdrant_points_count: int
    vector_backend: str
    collection_name: str
    active_generation: Optional[str] = None
    text_model: str
    image_model: str


class IndexingSummaryResponse(BaseModel):
    pending: int = 0
    indexed: int = 0
    failed: int = 0
    stale: int = 0
    total: int = 0


class ProjectComponentStatus(BaseModel):
    id: str
    label: str
    state: str
    present: bool
    integrated: bool
    detail: str


class ProjectStatusResponse(BaseModel):
    checkpoint: str
    checkpoint_label: str
    runtime: HealthResponse
    indexing: IndexingSummaryResponse
    components: List[ProjectComponentStatus] = Field(default_factory=list)


class RebuildIndexResponse(BaseModel):
    status: str = "success"
    vector_backend: str
    previous_generation: Optional[str] = None
    active_generation: Optional[str] = None
    previous_points: int
    active_points: int
    rebuilt_points: int
    verified: bool
