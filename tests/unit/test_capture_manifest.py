"""Contrato epigraph.capture/v1 con la herramienta de captura externa."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from episodic.domain.capture_manifest import CaptureManifestError, parse_capture_manifest
from episodic.domain.types import EvidenceMediaType, SourceType

T0 = 1791786600


def _manifest(**overrides):
    return {
        "schema": "epigraph.capture/v1",
        "environment_id": "env_context_01",
        "captured_at": T0,
        "devices": [
            {"id": "cam_norte", "kind": "camera", "files": ["cam_norte.mp4"]},
            {"id": "micro_1", "kind": "microphone", "files": ["audio/micro.wav"], "started_at": T0 - 1.5},
        ],
        **overrides,
    }


def test_valid_manifest_gives_key_devices_and_times() -> None:
    manifest = parse_capture_manifest(_manifest(title="Turno de mañana"))

    assert manifest.capture_key == "env_context_01_1791786600"
    assert manifest.captured_at == datetime(2026, 10, 12, 6, 30, tzinfo=timezone.utc)
    assert manifest.title == "Turno de mañana"
    camera, micro = manifest.devices
    assert camera.kind == SourceType.CAMERA and camera.started_at == manifest.captured_at
    assert micro.started_at == datetime.fromtimestamp(T0 - 1.5, timezone.utc)
    assert micro.source_id("env_context_01") == "env_context_01.micro_1"
    assert manifest.files == {"cam_norte.mp4": camera, "audio/micro.wav": micro}
    assert camera.accepts(EvidenceMediaType.VIDEO) and not camera.accepts(EvidenceMediaType.AUDIO)
    assert micro.accepts(EvidenceMediaType.AUDIO)


def test_fractional_epoch_keeps_the_key_in_whole_seconds() -> None:
    manifest = parse_capture_manifest(_manifest(captured_at=T0 + 0.75))
    assert manifest.capture_key == "env_context_01_1791786600"
    assert manifest.captured_at_epoch == T0 + 0.75


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"schema": "epigraph.capture/v2"}, "'schema'"),
        ({"entorno": "x"}, "claves desconocidas ['entorno']"),
        ({"environment_id": "env con espacios"}, "'environment_id' debe ser un identificador"),
        ({"captured_at": "1791786600"}, "epoch en segundos (número)"),
        ({"captured_at": T0 * 1000}, "milisegundos"),
        ({"captured_at": 12345}, "no es un epoch en segundos plausible"),
        ({"devices": []}, "al menos un dispositivo"),
        ({"devices": [{"id": "c", "kind": "drone", "files": ["a.mp4"]}]}, "devices[0].kind"),
        ({"devices": [{"id": "c", "kind": "camera", "files": []}]}, "al menos un fichero"),
        ({"devices": [{"id": "c", "kind": "camera", "files": ["../fuera.mp4"]}]}, "relativo"),
        ({"devices": [{"id": "c", "kind": "camera", "files": ["C:/x.mp4"]}]}, "relativo"),
        ({"devices": [{"id": "c", "kind": "camera", "files": ["a.mp4"], "fps": 30}]}, "claves desconocidas ['fps']"),
        (
            {"devices": [
                {"id": "c", "kind": "camera", "files": ["a.mp4"]},
                {"id": "c", "kind": "camera", "files": ["b.mp4"]},
            ]},
            "dispositivos repetidos",
        ),
        (
            {"devices": [
                {"id": "c1", "kind": "camera", "files": ["a.mp4"]},
                {"id": "c2", "kind": "camera", "files": ["a.mp4"]},
            ]},
            "más de un dispositivo",
        ),
        ({"title": ""}, "'title'"),
    ],
)
def test_contract_violations_are_explicit(overrides, message) -> None:
    with pytest.raises(CaptureManifestError) as error:
        parse_capture_manifest(_manifest(**overrides))
    assert message in str(error.value)


def test_manifest_must_be_an_object() -> None:
    with pytest.raises(CaptureManifestError, match="objeto JSON"):
        parse_capture_manifest([1, 2])
