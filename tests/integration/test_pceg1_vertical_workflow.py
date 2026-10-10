"""PC-EG1: un archivo real debe poder seguirse hasta Qdrant desde la API."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.examples.phase6_maintenance import persist_phase6_maintenance_scenario
from episodic.interfaces.http.server import create_api_app


def test_managed_asset_reaches_episode_timeline_and_qdrant(tmp_path) -> None:
    config = EpisodicConfig(project_root=tmp_path, data_dir="runtime")
    container = create_app_container(
        config,
        use_fake_encoders=True,
        use_memory_qdrant=True,
    )
    try:
        with TestClient(create_api_app(container=container)) as client:
            response = client.post(
                "/api/workflow/ingest-and-process",
                files={
                    "file": (
                        "registro_pipeta.txt",
                        b"La pipeta rosada permanece sobre la mesa de laboratorio.",
                        "text/plain",
                    )
                },
                data={
                    "environment_id": "laboratorio_01",
                    "source_id": "documentos_01",
                    "captured_at": "2026-10-07T10:00:00+02:00",
                    "window_duration_seconds": "60",
                    "overlap_seconds": "5",
                },
            )

            assert response.status_code == 200, response.text
            payload = response.json()
            workflow = payload["workflow"]
            assert workflow["observation_ids"]
            assert workflow["microepisode_ids"]
            assert workflow["episode_ids"]
            assert workflow["indexed_points"] == 1

            asset_id = workflow["asset_id"]
            asset_view = client.get(f"/api/memory/assets/{asset_id}")
            episode_view = client.get(
                f"/api/memory/episodes/{workflow['episode_ids'][0]}"
            )
            timeline = client.get("/api/memory/timeline")
            original = client.get(f"/api/memory/assets/{asset_id}/file")

        assert asset_view.status_code == 200
        asset_payload = asset_view.json()
        assert asset_payload["asset"]["original_filename"] == "registro_pipeta.txt"
        assert asset_payload["storage"] == {
            "mode": "managed_immutable",
            "available": True,
            "managed_uri": asset_payload["asset"]["metadata"]["managed_uri"],
        }
        managed_path = Path(asset_payload["asset"]["uri"])
        assert managed_path.is_file()
        assert managed_path.is_relative_to(config.media_dir)
        assert original.content == b"La pipeta rosada permanece sobre la mesa de laboratorio."

        detail = episode_view.json()
        assert detail["microepisodes"][0]["observations"][0]["asset_id"] == asset_id
        assert detail["events"] == []
        assert detail["evidence_references"][0]["asset_id"] == asset_id
        assert detail["vector_projection"]["indexed"] is True

        kinds = {item["kind"] for item in timeline.json()["items"]}
        assert {"observation", "microepisode", "episode"} <= kinds
    finally:
        container.close()


def test_fixture_episode_without_microepisodes_remains_inspectable(tmp_path) -> None:
    """El front debe poder abrir episodios heredados aunque aún no estén agrupados."""
    config = EpisodicConfig(project_root=tmp_path, data_dir="runtime")
    container = create_app_container(
        config,
        use_fake_encoders=True,
        use_memory_qdrant=True,
    )
    try:
        scenario = persist_phase6_maintenance_scenario(container.repository)
        episode = scenario.episodes[-1]

        with TestClient(create_api_app(container=container)) as client:
            response = client.get(f"/api/memory/episodes/{episode.id}")

        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["episode"]["id"] == episode.id
        assert detail["microepisodes"] == []
        assert len(detail["ungrouped_observations"]) == 1
        assert len(detail["evidence_references"]) == 2
        assert len(detail["assets"]) == 2
    finally:
        container.close()
