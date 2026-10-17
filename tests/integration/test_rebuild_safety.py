"""Garantías de seguridad de la reconstrucción vectorial."""

from pathlib import Path

import pytest

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig


class FailingTextEncoder:
    def embed_documents(self, texts):
        raise RuntimeError("fallo de encoder inyectado")

    def embed_query(self, text):
        raise RuntimeError("fallo de encoder inyectado")


def test_failed_rebuild_preserves_previous_index(tmp_path: Path) -> None:
    config = EpisodicConfig(
        data_dir=str(tmp_path / "rebuild_data"),
        qdrant_collection="rebuild_safety_col",
    )
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    try:
        container.ingest_service.ingest_manifest(Path("data/atlas/manifest.json"))
        while container.indexing_worker.process_pending_outbox(batch_size=50):
            pass

        points_before = container.vector_index.count()
        assert points_before == 35  # 20 + 15 pasajes
        aliases_before = {
            alias.alias_name: alias.collection_name
            for alias in container.vector_index.client.get_aliases().aliases
        }
        active_before = aliases_before[config.qdrant_collection]

        container.indexing_worker.text_encoder = FailingTextEncoder()
        with pytest.raises(RuntimeError, match="Falló la representación"):
            container.lifecycle_service.rebuild_index(container.indexing_worker)

        assert container.vector_index.count() == points_before
        aliases_after = {
            alias.alias_name: alias.collection_name
            for alias in container.vector_index.client.get_aliases().aliases
        }
        assert aliases_after[config.qdrant_collection] == active_before
        collections = {
            collection.name
            for collection in container.vector_index.client.get_collections().collections
        }
        assert not any("__staging_" in name for name in collections)
    finally:
        container.close()


def test_successful_rebuild_switches_verified_generation(tmp_path: Path) -> None:
    config = EpisodicConfig(
        data_dir=str(tmp_path / "rebuild_success_data"),
        qdrant_collection="rebuild_success_col",
    )
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    try:
        container.ingest_service.ingest_manifest(Path("data/atlas/manifest.json"))
        while container.indexing_worker.process_pending_outbox(batch_size=50):
            pass

        active_before = next(
            alias.collection_name
            for alias in container.vector_index.client.get_aliases().aliases
            if alias.alias_name == config.qdrant_collection
        )

        rebuilt = container.lifecycle_service.rebuild_index(container.indexing_worker)

        active_after = next(
            alias.collection_name
            for alias in container.vector_index.client.get_aliases().aliases
            if alias.alias_name == config.qdrant_collection
        )
        collections = {
            collection.name
            for collection in container.vector_index.client.get_collections().collections
        }
        assert rebuilt == 30  # 20 representaciones + 10 conjuntos de pasajes
        assert active_after != active_before
        assert active_after in collections
        assert active_before not in collections
        assert container.vector_index.count() == 35
    finally:
        container.close()
