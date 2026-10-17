"""Inspección visual básica sin inventar detecciones semánticas."""
from __future__ import annotations

from PIL import Image

from episodic.domain.types import EvidenceMediaType, EvidenceReferenceKind, ProvenanceKind
from episodic.processors.base import BaseObservationProcessor, ObservationDraft, ResolvedTarget


class ImageProcessor(BaseObservationProcessor):
    name = "image-processor"
    label = "imagen"  # nombre en el registro de ejecución
    model_version = "phase4-pillow-v1"
    supported_modalities = frozenset({EvidenceMediaType.IMAGE})

    def _extract(self, target: ResolvedTarget) -> list[ObservationDraft]:
        path = self.original_path(target)
        with Image.open(path) as image:
            attributes = {
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
                "format": image.format,
                "semantic_labels": [],
            }
        return [
            ObservationDraft(
                description=(
                    f"Imagen {attributes['width']}x{attributes['height']} "
                    f"en modo {attributes['mode']}."
                ),
                observation_type="image_frame_metadata",
                attributes=attributes,
                confidence=1.0,
                provenance_kind=ProvenanceKind.SYSTEM_DERIVED,
                evidence_kind=EvidenceReferenceKind.FRAME,
                evidence_locator={"frame_number": 0},
            )
        ]
