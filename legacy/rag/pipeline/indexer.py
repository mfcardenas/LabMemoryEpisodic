import os
from typing import Any, Dict, List, Optional
from domain.models import Document, Chunk
from embeddings.base import DenseEmbedder, SparseEmbedder
from storage.qdrant_store import QdrantHybridStore
from pipeline.chunker import TextChunker


class DomainIndexer:
    """
    Orquestador del pipeline de ingesta e indexación híbrida en Qdrant.
    Permite asociar metadatos personalizados y seleccionar colecciones de dominio.
    """

    def __init__(
        self,
        store: QdrantHybridStore,
        dense_embedder: DenseEmbedder,
        sparse_embedder: SparseEmbedder,
        chunker: Optional[TextChunker] = None,
    ):
        self.store = store
        self.dense_embedder = dense_embedder
        self.sparse_embedder = sparse_embedder
        self.chunker = chunker or TextChunker()

    def index_text(
        self,
        content: str,
        collection_name: str,
        custom_metadata: Optional[Dict[str, Any]] = None,
        source_id: Optional[str] = None,
    ) -> int:
        """Indexa un texto en memoria con metadatos personalizados en una colección dada."""
        doc = Document(
            content=content,
            source_path=source_id,
            metadata=custom_metadata or {},
        )
        return self._process_and_upsert([doc], collection_name)

    def index_file(
        self,
        file_path: str,
        collection_name: str,
        custom_metadata: Optional[Dict[str, Any]] = None,
        encoding: str = "utf-8",
    ) -> int:
        """Lee un fichero local e indexa su contenido enriquecido con metadatos personalizados."""
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"No se encontró el fichero: {file_path}")

        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()

        file_metadata = {
            "file_name": os.path.basename(file_path),
            "file_extension": os.path.splitext(file_path)[1].lower(),
            **(custom_metadata or {}),
        }

        doc = Document(
            content=content,
            source_path=os.path.abspath(file_path),
            metadata=file_metadata,
        )

        return self._process_and_upsert([doc], collection_name)

    def index_directory(
        self,
        dir_path: str,
        collection_name: str,
        common_metadata: Optional[Dict[str, Any]] = None,
        allowed_extensions: tuple = (".txt", ".md", ".json", ".csv"),
    ) -> int:
        """Indexa de forma recursiva los ficheros compatibles de un directorio."""
        if not os.path.isdir(dir_path):
            raise NotADirectoryError(f"Directorio no válido: {dir_path}")

        total_chunks = 0
        for root, _, files in os.walk(dir_path):
            for file in files:
                if any(file.endswith(ext) for ext in allowed_extensions):
                    path = os.path.join(root, file)
                    total_chunks += self.index_file(
                        file_path=path,
                        collection_name=collection_name,
                        custom_metadata=common_metadata,
                    )
        return total_chunks

    def _process_and_upsert(
        self, documents: List[Document], collection_name: str
    ) -> int:
        """Fragmenta, vectoriza y persiste los documentos en lotes."""
        all_chunks: List[Chunk] = []
        for doc in documents:
            chunks = self.chunker.chunk_document(doc)
            all_chunks.extend(chunks)

        if not all_chunks:
            return 0

        # Vectorización densa y dispersa
        texts = [chunk.text for chunk in all_chunks]
        dense_vectors = self.dense_embedder.embed_documents(texts)
        sparse_vectors = self.sparse_embedder.embed_documents(texts)

        # Inserción en el almacén vectorial de Qdrant
        self.store.upsert_chunks(
            chunks=all_chunks,
            dense_vectors=dense_vectors,
            sparse_vectors=sparse_vectors,
            collection_name=collection_name,
        )

        return len(all_chunks)
