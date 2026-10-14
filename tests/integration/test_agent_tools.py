"""Herramientas para agentes (Fase A1): momentos citables, vecinos, resumen,
navegación de arriba abajo y documentos, con salidas compactas."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.domain.entities import Episode, Observation, Source
from episodic.domain.passages import Passage
from episodic.domain.processing import Event
from episodic.domain.provenance import Asset, Environment
from episodic.domain.types import EvidenceMediaType, SourceType
from episodic.interfaces.http.server import create_api_app

T0 = datetime(2026, 10, 12, 14, 21, 15, tzinfo=timezone.utc)


def _episode(repo, external_id, start, titles, speech=None):
    episode = Episode(id=f"ep-{external_id}", title=external_id, external_id=external_id, environment_ids=["taller"],
                      start_time=start, end_time=start + timedelta(seconds=5 * len(titles)), source_ids=["taller.cam"])
    repo.save_episode(episode)
    for seq, title in enumerate(titles):
        repo.save_observation(Observation(id=f"obs-{external_id}-{seq}", episode_id=episode.id, observation_type="video_event",
                                          description=title, environment_id="taller", source_id="taller.cam", asset_id="asset-1"))
        attributes = {"actions": [title.split()[0].lower()], "objects": [{"label": "llave", "attributes": ["roja"]}], "visible_text": ["F-102"]}
        if speech and seq == 0:
            attributes["speech"] = [{"observation_id": "x", "text": speech, "start_offset_seconds": 1.0}]
        repo.save_event(Event(id=f"ev-{external_id}-{seq}", event_type="video_segment", description=f"El mecánico decide {title.lower()}.",
                              environment_id="taller", source_id="taller.cam", asset_id="asset-1", sequence_number=seq, title=title,
                              start_offset_seconds=seq * 5.0, end_offset_seconds=seq * 5.0 + 5.0,
                              start_time=start + timedelta(seconds=seq * 5), end_time=start + timedelta(seconds=seq * 5 + 5),
                              producer="video-event-derivation", model_version="events-v2", attributes=attributes,
                              metadata={"observation_ids": [f"obs-{external_id}-{seq}"]}))
    episode.observation_ids = [f"obs-{external_id}-{seq}" for seq in range(len(titles))]
    repo.save_episode(episode)
    repo.link_events_to_episode(episode.id, episode.observation_ids)
    return episode


@pytest.fixture()
def memory(tmp_path):
    config = EpisodicConfig(data_dir=str(tmp_path / "taller"), watch_config="", query_timezone="UTC")
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    repo = container.repository
    repo.save_environment(Environment(id="taller", name="Taller", producer="test"))
    repo.save_source(Source(id="taller.cam", environment_id="taller", name="cam", source_type=SourceType.CAMERA))
    repo.save_asset(Asset(id="asset-1", environment_id="taller", source_id="taller.cam", uri="x://v.mp4",
                          media_type=EvidenceMediaType.VIDEO, content_hash="h1"))
    first = _episode(repo, "taller_1", T0, ["Aplicar lubricante", "Girar la pieza con la llave", "Retirar la pieza"],
                     speech="Mira, esto está agarrotado.")
    second = _episode(repo, "taller_2", T0 + timedelta(days=1), ["Montar la rueda"])
    for episode in (first, second):
        container.summaries.summarize("episode", episode.id)
        container.lifecycle_service.index_episodes(container.indexing_worker, [episode.id])
    # Un documento adjunto al primer episodio, como pasaje.
    repo.replace_episode_passages(first.id, repo.list_passages_for_episode(first.id) + [
        Passage(id="doc-1", kind="document_text_fragment", episode_id=first.id, text="Manual: aplicar lubricante y esperar.", page_number=2),
        Passage(id="doc-2", kind="document_text_fragment", episode_id=first.id, text="Par de apriete de la rueda: 110 Nm.", page_number=5),
    ])
    container.summaries.summarize("day", "taller|2026-10-12")
    container.summaries.summarize("environment", "taller")
    yield container
    container.close()


def test_search_moments_returns_citable_moments_with_plan(memory) -> None:
    out = memory.memory_tools.execute("search_moments", {"query": "¿qué pasó después de aplicar lubricante?", "max_chars": 120})
    assert out.tool == "search_moments" and out.moments
    hit = next(item for item in out.moments if item.event_id == "ev-taller_1-0")
    assert hit.episode_external_id == "taller_1" and hit.start_offset_seconds == 0.0 and hit.kind == "video_event"
    assert len(hit.text) <= 120 and hit.scores["dense_rank"] is not None
    assert [item.title for item in hit.after] == ["Girar la pieza con la llave", "Retirar la pieza"]
    assert out.metadata["plan"]["intent"] == "antes_despues" and out.metadata["candidates"]["fused"] > 0
    assert all(item.kind != "episode_summary" for item in out.moments)

    with_summaries = memory.memory_tools.execute("search_moments", {"query": "resumen simulado", "include_summaries": True})
    assert any(item.kind == "episode_summary" for item in with_summaries.moments)


def test_get_event_and_neighbors_expose_structured_content(memory) -> None:
    event = memory.memory_tools.execute("get_event", {"event_id": "ev-taller_1-0"}).moments[0]
    assert event.actions == ["aplicar"] and event.objects == ["llave · roja"] and event.visible_text == ["F-102"]
    assert event.speech == [{"text": "Mira, esto está agarrotado.", "start_offset_seconds": 1.0}]
    assert event.episode_external_id == "taller_1"

    around = memory.memory_tools.execute("get_event_neighbors", {"event_id": "ev-taller_1-1", "before": 1, "after": 1})
    assert [item.event_id for item in around.moments] == ["ev-taller_1-0", "ev-taller_1-1", "ev-taller_1-2"]
    assert around.metadata["center"] == "ev-taller_1-1"
    with pytest.raises(ValueError):
        memory.memory_tools.execute("get_event", {"event_id": "nope"})


def test_episode_summary_is_marked_generated(memory) -> None:
    out = memory.memory_tools.execute("get_episode_summary", {"episode_id": "ep-taller_1"})
    assert out.summaries and out.summaries[0].record_kind == "generated" and out.summaries[0].revision == 1
    assert out.summaries[0].highlights[0]["event_sequence"] == 0
    assert out.metadata["generated"] is True


def test_browse_memory_goes_from_environments_to_events(memory) -> None:
    tools = memory.memory_tools
    envs = tools.execute("browse_memory", {"level": "environments"}).browse
    assert [(item.id, item.count) for item in envs] == [("taller", 2)]
    assert envs[0].summary_title and envs[0].level == "environment"

    days = tools.execute("browse_memory", {"level": "days", "environment_id": "taller"}).browse
    assert [(item.label, item.count) for item in days] == [("2026-10-12", 1), ("2026-10-13", 1)]
    assert days[0].summary_title and days[1].summary_title is None

    episodes = tools.execute("browse_memory", {"level": "episodes", "environment_id": "taller", "day": "2026-10-12"})
    assert [item.external_id for item in episodes.browse] == ["taller_1"] and episodes.browse[0].count == 3
    assert episodes.metadata["total"] == 1 and episodes.metadata["has_more"] is False

    events = tools.execute("browse_memory", {"level": "events", "episode_id": "ep-taller_1", "page_size": 2})
    assert [item.label for item in events.browse] == ["Aplicar lubricante", "Girar la pieza con la llave"]
    assert events.metadata["has_more"] is True and events.browse[0].count == 1  # una frase de habla
    page2 = tools.execute("browse_memory", {"level": "events", "episode_id": "ep-taller_1", "page_size": 2, "page": 2})
    assert [item.label for item in page2.browse] == ["Retirar la pieza"]
    with pytest.raises(Exception):
        tools.execute("browse_memory", {"level": "events"})


def test_document_passages_of_an_episode(memory) -> None:
    out = memory.memory_tools.execute("get_document_passages", {"episode_id": "ep-taller_1", "query": "par de apriete", "limit": 1})
    assert [item.id for item in out.moments] == ["doc-2"] and out.moments[0].page_number == 5
    assert out.metadata["total_document_passages"] == 2


def test_agent_tools_are_published_and_callable_over_http(memory) -> None:
    with TestClient(create_api_app(container=memory, start_watcher=False)) as client:
        catalog = {item["name"]: item for item in client.get("/api/tools").json()}
        assert {"search_moments", "browse_memory", "get_event"} <= set(catalog)
        assert "query" in catalog["search_moments"]["input_schema"]["properties"]
        body = client.post("/api/tools/search_moments", json={"query": "agarrotado"}).json()
        assert body["moments"] and body["moments"][0]["episode_external_id"] == "taller_1"
        assert client.post("/api/tools/browse_memory", json={"level": "days"}).status_code == 422
