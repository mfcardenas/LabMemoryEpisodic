"""Servidor FastAPI para la API REST del sistema de Memoria Episódica."""
from __future__ import annotations

import logging
import mimetypes
import threading
from contextlib import asynccontextmanager
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from episodic.application.captures import (
    CAPTURE_JOB_KIND,
    CAPTURE_STAGES,
    CaptureConflictError,
    CaptureFile,
)
from episodic.application.index_maintenance import REINDEX_JOB_KIND, TRIGGER_MANUAL
from episodic.application.summaries import SUMMARY_JOB_KIND, SUMMARY_STAGES
from episodic.application.passage_search import DEFAULT
from episodic.application.watch import (
    ACTIVITY_COMPONENT as WATCH_COMPONENT,
    LEASE_NAME,
    draft_group_manifest,
    retry_failed_group,
    write_manifest,
)
from episodic.bootstrap import AppContainer, create_app_container, submit_reindex
from episodic.config import EpisodicConfig
from episodic.domain.captures import JobState
from episodic.domain.watch import ActivityLevel, InboxFileState
from episodic.domain.times import Interval, TimeSpec
from episodic.domain.retrieval import HybridSearchFilters
from episodic.domain.types import (
    EvidenceMediaType,
    EvidenceState,
    ProvenanceKind,
    SearchIntent,
    SourceType,
    ValidationState,
)
from episodic.interfaces.http.schemas import (
    AnnotationCreateRequest,
    HealthResponse,
    HybridQueryRunResponse,
    IndexingSummaryResponse,
    PassageSearchRequest,
    ProjectComponentStatus,
    ProjectStatusResponse,
    QueryRunResponse,
    QueryResultDTO,
    RebuildIndexResponse,
    SearchRequest,
    SummaryRegenerateRequest,
)
from episodic.tools.contracts import MemoryToolOutput, ToolDescriptor

logger = logging.getLogger("episodic.http")


