from typing import List, Optional
from fastembed import SparseTextEmbedding
from qdrant_client.models import SparseVector
from embeddings.base import SparseEmbedder
from config import settings


class FastSparseEmbedder(SparseEmbedder):
    """Generador de embeddings dispersos usando FastEmbed (BM25 de Qdrant)."""

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or settings.sparse_model_name
        self._model = SparseTextEmbedding(model_name=self.model_name)

    def embed_documents(self, texts: List[str]) -> List[SparseVector]:
        if not texts:
            return []

        # FastEmbed genera objetos con attributes `indices` y `values`
        embeddings_generator = self._model.embed(texts)
        results: List[SparseVector] = []
        for emb in embeddings_generator:
            results.append(
                SparseVector(
                    indices=emb.indices.tolist(),
                    values=emb.values.tolist(),
                )
            )
        return results

    def embed_query(self, text: str) -> SparseVector:
        res = self.embed_documents([text])
        if not res:
            raise ValueError("No se pudo obtener vector sparse para la consulta.")
        return res[0]
