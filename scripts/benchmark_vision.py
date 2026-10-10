"""Compara modelos de visión de Ollama sobre un vídeo sin escribir en la memoria.

Uso:
    python scripts/benchmark_vision.py VIDEO.mp4 --models qwen3.5:4b gemma3:12b

Muestrea y segmenta el vídeo una vez (igual que la ingesta) y describe cada
tramo con cada modelo. Imprime la latencia por tramo y guarda las
descripciones en JSON para compararlas a mano. La primera llamada de cada
modelo incluye su carga en GPU y se informa por separado.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from episodic.adapters.models.ollama_vision import OllamaVisionDescriber
from episodic.adapters.video.decoder import PyAVFrameSampler
from episodic.application.video_segmentation import SceneSegmenter, SegmentationConfig
from episodic.ports.vision import SegmentContext, VisionDescriberError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("video", type=Path)
    parser.add_argument("--models", nargs="+", default=["qwen3.5:4b", "gemma3:12b"])
    parser.add_argument("--fps", type=float, default=1.0)
    parser.add_argument("--min-segment", type=float, default=2.0)
    parser.add_argument("--max-segment", type=float, default=15.0)
    parser.add_argument("--threshold", type=float, default=0.12)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--output", type=Path, help="JSON con todas las descripciones")
    args = parser.parse_args()

    started = time.perf_counter()
    video = PyAVFrameSampler(sample_fps=args.fps).sample(args.video)
    segments = SceneSegmenter(
        SegmentationConfig(args.min_segment, args.max_segment, args.threshold)
    ).segment(video.frames, video.duration_seconds)
    print(
        f"Vídeo {args.video.name}: {video.duration_seconds or 0:.1f} s, "
        f"{len(video.frames)} muestras, {len(segments)} tramo(s), "
        f"decodificación {time.perf_counter() - started:.1f} s"
    )

    report: dict[str, list[dict]] = {}
    for model in args.models:
        describer = OllamaVisionDescriber(model=model, base_url=args.ollama_url)
        results, latencies, previous = [], [], None
        for segment in segments:
            context = SegmentContext(
                segment_index=segment.index,
                segment_count=len(segments),
                start_offset_seconds=segment.start_offset_seconds,
                end_offset_seconds=segment.end_offset_seconds,
                video_duration_seconds=video.duration_seconds,
                frame_offsets_seconds=tuple(item.offset_seconds for item in segment.keyframes),
                previous_summary=previous,
            )
            tick = time.perf_counter()
            try:
                description = describer.describe_segment(
                    [item.jpeg for item in segment.keyframes], context
                )
            except VisionDescriberError as exc:
                print(f"  {model}: tramo {segment.index + 1} falló: {exc}")
                break
            latencies.append(time.perf_counter() - tick)
            previous = description.summary
            results.append(
                {
                    "segment": segment.index,
                    "start": segment.start_offset_seconds,
                    "end": segment.end_offset_seconds,
                    "seconds": round(latencies[-1], 2),
                    **description.to_dict(),
                }
            )
            print(
                f"  {model} · tramo {segment.index + 1} "
                f"[{segment.start_offset_seconds:.1f}–{segment.end_offset_seconds:.1f} s] "
                f"{latencies[-1]:.1f} s · {description.title}"
            )
        if latencies:
            warm = latencies[1:] or latencies
            print(
                f"{model}: primera llamada {latencies[0]:.1f} s, "
                f"media en caliente {sum(warm) / len(warm):.1f} s/tramo, "
                f"{sum(len(item['detailed_description']) for item in results) / len(results):.0f} "
                "caracteres de descripción por tramo"
            )
        report[model] = results

    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Descripciones guardadas en {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
