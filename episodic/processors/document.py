"""Extracción de PDF por página mediante un adaptador cargado en diferido."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from episodic.application.text_fragments import split_text_with_offsets
from episodic.domain.types import EvidenceMediaType, EvidenceReferenceKind, ProvenanceKind
from episodic.processors.base import BaseObservationProcessor, ObservationDraft, ResolvedTarget


class PageTextExtractor(Protocol):
    def extract_pages(self, path: Path) -> list[str]: ...


class PyPdfTextExtractor:
    """Importa pypdf solo al procesar un PDF; el núcleo sigue siendo ligero."""

    def extract_pages(self, path: Path) -> list[str]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError(
                "DocumentProcessor requiere pypdf; instale las dependencias actualizadas"
            ) from exc
        reader = PdfReader(str(path))
        return [(page.extract_text() or "") for page in reader.pages]


class DocumentProcessor(BaseObservationProcessor):
    name = "document-processor"
    model_version = "phase4-pypdf-v1"
    supported_modalities = frozenset({EvidenceMediaType.DOCUMENT})

    def __init__(
        self,
        repository,
        *,
        extractor: PageTextExtractor | None = None,
        chunk_size: int = 1000,
        chunk_overlap: int = 100,
    ) -> None:
        super().__init__(repository)
        self.extractor = extractor or PyPdfTextExtractor()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def _extract(self, target: ResolvedTarget) -> list[ObservationDraft]:
        path = self.original_path(target)
        if target.asset.mime_type != "application/pdf":
            raise ValueError(f"Documento no soportado: {target.asset.mime_type}")
        drafts: list[ObservationDraft] = []
        for page_number, page_text in enumerate(self.extractor.extract_pages(path), start=1):
            fragments = split_text_with_offsets(
                page_text,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
            )
            if not fragments:
                drafts.append(
                    ObservationDraft(
                        description=f"Página {page_number} sin texto extraíble.",
                        observation_type="document_page_status",
                        attributes={
                            "page_number": page_number,
                            "extraction_status": "no_text",
                        },
                        confidence=1.0,
                        provenance_kind=ProvenanceKind.SYSTEM_DERIVED,
                        evidence_kind=EvidenceReferenceKind.PAGE,
                        evidence_locator={"page_number": page_number},
                    )
                )
                continue
            for fragment in fragments:
                drafts.append(
                    ObservationDraft(
                        description=fragment.text,
                        observation_type="document_text_fragment",
                        attributes={
                            "page_number": page_number,
                            "fragment_index": fragment.index,
                            "page_character_start": fragment.start,
                            "page_character_end": fragment.end,
                        },
                        confidence=1.0,
                        provenance_kind=ProvenanceKind.SYSTEM_DERIVED,
                        evidence_kind=EvidenceReferenceKind.PAGE,
                        evidence_locator={"page_number": page_number},
                    )
                )
        return drafts
