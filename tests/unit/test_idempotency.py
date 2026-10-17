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


def _manifest_with_file(file_path: str) -> dict:
    return {
        "sources": [{"id": "cam-x", "name": "Cámara X", "source_type": "camera"}],
        "sessions": [{"id": "sess-x", "source_id": "cam-x"}],
        "episodes": [
            {
                "external_id": "X-001",
                "title": "Episodio con archivo",
                "source_ids": ["cam-x"],
                "evidence": [
                    {"session_id": "sess-x", "external_capture_id": "cap-x", "file_path": file_path}
                ],
            }
        ],
    }


def test_manifest_rejects_missing_or_escaping_files(services, tmp_path: Path):
    repo, _store, ingest_svc = services
    base = tmp_path / "manifest_root"
    base.mkdir()
    (tmp_path / "secret.txt").write_text("fuera del manifiesto", encoding="utf-8")

    for bad_path in ("no_existe.png", "../secret.txt"):
        with pytest.raises(ValueError):
            ingest_svc.ingest_manifest(_manifest_with_file(bad_path), base_dir=base)

    # Un manifiesto en memoria sin raíz no resuelve rutas contra el cwd.
    with pytest.raises(ValueError):
        ingest_svc.ingest_manifest(_manifest_with_file("secret.txt"))

    episodes, _ = repo.list_episodes()
    assert episodes == []


def test_manifest_stores_existing_file_inside_root(services, tmp_path: Path):
    repo, _store, ingest_svc = services
    (tmp_path / "frame.png").write_bytes(b"png bytes")

    stats = ingest_svc.ingest_manifest(_manifest_with_file("frame.png"), base_dir=tmp_path)

    assert stats["evidence"] == 1
