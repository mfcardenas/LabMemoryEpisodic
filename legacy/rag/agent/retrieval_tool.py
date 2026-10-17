from typing import Any, Dict, List, Optional
from domain.models import SearchResult
from embeddings.base import DenseEmbedder, SparseEmbedder
from storage.qdrant_store import QdrantHybridStore


class DomainRetrieverTool:
    """
    Herramienta desacoplada para Agentes Inteligentes.
    Permite recuperar información relevante mediante búsqueda híbrida sobre cualquier dominio (colección).
    """

    def __init__(
        self,
        store: QdrantHybridStore,
        dense_embedder: DenseEmbedder,
        sparse_embedder: SparseEmbedder,
    ):
        self.store = store
        self.dense_embedder = dense_embedder
        self.sparse_embedder = sparse_embedder

    def search(
        self,
        query: str,
        collection_name: str,
        metadata_filter: Optional[Dict[str, Any]] = None,
        limit: int = 4,
    ) -> List[SearchResult]:
        """Ejecuta la búsqueda híbrida denso + sparse con RRF sobre la colección especificada."""
        # 1. Vectorizar la consulta
        dense_vec = self.dense_embedder.embed_query(query)
        sparse_vec = self.sparse_embedder.embed_query(query)

        # 2. Consultar Qdrant
        return self.store.hybrid_search(
            dense_query=dense_vec,
            sparse_query=sparse_vec,
            collection_name=collection_name,
            metadata_filter=metadata_filter,
            limit=limit,
        )

    def retrieve_formatted_context(
        self,
        query: str,
        collection_name: str,
        metadata_filter: Optional[Dict[str, Any]] = None,
        limit: int = 4,
    ) -> str:
        """Recupera los resultados y los formatea como un bloque de contexto legible para el LLM."""
        results = self.search(
            query=query,
            collection_name=collection_name,
            metadata_filter=metadata_filter,
            limit=limit,
        )

        if not results:
            return "No se encontraron documentos relevantes para esta consulta en el dominio especificado."

        formatted_pieces = []
        for i, res in enumerate(results, start=1):
            meta_str = ", ".join(f"{k}: {v}" for k, v in res.metadata.items())
            piece = (
                f"--- [Fuente {i} | Dominio: {res.collection} | Score RRF: {res.score:.4f} | Metadatos: ({meta_str})] ---\n"
                f"{res.text}\n"
            )
            formatted_pieces.append(piece)

        return "\n".join(formatted_pieces)

    @classmethod
    def get_tool_schema(cls) -> Dict[str, Any]:
        """Esquema de herramienta compatible con Function Calling (OpenAI / Ollama / Agentes)."""
        return {
            "type": "function",
            "function": {
                "name": "retrieve_from_domain",
                "description": "Recupera fragmentos de información relevante usando búsqueda híbrida en un dominio específico.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Pregunta o texto de consulta a buscar.",
                        },
                        "collection_name": {
                            "type": "string",
                            "description": "Nombre de la colección o dominio de conocimiento (ej: 'memoria_episodica', 'documentacion_tecnica').",
                        },
                        "metadata_filter": {
                            "type": "object",
                            "description": "Filtro opcional por metadatos (ej: {'author': 'admin', 'category': 'research'}).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Número máximo de fragmentos a recuperar.",
                            "default": 4,
                        },
                    },
                    "required": ["query", "collection_name"],
                },
            },
        }
