"""Servidor con proyectos: catálogo en ``/api/projects`` y una app por proyecto.

``/p/<id>/api/...`` llega a la app del proyecto ``<id>`` (los mismos endpoints
de ``server.py``, sin cambiarlos); ``/api/...`` sin prefijo es el proyecto
principal, como antes. La consola web vive en ``/`` (principal) y en
``/p/<id>/``; el front deduce el proyecto de la URL.
"""
from __future__ import annotations

import logging
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from anyio import to_thread
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from episodic.bootstrap import ProjectHub
from episodic.config import EpisodicConfig
from episodic.domain.projects import DEFAULT_PROJECT_ID, Project, ProjectError, ProjectKind
from episodic.interfaces.http.server import create_api_app

logger = logging.getLogger("episodic.http.projects")

_PROJECT_PATH = re.compile(r"^/p/([^/]+)(/.*)?$")
_PROJECT_ROUTES = ("/docs", "/redoc", "/openapi.json")


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    kind: ProjectKind = ProjectKind.TEST
    folders: List[str] = Field(default_factory=list, description="carpetas a vigilar (rutas completas)")
    active: bool = True
    id: Optional[str] = Field(default=None, description="identificador; si falta, sale del nombre")


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    active: Optional[bool] = None
    folders: Optional[List[str]] = None


def _is_project_route(path: str) -> bool:
    return path.startswith("/api/") or path in _PROJECT_ROUTES or path.startswith("/docs/")


def create_projects_app(
    config: Optional[EpisodicConfig] = None,
    hub: Optional[ProjectHub] = None,
    *,
    use_fake_encoders: bool = False,
    start_watchers: bool = True,
):
    """App ASGI con proyectos. Con ``hub`` dado (tests), no lo crea ni lo cierra."""
    owns_hub = hub is None
    if hub is None:
        hub = ProjectHub(config, use_fake_encoders=use_fake_encoders, start_watchers=start_watchers)

    apps: dict[str, FastAPI] = {}
    apps_lock = threading.Lock()

    def forget_app(project_id: str) -> None:
        with apps_lock:
            apps.pop(project_id, None)

    hub.on_close.append(forget_app)

    def app_for(project_id: str) -> FastAPI:
        with apps_lock:
            app = apps.get(project_id)
            if app is None:
                app = create_api_app(
                    container=hub.container(project_id), manage_services=False, serve_web=False
                )
                apps[project_id] = app
            return app

    def describe(project: Project) -> dict:
        return {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "kind": project.kind.value,
            "active": project.active,
            "running": hub.is_running(project.id),
            "is_default": project.is_default,
            "data_dir": project.data_dir,
            "qdrant_collection": project.qdrant_collection,
            "folders": hub.projects.folders(project),
            "created_at": project.created_at.isoformat(),
            "stats": hub.projects.stats(project),
        }

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await to_thread.run_sync(hub.start)
        try:
            yield
        finally:
            if owns_hub:
                await to_thread.run_sync(hub.close)

    root = FastAPI(
        title="EpiGraph · proyectos",
        description="Catálogo de proyectos. La API de cada proyecto está en /p/<id>/docs.",
        lifespan=lifespan,
        docs_url="/api/projects/docs",
        openapi_url="/api/projects/openapi.json",
        redoc_url=None,
    )
    root.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
    )

    def lookup(project_id: str) -> Project:
        try:
            return hub.projects.get(project_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @root.get("/api/projects")
    def list_projects():
        return {"default": DEFAULT_PROJECT_ID, "projects": [describe(item) for item in hub.projects.list()]}

    @root.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        return describe(lookup(project_id))

    @root.post("/api/projects", status_code=201)
    def create_project(req: ProjectCreateRequest):
        try:
            project = hub.projects.create(
                req.name,
                description=req.description,
                kind=req.kind,
                folders=req.folders,
                active=req.active,
                project_id=req.id,
            )
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if project.active:
            hub.activate(project.id)
        return describe(project)

    @root.patch("/api/projects/{project_id}")
    def update_project(project_id: str, req: ProjectUpdateRequest):
        before = lookup(project_id)
        try:
            project = hub.projects.update(
                project_id, name=req.name, description=req.description, active=req.active, folders=req.folders
            )
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if project.watch_config != before.watch_config or req.folders is not None:
            hub.reload(project_id)  # el vigilante se rehace con las carpetas nuevas
        if project.active:
            hub.activate(project_id)
        else:
            hub.pause(project_id)
        return describe(project)

    @root.delete("/api/projects/{project_id}")
    def delete_project(project_id: str, confirm: str = Query("", description="el identificador, para confirmar")):
        lookup(project_id)
        try:
            return hub.delete(project_id, confirm)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    web_dir = Path(__file__).parent.parent / "web"
    index_path = web_dir / "index.html"
    if web_dir.exists():
        root.mount("/static", StaticFiles(directory=str(web_dir)), name="web_static")

        @root.get("/")
        def serve_index():
            if index_path.exists():
                return FileResponse(index_path, media_type="text/html")
            return JSONResponse({"message": "Memory Episodic API activa"})

        @root.get("/p/{project_id}")
        def project_slash(project_id: str):
            return RedirectResponse(f"/p/{project_id}/")

        @root.get("/p/{project_id}/")
        def serve_project_index(project_id: str):
            lookup(project_id)
            return FileResponse(index_path, media_type="text/html")

    async def router(scope, receive, send):
        """Envía cada petición a la app de su proyecto; el resto, al catálogo."""
        if scope["type"] in ("http", "websocket"):
            path = scope["path"]
            target = prefix = None
            match = _PROJECT_PATH.match(path)
            if match and _is_project_route(match.group(2) or "/"):
                target, prefix = match.group(1), f"/p/{match.group(1)}"
            elif not match and _is_project_route(path) and not path.startswith("/api/projects"):
                target, prefix = DEFAULT_PROJECT_ID, ""
            if target is not None:
                try:
                    app = await to_thread.run_sync(app_for, target)
                except LookupError as exc:
                    response = JSONResponse({"detail": str(exc)}, status_code=404)
                    await response(scope, receive, send)
                    return
                child = dict(scope)
                # Como un Mount: la ruta completa se conserva y root_path lleva el prefijo.
                child["root_path"] = scope.get("root_path", "") + prefix
                await app(child, receive, send)
                return
        await root(scope, receive, send)

    router.hub = hub  # type: ignore[attr-defined]
    router.root = root  # type: ignore[attr-defined]
    return router
