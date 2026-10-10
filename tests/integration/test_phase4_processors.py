"""Criterios de aceptación de Fase 4: observaciones y evidencia exacta."""
from __future__ import annotations

import struct
import wave
from datetime import datetime, timezone

import pytest
from PIL import Image

from episodic.adapters.files.metadata import LocalFileInspector
from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.application.asset_ingestion import AssetIngestionService
from episodic.application.observation_extraction import ObservationExtractionService
from episodic.application.windowing import WindowingService
from episodic.domain.types import EvidenceReferenceKind, ProcessingRunState
from episodic.processors import (
    AudioProcessor,
    DocumentProcessor,
    ImageProcessor,
    MockProcessor,
    TextProcessor,
    VideoProcessor,
)


class FakePageExtractor:
    def extract_pages(self, _path):
        return ["La pipeta rosada figura en el inventario.", ""]


@pytest.fixture
def phase4(tmp_path):
    repository = SqliteEpisodicRepository(tmp_path / "phase4.db")
    ingestion = AssetIngestionService(repository, LocalFileInspector())
    windowing = WindowingService(repository)
    processors = [
        TextProcessor(repository, chunk_size=32, chunk_overlap=4),
        DocumentProcessor(repository, extractor=FakePageExtractor(), chunk_size=32, chunk_overlap=4),
        AudioProcessor(repository),
        ImageProcessor(repository),
        VideoProcessor(repository),
        MockProcessor(repository),
    ]
    extraction = ObservationExtractionService(repository, processors)
    try:
        yield repository, ingestion, windowing, extraction, tmp_path
    finally:
        repository.close()


def _atom(atom_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, atom_type) + payload


def _write_video(path, duration_seconds: int = 5) -> None:
    ftyp = _atom(b"ftyp", b"isom\x00\x00\x02\x00isom")
    mvhd = b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 1_000, duration_seconds * 1_000)
    path.write_bytes(ftyp + _atom(b"moov", _atom(b"mvhd", mvhd)))


def _write_wav(path, duration_seconds: int = 3) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8_000)
        samples = [1000 if index % 2 else -1000 for index in range(8_000 * duration_seconds)]
        output.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def _ingest(ingestion, path, source):
    return ingestion.ingest_file(
        path,
        environment_id="laboratorio_01",
        source_id=source,
        captured_at=datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc),
    ).asset


def test_all_initial_processors_create_normalized_observations_with_evidence(phase4) -> None:
    repository, ingestion, windowing, extraction, tmp_path = phase4
    text_path = tmp_path / "registro.json"
    text_path.write_text(
        '{"objeto": "pipeta rosada", "estado": "presente en laboratorio"}',
        encoding="utf-8",
    )
    pdf_path = tmp_path / "inventario.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    audio_path = tmp_path / "nota.wav"
    _write_wav(audio_path)
    image_path = tmp_path / "mesa.png"
    Image.new("RGB", (8, 6), color=(255, 105, 180)).save(image_path)
    video_path = tmp_path / "camera.mp4"
    _write_video(video_path)

    assets = [
        _ingest(ingestion, text_path, "registro"),
        _ingest(ingestion, pdf_path, "documentos"),
        _ingest(ingestion, audio_path, "microfono"),
        _ingest(ingestion, image_path, "camara_fija"),
        _ingest(ingestion, video_path, "camera_A01"),
    ]
    windowing.create_windows(assets[2].id, duration_seconds=2, overlap_seconds=0.5)
    windowing.create_windows(assets[4].id, duration_seconds=3, overlap_seconds=1)

    observations = []
    for asset in assets:
        result = extraction.process_asset(asset.id)
        assert not result.failures
        assert result.observations
        observations.extend(result.observations)

    assert {observation.modality for observation in observations} == {
        "text", "document", "audio", "image", "video"
    }
    for observation in observations:
        assert observation.observation_id == observation.id
        assert observation.source_id
        assert observation.environment_id == "laboratorio_01"
        assert observation.asset_id
        assert observation.start_time.tzinfo is not None
        assert observation.end_time.tzinfo is not None
        assert observation.observation_type
        assert isinstance(observation.attributes, dict)
        assert observation.description
        assert observation.confidence is not None
        assert observation.evidence_references
        assert observation.producer
        assert observation.model_version
        assert observation.processing_run_id
        assert observation.episode_id is None
        assert observation.entity_id is None
        assert observation.to_dict()["observation_id"] == observation.id
        assert repository.get_processing_run(observation.processing_run_id).state == ProcessingRunState.COMPLETED

    references = {
        observation.modality: repository.get_evidence_reference(
            observation.evidence_reference_ids[0]
        )
        for observation in observations
    }
    assert references["text"].reference_kind == EvidenceReferenceKind.TEXT_FRAGMENT
    assert references["document"].reference_kind == EvidenceReferenceKind.PAGE
    assert references["audio"].reference_kind == EvidenceReferenceKind.AUDIOVISUAL_INTERVAL
    assert references["image"].reference_kind == EvidenceReferenceKind.FRAME
    assert references["video"].reference_kind == EvidenceReferenceKind.AUDIOVISUAL_INTERVAL
    assert all(obs.processing_window_id for obs in observations if obs.modality in {"audio", "video"})
    assert repository._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
    assert repository._conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0


