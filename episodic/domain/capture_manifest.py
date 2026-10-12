"""Contrato con la herramienta de captura externa (``epigraph.capture/v1``).

La herramienta que graba el entorno no forma parte de EpiGraph: es ella quien
declara, en un manifiesto JSON junto a los ficheros, el entorno, el instante
de la captura (epoch en segundos, UTC) y los dispositivos con sus ficheros.
EpiGraph no deduce nada del nombre de carpetas ni de ficheros.

Ejemplo::

    {
      "schema": "epigraph.capture/v1",
      "environment_id": "env_context_01",
      "captured_at": 1791786600,
      "title": "Desmontaje de la pieza 7",              (opcional)
      "devices": [
        {"id": "cam_norte", "kind": "camera", "files": ["cam_norte.mp4"]},
        {"id": "cam_sur", "kind": "camera", "files": ["cam_sur.mp4"],
         "started_at": 1791786601.4},                   (opcional)
        {"id": "micro_1", "kind": "microphone", "files": ["micro.wav"]},
        {"id": "operario", "kind": "document", "files": ["notas.txt"]}
      ]
    }

La captura se identifica como ``<environment_id>_<captured_at>`` (epoch
entero): ese es el nombre del episodio en la memoria y en Qdrant.

Para cargas manuales (sin la herramienta), ``draft_capture_manifest`` propone
un manifiesto a partir de una carpeta ``<entorno>_<epoch>`` y de sus ficheros,
un dispositivo por fichero. Solo se usa cuando una persona lo pide: el
vigilante nunca lo deduce por su cuenta.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Mapping

from episodic.domain.types import EvidenceMediaType, SourceType

MANIFEST_SCHEMA = "epigraph.capture/v1"
_KEYS = {"schema", "environment_id", "captured_at", "title", "devices"}
_DEVICE_KEYS = {"id", "kind", "files", "started_at"}
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
# 2000-01-01 … 2100-01-01: fuera de ese rango casi seguro no son segundos.
_EPOCH_MIN, _EPOCH_MAX = 946_684_800, 4_102_444_800

# Qué tipos de fichero puede producir cada clase de dispositivo.
DEVICE_MEDIA: dict[SourceType, frozenset[EvidenceMediaType]] = {
    SourceType.CAMERA: frozenset({EvidenceMediaType.VIDEO, EvidenceMediaType.IMAGE}),
    SourceType.MICROPHONE: frozenset({EvidenceMediaType.AUDIO, EvidenceMediaType.VIDEO}),
    SourceType.PHOTO_SET: frozenset({EvidenceMediaType.IMAGE}),
    SourceType.DOCUMENT: frozenset({EvidenceMediaType.DOCUMENT, EvidenceMediaType.TEXT}),
    SourceType.MANUAL: frozenset({EvidenceMediaType.DOCUMENT, EvidenceMediaType.TEXT}),
    SourceType.SENSOR: frozenset(EvidenceMediaType),
}


# Dispositivo que se propone para cada tipo de fichero en un borrador.
DRAFT_DEVICE_KIND: dict[EvidenceMediaType, SourceType] = {
    EvidenceMediaType.VIDEO: SourceType.CAMERA,
    EvidenceMediaType.AUDIO: SourceType.MICROPHONE,
    EvidenceMediaType.IMAGE: SourceType.PHOTO_SET,
    EvidenceMediaType.DOCUMENT: SourceType.DOCUMENT,
    EvidenceMediaType.TEXT: SourceType.DOCUMENT,
}
_FOLDER_NAME = re.compile(r"^(?P<environment>.+)_(?P<epoch>\d{9,10})$")


class CaptureManifestError(ValueError):
    """El manifiesto no cumple el contrato ``epigraph.capture/v1``."""


@dataclass(frozen=True)
class DeviceDeclaration:
    id: str
    kind: SourceType
    files: tuple[str, ...]
    started_at: datetime

    def source_id(self, environment_id: str) -> str:
        return f"{environment_id}.{self.id}"

    def accepts(self, media_type: EvidenceMediaType) -> bool:
        return media_type in DEVICE_MEDIA[self.kind]


@dataclass(frozen=True)
class CaptureManifest:
    environment_id: str
    captured_at: datetime
    captured_at_epoch: float
    devices: tuple[DeviceDeclaration, ...]
    title: str | None = None

    @property
    def capture_key(self) -> str:
        """Nombre de la captura y de su episodio: ``<entorno>_<epoch>``."""
        return f"{self.environment_id}_{int(self.captured_at_epoch)}"

    @property
    def files(self) -> dict[str, DeviceDeclaration]:
        return {name: device for device in self.devices for name in device.files}


def parse_capture_manifest(data: Any) -> CaptureManifest:
    if not isinstance(data, dict):
        raise CaptureManifestError("el manifiesto debe ser un objeto JSON")
    unknown = sorted(set(data) - _KEYS)
    if unknown:
        raise CaptureManifestError(f"claves desconocidas {unknown}; admitidas: {sorted(_KEYS)}")
    if data.get("schema") != MANIFEST_SCHEMA:
        raise CaptureManifestError(f"'schema' debe ser \"{MANIFEST_SCHEMA}\"")
    environment_id = _identifier(data.get("environment_id"), "environment_id")
    captured_epoch = _epoch(data.get("captured_at"), "captured_at")
    title = data.get("title")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise CaptureManifestError("'title' debe ser texto no vacío")

    raw_devices = data.get("devices")
    if not isinstance(raw_devices, list) or not raw_devices:
        raise CaptureManifestError("'devices' debe ser una lista con al menos un dispositivo")
    devices = tuple(
        _device(item, index, captured_epoch) for index, item in enumerate(raw_devices)
    )
    ids = [device.id for device in devices]
    repeated = sorted({item for item in ids if ids.count(item) > 1})
    if repeated:
        raise CaptureManifestError(f"dispositivos repetidos: {repeated}")
    names = [name for device in devices for name in device.files]
    repeated = sorted({item for item in names if names.count(item) > 1})
    if repeated:
        raise CaptureManifestError(f"ficheros declarados en más de un dispositivo: {repeated}")

    return CaptureManifest(
        environment_id=environment_id,
        captured_at=_datetime(captured_epoch),
        captured_at_epoch=captured_epoch,
        devices=devices,
        title=title.strip() if title else None,
    )


def split_capture_folder_name(name: str) -> tuple[str, int]:
    """``env_context_01_1791786600`` -> (``env_context_01``, 1791786600)."""
    match = _FOLDER_NAME.match(name)
    if match is None:
        raise CaptureManifestError(
            f"el nombre '{name}' no sigue el patrón <entorno>_<epoch en segundos>"
        )
    environment_id = _identifier(match["environment"], f"entorno de '{name}'")
    epoch = int(match["epoch"])
    _epoch(epoch, f"epoch de '{name}'")
    return environment_id, epoch


def draft_capture_manifest(
    folder_name: str, files: Mapping[str, EvidenceMediaType]
) -> dict[str, Any]:
    """Manifiesto propuesto: entorno e instante del nombre de la carpeta y un
    dispositivo por fichero, con su clase según el tipo de fichero."""
    environment_id, epoch = split_capture_folder_name(folder_name)
    if not files:
        raise CaptureManifestError(f"'{folder_name}' no tiene ficheros utilizables")
    devices: list[dict[str, Any]] = []
    taken: set[str] = set()
    for name, media_type in sorted(files.items()):
        base = _device_slug(PurePosixPath(name).stem)
        device_id, number = base, 2
        while device_id in taken:
            device_id, number = f"{base}_{number}", number + 1
        taken.add(device_id)
        devices.append(
            {"id": device_id, "kind": DRAFT_DEVICE_KIND[media_type].value, "files": [name]}
        )
    draft = {
        "schema": MANIFEST_SCHEMA,
        "environment_id": environment_id,
        "captured_at": epoch,
        "devices": devices,
    }
    parse_capture_manifest(draft)  # el borrador cumple el mismo contrato
    return draft


def _device_slug(stem: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_.-")[:64]
    return slug or "fichero"


def _device(item: Any, index: int, captured_epoch: float) -> DeviceDeclaration:
    where = f"devices[{index}]"
    if not isinstance(item, dict):
        raise CaptureManifestError(f"{where} debe ser un objeto")
    unknown = sorted(set(item) - _DEVICE_KEYS)
    if unknown:
        raise CaptureManifestError(f"{where}: claves desconocidas {unknown}")
    device_id = _identifier(item.get("id"), f"{where}.id")
    try:
        kind = SourceType(item.get("kind"))
    except ValueError as exc:
        raise CaptureManifestError(
            f"{where}.kind debe ser uno de {[member.value for member in SourceType]}"
        ) from exc
    files = item.get("files")
    if not isinstance(files, list) or not files:
        raise CaptureManifestError(f"{where}.files debe ser una lista con al menos un fichero")
    started = item.get("started_at")
    started_epoch = captured_epoch if started is None else _epoch(started, f"{where}.started_at")
    return DeviceDeclaration(
        id=device_id,
        kind=kind,
        files=tuple(_relative_file(name, f"{where}.files") for name in files),
        started_at=_datetime(started_epoch),
    )


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.match(value):
        raise CaptureManifestError(
            f"'{where}' debe ser un identificador (letras, dígitos, '_', '.', '-')"
        )
    return value


def _epoch(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaptureManifestError(f"'{where}' debe ser un epoch en segundos (número)")
    if value > _EPOCH_MAX * 100:
        raise CaptureManifestError(f"'{where}' = {value} parece estar en milisegundos; use segundos")
    if not _EPOCH_MIN <= value <= _EPOCH_MAX:
        raise CaptureManifestError(f"'{where}' = {value} no es un epoch en segundos plausible")
    return float(value)


def _datetime(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc)


def _relative_file(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaptureManifestError(f"{where} debe contener nombres de fichero")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in value:
        raise CaptureManifestError(f"{where}: '{value}' debe ser relativo a la carpeta de la captura")
    return path.as_posix()
