from typing import Any, Dict, List, Optional
from qdrant_client import QdrantClient, models
from config import settings
from domain.models import Chunk, SearchResult


class QdrantHybridStore:
    """
    Repositorio de persistencia y búsqueda híbrida en Qdrant.
    Soporta múltiples colecciones para aislar dominios de conocimiento.
    """

    def __init__(
        self,
        client: Optional[QdrantClient] = None,
        dense_size: Optional[int] = None,
    ):
        self.client = client or QdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
        )
        self.dense_size = dense_size or settings.dense_vector_size

    def list_collections(self) -> List[str]:
        """Devuelve los nombres de las colecciones existentes."""
        resp = self.client.get_collections()
        return [c.name for c in resp.collections]

    def get_collections_with_stats(self) -> List[Dict[str, Any]]:
        """Devuelve las colecciones junto con el conteo real de puntos/chunks en Qdrant."""
        collections = self.list_collections()
        result = []
        for name in collections:
            try:
                count_info = self.client.count(collection_name=name, exact=True)
                points_count = count_info.count
            except Exception:
                points_count = 0
            result.append({
                "name": name,
                "points_count": points_count,
            })
        return result

    def ensure_collection(self, collection_name: str) -> None:
        """Crea la colección si no existe, configurada para búsqueda híbrida (Dense + Sparse)."""
        if not self.client.collection_exists(collection_name=collection_name):
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config={
                    "dense": models.VectorParams(
                        size=self.dense_size,
                        distance=models.Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    "sparse": models.SparseVectorParams(
                        index=models.SparseIndexParams(on_disk=False)
                    )
                },
            )

    def get_collection_points(self, collection_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Recupera los chunks y metadatos almacenados en una colección dada."""
        if not self.client.collection_exists(collection_name=collection_name):
            return []
        records, _ = self.client.scroll(
            collection_name=collection_name,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [
            {
                "id": str(r.id),
                "payload": r.payload or {},
            }
            for r in records
        ]

    def upsert_chunks(
        self,
        chunks: List[Chunk],
        dense_vectors: List[List[float]],
        sparse_vectors: List[models.SparseVector],
        collection_name: Optional[str] = None,
    ) -> None:
        """Almacena chunks con sus representaciones vectoriales duales y metadatos."""
        target_col = collection_name or settings.default_collection
        self.ensure_collection(target_col)

        points = []
        for chunk, dense_vec, sparse_vec in zip(chunks, dense_vectors, sparse_vectors):
            # Combinamos datos del chunk y el diccionario arbitrario de metadatos
            payload: Dict[str, Any] = {
                "doc_id": chunk.doc_id,
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                **chunk.metadata,  # Metadatos personalizados directamente accesibles para filtrado
            }

            point = models.PointStruct(
                id=chunk.id,
                vector={
                    "dense": dense_vec,
                    "sparse": sparse_vec,
                },
                payload=payload,
            )
            points.append(point)

        self.client.upsert(
            collection_name=target_col,
            points=points,
            wait=True,
        )

    def hybrid_search(
        self,
        dense_query: List[float],
        sparse_query: models.SparseVector,
        collection_name: Optional[str] = None,
        metadata_filter: Optional[Dict[str, Any]] = None,
        limit: int = 4,
    ) -> List[SearchResult]:
        """
        Ejecuta búsqueda híbrida combinando dense y sparse con Reciprocal Rank Fusion (RRF).
        Permite filtrar por cualquier clave del diccionario de metadatos.
        """
        target_col = collection_name or settings.default_collection
        self.ensure_collection(target_col)

        # Construcción dinámica de filtros de metadatos
        qdrant_filter = None
        if metadata_filter:
            must_conditions = []
            for key, val in metadata_filter.items():
                must_conditions.append(
                    models.FieldCondition(
                        key=key,
                        match=models.MatchValue(value=val),
                    )
                )
            if must_conditions:
                qdrant_filter = models.Filter(must=must_conditions)

        # Query points usando Prefetch + RRF
        query_response = self.client.query_points(
            collection_name=target_col,
            prefetch=[
                models.Prefetch(
                    query=dense_query,
                    using="dense",
                    filter=qdrant_filter,
                    limit=limit * 2,
                ),
                models.Prefetch(
                    query=sparse_query,
                    using="sparse",
                    filter=qdrant_filter,
                    limit=limit * 2,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
        )

        results: List[SearchResult] = []
        for point in query_response.points:
            payload = point.payload or {}
            text = payload.pop("text", "")
            doc_id = payload.pop("doc_id", "")
            results.append(
                SearchResult(
                    chunk_id=str(point.id),
                    doc_id=doc_id,
                    text=text,
                    score=point.score if point.score is not None else 0.0,
                    collection=target_col,
                    metadata=payload,
                )
            )

        return results
