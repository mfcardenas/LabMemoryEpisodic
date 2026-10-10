"""Contrato HTTP consumido por el dashboard episódico."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from episodic.domain.entities import Episode, QueryResult, QueryRun
from episodic.domain.times import Interval, TimeSpec
from episodic.domain.types import SearchIntent, Verdict
from episodic.interfaces.http.server import create_api_app


ATLAS_INTERVAL = Interval(
    start=TimeSpec.from_utc(datetime(2026, 1, 1, 18, 23, 18, tzinfo=timezone.utc), "camera_c01"),
    end=TimeSpec.from_utc(datetime(2026, 1, 1, 18, 25, 6, tzinfo=timezone.utc), "camera_c01"),
)


class StubRepository:
    def __init__(self, episode: Episode) -> None:
        self.episode = episode

    def list_episodes(self, **_kwargs):
        return [self.episode], None

    def list_sources(self):
        return []

    def list_entities(self):
        return []

    def get_episode(self, episode_id: str):
        return self.episode if episode_id == self.episode.id else None

    def get_indexing_summary(self):
        return {
            "pending": 1,
            "indexed": 2,
            "failed": 0,
            "stale": 0,
            "total": 3,
        }


class StubRetrieval:
    def __init__(self, episode: Episode) -> None:
        self.episode = episode
        self.last_kwargs = None

    def search(self, **kwargs):
        self.last_kwargs = kwargs
        return QueryRun(
            intent=kwargs["intent"],
            text_query=kwargs.get("text_query"),
            results=[
                QueryResult(
                    episode_id=self.episode.id,
                    score=0.91,
                    rank=1,
                    verdict=Verdict.SUPPORTED,
                )
            ],
            overall_verdict=Verdict.SUPPORTED,
        )


class StubVectorIndex:
    collection_name = "episodic_memory"
    generation_name = "gen_before"

    def __init__(self) -> None:
        self.points = 1

    def count(self):
        return self.points


def build_container():
    episode = Episode(
        id="episode-1",
        external_id="ATLAS-EP-001",
        title="Acceso al pasillo",
        interval=ATLAS_INTERVAL,
    )
    repository = StubRepository(episode)
    retrieval = StubRetrieval(episode)
    vector_index = StubVectorIndex()

    class StubLifecycle:
        def rebuild_index(self, _worker):
            vector_index.generation_name = "staging_after"
            vector_index.points = 2
            return 2

    return SimpleNamespace(
        config=SimpleNamespace(
            vector_backend="edge",
            qdrant_collection="episodic_memory",
            nomic_model="nomic-test",
            clip_vision_model="clip-test",
        ),
        repository=repository,
        retrieval_service=retrieval,
        vector_index=vector_index,
        lifecycle_service=StubLifecycle(),
        indexing_worker=object(),
    )


def test_search_parses_temporal_filter_and_returns_timeline() -> None:
    container = build_container()
    client = TestClient(create_api_app(container=container))

    response = client.post(
        "/api/search",
        json={
            "intent": "semantic_search",
            "text_query": "persona en pasillo",
            "start_time": "2026-01-01T18:00:00Z",
            "end_time": "2026-01-01T18:45:00Z",
            "require_unique_resolution": True,
        },
    )

    assert response.status_code == 200
    sent = container.retrieval_service.last_kwargs
    assert sent["interval_filter"].start.absolute == datetime(
        2026, 1, 1, 18, 0, tzinfo=timezone.utc
    )
    assert sent["require_unique_resolution"] is True
    result = response.json()["results"][0]
    assert result["timeline_start"] == "2026-01-01T18:23:18Z"
    assert result["timeline_end"] == "2026-01-01T18:25:06Z"
    assert result["timeline_kind"] == "interval"
    assert result["timeline_clock_source"] == "camera_c01"


def test_search_rejects_incomplete_or_naive_intervals() -> None:
    client = TestClient(create_api_app(container=build_container()))

    incomplete = client.post(
        "/api/search",
        json={"start_time": "2026-01-01T18:00:00Z"},
    )
    naive = client.post(
        "/api/search",
        json={
            "start_time": "2026-01-01T18:00:00",
            "end_time": "2026-01-01T18:45:00",
        },
    )

    assert incomplete.status_code == 422
    assert naive.status_code == 422


def test_health_and_rebuild_expose_edge_generation() -> None:
    container = build_container()
    client = TestClient(create_api_app(container=container))

    health = client.get("/api/health")
    rebuild = client.post("/api/rebuild-index")

    assert health.status_code == 200
    assert health.json()["active_generation"] == "gen_before"
    assert health.json()["vector_backend"] == "edge"
    assert rebuild.status_code == 200
    assert rebuild.json() == {
        "status": "success",
        "vector_backend": "edge",
        "previous_generation": "gen_before",
        "active_generation": "staging_after",
        "previous_points": 1,
        "active_points": 2,
        "rebuilt_points": 2,
        "verified": True,
    }


def test_project_status_exposes_runtime_outbox_and_honest_integration() -> None:
    client = TestClient(create_api_app(container=build_container()))

    summary = client.get("/api/indexing/summary")
    status = client.get("/api/project-status")

    assert summary.status_code == 200
    assert summary.json() == {
        "pending": 1,
        "indexed": 2,
        "failed": 0,
        "stale": 0,
        "total": 3,
    }
    assert status.status_code == 200
    body = status.json()
    assert body["checkpoint"] == "PC-EG1"
    assert body["runtime"]["active_generation"] == "gen_before"
    assert body["indexing"] == summary.json()
    components = {component["id"]: component for component in body["components"]}
    assert components["episodic"]["integrated"] is True
    assert components["legacy_rag"]["present"] is True
    assert components["legacy_rag"]["integrated"] is False
    assert components["robot"]["integrated"] is False


def test_dashboard_exposes_canonical_epigraph_workflow() -> None:
    html = Path("episodic/interfaces/web/index.html").read_text(encoding="utf-8")
    javascript = Path("episodic/interfaces/web/app.js").read_text(encoding="utf-8")

    assert 'type="datetime-local"' in html
    assert 'id="input-require-unique"' in html
    assert 'id="ingest-form"' in html
    assert 'id="episode-inspector"' in html
    assert 'id="memory-map"' in html
    assert 'id="timeline-canvas"' in html
    assert "Asset \u2192 ProcessingWindow" not in html  # El front usa etiquetas en español.
    assert "Activo \u2192 ventanas \u2192 observaciones" in html
    assert "Atlas" not in html
    assert 'api("/api/captures", { method: "POST"' in javascript
    assert "/api/jobs/" in javascript
    assert 'name="files" type="file" required multiple' in html
    assert "require_unique_resolution:" in javascript
    assert "/api/memory/episodes/" in javascript
    assert "/api/memory/timeline" in javascript
    assert "/api/memory/assets/" in javascript
    assert "Contenido extraído" in javascript
    assert "Archivos y fuentes" in javascript
    assert "Fuente simulada: no existe un fichero físico" in javascript


def test_search_never_accepts_client_file_paths() -> None:
    container = build_container()
    container.repository.get_evidence = lambda _evidence_id: None
    client = TestClient(create_api_app(container=container))

    by_path = client.post(
        "/api/search",
        json={"text_query": "pasillo", "image_query_path": "C:/Windows/win.ini"},
    )
    unknown_evidence = client.post(
        "/api/search",
        json={"text_query": "pasillo", "image_evidence_id": "no-existe"},
    )

    assert by_path.status_code == 200
    assert container.retrieval_service.last_kwargs["image_query_path"] is None
    assert unknown_evidence.status_code == 404
