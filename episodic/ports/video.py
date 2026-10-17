"""Contrato de lectura de vídeo: fotogramas muestreados con su tiempo real."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SampledFrame:
    offset_seconds: float
    frame_number: int
    jpeg: bytes
    signature: tuple[int, ...]
    """Miniatura en escala de grises para detectar cambios de escena."""


@dataclass(frozen=True)
class SampledVideo:
    duration_seconds: float | None
    average_fps: float | None
    width: int
    height: int
    frames: tuple[SampledFrame, ...]
    time_source: str
    """``decoder_pts`` si cada fotograma trae su PTS; ``frame_index`` si no."""


class FrameSampler(Protocol):
    sample_fps: float

    @property
    def version(self) -> str: ...

    def sample(self, path: str | Path) -> SampledVideo: ...
