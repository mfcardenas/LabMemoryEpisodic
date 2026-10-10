"""Contrato del modelo de visión que describe segmentos de vídeo."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class SegmentContext:
    """Lo que el modelo necesita saber del segmento, además de sus fotogramas."""

    segment_index: int
    segment_count: int
    start_offset_seconds: float
    end_offset_seconds: float
    video_duration_seconds: float | None
    frame_offsets_seconds: tuple[float, ...]
    previous_summary: str | None = None
    language: str = "es"


@dataclass(frozen=True)
class SegmentDescription:
    """Descripción estructurada de lo que se ve en un segmento.

    Es texto generado por un modelo: se persiste con su procedencia y nunca se
    presenta como un hecho verificado.
    """

    title: str
    summary: str
    detailed_description: str
    scene: str = ""
    actions: tuple[str, ...] = ()
    actors: tuple[dict[str, str], ...] = ()
    objects: tuple[dict[str, Any], ...] = ()
    visible_text: tuple[str, ...] = ()
    changes_from_previous: str = ""
    uncertainty: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "detailed_description": self.detailed_description,
            "scene": self.scene,
            "actions": list(self.actions),
            "actors": [dict(item) for item in self.actors],
            "objects": [dict(item) for item in self.objects],
            "visible_text": list(self.visible_text),
            "changes_from_previous": self.changes_from_previous,
            "uncertainty": self.uncertainty,
        }


class VisionDescriberError(RuntimeError):
    """El modelo de visión no está disponible o devolvió una respuesta inválida."""


class VisionDescriber(Protocol):
    @property
    def model_id(self) -> str:
        """Identificador del modelo, registrado como procedencia."""
        ...

    @property
    def prompt_version(self) -> str: ...

    def describe_segment(
        self, frames_jpeg: Sequence[bytes], context: SegmentContext
    ) -> SegmentDescription: ...
