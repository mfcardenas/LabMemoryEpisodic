"""Inspección determinista de archivos originales para la ingesta de Fase 3."""
from __future__ import annotations

import hashlib
import mimetypes
import struct
import wave
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

from PIL import Image

from episodic.domain.types import EvidenceMediaType


@dataclass(frozen=True)
class FileInspection:
    path: Path
    content_hash: str
    mime_type: str
    media_type: EvidenceMediaType
    size_bytes: int
    duration_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class UnsupportedAssetTypeError(ValueError):
    """El archivo no pertenece a uno de los tipos admitidos en Fase 3."""


class LocalFileInspector:
    """Calcula metadatos sin transcribir ni describir el contenido."""

    _MIME_TO_MEDIA = {
        "application/pdf": EvidenceMediaType.DOCUMENT,
        "application/json": EvidenceMediaType.TEXT,
        "text/plain": EvidenceMediaType.TEXT,
        "text/csv": EvidenceMediaType.TEXT,
        "text/markdown": EvidenceMediaType.TEXT,
    }

    def inspect(self, file_path: str | Path) -> FileInspection:
        path = Path(file_path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"La ruta no es un archivo: {path}")

        with path.open("rb") as stream:
            head = stream.read(32)
        mime_type = self._detect_mime(path, head)
        media_type = self._media_type(mime_type)
        metadata: dict[str, Any] = {"extension": path.suffix.lower()}
        duration_seconds: float | None = None

        if media_type == EvidenceMediaType.IMAGE:
            with Image.open(path) as image:
                metadata.update(
                    width=image.width,
                    height=image.height,
                    image_format=image.format,
                )
        elif media_type == EvidenceMediaType.AUDIO and mime_type == "audio/wav":
            with wave.open(str(path), "rb") as audio:
                frame_rate = audio.getframerate()
                duration_seconds = audio.getnframes() / frame_rate if frame_rate else None
                metadata.update(
                    channels=audio.getnchannels(),
                    sample_rate_hz=frame_rate,
                    sample_width_bytes=audio.getsampwidth(),
                )
        elif media_type == EvidenceMediaType.AUDIO and mime_type == "audio/mpeg":
            duration_seconds = _read_mp3_duration(path)
            if duration_seconds is None:
                metadata["duration_status"] = "unavailable"
        elif media_type == EvidenceMediaType.VIDEO and mime_type == "video/mp4":
            duration_seconds = _read_mp4_duration(path)
            if duration_seconds is None:
                metadata["duration_status"] = "unavailable"
            creation_time = _read_mp4_creation_time(path)
            if creation_time is not None:
                metadata["creation_time"] = creation_time.isoformat()

        if mime_type == "application/json":
            metadata["structured_text"] = True

        return FileInspection(
            path=path,
            content_hash=_sha256(path),
            mime_type=mime_type,
            media_type=media_type,
            size_bytes=path.stat().st_size,
            duration_seconds=duration_seconds,
            metadata=metadata,
        )

    @staticmethod
    def _detect_mime(path: Path, head: bytes) -> str:
        suffix = path.suffix.lower()
        if head.startswith(b"%PDF-"):
            return "application/pdf"
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if head.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if head.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
            return "audio/wav"
        if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
            return "video/x-msvideo"
        if head.startswith(b"fLaC"):
            return "audio/flac"
        if head.startswith(b"OggS"):
            return "video/ogg" if suffix in {".ogv", ".ogm"} else "audio/ogg"
        if head.startswith(b"ID3") or head[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
            return "audio/mpeg"
        if len(head) >= 12 and head[4:8] == b"ftyp":
            return "audio/mp4" if suffix in {".m4a", ".m4b"} else "video/mp4"

        guessed, _ = mimetypes.guess_type(path.name)
        if suffix == ".md":
            return "text/markdown"
        if suffix == ".json":
            return "application/json"
        return guessed or "application/octet-stream"

    def _media_type(self, mime_type: str) -> EvidenceMediaType:
        if mime_type in self._MIME_TO_MEDIA:
            return self._MIME_TO_MEDIA[mime_type]
        if mime_type.startswith("image/"):
            return EvidenceMediaType.IMAGE
        if mime_type.startswith("audio/"):
            return EvidenceMediaType.AUDIO
        if mime_type.startswith("video/"):
            return EvidenceMediaType.VIDEO
        raise UnsupportedAssetTypeError(f"Tipo MIME no soportado en Fase 3: {mime_type}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _read_mp4_duration(path: Path) -> float | None:
    """Lee MP4 clásicos y fragmentados sin requerir un decodificador."""
    moov: bytes | None = None
    moofs: list[bytes] = []
    with path.open("rb") as stream:
        for atom_type, payload_start, payload_size in _iter_atoms(stream, path.stat().st_size):
            if atom_type not in {b"moov", b"moof"} or payload_size > 64 * 1024 * 1024:
                continue
            stream.seek(payload_start)
            payload = stream.read(payload_size)
            if atom_type == b"moov":
                moov = payload
            else:
                moofs.append(payload)

    if moov is None:
        return None
    durations = _collect_mp4_durations(moov)
    durations.extend(_fragmented_mp4_durations(moov, moofs))
    if durations:
        return max(durations)
    return None


_MP4_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)


def _read_mp4_creation_time(path: Path) -> datetime | None:
    """Lee ``mvhd.creation_time`` (UTC desde 1904); 0 significa no declarado."""
    with path.open("rb") as stream:
        for atom_type, payload_start, payload_size in _iter_atoms(stream, path.stat().st_size):
            if atom_type != b"moov" or payload_size > 64 * 1024 * 1024:
                continue
            stream.seek(payload_start)
            moov = stream.read(payload_size)
            for child_type, child_start, child_size in _iter_memory_atoms(moov):
                if child_type == b"mvhd":
                    return _creation_time_from_mvhd(moov[child_start:child_start + child_size])
            return None
    return None


def _creation_time_from_mvhd(payload: bytes) -> datetime | None:
    if len(payload) < 8:
        return None
    if payload[0] == 1 and len(payload) >= 12:
        seconds = struct.unpack(">Q", payload[4:12])[0]
    else:
        seconds = struct.unpack(">I", payload[4:8])[0]
    if seconds == 0:
        return None
    try:
        return _MP4_EPOCH + timedelta(seconds=seconds)
    except OverflowError:
        return None


def _collect_mp4_durations(data: bytes) -> list[float]:
    durations: list[float] = []
    containers = {b"moov", b"trak", b"mdia"}
    for atom_type, payload_start, payload_size in _iter_memory_atoms(data):
        payload = data[payload_start:payload_start + payload_size]
        if atom_type in {b"mvhd", b"mdhd"}:
            duration = _duration_from_mvhd(payload)
            if duration is not None and duration > 0:
                durations.append(duration)
        elif atom_type in containers:
            durations.extend(_collect_mp4_durations(payload))
    return durations


def _iter_atoms(stream: BinaryIO, file_size: int):
    position = 0
    while position + 8 <= file_size:
        stream.seek(position)
        header = stream.read(16)
        if len(header) < 8:
            return
        size = struct.unpack(">I", header[:4])[0]
        header_size = 8
        if size == 1:
            if len(header) < 16:
                return
            size = struct.unpack(">Q", header[8:16])[0]
            header_size = 16
        elif size == 0:
            size = file_size - position
        if size < header_size or position + size > file_size:
            return
        yield header[4:8], position + header_size, size - header_size
        position += size


def _iter_memory_atoms(data: bytes):
    position = 0
    while position + 8 <= len(data):
        size = struct.unpack(">I", data[position:position + 4])[0]
        atom_type = data[position + 4:position + 8]
        header_size = 8
        if size == 1 and position + 16 <= len(data):
            size = struct.unpack(">Q", data[position + 8:position + 16])[0]
            header_size = 16
        if size < header_size or position + size > len(data):
            return
        yield atom_type, position + header_size, size - header_size
        position += size


def _duration_from_mvhd(payload: bytes) -> float | None:
    if len(payload) < 20:
        return None
    version = payload[0]
    if version == 0 and len(payload) >= 20:
        timescale = struct.unpack(">I", payload[12:16])[0]
        duration = struct.unpack(">I", payload[16:20])[0]
    elif version == 1 and len(payload) >= 32:
        timescale = struct.unpack(">I", payload[20:24])[0]
        duration = struct.unpack(">Q", payload[24:32])[0]
    else:
        return None
    return duration / timescale if timescale else None


def _fragmented_mp4_durations(moov: bytes, moofs: list[bytes]) -> list[float]:
    """Calcula la duración desde ``tfhd``/``tfdt``/``trun`` (fMP4)."""
    timescales = _mp4_track_timescales(moov)
    track_ends: dict[int, int] = {}
    for moof in moofs:
        for atom_type, payload_start, payload_size in _iter_memory_atoms(moof):
            if atom_type != b"traf":
                continue
            traf = moof[payload_start:payload_start + payload_size]
            parsed = _fragment_track_end(traf)
            if parsed is None:
                continue
            track_id, end_time = parsed
            track_ends[track_id] = max(track_ends.get(track_id, 0), end_time)
    return [
        end_time / timescales[track_id]
        for track_id, end_time in track_ends.items()
        if end_time > 0 and timescales.get(track_id, 0) > 0
    ]


def _mp4_track_timescales(moov: bytes) -> dict[int, int]:
    result: dict[int, int] = {}
    for atom_type, payload_start, payload_size in _iter_memory_atoms(moov):
        if atom_type != b"trak":
            continue
        trak = moov[payload_start:payload_start + payload_size]
        track_id: int | None = None
        timescale: int | None = None
        for child_type, child_start, child_size in _iter_memory_atoms(trak):
            child = trak[child_start:child_start + child_size]
            if child_type == b"tkhd":
                track_id = _track_id_from_tkhd(child)
            elif child_type == b"mdia":
                timescale = _timescale_from_mdia(child)
        if track_id is not None and timescale:
            result[track_id] = timescale
    return result


def _track_id_from_tkhd(payload: bytes) -> int | None:
    if len(payload) < 24:
        return None
    version = payload[0]
    offset = 20 if version == 1 else 12
    if len(payload) < offset + 4:
        return None
    return struct.unpack(">I", payload[offset:offset + 4])[0]


def _timescale_from_mdia(mdia: bytes) -> int | None:
    for atom_type, payload_start, payload_size in _iter_memory_atoms(mdia):
        if atom_type != b"mdhd":
            continue
        payload = mdia[payload_start:payload_start + payload_size]
        if not payload:
            return None
        offset = 20 if payload[0] == 1 else 12
        if len(payload) >= offset + 4:
            return struct.unpack(">I", payload[offset:offset + 4])[0]
    return None


def _fragment_track_end(traf: bytes) -> tuple[int, int] | None:
    track_id: int | None = None
    default_duration: int | None = None
    decode_time = 0
    runs: list[bytes] = []
    for atom_type, payload_start, payload_size in _iter_memory_atoms(traf):
        payload = traf[payload_start:payload_start + payload_size]
        if atom_type == b"tfhd":
            parsed = _parse_tfhd(payload)
            if parsed is not None:
                track_id, default_duration = parsed
        elif atom_type == b"tfdt":
            parsed_time = _parse_tfdt(payload)
            if parsed_time is not None:
                decode_time = parsed_time
        elif atom_type == b"trun":
            runs.append(payload)
    if track_id is None:
        return None
    run_duration = sum(_trun_duration(run, default_duration) for run in runs)
    return track_id, decode_time + run_duration


def _parse_tfhd(payload: bytes) -> tuple[int, int | None] | None:
    if len(payload) < 8:
        return None
    flags = int.from_bytes(payload[1:4], "big")
    track_id = struct.unpack(">I", payload[4:8])[0]
    position = 8
    for flag, width in ((0x000001, 8), (0x000002, 4)):
        if flags & flag:
            position += width
    default_duration: int | None = None
    if flags & 0x000008:
        if len(payload) < position + 4:
            return None
        default_duration = struct.unpack(">I", payload[position:position + 4])[0]
    return track_id, default_duration


def _parse_tfdt(payload: bytes) -> int | None:
    if len(payload) < 8:
        return None
    if payload[0] == 1:
        return struct.unpack(">Q", payload[4:12])[0] if len(payload) >= 12 else None
    return struct.unpack(">I", payload[4:8])[0]


def _trun_duration(payload: bytes, default_duration: int | None) -> int:
    if len(payload) < 8:
        return 0
    flags = int.from_bytes(payload[1:4], "big")
    sample_count = struct.unpack(">I", payload[4:8])[0]
    position = 8
    if flags & 0x000001:
        position += 4
    if flags & 0x000004:
        position += 4
    if not flags & 0x000100:
        return sample_count * (default_duration or 0)

    duration = 0
    sample_width = sum(
        4 for flag in (0x000100, 0x000200, 0x000400, 0x000800) if flags & flag
    )
    duration_offset = 0
    for _ in range(sample_count):
        if len(payload) < position + duration_offset + 4:
            break
        duration += struct.unpack(">I", payload[position + duration_offset:position + duration_offset + 4])[0]
        position += sample_width
    return duration


def _read_mp3_duration(path: Path) -> float | None:
    """Suma frames MPEG Layer III; admite CBR y VBR sin decodificar audio."""
    data = path.read_bytes()
    position = _id3v2_size(data)
    duration = 0.0
    frame_count = 0
    while position + 4 <= len(data):
        header = int.from_bytes(data[position:position + 4], "big")
        frame = _mp3_frame_info(header)
        if frame is None:
            position += 1
            continue
        frame_length, samples_per_frame, sample_rate = frame
        if position + frame_length > len(data):
            break
        duration += samples_per_frame / sample_rate
        frame_count += 1
        position += frame_length
    return duration if frame_count else None


def _id3v2_size(data: bytes) -> int:
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    size_bytes = data[6:10]
    if any(value & 0x80 for value in size_bytes):
        return 0
    tag_size = sum(value << shift for value, shift in zip(size_bytes, (21, 14, 7, 0)))
    footer_size = 10 if data[5] & 0x10 else 0
    return 10 + tag_size + footer_size


def _mp3_frame_info(header: int) -> tuple[int, int, int] | None:
    if (header >> 21) & 0x7FF != 0x7FF:
        return None
    version_id = (header >> 19) & 0x3
    layer_id = (header >> 17) & 0x3
    bitrate_index = (header >> 12) & 0xF
    sample_rate_index = (header >> 10) & 0x3
    padding = (header >> 9) & 0x1
    if version_id == 1 or layer_id != 1 or bitrate_index in {0, 15} or sample_rate_index == 3:
        return None

    mpeg1_bitrates = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
    mpeg2_bitrates = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
    bitrate_kbps = (
        mpeg1_bitrates[bitrate_index]
        if version_id == 3
        else mpeg2_bitrates[bitrate_index]
    )
    base_rate = (44100, 48000, 32000)[sample_rate_index]
    sample_rate = base_rate if version_id == 3 else base_rate // (2 if version_id == 2 else 4)
    samples_per_frame = 1152 if version_id == 3 else 576
    coefficient = 144 if version_id == 3 else 72
    frame_length = int(coefficient * bitrate_kbps * 1000 / sample_rate) + padding
    return frame_length, samples_per_frame, sample_rate
