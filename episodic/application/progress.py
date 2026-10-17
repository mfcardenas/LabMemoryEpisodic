"""Avisos de avance dentro de una etapa larga: un tramo descrito, un audio
transcrito, una representación indexada...

El trabajo en curso instala un receptor para su hilo y los procesadores
llaman a ``note`` sin saber quién escucha. Sin receptor, ``note`` no hace
nada: los procesadores siguen funcionando fuera de la cola de trabajos.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger(__name__)

ProgressSink = Callable[[str], None]

_sink: ContextVar[ProgressSink | None] = ContextVar("epigraph_progress", default=None)


def note(message: str) -> None:
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink(message)
    except Exception:  # pragma: no cover - avisar nunca debe romper el trabajo
        logger.exception("No se pudo registrar el avance: %s", message)


@contextmanager
def progress_sink(sink: ProgressSink) -> Iterator[None]:
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


@contextmanager
def progress_scope(prefix: str) -> Iterator[None]:
    """Antepone ``prefix`` (p. ej. el fichero en curso) a los avisos internos."""
    outer = _sink.get()
    if outer is None:
        yield
        return
    with progress_sink(lambda message: outer(f"{prefix} · {message}")):
        yield
