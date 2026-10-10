"""Criterios de aceptación de Fase 3: activos y ventanas técnicas."""
from __future__ import annotations

import struct
import wave
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from PIL import Image

from episodic.adapters.files.metadata import LocalFileInspector
from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.application.asset_ingestion import AssetIngestionService
from episodic.application.windowing import WindowingService
from episodic.bootstrap import create_ingestion_container
from episodic.config import EpisodicConfig
from episodic.domain.types import EvidenceMediaType


@pytest.fixture
def phase3_services(tmp_path):
    repository = SqliteEpisodicRepository(tmp_path / "phase3.db")
    try:
        yield (
            repository,
            AssetIngestionService(repository, LocalFileInspector()),
            WindowingService(repository),
        )
    finally:
        repository.close()


def _write_supported_samples(tmp_path):
    image_path = tmp_path / "pipeta.png"
    Image.new("RGB", (4, 3), color=(255, 105, 180)).save(image_path)

    audio_path = tmp_path / "ambiente.wav"
    with wave.open(str(audio_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8_000)
        output.writeframes(b"\x00\x00" * 16_000)

    video_path = tmp_path / "camera.mp4"
    ftyp = _atom(b"ftyp", b"isom\x00\x00\x02\x00isom")
    mvhd_payload = b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 1_000, 125_000)
    video_path.write_bytes(ftyp + _atom(b"moov", _atom(b"mvhd", mvhd_payload)))

    text_path = tmp_path / "nota.txt"
    text_path.write_text("Pipeta rosada sobre la mesa.", encoding="utf-8")
    pdf_path = tmp_path / "protocolo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    json_path = tmp_path / "sensor.json"
    json_path.write_text('{"pipeta": "rosada", "presente": true}', encoding="utf-8")
    return [video_path, audio_path, image_path, text_path, pdf_path, json_path]


def _atom(atom_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, atom_type) + payload


def _write_fragmented_mp4(path) -> None:
    """fMP4 mínimo: duración solo en la tabla del fragmento."""
    mvhd = _atom(b"mvhd", b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 1_000, 0))
    tkhd = _atom(
        b"tkhd",
        b"\x00\x00\x00\x03" + struct.pack(">IIIII", 0, 0, 1, 0, 0),
    )
    mdhd = _atom(b"mdhd", b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 1_000, 0))
    trak = _atom(b"trak", tkhd + _atom(b"mdia", mdhd))
    moov = _atom(b"moov", mvhd + trak)
    tfhd = _atom(b"tfhd", b"\x00\x02\x00\x08" + struct.pack(">II", 1, 40))
    tfdt = _atom(b"tfdt", b"\x00\x00\x00\x00" + struct.pack(">I", 0))
    trun = _atom(b"trun", b"\x00\x00\x00\x00" + struct.pack(">I", 25))
    path.write_bytes(_atom(b"ftyp", b"isom\x00\x00\x02\x00isom") + moov + _atom(b"moof", _atom(b"traf", tfhd + tfdt + trun)))


def _write_mp3_frames(path, frame_count: int = 10) -> None:
    # MPEG-1 Layer III, 128 kbps, 44.1 kHz: frames de 417 bytes.
    path.write_bytes((b"\xff\xfb\x90\x00" + b"\x00" * 413) * frame_count)


def test_ingests_every_supported_type_and_preserves_metadata(phase3_services, tmp_path) -> None:
    repository, ingestion, _ = phase3_services
    paths = _write_supported_samples(tmp_path)
    expected_types = [
        EvidenceMediaType.VIDEO,
        EvidenceMediaType.AUDIO,
        EvidenceMediaType.IMAGE,
        EvidenceMediaType.TEXT,
        EvidenceMediaType.DOCUMENT,
        EvidenceMediaType.TEXT,
    ]

    assets = []
    for index, (path, expected_type) in enumerate(zip(paths, expected_types, strict=True)):
        result = ingestion.ingest_file(
            path,
            environment_id="laboratorio_01",
            source_id=f"source_{index}",
        )
        assert result.duplicate is False
        assert result.asset.media_type == expected_type
        assets.append(result.asset)

    assert len(repository.list_assets("laboratorio_01")) == 6
    assert all(asset.content_hash.startswith("sha256:") for asset in assets)
    assert all(asset.size_bytes and asset.size_bytes > 0 for asset in assets)
    assert all(asset.uri == str(path.resolve()) for asset, path in zip(assets, paths, strict=True))
    assert all(asset.metadata["content_extracted"] is False for asset in assets)
    assert assets[0].mime_type == "video/mp4"
    assert assets[0].duration_seconds == pytest.approx(125.0)
    assert assets[1].duration_seconds == pytest.approx(2.0)
    assert assets[2].metadata["inspection"]["width"] == 4
    assert assets[5].metadata["inspection"]["structured_text"] is True


