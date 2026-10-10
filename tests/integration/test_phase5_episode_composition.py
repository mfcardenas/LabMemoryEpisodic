"""Criterios de aceptación de la composición episódica determinista."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.application.episode_composition import (
    DeterministicEpisodeComposer,
    EpisodeRuleConfig,
)
from episodic.domain.entities import Observation, Source
from episodic.domain.provenance import Environment
from episodic.domain.types import ProvenanceKind, SourceType
from episodic.examples.phase5_scenarios import build_phase5_scenario


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = PROJECT_ROOT / "data_samples" / "phase5_observations.json"
RULES = PROJECT_ROOT / "data_samples" / "phase5_rules.json"


@pytest.fixture
def phase5_repo(tmp_path):
    repository = SqliteEpisodicRepository(tmp_path / "phase5.db")
    try:
        yield repository
    finally:
        repository.close()


def _composed_scenario(repository, *, sequence_numbers=None):
    scenario = build_phase5_scenario(
        SCENARIOS, window_sequence_numbers=sequence_numbers
    )
    scenario.persist(repository)
    rules = EpisodeRuleConfig.from_json_file(RULES)
    result = DeterministicEpisodeComposer(repository, rules).compose(
        scenario.environment.id
    )
    return scenario, rules, result


def _micro_for(result, observation_id):
    return next(
        item for item in result.microepisodes if observation_id in item.observation_ids
    )


def test_required_phase5_scenarios_and_evidence_conservation(phase5_repo) -> None:
    scenario, _, result = _composed_scenario(phase5_repo)

    across_1 = _micro_for(result, "obs-action-across-window-1")
    across_2 = _micro_for(result, "obs-action-across-window-2")
    assert across_1.id == across_2.id
    assert across_1.metadata["processing_window_ids"] == [
        "camera_A_window_1",
        "camera_A_window_2",
    ]

    action_a = _micro_for(result, "obs-same-window-action-a")
    action_b = _micro_for(result, "obs-same-window-action-b")
    assert action_a.id != action_b.id
    assert action_a.metadata["processing_window_ids"] == ["camera_A_window_2"]
    assert action_b.metadata["processing_window_ids"] == ["camera_A_window_2"]

    camera_a = _micro_for(result, "obs-simultaneous-camera-a")
    camera_b = _micro_for(result, "obs-simultaneous-camera-b")
    assert camera_a.id == camera_b.id
    assert "synchronized_sources" in camera_a.metadata["grouping_decisions"][1]["rules"]

    no_evidence = _micro_for(result, "obs-interval-without-evidence")
    assert no_evidence.evidence_reference_ids == []
    assert no_evidence.start_time < no_evidence.end_time

    presence = _micro_for(result, "obs-presence")
    contradiction = _micro_for(result, "obs-contradiction")
    assert presence.id != contradiction.id
    assert contradiction.metadata["contains_contradiction"] is True
    assert "contradiction_boundary" in contradiction.metadata["boundary_start_rules"]

    grouped_observations = {
        observation_id
        for microepisode in result.microepisodes
        for observation_id in microepisode.observation_ids
    }
    grouped_evidence = {
        reference_id
        for microepisode in result.microepisodes
        for reference_id in microepisode.evidence_reference_ids
    }
    assert grouped_observations == {item.id for item in scenario.observations}
    assert grouped_evidence == {item.id for item in scenario.evidence_references}
    assert len(result.episodes) == 1
    assert set(result.episodes[0].microepisode_ids) == {
        item.id for item in result.microepisodes
    }
    assert set(result.episodes[0].metadata["evidence_reference_ids"]) == grouped_evidence
    assert result.episodes[0].metadata["contains_contradiction"] is True
    assert all(
        phase5_repo.get_observation(item.id).episode_id == result.episodes[0].id
        for item in scenario.observations
    )


def test_grouping_does_not_depend_on_processing_window_numbering(tmp_path) -> None:
    first_repo = SqliteEpisodicRepository(tmp_path / "first.db")
    second_repo = SqliteEpisodicRepository(tmp_path / "second.db")
    try:
        _, _, first = _composed_scenario(
            first_repo,
            sequence_numbers={
                "camera_A_window_1": 999,
                "camera_A_window_2": 1,
                "camera_A_window_3": 400,
                "camera_B_window_1": 88,
                "microphone_A_window_1": 2,
            },
        )
        _, _, second = _composed_scenario(
            second_repo,
            sequence_numbers={
                "camera_A_window_1": 0,
                "camera_A_window_2": 500,
                "camera_A_window_3": 7,
                "camera_B_window_1": 1,
                "microphone_A_window_1": 900,
            },
        )
    finally:
        first_repo.close()
        second_repo.close()

    assert [item.id for item in first.microepisodes] == [
        item.id for item in second.microepisodes
    ]
    assert [item.observation_ids for item in first.microepisodes] == [
        item.observation_ids for item in second.microepisodes
    ]
    assert [item.id for item in first.episodes] == [item.id for item in second.episodes]


def test_composition_is_reproducible_and_supports_reconstruction(phase5_repo) -> None:
    scenario, rules, first = _composed_scenario(phase5_repo)
    composer = DeterministicEpisodeComposer(phase5_repo, rules)
    repeated = composer.compose(scenario.environment.id)

    assert [item.to_dict() for item in repeated.microepisodes] == [
        item.to_dict() for item in first.microepisodes
    ]
    assert [item.to_dict() for item in repeated.episodes] == [
        item.to_dict() for item in first.episodes
    ]
    assert repeated.removed_microepisode_ids == ()
    assert repeated.removed_episode_ids == ()

    changed = EpisodeRuleConfig.from_dict(
        {**rules.to_dict(), "max_episode_gap_seconds": 0.5}
    )
    rebuilt = DeterministicEpisodeComposer(phase5_repo, changed).compose(
        scenario.environment.id
    )

    assert set(rebuilt.removed_microepisode_ids) == {item.id for item in first.microepisodes}
    assert set(rebuilt.removed_episode_ids) == {item.id for item in first.episodes}
    assert len(phase5_repo.list_microepisodes(scenario.environment.id)) == len(
        rebuilt.microepisodes
    )
    assert len(phase5_repo.list_episodes_for_environment(scenario.environment.id)) == len(
        rebuilt.episodes
    )


def test_scene_entity_interaction_and_acoustic_boundaries_are_traced(
    phase5_repo,
) -> None:
    environment = Environment(id="env-boundaries", name="Boundary lab")
    camera = Source(
        id="camera-boundaries",
        environment_id=environment.id,
        name="Camera",
        source_type=SourceType.CAMERA,
    )
    microphone = Source(
        id="mic-boundaries",
        environment_id=environment.id,
        name="Microphone",
        source_type=SourceType.MICROPHONE,
    )
    phase5_repo.save_environment(environment)
    phase5_repo.save_source(camera)
    phase5_repo.save_source(microphone)
    base = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)

    specs = [
        ("base", camera.id, "video", {}),
        ("scene", camera.id, "video", {"scene_change_score": 0.9}),
        ("entry", camera.id, "video", {"entity_transition": "entry"}),
        ("interaction-start", camera.id, "video", {"interaction_phase": "start"}),
        ("interaction-end", camera.id, "video", {"interaction_phase": "end"}),
        ("after-interaction", camera.id, "video", {}),
        ("acoustic", microphone.id, "audio", {"acoustic_relevance": 0.95}),
        ("exit", camera.id, "video", {"entity_transition": "exit"}),
        ("after-exit", camera.id, "video", {}),
    ]
    for index, (observation_id, source_id, modality, attributes) in enumerate(specs):
        instant = base + timedelta(seconds=index)
        phase5_repo.save_observation(
            Observation(
                id=f"boundary-{observation_id}",
                content=observation_id,
                modality=modality,
                observation_type="boundary_test",
                attributes={"action": "work", "entity_ids": ["worker"], **attributes},
                provenance_kind=ProvenanceKind.SCENARIO_FIXTURE,
                environment_id=environment.id,
                source_id=source_id,
                start_time=instant,
                end_time=instant + timedelta(milliseconds=100),
                producer="phase5-test",
                model_version="1",
                created_at=instant,
            )
        )

    config = EpisodeRuleConfig(
        synchronized_source_groups=((camera.id, microphone.id),)
    )
    result = DeterministicEpisodeComposer(phase5_repo, config).compose(environment.id)
    traced = {
        rule
        for item in result.microepisodes
        for decision in item.metadata["grouping_decisions"]
        for rule in decision["rules"]
    }

    assert {
        "significant_scene_change",
        "entity_entry",
        "interaction_start",
        "interaction_end",
        "relevant_acoustic_event",
        "entity_exit",
    } <= traced


def test_rule_configuration_rejects_unknown_or_invalid_values() -> None:
    with pytest.raises(ValueError, match="Reglas desconocidas"):
        EpisodeRuleConfig.from_dict({"window_sequence_weight": 1})
    with pytest.raises(ValueError, match="entre 0 y 1"):
        EpisodeRuleConfig(scene_change_threshold=1.5)
