"""Evalúa el banco de preguntas reales (data/eval/preguntas.jsonl) contra la memoria.

Mide, por pregunta, si el **momento** correcto aparece (episodio esperado y,
si hay ventana, solapamiento de ±5 s con ella) en el primer resultado y en los
cinco primeros, si el episodio aparece, si las preguntas sin respuesta se
abstienen, y la latencia. Escribe un JSON con el detalle y una fila de tabla
para docs/evaluacion.md.

Dos formas de ejecutar:

  python scripts/evaluate_questions.py                 # en proceso: SQLite + Qdrant de .env, modelos reales
  python scripts/evaluate_questions.py --url http://127.0.0.1:8100   # contra el servidor en marcha

`--mode passages` (hoy) consulta la búsqueda por momentos; `--mode ask`
(Fase A2) consultará al agente y evaluará la cita principal de la respuesta.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOLERANCE_SECONDS = 5.0


@dataclass
class Moment:
    """Un resultado reducido a lo que se evalúa: episodio y posición en él."""

    external_id: str
    start_offset: Optional[float]
    end_offset: Optional[float]
    kind: str = ""

    def matches(self, expected_ids: Iterable[str], window: Optional[list[float]]) -> bool:
        if self.external_id not in set(expected_ids):
            return False
        if window is None or self.start_offset is None:
            # Sin ventana, o pasaje sin posición (documento): basta el episodio.
            return True
        end = self.end_offset if self.end_offset is not None else self.start_offset
        return self.start_offset - TOLERANCE_SECONDS <= window[1] and end + TOLERANCE_SECONDS >= window[0]


@dataclass
class Outcome:
    question_id: str
    tipo: str
    moments: list[Moment]
    took_ms: float
    abstained: bool
    extra: dict[str, Any] = field(default_factory=dict)


class PassageRunner:
    """Consulta la búsqueda por momentos, en proceso o por HTTP."""

    mode = "passages"

    def __init__(self, url: Optional[str]) -> None:
        self.url = url
        self.container = None
        if url is None:
            from episodic.bootstrap import create_app_container
            from episodic.config import EpisodicConfig, load_env_file

            load_env_file()
            config = EpisodicConfig.from_env()
            self.container = create_app_container(config)
            self.container.passage_search.warm_up()
        else:
            import requests

            self.session = requests.Session()
            self.session.get(f"{url}/api/health", timeout=10).raise_for_status()

    def describe(self) -> dict[str, Any]:
        if self.container is not None:
            settings = self.container.passage_search.settings()
            return {"transport": "in-process", **settings}
        return {"transport": self.url, **self.session.get(f"{self.url}/api/passage-search/settings", timeout=30).json()}

    def run(self, question: dict[str, Any]) -> Outcome:
        started = time.perf_counter()
        if self.container is not None:
            data = self.container.passage_search.search(question["pregunta"]).to_dict()
        else:
            response = self.session.post(
                f"{self.url}/api/passage-search", json={"text_query": question["pregunta"]}, timeout=120
            )
            response.raise_for_status()
            data = response.json()
        took = (time.perf_counter() - started) * 1000
        moments = [
            Moment(
                external_id=group["episode"]["external_id"],
                start_offset=hit.get("start_offset_seconds"),
                end_offset=hit.get("end_offset_seconds"),
                kind=hit.get("observation_type", ""),
            )
            for group in data["groups"]
            for hit in group["passages"]
        ]
        return Outcome(
            question_id=question["id"],
            tipo=question["tipo"],
            moments=moments,
            took_ms=took,
            abstained=not moments,
            extra={
                "lexical_query": data.get("lexical_query"),
                "discarded": data.get("rerank", {}).get("discarded_low_relevance"),
                "top": [
                    f"{m.external_id}@{m.start_offset}" if m.start_offset is not None else f"{m.external_id}[{m.kind}]"
                    for m in moments[:5]
                ],
            },
        )

    def close(self) -> None:
        if self.container is not None:
            self.container.close()


def evaluate(outcome: Outcome, question: dict[str, Any]) -> dict[str, Any]:
    expected = question["episodios_esperados"]
    window = question.get("ventana_esperada")
    if not expected:
        return {
            "id": outcome.question_id,
            "tipo": outcome.tipo,
            "abstencion_correcta": outcome.abstained,
            "took_ms": round(outcome.took_ms, 1),
            **outcome.extra,
        }
    hits = [m.matches(expected, window) for m in outcome.moments]
    episode_hits = [m.external_id in expected for m in outcome.moments]
    # Preguntas con varios episodios esperados: acierto si todos están en el top 5.
    top5_episodes = {m.external_id for m in outcome.moments[:5]}
    return {
        "id": outcome.question_id,
        "tipo": outcome.tipo,
        "momento_at_1": bool(hits[:1] and hits[0]),
        "momento_at_5": any(hits[:5]),
        "episodio_at_1": bool(episode_hits[:1] and episode_hits[0]),
        "episodio_at_5": set(expected) <= top5_episodes,
        "falsa_abstencion": outcome.abstained,
        "took_ms": round(outcome.took_ms, 1),
        **outcome.extra,
    }


def _rate(rows: list[dict[str, Any]], key: str) -> Optional[float]:
    values = [row[key] for row in rows if key in row]
    return round(sum(values) / len(values), 3) if values else None


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return round(ordered[index], 1)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    with_answer = [row for row in rows if "momento_at_1" in row]
    without = [row for row in rows if "abstencion_correcta" in row]
    latencies = [row["took_ms"] for row in rows]
    return {
        "preguntas": len(rows),
        "con_respuesta": len(with_answer),
        "momento_at_1": _rate(with_answer, "momento_at_1"),
        "momento_at_5": _rate(with_answer, "momento_at_5"),
        "episodio_at_1": _rate(with_answer, "episodio_at_1"),
        "episodio_at_5": _rate(with_answer, "episodio_at_5"),
        "falsas_abstenciones": sum(row["falsa_abstencion"] for row in with_answer),
        "abstencion_correcta": _rate(without, "abstencion_correcta"),
        "p50_ms": _percentile(latencies, 50),
        "p95_ms": _percentile(latencies, 95),
        "por_tipo": {
            tipo: _rate([row for row in with_answer if row["tipo"] == tipo], "momento_at_5")
            for tipo in sorted({row["tipo"] for row in with_answer})
        },
    }


def markdown_row(date: str, commit: str, mode: str, summary: dict[str, Any]) -> str:
    def pct(value: Optional[float]) -> str:
        return "—" if value is None else f"{value * 100:.0f} %"

    return (
        f"| {date} | `{commit}` | {mode} | {pct(summary['momento_at_1'])} | {pct(summary['momento_at_5'])} | "
        f"{pct(summary['episodio_at_5'])} | {pct(summary['abstencion_correcta'])} | "
        f"{summary['falsas_abstenciones']} | {summary['p50_ms']:.0f} | {summary['p95_ms']:.0f} |"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", default="data/eval/preguntas.jsonl")
    parser.add_argument("--url", default=None, help="Servidor EpiGraph; sin él se evalúa en proceso")
    parser.add_argument("--mode", choices=("passages",), default="passages")
    parser.add_argument("--out-dir", default="data/eval")
    parser.add_argument("--only", default=None, help="Ids separados por comas")
    args = parser.parse_args()

    questions = [
        json.loads(line)
        for line in Path(args.questions).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]

    runner = PassageRunner(args.url)
    try:
        rows = []
        for question in questions:
            outcome = runner.run(question)
            row = evaluate(outcome, question)
            rows.append(row)
            flag = row.get("momento_at_5", row.get("abstencion_correcta"))
            print(f"{'ok ' if flag else 'KO '} {row['id']:<32} {row['took_ms']:>7.0f} ms  {row.get('top', '')}")
        summary = summarize(rows)
        try:
            commit = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT, check=True
            ).stdout.strip()
        except Exception:
            commit = "?"
        now = datetime.now(timezone.utc)
        report = {
            "fecha": now.isoformat(timespec="seconds"),
            "commit": commit,
            "modo": runner.mode,
            "configuracion": runner.describe(),
            "resumen": summary,
            "preguntas": rows,
        }
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"resultados-{now.strftime('%Y%m%d-%H%M')}-{runner.mode}.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print()
        print("Fila para docs/evaluacion.md:")
        print(markdown_row(now.strftime("%Y-%m-%d"), commit, runner.mode, summary))
        print(f"Detalle: {out_path}")
    finally:
        runner.close()


if __name__ == "__main__":
    main()