def test_text_evidence_offsets_resolve_to_original_content(phase4) -> None:
    repository, ingestion, _, extraction, tmp_path = phase4
    path = tmp_path / "notas.txt"
    original = "  Primera observación de laboratorio.\n\nSegunda observación verificable.  "
    path.write_text(original, encoding="utf-8")
    asset = _ingest(ingestion, path, "notas")

    result = extraction.process_asset(asset.id)

    for observation in result.observations:
        reference = repository.get_evidence_reference(observation.evidence_reference_ids[0])
        assert original[reference.text_start:reference.text_end] == reference.text_quote
        assert reference.text_quote == observation.description


def test_audio_metrics_are_computed_for_each_window(phase4) -> None:
    repository, ingestion, windowing, extraction, tmp_path = phase4
    path = tmp_path / "audio.wav"
    _write_wav(path, duration_seconds=3)
    asset = _ingest(ingestion, path, "microfono")
    windowing.create_windows(asset.id, duration_seconds=2, overlap_seconds=1)

    result = extraction.process_asset(asset.id)

    assert len(result.observations) == 2
    assert all(obs.attributes["rms"] == pytest.approx(1000.0) for obs in result.observations)
    assert [obs.attributes["duration_seconds"] for obs in result.observations] == [2.0, 2.0]
    assert len(repository.list_observations_for_asset(asset.id)) == 2


def test_mock_processor_is_deterministic_and_idempotent(phase4) -> None:
    repository, ingestion, _, extraction, tmp_path = phase4
    path = tmp_path / "imagen.png"
    Image.new("RGB", (2, 2)).save(path)
    asset = _ingest(ingestion, path, "imagenes")

    first = extraction.process_asset(asset.id, processor_name="mock-processor")
    second = extraction.process_asset(asset.id, processor_name="mock-processor")

    assert first.observations[0].id == second.observations[0].id
    assert first.observations[0].attributes == {"deterministic": True}
    assert len(repository.list_observations_for_asset(asset.id)) == 1
    assert len(repository.list_evidence_references_for_asset(asset.id)) == 1


def test_processor_error_keeps_asset_and_records_failed_run(phase4) -> None:
    repository, ingestion, _, extraction, tmp_path = phase4
    path = tmp_path / "invalid.txt"
    path.write_bytes(b"\xff\xfe\xfa")
    asset = _ingest(ingestion, path, "documentos_invalidos")

    result = extraction.process_asset(asset.id)

    assert not result.observations
    assert len(result.failures) == 1
    assert repository.get_asset(asset.id) == asset
    failed = repository._conn.execute(
        "SELECT state FROM processing_runs WHERE source_id = ? ORDER BY created_at DESC LIMIT 1",
        (asset.source_id,),
    ).fetchone()
    assert failed[0] == ProcessingRunState.FAILED.value
    assert repository._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
