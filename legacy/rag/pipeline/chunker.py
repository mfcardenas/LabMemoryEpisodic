from typing import List
from domain.models import Document, Chunk
from config import settings
from episodic.application.text_fragments import split_text_with_offsets


class TextChunker:
    """Estrategia de fragmentación de texto con solapamiento y preservación de metadatos."""

    def __init__(
        self,
        chunk_size: int = settings.chunk_size,
        chunk_overlap: int = settings.chunk_overlap,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split_text(self, text: str) -> List[str]:
        """Divide el texto respetando párrafos o tamaño máximo con solapamiento."""
        return [
            fragment.text
            for fragment in split_text_with_offsets(
                text,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
            )
        ]

    def chunk_document(self, document: Document) -> List[Chunk]:
        """Convierte un Document en una lista de Chunks heredando sus metadatos."""
        text_pieces = self.split_text(document.content)
        chunks: List[Chunk] = []

        for idx, piece in enumerate(text_pieces):
            chunk_metadata = {
                **document.metadata,
                "source_path": document.source_path,
                "total_chunks": len(text_pieces),
            }
            chunks.append(
                Chunk(
                    doc_id=document.id,
                    chunk_index=idx,
                    text=piece,
                    metadata=chunk_metadata,
                )
            )

        return chunks
