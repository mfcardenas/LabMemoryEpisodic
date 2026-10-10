"""Demostración controlada: vídeo + audio + documento en un ambiente."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from episodic.adapters.files.metadata import LocalFileInspector, UnsupportedAssetTypeError
from episodic.bootstrap import create_ingestion_container
from episodic.config import EpisodicConfig
from episodic.domain.types import EvidenceMediaType


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demostración EpiGraph Fase 4")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data_samples/phase4_input"),
    )
    parser.add_argument("--data", type=Path, default=Path(".runtime/fase4-demo"))
    parser.add_argument("--environment", default="laboratorio_01")
    parser.add_argument("--window-duration", type=float, default=30.0)
    parser.add_argument("--overlap", type=float, default=2.0)
    parser.add_argument("--video-duration", type=float)
    parser.add_argument("--audio-duration", type=float)
    return parser.parse_args()


def _select_inputs(input_dir: Path) -> dict[str, Path]:
    inspector = LocalFileInspector()
    candidates: dict[str, list[tuple[EvidenceMediaType, Path]]] = {
        "video": [],
        "audio": [],
        "document": [],
    }
    for path in sorted(input_dir.iterdir()):
        if not path.is_file() or path.name.lower() == "readme.md":
            continue
        try:
            media_type = inspector.inspect(path).media_type
        except (UnsupportedAssetTypeError, ValueError):
            continue
        category = (
            "document"
            if media_type in {EvidenceMediaType.TEXT, EvidenceMediaType.DOCUMENT}
            else media_type.value
        )
        if category in {"video", "audio", "document"}:
            candidates[category].append((media_type, path))
    selected = {
        category: sorted(
            values,
            key=lambda item: (item[0] == EvidenceMediaType.DOCUMENT, item[1].name),
        )[0][1]
        for category, values in candidates.items()
        if values
    }
    missing = sorted({"video", "audio", "document"} - selected.keys())
    if missing:
        raise SystemExit(
            f"Faltan recursos en {input_dir.resolve()}: {', '.join(missing)}"
        )
    return selected


def main() -> int:
    # En Windows la consola puede usar cp1252; conserva la salida aunque el
    # documento contenga símbolos Unicode que esa página de códigos no admite.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = _arguments()
    inputs = _select_inputs(args.input_dir)
    config = EpisodicConfig(data_dir=args.data)
    container = create_ingestion_container(config)
    failures = []
    output = []
    try:
        for category in ("video", "audio", "document"):
            path = inputs[category]
            ingestion = container.asset_ingestion_service.ingest_file(
                path,
                environment_id=args.environment,
                source_id=f"phase4_{category}_01",
            )
            asset = ingestion.asset
            if asset.media_type in {EvidenceMediaType.VIDEO, EvidenceMediaType.AUDIO}:
                override = (
                    args.video_duration
                    if asset.media_type == EvidenceMediaType.VIDEO
                    else args.audio_duration
                )
                if (asset.duration_seconds is None or asset.duration_seconds <= 0) and override is None:
                    raise SystemExit(
                        f"No se pudo leer la duración de {path.name}. "
                        f"Use --{asset.media_type.value}-duration <segundos>."
                    )
                container.windowing_service.create_windows(
                    asset.id,
                    duration_seconds=args.window_duration,
                    overlap_seconds=args.overlap,
                    total_duration_seconds=override,
                )
            extraction = container.observation_extraction_service.process_asset(asset.id)
            failures.extend(extraction.failures)
            for observation in extraction.observations:
                references = [
                    container.repository.get_evidence_reference(reference_id).to_dict()
                    for reference_id in observation.evidence_reference_ids
                ]
                output.append(
                    {
                        "observation": observation.to_dict(),
                        "evidence_references": references,
                    }
                )

        print(json.dumps(output, ensure_ascii=False, indent=2))
        if failures:
            print(
                json.dumps(
                    {"processor_failures": [failure.__dict__ for failure in failures]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        return 0
    finally:
        container.close()


if __name__ == "__main__":
    raise SystemExit(main())
