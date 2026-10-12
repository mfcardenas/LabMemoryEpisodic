"""Entrada automática: ficheros vistos en las carpetas vigiladas y registro de ejecución."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from episodic.domain.model import (
    DomainValidationError,
    SerializableDomainModel,
    validate_identifier,
    validate_timestamp,
)
from episodic.domain.times import utc_now


class InboxFileState(str, Enum):
    """Ciclo de vida de un fichero en una carpeta vigilada."""

    PENDING = "pending"  # visto, todavía puede estar copiándose
    READY = "ready"  # estable e inspeccionado; espera a que su grupo se cierre
    CAPTURED = "captured"  # forma parte de una captura
    DUPLICATE = "duplicate"  # su contenido ya pertenece a otra captura
    IGNORED = "ignored"  # formato no admitido
    FAILED = "failed"  # el registro de su grupo falló
    VANISHED = "vanished"  # desapareció antes de capturarse


TERMINAL_INBOX_STATES = frozenset(
    {
        InboxFileState.CAPTURED,
        InboxFileState.DUPLICATE,
        InboxFileState.IGNORED,
        InboxFileState.FAILED,
    }
)


@dataclass
class InboxFile(SerializableDomainModel):
    """Un fichero detectado en una fuente vigilada y su destino en la memoria.

    ``changed_at`` es la hora (del vigilante) en que se vio cambiar por última
    vez; con ella se decide si el fichero ya está completo y si su grupo lleva
    el tiempo suficiente sin novedades.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_name: str = ""
    rel_path: str = ""
    group_key: str = ""
    state: InboxFileState = InboxFileState.PENDING
    size_bytes: int = 0
    mtime_ns: int = 0
    changed_at: float = 0.0
    content_hash: str | None = None
    media_type: str | None = None
    capture_id: str | None = None
    present: bool = True  # sigue en la carpeta vigilada
    archived_to: str | None = None
    error: str | None = None
    first_seen_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        validate_identifier(self.source_name, "source_name")
        if not self.rel_path.strip():
            raise DomainValidationError("InboxFile.rel_path es obligatorio")
        validate_timestamp(self.first_seen_at, "first_seen_at")
        validate_timestamp(self.updated_at, "updated_at")


class ActivityLevel(str, Enum):
    DEBUG = "debug"  # pase del vigilante: se poda aparte para no desplazar lo importante
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class ActivityEntry(SerializableDomainModel):
    """Línea del registro de ejecución que se muestra en el front."""

    component: str = ""
    message: str = ""
    level: ActivityLevel = ActivityLevel.INFO
    at: datetime = field(default_factory=utc_now)
    source_name: str | None = None
    path: str | None = None
    capture_id: str | None = None
    job_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    id: int | None = None  # asignado por el repositorio, creciente

    def __post_init__(self) -> None:
        validate_identifier(self.component, "component")
        if not self.message.strip():
            raise DomainValidationError("ActivityEntry.message es obligatorio")
        validate_timestamp(self.at, "at")
