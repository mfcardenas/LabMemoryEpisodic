"""Tests unitarios de idempotencia (Criterio de aceptación #1 y #2)."""
from pathlib import Path
import pytest

from episodic.adapters.media.store import FilesystemEvidenceStore
from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.application.ingest import IngestService


@pytest.fixture
def services(tmp_path: Path):
    db_file = tmp_path / "idempotent.db"
    media_dir = tmp_path / "media"

    repo = SqliteEpisodicRepository(db_file)
    store = FilesystemEvidenceStore(media_dir, repo.count_active_references_for_hash)
    svc = IngestService(repo, store)

    yield repo, store, svc
    repo.close()


def test_import_twice_does_not_duplicate(services, tmp_path: Path):
    repo, store, ingest_svc = services

    manifest = {
        "sources": [{"id": "cam-gate", "name": "Cámara Puerta", "source_type": "camera"}],
        "sessions": [{"id": "sess-morning", "source_id": "cam-gate"}],
        "episodes": [
            {
                "id": "ep-gate-01",
                "external_id": "GATE-001",
                "title": "Vehículo entrando",
                "source_ids": ["cam-gate"],
                "evidence": [
                    {
                        "id": "ev-car-01",
                        "session_id": "sess-morning",
                        "source_id": "cam-gate",
                        "external_capture_id": "cap-gate-frame-10",
                        "content": b"fake jpeg image data",
                        "filename": "frame10.jpg"
                    }
                ],
                "observations": [
                    {"content": "Vehículo blanco cruzando barrera", "provenance_kind": "visual_detection"}
                ]
            }
        ]
    }

    # Primera importación
    stats1 = ingest_svc.ingest_manifest(manifest, base_dir=tmp_path)
    assert stats1["episodes"] == 1
    assert stats1["evidence"] == 1
    assert stats1["skipped_duplicates"] == 0

    # Segunda importación (debe omitir duplicados por claves de idempotencia)
    stats2 = ingest_svc.ingest_manifest(manifest, base_dir=tmp_path)
    assert stats2["episodes"] == 0
    assert stats2["skipped_duplicates"] >= 1

    # Verificar que en SQLite solo hay 1 episodio y 1 evidencia
    episodes, _ = repo.list_episodes()
    assert len(episodes) == 1
    assert episodes[0].external_id == "GATE-001"
