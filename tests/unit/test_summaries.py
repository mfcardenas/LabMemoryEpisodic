"""Resúmenes jerárquicos: entradas, revisiones y verificación de citas."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from episodic.adapters.models.fake_text import FakeTextComposer
from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.application.summaries import PROMPT_VERSION, SummaryService, day_key
from episodic.domain.entities import Episode, Observation, Source
from episodic.domain.passages import Passage, episode_passages
from episodic.domain.processing import Event
from episodic.domain.provenance import Asset, Environment
from episodic.domain.summaries import Summary
from episodic.domain.types import EvidenceMediaType, SourceType
from episodic.ports.text_composer import Composition

T0 = datetime(2026, 10, 12, 14, 21, 15, tzinfo=timezone.utc)


@pytest.fixture()
def repo(tmp_path):
    repository = SqliteEpisodicRepository(tmp_path / "memory.db")
    repository.save_environment(Environment(id="taller", name="Taller", producer="test"))
    repository.save_source(Source(id="taller.cam", environment_id="taller", name="cam", source_type=SourceType.CAMERA))
    repository.save_asset(Asset(id="asset-1", environment_id="taller", source_id="taller.cam", uri="x://v.mp4",
                                media_type=EvidenceMediaType.VIDEO, content_hash="h1"))
    yield repository
    repository.close()


def _episode(repo, external_id="taller_1", start=T0, titles=("Aplicar lubricante", "Girar la pieza")) -> Episode:
    episode = Episode(id=f"ep-{external_id}", title=external_id, external_id=external_id, environment_ids=["taller"],
                      start_time=start, end_time=start + timedelta(seconds=10), source_ids=["taller.cam"])
    repo.save_episode(episode)
    for seq, title in enumerate(titles):
        repo.save_observation(Observation(id=f"obs-{external_id}-{seq}", episode_id=episode.id, observation_type="video_event",
                                          description=title, environment_id="taller", source_id="taller.cam", asset_id="asset-1"))
        repo.save_event(Event(id=f"ev-{external_id}-{seq}", event_type="video_segment", description=f"Se ve {title.lower()}.",
                              environment_id="taller", source_id="taller.cam", asset_id="asset-1", sequence_number=seq, title=title,
                              start_offset_seconds=seq * 5.0, end_offset_seconds=seq * 5.0 + 5.0, episode_id=episode.id,
                              start_time=start + timedelta(seconds=seq * 5), end_time=start + timedelta(seconds=seq * 5 + 5),
                              producer="video-event-derivation", model_version="events-v2",
                              attributes={"speech": [{"observation_id": "x", "text": "Mira esto.", "start_offset_seconds": 1.0}]},
                              metadata={"observation_ids": [f"obs-{external_id}-{seq}"]}))
    episode.observation_ids = [f"obs-{external_id}-{seq}" for seq in range(len(titles))]
    repo.save_episode(episode)
    repo.replace_episode_passages(episode.id, episode_passages(episode, repo.list_events(episode.id), []) + [
        Passage(id=f"doc-{external_id}", kind="document_text_fragment", episode_id=episode.id, text="Manual: usar lubricante.", page_number=2),
    ])
    return episode


def test_episode_summary_is_written_from_events_speech_and_documents(repo) -> None:
    episode = _episode(repo)
    composer = FakeTextComposer()
    service = SummaryService(repo, composer, timezone=timezone.utc)

    summary = service.summarize("episode", episode.id, reason="captura")

    assert summary is not None and summary.revision == 1 and summary.is_current
    assert summary.model_id == "fake-text" and summary.prompt_version == PROMPT_VERSION
    prompt = composer.calls[0]["prompt"]
    assert "[ev 0 · 00:00] (00:00–00:05) Aplicar lubricante" in prompt and "[ev 1 · 00:05]" in prompt
    assert "se dijo (00:01): «Mira esto.»" in prompt and "pág. 2: Manual: usar lubricante." in prompt
    assert summary.highlights == [
        {"event_sequence": 0, "offset_seconds": 0.0, "text": "Momento del evento 0"},
        {"event_sequence": 1, "offset_seconds": 5.0, "text": "Momento del evento 1"},
    ]
    assert "[ev 0 · 00:00]" in summary.narrative
    assert summary.metadata["provenance_kind"] == "model_generated" and summary.metadata["reason"] == "captura"
    assert repo.get_current_summary("episode", episode.id).id == summary.id
    # Sin cambios en la entrada no se vuelve a llamar al modelo.
    assert service.summarize("episode", episode.id).id == summary.id and len(composer.calls) == 1


def test_regenerating_creates_a_new_revision_and_keeps_the_old_one(repo) -> None:
    episode = _episode(repo)
    service = SummaryService(repo, FakeTextComposer(), timezone=timezone.utc)
    first = service.summarize("episode", episode.id)
    second = service.summarize("episode", episode.id, force=True, reason="manual")

    assert second.revision == 2 and second.id != first.id
    assert repo.get_current_summary("episode", episode.id).id == second.id
    revisions = repo.list_summary_revisions("episode", episode.id)
    assert [item.revision for item in revisions] == [2, 1] and revisions[1].is_current is False
    assert repo.count_summaries("episode") == 1


class LyingComposer:
    """Cita eventos que no existen: deben eliminarse y quedar anotados."""

    model_id = "liar"

    def compose(self, prompt, *, system="", schema=None):
        return Composition(
            data={
                "title": "Mentira",
                "summary": "Resumen.",
                "narrative": "Primero pasa algo [ev 0 · 00:00]. Luego otra cosa [ev 7 · 01:00].",
                "highlights": [{"event_sequence": 1, "offset_seconds": 99.0, "text": "real"},
                               {"event_sequence": 9, "offset_seconds": 0.0, "text": "inventado"}, "basura"],
                "open_questions": [],
            },
            model_id=self.model_id,
        )


def test_invalid_citations_are_removed_and_offsets_corrected(repo) -> None:
    episode = _episode(repo)
    summary = SummaryService(repo, LyingComposer(), timezone=timezone.utc).summarize("episode", episode.id)
    assert summary.narrative == "Primero pasa algo [ev 0 · 00:00]. Luego otra cosa ."
    assert summary.highlights == [{"event_sequence": 1, "offset_seconds": 5.0, "text": "real"}]
    assert summary.metadata["dropped_citations"] == ["[ev 7 · 01:00]"]
    assert len(summary.metadata["dropped_highlights"]) == 2


def test_day_and_environment_digests_build_on_the_lower_level(repo) -> None:
    morning = _episode(repo, "taller_1", T0)
    afternoon = _episode(repo, "taller_2", T0 + timedelta(hours=3), titles=("Retirar la pieza",))
    _episode(repo, "taller_3", T0 + timedelta(days=2))
    composer = FakeTextComposer()
    service = SummaryService(repo, composer, timezone=timezone.utc)
    for episode in (morning, afternoon):
        service.summarize("episode", episode.id)

    key = day_key("taller", T0)
    assert key == "taller|2026-10-12"
    digest = service.summarize("day", key)
    assert digest is not None and digest.scope == "day"
    prompt = composer.calls[-1]["prompt"]
    assert "[ep:taller_1]" in prompt and "[ep:taller_2]" in prompt and "[ep:taller_3]" not in prompt
    assert "Resumen simulado" in prompt  # usa los resúmenes de episodio, no los eventos
    assert digest.highlights == [{"episode_external_id": "taller_1", "text": "Episodio taller_1"},
                                 {"episode_external_id": "taller_2", "text": "Episodio taller_2"}]
    assert service.summarize("day", "taller|2026-10-20") is None  # día sin episodios

    overview = service.summarize("environment", "taller")
    assert overview is not None and "[día 2026-10-12]" in composer.calls[-1]["prompt"]
    assert overview.highlights == [{"day": "2026-10-12", "text": "Día 2026-10-12"}]
    assert [item.scope_key for item in repo.list_current_summaries("day", prefix="taller|")] == [key]


def test_without_a_composer_nothing_is_written(repo) -> None:
    episode = _episode(repo)
    service = SummaryService(repo, None)
    assert not service.enabled and service.summarize("episode", episode.id) is None


def test_summary_requires_provenance() -> None:
    with pytest.raises(Exception):
        Summary(id="s", scope="episode", scope_key="ep", model_id="")
