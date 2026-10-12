"""Servidor FastAPI para la API REST del sistema de Memoria Episódica."""
from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
import shutil
import tempfile
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
from episodic.bootstrap import AppContainer, create_app_container
from episodic.config import EpisodicConfig
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
    ProjectComponentStatus,
    ProjectStatusResponse,
    QueryRunResponse,
    QueryResultDTO,
    RebuildIndexResponse,
    SearchRequest,
)
from episodic.tools.contracts import MemoryToolOutput, ToolDescriptor

logger = logging.getLogger("episodic.http")


def create_api_app(
    config: Optional[EpisodicConfig] = None,
    container: Optional[AppContainer] = None,
    use_fake_encoders: bool = False,
) -> FastAPI:
    """Crea y configura la aplicación FastAPI con todas las rutas episódicas."""
    if container is None:
        container = create_app_container(config, use_fake_encoders=use_fake_encoders)

    job_runner = getattr(container, "job_runner", None)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # La cola reanuda al arrancar los trabajos que quedaron pendientes.
        if job_runner is not None:
            job_runner.start()
        try:
            yield
        finally:
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

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = container.repository.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Trabajo no encontrado")
        return job_response(job)

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

    @app.post("/api/rebuild-index", response_model=RebuildIndexResponse)
    def rebuild_index():
        try:
            previous_generation = active_vector_generation()
            previous_points = container.vector_index.count()
            total = container.lifecycle_service.rebuild_index(container.indexing_worker)
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
