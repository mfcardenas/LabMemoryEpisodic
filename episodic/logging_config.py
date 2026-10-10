"""Configuración de logging compartida por todas las interfaces EpiGraph."""
from __future__ import annotations

import logging
from pathlib import Path


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Configura consola y, opcionalmente, un fichero local de log."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=LOG_FORMAT,
        handlers=handlers,
        force=True,
    )
    logging.getLogger("episodic.runtime").info(
        "Logging inicializado (nivel=%s, fichero=%s)",
        level.upper(),
        log_file or "desactivado",
    )
