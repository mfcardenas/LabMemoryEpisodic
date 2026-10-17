"""Catálogo de proyectos: crear, editar y borrar espacios de datos aislados.

Cada proyecto nuevo vive en ``<projects_dir>/<id>/`` (SQLite, medios e índice)
con su colección de Qdrant y, si vigila carpetas, su propio ``watch.toml``.
Este servicio decide y valida; abrir, cerrar y borrar los datos lo hace el
gestor de proyectos en ``bootstrap`` (que conoce SQLite y Qdrant).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Optional

from episodic.application.watch_config import WatchConfigError, load_watch_settings
from episodic.domain.projects import (
    DEFAULT_PROJECT_ID,
    Project,
    ProjectError,
    ProjectKind,
    collection_for,
    project_id_from_name,
    validate_project_id,
)
from episodic.ports.projects import ProjectRegistry

logger = logging.getLogger(__name__)

DEFAULT_PROJECT_NAME = "Principal"
WATCH_FILE = "watch.toml"


@dataclass(frozen=True)
class ProjectLayout:
    """Dónde viven los proyectos y qué datos son los del principal (de la configuración)."""

    projects_path: Path
    default_data_dir: Path
    default_collection: str
    default_watch_config: Optional[Path] = None


class ProjectService:
    def __init__(self, registry: ProjectRegistry, layout: ProjectLayout) -> None:
        self.registry = registry
        self.layout = layout
        self.projects_path = layout.projects_path.resolve()

    # -- Consulta -------------------------------------------------------------

    def list(self) -> list[Project]:
        projects = self.registry.list_projects()
        # El principal, siempre primero.
        return sorted(projects, key=lambda project: (not project.is_default, project.created_at))

    def get(self, project_id: str) -> Project:
        project = self.registry.get_project(project_id)
        if project is None:
            raise LookupError(f"No existe el proyecto «{project_id}»")
        return project

    def data_path(self, project: Project) -> Path:
        return Path(project.data_dir).resolve()

    def watch_path(self, project: Project) -> Optional[Path]:
        return Path(project.watch_config).resolve() if project.watch_config else None

    def folders(self, project: Project) -> list[str]:
        """Carpetas que vigila el proyecto (las de su ``watch.toml``)."""
        path = self.watch_path(project)
        if path is None or not path.is_file():
            return []
        try:
            return [str(source.path) for source in load_watch_settings(path).sources]
        except (WatchConfigError, OSError) as exc:
            logger.warning("No se pudo leer la vigilancia de %s: %s", project.id, exc)
            return []

    def stats(self, project: Project) -> dict:
        """Cifras del proyecto leídas de su SQLite en solo lectura (sin abrirlo entero)."""
        db = self.data_path(project) / "episodic.db"
        empty = {"episodes": 0, "environments": 0, "events": 0, "captures": 0, "pending_jobs": 0}
        if not db.is_file():
            return empty
        try:
            conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=2)
        except sqlite3.Error:
            return empty
        try:
            def count(sql: str) -> int:
                try:
                    return int(conn.execute(sql).fetchone()[0])
                except sqlite3.Error:  # tabla aún no creada
                    return 0

            return {
                "episodes": count("SELECT count(*) FROM episodes WHERE is_visible = 1"),
                "environments": count("SELECT count(DISTINCT environment_id) FROM events"),
                "events": count("SELECT count(*) FROM events"),
                "captures": count("SELECT count(*) FROM captures"),
                "pending_jobs": count("SELECT count(*) FROM jobs WHERE state IN ('queued', 'running')"),
            }
        finally:
            conn.close()

    # -- Principal --------------------------------------------------------------

    def ensure_default(self) -> Project:
        """El principal refleja siempre la configuración vigente (datos, colección, vigilancia)."""
        current = self.registry.get_project(DEFAULT_PROJECT_ID)
        project = Project(
            id=DEFAULT_PROJECT_ID,
            name=current.name if current else DEFAULT_PROJECT_NAME,
            description=current.description if current else "Capturas reales de siempre.",
            kind=ProjectKind.REAL,
            data_dir=str(self.layout.default_data_dir.resolve()),
            qdrant_collection=self.layout.default_collection,
            watch_config=str(self.layout.default_watch_config.resolve()) if self.layout.default_watch_config else "",
            active=True,
            **({"created_at": current.created_at} if current else {}),
        )
        if project != current:
            self.registry.save_project(project)
        return project

    # -- Cambios ----------------------------------------------------------------

    def create(
        self,
        name: str,
        *,
        description: str = "",
        kind: ProjectKind | str = ProjectKind.TEST,
        folders: Iterable[str] = (),
        active: bool = True,
        project_id: str | None = None,
    ) -> Project:
        name = (name or "").strip()
        if not name:
            raise ProjectError("El proyecto necesita un nombre")
        if project_id:
            validate_project_id(project_id)
            if self.registry.get_project(project_id) is not None:
                raise ProjectError(f"Ya existe un proyecto con el identificador «{project_id}»")
        else:
            project_id = self._free_id(project_id_from_name(name))
        data_path = self.projects_path / project_id
        if data_path.exists() and any(data_path.iterdir()):
            raise ProjectError(f"El directorio {data_path} ya existe y no está vacío")
        folders = self._validate_folders(folders, project_id)
        project = Project(
            id=project_id,
            name=name,
            description=(description or "").strip(),
            kind=ProjectKind(kind),
            data_dir=str(data_path),
            qdrant_collection=collection_for(project_id),
            watch_config=self._write_watch(data_path, folders),
            active=active,
        )
        self.registry.save_project(project)
        return project

    def update(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        active: bool | None = None,
        folders: Iterable[str] | None = None,
    ) -> Project:
        project = self.get(project_id)
        changes: dict = {}
        if name is not None:
            if not name.strip():
                raise ProjectError("El proyecto necesita un nombre")
            changes["name"] = name.strip()
        if description is not None:
            changes["description"] = description.strip()
        if active is not None:
            if project.is_default and not active:
                raise ProjectError("El proyecto principal no se puede pausar")
            changes["active"] = active
        if folders is not None:
            if project.is_default:
                raise ProjectError(
                    "Las carpetas del principal se configuran en su watch.toml (EPIGRAPH_WATCH_CONFIG)"
                )
            changes["watch_config"] = self._write_watch(
                self.data_path(project), self._validate_folders(folders, project.id)
            )
        updated = replace(project, **changes)
        self.registry.save_project(updated)
        return updated

    def prepare_removal(self, project_id: str, confirm: str) -> Project:
        """Comprueba que el proyecto se puede borrar; el gestor borra sus datos y luego ``forget``."""
        project = self.get(project_id)
        if project.is_default:
            raise ProjectError("El proyecto principal no se puede borrar")
        if confirm != project.id:
            raise ProjectError(f"Para borrar, confirme escribiendo el identificador «{project.id}»")
        data_path = self.data_path(project)
        if self.projects_path not in data_path.parents:
            raise ProjectError(f"Negado: {data_path} está fuera de {self.projects_path}")
        return project

    def forget(self, project_id: str) -> None:
        self.registry.delete_project(project_id)

    # -- Detalles -----------------------------------------------------------------

    def _free_id(self, base: str) -> str:
        candidate, n = base, 2
        while self.registry.get_project(candidate) is not None:
            suffix = f"-{n}"
            candidate, n = f"{base[: 40 - len(suffix)]}{suffix}", n + 1
        return candidate

    def _validate_folders(self, folders: Iterable[str], project_id: str) -> list[Path]:
        """Carpetas existentes, sin repetir y que no vigile ya otro proyecto (ni sus subcarpetas)."""
        chosen: list[Path] = []
        for raw in folders:
            text = (raw or "").strip().strip('"')
            if not text:
                continue
            path = Path(text).expanduser()
            if not path.is_absolute():
                raise ProjectError(f"Use una ruta completa para la carpeta: {text}")
            path = path.resolve()
            if not path.is_dir():
                raise ProjectError(f"La carpeta no existe: {path}")
            if _overlaps(path, self.projects_path) or _overlaps(path, self.layout.default_data_dir.resolve()):
                raise ProjectError(f"No se puede vigilar una carpeta de datos de EpiGraph: {path}")
            if any(_overlaps(path, other) for other in chosen):
                raise ProjectError(f"Carpeta repetida o anidada en la lista: {path}")
            chosen.append(path)
        for other in self.registry.list_projects():
            if other.id == project_id:
                continue
            for folder in self.folders(other):
                other_path = Path(folder).resolve()
                for path in chosen:
                    if _overlaps(path, other_path):
                        raise ProjectError(
                            f"{path} ya la vigila el proyecto «{other.name}» ({other_path}); "
                            "una carpeta solo puede pertenecer a un proyecto"
                        )
        return chosen

    def _write_watch(self, data_path: Path, folders: list[Path]) -> str:
        """``watch.toml`` del proyecto: agrupación por manifiesto y sin mover originales."""
        watch_file = data_path / WATCH_FILE
        if not folders:
            if watch_file.exists():
                watch_file.unlink()
            return ""
        data_path.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Generado por EpiGraph al crear o editar el proyecto; se reescribe al guardarlo.",
            "[watch]",
            "interval_seconds = 5",
            "settle_seconds = 10",
        ]
        names: set[str] = set()
        for folder in folders:
            name, n = folder.name or "carpeta", 2
            while name in names:
                name, n = f"{folder.name}-{n}", n + 1
            names.add(name)
            lines += [
                "",
                "[[sources]]",
                f"name = {json.dumps(name, ensure_ascii=False)}",
                f"path = {json.dumps(folder.as_posix(), ensure_ascii=False)}",
                'grouping = "manifest"',
                'manifest_file = "capture.json"',
                'after = "keep"',
            ]
        watch_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(watch_file)


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents
