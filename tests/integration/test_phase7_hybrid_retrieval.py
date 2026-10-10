"""Criterios de aceptacion de la recuperacion hibrida de Fase 7."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from episodic.application.indexing import episode_index_text
from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.domain.retrieval import HybridSearchFilters
from episodic.domain.times import Interval, TimeSpec
from episodic.domain.types import ValidationState
from episodic.examples.phase6_maintenance import persist_phase6_maintenance_scenario
from episodic.interfaces.http.server import create_api_app


@pytest.fixture
def phase7_container(tmp_path):
    container = create_app_container(
        EpisodicConfig(data_dir=tmp_path / "phase7"),
        use_fake_encoders=True,
        use_memory_qdrant=True,
    )
    persist_phase6_maintenance_scenario(container.repository)
    container.lifecycle_service.rebuild_index(container.indexing_worker)
    try:
        yield container
    finally:
        container.close()


def test_all_structured_filters_return_canonical_episode_observations_and_evidence(
    phase7_container,
) -> None:
    start = datetime(2026, 10, 6, 10, 4, tzinfo=timezone.utc)
    run = phase7_container.hybrid_retrieval_service.search(
        filters=HybridSearchFilters(
            environment_ids=("workshop_south",),
            source_ids=("camera_south_a",),
            modalities=("video",),
            interval=Interval(
                start=TimeSpec.from_utc(start),
                end=TimeSpec.from_utc(start + timedelta(minutes=4)),
            ),
            entity_ids=("drill_north_track",),
            observation_types=("tool_presence",),
            episode_types=("maintenance",),
            min_confidence=0.9,
            validation_states=(ValidationState.VALIDATED,),
        )
    )

    assert [item.episode.id for item in run.results] == ["episode_south_repair"]
    result = run.results[0]
    assert [item.id for item in result.observations] == ["observation_drill_south"]
    assert [item.id for item in result.evidence_references] == ["evidence_drill_south"]
    assert result.scores.structured == 1.0
    assert result.scores.temporal == 1.0
    assert result.scores.semantic is None
    assert run.results_without_evidence == 0


def test_semantic_projection_indexes_descriptions_and_textual_attributes(
    phase7_container,
) -> None:
    episode = phase7_container.repository.get_episode("episode_north_inspection")
    projection = episode_index_text(phase7_container.repository, episode)
    assert "El taladro azul" in projection
    assert '"action":"observe_tool"' in projection

    run = phase7_container.hybrid_retrieval_service.search(
        text_query=projection,
        filters=HybridSearchFilters(episode_types=("inspection",)),
        limit=3,
    )
    assert run.results[0].episode.id == episode.id
    assert run.results[0].scores.semantic == pytest.approx(1.0)
    assert "semantic_similarity" in run.results[0].match_kinds


def test_temporal_and_relational_retrieval_keep_validation_and_evidence(
    phase7_container,
) -> None:
    service = phase7_container.hybrid_retrieval_service
    context = service.episode_context("episode_north_inventory")
    assert {item.episode.id for item in context.results} == {
        "episode_north_inspection",
        "episode_north_inventory",
        "episode_south_repair",
    }
    expanded = [item for item in context.results if not item.is_direct_match]
    assert expanded
    assert all(item.scores.relational == 1.0 for item in expanded)
    assert all(item.relation_expansions for item in expanded)
    assert all(
        relation.is_validated
        for item in expanded
        for relation in item.relation_expansions
    )
    assert all(item.evidence_references for item in expanded)

    contradictions = service.search(
        filters=HybridSearchFilters(entity_ids=("drill_north_track",)),
        expand_relations=True,
        relation_types=("CONTRADICTS",),
    )
    contradiction_relations = [
        expansion
        for item in contradictions.results
        for expansion in item.relation_expansions
        if expansion.relation.relation_type == "CONTRADICTS"
    ]
    assert contradiction_relations
    assert all(item.is_validated for item in contradiction_relations)
    assert {
        reference.id
        for item in contradiction_relations
        for reference in item.evidence_references
    } == {"evidence_drill_south", "evidence_drill_absent"}


def test_last_observation_and_cross_environment_query(phase7_container) -> None:
    service = phase7_container.hybrid_retrieval_service
    latest = service.last_confirmed_observation("Taladro azul (taller norte)")
    assert latest.results[0].observations[0].id == "observation_drill_south"

    cross_environment = service.search(
        filters=HybridSearchFilters(
            entity_ids=("drill_north_track",),
            validation_states=(ValidationState.VALIDATED,),
        )
    )
    assert {
        environment_id
        for result in cross_environment.results
        for environment_id in result.episode.environment_ids
    } == {"workshop_north", "workshop_south"}


def test_hybrid_http_contract_is_directly_verifiable_from_front(phase7_container) -> None:
    app = create_api_app(container=phase7_container)
    with TestClient(app) as client:
        frontend = client.get("/")
        response = client.post(
            "/api/hybrid-search",
            json={
                "entity_filter": "drill_north_track",
                "environment_filter": ["workshop_south"],
                "validation_state_filter": ["validated"],
                "expand_relations": True,
                "relation_type_filter": ["CONTRADICTS"],
            },
        )
    assert frontend.status_code == 200
    assert 'id="search-environment"' in frontend.text
    assert 'id="search-query"' in frontend.text
    assert response.status_code == 200
    payload = response.json()
    assert payload["results"]
    result = payload["results"][0]
    assert result["episode"]["id"] == "episode_south_repair"
    assert result["observations"][0]["id"] == "observation_drill_south"
    assert result["evidence_references"][0]["id"] == "evidence_drill_south"
    assert set(result["scores"]) == {
        "structured", "semantic", "temporal", "relational"
    }
