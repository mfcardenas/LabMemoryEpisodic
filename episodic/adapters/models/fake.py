"""Encoders deterministas para pruebas unitarias sin dependencias externas.

Generan vectores deterministas normalizados basados en hash del contenido,
permitiendo validar toda la lógica de dominio, persistencia, outbox y búsqueda
sin necesidad de descargar modelos pesados ni tener GPU.
"""
from __future__ import annotations

import hashlib
import math
from typing import List

from episodic.ports.encoders import (
    AlignedTextEncoder,
    EncoderInfo,
    EpisodeTextEncoder,
    ImageEncoder,
)


def _hash_to_unit_vector(text: str, dim: int) -> List[float]:
    """Genera un vector unitario determinista de dimensión `dim` para `text`."""
    vec = []
    for i in range(dim):
        h = hashlib.sha256(f"{text}:{i}".encode("utf-8")).digest()
        # mapear a float entre -1.0 y 1.0
        val = (int.from_bytes(h[:4], "big") / (2**32 - 1)) * 2.0 - 1.0
        vec.append(val)
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeEpisodeTextEncoder:
    """Fake determinista para EpisodeTextEncoder."""

    def __init__(self, dimension: int = 768) -> None:
        self._dim = dimension

    @property
    def info(self) -> EncoderInfo:
        return EncoderInfo(
            model_id="fake-nomic-text",
            revision="v1-test",
            dimension=self._dim,
            normalized=True,
            runtime="fake-deterministic",
        )

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [_hash_to_unit_vector(t, self._dim) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return _hash_to_unit_vector(text, self._dim)


class FakeImageEncoder:
    """Fake determinista para ImageEncoder."""

    def __init__(self, dimension: int = 512) -> None:
        self._dim = dimension

    @property
    def info(self) -> EncoderInfo:
        return EncoderInfo(
            model_id="fake-clip-vision",
            revision="v1-test",
            dimension=self._dim,
            normalized=True,
            runtime="fake-deterministic",
        )

    def embed_images(self, image_paths: List[str]) -> List[List[float]]:
        return [_hash_to_unit_vector(p, self._dim) for p in image_paths]


class FakeAlignedTextEncoder:
    """Fake determinista para AlignedTextEncoder."""

    def __init__(self, dimension: int = 512) -> None:
        self._dim = dimension

    @property
    def info(self) -> EncoderInfo:
        return EncoderInfo(
            model_id="fake-clip-text",
            revision="v1-test",
            dimension=self._dim,
            normalized=True,
            runtime="fake-deterministic",
        )

    def embed_query(self, text: str) -> List[float]:
        return _hash_to_unit_vector(text, self._dim)
