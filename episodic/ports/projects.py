"""Contrato del catálogo de proyectos (fuera de los datos de cada proyecto)."""
from __future__ import annotations

from typing import List, Optional, Protocol

from episodic.domain.projects import Project


class ProjectRegistry(Protocol):
    def list_projects(self) -> List[Project]: ...

    def get_project(self, project_id: str) -> Optional[Project]: ...

    def save_project(self, project: Project) -> None: ...

    def delete_project(self, project_id: str) -> None: ...

    def close(self) -> None: ...
