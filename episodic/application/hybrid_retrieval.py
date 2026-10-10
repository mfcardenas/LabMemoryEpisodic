"""Orquestacion determinista de recuperacion estructurada, semantica y relacional."""
from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime
from typing import Iterable

from episodic.application.logical_graph import LogicalGraphService
from episodic.application.retrieval import RetrievalService
from episodic.domain.entities import Episode, Observation
from episodic.domain.retrieval import (
    HybridMemoryResult,
    HybridQueryRun,
    HybridSearchFilters,
    RelationExpansion,
    RetrievalScores,
)
from episodic.domain.times import Interval
from episodic.domain.types import SearchIntent, ValidationState
from episodic.ports.repository import EpisodicRepository


class HybridRetrievalService:
    """Compone los servicios existentes sin usar LLM ni agentes."""

    def __init__(
        self,
        repository: EpisodicRepository,
        semantic_retrieval: RetrievalService,
        logical_graph: LogicalGraphService,
    ) -> None:
        self.repository = repository
        self.semantic_retrieval = semantic_retrieval
        self.logical_graph = logical_graph

    def search(
        self,
        *,
        text_query: str | None = None,
        filters: HybridSearchFilters | None = None,
        expand_relations: bool = False,
        relation_types: Iterable[str] = (),
        limit: int = 10,
    ) -> HybridQueryRun:
        """Ejecuta los cuatro niveles y devuelve registros canonicos completos."""
        if limit < 1:
            raise ValueError("limit debe ser mayor que cero")
        started = time.perf_counter()
        active_filters = filters or HybridSearchFilters()
        normalized_relation_types = tuple(
            dict.fromkeys(value.strip().upper() for value in relation_types if value.strip())
        )

        semantic_scores: dict[str, float] = {}
        ordered_ids: list[str] = []
        if text_query and text_query.strip():
            legacy_run = self.semantic_retrieval.search(
                text_query=text_query.strip(),
                intent=SearchIntent.SEMANTIC_SEARCH,
                source_filter=list(active_filters.source_ids) or None,
                interval_filter=active_filters.interval,
                structured_vector_filters=self._vector_filters(active_filters),
                limit=max(100, limit * 10),
            )
            for item in legacy_run.results:
                if item.episode_id in semantic_scores:
                    continue
                semantic_scores[item.episode_id] = item.original_score or item.score
                ordered_ids.append(item.episode_id)
        else:
            ordered_ids = [episode.id for episode in self._all_visible_episodes()]

        direct: list[HybridMemoryResult] = []
        for episode_id in ordered_ids:
            episode = self.repository.get_episode(episode_id)
            if episode is None or not episode.is_visible:
                continue
            observations = tuple(self.repository.list_observations_for_episode(episode.id))
            if not self._matches(episode, observations, active_filters):
                continue
            direct.append(
                self._hydrate_result(
                    episode,
                    observations,
                    scores=RetrievalScores(
                        structured=(1.0 if active_filters.active_count else None),
                        semantic=semantic_scores.get(episode.id),
                        temporal=self._temporal_score(episode, observations, active_filters.interval),
                    ),
                    match_kinds=tuple(
                        kind
                        for kind, present in (
                            ("structured_filter", bool(active_filters.active_count)),
                            ("semantic_similarity", episode.id in semantic_scores),
                            ("temporal_overlap", active_filters.interval is not None),
                        )
                        if present
                    ),
                )
            )

        direct = direct[:limit]
        combined = list(direct)
        if expand_relations and direct:
            combined = self._expand(direct, normalized_relation_types, active_filters, limit)

        ranked = tuple(replace(item, rank=index) for index, item in enumerate(combined, 1))
        return HybridQueryRun(
            text_query=text_query,
            filters=active_filters,
            results=ranked,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            relation_types=normalized_relation_types,
            expanded_relations=expand_relations,
        )

    def last_confirmed_observation(self, entity_id_or_label: str) -> HybridQueryRun:
        """Recuperacion temporal de la ultima observacion validada."""
        started = time.perf_counter()
        entity_id = self._resolve_entity_ids((entity_id_or_label,))[0]
        confirmed = self.logical_graph.last_confirmed_observation(entity_id)
        filters = HybridSearchFilters(
            entity_ids=(entity_id,), validation_states=(ValidationState.VALIDATED,)
        )
        results: tuple[HybridMemoryResult, ...] = ()
        if confirmed is not None and confirmed.observation.episode_id:
            episode = self.repository.get_episode(confirmed.observation.episode_id)
            if episode is not None:
                expansions = tuple(
                    RelationExpansion(
                        relation=relation,
                        evidence_references=self.logical_graph.evidence_for_relation(relation.id),
                    )
                    for relation in confirmed.contradictions
                )
                results = (
                    HybridMemoryResult(
                        episode=episode,
                        observations=(confirmed.observation,),
                        evidence_references=confirmed.evidence_references,
                        relation_expansions=expansions,
                        scores=RetrievalScores(structured=1.0, temporal=1.0),
                        match_kinds=("structured_filter", "latest_confirmed"),
                        rank=1,
                    ),
                )
        return HybridQueryRun(
            text_query=None,
            filters=filters,
            results=results,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    def episode_context(
        self,
        episode_id: str,
        *,
        relation_types: Iterable[str] = ("BEFORE", "AFTER", "OVERLAPS"),
        limit: int = 10,
    ) -> HybridQueryRun:
        """Recupera lo anterior/posterior mediante relaciones explicitas."""
        started = time.perf_counter()
        episode = self.repository.get_episode(episode_id)
        if episode is None:
            raise ValueError(f"Episodio no encontrado: {episode_id}")
        direct = self._hydrate_result(
            episode,
            tuple(self.repository.list_observations_for_episode(episode.id)),
            scores=RetrievalScores(structured=1.0),
            match_kinds=("episode_id",),
        )
        normalized = tuple(value.strip().upper() for value in relation_types)
        results = self._expand([direct], normalized, HybridSearchFilters(), limit)
        ranked = tuple(replace(item, rank=index) for index, item in enumerate(results, 1))
        return HybridQueryRun(
            text_query=None,
            filters=HybridSearchFilters(),
            results=ranked,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            relation_types=normalized,
            expanded_relations=True,
        )

    def _all_visible_episodes(self) -> list[Episode]:
        episodes: list[Episode] = []
        cursor: str | None = None
        while True:
            page, cursor = self.repository.list_episodes(limit=100, cursor=cursor)
            episodes.extend(page)
            if cursor is None:
                return episodes

    def _matches(
        self,
        episode: Episode,
        observations: tuple[Observation, ...],
        filters: HybridSearchFilters,
    ) -> bool:
        entity_ids = set(self._resolve_entity_ids(filters.entity_ids))
        if filters.environment_ids and not (
            set(filters.environment_ids) & (
                set(episode.environment_ids)
                | {item.environment_id for item in observations if item.environment_id}
            )
        ):
            return False
        if filters.source_ids and not (
            set(filters.source_ids) & (
                set(episode.source_ids)
                | {item.source_id for item in observations if item.source_id}
            )
        ):
            return False
        if filters.modalities and not set(filters.modalities) & {
            item.modality for item in observations
        }:
            return False
        if filters.interval is not None and self._temporal_score(
            episode, observations, filters.interval
        ) is None:
            return False
        if entity_ids and not entity_ids & {
            item.entity_id for item in observations if item.entity_id
        }:
            return False
        if filters.observation_types and not set(filters.observation_types) & {
            item.observation_type for item in observations
        }:
            return False
        episode_type = str(episode.metadata.get("episode_type", "generic"))
        if filters.episode_types and episode_type not in filters.episode_types:
            return False
        if filters.min_confidence is not None:
            confidences = [
                value
                for value in [episode.confidence, *(item.confidence for item in observations)]
                if value is not None
            ]
            if not confidences or max(confidences) < filters.min_confidence:
                return False
        if filters.validation_states and not set(filters.validation_states) & {
            item.validation_state for item in observations
        }:
            return False
        return True

    def _resolve_entity_ids(self, values: Iterable[str]) -> tuple[str, ...]:
        entities = self.repository.list_entities()
        by_label = {entity.label.casefold(): entity.id for entity in entities}
        resolved: set[str] = set()
        known_ids = {entity.id for entity in entities}
        for value in values:
            entity_id = value if value in known_ids else by_label.get(value.casefold(), value)
            if entity_id in known_ids:
                resolved.update(self.logical_graph.resolved_entity_ids(entity_id))
            else:
                resolved.add(entity_id)
        return tuple(sorted(resolved))

    def _vector_filters(self, filters: HybridSearchFilters) -> dict[str, object]:
        """Traduce filtros al payload Qdrant; SQLite los revalida despues."""
        payload: dict[str, object] = {"target_type": "episode"}
        mappings = (
            ("environment_ids", filters.environment_ids),
            ("source_ids", filters.source_ids),
            ("modalities", filters.modalities),
            ("entity_ids", self._resolve_entity_ids(filters.entity_ids)),
            ("observation_types", filters.observation_types),
            (
                "validation_states",
                tuple(item.value for item in filters.validation_states),
            ),
        )
        payload.update({key: list(values) for key, values in mappings if values})
        if len(filters.episode_types) == 1:
            payload["episode_type"] = filters.episode_types[0]
        if filters.min_confidence is not None:
            payload["min_confidence"] = filters.min_confidence
        return payload

    @staticmethod
    def _temporal_score(
        episode: Episode,
        observations: tuple[Observation, ...],
        query: Interval | None,
    ) -> float | None:
        if query is None:
            return None
        query_start = query.start.absolute if query.start else None
        query_end = query.end.absolute if query.end else None
        if query_start is None or query_end is None:
            return None
        candidates = [(episode.start_time, episode.end_time)] + [
            (item.start_time, item.end_time) for item in observations
        ]
        best: float | None = None
        query_seconds = max((query_end - query_start).total_seconds(), 1e-9)
        for start, end in candidates:
            if start is None or end is None:
                continue
            overlap = (min(end, query_end) - max(start, query_start)).total_seconds()
            if overlap < 0 or (overlap == 0 and start != end):
                continue
            score = 1.0 if start == end and query_start <= start <= query_end else min(
                1.0, max(overlap, 0.0) / query_seconds
            )
            best = max(best or 0.0, score)
        return best

    def _hydrate_result(
        self,
        episode: Episode,
        observations: tuple[Observation, ...],
        *,
        scores: RetrievalScores,
        match_kinds: tuple[str, ...],
        relation_expansions: tuple[RelationExpansion, ...] = (),
        is_direct_match: bool = True,
    ) -> HybridMemoryResult:
        reference_ids = list(episode.metadata.get("evidence_reference_ids", []))
        reference_ids.extend(
            reference_id
            for observation in observations
            for reference_id in observation.evidence_reference_ids
        )
        reference_ids.extend(
            reference.id
            for expansion in relation_expansions
            for reference in expansion.evidence_references
        )
        references = tuple(
            reference
            for reference_id in dict.fromkeys(reference_ids)
            if (reference := self.repository.get_evidence_reference(reference_id)) is not None
        )
        return HybridMemoryResult(
            episode=episode,
            observations=observations,
            evidence_references=references,
            relation_expansions=relation_expansions,
            scores=scores,
            match_kinds=match_kinds,
            is_direct_match=is_direct_match,
        )

    def _expand(
        self,
        direct: list[HybridMemoryResult],
        relation_types: tuple[str, ...],
        filters: HybridSearchFilters,
        limit: int,
    ) -> list[HybridMemoryResult]:
        results = list(direct)
        by_episode = {item.episode.id: index for index, item in enumerate(results)}
        accepted_states = set(filters.validation_states)
        for seed in direct:
            nodes = [("episode", seed.episode.id)]
            nodes.extend(("observation", item.id) for item in seed.observations)
            nodes.extend(
                ("entity", item.entity_id)
                for item in seed.observations
                if item.entity_id
            )
            relations = {
                relation.id: relation
                for node_type, node_id in nodes
                for relation in self.repository.list_relations(
                    node_type=node_type, node_id=node_id
                )
                if (not relation_types or relation.relation_type in relation_types)
                and (not accepted_states or relation.validation_state in accepted_states)
                and relation.validation_state != ValidationState.REJECTED
            }
            for relation in relations.values():
                expansion = RelationExpansion(
                    relation=relation,
                    evidence_references=self.logical_graph.evidence_for_relation(relation.id),
                )
                related_episode_ids = self._related_episode_ids(relation, seed.episode.id)
                if not related_episode_ids:
                    index = by_episode[seed.episode.id]
                    current = results[index]
                    if relation.id not in {
                        item.relation.id for item in current.relation_expansions
                    }:
                        results[index] = self._hydrate_result(
                            current.episode,
                            current.observations,
                            scores=replace(
                                current.scores,
                                relational=max(current.scores.relational or 0.0, 1.0),
                            ),
                            match_kinds=tuple(dict.fromkeys((*current.match_kinds, "validated_relation" if expansion.is_validated else "proposed_relation"))),
                            relation_expansions=(*current.relation_expansions, expansion),
                            is_direct_match=current.is_direct_match,
                        )
                    continue
                for episode_id in related_episode_ids:
                    episode = self.repository.get_episode(episode_id)
                    if episode is None or not episode.is_visible:
                        continue
                    observations = tuple(self.repository.list_observations_for_episode(episode.id))
                    if episode.id in by_episode:
                        index = by_episode[episode.id]
                        current = results[index]
                        results[index] = self._hydrate_result(
                            current.episode,
                            current.observations,
                            scores=replace(
                                current.scores,
                                relational=max(current.scores.relational or 0.0, 1.0),
                            ),
                            match_kinds=tuple(dict.fromkeys((*current.match_kinds, "validated_relation" if expansion.is_validated else "proposed_relation"))),
                            relation_expansions=(*current.relation_expansions, expansion),
                            is_direct_match=current.is_direct_match,
                        )
                    elif len(results) < limit:
                        by_episode[episode.id] = len(results)
                        results.append(
                            self._hydrate_result(
                                episode,
                                observations,
                                scores=RetrievalScores(relational=1.0),
                                match_kinds=("validated_relation" if expansion.is_validated else "proposed_relation",),
                                relation_expansions=(expansion,),
                                is_direct_match=False,
                            )
                        )
        return results[:limit]

    def _related_episode_ids(self, relation, seed_episode_id: str) -> tuple[str, ...]:
        result: set[str] = set()
        for node_type, node_id in (
            (relation.origin_type, relation.origin_id),
            (relation.destination_type, relation.destination_id),
        ):
            if node_type == "episode" and node_id != seed_episode_id:
                result.add(node_id)
            elif node_type == "observation":
                observation = self.repository.get_observation(node_id)
                if observation and observation.episode_id != seed_episode_id:
                    if observation.episode_id:
                        result.add(observation.episode_id)
        return tuple(sorted(result))
