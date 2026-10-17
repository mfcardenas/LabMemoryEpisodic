"""Proyecto: un espacio de datos aislado (SQLite, medios, índice y colección de Qdrant).

Cada proyecto guarda sus ambientes y evidencias por separado: un mismo
ambiente (``A001``) puede existir en dos proyectos sin mezclarse. El proyecto
``principal`` son las capturas de siempre; sus rutas salen de la configuración.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from episodic.domain.times import utc_now

DEFAULT_PROJECT_ID = "principal"
PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
# Ids que chocarían con rutas del servidor o con el directorio del registro.
RESERVED_PROJECT_IDS = frozenset({"api", "static", "docs", "p", "proyectos", "nuevo"})


class ProjectError(ValueError):
    """Petición sobre proyectos que no se puede cumplir; el mensaje es para el usuario."""


class ProjectKind(str, Enum):
    REAL = "real"
    TEST = "prueba"


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    data_dir: str
    qdrant_collection: str
    description: str = ""
    kind: ProjectKind = ProjectKind.REAL
    watch_config: str = ""  # fichero de vigilancia; vacío = sin carpetas vigiladas
    active: bool = True  # vigila y procesa; en pausa solo se consulta
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_project_id(self.id)
        if not self.name.strip():
            raise ProjectError("El proyecto necesita un nombre")
        if not self.data_dir or not self.qdrant_collection:
            raise ProjectError("El proyecto necesita directorio de datos y colección")
        if not isinstance(self.kind, ProjectKind):
            object.__setattr__(self, "kind", ProjectKind(self.kind))

    @property
    def is_default(self) -> bool:
        return self.id == DEFAULT_PROJECT_ID


def validate_project_id(value: str) -> str:
    if not PROJECT_ID_PATTERN.match(value or ""):
        raise ProjectError(
            "El identificador del proyecto usa minúsculas, cifras y guiones (2–40 caracteres)"
        )
    if value in RESERVED_PROJECT_IDS:
        raise ProjectError(f"«{value}» está reservado; elija otro identificador")
    return value


def project_id_from_name(name: str) -> str:
    """Identificador legible a partir del nombre: «Pruebas A3 (taller)» → ``pruebas-a3-taller``."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40].strip("-")
    if len(text) < 2:
        text = f"proyecto-{text}" if text else "proyecto"
    if text in RESERVED_PROJECT_IDS:
        text = f"{text}-1"
    return text


def collection_for(project_id: str) -> str:
    """Colección lógica de Qdrant del proyecto, con un prefijo propio."""
    return f"epigraph_p_{project_id.replace('-', '_')}"
