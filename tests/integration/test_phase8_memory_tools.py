"""Pruebas independientes de las once herramientas deterministas."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.examples.phase8_tools import persist_phase8_tool_scenario
from episodic.interfaces.http.server import create_api_app


@pytest.fixture(scope="module")
def phase8_runtime(tmp_path_factory):
    container = create_app_container(
        EpisodicConfig(data_dir=tmp_path_factory.mktemp("phase8") / "data"),
        use_fake_encoders=True,
        use_memory_qdrant=True,
    )
    scenario = persist_phase8_tool_scenario(container.repository)
    container.lifecycle_service.rebuild_index(container.indexing_worker)
    try:
        yield container, scenario
    finally:
        container.close()


def test_tool_registry_has_eleven_typed_read_only_contracts(phase8_runtime) -> None:
    container, _ = phase8_runtime
    descriptors = container.memory_tools.describe()
    assert {item.name for item in descriptors} == {
        "search_observations", "search_episodes", "get_episode",
        "get_entity_timeline", "get_last_confirmed_observation",
        "expand_relations", "get_supporting_evidence", "find_contradictions",
        "compare_entities", "search_documents", "get_environment_topology",
    }
    assert all(item.read_only for item in descriptors)
    assert all(item.input_schema["type"] == "object" for item in descriptors)
    assert all(item.output_schema["type"] == "object" for item in descriptors)
    with pytest.raises(ValidationError):
        container.memory_tools.execute("get_episode", {"episode_id": "x", "extra": 1})


def test_search_observations(phase8_runtime) -> None:
    container, _ = phase8_runtime
    output = container.memory_tools.execute("search_observations", {
        "query": "taladro azul",
        "entity_id": "drill_north_track",
        "validation_states": ["validated"],
    })
    assert {item.id for item in output.observations} == {
        "observation_drill_north", "observation_drill_south"
    }
    assert all(item.record_kind == "fact" for item in output.observations)
    assert {item.id for item in output.evidence} == {
        "evidence_drill_north", "evidence_drill_south"
    }


def test_search_episodes(phase8_runtime) -> None:
    container, _ = phase8_runtime
    output = container.memory_tools.execute("search_episodes", {
        "environment_ids": ["workshop_south"],
        "modalities": ["video"],
        "validation_states": ["validated"],
    })
    assert [item.id for item in output.episodes] == ["episode_south_repair"]
    assert output.observations[0].id == "observation_drill_south"
    assert output.evidence[0].id == "evidence_drill_south"
    assert output.confidence is not None


def test_get_episode(phase8_runtime) -> None:
    container, _ = phase8_runtime
    output = container.memory_tools.execute(
        "get_episode", {"episode_id": "episode_north_inventory"}
    )
    assert output.episodes[0].id == "episode_north_inventory"
    assert "observation_maintenance_document" in output.episodes[0].observation_ids
    assert "evidence_review_record" in output.episodes[0].evidence_reference_ids
    assert {item.id for item in output.observations} >= {
        "observation_wrench_north", "observation_maintenance_document"
    }


def test_get_entity_timeline_keeps_hypotheses_separate(phase8_runtime) -> None:
    container, scenario = phase8_runtime
    output = container.memory_tools.execute(
        "get_entity_timeline", {"entity_id": "drill_north_track"}
    )
    assert [item.id for item in output.observations] == [
        "observation_drill_north", "observation_drill_south",
        "observation_drill_absent",
    ]
    assert scenario.maintenance.identity_hypothesis.id in {
        item.id for item in output.hypotheses
    }
    assert all(item.record_kind == "hypothesis" for item in output.hypotheses)


def test_get_last_confirmed_observation(phase8_runtime) -> None:
    container, _ = phase8_runtime
    output = container.memory_tools.execute(
        "get_last_confirmed_observation", {"entity_id": "drill_north_track"}
    )
    assert output.result_ids == ["observation_drill_south"]
    assert output.observations[0].validation_state.value == "validated"
    assert output.evidence[0].id == "evidence_drill_south"


def test_expand_relations(phase8_runtime) -> None:
    container, _ = phase8_runtime
    output = container.memory_tools.execute("expand_relations", {
        "node_type": "episode",
        "node_id": "episode_north_inventory",
        "relation_types": ["BEFORE", "AFTER"],
        "validation_states": ["validated"],
        "max_depth": 2,
    })
    assert {item.relation_type for item in output.relations} == {"BEFORE", "AFTER"}
    assert all(item.is_validated for item in output.relations)
    assert output.evidence


def test_get_supporting_evidence(phase8_runtime) -> None:
    container, scenario = phase8_runtime
    hypothesis_id = scenario.maintenance.identity_hypothesis.id
    output = container.memory_tools.execute("get_supporting_evidence", {
        "target_type": "hypothesis", "target_id": hypothesis_id,
    })
    assert output.hypotheses[0].id == hypothesis_id
    assert output.hypotheses[0].record_kind == "hypothesis"
    assert {item.id for item in output.evidence} == {
        "evidence_drill_north", "evidence_drill_south"
    }


def test_find_contradictions(phase8_runtime) -> None:
    container, _ = phase8_runtime
    output = container.memory_tools.execute(
        "find_contradictions", {"entity_id": "drill_north_track"}
    )
    assert [item.relation_type for item in output.relations] == ["CONTRADICTS"]
    assert {item.id for item in output.observations} == {
        "observation_drill_south", "observation_drill_absent"
    }
    assert {item.id for item in output.evidence} == {
        "evidence_drill_south", "evidence_drill_absent"
    }


def test_compare_entities_does_not_promote_hypothesis(phase8_runtime) -> None:
    container, scenario = phase8_runtime
    before = container.repository.list_hypotheses()
    output = container.memory_tools.execute("compare_entities", {
        "entity_a_id": "drill_north_track",
        "entity_b_id": "drill_south_track",
    })
    after = container.repository.list_hypotheses()
    assert output.comparisons[0].identity_status == "validated_same"
    assert output.comparisons[0].supporting_relation_ids
    assert output.comparisons[0].supporting_hypothesis_ids == [
        scenario.maintenance.identity_hypothesis.id
    ]
    assert before == after


def test_search_documents(phase8_runtime) -> None:
    container, _ = phase8_runtime
    output = container.memory_tools.execute(
        "search_documents", {"query": "numero de serie", "limit": 5}
    )
    assert [item.observation_id for item in output.documents] == [
        "observation_maintenance_document"
    ]
    assert output.documents[0].page_numbers == [2]
    assert output.documents[0].evidence_reference_ids == ["evidence_review_record"]


def test_get_environment_topology(phase8_runtime) -> None:
    container, scenario = phase8_runtime
    output = container.memory_tools.execute("get_environment_topology", {
        "environment_ids": ["workshop_north", "workshop_south"],
    })
    assert {item.id for item in output.environments} == {
        "workshop_north", "workshop_south"
    }
    assert {item.id for item in output.sources} >= {
        "camera_north", "camera_south_a", "maintenance_log"
    }
    assert "MOVED_TO" in {item.relation_type for item in output.relations}
    assert scenario.maintenance.identity_hypothesis.id in {
        item.id for item in output.hypotheses
    }


def test_tools_never_mutate_primary_observations(phase8_runtime) -> None:
    container, scenario = phase8_runtime
    before = [item.to_dict() for item in container.repository.list_observations()]
    calls = {
        "search_observations": {},
        "search_episodes": {},
        "get_episode": {"episode_id": "episode_north_inventory"},
        "get_entity_timeline": {"entity_id": "drill_north_track"},
        "get_last_confirmed_observation": {"entity_id": "drill_north_track"},
        "expand_relations": {"node_type": "episode", "node_id": "episode_north_inventory"},
        "get_supporting_evidence": {"target_type": "observation", "target_id": "observation_drill_north"},
        "find_contradictions": {},
        "compare_entities": {"entity_a_id": "drill_north_track", "entity_b_id": "drill_south_track"},
        "search_documents": {},
        "get_environment_topology": {},
    }
    for name, payload in calls.items():
        container.memory_tools.execute(name, payload)
    assert [item.to_dict() for item in container.repository.list_observations()] == before


def test_tool_http_api_validates_and_executes_without_llm(tmp_path) -> None:
    container = create_app_container(
        EpisodicConfig(data_dir=tmp_path / "phase8-http"),
        use_fake_encoders=True,
        use_memory_qdrant=True,
    )
    persist_phase8_tool_scenario(container.repository)
    container.lifecycle_service.rebuild_index(container.indexing_worker)
    app = create_api_app(container=container)
    with TestClient(app) as client:
        catalog = client.get("/api/tools")
        result = client.post(
            "/api/tools/get_last_confirmed_observation",
            json={"entity_id": "drill_north_track"},
        )
        invalid = client.post(
            "/api/tools/get_episode", json={"episode_id": "x", "extra": True}
        )
    assert catalog.status_code == 200
    assert len(catalog.json()) == 11
    assert result.status_code == 200
    assert result.json()["result_ids"] == ["observation_drill_south"]
    assert invalid.status_code == 422
