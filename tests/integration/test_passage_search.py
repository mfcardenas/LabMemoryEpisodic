"""Búsqueda por pasajes sobre el corpus Atlas: el momento exacto, por dos vías."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.domain.passages import passage_from_observation, passage_point_id
from episodic.domain.retrieval import HybridSearchFilters
from episodic.domain.times import Interval, TimeSpec
from episodic.domain.types import RepresentationSpace
from episodic.interfaces.http.server import create_api_app


@pytest.fixture()
def atlas(tmp_path):
    config = EpisodicConfig(data_dir=str(tmp_path / "atlas"), watch_config="")
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    container.ingest_service.ingest_manifest(Path("data/atlas/manifest.json"))
    while container.indexing_worker.process_pending_outbox(batch_size=50):
        pass
    yield container
    container.close()


def _external_ids(run) -> list[str]:
    return [group.episode.external_id for group in run.groups]


def test_exact_code_is_found_by_full_text_and_reranked(atlas) -> None:
    run = atlas.passage_search.search("sensor de humo F-102")

    assert _external_ids(run)[0] == "ATLAS-EP-005"
    hit = run.groups[0].passages[0]
    assert hit.scores.lexical_rank == 1
    assert "\x02" in hit.snippet  # término resaltado
    assert run.rerank_applied and run.rerank_model == "fake-reranker"
    assert hit.scores.rerank is not None


def test_phrase_finds_every_episode_that_mentions_it(atlas) -> None:
    run = atlas.passage_search.search('"suela blanca"')
    lexical = {
        group.episode.external_id
        for group in run.groups
        if any(hit.scores.lexical_rank for hit in group.passages)
    }
    assert lexical == {"ATLAS-EP-006", "ATLAS-EP-008"}


def test_interval_filter_applies_to_both_paths(atlas) -> None:
    queries = json.loads(Path("data/atlas/queries.json").read_text(encoding="utf-8"))
    q3 = next(item for item in queries if item["id"] == "q3_simultaneous_events")
    run = atlas.passage_search.search(
        "mochila puerta recepción", HybridSearchFilters(interval=Interval.from_dict(q3["interval"]))
    )
    assert set(_external_ids(run)) <= set(q3["expected_episodes"])
    assert run.groups


def test_episode_search_never_returns_passage_points(atlas) -> None:
    candidates = atlas.vector_index.search(
        query_vector=atlas.text_encoder.embed_query("mochila"),
        space=RepresentationSpace.NOMIC_TEXT,
        limit=100,
    )
    assert candidates and all(item.payload.get("target_type") != "passage" for item in candidates)
    passages = atlas.vector_index.search(
        query_vector=atlas.text_encoder.embed_query("mochila"),
        space=RepresentationSpace.NOMIC_TEXT,
        filters={"target_type": "passage"},
        limit=100,
    )
    assert len(passages) == 15


def test_reindex_drops_passages_that_no_longer_exist(atlas) -> None:
    episode = next(item for item in atlas.repository.list_episodes(limit=100)[0] if item.external_id == "ATLAS-EP-010")
    observations = atlas.repository.list_observations_for_episode(episode.id)
    keep = passage_from_observation(observations[0], episode)
    removed = atlas.repository.replace_episode_passages(episode.id, [keep])
    assert sorted(removed) == sorted(item.id for item in observations[1:])
    assert atlas.repository.get_passages([keep.id])[keep.id].text == keep.text
    assert atlas.repository.delete_episode_passages(episode.id) == [keep.id]
    assert keep.point_id == passage_point_id(keep.id) == f"ps_{keep.id}"


def test_passage_search_api(atlas) -> None:
    with TestClient(create_api_app(container=atlas, start_watcher=False)) as client:
        response = client.post("/api/passage-search", json={"text_query": "precintos rojos", "per_episode": 2})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["groups"][0]["episode"]["external_id"] == "ATLAS-EP-010"
        first = body["groups"][0]["passages"][0]
        assert {"observation_id", "text", "snippet", "start_time", "scores"} <= set(first)
        assert body["rerank"]["applied"] is True
        assert all(len(group["passages"]) <= 2 for group in body["groups"])

        assert client.post("/api/passage-search", json={"text_query": ""}).status_code == 422


def test_low_relevance_passages_are_discarded_and_counted(atlas) -> None:
    from episodic.application.passage_search import PassageSearchService

    strict = PassageSearchService(
        atlas.repository,
        atlas.vector_index,
        atlas.text_encoder,
        atlas.passage_search.reranker,
        rerank_min_score=1.0,  # el reordenador falso puntúa términos coincidentes
    )
    run = strict.search("sensor humo")
    kept = [hit for group in run.groups for hit in group.passages]
    assert kept and all(hit.scores.rerank >= 1.0 for hit in kept)
    assert run.discarded_low_relevance > 0
    assert run.to_dict()["rerank"]["min_score"] == 1.0


def test_agreement_of_both_paths_keeps_a_passage_below_the_threshold(atlas) -> None:
    # Con los encoders falsos el pasaje de EP-005 es léxico #1 y significado #5.
    run = atlas.passage_search.search("sensor de humo F-102", min_score=100.0, agreement_rank=5)
    first = run.groups[0].passages[0]
    assert run.groups[0].episode.external_id == "ATLAS-EP-005"
    assert (first.scores.lexical_rank, first.scores.dense_rank) == (1, 5)
    assert first.kept_by == "agreement" and first.scores.rerank < 100.0
    assert all(hit.kept_by == "agreement" for group in run.groups for hit in group.passages)

    assert not atlas.passage_search.search("sensor de humo F-102", min_score=100.0, agreement_rank=4).groups
    strict = atlas.passage_search.search("sensor de humo F-102", min_score=100.0, agreement_rank=0)
    assert not strict.groups
    # Lo descartado no se oculta: se devuelve aparte.
    assert strict.discarded_groups[0].episode.external_id == "ATLAS-EP-005"
    assert strict.to_dict()["discarded_groups"] and strict.to_dict()["rerank"]["agreement_rank"] == 0


def test_thresholds_are_visible_and_overridable_per_query(atlas) -> None:
    with TestClient(create_api_app(container=atlas, start_watcher=False)) as client:
        settings = client.get("/api/passage-search/settings").json()
        assert settings["rerank_min_score"] == atlas.passage_search.rerank_min_score
        assert settings["agreement_rank"] == atlas.passage_search.agreement_rank
        assert settings["indexed_passages"] == 15

        query = {"text_query": "sensor humo"}
        default = client.post("/api/passage-search", json=query).json()
        assert default["rerank"]["min_score"] == settings["rerank_min_score"]
        assert default["indexed_passages"] == 15

        no_threshold = client.post("/api/passage-search", json={**query, "rerank_min_score": None}).json()
        assert no_threshold["rerank"]["min_score"] is None
        assert no_threshold["rerank"]["discarded_low_relevance"] == 0

        strict = client.post(
            "/api/passage-search", json={**query, "rerank_min_score": 100, "agreement_rank": 0}
        ).json()
        assert strict["groups"] == [] and strict["discarded_groups"]


def test_time_in_the_question_becomes_a_filter_and_leaves_the_text(atlas) -> None:
    # Los sucesos Atlas son del 1 de enero de 2026.
    found = atlas.passage_search.search("mochila ayer", now=datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc))
    assert found.groups and found.plan.time_expression == "ayer"
    assert found.lexical_query == '"mochil"*' and found.plan.search_text == "mochila"
    assert found.to_dict()["plan"]["interval"]["start"] < "2026-01-01T18:23:35"

    nothing = atlas.passage_search.search("mochila ayer", now=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc))
    assert not nothing.groups and nothing.dense_candidates == 0 and nothing.lexical_candidates == 0

    # Un intervalo explícito manda sobre el de la pregunta.
    explicit = atlas.passage_search.search(
        "mochila ayer",
        HybridSearchFilters(
            interval=Interval(
                start=TimeSpec.from_utc(datetime(2026, 1, 1, tzinfo=timezone.utc)),
                end=TimeSpec.from_utc(datetime(2026, 1, 2, tzinfo=timezone.utc)),
            )
        ),
        now=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert explicit.groups


@pytest.fixture()
def workshop(tmp_path):
    """Un episodio con tres eventos consecutivos, indexados como en una captura."""
    from datetime import timedelta

    from episodic.domain.entities import Episode, Observation, Source
    from episodic.domain.processing import Event
    from episodic.domain.provenance import Asset, Environment
    from episodic.domain.types import EvidenceMediaType, SourceType

    config = EpisodicConfig(data_dir=str(tmp_path / "taller"), watch_config="", query_timezone="UTC")
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    repo = container.repository
    t0 = datetime(2026, 10, 12, 14, 21, 15, tzinfo=timezone.utc)
    repo.save_environment(Environment(id="taller", name="Taller", producer="test"))
    repo.save_source(Source(id="taller.cam", environment_id="taller", name="cam", source_type=SourceType.CAMERA))
    repo.save_asset(Asset(id="asset-1", environment_id="taller", source_id="taller.cam", uri="x://v.mp4",
                          media_type=EvidenceMediaType.VIDEO, content_hash="h1"))
    episode = Episode(id="ep-taller", title="Taller", external_id="taller_1", environment_ids=["taller"],
                      start_time=t0, end_time=t0 + timedelta(seconds=15), source_ids=["taller.cam"])
    repo.save_episode(episode)
    titles = ["Aplicar lubricante", "Girar la pieza con la llave", "Retirar la pieza"]
    for seq, title in enumerate(titles):
        repo.save_observation(Observation(id=f"obs-{seq}", episode_id=episode.id, observation_type="video_event",
                                          description=title, environment_id="taller", source_id="taller.cam", asset_id="asset-1"))
        repo.save_event(Event(id=f"ev-{seq}", event_type="video_segment", description=f"El mecánico decide {title.lower()}.",
                              environment_id="taller", source_id="taller.cam", asset_id="asset-1", sequence_number=seq,
                              title=title, start_offset_seconds=seq * 5.0, end_offset_seconds=seq * 5.0 + 5.0,
                              start_time=t0 + timedelta(seconds=seq * 5), end_time=t0 + timedelta(seconds=seq * 5 + 5),
                              producer="video-event-derivation", model_version="events-v2",
                              metadata={"observation_ids": [f"obs-{seq}"]}))
    episode.observation_ids = [f"obs-{seq}" for seq in range(3)]
    repo.save_episode(episode)
    repo.link_events_to_episode(episode.id, episode.observation_ids)
    container.lifecycle_service.index_episodes(container.indexing_worker, [episode.id])
    yield container
    container.close()


def test_before_after_questions_bring_the_neighbouring_events(workshop) -> None:
    run = workshop.passage_search.search("¿qué pasó después de aplicar lubricante?", rerank=False)
    assert run.plan.intent == "antes_despues" and run.plan.neighbors == 2
    hit = next(hit for group in run.groups for hit in group.passages if hit.passage.event_id == "ev-0")
    assert [item.title for item in hit.neighbors["before"]] == []
    assert [item.title for item in hit.neighbors["after"]] == ["Girar la pieza con la llave", "Retirar la pieza"]
    assert hit.to_dict()["neighbors"]["after"][0]["start_offset_seconds"] == 5.0

    plain = workshop.passage_search.search("girar la pieza", rerank=False)
    middle = next(hit for group in plain.groups for hit in group.passages if hit.passage.event_id == "ev-1")
    assert middle.neighbors is None  # sin intención de vecindad no se cargan

    explicit = workshop.passage_search.search("girar la pieza", rerank=False, neighbors=1)
    middle = next(hit for group in explicit.groups for hit in group.passages if hit.passage.event_id == "ev-1")
    assert [item.id for item in middle.neighbors["before"]] == ["ev-0"]
    assert [item.id for item in middle.neighbors["after"]] == ["ev-2"]

    with TestClient(create_api_app(container=workshop, start_watcher=False)) as client:
        body = client.post("/api/passage-search", json={"text_query": "girar la pieza", "neighbors": 1, "rerank": False}).json()
        assert body["plan"]["intent"] == "libre" and body["plan"]["timezone"] == "UTC"
        hit = next(h for g in body["groups"] for h in g["passages"] if h["event_id"] == "ev-1")
        assert hit["neighbors"]["before"][0]["event_id"] == "ev-0"
        assert client.get("/api/passage-search/settings").json()["timezone"] == "UTC"


def test_empty_passage_index_is_reported(tmp_path) -> None:
    config = EpisodicConfig(data_dir=str(tmp_path / "empty"), watch_config="")
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    try:
        run = container.passage_search.search("tarjeta de crédito")
        assert run.indexed_passages == 0 and not run.groups
    finally:
        container.close()