def test_duplicate_hash_returns_the_existing_asset(phase3_services, tmp_path) -> None:
    repository, ingestion, _ = phase3_services
    text_path = tmp_path / "registro.txt"
    text_path.write_text("mismo contenido", encoding="utf-8")

    first = ingestion.ingest_file(
        text_path, environment_id="laboratorio_01", source_id="documentos"
    )
    second = ingestion.ingest_file(
        text_path, environment_id="laboratorio_01", source_id="documentos"
    )

    assert second.duplicate is True
    assert second.asset.id == first.asset.id
    assert len(repository.list_assets()) == 1


def test_reads_mp3_and_fragmented_mp4_durations(tmp_path) -> None:
    inspector = LocalFileInspector()
    mp3_path = tmp_path / "audio.mp3"
    mp4_path = tmp_path / "fragmented.mp4"
    _write_mp3_frames(mp3_path)
    _write_fragmented_mp4(mp4_path)

    assert inspector.inspect(mp3_path).duration_seconds == pytest.approx(
        10 * 1152 / 44_100
    )
    assert inspector.inspect(mp4_path).duration_seconds == pytest.approx(1.0)


def test_duplicate_ingestion_refreshes_derived_duration(phase3_services, tmp_path) -> None:
    repository, ingestion, _ = phase3_services
    mp4_path = tmp_path / "fragmented.mp4"
    _write_fragmented_mp4(mp4_path)
    first = ingestion.ingest_file(
        mp4_path, environment_id="laboratorio_01", source_id="camera_A01"
    )
    repository.save_asset(replace(first.asset, duration_seconds=0.0))

    refreshed = ingestion.ingest_file(
        mp4_path, environment_id="laboratorio_01", source_id="camera_A01"
    )

    assert refreshed.duplicate is True
    assert refreshed.asset.id == first.asset.id
    assert refreshed.asset.duration_seconds == pytest.approx(1.0)
    assert repository.get_asset(first.asset.id).duration_seconds == pytest.approx(1.0)


def test_audio_video_windows_keep_timeline_and_do_not_create_episodes(
    phase3_services, tmp_path
) -> None:
    repository, ingestion, windowing = phase3_services
    video_path = _write_supported_samples(tmp_path)[0]
    captured_at = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    asset = ingestion.ingest_file(
        video_path,
        environment_id="laboratorio_01",
        source_id="camera_A01",
        captured_at=captured_at,
    ).asset

    result = windowing.create_windows(
        asset.id,
        duration_seconds=60,
        overlap_seconds=5,
    )

    assert [(w.start_offset_seconds, w.end_offset_seconds) for w in result.windows] == [
        (0.0, 60.0),
        (55.0, 115.0),
        (110.0, 125.0),
    ]
    assert all(window.delta_time_seconds == 60 for window in result.windows)
    assert all(window.overlap_seconds == 5 for window in result.windows)
    assert result.windows[1].start_time == captured_at.replace(minute=0, second=55)
    assert result.windows[-1].end_time == captured_at.replace(minute=2, second=5)
    assert result.windows[0].metadata["timeline_basis"] == "asset_captured_at"
    assert repository.list_processing_windows_for_asset(asset.id) == result.windows
    assert repository._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0

    repeated = windowing.create_windows(asset.id, duration_seconds=60, overlap_seconds=5)
    assert [window.id for window in repeated.windows] == [window.id for window in result.windows]
    assert repository._conn.execute("SELECT COUNT(*) FROM processing_windows").fetchone()[0] == 3


def test_windows_reject_non_audiovisual_asset(phase3_services, tmp_path) -> None:
    _, ingestion, windowing = phase3_services
    text_path = tmp_path / "nota.txt"
    text_path.write_text("texto estructurado", encoding="utf-8")
    asset = ingestion.ingest_file(
        text_path, environment_id="laboratorio_01", source_id="docs"
    ).asset

    with pytest.raises(ValueError, match="audio y vídeo"):
        windowing.create_windows(asset.id, duration_seconds=60)


def test_phase3_bootstrap_does_not_start_models_or_vector_index(tmp_path) -> None:
    config = EpisodicConfig(project_root=tmp_path, data_dir="runtime")
    container = create_ingestion_container(config)
    try:
        assert config.sqlite_path.exists()
        assert not config.index_dir.exists()
        assert config.media_dir.exists()
    finally:
        container.close()
