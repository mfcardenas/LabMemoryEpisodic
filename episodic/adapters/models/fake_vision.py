"""Descriptor de visión determinista para pruebas sin modelo."""
from __future__ import annotations

import io
from typing import Sequence

from PIL import Image, ImageStat

from episodic.ports.vision import SegmentContext, SegmentDescription


class FakeVisionDescriber:
    """Describe el brillo medio de los fotogramas; nunca afirma acciones reales."""

    model_id = "fake-vision"
    prompt_version = "fake-v1"

    def __init__(self) -> None:
        self.calls: list[SegmentContext] = []

    def describe_segment(
        self, frames_jpeg: Sequence[bytes], context: SegmentContext
    ) -> SegmentDescription:
        self.calls.append(context)
        brightness = [
            ImageStat.Stat(Image.open(io.BytesIO(item)).convert("L")).mean[0]
            for item in frames_jpeg
        ]
        level = sum(brightness) / len(brightness)
        title = f"Tramo {context.segment_index + 1} de vídeo"
        return SegmentDescription(
            title=title,
            summary=f"{title}: brillo medio {level:.0f}.",
            detailed_description=(
                f"Descripción simulada del segundo {context.start_offset_seconds:.1f} "
                f"al {context.end_offset_seconds:.1f} con {len(frames_jpeg)} fotograma(s)."
            ),
            scene="escena simulada",
            uncertainty="Descriptor de prueba: no interpreta el contenido.",
        )
