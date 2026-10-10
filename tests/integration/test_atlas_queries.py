"""Coherencia entre el corpus Atlas y sus consultas de referencia."""

import json
from pathlib import Path

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.domain.times import Interval
from episodic.domain.types import SearchIntent


def test_query_corpus_includes_positive_negative_and_ambiguous_cases() -> None:
    queries = json.loads(Path("data/atlas/queries.json").read_text(encoding="utf-8"))
    verdicts = {query["expected_verdict"] for query in queries}

    assert {"supported", "no_match", "ambiguous"} <= verdicts


def test_simultaneous_events_query_matches_declared_episodes(tmp_path: Path) -> None:
    queries = json.loads(Path("data/atlas/queries.json").read_text(encoding="utf-8"))
    query = next(item for item in queries if item["id"] == "q3_simultaneous_events")
    interval = Interval.from_dict(query["interval"])
    config = EpisodicConfig(data_dir=str(tmp_path / "atlas_query"))
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    try:
        container.ingest_service.ingest_manifest(Path("data/atlas/manifest.json"))
        run = container.retrieval_service.search(
            intent=SearchIntent.INTERVAL_SEARCH,
            interval_filter=interval,
            limit=20,
        )
        actual = {
            container.repository.get_episode(result.episode_id).external_id
            for result in run.results
        }

        assert actual == set(query["expected_episodes"])
    finally:
        container.close()
