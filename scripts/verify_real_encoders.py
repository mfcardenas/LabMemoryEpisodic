"""Smoke reproducible de Nomic y CLIP reales, sin escribir en la memoria."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from episodic.adapters.models.fastembed import (
    FastEmbedAlignedTextEncoder,
    FastEmbedEpisodeTextEncoder,
    FastEmbedImageEncoder,
)
from episodic.config import EpisodicConfig


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def _timed(operation):
    started = time.perf_counter()
    value = operation()
    return value, round((time.perf_counter() - started) * 1000.0, 3)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        default="data/atlas/media/c01_180133_p01_entry.png",
        help="Imagen local usada para comprobar CLIP vision",
    )
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.is_file():
        raise SystemExit(f"No existe la imagen de prueba: {image_path}")

    config = EpisodicConfig()
    common = {
        "cache_dir": config.fastembed_cache_dir,
        "threads": config.encoder_threads,
    }

    nomic = FastEmbedEpisodeTextEncoder(
        model_id=config.nomic_model,
        dimension=config.nomic_dim,
        **common,
    )
    clip_text = FastEmbedAlignedTextEncoder(
        model_id=config.clip_text_model,
        dimension=config.clip_dim,
        **common,
    )
    clip_vision = FastEmbedImageEncoder(
        model_id=config.clip_vision_model,
        dimension=config.clip_dim,
        **common,
    )

    document, nomic_document_ms = _timed(
        lambda: nomic.embed_documents(["persona con chaqueta azul y mochila reflectante"])[0]
    )
    query, nomic_query_ms = _timed(lambda: nomic.embed_query("chaqueta azul"))
    aligned, clip_text_ms = _timed(
        lambda: clip_text.embed_query("mochila con franja reflectante")
    )
    image, clip_vision_ms = _timed(
        lambda: clip_vision.embed_images([str(image_path)])[0]
    )

    result = {
        "nomic_document": {
            "dimension": len(document),
            "l2_norm": round(_norm(document), 6),
            "duration_ms": nomic_document_ms,
        },
        "nomic_query": {
            "dimension": len(query),
            "l2_norm": round(_norm(query), 6),
            "duration_ms": nomic_query_ms,
            "metadata_normalized": nomic.info.normalized,
        },
        "clip_text": {
            "dimension": len(aligned),
            "l2_norm": round(_norm(aligned), 6),
            "duration_ms": clip_text_ms,
        },
        "clip_vision": {
            "dimension": len(image),
            "l2_norm": round(_norm(image), 6),
            "duration_ms": clip_vision_ms,
            "image": str(image_path),
        },
    }

    expected = (config.nomic_dim, config.nomic_dim, config.clip_dim, config.clip_dim)
    actual = (len(document), len(query), len(aligned), len(image))
    if actual != expected:
        raise SystemExit(f"Dimensiones inesperadas: actual={actual}, expected={expected}")

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
