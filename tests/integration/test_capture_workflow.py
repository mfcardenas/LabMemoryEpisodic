"""Fase B: una subida conjunta es una captura y produce exactamente un episodio."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.interfaces.http.server import create_api_app

SAMPLES = Path("data_samples/phase4_input")
VIDEO = SAMPLES / "13653860_1080_1920_30fps.mp4"
AUDIO = SAMPLES / "conversacion_mecanico_15s_exact.mp3"
MANUAL = b"Manual: aplicar lubricante penetrante y esperar diez minutos antes de girar."


@pytest.fixture
def app(tmp_path):
    config = EpisodicConfig(project_root=tmp_path, data_dir="runtime", vision_backend="fake")
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    try:
        with TestClient(create_api_app(container=container)) as client:
            yield client, container
    finally:
        container.close()


def _upload(client, files, **data):
    payload = {"environment_id": "taller_01", **data}
    return client.post("/api/captures", files=files, data=payload)


def _capture_files(manual_name: str = "manual.txt"):
    return [
        ("files", (VIDEO.name, VIDEO.read_bytes(), "video/mp4")),
        ("files", (AUDIO.name, AUDIO.read_bytes(), "audio/mpeg")),
        ("files", (manual_name, MANUAL, "text/plain")),
    ]


def test_joint_upload_becomes_one_episode_with_all_assets(app) -> None:
    client, container = app

    response = _upload(
        client,
        _capture_files(),
        title="Desmontaje de pieza agarrotada",
        captured_at="2026-10-10T09:00:00+02:00",
        window_duration_seconds="2",
        overlap_seconds="0",
    )

    assert response.status_code == 202, response.text
    created = response.json()
    assert created["status"] == "received"
    assert created["captured_at_origin"] == "declared"
    assert len(created["assets"]) == 3
    job = container.job_runner.wait(created["job"]["id"], timeout=60)
    assert job.state.value == "completed", job.error

    job_view = client.get(f"/api/jobs/{job.id}").json()
    assert job_view["progress"] == 1.0
    assert [stage["state"] for stage in job_view["stages"]] == ["completed"] * 5

    capture = client.get(f"/api/captures/{created['id']}").json()
    assert capture["status"] == "ready"
    episode_id = capture["episode_id"]

    detail = client.get(f"/api/memory/episodes/{episode_id}").json()
    assert detail["episode"]["title"] == "Desmontaje de pieza agarrotada"
    assert detail["episode"]["metadata"]["capture_id"] == created["id"]
    assert {asset["id"] for asset in detail["assets"]} == {
        asset["id"] for asset in created["assets"]
    }
    assert detail["vector_projection"]["indexed"] is True

    # El vídeo produce eventos con intervalo relativo, fotogramas y procedencia.
    events = detail["events"]
    assert events, "el vídeo debe generar al menos un evento"
    first = events[0]
    assert first["start_offset_seconds"] == 0.0
    assert first["end_offset_seconds"] > first["start_offset_seconds"]
    assert first["title"] and first["summary"]
    assert first["metadata"]["generation"]["vision_model"] == "fake-vision"
    keyframe = client.get(f"/api/memory/events/{first['id']}/keyframes/0")
    assert keyframe.status_code == 200
    assert keyframe.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/memory/events/{first['id']}/keyframes/99").status_code == 404
    # Vídeo y audio comparten la hora declarada: quedan alineados.
    assert datetime.fromisoformat(detail["episode"]["start_time"]) == datetime(
        2026, 10, 10, 7, 0, tzinfo=timezone.utc
    )


def test_new_capture_does_not_alter_previous_episode(app) -> None:
    client, container = app
    first = _upload(client, _capture_files(), title="Primera").json()
    container.job_runner.wait(first["job"]["id"], timeout=60)
    first_ready = client.get(f"/api/captures/{first['id']}").json()
    first_detail = client.get(f"/api/memory/episodes/{first_ready['episode_id']}").json()

    second = _upload(
        client,
        [("files", ("nota.txt", b"Segunda captura independiente.", "text/plain"))],
        title="Segunda",
    ).json()
    container.job_runner.wait(second["job"]["id"], timeout=60)
    second_ready = client.get(f"/api/captures/{second['id']}").json()

    assert second_ready["episode_id"] != first_ready["episode_id"]
    after = client.get(f"/api/memory/episodes/{first_ready['episode_id']}").json()
    assert after["episode"]["observation_ids"] == first_detail["episode"]["observation_ids"]
    assert second_ready["captured_at_origin"] == "upload_time"


def test_file_already_in_a_capture_is_rejected(app) -> None:
    client, container = app
    first = _upload(client, _capture_files(), title="Primera").json()
    container.job_runner.wait(first["job"]["id"], timeout=60)

    repeated = _upload(
        client, [("files", (AUDIO.name, AUDIO.read_bytes(), "audio/mpeg"))]
    )

    assert repeated.status_code == 409
    assert first["id"] in repeated.json()["detail"]


def test_unknown_job_and_capture_return_404(app) -> None:
    client, _container = app
    assert client.get("/api/jobs/no-existe").status_code == 404
    assert client.get("/api/captures/no-existe").status_code == 404
