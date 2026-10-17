"""Catálogo de proyectos en su propio fichero SQLite.

Vive fuera de los datos de cada proyecto (``epigraph-projects/proyectos.db``):
borrar un proyecto no toca el catálogo de los demás.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from episodic.domain.projects import Project, ProjectKind

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    data_dir TEXT NOT NULL,
    qdrant_collection TEXT NOT NULL,
    watch_config TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
"""


class SqliteProjectRegistry:
    def __init__(self, db_path: Union[str, Path]) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def list_projects(self) -> List[Project]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM projects ORDER BY created_at, id").fetchall()
        return [_project(row) for row in rows]

    def get_project(self, project_id: str) -> Optional[Project]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return _project(row) if row else None

    def save_project(self, project: Project) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO projects (id, name, description, kind, data_dir, qdrant_collection,"
                " watch_config, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET name = excluded.name,"
                " description = excluded.description, kind = excluded.kind,"
                " data_dir = excluded.data_dir, qdrant_collection = excluded.qdrant_collection,"
                " watch_config = excluded.watch_config, active = excluded.active",
                (
                    project.id, project.name, project.description, project.kind.value,
                    project.data_dir, project.qdrant_collection, project.watch_config,
                    int(project.active), project.created_at.isoformat(),
                ),
            )

    def delete_project(self, project_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def _project(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        kind=ProjectKind(row["kind"]),
        data_dir=row["data_dir"],
        qdrant_collection=row["qdrant_collection"],
        watch_config=row["watch_config"],
        active=bool(row["active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )
