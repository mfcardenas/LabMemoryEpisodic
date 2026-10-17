"""Observaciones técnicas de vídeo por ventana verificable."""
from __future__ import annotations

from episodic.domain.types import EvidenceMediaType, EvidenceReferenceKind, ProvenanceKind
from episodic.processors.base import BaseObservationProcessor, ObservationDraft, ResolvedTarget


class VideoProcessor(BaseObservationProcessor):
    name = "video-processor"
    label = "ventana de vídeo"  # nombre en el registro de ejecución
    model_version = "phase4-video-window-v1"
    supported_modalities = frozenset({EvidenceMediaType.VIDEO})

    def _extract(self, target: ResolvedTarget) -> list[ObservationDraft]:
        self.original_path(target)
        duration = (target.end_time - target.start_time).total_seconds()
        start_offset = target.window.start_offset_seconds if target.window else 0.0
        end_offset = (
            target.window.end_offset_seconds
            if target.window
            else target.asset.duration_seconds or 0.0
        )
        return [
            ObservationDraft(
                description=(
                    f"Intervalo de vídeo disponible entre {start_offset:.3f} "
                    f"y {end_offset:.3f} segundos."
                ),
                observation_type="video_interval",
                attributes={
                    "duration_seconds": duration,
                    "start_offset_seconds": start_offset,
                    "end_offset_seconds": end_offset,
                    "detections": [],
                    "decoder": None,
                },
                confidence=1.0,
                provenance_kind=ProvenanceKind.SYSTEM_DERIVED,
                evidence_kind=EvidenceReferenceKind.AUDIOVISUAL_INTERVAL,
                evidence_locator=self.audiovisual_locator(target),
            )
        ]
