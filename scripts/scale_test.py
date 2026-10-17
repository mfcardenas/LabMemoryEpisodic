"""Prueba de escala (Fase S): genera un corpus sintético, lo indexa y mide la consulta.

Autocontenida y sin intervención: con ``--reset`` borra el directorio de datos
y las colecciones de Qdrant del perfil (solo las que empiezan por el nombre de
la colección dado, nunca otras), genera ``--events`` eventos, los indexa y
lanza una batería de consultas (el banco real de preguntas más consultas
sintéticas) midiendo p50/p95 de la búsqueda por momentos (con y sin
reordenador), de las herramientas del agente y de la navegación. Deja el
informe en ``data/eval/escala-<fecha>-<perfil>.json`` y una fila para
``docs/evaluacion.md``.

Perfiles recomendados (los lanza ``scripts/scale_test.bat`` / ``.sh``):

  python scripts/scale_test.py --events 200000 --fake-encoders --reset       # ruta de datos: SQLite, FTS, Qdrant
  python scripts/scale_test.py --events 50000 --reset --profile real          # con Nomic y el reordenador reales (lento)

Nunca toca ``episodic-data`` ni la colección ``episodic_memory`` de las capturas reales.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.synthesize_corpus import ACCIONES, LUGARES, OBJETOS, TEXTO_VISIBLE, build  # noqa: E402


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))], 1)


def _stats(values: list[float]) -> dict:
    return {"n": len(values), "p50_ms": _percentile(values, 50), "p95_ms": _percentile(values, 95),
            "max_ms": round(max(values), 1) if values else 0.0, "mean_ms": round(statistics.fmean(values), 1) if values else 0.0}


def reset_profile(data_dir: Path, collection: str, qdrant_host: str, qdrant_port: int) -> None:
    """Borra el directorio de datos del perfil y SOLO sus colecciones de Qdrant."""
    if data_dir.resolve() == (ROOT / "episodic-data").resolve() or collection == "episodic_memory":
        raise SystemExit("Negado: no se resetea el perfil de las capturas reales")
    if data_dir.exists():
        shutil.rmtree(data_dir)
        print(f"borrado {data_dir}")
    from qdrant_client import QdrantClient

    client = QdrantClient(host=qdrant_host, port=qdrant_port)
    for alias in client.get_aliases().aliases:
        if alias.alias_name == collection:
            from qdrant_client import models

            client.update_collection_aliases(change_aliases_operations=[
                models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=collection))
            ])
    for item in client.get_collections().collections:
        if item.name == collection or item.name.startswith(collection + "__"):
            client.delete_collection(item.name)
            print(f"borrada colección {item.name}")


def synthetic_queries(rng: random.Random, count: int, environments: int) -> list[str]:
    templates = [
        "¿cuándo se vio {obj}?",
        "{acc} {obj}",
        "¿quién estaba en {lugar}?",
        "¿qué pasó después de {acc} {obj}?",
        "¿dónde aparece el texto {vis}?",
        "{obj} {lugar} ayer por la tarde",
        "¿qué se dijo sobre {obj} en synth_env_{env:03d}?",
        "el 3 de marzo en synth_env_{env:03d}",
        '"{obj}"',
        "{acc} {obj} la semana pasada",
    ]
    out = []
    for _ in range(count):
        out.append(rng.choice(templates).format(
            obj=rng.choice(OBJETOS), acc=rng.choice(ACCIONES), lugar=rng.choice(LUGARES),
            vis=rng.choice(TEXTO_VISIBLE), env=rng.randrange(environments),
        ))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--events", type=int, default=200000)
    parser.add_argument("--environments", type=int, default=20)
    parser.add_argument("--profile", default=None, help="nombre del perfil (por defecto: hash|real según --fake-encoders)")
    parser.add_argument("--fake-encoders", action="store_true", help="vectores hash y reordenador falso: mide solo la ruta de datos")
    parser.add_argument("--data", default=None, help="directorio de datos (por defecto episodic-data-scale-<perfil>)")
    parser.add_argument("--collection", default=None, help="colección de Qdrant (por defecto episodic_memory_scale_<perfil>)")
    parser.add_argument("--reset", action="store_true", help="borra datos y colecciones del perfil antes de generar")
    parser.add_argument("--skip-build", action="store_true", help="no genera: solo mide sobre lo que ya hay")
    parser.add_argument("--queries", type=int, default=60, help="consultas sintéticas además del banco")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out-dir", default="data/eval")
    args = parser.parse_args()

    profile = args.profile or ("hash" if args.fake_encoders else "real")
    data_dir = Path(args.data or f"episodic-data-scale-{profile}")
    collection = args.collection or f"episodic_memory_scale_{profile}"
    if not collection.startswith("episodic_memory"):
        parser.error("La colección debe empezar por episodic_memory")

    from episodic.config import EpisodicConfig, load_env_file

    load_env_file()
    probe = EpisodicConfig.from_env()
    report: dict = {"perfil": profile, "eventos_pedidos": args.events, "fake_encoders": args.fake_encoders,
                    "data": str(data_dir), "collection": collection, "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    if args.reset:
        reset_profile(data_dir, collection, probe.qdrant_host, probe.qdrant_port)

    if not args.skip_build:
        started = time.perf_counter()
        build(SimpleNamespace(
            events=args.events, environments=args.environments, days=270, min_events=5, max_events=40,
            data=str(data_dir), collection=collection, seed=args.seed, no_index=False, fake_encoders=args.fake_encoders,
        ))
        report["construccion_s"] = round(time.perf_counter() - started, 1)

    # -- Medición ---------------------------------------------------------------------------
    from episodic.bootstrap import create_app_container

    config = EpisodicConfig.from_env(data_dir=str(data_dir), qdrant_collection=collection)
    container = create_app_container(config, use_fake_encoders=args.fake_encoders)
    try:
        repo = container.repository
        report["eventos"] = repo.count_events()
        report["pasajes"] = repo.count_passages()
        report["puntos_qdrant"] = container.vector_index.count()
        episodes, _ = repo.list_episodes(limit=1)
        container.passage_search.warm_up()

        rng = random.Random(args.seed)
        bank = [json.loads(line)["pregunta"] for line in (ROOT / "data/eval/preguntas.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        queries = bank + synthetic_queries(rng, args.queries, args.environments)

        def timed(fn) -> float:
            t = time.perf_counter()
            fn()
            return (time.perf_counter() - t) * 1000

        search = container.passage_search
        report["busqueda_sin_reordenar"] = _stats([timed(lambda q=q: search.search(q, rerank=False)) for q in queries])
        report["busqueda_con_reordenador"] = _stats([timed(lambda q=q: search.search(q)) for q in queries])
        report["busqueda_con_intervalo"] = _stats([timed(lambda q=q: search.search(q + " el 3 de marzo", rerank=False)) for q in queries[:30]])
        tools = container.memory_tools
        report["search_moments"] = _stats([timed(lambda q=q: tools.execute("search_moments", {"query": q, "max_episodes": 4, "per_episode": 3})) for q in queries[:40]])
        envs = tools.execute("browse_memory", {"level": "environments"}).browse
        report["browse_environments_ms"] = round(timed(lambda: tools.execute("browse_memory", {"level": "environments"})), 1)
        if envs:
            env_id = envs[0].id
            report["browse_days"] = _stats([timed(lambda: tools.execute("browse_memory", {"level": "days", "environment_id": env_id})) for _ in range(5)])
            days = tools.execute("browse_memory", {"level": "days", "environment_id": env_id}).browse
            if days:
                day = days[0].label
                report["browse_episodes"] = _stats([timed(lambda: tools.execute("browse_memory", {"level": "episodes", "environment_id": env_id, "day": day})) for _ in range(5)])
                eps = tools.execute("browse_memory", {"level": "episodes", "environment_id": env_id, "day": day}).browse
                if eps:
                    events = repo.list_events(eps[0].id)
                    report["browse_events"] = _stats([timed(lambda: tools.execute("browse_memory", {"level": "events", "episode_id": eps[0].id})) for _ in range(5)])
                    if events:
                        mid = events[len(events) // 2].id
                        report["get_event_neighbors"] = _stats([timed(lambda: tools.execute("get_event_neighbors", {"event_id": mid})) for _ in range(20)])
                        report["get_event"] = _stats([timed(lambda: tools.execute("get_event", {"event_id": mid})) for _ in range(20)])
        report["index_status_ms"] = round(timed(lambda: container.index_maintenance.status()), 1)
        report["count_passages_ms"] = round(timed(lambda: repo.count_passages()), 1)
        report["list_episodes_100_ms"] = round(timed(lambda: repo.list_episodes(limit=100)), 1)
    finally:
        container.close()

    try:
        report["commit"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT, check=True).stdout.strip()
    except Exception:
        report["commit"] = "?"
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"escala-{datetime.now():%Y%m%d-%H%M}-{profile}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    s = report
    print("\nFila para docs/evaluacion.md (escala):")
    print(f"| {s['fecha'][:10]} | `{s['commit']}` | {profile} | {s['eventos']:,} | {s['pasajes']:,} | "
          f"{s['busqueda_sin_reordenar']['p50_ms']:.0f} / {s['busqueda_sin_reordenar']['p95_ms']:.0f} | "
          f"{s['busqueda_con_reordenador']['p50_ms']:.0f} / {s['busqueda_con_reordenador']['p95_ms']:.0f} | "
          f"{s['search_moments']['p50_ms']:.0f} / {s['search_moments']['p95_ms']:.0f} | {s.get('construccion_s', '—')} |".replace(",", " "))
    print(f"Informe: {out}")


if __name__ == "__main__":
    main()
