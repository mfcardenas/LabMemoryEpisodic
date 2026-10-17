"""Registro de ejecución persistente: lo que el vigilante y la cola van haciendo.

Cada línea se guarda en SQLite (el front la consulta aunque la escriba otro
proceso, p. ej. ``epigraph watch``) y se reenvía al logging estándar. Los
pases del vigilante (nivel ``debug``) se podan aparte: llegan cada pocos
segundos y no deben desplazar las líneas de capturas y trabajos.
"""
from __future__ import annotations

import logging
from typing import Any

from episodic.domain.watch import ActivityEntry, ActivityLevel
from episodic.ports.repository import EpisodicRepository

logger = logging.getLogger(__name__)

_LOGGING_LEVEL = {
    ActivityLevel.DEBUG: logging.DEBUG,
    ActivityLevel.INFO: logging.INFO,
    ActivityLevel.WARNING: logging.WARNING,
    ActivityLevel.ERROR: logging.ERROR,
}
_PRUNE_EVERY = 200
_MAIN_LEVELS = [ActivityLevel.INFO, ActivityLevel.WARNING, ActivityLevel.ERROR]


class ActivityLog:
    """Escribe en el registro con la conexión del hilo que lo usa."""

    def __init__(
        self,
        repository: EpisodicRepository,
        max_rows: int = 5000,
        max_debug_rows: int = 1000,
    ) -> None:
        self.repository = repository
        self.max_rows = max_rows
        self.max_debug_rows = max_debug_rows
        self._writes = {False: 0, True: 0}

    def debug(self, component: str, message: str, **refs: Any) -> None:
        self.record(ActivityLevel.DEBUG, component, message, **refs)

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
            debug = level == ActivityLevel.DEBUG
            self._writes[debug] += 1
            keep = self.max_debug_rows if debug else self.max_rows
            if keep > 0 and self._writes[debug] % _PRUNE_EVERY == 0:
                levels = [ActivityLevel.DEBUG] if debug else _MAIN_LEVELS
                self.repository.prune_activity(keep, levels)
        except Exception:  # pragma: no cover - el registro nunca debe tumbar el trabajo
            logger.exception("No se pudo guardar la línea del registro de ejecución")
