"""Resolución de entidades y grafo lógico persistido en SQLite."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Iterable

from episodic.domain.entities import Entity, Episode, Observation
from episodic.domain.relations import Hypothesis, Relation
from episodic.domain.provenance import EvidenceReference
from episodic.domain.times import utc_now
from episodic.domain.types import (
    HypothesisState,
    LogicalRelationType,
    TransitionMode,
    ValidationState,
)
from episodic.ports.repository import EpisodicRepository


_GRAPH_NAMESPACE = uuid.UUID("993ecead-f556-4ce9-bb72-0413a751fe1d")


@dataclass(frozen=True)
class RelatedEpisode:
    episode: Episode
    relation: Relation
    observation_gap: dict[str, Any] | None = None
    depth: int = 1


@dataclass(frozen=True)
class EpisodeTraversal:
    previous: tuple[RelatedEpisode, ...]
    following: tuple[RelatedEpisode, ...]
    overlapping: tuple[RelatedEpisode, ...]


@dataclass(frozen=True)
class ConfirmedObservation:
    observation: Observation
    evidence_references: tuple[EvidenceReference, ...]
    contradictions: tuple[Relation, ...]


class LogicalGraphService:
    """Opera el grafo como relaciones canónicas, sin motor de grafos externo."""

    producer = "phase6-logical-graph"
    model_version = "phase6-rules-v1"

    def __init__(self, repository: EpisodicRepository) -> None:
        self.repository = repository

    def register_entity(self, entity: Entity) -> Entity:
        self.repository.save_entity(entity)
        return entity

    def create_relation(
        self,
        relation_type: LogicalRelationType | str,
        *,
        origin_type: str,
        origin_id: str,
        destination_type: str,
        destination_id: str,
        method: str,
        evidence_reference_ids: Iterable[str] = (),
        validation_state: ValidationState = ValidationState.PROPOSED,
        confidence: float | None = None,
        transition_mode: TransitionMode | str | None = None,
        derivation: str = "observed",
        observation_gap: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> Relation:
        resolved_type = _relation_type(relation_type)
        if resolved_type == LogicalRelationType.POSSIBLY_SAME_ENTITY_AS:
            raise ValueError(
                "POSSIBLY_SAME_ENTITY_AS es una hipótesis; use propose_possible_same_entity"
            )
        self._validate_endpoint_contract(
            resolved_type,
            origin_type,
            destination_type,
        )
        evidence_ids = tuple(dict.fromkeys(evidence_reference_ids))
        relation_metadata = dict(metadata or {})
        relation_metadata["derivation"] = derivation
        if resolved_type == LogicalRelationType.MOVED_TO:
            if transition_mode is None:
                raise ValueError("MOVED_TO requiere transition_mode")
            relation_metadata["transition_mode"] = TransitionMode(transition_mode).value
        if observation_gap is not None:
            relation_metadata["observation_gap"] = observation_gap
        relation = Relation(
            id=_stable_id(
                "relation",
                resolved_type.value,
                origin_type,
                origin_id,
                destination_type,
                destination_id,
                method,
                *evidence_ids,
                json.dumps(relation_metadata, sort_keys=True, separators=(",", ":")),
            ),
            relation_type=resolved_type.value,
            origin_type=origin_type,
            origin_id=origin_id,
            destination_type=destination_type,
            destination_id=destination_id,
            method=method,
            evidence_reference_ids=list(evidence_ids),
            validation_state=validation_state,
            producer=self.producer,
            model_version=self.model_version,
            confidence=confidence,
            created_at=created_at or utc_now(),
            metadata=relation_metadata,
        )
        self.repository.save_relation(relation)
        return relation

    def propose_possible_same_entity(
        self,
        entity_id: str,
        candidate_entity_id: str,
        *,
        method: str,
        evidence_reference_ids: Iterable[str],
        confidence: float | None = None,
        created_at: datetime | None = None,
    ) -> Hypothesis:
        if entity_id == candidate_entity_id:
            raise ValueError("Una entidad no puede ser candidata de sí misma")
        evidence_ids = tuple(dict.fromkeys(evidence_reference_ids))
        if not evidence_ids:
            raise ValueError("La hipótesis de identidad requiere evidencia")
        hypothesis = Hypothesis(
            id=_stable_id(
                "hypothesis",
                LogicalRelationType.POSSIBLY_SAME_ENTITY_AS.value,
                *sorted((entity_id, candidate_entity_id)),
                method,
                *evidence_ids,
            ),
            statement=(
                f"Las entidades {entity_id} y {candidate_entity_id} "
                "podrían representar la misma identidad persistente."
            ),
            subject_type="entity",
            subject_id=entity_id,
            object_type="entity",
            object_id=candidate_entity_id,
            evidence_reference_ids=list(evidence_ids),
            state=HypothesisState.OPEN,
            producer=self.producer,
            model_version=self.model_version,
            confidence=confidence,
            created_at=created_at or utc_now(),
            metadata={
                "relation_type": LogicalRelationType.POSSIBLY_SAME_ENTITY_AS.value,
                "method": method,
            },
        )
        self.repository.save_hypothesis(hypothesis)
        return hypothesis

    def validate_same_entity(
        self,
        entity_id: str,
        candidate_entity_id: str,
        *,
        method: str,
        evidence_reference_ids: Iterable[str],
        hypothesis_id: str | None = None,
        reviewer_entity_id: str | None = None,
        confidence: float | None = None,
        created_at: datetime | None = None,
    ) -> Relation:
        evidence_ids = tuple(dict.fromkeys(evidence_reference_ids))
        if not evidence_ids:
            raise ValueError("SAME_ENTITY_AS requiere evidencia de la decisión")
        hypothesis = self.repository.get_hypothesis(hypothesis_id) if hypothesis_id else None
        if hypothesis_id and hypothesis is None:
            raise ValueError(f"Hipótesis no encontrada: {hypothesis_id}")
        if hypothesis is not None:
            endpoints = {hypothesis.subject_id, hypothesis.object_id}
            if endpoints != {entity_id, candidate_entity_id}:
                raise ValueError("La hipótesis no corresponde a las entidades indicadas")
        relation = self.create_relation(
            LogicalRelationType.SAME_ENTITY_AS,
            origin_type="entity",
            origin_id=entity_id,
            destination_type="entity",
            destination_id=candidate_entity_id,
            method=method,
            evidence_reference_ids=evidence_ids,
            validation_state=ValidationState.VALIDATED,
            confidence=confidence,
            metadata={
                "resolved_hypothesis_id": hypothesis_id,
                "reviewer_entity_id": reviewer_entity_id,
            },
            created_at=created_at,
        )
        if hypothesis is not None:
            self.repository.save_hypothesis(
                replace(
                    hypothesis,
                    state=HypothesisState.SUPPORTED,
                    metadata={
                        **hypothesis.metadata,
                        "resolved_by_relation_id": relation.id,
                        "reviewer_entity_id": reviewer_entity_id,
                    },
                )
            )
        return relation

    def resolved_entity_ids(self, entity_id: str) -> tuple[str, ...]:
        if self.repository.get_entity(entity_id) is None:
            raise ValueError(f"Entidad no encontrada: {entity_id}")
        relations = self.repository.list_relations(
            relation_type=LogicalRelationType.SAME_ENTITY_AS.value,
            validation_state=ValidationState.VALIDATED,
        )
        adjacency: dict[str, set[str]] = {}
        for relation in relations:
            adjacency.setdefault(relation.origin_id, set()).add(relation.destination_id)
            adjacency.setdefault(relation.destination_id, set()).add(relation.origin_id)
        visited: set[str] = set()
        pending = [entity_id]
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(sorted(adjacency.get(current, set()) - visited))
        return tuple(sorted(visited))

    def observations_for_entity(
        self, entity_id: str, *, include_validated_aliases: bool = True
    ) -> tuple[Observation, ...]:
        entity_ids = (
            self.resolved_entity_ids(entity_id)
            if include_validated_aliases
            else (entity_id,)
        )
        observations: dict[str, Observation] = {}
        for resolved_id in entity_ids:
            cursor: str | None = None
            while True:
                batch, cursor = self.repository.list_observations_for_entity(
                    resolved_id, limit=100, cursor=cursor
                )
                observations.update((item.id, item) for item in batch)
                if cursor is None:
                    break
        return tuple(sorted(observations.values(), key=_observation_order))

    def relations_for_episode(self, episode_id: str) -> tuple[Relation, ...]:
        if self.repository.get_episode(episode_id) is None:
            raise ValueError(f"Episodio no encontrado: {episode_id}")
        return tuple(
            self.repository.list_relations(node_type="episode", node_id=episode_id)
        )

    def traverse_episodes(
        self, episode_id: str, *, max_depth: int | None = None
    ) -> EpisodeTraversal:
        if self.repository.get_episode(episode_id) is None:
            raise ValueError(f"Episodio no encontrado: {episode_id}")
        if max_depth is not None and max_depth < 1:
            raise ValueError("max_depth debe ser al menos 1")
        temporal = [
            relation
            for relation in self.repository.list_relations()
            if relation.validation_state != ValidationState.REJECTED
            and relation.origin_type == "episode"
            and relation.destination_type == "episode"
            and relation.relation_type
            in {
                LogicalRelationType.BEFORE.value,
                LogicalRelationType.AFTER.value,
            }
        ]
        forward: dict[str, list[tuple[str, Relation]]] = {}
        backward: dict[str, list[tuple[str, Relation]]] = {}
        for relation in temporal:
            if relation.relation_type == LogicalRelationType.BEFORE.value:
                earlier, later = relation.origin_id, relation.destination_id
            else:
                earlier, later = relation.destination_id, relation.origin_id
            forward.setdefault(earlier, []).append((later, relation))
            backward.setdefault(later, []).append((earlier, relation))

        previous = self._walk_temporal(episode_id, backward, max_depth)
        following = self._walk_temporal(episode_id, forward, max_depth)
        overlapping: list[RelatedEpisode] = []
        for relation in self.repository.list_relations(
            relation_type=LogicalRelationType.OVERLAPS.value,
            node_type="episode",
            node_id=episode_id,
        ):
            if relation.validation_state == ValidationState.REJECTED:
                continue
            other_id = (
                relation.destination_id
                if relation.origin_id == episode_id
                else relation.origin_id
            )
            episode = self.repository.get_episode(other_id)
            if episode is not None:
                overlapping.append(RelatedEpisode(episode, relation))
        return EpisodeTraversal(
            previous=tuple(sorted(_unique_related(previous), key=_related_episode_order)),
            following=tuple(sorted(_unique_related(following), key=_related_episode_order)),
            overlapping=tuple(sorted(_unique_related(overlapping), key=_related_episode_order)),
        )

    def _walk_temporal(
        self,
        start_episode_id: str,
        adjacency: dict[str, list[tuple[str, Relation]]],
        max_depth: int | None,
    ) -> list[RelatedEpisode]:
        result: list[RelatedEpisode] = []
        visited = {start_episode_id}
        pending: list[tuple[str, int]] = [(start_episode_id, 0)]
        while pending:
            current_id, depth = pending.pop(0)
            if max_depth is not None and depth >= max_depth:
                continue
            for other_id, relation in sorted(
                adjacency.get(current_id, []), key=lambda item: (item[0], item[1].id)
            ):
                if other_id in visited:
                    continue
                visited.add(other_id)
                episode = self.repository.get_episode(other_id)
                if episode is None:
                    continue
                next_depth = depth + 1
                result.append(
                    RelatedEpisode(
                        episode=episode,
                        relation=relation,
                        observation_gap=relation.metadata.get("observation_gap"),
                        depth=next_depth,
                    )
                )
                pending.append((other_id, next_depth))
        return result

    def contradictions(
        self, node_type: str | None = None, node_id: str | None = None
    ) -> tuple[Relation, ...]:
        return tuple(
            self.repository.list_relations(
                relation_type=LogicalRelationType.CONTRADICTS.value,
                node_type=node_type,
                node_id=node_id,
            )
        )

    def evidence_for_relation(
        self, relation_id: str
    ) -> tuple[EvidenceReference, ...]:
        relation = self.repository.get_relation(relation_id)
        if relation is None:
            raise ValueError(f"Relación no encontrada: {relation_id}")
        references = []
        for reference_id in relation.evidence_reference_ids:
            reference = self.repository.get_evidence_reference(reference_id)
            if reference is not None:
                references.append(reference)
        return tuple(references)

    def last_confirmed_observation(
        self, entity_id: str
    ) -> ConfirmedObservation | None:
        confirmed = [
            item
            for item in self.observations_for_entity(entity_id)
            if item.validation_state == ValidationState.VALIDATED
        ]
        if not confirmed:
            return None
        observation = max(confirmed, key=_observation_order)
        evidence = tuple(
            reference
            for reference_id in observation.evidence_reference_ids
            if (reference := self.repository.get_evidence_reference(reference_id))
            is not None
        )
        contradictions = self.contradictions("observation", observation.id)
        return ConfirmedObservation(observation, evidence, contradictions)

    @staticmethod
    def _validate_endpoint_contract(
        relation_type: LogicalRelationType,
        origin_type: str,
        destination_type: str,
    ) -> None:
        if relation_type in {
            LogicalRelationType.BEFORE,
            LogicalRelationType.AFTER,
            LogicalRelationType.OVERLAPS,
        } and (origin_type, destination_type) != ("episode", "episode"):
            raise ValueError(f"{relation_type.value} debe enlazar episodios")
        if relation_type == LogicalRelationType.SAME_ENTITY_AS and (
            origin_type,
            destination_type,
        ) != ("entity", "entity"):
            raise ValueError("SAME_ENTITY_AS debe enlazar entidades")
        if relation_type in {
            LogicalRelationType.OBSERVED_IN,
            LogicalRelationType.LOCATED_IN,
            LogicalRelationType.MOVED_TO,
        } and (origin_type, destination_type) != ("entity", "environment"):
            raise ValueError(f"{relation_type.value} requiere entity -> environment")
        if relation_type == LogicalRelationType.REVIEWED_BY and destination_type != "entity":
            raise ValueError("REVIEWED_BY requiere una entidad revisora como destino")


def _relation_type(value: LogicalRelationType | str) -> LogicalRelationType:
    if isinstance(value, LogicalRelationType):
        return value
    try:
        return LogicalRelationType(value.strip().upper())
    except ValueError as exc:
        raise ValueError(f"Tipo de relación no soportado: {value}") from exc


def _stable_id(kind: str, *parts: str) -> str:
    return str(uuid.uuid5(_GRAPH_NAMESPACE, "|".join((kind, *parts))))


def _observation_order(observation: Observation) -> tuple[datetime, str]:
    instant = (
        observation.end_time
        or observation.start_time
        or (observation.capture_time.absolute if observation.capture_time else None)
        or observation.created_at
    )
    return instant, observation.id


def _related_episode_order(item: RelatedEpisode) -> tuple[datetime, str]:
    instant = item.episode.start_time or item.episode.created_at
    return instant, item.episode.id


def _unique_related(items: Iterable[RelatedEpisode]) -> list[RelatedEpisode]:
    return list({item.episode.id: item for item in items}.values())
