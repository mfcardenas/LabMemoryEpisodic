"""Paridad esencial del backend Qdrant Edge persistente."""

import json
from pathlib import Path

import pytest

from episodic.application.temporal_index import absolute_interval_filter
from episodic.adapters.qdrant_edge.index import QdrantEdgeVectorIndex
from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.domain.times import Interval
from episodic.domain.types import RepresentationSpace


class FailingTextEncoder:
    def embed_documents(self, texts):
        raise RuntimeError("fallo Edge inyectado")

    def embed_query(self, text):
        raise RuntimeError("fallo Edge inyectado")


def _index_all(container) -> None:
    while container.indexing_worker.process_pending_outbox(batch_size=50):
        pass


def test_edge_reports_exclusive_shard_ownership(tmp_path: Path) -> None:
    first = QdrantEdgeVectorIndex(
        root_dir=tmp_path,
        collection_name="exclusive_owner",
        text_dim=3,
        image_dim=2,
    )
    try:
        with pytest.raises(RuntimeError, match="ya está abierto por otro proceso"):
            QdrantEdgeVectorIndex(
                root_dir=tmp_path,
                collection_name="exclusive_owner",
                text_dim=3,
                image_dim=2,
            )
    finally:
        first.close()


def test_edge_persists_timeline_rebuilds_and_reopens(tmp_path: Path) -> None:
    config = EpisodicConfig(
        data_dir=str(tmp_path / "edge_data"),
        vector_backend="edge",
        qdrant_collection="edge_e2e",
    )
    container = create_app_container(config, use_fake_encoders=True)
    try:
        container.ingest_service.ingest_manifest(Path("data/atlas/manifest.json"))
        _index_all(container)
        assert container.vector_index.count() == 20

        queries = json.loads(Path("data/atlas/queries.json").read_text(encoding="utf-8"))
        q3 = next(query for query in queries if query["id"] == "q3_simultaneous_events")
        candidates = container.vector_index.search(
            query_vector=container.text_encoder.embed_query("sucesos simultáneos"),
            space=RepresentationSpace.NOMIC_TEXT,
            filters=absolute_interval_filter(Interval.from_dict(q3["interval"])),
            limit=20,
        )
        assert {candidate.payload["external_id"] for candidate in candidates} == {
            "ATLAS-EP-006",
            "ATLAS-EP-007",
        }

        generation_before = container.vector_index.generation_name
        rebuilt = container.lifecycle_service.rebuild_index(container.indexing_worker)
        generation_after = container.vector_index.generation_name
        assert rebuilt == 20
        assert generation_after != generation_before
        assert container.vector_index.count() == 20

        container.indexing_worker.text_encoder = FailingTextEncoder()
        with pytest.raises(RuntimeError, match="Falló la representación"):
            container.lifecycle_service.rebuild_index(container.indexing_worker)
        assert container.vector_index.generation_name == generation_after
        assert container.vector_index.count() == 20
        assert {
            path.name for path in container.vector_index.generations_dir.iterdir()
        } == {generation_after}
    finally:
        container.close()

    reopened = create_app_container(config, use_fake_encoders=True)
    try:
        assert reopened.vector_index.count() == 20
        assert reopened.vector_index.generation_name == generation_after
    finally:
        reopened.close()
