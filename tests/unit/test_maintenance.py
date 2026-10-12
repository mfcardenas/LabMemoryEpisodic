"""Limpieza de datos para repetir cargas: solo lo de EpiGraph."""
import socket

import pytest
from qdrant_client import QdrantClient, models

from episodic.adapters.qdrant.index import QdrantVectorIndex
from episodic.config import EpisodicConfig
from episodic.maintenance import ResetError, apply_reset, plan_reset


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _config(tmp_path, port: int) -> EpisodicConfig:
    return EpisodicConfig(
        project_root=tmp_path,
        data_dir="datos",
        vector_backend="http",
        qdrant_collection="episodic_memory",
        port=port,
        watch_config="",
    )


def test_reset_removes_only_epigraph_data(tmp_path) -> None:
    config = _config(tmp_path, _free_port())
    config.ensure_dirs()
    config.sqlite_path.write_bytes(b"sqlite")
    (config.media_dir / "ab").mkdir()
    (config.media_dir / "ab" / "original.mp4").write_bytes(b"video")
    keep = config.data_path / "logs"
    keep.mkdir()

    client = QdrantClient(":memory:")
    index = QdrantVectorIndex(client=client, collection_name="episodic_memory", text_dim=4, image_dim=4)
    index.drop_and_recreate()  # deja una generación nueva activa y otra antigua sin alias
    client.create_collection("episodic_memory__staging_abc", vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE))
    client.create_collection("otro_proyecto", vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE))
    client.create_collection("episodic_memory_extra", vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE))

    plan, _ = plan_reset(config, client=client)
    assert config.sqlite_path in plan.local_paths and config.media_dir in plan.local_paths
    assert plan.qdrant_alias is True
    assert all(name.startswith("episodic_memory__") for name in plan.qdrant_collections)

    apply_reset(plan, config, client=client, log=lambda _message: None)

    assert not config.sqlite_path.exists() and not config.media_dir.exists()
    assert keep.exists()
    assert {c.name for c in client.get_collections().collections} == {"otro_proyecto", "episodic_memory_extra"}
    assert client.get_aliases().aliases == []
    plan, _ = plan_reset(config, client=client)
    assert plan.empty


def test_reset_refuses_while_epigraph_is_running(tmp_path) -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        config = _config(tmp_path, server.getsockname()[1])
        with pytest.raises(ResetError, match="sigue en marcha"):
            plan_reset(config, client=QdrantClient(":memory:"))
