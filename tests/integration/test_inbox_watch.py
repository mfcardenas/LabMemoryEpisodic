"""Entrada automática: las carpetas vigiladas se convierten en capturas y episodios.

El reloj del vigilante se inyecta: las esperas de estabilidad y de cierre se
simulan sin dormir.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from episodic.application.watch_config import parse_watch_settings
from episodic.bootstrap import create_app_container, create_inbox_watcher
from episodic.config import EpisodicConfig
from episodic.domain.captures import JobState
from episodic.domain.watch import InboxFileState
from episodic.interfaces.http.server import create_api_app

SAMPLES = Path("data_samples/phase4_input")
VIDEO = SAMPLES / "13653860_1080_1920_30fps.mp4"
AUDIO = SAMPLES / "conversacion_mecanico_15s_exact.mp3"
DOCX = SAMPLES / "Manual_taller_desmontaje_pieza_agarrotada.docx"
SETTLE = 5.0
CLOSE_AFTER = 20.0


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def env(tmp_path):
    config = EpisodicConfig(
        project_root=tmp_path, data_dir="runtime", vision_backend="fake", speech_backend="fake"
    )
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    clock = Clock()
    watchers = []

    def make(owner: str = "prueba", **source):
        base = {"name": "taller", "path": str(inbox)}
        if source.get("grouping", "folder") != "manifest":
            base.update(grouping="folder", environment_id="taller_01", close_after_seconds=CLOSE_AFTER)
        settings = parse_watch_settings(
            {
                "watch": {"interval_seconds": 1, "settle_seconds": SETTLE},
                "sources": [{**base, **source}],
            },
            base_dir=tmp_path,
        )
        watcher = create_inbox_watcher(
            config, container.evidence_store, container.job_runner, settings, clock=clock, owner=owner
        )
        watchers.append(watcher)
        return watcher

    yield SimpleNamespace(container=container, inbox=inbox, clock=clock, make=make)
    for watcher in watchers:
        watcher.stop()
        watcher.repository.close()
    container.close()


def _put(folder: Path, name: str, content: bytes | Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if isinstance(content, Path):
        shutil.copyfile(content, target)
    else:
        target.write_bytes(content)
    return target


def _manifest(folder: Path, environment_id: str, captured_at, devices, **extra) -> Path:
    """Escribe el manifiesto como lo haría la herramienta de captura (al final).

    ``devices``: tuplas ``(id, kind, files)`` o ``(id, kind, files, started_at)``.
    """
    folder.mkdir(parents=True, exist_ok=True)
    declared = []
    for device_id, kind, files, *started in devices:
        item = {"id": device_id, "kind": kind, "files": files}
        if started:
            item["started_at"] = started[0]
        declared.append(item)
    payload = {
        "schema": "epigraph.capture/v1",
        "environment_id": environment_id,
        "captured_at": captured_at,
        "devices": declared,
        **extra,
    }
    path = folder / "capture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _until_closed(env, watcher):
    """Detección → estable → grupo cerrado, avanzando el reloj."""
    reports = [watcher.tick()]
    env.clock.advance(SETTLE + 1)
    reports.append(watcher.tick())
    env.clock.advance(CLOSE_AFTER + 1)
    reports.append(watcher.tick())
    return reports


def _files(env):
    return {item.rel_path: item for item in env.container.repository.list_inbox_files("taller")}


def _messages(env, component=None):
    return [entry.message for entry in env.container.repository.list_activity(limit=1000, component=component)]


def _run_jobs(env, job_ids):
    runner = env.container.job_runner
    runner.start()
    return [runner.wait(job_id, timeout=120) for job_id in job_ids]


def test_each_folder_becomes_one_capture_whatever_it_contains(env) -> None:
    _put(env.inbox / "desmontaje", "camara.mp4", VIDEO)
    _put(env.inbox / "desmontaje", "manual.txt", b"Aflojar la tuerca con llave del 17.")
    _put(env.inbox / "solo_audio", "conversacion.mp3", AUDIO)
    watcher = env.make()

    first, second, third = _until_closed(env, watcher)

    assert first.detected == 3 and not first.capture_ids
    assert second.ready == 3 and second.open_groups == 2 and not second.capture_ids
    assert len(third.capture_ids) == 2

    jobs = _run_jobs(env, third.job_ids)
    assert [job.state for job in jobs] == [JobState.COMPLETED, JobState.COMPLETED]

    captures = {
        capture.title: capture
        for capture in env.container.repository.list_captures()
    }
    assert set(captures) == {"desmontaje", "solo_audio"}
    mixed, audio_only = captures["desmontaje"], captures["solo_audio"]
    assert len(mixed.asset_ids) == 2 and len(audio_only.asset_ids) == 1
    assert audio_only.captured_at_origin.value == "file_time"
    assert audio_only.metadata["origin"] == {
        "kind": "watch",
        "source": "taller",
        "group": "solo_audio",
        "root": str(env.inbox.resolve()),
        "files": ["solo_audio/conversacion.mp3"],
    }
    assert mixed.episode_id is not None and audio_only.episode_id is not None

    files = _files(env)
    assert {item.state for item in files.values()} == {InboxFileState.CAPTURED}
    assert files["solo_audio/conversacion.mp3"].capture_id == audio_only.id

    watch_log = _messages(env, "watch")
    assert any(message.startswith("Detectado: desmontaje/camara.mp4") for message in watch_log)
    assert any(message.startswith("Completo: solo_audio/conversacion.mp3 · audio") for message in watch_log)
    assert any("Captura creada: «desmontaje» con 2 fichero(s) (text, video)" in m for m in watch_log)
    jobs_log = _messages(env, "jobs")
    assert any(message.startswith("Observaciones · en curso: camara.mp4") for message in jobs_log)
    assert "Completado: captura «solo_audio»" in jobs_log

    # Cada pase deja su línea, con lo que ha cambiado y lo que espera.
    passes = [
        entry
        for entry in env.container.repository.list_activity(limit=1000, component="watch")
        if entry.level.value == "debug"
    ]
    assert [entry.message for entry in passes] == [
        "Pase 1 · taller: 3 fichero(s) en carpeta · 3 nuevo(s) · 3 escribiéndose",
        "Pase 2 · taller: 3 fichero(s) en carpeta · 3 completo(s) · 3 esperando a cerrar el grupo",
        "Pase 3 · taller: 3 fichero(s) en carpeta · 2 captura(s) creada(s)",
    ]
    assert passes[0].data == {
        "pass": 1,
        "summary": "taller: 3 fichero(s) en carpeta · 3 nuevo(s) · 3 escribiéndose",
        "idle": False,
    }

    # Y los pasos internos del trabajo: cada tramo del vídeo, la transcripción
    # y la indexación, bajo la etapa y el fichero en curso.
    steps = [
        entry.message
        for entry in env.container.repository.list_activity(limit=1000, component="jobs")
        if entry.data.get("step")
    ]
    assert (
        "Observaciones · camara.mp4 (1/2) · descripción visual por tramos"
        " · tramo 1/1 (0.0–5.4 s): describiendo 3 fotograma(s) clave con fake-vision"
    ) in steps
    assert (
        "Observaciones · conversacion.mp3 (1/1) · transcripción del habla"
        " · transcribiendo el audio con fake-speech"
    ) in steps
    assert "Indexación · codificando el texto del episodio con fake-nomic-text" in steps

    # Un nuevo barrido (o un reinicio) no vuelve a capturar nada.
    restarted = env.make(owner="otro-arranque")
    watcher.stop()
    env.clock.advance(60)
    assert restarted.tick().capture_ids == []
    assert len(env.container.repository.list_captures()) == 2
    last = env.container.repository.list_activity(limit=1, component="watch")[0]
    assert last.message == "Pase 1 · taller: 3 fichero(s) en carpeta · sin novedades"
    assert last.data["idle"] is True


def test_file_still_being_copied_waits_until_it_stops_changing(env) -> None:
    target = _put(env.inbox / "grabacion", "notas.txt", b"primera parte")
    watcher = env.make()
    watcher.tick()
    env.clock.advance(SETTLE + 1)
    with target.open("ab") as handle:
        handle.write(b" y segunda parte")
    watcher.tick()
    assert _files(env)["grabacion/notas.txt"].state == InboxFileState.PENDING

    env.clock.advance(SETTLE + 1)
    watcher.tick()
    row = _files(env)["grabacion/notas.txt"]
    assert row.state == InboxFileState.READY
    assert row.size_bytes == len(b"primera parte y segunda parte")

    # El grupo se cierra contando desde el último cambio, no desde la detección.
    env.clock.advance(CLOSE_AFTER - SETTLE - 2)
    assert watcher.tick().capture_ids == []
    env.clock.advance(SETTLE + 2)
    assert len(watcher.tick().capture_ids) == 1


def test_duplicates_and_unsupported_files_do_not_block_the_rest(env) -> None:
    _put(env.inbox / "primera", "notas.txt", b"contenido repetido")
    watcher = env.make()
    _until_closed(env, watcher)

    _put(env.inbox / "segunda", "copia.txt", b"contenido repetido")
    _put(env.inbox / "segunda", "manual.docx", DOCX)
    _put(env.inbox / "segunda", "nuevo.txt", b"contenido distinto")
    _put(env.inbox / "segunda", "borrador.tmp", b"temporal del editor")
    *_, closing = _until_closed(env, watcher)

    files = _files(env)
    assert files["segunda/copia.txt"].state == InboxFileState.DUPLICATE
    assert files["segunda/manual.docx"].state == InboxFileState.IGNORED
    assert "no soportado" in files["segunda/manual.docx"].error
    assert "segunda/borrador.tmp" not in files
    assert len(closing.capture_ids) == 1
    capture = env.container.repository.get_capture(closing.capture_ids[0])
    assert capture.metadata["origin"]["files"] == ["segunda/nuevo.txt"]


def test_ready_marker_closes_the_folder_instead_of_the_quiet_period(env) -> None:
    folder = env.inbox / "con_marcador"
    _put(folder, "notas.txt", b"datos")
    watcher = env.make(ready_marker="LISTO")
    _until_closed(env, watcher)
    env.clock.advance(3600)
    assert watcher.tick().capture_ids == []

    _put(folder, "LISTO", b"")
    report = watcher.tick()
    assert len(report.capture_ids) == 1
    capture = env.container.repository.get_capture(report.capture_ids[0])
    assert capture.metadata["origin"]["files"] == ["con_marcador/notas.txt"]


def test_failed_group_can_be_retried_from_the_api(env) -> None:
    _put(env.inbox / "turno_manana", "notas.txt", b"datos del turno")
    watcher = env.make()
    original = watcher.capture_service.register

    def broken(*_args, **_kwargs):
        raise ValueError("disco lleno")

    watcher.capture_service.register = broken
    *_, closing = _until_closed(env, watcher)
    assert closing.capture_ids == []
    row = _files(env)["turno_manana/notas.txt"]
    assert row.state == InboxFileState.FAILED and row.error == "disco lleno"
    assert any("No se pudo crear la captura del grupo turno_manana" in m for m in _messages(env))

    watcher.capture_service.register = original
    client = TestClient(create_api_app(container=env.container, start_watcher=False))
    response = client.post("/api/watch/retry", data={"source": "taller", "group_key": "turno_manana"})
    assert response.json() == {"reset": 1}
    # El reintento no espera de nuevo: el siguiente barrido crea la captura.
    assert len(watcher.tick().capture_ids) == 1


def test_move_archives_the_folder_and_late_files_form_a_new_capture(env) -> None:
    _put(env.inbox / "pieza", "notas.txt", b"desmontaje")
    watcher = env.make(after="move")
    *_, closing = _until_closed(env, watcher)

    archived = env.inbox / "_procesados" / "pieza" / "notas.txt"
    assert archived.is_file() and not (env.inbox / "pieza").exists()
    row = env.container.repository.list_inbox_files("taller")[0]
    assert row.present is False and row.archived_to == str(archived)

    # La carpeta de archivo no se vigila; una carpeta nueva con el mismo
    # nombre es otra captura.
    _put(env.inbox / "pieza", "notas.txt", b"otro desmontaje")
    *_, second = _until_closed(env, watcher)
    assert len(second.capture_ids) == 1
    capture = env.container.repository.get_capture(second.capture_ids[0])
    assert capture.title == "pieza · parte 2"
    assert (env.inbox / "_procesados" / "pieza-2" / "notas.txt").is_file()
    assert closing.capture_ids != second.capture_ids


def test_time_gap_groups_loose_files_by_modification_time(env) -> None:
    now = env.clock.now
    for name, age in (("a.txt", 1000), ("b.txt", 980), ("c.txt", 400)):
        path = _put(env.inbox, name, name.encode())
        os.utime(path, (now - age, now - age))
    watcher = env.make(grouping="time_gap", max_gap_seconds=120)
    watcher.tick()
    env.clock.advance(SETTLE + 1)
    report = watcher.tick()

    assert len(report.capture_ids) == 2
    groups = sorted(
        env.container.repository.get_capture(capture_id).metadata["origin"]["files"]
        for capture_id in report.capture_ids
    )
    assert groups == [["a.txt", "b.txt"], ["c.txt"]]


def test_interrupted_registration_is_recovered_without_duplicating(env) -> None:
    _put(env.inbox / "corte", "notas.txt", b"proceso interrumpido")
    watcher = env.make()
    original = watcher._mark_captured

    def crash(*_args, **_kwargs):
        raise RuntimeError("corte de luz")

    watcher._mark_captured = crash
    _until_closed(env, watcher)
    captures = env.container.repository.list_captures()
    assert len(captures) == 1
    assert _files(env)["corte/notas.txt"].state == InboxFileState.READY

    watcher._mark_captured = original
    report = watcher.tick()
    assert report.capture_ids == []
    assert len(env.container.repository.list_captures()) == 1
    assert len(env.container.repository.list_jobs(target_id=captures[0].id)) == 1
    assert _files(env)["corte/notas.txt"].capture_id == captures[0].id
    assert any("Recuperada la captura del grupo corte" in m for m in _messages(env))


def test_only_one_process_watches_at_a_time(env) -> None:
    _put(env.inbox / "x", "notas.txt", b"datos")
    first = env.make(owner="proceso-a")
    second = env.make(owner="proceso-b")

    assert first.tick() is not None
    assert second.tick() is None
    assert any("Otro proceso vigila ya estas carpetas (proceso-a)" in m for m in _messages(env))

    first.stop()
    assert second.tick() is not None


def test_unavailable_folder_is_reported_and_recovers(env, tmp_path) -> None:
    missing = tmp_path / "unidad_red"
    watcher = env.make(path=str(missing))
    watcher.tick()
    assert watcher.status()["sources"]["taller"]["available"] is False
    missing.mkdir()
    watcher.tick()
    assert watcher.status()["sources"]["taller"]["available"] is True
    log = _messages(env)
    assert any(m.startswith("Carpeta no disponible") for m in log)
    assert any(m.startswith("Carpeta disponible de nuevo") for m in log)


def test_watch_api_exposes_sources_files_jobs_and_log(tmp_path) -> None:
    inbox = tmp_path / "entrada"
    _put(inbox / "captura_1", "notas.txt", b"hola")
    _manifest(inbox / "captura_1", "env_context_01", 1791786600, [("operario", "document", ["notas.txt"])])
    config_file = tmp_path / "watch.toml"
    config_file.write_text(
        "[watch]\ninterval_seconds = 1\nsettle_seconds = 0\n\n"
        "[[sources]]\nname = \"taller\"\npath = \"entrada\"\n",
        encoding="utf-8",
    )
    config = EpisodicConfig(
        project_root=tmp_path,
        data_dir="runtime",
        vision_backend="fake",
        speech_backend="fake",
        watch_config="watch.toml",
    )
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    try:
        with TestClient(create_api_app(container=container, start_watcher=False)) as client:
            status = client.get("/api/watch").json()
            assert status["configured"] is True and status["running"] is False
            assert status["sources"][0]["path"] == str(inbox.resolve())
            assert client.post("/api/watch/scan").status_code == 409

            container.watcher.tick()
            report = container.watcher.tick()
            job = container.job_runner.wait(report.job_ids[0], timeout=60)
            assert job.state == JobState.COMPLETED

            status = client.get("/api/watch").json()
            assert status["sources"][0]["counts"] == {"captured": 1}
            assert status["lease"]["this_process"] is True

            files = client.get("/api/watch/files", params={"state": "captured"}).json()["files"]
            assert [item["rel_path"] for item in files] == ["captura_1/notas.txt"]

            # La captura encola después el resumen del día; se espera a que termine.
            for pending in container.repository.list_jobs(states=[JobState.QUEUED, JobState.RUNNING]):
                container.job_runner.wait(pending.id, timeout=60)
            jobs = client.get("/api/jobs").json()
            assert jobs["active_count"] == 0
            capture_job = next(item for item in jobs["jobs"] if item["job_type"] == "process_capture")
            assert capture_job["capture"]["origin"]["kind"] == "watch"
            assert capture_job["capture"]["title"] == "env_context_01_1791786600"

            status = client.get("/api/watch").json()
            assert status["pass_count"] == 2

            # Sin los pases del vigilante (que pueden llegar tras el trabajo).
            main_levels = {"level": ["info", "warning", "error"]}
            log = client.get("/api/activity", params={"limit": 40, **main_levels}).json()
            messages = [entry["message"] for entry in log["entries"]]
            assert "Completado: captura «env_context_01_1791786600»" in messages, messages
            assert messages[-1].startswith("Completado: resumen diario"), messages
            newer = client.get("/api/activity", params={"after_id": log["last_id"], **main_levels}).json()
            assert newer["entries"] == [] and newer["last_id"] == log["last_id"]
            passes = client.get("/api/activity", params={"level": "debug"}).json()["entries"]
            assert [entry["data"]["pass"] for entry in passes] == [1, 2]
            only_watch = client.get("/api/activity", params={"component": "watch"}).json()
            assert {entry["component"] for entry in only_watch["entries"]} == {"watch"}
    finally:
        container.close()


# -- Contrato con la herramienta de captura (manifiesto epigraph.capture/v1) --

T0 = 1791786600  # 2026-10-12T06:30:00Z


def _epoch(value: float):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(value, timezone.utc)


def _image(path: Path, color: str) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 24), color).save(path)


def _qdrant_payload(container, external_id: str) -> dict:
    from qdrant_client import models

    points, _ = container.vector_index.client.scroll(
        collection_name=container.config.qdrant_collection,
        scroll_filter=models.Filter(
            must=[models.FieldCondition(key="external_id", match=models.MatchValue(value=external_id))]
        ),
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    assert len(points) == 1
    return points[0].payload


def test_manifest_declares_environment_devices_and_epoch_time(env) -> None:
    from episodic.application.captures import capture_id_for_key
    from episodic.domain.types import SourceType

    # Llega primero la captura posterior del entorno 01: el orden lo da el epoch.
    later = env.inbox / "lote_b"
    _image(later / "norte.png", "red")
    _image(later / "sur.png", "blue")
    _put(later, "notas.txt", b"Cambio de pastillas de freno")
    _manifest(
        later,
        "env_context_01",
        T0 + 3600,
        [
            ("cam_norte", "camera", ["norte.png"]),
            ("cam_sur", "camera", ["sur.png"], T0 + 3601.5),
            ("operario", "document", ["notas.txt"]),
        ],
    )
    earlier = env.inbox / "lote_a"
    _put(earlier, "micro.mp3", AUDIO)
    _put(earlier, "parte.txt", b"Inicio de turno")
    _manifest(
        earlier,
        "env_context_01",
        T0,
        [("micro_1", "microphone", ["micro.mp3"], T0 - 2), ("operario", "document", ["parte.txt"])],
    )
    other = env.inbox / "lote_c"
    _put(other, "notas.txt", b"Otro entorno")
    _manifest(other, "env_context_02", T0 + 60, [("operario", "document", ["notas.txt"])], title="Línea 2")

    watcher = env.make(grouping="manifest")
    watcher.tick()
    env.clock.advance(SETTLE + 1)
    report = watcher.tick()
    # El manifiesto cierra la captura: no hay espera de inactividad.
    assert len(report.capture_ids) == 3
    assert {job.state for job in _run_jobs(env, report.job_ids)} == {JobState.COMPLETED}

    repo = env.container.repository
    captures = {capture.metadata["capture_key"]: capture for capture in repo.list_captures()}
    assert set(captures) == {
        "env_context_01_1791786600",
        "env_context_01_1791790200",
        "env_context_02_1791786660",
    }
    later_capture = captures["env_context_01_1791790200"]
    assert later_capture.id == capture_id_for_key("env_context_01_1791790200")
    assert later_capture.environment_id == "env_context_01"
    assert later_capture.captured_at == _epoch(T0 + 3600)
    assert later_capture.captured_at_origin.value == "declared"
    assert later_capture.metadata["manifest"]["devices"][1]["id"] == "cam_sur"
    assert later_capture.metadata["origin"]["manifest"] == "lote_b/capture.json"

    # Un dispositivo, una fuente; cada fichero con la hora de su dispositivo.
    assets = {
        asset.original_filename: asset
        for asset in (repo.get_asset(asset_id) for asset_id in later_capture.asset_ids)
    }
    assert assets["norte.png"].source_id == "env_context_01.cam_norte"
    assert assets["sur.png"].source_id == "env_context_01.cam_sur"
    assert assets["norte.png"].captured_at == _epoch(T0 + 3600)
    assert assets["sur.png"].captured_at == _epoch(T0 + 3601.5)
    assert repo.get_source("env_context_01.micro_1").source_type == SourceType.MICROPHONE
    assert repo.get_source("env_context_01.cam_sur").source_type == SourceType.CAMERA

    # El episodio lleva el nombre de la captura, también en la colección de Qdrant.
    episodes = {
        repo.get_episode(capture.episode_id).external_id: repo.get_episode(capture.episode_id)
        for capture in captures.values()
    }
    assert set(episodes) == set(captures)
    assert repo.find_episode_by_external_id("env_context_02_1791786660").title == "Línea 2"
    payload = _qdrant_payload(env.container, "env_context_01_1791790200")
    assert payload["episode_id"] == later_capture.episode_id
    assert payload["environment_ids"] == ["env_context_01"]

    # Línea temporal del entorno: ordenada por el epoch declarado, no por llegada.
    timeline = sorted(
        (episode for episode in episodes.values() if episode.environment_ids == ["env_context_01"]),
        key=lambda episode: episode.start_time,
    )
    assert [episode.external_id for episode in timeline] == [
        "env_context_01_1791786600",
        "env_context_01_1791790200",
    ]
    # El micrófono empezó 2 s antes que la captura: el habla se sitúa ahí.
    micro = next(
        repo.get_asset(asset_id)
        for asset_id in captures["env_context_01_1791786600"].asset_ids
        if repo.get_asset(asset_id).source_id == "env_context_01.micro_1"
    )
    speech = [
        item for item in repo.list_observations_for_asset(micro.id)
        if item.observation_type == "speech_segment"
    ]
    assert min(item.start_time for item in speech) == _epoch(T0 - 2)
    assert timeline[0].start_time == _epoch(T0 - 2)

    # La línea temporal del front traza cada episodio a su captura de origen.
    view = env.container.inspection_service.timeline(environment_id="env_context_01")
    episode_items = sorted(
        (item for item in view["items"] if item["kind"] == "episode"),
        key=lambda item: item["start_time"],
    )
    assert [item["external_id"] for item in episode_items] == [
        "env_context_01_1791786600",
        "env_context_01_1791790200",
    ]
    # Un carril por dispositivo declarado.
    lanes = {lane["id"] for lane in view["lanes"]}
    assert {
        "env_context_01.cam_norte",
        "env_context_01.cam_sur",
        "env_context_01.micro_1",
        "env_context_01.operario",
    } <= lanes


def test_manifest_written_before_its_files_waits_and_times_out(env) -> None:
    folder = env.inbox / "copia_lenta"
    _manifest(folder, "env_context_01", T0, [("operario", "document", ["a.txt", "b.txt"])])
    _put(folder, "a.txt", b"primero")
    watcher = env.make(grouping="manifest", missing_files_timeout_seconds=60)
    watcher.tick()
    env.clock.advance(SETTLE + 1)
    assert watcher.tick().capture_ids == []  # falta b.txt: se espera

    _put(folder, "b.txt", b"segundo")
    watcher.tick()
    env.clock.advance(SETTLE + 1)
    report = watcher.tick()
    assert len(report.capture_ids) == 1
    capture = env.container.repository.get_capture(report.capture_ids[0])
    assert sorted(capture.metadata["origin"]["files"]) == ["copia_lenta/a.txt", "copia_lenta/b.txt"]

    # Si un fichero declarado no llega nunca, la captura falla con el motivo…
    stuck = env.inbox / "incompleta"
    _put(stuck, "a.txt", b"solo uno")
    _manifest(stuck, "env_context_01", T0 + 60, [("operario", "document", ["a.txt", "nunca.txt"])])
    watcher.tick()
    env.clock.advance(SETTLE + 1)
    watcher.tick()
    env.clock.advance(61)
    watcher.tick()
    row = _files(env)["incompleta/a.txt"]
    assert row.state == InboxFileState.FAILED
    assert row.error == "faltan ficheros declarados en el manifiesto: incompleta/nunca.txt"

    # …y cuando la herramienta reescribe el manifiesto, se reintenta sola.
    time.sleep(0.02)
    _manifest(stuck, "env_context_01", T0 + 60, [("operario", "document", ["a.txt"])], title="Corregida")
    watcher.tick()
    report = watcher.tick()
    assert len(report.capture_ids) == 1
    assert any("Manifiesto reescrito en incompleta" in m for m in _messages(env))


def test_manifest_contract_violations_are_reported(env) -> None:
    bad_epoch = env.inbox / "milisegundos"
    _put(bad_epoch, "notas.txt", b"a")
    _manifest(bad_epoch, "env_context_01", T0 * 1000, [("operario", "document", ["notas.txt"])])

    wrong_kind = env.inbox / "tipo_erroneo"
    _put(wrong_kind, "voz.mp3", AUDIO)
    _manifest(wrong_kind, "env_context_01", T0 + 10, [("cam_1", "camera", ["voz.mp3"])])

    extra = env.inbox / "con_extra"
    _put(extra, "notas.txt", b"declarado")
    _put(extra, "suelto.txt", b"no declarado")
    _manifest(extra, "env_context_01", T0 + 20, [("operario", "document", ["notas.txt"])])

    repeated = env.inbox / "repetida"
    _put(repeated, "otra.txt", b"misma clave")
    _manifest(repeated, "env_context_01", T0 + 20, [("operario", "document", ["otra.txt"])])

    _put(env.inbox, "raiz.txt", b"fuera de carpeta")

    watcher = env.make(grouping="manifest")
    watcher.tick()
    env.clock.advance(SETTLE + 1)
    report = watcher.tick()

    files = _files(env)
    assert "milisegundos" in files["milisegundos/notas.txt"].error
    assert files["milisegundos/notas.txt"].state == InboxFileState.FAILED
    assert "cam_1 es camera, el fichero es audio" in files["tipo_erroneo/voz.mp3"].error
    assert files["con_extra/suelto.txt"].state == InboxFileState.IGNORED
    assert files["con_extra/suelto.txt"].error == "no declarado en el manifiesto"
    assert files["raiz.txt"].state == InboxFileState.IGNORED
    # Dos carpetas no pueden declarar la misma captura (entorno + epoch).
    keys = {
        env.container.repository.get_capture(capture_id).metadata["capture_key"]
        for capture_id in report.capture_ids
    }
    assert keys == {"env_context_01_1791786620"}
    assert files["repetida/otra.txt"].state == InboxFileState.FAILED
    assert files["repetida/otra.txt"].error == "La captura env_context_01_1791786620 ya existe"


def test_folder_without_manifest_is_reported_and_can_be_drafted_by_hand(env) -> None:
    """Carga manual: la carpeta espera su manifiesto a la vista, y la persona
    puede pedir que se proponga a partir del nombre <entorno>_<epoch>."""
    folder = env.inbox / f"env_context_A015_{T0}"
    _put(folder, "camara.mp4", VIDEO)
    _put(folder, "conversacion.mp3", AUDIO)
    _put(folder, "notas.txt", b"Se revisa el contador.")
    _put(env.inbox / "sin_patron", "notas.txt", b"Otra carga.")
    watcher = env.make(grouping="manifest", manifest_warning_seconds=30)
    watcher.tick()
    env.clock.advance(SETTLE + 1)
    assert watcher.tick().capture_ids == []

    awaiting = {item["folder"]: item for item in watcher.status()["sources"]["taller"]["awaiting_manifest"]}
    assert set(awaiting) == {folder.name, "sin_patron"}
    assert awaiting[folder.name]["files"] == 3 and awaiting[folder.name]["draftable"] is True
    assert awaiting["sin_patron"]["draftable"] is False
    assert "<entorno>_<epoch en segundos>" in awaiting["sin_patron"]["draft_error"]
    assert not [m for m in _messages(env, "watch") if "sin capture.json" in m]

    # Pasado el plazo se avisa, una sola vez por carpeta.
    env.clock.advance(31)
    watcher.tick()
    watcher.tick()
    warnings = [m for m in _messages(env, "watch") if "sin capture.json" in m]
    assert len(warnings) == 2
    assert any(m.startswith(f"{folder.name}: 3 fichero(s) completos sin capture.json") for m in warnings)
    passes = [e.message for e in env.container.repository.list_activity(limit=5, component="watch") if e.level.value == "debug"]
    assert passes[-1].endswith("4 esperando su manifiesto")

    from episodic.application.watch import draft_group_manifest, write_manifest

    source = watcher.settings.sources[0]
    target, manifest = draft_group_manifest(env.container.repository, source, folder.name)
    assert target == folder / "capture.json"
    assert manifest == {
        "schema": "epigraph.capture/v1",
        "environment_id": "env_context_A015",
        "captured_at": T0,
        "devices": [
            {"id": "camara", "kind": "camera", "files": ["camara.mp4"]},
            {"id": "conversacion", "kind": "microphone", "files": ["conversacion.mp3"]},
            {"id": "notas", "kind": "document", "files": ["notas.txt"]},
        ],
    }
    with pytest.raises(ValueError, match="<entorno>_<epoch"):
        draft_group_manifest(env.container.repository, source, "sin_patron")

    write_manifest(target, manifest)
    with pytest.raises(FileExistsError):
        draft_group_manifest(env.container.repository, source, folder.name)
    report = watcher.tick()
    assert len(report.capture_ids) == 1
    capture = env.container.repository.get_capture(report.capture_ids[0])
    assert capture.metadata["capture_key"] == f"env_context_A015_{T0}"
    awaiting = watcher.status()["sources"]["taller"]["awaiting_manifest"]
    assert [item["folder"] for item in awaiting] == ["sin_patron"]


def test_manifest_api_previews_and_writes_the_draft(tmp_path) -> None:
    inbox = tmp_path / "entrada"
    folder = inbox / f"env_context_A001_{T0}"
    _put(folder, "notas.txt", b"hola")
    config_file = tmp_path / "watch.toml"
    config_file.write_text(
        "[watch]\ninterval_seconds = 1\nsettle_seconds = 0\n\n"
        "[[sources]]\nname = \"taller\"\npath = \"entrada\"\n",
        encoding="utf-8",
    )
    config = EpisodicConfig(
        project_root=tmp_path,
        data_dir="runtime",
        vision_backend="fake",
        speech_backend="fake",
        watch_config="watch.toml",
    )
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    try:
        with TestClient(create_api_app(container=container, start_watcher=False)) as client:
            container.watcher.tick()
            container.watcher.tick()
            status = client.get("/api/watch").json()
            assert [item["folder"] for item in status["sources"][0]["awaiting_manifest"]] == [folder.name]

            form = {"source": "taller", "group_key": folder.name}
            preview = client.post("/api/watch/manifest", data=form).json()
            assert preview["written"] is False and not (folder / "capture.json").exists()
            assert preview["manifest"]["environment_id"] == "env_context_A001"

            written = client.post("/api/watch/manifest", data={**form, "write": "true"}).json()
            assert written["written"] is True
            assert json.loads((folder / "capture.json").read_text(encoding="utf-8")) == preview["manifest"]
            assert client.post("/api/watch/manifest", data=form).status_code == 409
            assert client.post("/api/watch/manifest", data={**form, "group_key": "otra"}).status_code == 404
            assert any(
                entry["message"].startswith(f"Manifiesto creado a mano para {folder.name}")
                for entry in client.get("/api/activity", params={"component": "watch"}).json()["entries"]
            )

            report = container.watcher.tick()
            job = container.job_runner.wait(report.job_ids[0], timeout=60)
            assert job.state == JobState.COMPLETED
    finally:
        container.close()
