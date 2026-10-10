"""Contratos de encoders para el dominio episódico.

Registra identificador, revisión, dimensión y normalización.
No declara una revisión inmutable usando solo el nombre del modelo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol


@dataclass(frozen=True)
class EncoderInfo:
    """Metadatos del encoder para registro y versionado."""
    model_id: str
    revision: str        # checksum o tag resuelto
    dimension: int
    normalized: bool     # ¿vectores unitarios?
    runtime: str = ""    # e.g. "onnxruntime-1.27.0"


class EpisodeTextEncoder(Protocol):
    """Encoder de texto para documentos y queries (Nomic)."""
    @property
    def info(self) -> EncoderInfo: ...
    def embed_documents(self, texts: List[str]) -> List[List[float]]: ...
    def embed_query(self, text: str) -> List[float]: ...


class ImageEncoder(Protocol):
    """Encoder visual (CLIP vision tower)."""
    @property
    def info(self) -> EncoderInfo: ...
    def embed_images(self, image_paths: List[str]) -> List[List[float]]: ...


class AlignedTextEncoder(Protocol):
    """Encoder de texto alineado con el espacio visual (CLIP text tower).

    Permite búsqueda texto→imagen en el mismo espacio que CLIP vision.
    Falta en robot/brain/models.py pero está en helper.py (hallazgo #8).
    """
    @property
    def info(self) -> EncoderInfo: ...
    def embed_query(self, text: str) -> List[float]: ...
