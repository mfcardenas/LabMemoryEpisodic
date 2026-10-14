"""El índice se mantiene al día solo: huella de indexado y reindexado en la cola."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from episodic.application.index_maintenance import (
    PROFILE_KEY,
    REINDEX_JOB_KIND,
    TRIGGER_MANUAL,
    TRIGGER_STARTUP,
    IndexMaintenanceService,
)
from episodic.bootstrap import create_app_container, submit_reindex
from episodic.config import EpisodicConfig
from episodic.domain.captures import JobState
from episodic.interfaces.http.server import create_api_app


def _container(tmp_path, **overrides):
    config = EpisodicConfig(data_dir=str(tmp_path / "data"), watch_config="", **overrides)
    return create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)


@pytest.fixture()
def atlas(tmp_path):
    """Atlas indexado sin huella: como una base anterior a esta comprobación."""
    container = _container(tmp_path)
    container.ingest_service.ingest_manifest(Path("data/atlas/manifest.json"))
    while container.indexing_worker.process_pending_outbox(batch_size=50):
        pass
    yield container
    container.close()


def _submit(container):
    return lambda trigger: submit_reindex(container, trigger)


def _run_queued(container):
    while container.job_runner.run_next():
        pass


def test_empty_base_records_the_profile_without_reindexing(tmp_path) -> None:
    container = _container(tmp_path)
    try:
        maintenance = container.index_maintenance
        assert maintenance.ensure_current(_submit(container)) is None
        assert container.repository.get_system_state(PROFILE_KEY) == maintenance.profile
        assert maintenance.status().up_to_date
    finally:
        container.close()


def test_index_without_profile_is_rebuilt_by_a_queued_job(atlas) -> None:
    maintenance = atlas.index_maintenance
    status = maintenance.status()
    assert status.full and "no consta" in status.reasons[0]

    job = maintenance.ensure_current(_submit(atlas))
    assert job is not None and job.job_type == REINDEX_JOB_KIND and job.target_id == TRIGGER_STARTUP
    # Mientras haya uno en cola no se encola otro.
    assert maintenance.ensure_current(_submit(atlas)).id == job.id

    _run_queued(atlas)
    done = atlas.repository.get_job(job.id)
    assert done.state == JobState.COMPLETED
    assert [stage.state.value for stage in done.stages] == ["completed", "completed"]
    assert done.result["full"] is True
    assert "no consta" in done.stages[0].detail
    assert maintenance.status().up_to_date
    assert atlas.repository.get_system_state(PROFILE_KEY) == maintenance.profile
    assert atlas.vector_index.count() == 35  # 10 episodios + 15 pasajes + 10 imágenes
    assert maintenance.ensure_current(_submit(atlas)) is None


def test_changing_the_document_prefix_requires_a_full_rebuild(atlas) -> None:
    atlas.index_maintenance.rebuild()
    changed = IndexMaintenanceService(
        atlas.repository,
        atlas.vector_index,
        atlas.lifecycle_service,
        atlas.indexing_worker,
        {**atlas.index_maintenance.profile, "document_prefix": "otro: "},
    )
    status = changed.status()
    assert status.full
    assert status.reasons == ["cambió el prefijo de documento: 'search_document: ' → 'otro: '"]


def test_missing_passages_are_indexed_without_a_full_rebuild(atlas) -> None:
    maintenance = atlas.index_maintenance
    maintenance.rebuild()
    episode = atlas.repository.list_episodes(limit=1)[0][0]
    atlas.repository._conn.execute(
        "DELETE FROM representations WHERE target_id = ? AND target_type = 'episode_passages'", (episode.id,)
    )
    status = maintenance.status()
    assert not status.full and status.missing_episode_ids == [episode.id]
    assert status.reasons == ["1 episodio(s) sin pasajes indexados"]

    maintenance.ensure_current(_submit(atlas))
    _run_queued(atlas)
    assert maintenance.status().up_to_date


def test_server_start_queues_the_reindex_and_the_api_shows_it(atlas) -> None:
    with TestClient(create_api_app(container=atlas, start_watcher=False)) as client:
        job = atlas.index_maintenance.active_job() or next(
            job for job in atlas.repository.list_recent_jobs(5) if job.job_type == REINDEX_JOB_KIND
        )
        assert job.target_id == TRIGGER_STARTUP
        atlas.job_runner.wait(job.id, timeout=60)

        status = client.get("/api/index/status").json()
        assert status["up_to_date"] is True and status["job"] is None
        assert status["stored_profile"] == status["current_profile"]

        manual = client.post("/api/index/reindex")
        assert manual.status_code == 202
        body = manual.json()
        assert body["target_id"] == TRIGGER_MANUAL
        atlas.job_runner.wait(body["id"], timeout=60)
        assert atlas.repository.get_job(body["id"]).result["full"] is True

        jobs = client.get("/api/jobs").json()["jobs"]
        assert jobs[0]["title"] == "Reindexado de la memoria"
