"""Genera un corpus sintético grande para medir la consulta a escala (Fase S).

Crea ambientes, fuentes, episodios, observaciones `video_event` y
`speech_segment` con la misma forma que produce la captura real (atributos
del VLM, habla alineada, horas absolutas repartidas en meses), y sus `Event`,
directamente en SQLite; después encola la indexación por el outbox igual que
una captura. No hay vídeo ni modelos de visión: solo texto variado en español.

Trabaja SIEMPRE sobre un directorio de datos y una colección de Qdrant
propios, para no mezclarse con las capturas reales:

  python scripts/synthesize_corpus.py --events 100000
  python scripts/synthesize_corpus.py --events 1000000 --data episodic-data-synth --collection episodic_memory_synth
  python scripts/synthesize_corpus.py --events 20000 --no-index      # solo SQLite

Con `--fake-encoders` la indexación usa vectores hash (sin Nomic) para medir
solo SQLite/Qdrant; sin él, codifica con Nomic real (lento: ~50 pasajes/s en CPU).
"""
from __future__ import annotations

import argparse
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from episodic.domain.entities import Episode, Observation, Source  # noqa: E402
from episodic.domain.processing import Event  # noqa: E402
from episodic.domain.provenance import Asset, Environment  # noqa: E402
from episodic.domain.types import EvidenceMediaType, ProvenanceKind, SourceType, ValidationState  # noqa: E402

PRODUCER = "synthetic-corpus"
MODEL_VERSION = "synthetic-v1"

# Vocabulario: suficiente variedad para que las búsquedas no sean triviales.
LUGARES = ["taller mecánico", "oficina", "laboratorio", "cocina", "almacén", "aula", "salón", "garaje", "sala de reuniones", "banco de pruebas"]
ACTORES = ["persona 1", "persona 2", "técnico", "operaria", "estudiante", "mecánico", "visitante", "supervisora"]
ACCIONES = ["aflojar", "apretar", "cortar", "medir", "escribir", "leer", "conectar", "desconectar", "limpiar", "inspeccionar", "sostener", "señalar", "abrir", "cerrar", "montar", "desmontar", "pulir", "soldar", "teclear", "consultar"]
OBJETOS = ["llave dinamométrica", "destornillador", "tarjeta de crédito", "tablet", "portátil", "router", "sensor de humo", "pieza metálica", "disco de freno", "latiguillo", "caja de herramientas", "cable de red", "pipeta", "probeta", "monitor", "impresora", "manual", "carpeta azul", "guante", "taladro", "multímetro", "lubricante", "mochila negra", "chaqueta reflectante"]
ATRIBUTOS = ["azul", "rojo", "plateado", "desgastado", "nuevo", "abierto", "cerrado", "encendido", "apagado", "en funcionamiento", "sobre la mesa", "en el suelo", "a la izquierda", "a la derecha"]
TEXTO_VISIBLE = ["Mobil", "VISA", "NexaHome", "SALIDA", "F-102", "RX-7", "Lote 2026-09", "PELIGRO", "ABS", "12 V"]
FRASES = [
    "Mira, esto está {attr}, voy a {acc} con {obj}.",
    "Pásame {obj}, que hay que {acc} esto antes de las {hora}.",
    "He revisado {obj}; falta {acc} la parte de arriba.",
    "Cuidado con {obj}, no lo toques hasta que termine de {acc}.",
    "¿Tienes la última versión del informe de {lugar}?",
    "Apunta: {obj} {attr}, revisar mañana.",
]


def _pick(rng: random.Random, items: list[str], k: int) -> list[str]:
    return rng.sample(items, k=min(k, len(items)))


def _event_description(rng: random.Random, lugar: str, actor: str, acciones: list[str], objetos: list[dict], visible: list[str]) -> dict:
    obj_text = ", ".join(f"{o['label']} ({', '.join(o['attributes'])})" for o in objetos)
    title = f"{actor.capitalize()} {acciones[0]} {objetos[0]['label']}"
    summary = f"{actor.capitalize()} {acciones[0]} {objetos[0]['label']} en {lugar}."
    detailed = (
        f"En {lugar}, {actor} {acciones[0]} {objetos[0]['label']}"
        + (f" y después {acciones[1]} {objetos[1]['label']}" if len(acciones) > 1 and len(objetos) > 1 else "")
        + f". Se observan {obj_text}. La iluminación es {rng.choice(['artificial', 'natural', 'tenue', 'intensa'])}."
        + (f" Se lee el texto «{visible[0]}»." if visible else "")
    )
    return {
        "title": title,
        "summary": summary,
        "detailed_description": detailed,
        "scene": f"{lugar.capitalize()}, iluminación {rng.choice(['artificial', 'natural'])}.",
        "actions": acciones,
        "actors": [{"label": actor, "description": rng.choice(["vista de espaldas", "de perfil", "rostro no visible"])}],
        "objects": objetos,
        "visible_text": visible,
        "changes_from_previous": rng.choice(["", "Cambia la herramienta.", "Entra otra persona.", "Se mueve la cámara."]),
        "uncertainty": "",
    }


