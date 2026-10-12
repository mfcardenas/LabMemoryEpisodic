"""Comandos de la interfaz de línea de comandos (CLI) episódica.

Implementa los subcomandos:
- init
- import
- index
- search
- timeline
- last-observation
- rebuild-index
- serve
- ingest
- window
- observe
- compose
- capture
- speech-check
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from episodic.bootstrap import create_app_container, create_ingestion_container
from episodic.application.episode_composition import (
    DeterministicEpisodeComposer,
    EpisodeRuleConfig,
)
from episodic.config import EpisodicConfig, load_env_file
from episodic.domain.times import Interval, TimeSpec
from episodic.domain.types import SearchIntent
from episodic.domain.types import SourceType
from episodic.logging_config import configure_logging

# Asegurar UTF-8 en consola de Windows
if sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

logger = logging.getLogger("episodic.cli")


def _parse_time_spec(value: str) -> TimeSpec:
    """Convierte un instante ISO-8601 de CLI a UTC explícito."""
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"instante no válido '{value}'; use ISO-8601, por ejemplo 2026-01-01T18:23:20Z"
        ) from exc
    return TimeSpec.from_utc(parsed, clock_source="cli")


def _parse_aware_datetime(value: str) -> datetime:
    """Convierte un timestamp ISO-8601 y exige zona horaria explícita."""
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"timestamp ISO-8601 no válido: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("el timestamp debe incluir zona horaria")
    return parsed


def _config_from_args(args: argparse.Namespace, **overrides: object) -> EpisodicConfig:
    """Construye una configuración explícita y compartida para cada comando."""
    values: dict[str, object] = {
        "data_dir": args.data,
        "vector_backend": getattr(args, "vector_backend", "edge"),
        "qdrant_host": args.qdrant_host,
        "qdrant_port": args.qdrant_port,
        "qdrant_collection": args.qdrant_collection,
        "log_level": getattr(args, "log_level", None),
        "log_file": getattr(args, "log_file", None),
    }
    values.update(overrides)
    return EpisodicConfig.from_env(**values)


def cmd_init(args: argparse.Namespace) -> None:
    """Inicializa la base de datos SQLite y directorios."""
    config = _config_from_args(args)
    container = create_app_container(config, use_fake_encoders=True)
    container.close()
    print(f"Sistema episódico inicializado en: {config.data_path}")
    print(f"Base de datos SQLite: {config.sqlite_path}")
    print(f"Directorio de medios: {config.media_dir}")


def cmd_import(args: argparse.Namespace) -> None:
    """Importa un manifiesto estructurado."""
    config = _config_from_args(args)
    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"Error: archivo de manifiesto no encontrado: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    container = create_app_container(config, use_fake_encoders=args.fake_encoders)
    try:
        print(f"Importando manifiesto: {manifest_path}...")
        stats = container.ingest_service.ingest_manifest(manifest_path)
        print("Ingesta completada:")
        for k, v in stats.items():
            print(f"  - {k}: {v}")
    finally:
        container.close()


def cmd_index(args: argparse.Namespace) -> None:
    """Procesa el outbox de representaciones pendientes."""
    config = _config_from_args(args)
    container = create_app_container(config, use_fake_encoders=args.fake_encoders)
    try:
        print("Procesando outbox de indexación hacia Qdrant...")
        total = 0
        while True:
            processed = container.indexing_worker.process_pending_outbox(batch_size=args.batch_size)
            if processed == 0:
                break
            total += processed
            print(f"  Lote procesado: {processed} representaciones")
        print(f"Indexación finalizada. Total de representaciones indexadas: {total}")
        print(f"Total de puntos en Qdrant: {container.vector_index.count()}")
    finally:
        container.close()


def cmd_search(args: argparse.Namespace) -> None:
    """Busca episodios según texto, imagen o filtros."""
    config = _config_from_args(args)
    container = create_app_container(config, use_fake_encoders=args.fake_encoders)
    try:
        intent_map = {
            "semantic_search": SearchIntent.SEMANTIC_SEARCH,
            "interval_search": SearchIntent.INTERVAL_SEARCH,
            "last_observation": SearchIntent.LAST_OBSERVATION,
            "timeline": SearchIntent.TIMELINE,
        }
        intent = intent_map.get(args.intent, SearchIntent.SEMANTIC_SEARCH)

        source_filter = [args.source] if args.source else None
        interval_filter = None
        if bool(args.start) != bool(args.end):
            raise SystemExit("Error: --start y --end deben proporcionarse juntos")
        if args.start and args.end:
            interval_filter = Interval(start=args.start, end=args.end)

        print(f"\nConsulta: '{args.text or ''}' | Intención: {intent.value}")
        run = container.retrieval_service.search(
            text_query=args.text,
            image_query_path=args.image,
            intent=intent,
            source_filter=source_filter,
            interval_filter=interval_filter,
            require_unique_resolution=args.require_unique_resolution,
            limit=args.limit,
        )

        print(f"Veredicto global: [{run.overall_verdict.value.upper()}] (en {run.duration_ms:.1f}ms)")
        if run.reasons:
            for r in run.reasons:
                print(f"  Motivo: {r}")

        print(f"\nResultados encontrados: {len(run.results)}")
        for r in run.results:
            ep = container.repository.get_episode(r.episode_id)
            title = ep.title if ep else "Episodio desconocido"
            ext_id = ep.external_id if ep else r.episode_id
            print(f"\n  #{r.rank} [{ext_id}] {title}")
            print(f"     Score: {r.score:.4f} | Veredicto: {r.verdict.value}")
            if r.evidence_ids:
                print(f"     Evidencias vinculadas ({len(r.evidence_ids)}): {', '.join(r.evidence_ids)}")
            if r.reasons:
                for reason in r.reasons:
                    print(f"     > {reason}")

    finally:
        container.close()


def cmd_timeline(args: argparse.Namespace) -> None:
    """Muestra la línea temporal cronológica de episodios."""
    config = _config_from_args(args)
    container = create_app_container(config, use_fake_encoders=True)
    try:
        source_filter = [args.source] if args.source else None
        run = container.retrieval_service.search(
            intent=SearchIntent.TIMELINE,
            source_filter=source_filter,
            limit=args.limit,
        )
        print(f"\nLínea temporal episódica ({len(run.results)} hitos):")
        for r in run.results:
            ep = container.repository.get_episode(r.episode_id)
            ext_id = ep.external_id if ep else r.episode_id
            title = ep.title if ep else ""
            t_str = "Tiempo desconocido"
            if ep and ep.interval and ep.interval.start and ep.interval.start.absolute:
                t_str = ep.interval.start.absolute.strftime("%Y-%m-%d %H:%M:%S UTC")
            print(f"  - [{t_str}] [{ext_id}] {title} (en {ep.location if ep else ''})")
    finally:
        container.close()


def cmd_last_obs(args: argparse.Namespace) -> None:
    """Busca la última observación de una entidad específica."""
    config = _config_from_args(args)
    container = create_app_container(config, use_fake_encoders=True)
    try:
        run = container.retrieval_service.search(
            intent=SearchIntent.LAST_OBSERVATION,
            entity_filter=args.entity,
        )
        print(f"\nÚltima observación para: '{args.entity}'")
        print(f"Veredicto: [{run.overall_verdict.value.upper()}]")
        for r in run.reasons:
            print(f"  {r}")
        if run.results:
            ep = container.repository.get_episode(run.results[0].episode_id)
            if ep:
                print(f"Episodio asociado: [{ep.external_id}] {ep.title} ({ep.location})")
    finally:
        container.close()


def cmd_rebuild(args: argparse.Namespace) -> None:
    """Reconstruye el índice vectorial en Qdrant desde SQLite."""
    config = _config_from_args(args)
    container = create_app_container(config, use_fake_encoders=args.fake_encoders)
    try:
        print("Reconstruyendo proyección vectorial desde SQLite...")
        total = container.lifecycle_service.rebuild_index(container.indexing_worker)
        print(f"Reconstrucción completada. Total de representaciones indexadas: {total}")
        print(f"Puntos en Qdrant: {container.vector_index.count()}")
    finally:
        container.close()


def cmd_capture(args: argparse.Namespace) -> None:
    """Sube varios archivos como una captura y espera a que exista su episodio."""
    from episodic.application.captures import CAPTURE_JOB_KIND, CAPTURE_STAGES, CaptureFile

    config = _config_from_args(args)
    container = create_app_container(config, use_fake_encoders=args.fake_encoders)
    try:
        capture = container.capture_service.register(
            [CaptureFile(Path(item), Path(item).name) for item in args.files],
            environment_id=args.environment,
            title=args.title,
            captured_at=args.captured_at,
            window_duration_seconds=args.window,
            overlap_seconds=args.overlap,
            audio_offset_seconds=args.audio_offset,
        )
        print(f"Captura: {capture.id} ({len(capture.asset_ids)} archivo(s))")
        print(f"Hora: {capture.captured_at.isoformat()} [{capture.captured_at_origin.value}]")
        runner = container.job_runner
        job = runner.submit(CAPTURE_JOB_KIND, capture.id, CAPTURE_STAGES)
        runner.run_next()
        job = container.repository.get_job(job.id)
        for stage in job.stages:
            print(f"  {stage.label:<22} {stage.state.value:<10} {stage.detail}")
        if job.state.value != "completed":
            raise SystemExit(f"El procesamiento falló: {job.error}")
        print(f"Episodio: {job.result['episode_id']}")
    finally:
        container.close()


def cmd_speech_check(args: argparse.Namespace) -> None:
    """Comprueba el servicio de transcripción configurado y, opcionalmente, lo prueba."""
    import time

    from episodic.bootstrap import build_speech_transcriber
    from episodic.ports.speech import SpeechTranscriberError

    config = _config_from_args(args)
    print(f"Backend: {config.speech_backend}")
    transcriber = build_speech_transcriber(config)
    if transcriber is None:
        print("La transcripción está desactivada (EPIGRAPH_SPEECH_BACKEND=none).")
        return
    print(f"Modelo:  {transcriber.model_id}")
    print(f"Idioma:  {config.speech_language or 'autodetección'}")
    try:
        if config.speech_backend == "http":
            print(f"URL:     {config.speech_url}")
            models = transcriber.list_models()
            if transcriber.model_id not in models:
                if not args.download:
                    raise SystemExit(
                        f"El modelo no está en el servicio (disponibles: {models or 'ninguno'}). "
                        "Use --download para descargarlo."
                    )
                print("Descargando el modelo…")
                print(transcriber.download_model())
            print("Servicio: disponible, modelo instalado")
        if args.file:
            from episodic.adapters.audio.extract import PyAVAudioExtractor

            audio = PyAVAudioExtractor(config.speech_sample_rate_hz).extract(args.file)
            if audio is None:
                raise SystemExit(f"{args.file} no tiene pista de audio")
            started = time.perf_counter()
            transcript = transcriber.transcribe(
                audio.wav, f"{Path(args.file).stem}.wav", config.speech_language or None
            )
            elapsed = time.perf_counter() - started
            print(
                f"Transcripción de {audio.duration_seconds:.1f} s en {elapsed:.2f} s "
                f"(idioma {transcript.language or '?'}):"
            )
            for segment in transcript.segments:
                print(f"  {segment.start_seconds:7.2f}–{segment.end_seconds:7.2f}  {segment.text}")
    except SpeechTranscriberError as exc:
        raise SystemExit(f"Error: {exc}") from exc


def cmd_ingest_asset(args: argparse.Namespace) -> None:
    """Registra un archivo original como Asset sin extraer su contenido."""
    config = _config_from_args(args)
    container = create_ingestion_container(config)
    try:
        source_type = SourceType(args.source_type) if args.source_type else None
        result = container.asset_ingestion_service.ingest_file(
            args.file,
            environment_id=args.environment,
            source_id=args.source,
            source_type=source_type,
            captured_at=args.captured_at,
        )
        asset = result.asset
        print(f"Asset ID: {asset.id}")
        print(f"Duplicado: {'sí' if result.duplicate else 'no'}")
        print(f"Tipo: {asset.media_type.value} ({asset.mime_type})")
        print(f"SHA-256: {asset.content_hash}")
        print(f"Tamaño: {asset.size_bytes} bytes")
        print(f"Duración: {asset.duration_seconds if asset.duration_seconds is not None else 'no disponible'}")
        print(f"Original: {asset.uri}")
    finally:
        container.close()


def cmd_window_asset(args: argparse.Namespace) -> None:
    """Genera ProcessingWindow configurables para un Asset audiovisual."""
    config = _config_from_args(args)
    container = create_ingestion_container(config)
    try:
        result = container.windowing_service.create_windows(
            args.asset_id,
            duration_seconds=args.duration,
            overlap_seconds=args.overlap,
            total_duration_seconds=args.total_duration,
        )
        print(f"ProcessingRun ID: {result.run.id}")
        print(f"Ventanas: {len(result.windows)}")
        for window in result.windows:
            print(
                f"  [{window.sequence_number}] {window.id} "
                f"offset={window.start_offset_seconds:g}-{window.end_offset_seconds:g}s "
                f"time={window.start_time.isoformat()}..{window.end_time.isoformat()}"
            )
    finally:
        container.close()


def cmd_observe_asset(args: argparse.Namespace) -> None:
    """Extrae y muestra observaciones normalizadas con evidencia directa."""
    config = _config_from_args(args)
    container = create_ingestion_container(config)
    try:
        result = container.observation_extraction_service.process_asset(
            args.asset_id,
            processor_name=args.processor,
        )
        print(f"Observaciones: {len(result.observations)}")
        for observation in result.observations:
            print(
                f"  [{observation.modality}/{observation.observation_type}] "
                f"{observation.observation_id}"
            )
            print(f"    {observation.description}")
            print(
                f"    asset={observation.asset_id} "
                f"window={observation.processing_window_id or '-'} "
                f"run={observation.processing_run_id}"
            )
            for reference_id in observation.evidence_reference_ids:
                reference = container.repository.get_evidence_reference(reference_id)
                if reference:
                    print(
                        f"    evidencia={reference.id} "
                        f"tipo={reference.reference_kind.value} "
                        f"localizador={reference.to_dict()}"
                    )
        if result.failures:
            print(f"Errores: {len(result.failures)}", file=sys.stderr)
            for failure in result.failures:
                print(
                    f"  {failure.processor} / {failure.target_id}: {failure.error}",
                    file=sys.stderr,
                )
            raise SystemExit(2)
    finally:
        container.close()


def cmd_compose_episodes(args: argparse.Namespace) -> None:
    """Reconstruye microepisodios y episodios mediante reglas deterministas."""
    config = _config_from_args(args)
    container = create_ingestion_container(config)
    try:
        rules = (
            EpisodeRuleConfig.from_json_file(args.rules)
            if args.rules
            else EpisodeRuleConfig()
        )
        composer = DeterministicEpisodeComposer(container.repository, rules)
        result = composer.compose(args.environment)
        print(f"Observaciones agrupadas: {result.observation_count}")
        print(f"Microepisodios: {len(result.microepisodes)}")
        for microepisode in result.microepisodes:
            print(
                f"  {microepisode.id} "
                f"{microepisode.start_time.isoformat()}..{microepisode.end_time.isoformat()} "
                f"observaciones={len(microepisode.observation_ids)} "
                f"evidencias={len(microepisode.evidence_reference_ids)}"
            )
            print(
                "    reglas="
                f"{microepisode.metadata.get('grouping_decisions', [])}"
            )
        print(f"Episodios: {len(result.episodes)}")
        for episode in result.episodes:
            print(
                f"  {episode.id} "
                f"microepisodios={len(episode.microepisode_ids)} "
                f"observaciones={len(episode.observation_ids)}"
            )
        if result.removed_microepisode_ids or result.removed_episode_ids:
            print(
                "Reconstrucción: "
                f"retirados {len(result.removed_microepisode_ids)} microepisodios y "
                f"{len(result.removed_episode_ids)} episodios obsoletos"
            )
    finally:
        container.close()


def cmd_serve(args: argparse.Namespace) -> None:
    """Inicia el servidor HTTP y la UI Web local."""
    import uvicorn
    from episodic.interfaces.http.server import create_api_app

    config = _config_from_args(
        args,
        host=args.host,
        port=args.port,
    )
    app = create_api_app(config, use_fake_encoders=args.fake_encoders)
    print(f"\nIniciando servidor de Memoria Episódica en http://{args.host}:{args.port}")
    print(f"Dashboard Web disponible en: http://{args.host}:{args.port}/\n")
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
        log_config=None,
    )


def _add_common_arguments(
    parser: argparse.ArgumentParser,
    config: EpisodicConfig,
    suppress_defaults: bool = False,
) -> None:
    def default(value):
        return argparse.SUPPRESS if suppress_defaults else value

    parser.add_argument("--data", default=default(config.data_dir), help="Directorio raíz de datos")
    parser.add_argument(
        "--vector-backend",
        choices=("edge", "http"),
        default=default(config.vector_backend),
        help="Backend vectorial: Edge embebido (predeterminado) o servidor HTTP",
    )
    parser.add_argument(
        "--fake-encoders",
        action="store_true",
        default=default(False),
        help="Usar encoders falsos deterministas (tests offline)",
    )
    parser.add_argument("--qdrant-host", default=default(config.qdrant_host), help="Host del servidor Qdrant")
    parser.add_argument("--qdrant-port", type=int, default=default(config.qdrant_port), help="Puerto HTTP del servidor Qdrant")
    parser.add_argument(
        "--qdrant-collection",
        default=default(config.qdrant_collection),
        help="Colección Qdrant; use una colección exclusiva para pruebas",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default=default(config.log_level),
        help="Nivel de logging",
    )
    parser.add_argument(
        "--log-file",
        default=default(config.log_file),
        help="Fichero de log opcional, relativo a la raíz del proyecto",
    )


def main() -> None:
    load_env_file()
    environment_config = EpisodicConfig.from_env()
    common_parser = argparse.ArgumentParser(add_help=False)
    _add_common_arguments(common_parser, environment_config)
    subcommand_common = argparse.ArgumentParser(add_help=False)
    _add_common_arguments(subcommand_common, environment_config, suppress_defaults=True)

    parser = argparse.ArgumentParser(
        prog="epigraph",
        description="Sistema de Memoria Episódica Multimodal EpiGraph",
        parents=[common_parser],
    )

    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcomando a ejecutar")

    # init
    p_init = subparsers.add_parser("init", parents=[subcommand_common], help="Inicializa el repositorio SQLite y directorios")
    p_init.set_defaults(func=cmd_init)

    # import
    p_import = subparsers.add_parser("import", parents=[subcommand_common], help="Importa un manifiesto estructurado de episodios")
    p_import.add_argument("--manifest", required=True, help="Ruta al archivo manifest.json")
    p_import.set_defaults(func=cmd_import)

    # index
    p_index = subparsers.add_parser("index", parents=[subcommand_common], help="Procesa el outbox y actualiza Qdrant")
    p_index.add_argument("--batch-size", type=int, default=50, help="Tamaño de lote")
    p_index.set_defaults(func=cmd_index)

    # search
    p_search = subparsers.add_parser("search", parents=[subcommand_common], help="Ejecuta una búsqueda multimodal")
    p_search.add_argument("--text", help="Texto de consulta")
    p_search.add_argument("--image", help="Ruta a imagen de consulta")
    p_search.add_argument("--intent", default="semantic_search", choices=["semantic_search", "interval_search", "last_observation", "timeline"])
    p_search.add_argument("--source", help="Filtro de ID de fuente")
    p_search.add_argument("--start", type=_parse_time_spec, help="Inicio ISO-8601 del intervalo")
    p_search.add_argument("--end", type=_parse_time_spec, help="Fin ISO-8601 del intervalo")
    p_search.add_argument("--limit", type=int, default=10, help="Límite de resultados")
    p_search.add_argument(
        "--require-unique-resolution",
        action="store_true",
        help="Declarar que la consulta debe resolverse a una única correspondencia",
    )
    p_search.set_defaults(func=cmd_search)

    # timeline
    p_timeline = subparsers.add_parser("timeline", parents=[subcommand_common], help="Visualiza la línea temporal de episodios")
    p_timeline.add_argument("--source", help="Filtro opcional de fuente")
    p_timeline.add_argument("--limit", type=int, default=50, help="Límite de episodios")
    p_timeline.set_defaults(func=cmd_timeline)

    # last-observation
    p_last = subparsers.add_parser("last-observation", parents=[subcommand_common], help="Última observación de una entidad")
    p_last.add_argument("--entity", required=True, help="ID o etiqueta de la entidad")
    p_last.set_defaults(func=cmd_last_obs)

    # rebuild-index
    p_rebuild = subparsers.add_parser("rebuild-index", parents=[subcommand_common], help="Reconstruye íntegramente la proyección vectorial")
    p_rebuild.set_defaults(func=cmd_rebuild)

    # ingest (Fase 3)
    p_ingest = subparsers.add_parser(
        "ingest",
        parents=[subcommand_common],
        help="Registra un archivo original como Asset",
    )
    p_ingest.add_argument("file", help="Ruta del vídeo, audio, imagen, PDF o texto")
    p_ingest.add_argument("--environment", required=True, help="ID del ambiente")
    p_ingest.add_argument("--source", required=True, help="ID de la fuente")
    p_ingest.add_argument(
        "--source-type",
        choices=[member.value for member in SourceType],
        help="Tipo de fuente; si se omite se infiere del archivo",
    )
    p_ingest.add_argument(
        "--captured-at",
        type=_parse_aware_datetime,
        help="Instante de captura ISO-8601 con zona horaria",
    )
    p_ingest.set_defaults(func=cmd_ingest_asset)

    # window (Fase 3)
    p_window = subparsers.add_parser(
        "window",
        parents=[subcommand_common],
        help="Genera ventanas técnicas para un Asset audiovisual",
    )
    p_window.add_argument("asset_id", help="ID del Asset")
    p_window.add_argument("--duration", type=float, required=True, help="DeltaTime en segundos")
    p_window.add_argument("--overlap", type=float, default=0.0, help="Solapamiento en segundos")
    p_window.add_argument(
        "--total-duration",
        type=float,
        help="Duración total si no puede leerse del contenedor",
    )
    p_window.set_defaults(func=cmd_window_asset)

    # observe (Fase 4)
    p_observe = subparsers.add_parser(
        "observe",
        parents=[subcommand_common],
        help="Extrae observaciones normalizadas de un Asset",
    )
    p_observe.add_argument("asset_id", help="ID del Asset")
    p_observe.add_argument(
        "--processor",
        choices=(
            "text-processor",
            "document-processor",
            "audio-processor",
            "image-processor",
            "video-processor",
            "mock-processor",
        ),
        help="Procesador explícito; por defecto se selecciona por modalidad",
    )
    p_observe.set_defaults(func=cmd_observe_asset)

    # compose (Fase 5)
    p_compose = subparsers.add_parser(
        "compose",
        parents=[subcommand_common],
        help="Construye microepisodios y episodios con reglas deterministas",
    )
    p_compose.add_argument("--environment", required=True, help="ID del ambiente")
    p_compose.add_argument(
        "--rules",
        type=Path,
        help="Fichero JSON opcional con las reglas de agrupación",
    )
    p_compose.set_defaults(func=cmd_compose_episodes)

    # capture (Fase B)
    p_capture = subparsers.add_parser(
        "capture",
        parents=[subcommand_common],
        help="Sube vídeo, audio y documentos tomados juntos como un único episodio",
    )
    p_capture.add_argument("files", nargs="+", help="Archivos de la captura")
    p_capture.add_argument("--environment", required=True, help="ID del ambiente")
    p_capture.add_argument("--title", help="Título del episodio")
    p_capture.add_argument(
        "--captured-at",
        type=_parse_aware_datetime,
        help="Instante de captura ISO-8601 con zona horaria",
    )
    p_capture.add_argument("--window", type=float, default=60.0, help="Duración de ventana (s)")
    p_capture.add_argument("--overlap", type=float, default=5.0, help="Solapamiento (s)")
    p_capture.add_argument(
        "--audio-offset",
        type=float,
        default=0.0,
        help="Segundos que se suman a un audio grabado aparte para alinearlo con el vídeo",
    )
    p_capture.set_defaults(func=cmd_capture)

    # speech-check (Fase D)
    p_speech = subparsers.add_parser(
        "speech-check",
        parents=[subcommand_common],
        help="Comprueba el servicio de transcripción configurado (EPIGRAPH_SPEECH_*)",
    )
    p_speech.add_argument("--file", help="Audio o vídeo con el que probar la transcripción")
    p_speech.add_argument(
        "--download",
        action="store_true",
        help="Descarga el modelo en el servicio si no está (Speaches)",
    )
    p_speech.set_defaults(func=cmd_speech_check)

    # serve
    p_serve = subparsers.add_parser("serve", parents=[subcommand_common], help="Inicia la API HTTP y la interfaz web")
    p_serve.add_argument("--host", default=environment_config.host, help="Host de escucha")
    p_serve.add_argument("--port", type=int, default=environment_config.port, help="Puerto de escucha")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    runtime_config = _config_from_args(args)
    configure_logging(runtime_config.log_level, runtime_config.log_file_path)
    logger.info("Comando iniciado: %s", args.command)
    args.func(args)


if __name__ == "__main__":
    main()
