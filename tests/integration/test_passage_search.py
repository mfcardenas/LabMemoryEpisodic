"""Búsqueda por pasajes sobre el corpus Atlas: el momento exacto, por dos vías."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.domain.passages import passage_point_id
from episodic.domain.retrieval import HybridSearchFilters
from episodic.domain.times import Interval
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
    keep = observations[0]
    removed = atlas.repository.replace_episode_passages(
        episode.id, [(keep.id, keep.observation_type, "", keep.content)]
    )
    assert sorted(removed) == sorted(item.id for item in observations[1:])
    assert atlas.repository.delete_episode_passages(episode.id) == [keep.id]
    assert passage_point_id(keep.id) == f"ps_{keep.id}"


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


def test_empty_passage_index_is_reported(tmp_path) -> None:
    config = EpisodicConfig(data_dir=str(tmp_path / "empty"), watch_config="")
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    try:
        run = container.passage_search.search("tarjeta de crédito")
        assert run.indexed_passages == 0 and not run.groups
    finally:
        container.close()
