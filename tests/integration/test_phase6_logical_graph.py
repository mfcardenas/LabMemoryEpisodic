"""Criterios de aceptación del grafo lógico de Fase 6."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.application.logical_graph import LogicalGraphService
from episodic.domain.model import DomainValidationError
from episodic.domain.relations import Relation
from episodic.domain.types import (
    HypothesisState,
    LogicalRelationType,
    TransitionMode,
    ValidationState,
)
from episodic.examples.phase6_maintenance import (
    persist_phase6_maintenance_scenario,
)


@pytest.fixture
def phase6_repo(tmp_path):
    repository = SqliteEpisodicRepository(tmp_path / "phase6.db")
    try:
        yield repository
    finally:
        repository.close()


def test_initial_relation_vocabulary_and_identity_resolution(phase6_repo) -> None:
    scenario = persist_phase6_maintenance_scenario(phase6_repo)
    graph = LogicalGraphService(phase6_repo)
    relation_types = {item.relation_type for item in scenario.relations}

    assert relation_types | {
        scenario.identity_hypothesis.metadata["relation_type"]
    } == {item.value for item in LogicalRelationType}
    assert scenario.identity_hypothesis.state == HypothesisState.SUPPORTED
    assert scenario.identity_hypothesis.metadata["relation_type"] == (
        LogicalRelationType.POSSIBLY_SAME_ENTITY_AS.value
    )
    assert graph.resolved_entity_ids("drill_north_track") == (
        "drill_north_track",
        "drill_south_track",
    )

    same = next(
        item
        for item in scenario.relations
        if item.relation_type == LogicalRelationType.SAME_ENTITY_AS.value
    )
    assert same.validation_state == ValidationState.VALIDATED
    moved = next(
        item
        for item in scenario.relations
        if item.relation_type == LogicalRelationType.MOVED_TO.value
    )
    assert moved.metadata["transition_mode"] == TransitionMode.INFERRED.value
    assert moved.metadata["from_environment_id"] == "workshop_north"


def test_graph_queries_preserve_observations_contradictions_and_evidence(
    phase6_repo,
) -> None:
    scenario = persist_phase6_maintenance_scenario(phase6_repo)
    graph = LogicalGraphService(phase6_repo)

    observations = graph.observations_for_entity("drill_north_track")
    assert [item.id for item in observations] == [
        "observation_drill_north",
        "observation_drill_south",
        "observation_drill_absent",
    ]
    assert tuple(phase6_repo.get_observation(item.id) for item in scenario.observations) == (
        scenario.observations
    )

    episode_relations = graph.relations_for_episode("episode_north_inventory")
    assert {item.relation_type for item in episode_relations} == {
        LogicalRelationType.BEFORE.value,
        LogicalRelationType.AFTER.value,
    }
    traversal = graph.traverse_episodes("episode_north_inventory")
    assert [item.episode.id for item in traversal.previous] == [
        "episode_north_inspection"
    ]
    assert [item.episode.id for item in traversal.following] == [
        "episode_south_repair"
    ]
    overlap = graph.traverse_episodes("episode_south_repair")
    assert [item.episode.id for item in overlap.overlapping] == [
        "episode_south_second_angle"
    ]

    gap = graph.traverse_episodes("episode_north_inspection").following[0]
    assert gap.observation_gap is not None
    assert gap.observation_gap["duration_seconds"] == 1200.0
    full_chain = graph.traverse_episodes("episode_north_inspection").following
    assert [item.episode.id for item in full_chain] == [
        "episode_north_inventory",
        "episode_south_repair",
    ]
    assert [item.depth for item in full_chain] == [1, 2]
    assert [
        item.episode.id
        for item in graph.traverse_episodes(
            "episode_north_inspection", max_depth=1
        ).following
    ] == ["episode_north_inventory"]

    contradictions = graph.contradictions()
    assert len(contradictions) == 1
    assert {
        contradictions[0].origin_id,
        contradictions[0].destination_id,
    } == {"observation_drill_south", "observation_drill_absent"}
    contradiction_evidence = graph.evidence_for_relation(contradictions[0].id)
    assert {item.id for item in contradiction_evidence} == {
        "evidence_drill_south",
        "evidence_drill_absent",
    }

    last = graph.last_confirmed_observation("drill_north_track")
    assert last is not None
    assert last.observation.id == "observation_drill_south"
    assert [item.id for item in last.evidence_references] == ["evidence_drill_south"]
    assert [item.id for item in last.contradictions] == [contradictions[0].id]


def test_every_inferred_relation_keeps_method_and_evidence(phase6_repo) -> None:
    scenario = persist_phase6_maintenance_scenario(phase6_repo)
    inferred = [
        item
        for item in scenario.relations
        if item.metadata.get("derivation") == "inferred"
        or item.relation_type == LogicalRelationType.DERIVED_FROM.value
    ]

    assert inferred
    assert all(item.method for item in inferred)
    assert all(item.evidence_reference_ids for item in inferred)


def test_relation_invariants_reject_hypothesis_as_fact_and_unfounded_inference() -> None:
    common = {
        "origin_type": "entity",
        "origin_id": "entity-a",
        "destination_type": "entity",
        "destination_id": "entity-b",
        "method": "test",
        "producer": "test",
        "model_version": "1",
        "created_at": datetime(2026, 10, 6, tzinfo=timezone.utc),
    }
    with pytest.raises(DomainValidationError, match="Hypothesis"):
        Relation(
            relation_type=LogicalRelationType.POSSIBLY_SAME_ENTITY_AS,
            **common,
        )
    with pytest.raises(DomainValidationError, match="decisión validada"):
        Relation(
            relation_type=LogicalRelationType.SAME_ENTITY_AS,
            validation_state=ValidationState.PROPOSED,
            **common,
        )
    with pytest.raises(DomainValidationError, match="transition_mode"):
        Relation(
            relation_type=LogicalRelationType.MOVED_TO,
            destination_type="environment",
            metadata={},
            **{key: value for key, value in common.items() if key != "destination_type"},
        )
    with pytest.raises(DomainValidationError, match="requiere referencias"):
        Relation(
            relation_type=LogicalRelationType.DERIVED_FROM,
            origin_type="relation",
            destination_type="hypothesis",
            evidence_reference_ids=[],
            **{
                key: value
                for key, value in common.items()
                if key not in {"origin_type", "destination_type"}
            },
        )


def test_graph_service_rejects_invalid_endpoint_contracts(phase6_repo) -> None:
    persist_phase6_maintenance_scenario(phase6_repo)
    graph = LogicalGraphService(phase6_repo)

    with pytest.raises(ValueError, match="debe enlazar episodios"):
        graph.create_relation(
            LogicalRelationType.BEFORE,
            origin_type="entity",
            origin_id="drill_north_track",
            destination_type="entity",
            destination_id="drill_south_track",
            method="invalid",
        )
    with pytest.raises(ValueError, match="use propose_possible_same_entity"):
        graph.create_relation(
            LogicalRelationType.POSSIBLY_SAME_ENTITY_AS,
            origin_type="entity",
            origin_id="drill_north_track",
            destination_type="entity",
            destination_id="drill_south_track",
            method="invalid",
        )
