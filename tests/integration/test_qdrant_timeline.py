"""La línea temporal forma parte de cada chunk vectorial, no solo de SQLite."""

import json
from pathlib import Path

from qdrant_client import models

from episodic.application.temporal_index import absolute_interval_filter
from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.domain.times import Interval
from episodic.domain.types import RepresentationSpace


def _payload_for(container, field: str, value: str) -> dict:
    points, _ = container.vector_index.client.scroll(
        collection_name=container.config.qdrant_collection,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key=field,
                    match=models.MatchValue(value=value),
                )
            ]
        ),
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    assert len(points) == 1
    return points[0].payload


def test_chunks_store_timeline_and_qdrant_filters_before_top_k(tmp_path: Path) -> None:
    config = EpisodicConfig(
        data_dir=str(tmp_path / "timeline_data"),
        qdrant_collection="timeline_payload_col",
    )
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    try:
        container.ingest_service.ingest_manifest(Path("data/atlas/manifest.json"))
        while container.indexing_worker.process_pending_outbox(batch_size=50):
            pass

        episode_payload = _payload_for(container, "external_id", "ATLAS-EP-006")
        assert episode_payload["timeline_kind"] == "interval"
        assert episode_payload["timeline_unknown"] is False
        assert episode_payload["timeline_start"] == "2026-01-01T18:23:18+00:00"
        assert episode_payload["timeline_end"] == "2026-01-01T18:25:06+00:00"
        assert episode_payload["timeline_filter_end_ms"] == episode_payload["timeline_end_ms"]

        evidence_payload = _payload_for(container, "evidence_id", "ev-006-1")
        assert evidence_payload["timeline_kind"] == "interval"
        assert evidence_payload["timeline_origin"] == "episode_interval"
        assert evidence_payload["episode_ids"] == ["ep-atlas-006"]
        assert evidence_payload["episode_timelines"][0]["timeline_start"] == (
            "2026-01-01T18:23:18+00:00"
        )

        queries = json.loads(Path("data/atlas/queries.json").read_text(encoding="utf-8"))
        query = next(item for item in queries if item["id"] == "q3_simultaneous_events")
        interval = Interval.from_dict(query["interval"])
        candidates = container.vector_index.search(
            query_vector=container.text_encoder.embed_query("sucesos simultáneos"),
            space=RepresentationSpace.NOMIC_TEXT,
            filters=absolute_interval_filter(interval),
            limit=20,
        )
        assert {candidate.payload["external_id"] for candidate in candidates} == {
            "ATLAS-EP-006",
            "ATLAS-EP-007",
        }
    finally:
        container.close()
