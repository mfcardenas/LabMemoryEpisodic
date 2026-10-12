"""Extracción determinista de texto y JSON con posiciones verificables."""
from __future__ import annotations

import json

from episodic.application.text_fragments import split_text_with_offsets
from episodic.domain.types import EvidenceMediaType, EvidenceReferenceKind, ProvenanceKind
from episodic.processors.base import BaseObservationProcessor, ObservationDraft, ResolvedTarget


class TextProcessor(BaseObservationProcessor):
    name = "text-processor"
    label = "lectura del texto"  # nombre en el registro de ejecución
    model_version = "phase4-text-v1"
    supported_modalities = frozenset({EvidenceMediaType.TEXT})

    def __init__(self, repository, *, chunk_size: int = 1000, chunk_overlap: int = 100):
        super().__init__(repository)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def _extract(self, target: ResolvedTarget) -> list[ObservationDraft]:
        path = self.original_path(target)
        content = path.read_text(encoding="utf-8")
        structured_attributes: dict[str, object] = {}
        observation_type = "text_fragment"
        if target.asset.mime_type == "application/json":
            parsed = json.loads(content)
            observation_type = "structured_text_fragment"
            structured_attributes = {
                "structured_type": type(parsed).__name__,
                "top_level_keys": sorted(parsed) if isinstance(parsed, dict) else [],
            }

        fragments = split_text_with_offsets(
            content,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        return [
            ObservationDraft(
                description=fragment.text,
                observation_type=observation_type,
                attributes={
                    "fragment_index": fragment.index,
                    "character_start": fragment.start,
                    "character_end": fragment.end,
                    "encoding": "utf-8",
                    **structured_attributes,
                },
                confidence=1.0,
                provenance_kind=ProvenanceKind.SYSTEM_DERIVED,
                evidence_kind=EvidenceReferenceKind.TEXT_FRAGMENT,
                evidence_locator={
                    "text_start": fragment.start,
                    "text_end": fragment.end,
                    "text_quote": fragment.text,
                },
            )
            for fragment in fragments
        ]
