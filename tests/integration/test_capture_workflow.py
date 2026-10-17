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
    assert [stage["state"] for stage in job_view["stages"]] == ["completed"] * 6

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
    # Los eventos pertenecen al episodio y son la unidad de búsqueda: la
    # consulta devuelve el evento, con su fotograma y su posición en el vídeo.
    assert all(event["episode_id"] == episode_id for event in events)
    found = client.post("/api/passage-search", json={"text_query": first["title"], "rerank": False}).json()
    assert found["groups"][0]["episode"]["id"] == episode_id
    hits = {hit["event_id"]: hit for hit in found["groups"][0]["passages"]}
    assert first["id"] in hits and hits[first["id"]]["kind"] == "video_event"
    assert hits[first["id"]]["keyframes"] >= 1 and hits[first["id"]]["start_offset_seconds"] == 0.0
    assert hits[first["id"]]["observation_id"] == first["metadata"]["observation_ids"][0]
    # Vídeo y audio comparten la hora declarada: quedan alineados.
    assert datetime.fromisoformat(detail["episode"]["start_time"]) == datetime(
        2026, 10, 10, 7, 0, tzinfo=timezone.utc
    )

    # El episodio llega resumido (modelo de texto falso), con citas a eventos
    # reales, indexado como pasaje, y se puede regenerar como trabajo visible.
    summary = detail["summary"]
    assert summary and summary["revision"] == 1 and summary["model_id"] == "fake-text"
    assert summary["highlights"][0]["event_sequence"] == first["sequence_number"]
    assert detail["summary_revisions"] == 1
    # El resumen solo entra en la búsqueda si se pide: por defecto manda el momento exacto.
    query = {"text_query": "resumen simulado", "rerank": False}
    found = client.post("/api/passage-search", json={**query, "include_summaries": True}).json()
    kinds = {hit["kind"]: hit for group in found["groups"] for hit in group["passages"]}
    assert kinds["episode_summary"]["passage_id"] == summary["id"]
    without = client.post("/api/passage-search", json=query).json()
    assert all(hit["kind"] != "episode_summary" for group in without["groups"] for hit in group["passages"])

    listing = client.get("/api/summaries", params={"scope": "episode", "key": episode_id}).json()
    assert listing["enabled"] is True and listing["current"]["id"] == summary["id"]
    regenerated = client.post("/api/summaries/regenerate", json={"scope": "episode", "key": episode_id})
    assert regenerated.status_code == 202, regenerated.text
    job = container.job_runner.wait(regenerated.json()["id"], timeout=60)
    assert job.state.value == "completed", job.error
    assert client.get(f"/api/memory/episodes/{episode_id}").json()["summary"]["revision"] == 2
    assert client.post("/api/summaries/regenerate", json={"scope": "episode", "key": "nope"}).status_code == 404

    # Tras la captura se encoló el resumen del día y del entorno.
    digests = [item for item in client.get("/api/jobs").json()["jobs"] if item["job_type"] == "digest"]
    assert digests, "la captura debe encolar el resumen del día"
    container.job_runner.wait(digests[0]["id"], timeout=60)
    days = client.get("/api/summaries", params={"scope": "day", "environment_id": "taller_01"}).json()["items"]
    assert [item["scope_key"] for item in days] == ["taller_01|2026-10-10"]
    assert client.get("/api/summaries", params={"scope": "environment", "key": "taller_01"}).json()["current"]


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


def _observations(detail) -> list[dict]:
    return list(
        {
            item["id"]: item
            for item in [
                *(obs for micro in detail["microepisodes"] for obs in micro["observations"]),
                *detail["ungrouped_observations"],
            ]
        }.values()
    )


def test_speech_of_separate_audio_is_attached_to_video_events(app) -> None:
    client, container = app
    created = _upload(
        client,
        _capture_files(),
        title="Con habla",
        captured_at="2026-10-10T09:00:00+02:00",
    ).json()
    job = container.job_runner.wait(created["job"]["id"], timeout=60)
    assert job.state.value == "completed", job.error
    detail = client.get(
        f"/api/memory/episodes/{client.get(f'/api/captures/{created['id']}').json()['episode_id']}"
    ).json()

    speech = [
        item for item in _observations(detail) if item["observation_type"] == "speech_segment"
    ]
    # Transcriptor simulado: un segmento cada 4 s de los 15 s del MP3.
    assert len(speech) == 4
    assert {item["provenance_kind"] for item in speech} == {"audio_transcription"}
    assert speech[0]["attributes"]["generation"]["speech_model"] == "fake-speech"
    # El vídeo dura 5,4 s: solo el habla que se solapa con él se asigna a eventos.
    attached = [line for event in detail["events"] for line in event["attributes"].get("speech", [])]
    assert [line["start_offset_seconds"] for line in attached] == [0.0, 4.0]
    assert all(line["text"].startswith("Habla simulada") for line in attached)


def test_audio_offset_is_stored_and_applied(app) -> None:
    client, container = app
    created = _upload(
        client,
        _capture_files(),
        captured_at="2026-10-10T09:00:00+02:00",
        audio_offset_seconds="-4",
    ).json()
    container.job_runner.wait(created["job"]["id"], timeout=60)
    capture = container.repository.get_capture(created["id"])
    assert capture.metadata["processing"]["audio_offset_seconds"] == -4.0

    detail = client.get(f"/api/memory/episodes/{capture.episode_id}").json()
    attached = [line for event in detail["events"] for line in event["attributes"].get("speech", [])]
    # Con -4 s, el segmento 4–8 s del audio cae en 0–4 s del vídeo y el 8–12 s en 4–8 s.
    assert [line["start_offset_seconds"] for line in attached] == [0.0, 4.0]
    assert attached[0]["text"] == "Habla simulada del segundo 4.0 al 8.0."


def test_capture_survives_unreachable_speech_service(tmp_path) -> None:
    config = EpisodicConfig(
        project_root=tmp_path,
        data_dir="runtime",
        vision_backend="fake",
        speech_backend="http",
        speech_url="http://127.0.0.1:9",
        speech_timeout_seconds=2,
    )
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    try:
        with TestClient(create_api_app(container=container)) as client:
            created = _upload(client, _capture_files(), title="Sin servicio de habla").json()
            job = container.job_runner.wait(created["job"]["id"], timeout=60)
    finally:
        container.close()

    assert job.state.value == "completed", job.error
    observations = next(stage for stage in job.stages if stage.name == "observations")
    assert "1 fallo(s)" in observations.detail
    assert job.result["processor_failures"]
    assert "no responde" in job.result["processor_failures"][0]
