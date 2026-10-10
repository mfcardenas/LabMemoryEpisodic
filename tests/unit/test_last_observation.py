"""Pruebas de orden temporal y alcance de última observación."""

from datetime import datetime, timezone
from pathlib import Path

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.domain.entities import Entity, Episode, Observation
from episodic.domain.times import TimeSpec
from episodic.domain.types import SearchIntent, Verdict


def _utc(day: int) -> datetime:
    return datetime(2026, 1, day, 12, 0, tzinfo=timezone.utc)


def test_last_observation_uses_capture_time_and_reports_unknown_scope(tmp_path: Path) -> None:
    config = EpisodicConfig(data_dir=str(tmp_path / "last_observation"))
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    try:
        entity = Entity(id="entity-1", label="portátil Atlas", entity_type="object")
        container.repository.save_entity(entity)

        episodes = [
            Episode(id="ep-old", title="captura antigua", source_ids=["camera-1"]),
            Episode(id="ep-new", title="captura más reciente", source_ids=["camera-1"]),
            Episode(id="ep-unknown", title="captura sin reloj", source_ids=["camera-1"]),
            Episode(id="ep-hidden", title="captura eliminada", source_ids=["camera-1"]),
        ]
        for episode in episodes:
            container.repository.save_episode(episode)
        container.repository.set_episode_visibility("ep-hidden", False)

        # El orden de created_at contradice deliberadamente el de captura.
        observations = [
            Observation(
                id="obs-old",
                episode_id="ep-old",
                entity_id=entity.id,
                content="observación antigua",
                capture_time=TimeSpec.from_utc(_utc(1)),
                created_at=_utc(4),
            ),
            Observation(
                id="obs-new",
                episode_id="ep-new",
                entity_id=entity.id,
                content="última captura conocida",
                capture_time=TimeSpec.from_utc(_utc(2)),
                created_at=_utc(1),
            ),
            Observation(
                id="obs-unknown",
                episode_id="ep-unknown",
                entity_id=entity.id,
                content="observación sin tiempo fiable",
                capture_time=TimeSpec.unknown(),
                created_at=_utc(5),
            ),
            Observation(
                id="obs-hidden",
                episode_id="ep-hidden",
                entity_id=entity.id,
                content="captura posterior pero eliminada",
                capture_time=TimeSpec.from_utc(_utc(5)),
                created_at=_utc(5),
            ),
        ]
        for observation in observations:
            container.repository.save_observation(observation)

        run = container.retrieval_service.search(
            intent=SearchIntent.LAST_OBSERVATION,
            entity_filter=entity.id,
        )

        assert run.results[0].episode_id == "ep-new"
        assert run.results[0].verdict == Verdict.CANDIDATE
        assert run.overall_verdict == Verdict.INSUFFICIENT_EVIDENCE
        assert any("latest_known" in reason for reason in run.reasons)
    finally:
        container.close()
