"""Proyectos: espacios de datos aislados servidos por un único proceso."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from episodic.application.jobs import JobRunner
from episodic.bootstrap import ProjectHub
from episodic.config import EpisodicConfig
from episodic.domain.entities import Source
from episodic.domain.processing import Event
from episodic.domain.projects import ProjectError, collection_for, project_id_from_name
from episodic.domain.provenance import Asset, Environment
from episodic.domain.types import EvidenceMediaType, SourceType
from episodic.interfaces.http.projects import create_projects_app

T0 = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)


def _event(repo, env: str, n: int, title: str) -> None:
    repo.save_environment(Environment(id=env, name=f"{env} · {title}", producer="test"))
    repo.save_source(Source(id=f"{env}.cam", environment_id=env, name="cam", source_type=SourceType.CAMERA))
    repo.save_asset(Asset(id=f"asset-{env}", environment_id=env, source_id=f"{env}.cam", uri=f"x://{env}.mp4",
                          media_type=EvidenceMediaType.VIDEO, content_hash=f"h-{env}"))
    for seq in range(n):
        repo.save_event(Event(id=f"ev-{env}-{title}-{seq}", event_type="video_segment", description=title,
                              environment_id=env, source_id=f"{env}.cam", asset_id=f"asset-{env}", sequence_number=seq,
                              title=title, start_time=T0 + timedelta(seconds=seq * 5), end_time=T0 + timedelta(seconds=seq * 5 + 5),
                              producer="video-event-derivation", model_version="events-v2"))


@pytest.fixture()
def hub(tmp_path):
    config = EpisodicConfig(
        project_root=tmp_path, data_dir="runtime", projects_dir="proyectos", watch_config="", query_timezone="UTC"
    )
    hub = ProjectHub(config, use_fake_encoders=True, use_memory_qdrant=True, start_watchers=False)
    yield hub
    hub.close()


@pytest.fixture()
def client(hub):
    with TestClient(create_projects_app(hub=hub)) as test_client:
        yield test_client


def test_the_current_data_is_the_default_project_and_legacy_api_still_works(client, hub, tmp_path) -> None:
    listing = client.get("/api/projects").json()
    assert listing["default"] == "principal"
    [principal] = listing["projects"]
    assert principal["is_default"] and principal["running"] and principal["kind"] == "real"
    assert principal["data_dir"] == str((tmp_path / "runtime").resolve())
    assert principal["qdrant_collection"] == "episodic_memory"
    _event(hub.container().repository, "A001", 2, "real")
    # /api/... sin prefijo y /p/principal/api/... son el mismo proyecto.
    assert client.get("/api/timeline/span").json()["events"] == 2
    assert client.get("/p/principal/api/timeline/span").json()["events"] == 2
    assert client.get("/api/health").status_code == 200


def test_projects_are_isolated_even_with_the_same_environment(client, hub) -> None:
    created = client.post("/api/projects", json={"name": "Pruebas A3 (taller)", "description": "banco compuesto"})
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["id"] == "pruebas-a3-taller" and project["kind"] == "prueba" and project["running"]
    assert project["qdrant_collection"] == collection_for("pruebas-a3-taller") == "epigraph_p_pruebas_a3_taller"
    _event(hub.container().repository, "A001", 3, "real")
    _event(hub.container("pruebas-a3-taller").repository, "A001", 1, "prueba")

    principal = client.get("/api/timeline/span").json()
    test = client.get("/p/pruebas-a3-taller/api/timeline/span").json()
    assert principal["events"] == 3 and test["events"] == 1
    assert [env["name"] for env in principal["environments"]] == ["A001 · real"]
    assert [env["name"] for env in test["environments"]] == ["A001 · prueba"]
    stats = {item["id"]: item["stats"] for item in client.get("/api/projects").json()["projects"]}
    assert stats["principal"]["events"] == 3 and stats["pruebas-a3-taller"]["events"] == 1
    # La consola del proyecto se sirve en su ruta; un proyecto que no existe da 404.
    assert client.get("/p/pruebas-a3-taller/").status_code == 200
    assert client.get("/p/no-existe/api/timeline/span").status_code == 404


def test_folders_belong_to_one_project_and_generate_its_watch_file(client, hub, tmp_path) -> None:
    inbox = tmp_path / "entrada"
    (inbox / "sub").mkdir(parents=True)
    first = client.post("/api/projects", json={"name": "Uno", "folders": [str(inbox)], "active": False})
    assert first.status_code == 201, first.text
    assert first.json()["folders"] == [str(inbox.resolve())] and not first.json()["running"]
    watch = (hub.projects.data_path(hub.projects.get("uno")) / "watch.toml").read_text(encoding="utf-8")
    assert 'grouping = "manifest"' in watch and 'after = "keep"' in watch
    # La misma carpeta o una subcarpeta no pueden ir a otro proyecto.
    for folder in (inbox, inbox / "sub"):
        clash = client.post("/api/projects", json={"name": "Dos", "folders": [str(folder)]})
        assert clash.status_code == 422 and "ya la vigila" in clash.json()["detail"]
    missing = client.post("/api/projects", json={"name": "Tres", "folders": [str(tmp_path / "no-existe")]})
    assert missing.status_code == 422 and "no existe" in missing.json()["detail"]
    # Activar y quitar carpetas se hace con PATCH.
    updated = client.patch("/api/projects/uno", json={"active": True, "folders": []}).json()
    assert updated["running"] and updated["folders"] == []


def test_deleting_a_project_removes_its_data_but_never_the_default(client, hub, tmp_path) -> None:
    inbox = tmp_path / "entrada"
    inbox.mkdir()
    (inbox / "original.mp4").write_bytes(b"x")
    client.post("/api/projects", json={"name": "Borrable", "folders": [str(inbox)]})
    data_dir = hub.projects.data_path(hub.projects.get("borrable"))
    _event(hub.container("borrable").repository, "A001", 1, "x")
    assert (data_dir / "episodic.db").exists()

    refused = client.delete("/api/projects/borrable", params={"confirm": "otro"})
    assert refused.status_code == 422 and "confirme" in refused.json()["detail"]
    done = client.delete("/api/projects/borrable", params={"confirm": "borrable"})
    assert done.status_code == 200, done.text
    assert not data_dir.exists() and (inbox / "original.mp4").exists()  # los originales no se tocan
    assert client.get("/api/projects/borrable").status_code == 404
    assert client.get("/p/borrable/api/timeline/span").status_code == 404
    assert client.delete("/api/projects/principal", params={"confirm": "principal"}).status_code == 422
    assert client.patch("/api/projects/principal", json={"active": False}).status_code == 422


def test_project_ids_come_from_names_and_reserved_ones_are_refused(hub) -> None:
    assert project_id_from_name("Ensayo ñandú 1") == "ensayo-nandu-1"
    assert project_id_from_name("API") == "api-1"
    first = hub.projects.create("Ensayo")
    second = hub.projects.create("Ensayo")
    assert (first.id, second.id) == ("ensayo", "ensayo-2")
    with pytest.raises(ProjectError):
        hub.projects.create("Otro", project_id="static")


def test_job_queues_of_all_projects_share_one_heavy_turn() -> None:
    turn = threading.Lock()
    active, peak = [0], [0]
    gate = threading.Lock()

    class Repo:
        def __init__(self):
            self.jobs = {}

        def get_job(self, job_id):
            return self.jobs.get(job_id)

        def save_job(self, job):
            self.jobs[job.id] = job

        def list_jobs(self, states=None, limit=None):
            return [job for job in self.jobs.values() if states is None or job.state in states]

    def handler(job, report):
        with gate:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with gate:
            active[0] -= 1
        return {}

    runners = [JobRunner(Repo(), {"work": handler}, turn=turn) for _ in range(3)]
    for runner in runners:
        runner.start()
    jobs = [(runner, runner.submit("work", f"t{n}", [("work", "Trabajo")])) for runner in runners for n in range(2)]
    for runner, job in jobs:
        assert runner.wait(job.id, timeout=5).state.value == "completed"
    for runner in runners:
        runner.stop()
    assert peak[0] == 1


def test_front_offers_projects_and_prefixes_every_project_url(client) -> None:
    page = client.get("/").text
    assert 'id="project-select"' in page and 'data-view="projects"' in page and 'id="view-projects"' in page
    assert "/static/proyectos.js" in page and client.get("/static/proyectos.js").status_code == 200
    assert client.get("/p/principal/").text == page
    app = client.get("/static/app.js").text
    # Las llamadas y los medios del proyecto llevan su prefijo; el catálogo no.
    assert "const apiUrl = url =>" in app and "fetch(apiUrl(url), options)" in app
    assert 'src="/api/' not in app and 'href="/api/' not in app and '${BASE}/api/memory/assets/' in app
    assert '${G().base || ""}/api/memory/events/' in client.get("/static/cronologia.js").text


def test_front_has_the_agent_console(client) -> None:
    page = client.get("/").text
    assert 'id="agent-console"' in page and 'id="agent-console-pill"' in page and 'id="agent-console-open"' in page
    assert 'id="ask-mode"' in page and "/static/agentes.js" in page
    console = client.get("/static/agentes.js").text
    # Lee la traza en directo del registro de actividad, filtrada por la ejecución.
    assert "/api/activity?component=agente" in console and "entry.data?.run_id === A.run.id" in console
    assert "window.EpiGraphAgents = {" in console
    app = client.get("/static/app.js").text
    assert "window.EpiGraphAgents?.start(" in app and "window.EpiGraphAgents?.finish(answered)" in app
