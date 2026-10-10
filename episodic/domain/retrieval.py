"""Contratos de recuperacion hibrida sobre la memoria canonica.

Estos modelos no generan respuestas: conservan los objetos de dominio que
permiten verificar cada resultado y separan las senales de recuperacion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from episodic.domain.entities import Episode, Observation
from episodic.domain.provenance import EvidenceReference
from episodic.domain.relations import Relation
from episodic.domain.times import Interval, utc_now
from episodic.domain.types import ValidationState


@dataclass(frozen=True)
class HybridSearchFilters:
    """Filtros estructurados con semantica AND entre grupos."""

    environment_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    modalities: tuple[str, ...] = ()
    interval: Interval | None = None
    entity_ids: tuple[str, ...] = ()
    observation_types: tuple[str, ...] = ()
    episode_types: tuple[str, ...] = ()
    min_confidence: float | None = None
    validation_states: tuple[ValidationState, ...] = ()

    def __post_init__(self) -> None:
        if self.min_confidence is not None and not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence debe estar entre 0 y 1")

    @property
    def active_count(self) -> int:
        return sum(
            bool(value)
            for value in (
                self.environment_ids,
                self.source_ids,
                self.modalities,
                self.interval,
                self.entity_ids,
                self.observation_types,
                self.episode_types,
                self.min_confidence is not None,
                self.validation_states,
            )
        )


@dataclass(frozen=True)
class RetrievalScores:
    """Senales independientes; ``None`` significa que no se aplico ese nivel."""

    structured: float | None = None
    semantic: float | None = None
    temporal: float | None = None
    relational: float | None = None


@dataclass(frozen=True)
class RelationExpansion:
    """Relacion que incorporo contexto sin convertir similitud en un hecho."""

    relation: Relation
    evidence_references: tuple[EvidenceReference, ...] = ()

    @property
    def is_validated(self) -> bool:
        return self.relation.validation_state == ValidationState.VALIDATED


@dataclass(frozen=True)
class HybridMemoryResult:
    """Resultado rehidratado desde SQLite, no un fragmento de texto."""

    episode: Episode
    observations: tuple[Observation, ...]
    evidence_references: tuple[EvidenceReference, ...]
    relation_expansions: tuple[RelationExpansion, ...]
    scores: RetrievalScores
    match_kinds: tuple[str, ...]
    is_direct_match: bool = True
    rank: int = 0

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence_references or self.episode.evidence_ids)


@dataclass(frozen=True)
class HybridQueryRun:
    """Ejecucion determinista de los cuatro niveles de recuperacion."""

    text_query: str | None
    filters: HybridSearchFilters
    results: tuple[HybridMemoryResult, ...]
    duration_ms: float
    relation_types: tuple[str, ...] = ()
    expanded_relations: bool = False
    executed_at: datetime = field(default_factory=utc_now)

    @property
    def results_without_evidence(self) -> int:
        return sum(not result.has_evidence for result in self.results)