def build(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
    from episodic.config import EpisodicConfig, load_env_file

    load_env_file()
    config = EpisodicConfig.from_env(data_dir=args.data, qdrant_collection=args.collection)
    config.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    repo = SqliteEpisodicRepository(config.sqlite_path)

    environments = [f"synth_env_{i:03d}" for i in range(args.environments)]
    for env_id in environments:
        if repo.get_environment(env_id) is None:
            repo.save_environment(Environment(id=env_id, name=env_id, producer=PRODUCER))
        for kind, stype in (("cam", SourceType.CAMERA), ("mic", SourceType.MICROPHONE)):
            sid = f"{env_id}.{kind}"
            if repo.get_source(sid) is None:
                repo.save_source(Source(id=sid, environment_id=env_id, name=sid, source_type=stype, producer=PRODUCER))

    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    span_seconds = int(timedelta(days=args.days).total_seconds())
    events_left = args.events
    episode_count = 0
    started = time.perf_counter()
    episode_ids: list[str] = []

    while events_left > 0:
        env_id = rng.choice(environments)
        n_events = min(events_left, rng.randint(args.min_events, args.max_events))
        events_left -= n_events
        captured_at = origin + timedelta(seconds=rng.randrange(span_seconds))
        epoch = int(captured_at.timestamp())
        external_id = f"{env_id}_{epoch}_{episode_count}"
        episode_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic|{external_id}"))
        lugar = rng.choice(LUGARES)
        cam, mic = f"{env_id}.cam", f"{env_id}.mic"
        asset_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic-asset|{external_id}"))

        observations: list[Observation] = []
        events: list[Event] = []
        offset = 0.0
        for seq in range(n_events):
            duration = rng.uniform(2.0, 15.0)
            start, end = offset, offset + duration
            offset = end
            actor = rng.choice(ACTORES)
            acciones = _pick(rng, ACCIONES, rng.randint(1, 3))
            objetos = [{"label": o, "attributes": _pick(rng, ATRIBUTOS, rng.randint(1, 2))} for o in _pick(rng, OBJETOS, rng.randint(2, 5))]
            visible = _pick(rng, TEXTO_VISIBLE, 1) if rng.random() < 0.3 else []
            description = _event_description(rng, lugar, actor, acciones, objetos, visible)
            abs_start = captured_at + timedelta(seconds=start)
            abs_end = captured_at + timedelta(seconds=end)
            obs_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic-obs|{external_id}|{seq}"))
            speech_entries = []
            if rng.random() < 0.5:
                frase = rng.choice(FRASES).format(
                    attr=rng.choice(ATRIBUTOS), acc=rng.choice(ACCIONES), obj=rng.choice(OBJETOS),
                    hora=rng.choice(["6", "cinco", "dos", "ocho"]), lugar=rng.choice(LUGARES),
                )
                sp_start = start + rng.uniform(0, max(0.1, duration - 2))
                sp_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic-speech|{external_id}|{seq}"))
                observations.append(
                    Observation(
                        id=sp_id, episode_id=episode_id, content=frase, description=frase, modality="audio",
                        observation_type="speech_segment", validation_state=ValidationState.PROPOSED,
                        attributes={"start_offset_seconds": sp_start, "end_offset_seconds": sp_start + 2.5,
                                    "generation": {"speech_model": "synthetic-whisper"}},
                        provenance_kind=ProvenanceKind.AUDIO_TRANSCRIPTION, author=PRODUCER,
                        environment_id=env_id, source_id=mic, asset_id=asset_id,
                        start_time=captured_at + timedelta(seconds=sp_start),
                        end_time=captured_at + timedelta(seconds=sp_start + 2.5),
                        producer=PRODUCER, model_version=MODEL_VERSION,
                    )
                )
                speech_entries.append({"observation_id": sp_id, "asset_id": asset_id, "source_id": mic, "text": frase,
                                       "start_offset_seconds": sp_start, "end_offset_seconds": sp_start + 2.5,
                                       "speech_model": "synthetic-whisper"})
            observations.append(
                Observation(
                    id=obs_id, episode_id=episode_id, content=description["detailed_description"],
                    description=description["detailed_description"], modality="video", observation_type="video_event",
                    validation_state=ValidationState.PROPOSED,
                    attributes={
                        "segment_index": seq, "segment_count": n_events,
                        "start_offset_seconds": start, "end_offset_seconds": end,
                        "keyframes": [], "description": description,
                        "generation": {"vision_model": "synthetic-vlm", "prompt_version": "synthetic-v1", "language": "es"},
                    },
                    provenance_kind=ProvenanceKind.MODEL_DESCRIPTION, author=PRODUCER,
                    environment_id=env_id, source_id=cam, asset_id=asset_id,
                    start_time=abs_start, end_time=abs_end, producer=PRODUCER, model_version=MODEL_VERSION,
                )
            )
            attributes = {k: description[k] for k in ("scene", "actions", "actors", "objects", "visible_text", "changes_from_previous", "uncertainty")}
            if speech_entries:
                attributes["speech"] = speech_entries
            events.append(
                Event(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic-event|{obs_id}")),
                    event_type="video_segment", description=description["detailed_description"],
                    environment_id=env_id, source_id=cam, start_time=abs_start, end_time=abs_end,
                    producer=PRODUCER, model_version=MODEL_VERSION,
                    metadata={"observation_ids": [obs_id], "provenance_kind": "model_description",
                              "generation": {"vision_model": "synthetic-vlm", "prompt_version": "synthetic-v1"}},
                    asset_id=asset_id, sequence_number=seq, start_offset_seconds=start, end_offset_seconds=end,
                    title=description["title"], summary=description["summary"], attributes=attributes,
                )
            )

        episode = Episode(
            id=episode_id, title=f"{lugar.capitalize()} · {captured_at:%Y-%m-%d %H:%M}", external_id=external_id,
            source_ids=[cam, mic], location=lugar, observation_ids=[o.id for o in observations],
            indexable_text="\n".join(o.description for o in observations),
            environment_ids=[env_id], start_time=captured_at, end_time=captured_at + timedelta(seconds=offset),
            producer=PRODUCER, model_version=MODEL_VERSION,
            metadata={"synthetic": True, "capture_key": external_id},
        )
        asset = Asset(
            id=asset_id, environment_id=env_id, source_id=cam, uri=f"synthetic://{external_id}.mp4",
            media_type=EvidenceMediaType.VIDEO, mime_type="video/mp4", content_hash=asset_id.replace("-", ""),
            duration_seconds=offset, captured_at=captured_at, producer=PRODUCER, model_version=MODEL_VERSION,
        )
        repo.begin_transaction()
        try:
            repo.save_asset(asset)
            repo.save_episode(episode)
            for obs in observations:
                repo.save_observation(obs)
            for event in events:
                repo.save_event(event)
            repo.commit_transaction()
        except Exception:
            repo.rollback_transaction()
            raise
        episode_ids.append(episode_id)
        episode_count += 1
        if episode_count % 200 == 0:
            done = args.events - events_left
            rate = done / (time.perf_counter() - started)
            print(f"  {episode_count} episodios, {done}/{args.events} eventos ({rate:,.0f} ev/s)", flush=True)

    print(f"SQLite: {episode_count} episodios, {args.events} eventos en {time.perf_counter() - started:,.1f} s → {config.sqlite_path}")

    if args.no_index:
        repo.close()
        return

    from episodic.bootstrap import create_app_container

    container = create_app_container(config, use_fake_encoders=args.fake_encoders)
    try:
        started = time.perf_counter()
        batch = 500
        for i in range(0, len(episode_ids), batch):
            container.lifecycle_service.index_episodes(container.indexing_worker, episode_ids[i:i + batch])
            print(f"  indexados {min(i + batch, len(episode_ids))}/{len(episode_ids)} episodios", flush=True)
        print(f"Índice: {len(episode_ids)} episodios en {time.perf_counter() - started:,.1f} s → colección {config.qdrant_collection}")
    finally:
        container.close()
    repo.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--events", type=int, default=10000)
    parser.add_argument("--environments", type=int, default=20)
    parser.add_argument("--days", type=int, default=270, help="Ventana de fechas desde 2026-01-01")
    parser.add_argument("--min-events", type=int, default=5)
    parser.add_argument("--max-events", type=int, default=40)
    parser.add_argument("--data", default="episodic-data-synth", help="Directorio de datos propio (nunca el real)")
    parser.add_argument("--collection", default="episodic_memory_synth", help="Colección de Qdrant propia (prefijo episodic_memory)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--no-index", action="store_true", help="Solo SQLite, sin encolar al índice")
    parser.add_argument("--fake-encoders", action="store_true", help="Vectores hash en lugar de Nomic")
    args = parser.parse_args()
    if Path(args.data).resolve() == (ROOT / "episodic-data").resolve():
        parser.error("No se genera sobre episodic-data (datos reales); use otro --data")
    if not args.collection.startswith("episodic_memory"):
        parser.error("La colección debe empezar por episodic_memory (regla del Qdrant compartido)")
    build(args)


if __name__ == "__main__":
    main()
