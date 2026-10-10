"""Smoke test del runtime compartido por CLI, API y frontend."""

from fastapi.testclient import TestClient

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.interfaces.http.server import create_api_app


def test_application_starts_and_serves_frontend(tmp_path) -> None:
    config = EpisodicConfig(data_dir=tmp_path / "runtime")
    container = create_app_container(
        config,
        use_fake_encoders=True,
        use_memory_qdrant=True,
    )
    try:
        app = create_api_app(container=container)
        with TestClient(app) as client:
            health = client.get("/api/health")
            frontend = client.get("/")
            frontend_script = client.get("/static/app.js")

        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert frontend.status_code == 200
        assert "EpiGraph" in frontend.text
        assert frontend_script.status_code == 200
    finally:
        container.close()
