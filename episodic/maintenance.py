"""Limpieza de los datos de EpiGraph para repetir cargas de prueba.

Borra lo que esta instalación ha generado: SQLite (la verdad), los medios
custodiados, el índice local y, en el servidor Qdrant, solo la colección
configurada con sus generaciones. No toca la carpeta vigilada ni
``_procesados`` (las cargas las pone la persona a mano), ni la caché de
modelos, ni otras colecciones del servidor.
"""
from __future__ import annotations

import shutil
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from episodic.config import EpisodicConfig


class ResetError(RuntimeError):
    """La limpieza no puede hacerse con seguridad."""


@dataclass
class ResetPlan:
    local_paths: list[Path] = field(default_factory=list)
    qdrant_where: str | None = None
    qdrant_collections: list[str] = field(default_factory=list)
    qdrant_alias: bool = False
    # Informativo: carpetas pendientes en la entrada y ya archivadas, por fuente.
    inbox_pending: dict[str, int] = field(default_factory=dict)
    archived: dict[str, tuple[Path, int]] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.local_paths or self.qdrant_collections or self.qdrant_alias)

    def describe(self) -> list[str]:
        lines = [f"  · {path}" for path in self.local_paths]
        if self.qdrant_where:
            if self.qdrant_collections or self.qdrant_alias:
                names = ", ".join(self.qdrant_collections) or "(solo el alias)"
                lines.append(f"  · Qdrant {self.qdrant_where}: {names}")
            else:
                lines.append(f"  · Qdrant {self.qdrant_where}: nada de EpiGraph")
        return lines


def server_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    probe_host = "127.0.0.1" if host in {"0.0.0.0", ""} else host
    with socket.socket() as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((probe_host, port)) == 0


def _qdrant_client(config: EpisodicConfig) -> Any:
    from qdrant_client import QdrantClient

    return QdrantClient(host=config.qdrant_host, port=config.qdrant_port)


def plan_reset(config: EpisodicConfig, client: Optional[Any] = None) -> tuple[ResetPlan, Any]:
    """Qué se borraría, sin borrar nada. Falla si EpiGraph está en marcha o
    si el servidor Qdrant no responde (quedarían vectores huérfanos)."""
    if server_listening(config.host, config.port):
        raise ResetError(
            f"EpiGraph sigue en marcha en {config.host}:{config.port}: párelo antes de limpiar."
        )
    plan = ResetPlan()
    sqlite = config.sqlite_path
    candidates = [sqlite, Path(f"{sqlite}-wal"), Path(f"{sqlite}-shm"), config.media_dir, config.index_dir]
    plan.local_paths = [path for path in candidates if path.exists()]

    if config.vector_backend == "http":
        from episodic.adapters.qdrant.index import logical_collection_members

        plan.qdrant_where = (
            f"{config.qdrant_host}:{config.qdrant_port}, colección {config.qdrant_collection}"
        )
        try:
            client = client or _qdrant_client(config)
            plan.qdrant_collections, plan.qdrant_alias = logical_collection_members(
                client, config.qdrant_collection
            )
        except Exception as exc:
            raise ResetError(
                f"No se puede consultar Qdrant en {config.qdrant_host}:{config.qdrant_port}: {exc}. "
                "Arránquelo (docker compose up -d qdrant) antes de limpiar."
            ) from exc

    _describe_inbox(config, plan)
    return plan, client


def _describe_inbox(config: EpisodicConfig, plan: ResetPlan) -> None:
    if config.watch_config_path is None or not config.watch_config_path.exists():
        return
    from episodic.application.watch_config import load_watch_settings

    try:
        settings = load_watch_settings(config.watch_config_path)
    except Exception:
        return
    for source in settings.sources:
        if source.path.is_dir():
            plan.inbox_pending[source.name] = sum(
                1
                for item in source.path.iterdir()
                if item != source.archive_dir and not item.name.startswith((".", "_"))
            )
        if source.archive_dir and source.archive_dir.is_dir():
            plan.archived[source.name] = (source.archive_dir, sum(1 for _ in source.archive_dir.iterdir()))


def apply_reset(
    plan: ResetPlan,
    config: EpisodicConfig,
    client: Optional[Any] = None,
    log: Callable[[str], None] = print,
) -> None:
    """Borra lo planificado: primero lo local (si un fichero está abierto,
    se detiene antes de tocar Qdrant) y después la colección."""
    for path in plan.local_paths:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except PermissionError as exc:
            raise ResetError(f"No se puede borrar {path} (¿EpiGraph sigue abierto?): {exc}") from exc
        log(f"Borrado {path}")

    if plan.qdrant_collections or plan.qdrant_alias:
        from episodic.adapters.qdrant.index import drop_logical_collection

        client = client or _qdrant_client(config)
        for name in drop_logical_collection(client, config.qdrant_collection):
            log(f"Qdrant: colección {name} borrada")