def create_api_app(
    config: Optional[EpisodicConfig] = None,
    container: Optional[AppContainer] = None,
    use_fake_encoders: bool = False,
    start_watcher: bool = True,
) -> FastAPI:
    """Crea y configura la aplicación FastAPI con todas las rutas episódicas.

    Si el contenedor trae vigilante (``EPIGRAPH_WATCH_CONFIG``) y
    ``start_watcher`` es cierto, las carpetas se vigilan mientras el servidor
    está en marcha.
    """
    if container is None:
        container = create_app_container(config, use_fake_encoders=use_fake_encoders)

    job_runner = getattr(container, "job_runner", None)
    watcher = getattr(container, "watcher", None)

    def warm_up_search() -> None:
        try:
            container.passage_search.warm_up()
            logger.info("Modelos de búsqueda cargados")
        except Exception as exc:  # la búsqueda lo intentará de nuevo y lo dirá
            logger.warning("No se pudieron precargar los modelos de búsqueda: %s", exc)

    def ensure_index_current() -> None:
        """Si el índice no refleja la configuración vigente, se reindexa solo."""
        maintenance = getattr(container, "index_maintenance", None)
        if maintenance is None:
            return
        try:
            maintenance.ensure_current(lambda trigger: submit_reindex(container, trigger))
        except Exception as exc:  # comprobarlo nunca debe impedir arrancar
            logger.warning("No se pudo comprobar si el índice está al día: %s", exc)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if getattr(container, "passage_search", None) is not None:
            threading.Thread(target=warm_up_search, name="epigraph-warmup", daemon=True).start()
        # La cola reanuda al arrancar los trabajos que quedaron pendientes.
        if job_runner is not None:
            job_runner.start()
            ensure_index_current()
        if watcher is not None and start_watcher:
            watcher.start()
        try:
            yield
        finally:
            if watcher is not None:
                watcher.stop()
            if job_runner is not None:
                job_runner.stop()

    app = FastAPI(
        title="Memory Episodic API",
        version="0.1.0",
        description="Sistema de memoria episódica multimodal, auditable y basada en evidencia.",
        lifespan=lifespan,
    )

    # CORS para facilitar desarrollo y frontend local
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def active_vector_generation() -> Optional[str]:
        """Devuelve el identificador físico activo sin acoplar la API a un adaptador."""
        edge_generation = getattr(container.vector_index, "generation_name", None)
        if edge_generation:
            return str(edge_generation)

        client = getattr(container.vector_index, "client", None)
        collection_name = getattr(container.vector_index, "collection_name", None)
        if client is None or collection_name is None:
            return None
        try:
            for alias in client.get_aliases().aliases:
                if alias.alias_name == collection_name:
                    return str(alias.collection_name)
        except Exception:
            logger.debug("No se pudo resolver la colección física activa", exc_info=True)
        return str(collection_name)

    def image_evidence_path(evidence_id: Optional[str]) -> Optional[str]:
        """Resuelve una evidencia de imagen vigente; nunca acepta rutas del cliente."""
        if not evidence_id:
            return None
        evidence = container.repository.get_evidence(evidence_id)
        if evidence is None or evidence.state == EvidenceState.DELETED or not evidence.internal_uri:
            raise HTTPException(status_code=404, detail="Evidencia de imagen no encontrada")
        if evidence.media_type != EvidenceMediaType.IMAGE:
            raise HTTPException(status_code=422, detail="La evidencia de consulta debe ser una imagen")
        try:
            path = container.evidence_store.resolve_path(evidence.internal_uri)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Evidencia de imagen no encontrada") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Archivo de evidencia no disponible")
        return str(path)

    def query_run_response(run) -> QueryRunResponse:
        results_dto = []
        for result in run.results:
            episode = container.repository.get_episode(result.episode_id)
            interval = episode.interval if episode else None
            start = interval.start.absolute if interval and interval.start else None
            end = interval.end.absolute if interval and interval.end else None
            timeline_kind = "unknown"
            clock_source = None
            if start is not None and end is not None:
                timeline_kind = "point" if start == end else "interval"
                clock_source = interval.start.clock_source or interval.end.clock_source
                if not clock_source and episode:
                    source_clocks = {
                        source.clock_source
                        for source_id in episode.source_ids
                        if (source := container.repository.get_source(source_id))
                        and source.clock_source
                    }
                    if source_clocks:
                        clock_source = ",".join(sorted(source_clocks))

            results_dto.append(
                QueryResultDTO(
                    episode_id=result.episode_id,
                    external_id=episode.external_id if episode else None,
                    title=episode.title if episode else "",
                    score=result.score,
                    rank=result.rank,
                    space=result.space.value if result.space else None,
                    evidence_ids=result.evidence_ids,
                    verdict=result.verdict.value,
                    reasons=result.reasons,
                    timeline_start=start,
                    timeline_end=end,
                    timeline_kind=timeline_kind,
                    timeline_clock_source=clock_source,
                )
            )

        return QueryRunResponse(
            id=run.id,
            intent=run.intent.value,
            text_query=run.text_query,
            overall_verdict=run.overall_verdict.value,
            reasons=run.reasons,
            results=results_dto,
            duration_ms=run.duration_ms,
        )

    def health_response() -> HealthResponse:
        episodes, _ = container.repository.list_episodes(limit=10000)
        sources = container.repository.list_sources()
        entities = container.repository.list_entities()
        return HealthResponse(
            status="ok",
            version="0.1.0",
            episodes_count=len(episodes),
            sources_count=len(sources),
            entities_count=len(entities),
            qdrant_points_count=container.vector_index.count(),
            vector_backend=container.config.vector_backend,
            collection_name=container.config.qdrant_collection,
            active_generation=active_vector_generation(),
            text_model=container.config.nomic_model,
            image_model=container.config.clip_vision_model,
        )

    # -----------------------------------------------------------------------
    # Endpoints de Sistema y Salud
    # -----------------------------------------------------------------------

    @app.get("/api/health", response_model=HealthResponse)
    def health():
        return health_response()

    @app.get("/api/indexing/summary", response_model=IndexingSummaryResponse)
    def indexing_summary():
        return IndexingSummaryResponse(**container.repository.get_indexing_summary())

    @app.get("/api/project-status", response_model=ProjectStatusResponse)
    def project_status():
        """Estado verificable del producto; presencia no implica integración."""
        project_root = Path(__file__).resolve().parents[3]
        episodic_present = (project_root / "episodic" / "bootstrap.py").is_file()
        legacy_rag_present = all(
            (project_root / "legacy" / "rag" / path).is_file()
            for path in ("agent/rag_agent.py", "api/server.py", "storage/qdrant_store.py")
        )
        integrated_robot_present = (project_root / "robot" / "robot.py").is_file()
        reference_robot_present = (
            project_root
            / "sc-on-device-ai-memory"
            / "sc-on-device-ai-memory"
            / "robot"
        ).is_dir()

        components = [
            ProjectComponentStatus(
                id="episodic",
                label="Memoria episódica",
                state="active",
                present=episodic_present,
                integrated=True,
                detail="Producto activo: SQLite canónico, recuperación y proyección vectorial.",
            ),
            ProjectComponentStatus(
                id="legacy_rag",
                label="RAG legado",
                state="isolated" if legacy_rag_present else "missing",
                present=legacy_rag_present,
                integrated=False,
                detail="Código conservado en legacy/rag, aislado del contrato de memoria episódica.",
            ),
            ProjectComponentStatus(
                id="robot",
                label="Robot de referencia",
                state=(
                    "integrated"
                    if integrated_robot_present
                    else "reference_only" if reference_robot_present else "missing"
                ),
                present=integrated_robot_present or reference_robot_present,
                integrated=integrated_robot_present,
                detail=(
                    "Integrado en la raíz del producto."
                    if integrated_robot_present
                    else "Disponible solo como referencia; queda fuera de PC-EG1."
                ),
            ),
        ]
        return ProjectStatusResponse(
            checkpoint="PC-EG1",
            checkpoint_label="Flujo vertical canónico y trazable",
            runtime=health_response(),
            indexing=IndexingSummaryResponse(
                **container.repository.get_indexing_summary()
            ),
            components=components,
        )

    # -----------------------------------------------------------------------
    # Fuentes y Entidades
    # -----------------------------------------------------------------------

    @app.get("/api/sources")
    def list_sources():
        return container.repository.list_sources()

    @app.get("/api/environments")
    def list_environments():
        return container.repository.list_environments()

    @app.get("/api/entities")
    def list_entities():
        return container.repository.list_entities()

    # -----------------------------------------------------------------------
    # Flujo canónico e inspección de memoria (PC-EG1)
    # -----------------------------------------------------------------------

    @app.post("/api/workflow/ingest-and-process")
    def ingest_and_process(
        file: UploadFile = File(...),
        environment_id: str = Form(...),
        source_id: str = Form(...),
        source_type: Optional[str] = Form(None),
        captured_at: Optional[datetime] = Form(None),
        window_duration_seconds: float = Form(60.0, gt=0),
        overlap_seconds: float = Form(5.0, ge=0),
    ):
        incoming_dir = container.config.data_path / ".incoming"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        original_name = Path(file.filename or "asset.bin").name
        suffix = Path(original_name).suffix
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=incoming_dir, suffix=suffix, delete=False
            ) as temporary:
                shutil.copyfileobj(file.file, temporary)
                temporary_path = Path(temporary.name)
            resolved_source_type = SourceType(source_type) if source_type else None
            result = container.workflow_service.ingest_and_process(
                temporary_path,
                environment_id=environment_id.strip(),
                source_id=source_id.strip(),
                source_type=resolved_source_type,
                captured_at=captured_at,
                window_duration_seconds=window_duration_seconds,
                overlap_seconds=overlap_seconds,
                original_filename=original_name,
            )
            detail = container.inspection_service.asset_detail(result.asset_id)
            return {
                "status": "completed" if not result.processor_failures else "completed_with_failures",
                "workflow": {
                    "asset_id": result.asset_id,
                    "duplicate": result.duplicate,
                    "processing_window_ids": list(result.processing_window_ids),
                    "observation_ids": list(result.observation_ids),
                    "processor_failures": list(result.processor_failures),
                    "microepisode_ids": list(result.microepisode_ids),
                    "episode_ids": list(result.episode_ids),
                    "indexed_points": result.indexed_points,
                },
                "asset": detail,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            file.file.close()
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    # -----------------------------------------------------------------------
    # Capturas (una subida conjunta = un episodio) y trabajos asíncronos
    # -----------------------------------------------------------------------

    def job_response(job) -> Dict[str, Any]:
        return {**job.to_dict(), "progress": job.progress}

    def capture_response(capture) -> Dict[str, Any]:
        jobs = container.repository.list_jobs(target_id=capture.id)
        assets = [
            asset
            for asset_id in capture.asset_ids
            if (asset := container.repository.get_asset(asset_id)) is not None
        ]
        return {
            **capture.to_dict(),
            "assets": [
                {
                    "id": asset.id,
                    "original_filename": asset.original_filename,
                    "media_type": asset.media_type.value,
                    "mime_type": asset.mime_type,
                    "source_id": asset.source_id,
                    "duration_seconds": asset.duration_seconds,
                }
                for asset in assets
            ],
            "job": job_response(jobs[-1]) if jobs else None,
        }

    @app.post("/api/captures", status_code=202)
    def create_capture(
        files: List[UploadFile] = File(...),
        environment_id: str = Form(...),
        title: Optional[str] = Form(None),
        captured_at: Optional[datetime] = Form(None),
        window_duration_seconds: float = Form(60.0, gt=0),
        overlap_seconds: float = Form(5.0, ge=0),
        audio_offset_seconds: float = Form(0.0),
    ):
        if job_runner is None:
            raise HTTPException(status_code=503, detail="Cola de trabajos no disponible")
        if not environment_id.strip():
            raise HTTPException(status_code=422, detail="environment_id es obligatorio")
        incoming_dir = container.config.data_path / ".incoming"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        temporary_paths: List[Path] = []
        try:
            capture_files: List[CaptureFile] = []
            for upload in files:
                original_name = Path(upload.filename or "archivo.bin").name
                with tempfile.NamedTemporaryFile(
                    dir=incoming_dir, suffix=Path(original_name).suffix, delete=False
                ) as temporary:
                    shutil.copyfileobj(upload.file, temporary)
                    temporary_paths.append(Path(temporary.name))
                capture_files.append(CaptureFile(temporary_paths[-1], original_name))
            capture = container.capture_service.register(
                capture_files,
                environment_id=environment_id,
                title=title,
                captured_at=captured_at,
                window_duration_seconds=window_duration_seconds,
                overlap_seconds=overlap_seconds,
                audio_offset_seconds=audio_offset_seconds,
                origin={"kind": "upload"},
            )
        except CaptureConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            for upload in files:
                upload.file.close()
            for path in temporary_paths:
                path.unlink(missing_ok=True)
        job_runner.submit(CAPTURE_JOB_KIND, capture.id, CAPTURE_STAGES)
        return capture_response(capture)

    @app.get("/api/captures")
    def list_captures(
        environment_id: Optional[str] = None,
        limit: int = Query(50, ge=1, le=200),
    ):
        return {
            "captures": [
                capture_response(item)
                for item in container.repository.list_captures(environment_id, limit)
            ]
        }

    @app.get("/api/captures/{capture_id}")
    def get_capture(capture_id: str):
        capture = container.repository.get_capture(capture_id)
        if capture is None:
            raise HTTPException(status_code=404, detail="Captura no encontrada")
        return capture_response(capture)

    @app.get("/api/jobs")
    def list_jobs(limit: int = Query(30, ge=1, le=200)):
        """Trabajos recientes con la captura a la que pertenecen."""
        jobs = []
        for job in container.repository.list_recent_jobs(limit):
            capture = container.repository.get_capture(job.target_id)
            jobs.append(
                {
                    **job_response(job),
                    "title": capture.title
                    if capture
                    else "Reindexado de la memoria"
                    if job.job_type == REINDEX_JOB_KIND
                    else job.target_id,
                    "capture": {
                        "id": capture.id,
                        "title": capture.title,
                        "environment_id": capture.environment_id,
                        "status": capture.status.value,
                        "episode_id": capture.episode_id,
                        "asset_count": len(capture.asset_ids),
                        "origin": capture.metadata.get("origin", {}),
                    }
                    if capture
                    else None,
                }
            )
        active = container.repository.list_jobs(states=[JobState.QUEUED, JobState.RUNNING])
        return {"jobs": jobs, "active_count": len(active)}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = container.repository.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Trabajo no encontrado")
        return job_response(job)

    # -----------------------------------------------------------------------
    # Entrada automática (carpetas vigiladas) y registro de ejecución
    # -----------------------------------------------------------------------

    @app.get("/api/watch")
    def watch_status():
        lease = container.repository.get_lease(LEASE_NAME)
        holder = None
        if lease is not None:
            owner, expires_at = lease
            holder = {
                "owner": owner,
                "expires_at": datetime.fromtimestamp(expires_at).astimezone().isoformat(),
                "active": expires_at > time.time(),
                "this_process": watcher is not None and owner == watcher.owner,
            }
        if watcher is None:
            return {
                "configured": False,
                "config_path": None,
                "lease": holder,
                "sources": [],
            }
        runtime = watcher.status()
        return {
            "configured": True,
            "config_path": str(watcher.settings.config_path or ""),
            "interval_seconds": watcher.settings.interval_seconds,
            "settle_seconds": watcher.settings.settle_seconds,
            "running": runtime["running"],
            "has_lease": runtime["has_lease"],
            "last_scan_at": runtime["last_scan_at"],
            "last_error": runtime["last_error"],
            "pass_count": runtime["pass_count"],
            "lease": holder,
            "sources": [
                {
                    **source.to_dict(),
                    **runtime["sources"].get(source.name, {}),
                    "counts": container.repository.count_inbox_files(source.name),
                }
                for source in watcher.settings.sources
            ],
        }

    @app.post("/api/watch/scan", status_code=202)
    def watch_scan_now():
        if watcher is None or not watcher.running:
            raise HTTPException(status_code=409, detail="La vigilancia no está en marcha en este servidor")
        watcher.request_scan()
        return {"requested": True}

    @app.get("/api/watch/files")
    def watch_files(
        source: Optional[str] = None,
        state: Optional[List[InboxFileState]] = Query(None),
        limit: int = Query(200, ge=1, le=1000),
    ):
        files = container.repository.list_inbox_files(source, state, limit)
        return {"files": [item.to_dict() for item in files]}

    @app.post("/api/watch/retry")
    def watch_retry(source: str = Form(...), group_key: str = Form(...)):
        reset = retry_failed_group(
            container.repository, container.activity_log, source, group_key
        )
        if not reset:
            raise HTTPException(status_code=404, detail="No hay ficheros fallidos en ese grupo")
        if watcher is not None and watcher.running:
            watcher.request_scan()
        return {"reset": reset}

    @app.post("/api/watch/manifest")
    def watch_manifest(
        source: str = Form(...), group_key: str = Form(...), write: bool = Form(False)
    ):
        """Propone (o escribe, con ``write``) el manifiesto de una carpeta que
        espera el suyo: para cargas manuales sin la herramienta de captura."""
        if watcher is None:
            raise HTTPException(status_code=404, detail="Este servidor no tiene vigilancia configurada")
        selected = next((item for item in watcher.settings.sources if item.name == source), None)
        if selected is None:
            raise HTTPException(status_code=404, detail=f"Fuente desconocida: {source}")
        try:
            target, manifest = draft_group_manifest(container.repository, selected, group_key)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if write:
            write_manifest(target, manifest)
            container.activity_log.info(
                WATCH_COMPONENT,
                f"Manifiesto creado a mano para {target.parent.name}: "
                f"{len(manifest['devices'])} dispositivo(s) deducidos del nombre y de los ficheros",
                source_name=source,
                path=target.parent.name,
                data={"manifest": manifest},
            )
            if watcher.running:
                watcher.request_scan()
        return {"path": str(target), "manifest": manifest, "written": write}

    @app.get("/api/activity")
    def list_activity(
        after_id: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
        component: Optional[str] = None,
        level: Optional[List[ActivityLevel]] = Query(None),
    ):
        entries = container.repository.list_activity(after_id, limit, component, level)
        return {
            "entries": [entry.to_dict() for entry in entries],
            "last_id": entries[-1].id if entries else after_id,
        }

    @app.get("/api/memory/assets")
    def list_memory_assets(environment_id: Optional[str] = None):
        return {"assets": container.inspection_service.list_assets(environment_id)}

    @app.get("/api/memory/assets/{asset_id}")
    def get_memory_asset(asset_id: str):
        try:
            return container.inspection_service.asset_detail(asset_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/memory/assets/{asset_id}/file")
    def get_memory_asset_file(asset_id: str):
        asset = container.repository.get_asset(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset no encontrado")
        path = Path(asset.uri)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Archivo original no disponible")
        return FileResponse(
            path,
            media_type=asset.mime_type,
            filename=asset.original_filename or path.name,
        )

    @app.get("/api/memory/events/{event_id}/keyframes/{index}")
    def get_event_keyframe(event_id: str, index: int):
        event = container.repository.get_event(event_id)
        if event is None or not 0 <= index < len(event.keyframes):
            raise HTTPException(status_code=404, detail="Fotograma clave no encontrado")
        try:
            path = container.evidence_store.resolve_path(event.keyframes[index]["internal_uri"])
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Fotograma clave no encontrado") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Fotograma clave no disponible")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/memory/episodes/{episode_id}")
    def inspect_memory_episode(episode_id: str):
        try:
            return container.inspection_service.episode_detail(episode_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/memory/timeline")
    def inspect_memory_timeline(
        environment_id: Optional[str] = None,
        source_id: Optional[str] = None,
    ):
        return container.inspection_service.timeline(
            environment_id=environment_id,
            source_id=source_id,
        )

    # -----------------------------------------------------------------------
    # Ingesta e Indexación
    # -----------------------------------------------------------------------

    @app.post("/api/import")
    def import_manifest(manifest: Optional[Dict[str, Any]] = None):
        try:
            if not manifest or "episodes" not in manifest:
                default_manifest = container.config.default_manifest_path
                if default_manifest.exists():
                    stats = container.ingest_service.ingest_manifest(default_manifest)
                else:
                    raise ValueError("No se proporcionó manifiesto ni existe el predeterminado en data/atlas/manifest.json")
            else:
                stats = container.ingest_service.ingest_manifest(manifest)
            return {"status": "success", "stats": stats}
        except Exception as e:
            logger.error("Error importando manifiesto: %s", e)
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/index")
    def process_index_outbox(batch_size: int = Query(50, ge=1, le=200)):
        total = 0
        while True:
            processed = container.indexing_worker.process_pending_outbox(batch_size=batch_size)
            if processed == 0:
                break
            total += processed
        return {"status": "success", "indexed_count": total, "total_points": container.vector_index.count()}

    @app.get("/api/index/status")
    def index_status():
        """Si el índice está al día, por qué no, y el reindexado en curso."""
        maintenance = container.index_maintenance
        job = maintenance.active_job()
        return {**maintenance.status().to_dict(), "job": job_response(job) if job else None}

    @app.post("/api/index/reindex", status_code=202)
    def reindex():
        """Encola una reconstrucción completa; si ya hay una, devuelve esa."""
        job = container.index_maintenance.active_job() or submit_reindex(container, TRIGGER_MANUAL)
        return job_response(job)

    # -----------------------------------------------------------------------
    # Resúmenes jerárquicos (texto generado, con revisiones)
    # -----------------------------------------------------------------------

    @app.get("/api/summaries")
    def get_summaries(scope: str, key: Optional[str] = None, environment_id: Optional[str] = None):
        """``scope``=episode|day|environment. Con ``key``: el vigente y sus
        revisiones; sin ella: los vigentes del ámbito (``environment_id``
        acota los días de un entorno)."""
        if scope not in {"episode", "day", "environment"}:
            raise HTTPException(status_code=422, detail="scope debe ser episode, day o environment")
        repo = container.repository
        service = getattr(container, "summaries", None)
        if key:
            current = repo.get_current_summary(scope, key)
            revisions = repo.list_summary_revisions(scope, key)
            return {
                "scope": scope,
                "key": key,
                "enabled": bool(service and service.enabled),
                "current": current.to_dict() if current else None,
                "revisions": [item.to_dict() for item in revisions],
            }
        prefix = f"{environment_id}|" if scope == "day" and environment_id else None
        items = repo.list_current_summaries(scope, prefix=prefix)
        return {"scope": scope, "enabled": bool(service and service.enabled), "items": [item.to_dict() for item in items]}

    @app.post("/api/summaries/regenerate", status_code=202)
    def regenerate_summary(req: SummaryRegenerateRequest):
        """Encola la redacción de una revisión nueva; se ve en Vigilancia."""
        service = getattr(container, "summaries", None)
        if service is None or not service.enabled:
            raise HTTPException(status_code=503, detail="No hay modelo de texto configurado (EPIGRAPH_TEXT_BACKEND)")
        if req.scope == "episode" and container.repository.get_episode(req.key) is None:
            raise HTTPException(status_code=404, detail=f"Episodio no encontrado: {req.key}")
        job = container.job_runner.submit(SUMMARY_JOB_KIND, f"{req.scope}|{req.key}", SUMMARY_STAGES)
        return job_response(job)

    @app.post("/api/rebuild-index", response_model=RebuildIndexResponse)
    def rebuild_index():
        try:
            previous_generation = active_vector_generation()
            previous_points = container.vector_index.count()
            maintenance = getattr(container, "index_maintenance", None)
            total = (
                maintenance.rebuild()
                if maintenance is not None
                else container.lifecycle_service.rebuild_index(container.indexing_worker)
            )
            active_points = container.vector_index.count()
            return RebuildIndexResponse(
                vector_backend=container.config.vector_backend,
                previous_generation=previous_generation,
                active_generation=active_vector_generation(),
                previous_points=previous_points,
                active_points=active_points,
                rebuilt_points=total,
                verified=True,
            )
        except Exception as e:
            logger.error("Error reconstruyendo índice: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    # -----------------------------------------------------------------------
    # Búsqueda y Recuperación Multimodal
    # -----------------------------------------------------------------------

    @app.post("/api/search", response_model=QueryRunResponse)
    def search_episodes(req: SearchRequest):
        intent_map = {
            "semantic_search": SearchIntent.SEMANTIC_SEARCH,
            "interval_search": SearchIntent.INTERVAL_SEARCH,
            "last_observation": SearchIntent.LAST_OBSERVATION,
            "timeline": SearchIntent.TIMELINE,
        }
        intent = intent_map.get(req.intent, SearchIntent.SEMANTIC_SEARCH)

        interval_filter = None
        if req.start_time and req.end_time:
            interval_filter = Interval(
                start=TimeSpec.from_utc(req.start_time, clock_source="api_query"),
                end=TimeSpec.from_utc(req.end_time, clock_source="api_query"),
            )

        run = container.retrieval_service.search(
            text_query=req.text_query,
            image_query_path=image_evidence_path(req.image_evidence_id),
            intent=intent,
            source_filter=req.source_filter,
            interval_filter=interval_filter,
            entity_filter=req.entity_filter,
            require_unique_resolution=req.require_unique_resolution,
            limit=req.limit,
        )

        return query_run_response(run)

    @app.post("/api/hybrid-search", response_model=HybridQueryRunResponse)
    def hybrid_search(req: SearchRequest):
        interval_filter = None
        if req.start_time and req.end_time:
            interval_filter = Interval(
                start=TimeSpec.from_utc(req.start_time, clock_source="api_query"),
                end=TimeSpec.from_utc(req.end_time, clock_source="api_query"),
            )
        try:
            validation_states = tuple(
                ValidationState(value) for value in (req.validation_state_filter or [])
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"Estado de validacion no soportado: {exc}"
            ) from exc

        if req.intent == "last_observation":
            entity = req.entity_filter or req.text_query
            if not entity:
                raise HTTPException(
                    status_code=422,
                    detail="last_observation requiere entity_filter o text_query",
                )
            run = container.hybrid_retrieval_service.last_confirmed_observation(entity)
        else:
            run = container.hybrid_retrieval_service.search(
                text_query=req.text_query,
                filters=HybridSearchFilters(
                    environment_ids=tuple(req.environment_filter or []),
                    source_ids=tuple(req.source_filter or []),
                    modalities=tuple(req.modality_filter or []),
                    interval=interval_filter,
                    entity_ids=((req.entity_filter,) if req.entity_filter else ()),
                    observation_types=tuple(req.observation_type_filter or []),
                    episode_types=tuple(req.episode_type_filter or []),
                    min_confidence=req.min_confidence,
                    validation_states=validation_states,
                ),
                expand_relations=req.expand_relations,
                relation_types=req.relation_type_filter or (),
                limit=req.limit,
            )

        return HybridQueryRunResponse(
            text_query=run.text_query,
            duration_ms=run.duration_ms,
            results_without_evidence=run.results_without_evidence,
            expanded_relations=run.expanded_relations,
            relation_types=list(run.relation_types),
            results=[
                {
                    "rank": result.rank,
                    "episode": result.episode.to_dict(),
                    "observations": [item.to_dict() for item in result.observations],
                    "evidence_references": [
                        item.to_dict() for item in result.evidence_references
                    ],
                    "relation_expansions": [
                        {
                            "relation": item.relation.to_dict(),
                            "evidence_references": [
                                reference.to_dict()
                                for reference in item.evidence_references
                            ],
                            "is_validated": item.is_validated,
                        }
                        for item in result.relation_expansions
                    ],
                    "scores": {
                        "structured": result.scores.structured,
                        "semantic": result.scores.semantic,
                        "temporal": result.scores.temporal,
                        "relational": result.scores.relational,
                    },
                    "match_kinds": list(result.match_kinds),
                    "is_direct_match": result.is_direct_match,
                    "has_evidence": result.has_evidence,
                }
                for result in run.results
            ],
        )

    @app.get("/api/passage-search/settings")
    def passage_search_settings():
        service = getattr(container, "passage_search", None)
        if service is None:
            raise HTTPException(status_code=503, detail="La búsqueda por pasajes no está configurada")
        return service.settings()

    @app.post("/api/passage-search")
    def passage_search(req: PassageSearchRequest):
        service = getattr(container, "passage_search", None)
        if service is None:
            raise HTTPException(status_code=503, detail="La búsqueda por pasajes no está configurada")
        interval = None
        if req.start_time and req.end_time:
            interval = Interval(
                start=TimeSpec.from_utc(req.start_time, clock_source="api_query"),
                end=TimeSpec.from_utc(req.end_time, clock_source="api_query"),
            )
        try:
            run = service.search(
                req.text_query,
                HybridSearchFilters(
                    environment_ids=tuple(req.environment_filter or []),
                    source_ids=tuple(req.source_filter or []),
                    modalities=tuple(req.modality_filter or []),
                    observation_types=tuple(req.observation_type_filter or []),
                    interval=interval,
                ),
                max_episodes=req.max_episodes,
                per_episode=req.per_episode,
                rerank=req.rerank,
                # Un null explícito quita el umbral; omitirlo usa el configurado.
                min_score=req.rerank_min_score if "rerank_min_score" in req.model_fields_set else DEFAULT,
                agreement_rank=req.agreement_rank,
                neighbors=req.neighbors,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return run.to_dict()

    # -----------------------------------------------------------------------
    # Herramientas deterministas de memoria (Fase 8)
    # -----------------------------------------------------------------------

    @app.get("/api/tools", response_model=list[ToolDescriptor])
    def list_memory_tools():
        return container.memory_tools.describe()

    @app.post("/api/tools/{tool_name}", response_model=MemoryToolOutput)
    def execute_memory_tool(tool_name: str, payload: Dict[str, Any]):
        try:
            return container.memory_tools.execute(tool_name, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # -----------------------------------------------------------------------
    # Episodios y Detalles
    # -----------------------------------------------------------------------

    @app.get("/api/episodes")
    def list_episodes(
        source_id: Optional[str] = None,
        limit: int = Query(50, ge=1, le=100),
        cursor: Optional[str] = None,
    ):
        episodes, next_cursor = container.repository.list_episodes(
            source_id=source_id,
            limit=limit,
            cursor=cursor,
        )
        return {
            "episodes": episodes,
            "next_cursor": next_cursor,
        }

    @app.get("/api/episodes/{episode_id}")
    def get_episode_details(episode_id: str):
        ep = container.repository.get_episode(episode_id)
        if not ep:
            raise HTTPException(status_code=404, detail="Episodio no encontrado")

        observations = container.repository.list_observations_for_episode(episode_id)
        evidence = container.repository.list_evidence_for_episode(episode_id)
        annotations = container.repository.list_annotations_for_episode(episode_id)

        return {
            "episode": ep,
            "observations": observations,
            "evidence": evidence,
            "annotations": annotations,
        }

    @app.delete("/api/episodes/{episode_id}")
    def delete_episode(episode_id: str):
        success = container.lifecycle_service.delete_episode(episode_id)
        if not success:
            raise HTTPException(status_code=404, detail="Episodio no encontrado")
        return {"status": "success", "deleted_episode_id": episode_id}

    # -----------------------------------------------------------------------
    # Anotaciones
    # -----------------------------------------------------------------------

    @app.get("/api/episodes/{episode_id}/annotations")
    def list_annotations(episode_id: str):
        return container.repository.list_annotations_for_episode(episode_id)

    @app.post("/api/episodes/{episode_id}/annotations")
    def create_annotation(episode_id: str, req: AnnotationCreateRequest):
        try:
            prov = ProvenanceKind(req.provenance_kind)
        except ValueError:
            prov = ProvenanceKind.HUMAN_DECLARATION

        try:
            ann = container.lifecycle_service.add_annotation(
                episode_id=episode_id,
                content=req.content,
                author=req.author,
                provenance_kind=prov,
                evidence_ids=req.evidence_ids,
                reindex=req.reindex,
            )
            # Si reindex=True, procesar inmediatamente el outbox
            if req.reindex:
                container.indexing_worker.process_pending_outbox(batch_size=10)
            return ann
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # -----------------------------------------------------------------------
    # Timeline y Last-Observation
    # -----------------------------------------------------------------------

    @app.get("/api/timeline", response_model=QueryRunResponse)
    def get_timeline(source_id: Optional[str] = None, limit: int = Query(50, ge=1, le=100)):
        run = container.retrieval_service.search(
            intent=SearchIntent.TIMELINE,
            source_filter=[source_id] if source_id else None,
            limit=limit,
        )
        return query_run_response(run)

    @app.get("/api/entities/{entity_id}/last-observation")
    def get_entity_last_observation(entity_id: str):
        run = container.retrieval_service.search(
            intent=SearchIntent.LAST_OBSERVATION,
            entity_filter=entity_id,
        )
        return run

    # -----------------------------------------------------------------------
    # Archivos de Evidencia (Servir blob canónico)
    # -----------------------------------------------------------------------

    @app.get("/api/evidence/{evidence_id}/file")
    def get_evidence_file(evidence_id: str):
        ev = container.repository.get_evidence(evidence_id)
        if not ev or ev.state == EvidenceState.DELETED:
            raise HTTPException(status_code=404, detail="Evidencia no encontrada o purgada")

        if not ev.internal_uri:
            raise HTTPException(status_code=404, detail="La evidencia no tiene archivo asociado")

        try:
            file_path = container.evidence_store.resolve_path(ev.internal_uri)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Archivo físico de evidencia no encontrado") from exc
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="Archivo físico de evidencia no encontrado")

        media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        return FileResponse(file_path, media_type=media_type)

    # -----------------------------------------------------------------------
    # UI Web Estática
    # -----------------------------------------------------------------------

    web_dir = Path(__file__).parent.parent / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="web_static")

        @app.get("/")
        def serve_index():
            index_path = web_dir / "index.html"
            if index_path.exists():
                return FileResponse(index_path, media_type="text/html")
            return JSONResponse({"message": "Memory Episodic API activa"})

    return app
