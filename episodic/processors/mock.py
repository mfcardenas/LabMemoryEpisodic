"""Procesador determinista inyectable para pruebas de contrato."""
from __future__ import annotations

from episodic.domain.types import EvidenceMediaType, EvidenceReferenceKind, ProvenanceKind
from episodic.processors.base import BaseObservationProcessor, ObservationDraft, ResolvedTarget


class MockProcessor(BaseObservationProcessor):
    name = "mock-processor"
    label = "procesador simulado"  # nombre en el registro de ejecución
    model_version = "phase4-mock-v1"
    supported_modalities = frozenset(EvidenceMediaType)

    def _extract(self, target: ResolvedTarget) -> list[ObservationDraft]:
        media_type = target.asset.media_type
        if media_type in {EvidenceMediaType.AUDIO, EvidenceMediaType.VIDEO}:
            kind = EvidenceReferenceKind.AUDIOVISUAL_INTERVAL
            locator = self.audiovisual_locator(target)
        elif media_type == EvidenceMediaType.IMAGE:
            kind = EvidenceReferenceKind.FRAME
            locator = {"frame_number": 0}
        elif media_type == EvidenceMediaType.DOCUMENT:
            kind = EvidenceReferenceKind.PAGE
            locator = {"page_number": 1}
        else:
            kind = EvidenceReferenceKind.FILE
            locator = {}
        return [
            ObservationDraft(
                description=f"Observación mock determinista para {target.asset.id}.",
                observation_type="mock_observation",
                attributes={"deterministic": True},
                confidence=1.0,
                provenance_kind=ProvenanceKind.SYSTEM_DERIVED,
                evidence_kind=kind,
                evidence_locator=locator,
            )
        ]
