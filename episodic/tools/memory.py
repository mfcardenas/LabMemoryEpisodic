"""Once herramientas deterministas y de solo lectura para consultar EpiGraph."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Type

from pydantic import ValidationError

from episodic.application.hybrid_retrieval import HybridRetrievalService
from episodic.application.logical_graph import LogicalGraphService
from episodic.domain.entities import Entity, Episode, Observation
from episodic.domain.provenance import EvidenceReference
from episodic.domain.relations import Hypothesis, Relation
from episodic.domain.retrieval import HybridSearchFilters, RetrievalScores
from episodic.domain.times import Interval, TimeSpec
from episodic.domain.types import (
    EvidenceMediaType,
    EvidenceReferenceKind,
    LogicalRelationType,
    ValidationState,
)
from episodic.ports.repository import EpisodicRepository
from episodic.tools.agent_tools import AgentMemoryTools
from episodic.tools.contracts import (
    BrowseMemoryInput,
    FindEpisodesInput,
    GetDocumentPassagesInput,
    GetEpisodeSummaryInput,
    GetEventInput,
    GetEventNeighborsInput,
    SearchMomentsInput,
    CompareEntitiesInput,
    DocumentToolRecord,
    EntityComparisonRecord,
    EntityToolRecord,
    EnvironmentTopologyRecord,
    EvidenceToolRecord,
    ExpandRelationsInput,
    FindContradictionsInput,
    GetEntityTimelineInput,
    GetEnvironmentTopologyInput,
    GetEpisodeInput,
    GetLastConfirmedObservationInput,
    GetSupportingEvidenceInput,
    HypothesisToolRecord,
    MemoryToolOutput,
    ObservationToolRecord,
    RelationToolRecord,
    SearchDocumentsInput,
    SearchEpisodesInput,
    SearchObservationsInput,
    ToolDescriptor,
    ToolInput,
    TopologySourceRecord,
    EpisodeToolRecord,
)


@dataclass(frozen=True)
class _ToolDefinition:
    input_model: Type[ToolInput]
    handler: Callable[[Any], MemoryToolOutput]
    description: str


class MemoryToolbox:
    """Registro ejecutable sin LLM; ninguna operacion escribe en la memoria."""

    def __init__(
        self,
        repository: EpisodicRepository,
        hybrid_retrieval: HybridRetrievalService,
        logical_graph: LogicalGraphService,
        passage_search=None,
        timezone=None,
    ) -> None:
        self.repository = repository
        self.hybrid_retrieval = hybrid_retrieval
        self.logical_graph = logical_graph
        self.agent_tools = AgentMemoryTools(repository, passage_search, timezone)
        self._definitions: dict[str, _ToolDefinition] = {
            # -- Para agentes (Fase A1): compactas, paginadas, sin barridos --
            "search_moments": _ToolDefinition(
                SearchMomentsInput, self.agent_tools.search_moments,
                "Busca los momentos (eventos del vídeo, habla, fragmentos de documento) que responden a una "
                "pregunta; entiende el tiempo y la intención, devuelve posición en el vídeo y citas.",
            ),
            "find_episodes": _ToolDefinition(
                FindEpisodesInput, self.agent_tools.find_episodes,
                "Episodios en los que aparece algo, con su mejor momento cada uno, el ambiente y la hora; "
                "cubre muchos episodios a la vez (cada episodio llega al reordenador) para recorrerlos después.",
            ),
            "get_event": _ToolDefinition(
                GetEventInput, self.agent_tools.get_event,
                "Devuelve un evento completo: descripción, acciones, actores, objetos, texto visible y habla.",
            ),
            "get_event_neighbors": _ToolDefinition(
                GetEventNeighborsInput, self.agent_tools.get_event_neighbors,
                "Devuelve los eventos anteriores y posteriores a uno dado, en orden cronológico.",
            ),
            "get_episode_summary": _ToolDefinition(
                GetEpisodeSummaryInput, self.agent_tools.get_episode_summary,
                "Resumen generado de un episodio (título, resumen, narrativa con citas, momentos clave).",
            ),
            "browse_memory": _ToolDefinition(
                BrowseMemoryInput, self.agent_tools.browse_memory,
                "Navega la memoria de arriba abajo: ambientes → días → episodios → eventos, con resúmenes.",
            ),
            "get_document_passages": _ToolDefinition(
                GetDocumentPassagesInput, self.agent_tools.get_document_passages,
                "Fragmentos de los documentos adjuntos a un episodio, opcionalmente ordenados por una consulta.",
            ),
            "search_observations": _ToolDefinition(
                SearchObservationsInput, self.search_observations,
                "Busca observaciones canonicas con filtros y relevancia semantica.",
            ),
            "search_episodes": _ToolDefinition(
                SearchEpisodesInput, self.search_episodes,
                "Busca episodios y rehidrata observaciones y evidencias.",
            ),
            "get_episode": _ToolDefinition(
                GetEpisodeInput, self.get_episode,
                "Obtiene un episodio completo por identificador.",
            ),
            "get_entity_timeline": _ToolDefinition(
                GetEntityTimelineInput, self.get_entity_timeline,
                "Obtiene la linea temporal canonica de una entidad resuelta.",
            ),
            "get_last_confirmed_observation": _ToolDefinition(
                GetLastConfirmedObservationInput, self.get_last_confirmed_observation,
                "Obtiene la ultima observacion validada de una entidad.",
            ),
            "expand_relations": _ToolDefinition(
                ExpandRelationsInput, self.expand_relations,
                "Expande relaciones logicas con profundidad acotada.",
            ),
            "get_supporting_evidence": _ToolDefinition(
                GetSupportingEvidenceInput, self.get_supporting_evidence,
                "Resuelve evidencias que respaldan un registro.",
            ),
            "find_contradictions": _ToolDefinition(
                FindContradictionsInput, self.find_contradictions,
                "Recupera contradicciones preservadas y sus evidencias.",
            ),
            "compare_entities": _ToolDefinition(
                CompareEntitiesInput, self.compare_entities,
                "Compara dos entidades sin decidir identidades nuevas.",
            ),
            "search_documents": _ToolDefinition(
                SearchDocumentsInput, self.search_documents,
                "Busca observaciones documentales conservando pagina o fragmento.",
            ),
            "get_environment_topology": _ToolDefinition(
                GetEnvironmentTopologyInput, self.get_environment_topology,
                "Obtiene ambientes, fuentes, entidades y vinculos espaciales.",
            ),
        }

    def describe(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(
                name=name,
                description=definition.description,
                input_schema=definition.input_model.model_json_schema(),
                output_schema=MemoryToolOutput.model_json_schema(),
            )
            for name, definition in self._definitions.items()
        ]

    def execute(self, name: str, payload: dict[str, Any]) -> MemoryToolOutput:
        definition = self._definitions.get(name)
        if definition is None:
            raise KeyError(f"Herramienta no encontrada: {name}")
        typed_input = definition.input_model.model_validate(payload)
        return definition.handler(typed_input)

    def search_observations(
        self, request: SearchObservationsInput
    ) -> MemoryToolOutput:
        filters = self._hybrid_filters(request)
        semantic_by_episode: dict[str, float | None] = {}
        if request.query:
            run = self.hybrid_retrieval.search(
                text_query=request.query,
                filters=filters,
                limit=max(request.limit * 5, 50),
            )
            semantic_by_episode = {
                result.episode.id: result.scores.semantic for result in run.results
            }

        entity_ids = self._resolved_entity_ids(request.entity_id)
        ranked: list[tuple[float, datetime, Observation]] = []
        for observation in self.repository.list_observations():
            if not self._observation_matches(observation, request, entity_ids):
                continue
            semantic = semantic_by_episode.get(observation.episode_id or "")
            lexical = self._lexical_score(request.query, self._observation_text(observation))
            if request.query and semantic is None and lexical <= 0:
                continue
            score = semantic if semantic is not None else lexical
            ranked.append((score or 0.0, self._observation_time(observation), observation))
        ranked.sort(key=lambda item: (item[0], item[1], item[2].id), reverse=True)
        selected = ranked[: request.limit]
        observations = [
            self._observation_record(item, semantic_by_episode.get(item.episode_id or ""))
            for _, _, item in selected
        ]
        episodes = self._episode_records_for_observations(item for _, _, item in selected)
        evidence = self._evidence_for_observations(item for _, _, item in selected)
        return self._output(
            "search_observations",
            result_ids=[item.id for item in observations],
            observations=observations,
            episodes=episodes,
            evidence=evidence,
            metadata={"query": request.query, "read_only": True},
        )

    def search_episodes(self, request: SearchEpisodesInput) -> MemoryToolOutput:
        run = self.hybrid_retrieval.search(
            text_query=request.query,
            filters=self._hybrid_filters(request, request.episode_types),
            expand_relations=request.expand_relations,
            relation_types=request.relation_types,
            limit=request.limit,
        )
        observations = self._unique_observation_records(
            self._observation_record(observation, result.scores.semantic)
            for result in run.results
            for observation in result.observations
        )
        relations = self._unique_relation_records(
            self._relation_record(expansion.relation)
            for result in run.results
            for expansion in result.relation_expansions
        )
        evidence = self._unique_evidence_records(
            self._evidence_record(reference)
            for result in run.results
            for reference in result.evidence_references
        )
        episodes = [
            self._episode_record(
                result.episode,
                [item.id for item in result.evidence_references],
                result.scores,
            )
            for result in run.results
        ]
        hypotheses = self._hypotheses_for_nodes(
            [("episode", item.id) for item in episodes]
            + [("observation", item.id) for item in observations]
        )
        evidence = self._merge_evidence(
            evidence, self._evidence_for_hypotheses(hypotheses)
        )
        return self._output(
            "search_episodes",
            result_ids=[item.id for item in episodes],
            observations=observations,
            episodes=episodes,
            relations=relations,
            hypotheses=hypotheses,
            evidence=evidence,
            metadata={
                "query": request.query,
                "expanded_relations": request.expand_relations,
                "read_only": True,
            },
        )

    def get_episode(self, request: GetEpisodeInput) -> MemoryToolOutput:
        episode = self.repository.get_episode(request.episode_id)
        if episode is None:
            raise ValueError(f"Episodio no encontrado: {request.episode_id}")
        observations_raw = self.repository.list_observations_for_episode(episode.id)
        observations = [self._observation_record(item) for item in observations_raw]
        evidence = self._evidence_for_observations(observations_raw)
        relations_raw = (
            self.logical_graph.relations_for_episode(episode.id)
            if request.include_relations else ()
        )
        relations = [self._relation_record(item) for item in relations_raw]
        evidence = self._merge_evidence(
            evidence, self._evidence_for_relations(relations_raw)
        )
        nodes = [("episode", episode.id)] + [
            ("observation", item.id) for item in observations_raw
        ]
        hypotheses = self._hypotheses_for_nodes(nodes) if request.include_hypotheses else []
        evidence = self._merge_evidence(
            evidence, self._evidence_for_hypotheses(hypotheses)
        )
        return self._output(
            "get_episode",
            result_ids=[episode.id],
            episodes=[self._episode_record(episode, [item.id for item in evidence])],
            observations=observations,
            relations=relations,
            hypotheses=hypotheses,
            evidence=evidence,
            metadata={"read_only": True},
        )

    def get_entity_timeline(
        self, request: GetEntityTimelineInput
    ) -> MemoryToolOutput:
        entity = self._entity(request.entity_id)
        resolved = set(self.logical_graph.resolved_entity_ids(entity.id))
        observations_raw = [
            item
            for entity_id in resolved
            for item in self.logical_graph.observations_for_entity(
                entity_id, include_validated_aliases=False
            )
        ]
        observations_raw = list({item.id: item for item in observations_raw}.values())
        observations_raw = [
            item for item in observations_raw
            if self._timeline_matches(item, request)
            and (
                not request.validation_states
                or item.validation_state in request.validation_states
            )
        ]
        observations_raw.sort(key=self._observation_time)
        observations_raw = observations_raw[: request.limit]
        nodes = [("entity", item) for item in resolved] + [
            ("observation", item.id) for item in observations_raw
        ]
        relations_raw = self._relations_for_nodes(nodes)
        hypotheses = self._hypotheses_for_nodes(nodes) if request.include_hypotheses else []
        evidence = self._merge_evidence(
            self._evidence_for_observations(observations_raw),
            self._evidence_for_relations(relations_raw),
            self._evidence_for_hypotheses(hypotheses),
        )
        entities = [
            self._entity_record(item)
            for entity_id in sorted(resolved)
            if (item := self.repository.get_entity(entity_id)) is not None
        ]
        return self._output(
            "get_entity_timeline",
            result_ids=[item.id for item in observations_raw],
            observations=[self._observation_record(item) for item in observations_raw],
            episodes=self._episode_records_for_observations(observations_raw),
            entities=entities,
            relations=[self._relation_record(item) for item in relations_raw],
            hypotheses=hypotheses,
            evidence=evidence,
            metadata={"resolved_entity_ids": sorted(resolved), "read_only": True},
        )

    def get_last_confirmed_observation(
        self, request: GetLastConfirmedObservationInput
    ) -> MemoryToolOutput:
        entity = self._entity(request.entity_id)
        run = self.hybrid_retrieval.last_confirmed_observation(entity.id)
        if not run.results:
            return self._output(
                "get_last_confirmed_observation",
                metadata={"entity_id": entity.id, "read_only": True},
            )
        result = run.results[0]
        relations_raw = [item.relation for item in result.relation_expansions]
        observations = [self._observation_record(item) for item in result.observations]
        return self._output(
            "get_last_confirmed_observation",
            result_ids=[item.id for item in observations],
            observations=observations,
            episodes=[
                self._episode_record(
                    result.episode,
                    [item.id for item in result.evidence_references],
                    result.scores,
                )
            ],
            entities=[self._entity_record(entity)],
            relations=[self._relation_record(item) for item in relations_raw],
            evidence=[self._evidence_record(item) for item in result.evidence_references],
            metadata={"entity_id": entity.id, "read_only": True},
        )

    def expand_relations(self, request: ExpandRelationsInput) -> MemoryToolOutput:
        if not self._node_exists(request.node_type, request.node_id):
            raise ValueError(
                f"Nodo no encontrado: {request.node_type}/{request.node_id}"
            )
        accepted_types = {item.upper() for item in request.relation_types}
        accepted_states = set(request.validation_states)
        visited = {(request.node_type, request.node_id)}
        frontier = {(request.node_type, request.node_id)}
        relations: dict[str, Relation] = {}
        for _ in range(request.max_depth):
            next_frontier: set[tuple[str, str]] = set()
            for node_type, node_id in sorted(frontier):
                for relation in self.repository.list_relations(
                    node_type=node_type, node_id=node_id
                ):
                    if accepted_types and relation.relation_type not in accepted_types:
                        continue
                    if accepted_states and relation.validation_state not in accepted_states:
                        continue
                    if relation.validation_state == ValidationState.REJECTED:
                        continue
                    relations[relation.id] = relation
                    for endpoint in (
                        (relation.origin_type, relation.origin_id),
                        (relation.destination_type, relation.destination_id),
                    ):
                        if endpoint not in visited:
                            visited.add(endpoint)
                            next_frontier.add(endpoint)
            frontier = next_frontier
            if not frontier:
                break
        observations_raw, episodes_raw, entities_raw = self._records_for_nodes(visited)
        hypotheses = self._hypotheses_for_nodes(visited) if request.include_hypotheses else []
        evidence = self._merge_evidence(
            self._evidence_for_relations(relations.values()),
            self._evidence_for_observations(observations_raw),
            self._evidence_for_hypotheses(hypotheses),
        )
        return self._output(
            "expand_relations",
            result_ids=list(relations),
            observations=[self._observation_record(item) for item in observations_raw],
            episodes=[self._episode_record(item) for item in episodes_raw],
            entities=[self._entity_record(item) for item in entities_raw],
            relations=[self._relation_record(item) for item in relations.values()],
            hypotheses=hypotheses,
            evidence=evidence,
            metadata={
                "root": {"type": request.node_type, "id": request.node_id},
                "max_depth": request.max_depth,
                "read_only": True,
            },
        )

    def get_supporting_evidence(
        self, request: GetSupportingEvidenceInput
    ) -> MemoryToolOutput:
        observations_raw: list[Observation] = []
        episodes_raw: list[Episode] = []
        entities_raw: list[Entity] = []
        relations_raw: list[Relation] = []
        hypotheses_raw: list[Hypothesis] = []
        references: list[EvidenceReference] = []
        target_type = request.target_type.lower()
        if target_type == "observation":
            item = self.repository.get_observation(request.target_id)
            if item is None:
                raise ValueError(f"Observacion no encontrada: {request.target_id}")
            observations_raw.append(item)
            references.extend(self._raw_evidence_for_observations([item]))
        elif target_type == "episode":
            item = self.repository.get_episode(request.target_id)
            if item is None:
                raise ValueError(f"Episodio no encontrado: {request.target_id}")
            episodes_raw.append(item)
            episode_observations = self.repository.list_observations_for_episode(item.id)
            observations_raw.extend(episode_observations)
            references.extend(self._raw_evidence_for_observations(episode_observations))
        elif target_type == "relation":
            item = self.repository.get_relation(request.target_id)
            if item is None:
                raise ValueError(f"Relacion no encontrada: {request.target_id}")
            relations_raw.append(item)
            references.extend(self.logical_graph.evidence_for_relation(item.id))
        elif target_type == "hypothesis":
            item = self.repository.get_hypothesis(request.target_id)
            if item is None:
                raise ValueError(f"Hipotesis no encontrada: {request.target_id}")
            hypotheses_raw.append(item)
            references.extend(self._raw_evidence(item.evidence_reference_ids))
        elif target_type == "entity":
            item = self._entity(request.target_id)
            entities_raw.append(item)
            entity_observations = list(self.logical_graph.observations_for_entity(item.id))
            observations_raw.extend(entity_observations)
            relations_raw.extend(
                self.repository.list_relations(node_type="entity", node_id=item.id)
            )
            hypotheses_raw.extend(
                self.repository.list_hypotheses("entity", item.id)
            )
            references.extend(self._raw_evidence_for_observations(entity_observations))
            references.extend(self._raw_evidence_for_relations(relations_raw))
            references.extend(self._raw_evidence_for_hypotheses(hypotheses_raw))
        elif target_type == "asset":
            if self.repository.get_asset(request.target_id) is None:
                raise ValueError(f"Activo no encontrado: {request.target_id}")
            references.extend(
                self.repository.list_evidence_references_for_asset(request.target_id)
            )
        elif target_type == "evidence_reference":
            item = self.repository.get_evidence_reference(request.target_id)
            if item is None:
                raise ValueError(f"Evidencia no encontrada: {request.target_id}")
            references.append(item)
        else:
            raise ValueError(f"target_type no soportado: {request.target_type}")
        evidence = self._unique_evidence_records(
            self._evidence_record(item) for item in references
        )
        return self._output(
            "get_supporting_evidence",
            result_ids=[item.id for item in evidence],
            observations=[self._observation_record(item) for item in observations_raw],
            episodes=[self._episode_record(item) for item in episodes_raw],
            entities=[self._entity_record(item) for item in entities_raw],
            relations=[self._relation_record(item) for item in relations_raw],
            hypotheses=[self._hypothesis_record(item) for item in hypotheses_raw],
            evidence=evidence,
            metadata={
                "target_type": request.target_type,
                "target_id": request.target_id,
                "read_only": True,
            },
        )

    def find_contradictions(
        self, request: FindContradictionsInput
    ) -> MemoryToolOutput:
        if request.entity_id:
            entity = self._entity(request.entity_id)
            observations_scope = self.logical_graph.observations_for_entity(entity.id)
            relation_map = {
                relation.id: relation
                for observation in observations_scope
                for relation in self.logical_graph.contradictions(
                    "observation", observation.id
                )
            }
        else:
            relation_map = {
                item.id: item
                for item in self.logical_graph.contradictions(
                    request.node_type, request.node_id
                )
            }
        observation_ids = {
            node_id
            for relation in relation_map.values()
            for node_type, node_id in (
                (relation.origin_type, relation.origin_id),
                (relation.destination_type, relation.destination_id),
            )
            if node_type == "observation"
        }
        observations_raw = [
            item
            for observation_id in sorted(observation_ids)
            if (item := self.repository.get_observation(observation_id)) is not None
        ]
        evidence = self._merge_evidence(
            self._evidence_for_relations(relation_map.values()),
            self._evidence_for_observations(observations_raw),
        )
        return self._output(
            "find_contradictions",
            result_ids=list(relation_map),
            observations=[self._observation_record(item) for item in observations_raw],
            episodes=self._episode_records_for_observations(observations_raw),
            relations=[self._relation_record(item) for item in relation_map.values()],
            evidence=evidence,
            metadata={"read_only": True},
        )

    def compare_entities(self, request: CompareEntitiesInput) -> MemoryToolOutput:
        entity_a = self._entity(request.entity_a_id)
        entity_b = self._entity(request.entity_b_id)
        observations_a = list(
            self.logical_graph.observations_for_entity(
                entity_a.id, include_validated_aliases=False
            )
        )
        observations_b = list(
            self.logical_graph.observations_for_entity(
                entity_b.id, include_validated_aliases=False
            )
        )
        pair = {entity_a.id, entity_b.id}
        relations_raw = [
            item for item in self.repository.list_relations()
            if {item.origin_id, item.destination_id} == pair
            and item.origin_type == item.destination_type == "entity"
        ]
        hypotheses_raw = [
            item for item in self.repository.list_hypotheses()
            if item.object_id is not None
            and {item.subject_id, item.object_id} == pair
            and item.subject_type == item.object_type == "entity"
        ]
        validated_same = [
            item for item in relations_raw
            if item.relation_type == LogicalRelationType.SAME_ENTITY_AS.value
            and item.validation_state == ValidationState.VALIDATED
        ]
        identity_hypotheses = [
            item for item in hypotheses_raw
            if item.metadata.get("relation_type")
            == LogicalRelationType.POSSIBLY_SAME_ENTITY_AS.value
        ]
        identity_status = (
            "validated_same" if validated_same
            else "hypothesized_same" if identity_hypotheses
            else "undetermined"
        )
        environments_a = set(entity_a.environment_ids) | {
            item.environment_id for item in observations_a if item.environment_id
        }
        environments_b = set(entity_b.environment_ids) | {
            item.environment_id for item in observations_b if item.environment_id
        }
        latest_a = max(observations_a, key=self._observation_time) if observations_a else None
        latest_b = max(observations_b, key=self._observation_time) if observations_b else None
        overlap = any(
            self._observations_overlap(left, right)
            for left in observations_a for right in observations_b
        )
        confidence_values = [
            item.confidence for item in [*validated_same, *identity_hypotheses]
            if item.confidence is not None
        ]
        comparison = EntityComparisonRecord(
            entity_a_id=entity_a.id,
            entity_b_id=entity_b.id,
            identity_status=identity_status,
            shared_environment_ids=sorted(environments_a & environments_b),
            entity_a_only_environment_ids=sorted(environments_a - environments_b),
            entity_b_only_environment_ids=sorted(environments_b - environments_a),
            latest_observation_a_id=latest_a.id if latest_a else None,
            latest_observation_b_id=latest_b.id if latest_b else None,
            temporal_overlap=overlap,
            supporting_relation_ids=[item.id for item in validated_same],
            supporting_hypothesis_ids=[item.id for item in identity_hypotheses],
            confidence=max(confidence_values) if confidence_values else None,
        )
        evidence = self._merge_evidence(
            self._evidence_for_relations(relations_raw),
            self._raw_records_to_evidence(hypotheses_raw),
        )
        observations = (
            [self._observation_record(item) for item in observations_a + observations_b]
            if request.include_observations else []
        )
        return self._output(
            "compare_entities",
            result_ids=[entity_a.id, entity_b.id],
            observations=self._unique_observation_records(observations),
            entities=[self._entity_record(entity_a), self._entity_record(entity_b)],
            relations=[self._relation_record(item) for item in relations_raw],
            hypotheses=[self._hypothesis_record(item) for item in hypotheses_raw],
            evidence=evidence,
            comparisons=[comparison],
            metadata={"created_no_hypothesis": True, "read_only": True},
        )

    def search_documents(self, request: SearchDocumentsInput) -> MemoryToolOutput:
        observation_result = self.search_observations(
            SearchObservationsInput(
                query=request.query,
                environment_ids=request.environment_ids,
                source_ids=request.source_ids,
                modalities=["document", "text"],
                min_confidence=request.min_confidence,
                validation_states=request.validation_states,
                start_time=request.start_time,
                end_time=request.end_time,
                limit=request.limit,
            )
        )
        evidence_by_id = {item.id: item for item in observation_result.evidence}
        documents: list[DocumentToolRecord] = []
        for observation in observation_result.observations:
            references = [
                evidence_by_id[item]
                for item in observation.evidence_reference_ids
                if item in evidence_by_id
            ]
            if not observation.asset_id:
                continue
            documents.append(
                DocumentToolRecord(
                    id=f"document:{observation.id}",
                    observation_id=observation.id,
                    asset_id=observation.asset_id,
                    environment_id=observation.environment_id,
                    source_id=observation.source_id,
                    text=observation.description,
                    page_numbers=sorted(
                        {
                            int(item.locator["page_number"])
                            for item in references
                            if item.locator.get("page_number") is not None
                        }
                    ),
                    text_fragments=[
                        item.locator for item in references
                        if item.reference_kind == EvidenceReferenceKind.TEXT_FRAGMENT.value
                    ],
                    evidence_reference_ids=[item.id for item in references],
                    confidence=observation.confidence,
                    relevance_score=observation.semantic_score
                    if observation.semantic_score is not None
                    else self._lexical_score(request.query, observation.description),
                )
            )
        return self._output(
            "search_documents",
            result_ids=[item.id for item in documents],
            observations=observation_result.observations,
            episodes=observation_result.episodes,
            evidence=observation_result.evidence,
            documents=documents,
            metadata={"query": request.query, "read_only": True},
        )

    def get_environment_topology(
        self, request: GetEnvironmentTopologyInput
    ) -> MemoryToolOutput:
        environments_raw = self.repository.list_environments()
        if request.environment_ids:
            wanted = set(request.environment_ids)
            environments_raw = [item for item in environments_raw if item.id in wanted]
            missing = wanted - {item.id for item in environments_raw}
            if missing:
                raise ValueError(f"Ambientes no encontrados: {sorted(missing)}")
        environment_ids = {item.id for item in environments_raw}
        sources_raw = [
            item for item in self.repository.list_sources()
            if item.environment_id in environment_ids
        ]
        observations_in_scope = [
            item for item in self.repository.list_observations()
            if item.environment_id in environment_ids
        ]
        all_relations = self.repository.list_relations()
        related_entity_ids = {
            item.entity_id for item in observations_in_scope if item.entity_id
        }
        for relation in all_relations:
            endpoints = (
                (relation.origin_type, relation.origin_id),
                (relation.destination_type, relation.destination_id),
            )
            if any(
                node_type == "environment" and node_id in environment_ids
                for node_type, node_id in endpoints
            ):
                related_entity_ids.update(
                    node_id for node_type, node_id in endpoints
                    if node_type == "entity"
                )
        entities_raw = [
            item for item in self.repository.list_entities()
            if environment_ids & set(item.environment_ids)
            or item.id in related_entity_ids
        ]
        episodes_raw = [
            episode
            for environment_id in environment_ids
            for episode in self.repository.list_episodes_for_environment(environment_id)
        ]
        episodes_raw = list({item.id: item for item in episodes_raw}.values())
        entity_ids = {item.id for item in entities_raw}
        relations_raw = [
            item for item in all_relations
            if (
                (item.origin_type == "environment" and item.origin_id in environment_ids)
                or (item.destination_type == "environment" and item.destination_id in environment_ids)
                or (item.origin_type == "entity" and item.origin_id in entity_ids)
                or (item.destination_type == "entity" and item.destination_id in entity_ids)
            )
            and (
                request.include_inferred
                or (
                    item.metadata.get("derivation") != "inferred"
                    and item.metadata.get("transition_mode") != "inferred"
                )
            )
        ]
        nodes = [("entity", item.id) for item in entities_raw] + [
            ("environment", item) for item in environment_ids
        ]
        hypotheses = self._hypotheses_for_nodes(nodes) if request.include_hypotheses else []
        evidence = self._merge_evidence(
            self._evidence_for_relations(relations_raw),
            self._evidence_for_hypotheses(hypotheses),
        )
        entity_environments: dict[str, set[str]] = {
            item.id: set(item.environment_ids) for item in entities_raw
        }
        for observation in observations_in_scope:
            if observation.entity_id and observation.environment_id:
                entity_environments.setdefault(observation.entity_id, set()).add(
                    observation.environment_id
                )
        for relation in relations_raw:
            endpoints = {
                relation.origin_type: relation.origin_id,
                relation.destination_type: relation.destination_id,
            }
            entity_id = endpoints.get("entity")
            environment_id = endpoints.get("environment")
            if entity_id and environment_id:
                entity_environments.setdefault(entity_id, set()).add(environment_id)
        topology = [
            EnvironmentTopologyRecord(
                id=environment.id,
                name=environment.name,
                description=environment.description,
                source_ids=sorted(
                    item.id for item in sources_raw
                    if item.environment_id == environment.id
                ),
                entity_ids=sorted(
                    item.id for item in entities_raw
                    if environment.id in entity_environments.get(item.id, set())
                ),
                episode_ids=sorted(
                    item.id for item in episodes_raw
                    if environment.id in item.environment_ids
                ),
                relation_ids=sorted(
                    item.id for item in relations_raw
                    if environment.id in {item.origin_id, item.destination_id}
                ),
                confidence=environment.confidence,
            )
            for environment in environments_raw
        ]
        sources = [
            TopologySourceRecord(
                id=item.id,
                environment_id=item.environment_id,
                name=item.name,
                source_type=item.source_type.value,
                confidence=item.confidence,
            )
            for item in sources_raw
        ]
        return self._output(
            "get_environment_topology",
            result_ids=[item.id for item in topology],
            episodes=[self._episode_record(item) for item in episodes_raw],
            entities=[self._entity_record(item) for item in entities_raw],
            relations=[self._relation_record(item) for item in relations_raw],
            hypotheses=hypotheses,
            evidence=evidence,
            environments=topology,
            sources=sources,
            metadata={"include_inferred": request.include_inferred, "read_only": True},
        )

    def _hybrid_filters(
        self,
        request: SearchObservationsInput,
        episode_types: Iterable[str] = (),
    ) -> HybridSearchFilters:
        interval = None
        if request.start_time and request.end_time:
            interval = Interval(
                start=TimeSpec.from_utc(request.start_time, clock_source="memory_tool"),
                end=TimeSpec.from_utc(request.end_time, clock_source="memory_tool"),
            )
        return HybridSearchFilters(
            environment_ids=tuple(request.environment_ids),
            source_ids=tuple(request.source_ids),
            modalities=tuple(request.modalities),
            interval=interval,
            entity_ids=((request.entity_id,) if request.entity_id else ()),
            observation_types=tuple(request.observation_types),
            episode_types=tuple(episode_types),
            min_confidence=request.min_confidence,
            validation_states=tuple(request.validation_states),
        )

    def _observation_matches(
        self,
        item: Observation,
        request: SearchObservationsInput,
        entity_ids: set[str],
    ) -> bool:
        if request.environment_ids and item.environment_id not in request.environment_ids:
            return False
        if request.source_ids and item.source_id not in request.source_ids:
            return False
        if request.modalities and item.modality not in request.modalities:
            return False
        if entity_ids and item.entity_id not in entity_ids:
            return False
        if request.observation_types and item.observation_type not in request.observation_types:
            return False
        if request.min_confidence is not None and (
            item.confidence is None or item.confidence < request.min_confidence
        ):
            return False
        if request.validation_states and item.validation_state not in request.validation_states:
            return False
        return self._timeline_matches(item, request)

    @staticmethod
    def _timeline_matches(item: Observation, request: Any) -> bool:
        if request.start_time is None:
            return True
        start = item.start_time or (
            item.capture_time.absolute if item.capture_time else None
        )
        end = item.end_time or start
        if start is None or end is None:
            return False
        return start < request.end_time and end >= request.start_time

    def _resolved_entity_ids(self, entity_id_or_label: str | None) -> set[str]:
        if not entity_id_or_label:
            return set()
        entity = self._entity(entity_id_or_label)
        return set(self.logical_graph.resolved_entity_ids(entity.id))

    def _entity(self, entity_id_or_label: str) -> Entity:
        entity = self.repository.get_entity(entity_id_or_label)
        if entity is None:
            folded = entity_id_or_label.casefold()
            entity = next(
                (item for item in self.repository.list_entities()
                 if item.label.casefold() == folded),
                None,
            )
        if entity is None:
            raise ValueError(f"Entidad no encontrada: {entity_id_or_label}")
        return entity

    @staticmethod
    def _observation_time(item: Observation) -> datetime:
        return (
            item.end_time or item.start_time
            or (item.capture_time.absolute if item.capture_time else None)
            or item.created_at
        )

    @staticmethod
    def _observation_text(item: Observation) -> str:
        return " ".join(
            (item.description, item.content, json.dumps(item.attributes, ensure_ascii=False))
        )

    @staticmethod
    def _lexical_score(query: str | None, text: str) -> float:
        if not query:
            return 0.0
        tokens = set(re.findall(r"\w+", query.casefold()))
        if not tokens:
            return 0.0
        content = set(re.findall(r"\w+", text.casefold()))
        return len(tokens & content) / len(tokens)

    def _observation_record(
        self, item: Observation, semantic_score: float | None = None
    ) -> ObservationToolRecord:
        return ObservationToolRecord(
            id=item.id,
            episode_id=item.episode_id,
            entity_id=item.entity_id,
            environment_id=item.environment_id,
            source_id=item.source_id,
            asset_id=item.asset_id,
            modality=item.modality,
            observation_type=item.observation_type,
            description=item.description,
            attributes=item.attributes,
            start_time=item.start_time,
            end_time=item.end_time,
            validation_state=item.validation_state,
            confidence=item.confidence,
            evidence_reference_ids=item.evidence_reference_ids,
            semantic_score=semantic_score,
        )

    def _episode_record(
        self,
        item: Episode,
        evidence_reference_ids: Iterable[str] = (),
        scores: RetrievalScores | None = None,
    ) -> EpisodeToolRecord:
        observations = self.repository.list_observations_for_episode(item.id)
        observation_ids = list(dict.fromkeys(
            [*item.observation_ids, *(observation.id for observation in observations)]
        ))
        environment_ids = list(dict.fromkeys(
            [
                *item.environment_ids,
                *(observation.environment_id for observation in observations
                  if observation.environment_id),
            ]
        ))
        source_ids = list(dict.fromkeys(
            [
                *item.source_ids,
                *(observation.source_id for observation in observations
                  if observation.source_id),
            ]
        ))
        references = list(dict.fromkeys(
            [
                *evidence_reference_ids,
                *(reference_id for observation in observations
                  for reference_id in observation.evidence_reference_ids),
            ]
        ))
        return EpisodeToolRecord(
            id=item.id,
            title=item.title,
            episode_type=str(item.metadata.get("episode_type", "generic")),
            environment_ids=environment_ids,
            source_ids=source_ids,
            observation_ids=observation_ids,
            start_time=item.start_time,
            end_time=item.end_time,
            confidence=item.confidence,
            evidence_reference_ids=references,
            scores=(
                {
                    "structured": scores.structured,
                    "semantic": scores.semantic,
                    "temporal": scores.temporal,
                    "relational": scores.relational,
                }
                if scores else {}
            ),
        )

    @staticmethod
    def _entity_record(item: Entity) -> EntityToolRecord:
        return EntityToolRecord(
            id=item.id,
            label=item.label,
            entity_type=item.entity_type,
            description=item.description,
            environment_ids=item.environment_ids,
            confidence=item.confidence,
        )

    @staticmethod
    def _relation_record(item: Relation) -> RelationToolRecord:
        return RelationToolRecord(
            id=item.id,
            relation_type=item.relation_type,
            origin_type=item.origin_type,
            origin_id=item.origin_id,
            destination_type=item.destination_type,
            destination_id=item.destination_id,
            validation_state=item.validation_state,
            is_validated=item.validation_state == ValidationState.VALIDATED,
            method=item.method,
            confidence=item.confidence,
            evidence_reference_ids=item.evidence_reference_ids,
            metadata=item.metadata,
        )

    @staticmethod
    def _hypothesis_record(item: Hypothesis) -> HypothesisToolRecord:
        return HypothesisToolRecord(
            id=item.id,
            statement=item.statement,
            subject_type=item.subject_type,
            subject_id=item.subject_id,
            object_type=item.object_type,
            object_id=item.object_id,
            state=item.state.value,
            confidence=item.confidence,
            evidence_reference_ids=item.evidence_reference_ids,
            metadata=item.metadata,
        )

    @staticmethod
    def _evidence_record(item: EvidenceReference) -> EvidenceToolRecord:
        locator = {
            key: value for key, value in {
                "start_time": item.start_time,
                "end_time": item.end_time,
                "start_offset_seconds": item.start_offset_seconds,
                "end_offset_seconds": item.end_offset_seconds,
                "frame_number": item.frame_number,
                "region": item.region,
                "page_number": item.page_number,
                "text_start": item.text_start,
                "text_end": item.text_end,
                "text_quote": item.text_quote,
            }.items() if value is not None
        }
        return EvidenceToolRecord(
            id=item.id,
            asset_id=item.asset_id,
            reference_kind=item.reference_kind.value,
            locator=locator,
            confidence=item.confidence,
            producer=item.producer,
            model_version=item.model_version,
        )

    def _hypotheses_for_nodes(
        self, nodes: Iterable[tuple[str, str]]
    ) -> list[HypothesisToolRecord]:
        hypotheses = {
            item.id: item
            for node_type, node_id in nodes
            for item in self.repository.list_hypotheses(node_type, node_id)
        }
        return [self._hypothesis_record(item) for item in hypotheses.values()]

    def _relations_for_nodes(
        self, nodes: Iterable[tuple[str, str]]
    ) -> list[Relation]:
        return list({
            item.id: item
            for node_type, node_id in nodes
            for item in self.repository.list_relations(node_type=node_type, node_id=node_id)
        }.values())

    def _records_for_nodes(
        self, nodes: Iterable[tuple[str, str]]
    ) -> tuple[list[Observation], list[Episode], list[Entity]]:
        observations: dict[str, Observation] = {}
        episodes: dict[str, Episode] = {}
        entities: dict[str, Entity] = {}
        for node_type, node_id in nodes:
            if node_type == "observation":
                item = self.repository.get_observation(node_id)
                if item:
                    observations[item.id] = item
            elif node_type == "episode":
                item = self.repository.get_episode(node_id)
                if item:
                    episodes[item.id] = item
            elif node_type == "entity":
                item = self.repository.get_entity(node_id)
                if item:
                    entities[item.id] = item
        return list(observations.values()), list(episodes.values()), list(entities.values())

    def _node_exists(self, node_type: str, node_id: str) -> bool:
        getters = {
            "observation": self.repository.get_observation,
            "episode": self.repository.get_episode,
            "entity": self.repository.get_entity,
            "environment": self.repository.get_environment,
            "source": self.repository.get_source,
            "asset": self.repository.get_asset,
            "relation": self.repository.get_relation,
            "hypothesis": self.repository.get_hypothesis,
            "evidence_reference": self.repository.get_evidence_reference,
        }
        getter = getters.get(node_type)
        return getter is not None and getter(node_id) is not None

    def _episode_records_for_observations(
        self, observations: Iterable[Observation]
    ) -> list[EpisodeToolRecord]:
        episodes = {
            item.episode_id: self.repository.get_episode(item.episode_id)
            for item in observations if item.episode_id
        }
        return [
            self._episode_record(item)
            for item in episodes.values() if item is not None
        ]

    def _raw_evidence(self, reference_ids: Iterable[str]) -> list[EvidenceReference]:
        return [
            item for reference_id in dict.fromkeys(reference_ids)
            if (item := self.repository.get_evidence_reference(reference_id)) is not None
        ]

    def _raw_evidence_for_observations(
        self, observations: Iterable[Observation]
    ) -> list[EvidenceReference]:
        return self._raw_evidence(
            reference_id for item in observations
            for reference_id in item.evidence_reference_ids
        )

    def _raw_evidence_for_relations(
        self, relations: Iterable[Relation]
    ) -> list[EvidenceReference]:
        return self._raw_evidence(
            reference_id for item in relations
            for reference_id in item.evidence_reference_ids
        )

    def _raw_evidence_for_hypotheses(
        self, hypotheses: Iterable[Hypothesis]
    ) -> list[EvidenceReference]:
        return self._raw_evidence(
            reference_id for item in hypotheses
            for reference_id in item.evidence_reference_ids
        )

    def _evidence_for_observations(
        self, observations: Iterable[Observation]
    ) -> list[EvidenceToolRecord]:
        return [self._evidence_record(item) for item in self._raw_evidence_for_observations(observations)]

    def _evidence_for_relations(
        self, relations: Iterable[Relation]
    ) -> list[EvidenceToolRecord]:
        return [self._evidence_record(item) for item in self._raw_evidence_for_relations(relations)]

    def _evidence_for_hypotheses(
        self, hypotheses: Iterable[HypothesisToolRecord]
    ) -> list[EvidenceToolRecord]:
        return [
            self._evidence_record(item)
            for item in self._raw_evidence(
                reference_id for hypothesis in hypotheses
                for reference_id in hypothesis.evidence_reference_ids
            )
        ]

    def _raw_records_to_evidence(
        self, hypotheses: Iterable[Hypothesis]
    ) -> list[EvidenceToolRecord]:
        return [self._evidence_record(item) for item in self._raw_evidence_for_hypotheses(hypotheses)]

    @staticmethod
    def _unique_observation_records(
        items: Iterable[ObservationToolRecord],
    ) -> list[ObservationToolRecord]:
        return list({item.id: item for item in items}.values())

    @staticmethod
    def _unique_relation_records(
        items: Iterable[RelationToolRecord],
    ) -> list[RelationToolRecord]:
        return list({item.id: item for item in items}.values())

    @staticmethod
    def _unique_evidence_records(
        items: Iterable[EvidenceToolRecord],
    ) -> list[EvidenceToolRecord]:
        return list({item.id: item for item in items}.values())

    def _merge_evidence(
        self, *groups: Iterable[EvidenceToolRecord]
    ) -> list[EvidenceToolRecord]:
        return self._unique_evidence_records(
            item for group in groups for item in group
        )

    @staticmethod
    def _observations_overlap(left: Observation, right: Observation) -> bool:
        left_start = left.start_time or left.created_at
        left_end = left.end_time or left_start
        right_start = right.start_time or right.created_at
        right_end = right.end_time or right_start
        return left_start <= right_end and right_start <= left_end

    @staticmethod
    def _output(
        tool: str,
        *,
        result_ids: list[str] | None = None,
        observations: list[ObservationToolRecord] | None = None,
        episodes: list[EpisodeToolRecord] | None = None,
        entities: list[EntityToolRecord] | None = None,
        relations: list[RelationToolRecord] | None = None,
        hypotheses: list[HypothesisToolRecord] | None = None,
        evidence: list[EvidenceToolRecord] | None = None,
        documents: list[DocumentToolRecord] | None = None,
        environments: list[EnvironmentTopologyRecord] | None = None,
        sources: list[TopologySourceRecord] | None = None,
        comparisons: list[EntityComparisonRecord] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryToolOutput:
        collections = [
            observations or [], episodes or [], entities or [], relations or [],
            hypotheses or [], evidence or [], documents or [], environments or [],
            sources or [], comparisons or [],
        ]
        confidences = [
            item.confidence for collection in collections for item in collection
            if getattr(item, "confidence", None) is not None
        ]
        return MemoryToolOutput(
            tool=tool,
            result_ids=result_ids or [],
            observations=observations or [],
            episodes=episodes or [],
            entities=entities or [],
            relations=relations or [],
            hypotheses=hypotheses or [],
            evidence=evidence or [],
            documents=documents or [],
            environments=environments or [],
            sources=sources or [],
            comparisons=comparisons or [],
            confidence=(sum(confidences) / len(confidences) if confidences else None),
            metadata=metadata or {"read_only": True},
        )
