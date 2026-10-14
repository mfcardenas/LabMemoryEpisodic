"""Persistencia SQLite e invariantes referenciales de Fase 2."""
from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from episodic.adapters.sqlite.migrations import MIGRATION_V1, init_connection
from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.domain.relations import Relation
from episodic.domain.types import HypothesisState, ValidationState
from episodic.examples.pink_pipette import build_pink_pipette_fixture


@pytest.fixture
def phase2_repo(tmp_path):
    repository = SqliteEpisodicRepository(tmp_path / "phase2.db")
    try:
        yield repository
    finally:
        repository.close()


def test_create_and_recover_required_entities(phase2_repo) -> None:
    fixture = build_pink_pipette_fixture()
    fixture.persist(phase2_repo)

    assert len(phase2_repo.list_environments()) == 3  # incluye entorno legado de migración
    assert len(phase2_repo.list_assets()) == 3
    assert phase2_repo.get_environment(fixture.environments[0].id) == fixture.environments[0]
    assert phase2_repo.get_source(fixture.sources[0].id) == fixture.sources[0]
    assert phase2_repo.get_asset(fixture.assets[0].id) == fixture.assets[0]
    assert phase2_repo.get_processing_run(fixture.processing_runs[0].id) == fixture.processing_runs[0]
    assert phase2_repo.get_processing_window(fixture.processing_windows[0].id) == fixture.processing_windows[0]
    assert phase2_repo.get_episode(fixture.processing_windows[0].id) is None
    assert phase2_repo.get_event(fixture.events[0].id) == fixture.events[0]
    assert phase2_repo.get_microepisode(fixture.microepisodes[0].id) == fixture.microepisodes[0]
    assert phase2_repo.get_episode(fixture.episodes[0].id) == fixture.episodes[0]


def test_observation_links_to_exact_evidence_reference(phase2_repo) -> None:
    fixture = build_pink_pipette_fixture()
    fixture.persist(phase2_repo)
    expected = fixture.observations[0]

    observation = phase2_repo.get_observation(expected.id)
    reference = phase2_repo.get_evidence_reference(expected.evidence_reference_ids[0])

    assert observation == expected
    assert reference is not None
    assert reference.asset_id == observation.asset_id
    assert phase2_repo.get_observation(fixture.observations[1].id).episode_id is None


def test_original_asset_is_immutable(phase2_repo) -> None:
    fixture = build_pink_pipette_fixture()
    fixture.persist(phase2_repo)

    with pytest.raises(ValueError, match="Asset inmutable"):
        phase2_repo.save_asset(
            replace(fixture.assets[0], uri="fixtures/changed-original.mp4")
        )


def test_pink_pipette_fixture_is_idempotent(phase2_repo) -> None:
    build_pink_pipette_fixture().persist(phase2_repo)
    build_pink_pipette_fixture().persist(phase2_repo)

    assert len(phase2_repo.list_assets()) == 3
    assert phase2_repo._conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 3
    assert phase2_repo._conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 1


def test_relation_with_missing_reference_is_rejected(phase2_repo) -> None:
    fixture = build_pink_pipette_fixture()
    fixture.persist(phase2_repo)
    invalid = Relation(
        relation_type="observed_in",
        origin_type="entity",
        origin_id=fixture.entities[0].id,
        destination_type="environment",
        destination_id="00000000-0000-0000-0000-000000000000",
        method="test",
        producer="test",
        model_version="1",
        confidence=1.0,
    )

    with pytest.raises(ValueError, match="referencias inexistentes"):
        phase2_repo.save_relation(invalid)
    assert phase2_repo.get_relation(invalid.id) is None


def test_hypothesis_is_not_a_validated_relation(phase2_repo) -> None:
    fixture = build_pink_pipette_fixture()
    fixture.persist(phase2_repo)

    relation = phase2_repo.get_relation(fixture.relations[0].id)
    hypothesis = phase2_repo.get_hypothesis(fixture.hypotheses[0].id)

    assert relation is not None
    assert relation.validation_state == ValidationState.VALIDATED
    assert hypothesis is not None
    assert hypothesis.state == HypothesisState.OPEN
    assert phase2_repo.get_relation(hypothesis.id) is None


def test_migration_v2_preserves_legacy_observation(tmp_path) -> None:
    database = tmp_path / "legacy-v1.db"
    connection = sqlite3.connect(database)
    init_connection(connection)
    connection.executescript(MIGRATION_V1)
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
        ("2026-01-01T00:00:00+00:00",),
    )
    connection.execute(
        """
        INSERT INTO sources(id, name, source_type, scope, clock_source, metadata_json, created_at)
        VALUES ('legacy-source', 'Legacy', 'camera', '', 'system', '{}', ?)
        """,
        ("2026-01-01T00:00:00+00:00",),
    )
    connection.execute(
        """
        INSERT INTO episodes(
            id, title, source_ids_json, tags_json, evidence_ids_json,
            observation_ids_json, indexable_text, created_at, metadata_json
        ) VALUES ('legacy-episode', 'Legacy', '["legacy-source"]', '[]', '[]',
                  '["legacy-observation"]', '', ?, '{}')
        """,
        ("2026-01-01T00:00:00+00:00",),
    )
    connection.execute(
        """
        INSERT INTO observations(
            id, episode_id, content, provenance_kind, author,
            evidence_ids_json, created_at, metadata_json
        ) VALUES ('legacy-observation', 'legacy-episode', 'dato previo',
                  'human_declaration', 'legacy', '[]', ?, '{}')
        """,
        ("2026-01-01T00:00:01+00:00",),
    )
    connection.commit()
    connection.close()

    repository = SqliteEpisodicRepository(database)
    try:
        source = repository.get_source("legacy-source")
        observation = repository.get_observation("legacy-observation")
        versions = {
            row[0]
            for row in repository._conn.execute(
                "SELECT version FROM schema_migrations"
            ).fetchall()
        }
    finally:
        repository.close()

    assert versions == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}
    assert source is not None
    assert source.environment_id == "env-legacy-default"
    assert observation is not None
    assert observation.episode_id == "legacy-episode"
    assert observation.content == "dato previo"
