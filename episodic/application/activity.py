"""Registro de ejecución persistente: lo que el vigilante y la cola van haciendo.

Cada línea se guarda en SQLite (el front la consulta aunque la escriba otro
proceso, p. ej. ``epigraph watch``) y se reenvía al logging estándar.
"""
from __future__ import annotations

import logging
from typing import Any

from episodic.domain.watch import ActivityEntry, ActivityLevel
from episodic.ports.repository import EpisodicRepository

logger = logging.getLogger(__name__)

_LOGGING_LEVEL = {
    ActivityLevel.INFO: logging.INFO,
    ActivityLevel.WARNING: logging.WARNING,
    ActivityLevel.ERROR: logging.ERROR,
}
_PRUNE_EVERY = 200


class ActivityLog:
    """Escribe en el registro con la conexión del hilo que lo usa."""

    def __init__(self, repository: EpisodicRepository, max_rows: int = 5000) -> None:
        self.repository = repository
        self.max_rows = max_rows
        self._writes = 0

    def info(self, component: str, message: str, **refs: Any) -> None:
        self.record(ActivityLevel.INFO, component, message, **refs)

    def warning(self, component: str, message: str, **refs: Any) -> None:
        self.record(ActivityLevel.WARNING, component, message, **refs)

    def error(self, component: str, message: str, **refs: Any) -> None:
        self.record(ActivityLevel.ERROR, component, message, **refs)

    def record(
        self,
        level: ActivityLevel,
        component: str,
        message: str,
        *,
        source_name: str | None = None,
        path: str | None = None,
        capture_id: str | None = None,
        job_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        logger.log(_LOGGING_LEVEL[level], "[%s] %s", component, message)
        entry = ActivityEntry(
            component=component,
            message=message,
            level=level,
            source_name=source_name,
            path=path,
            capture_id=capture_id,
            job_id=job_id,
            data=data or {},
        )
        try:
            self.repository.append_activity(entry)
            self._writes += 1
            if self.max_rows > 0 and self._writes % _PRUNE_EVERY == 0:
                self.repository.prune_activity(self.max_rows)
        except Exception:  # pragma: no cover - el registro nunca debe tumbar el trabajo
            logger.exception("No se pudo guardar la línea del registro de ejecución")
