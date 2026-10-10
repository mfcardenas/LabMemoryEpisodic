from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid


class Document(BaseModel):
    """Representa un documento de entrada con su contenido y metadatos personalizados."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    source_path: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    """Representa una porción indexable del documento."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    doc_id: str
    chunk_index: int
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    """Resultado estructurado retornado por el retriever."""
    chunk_id: str
    doc_id: str
    text: str
    score: float
    collection: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RetrievalQuery(BaseModel):
    """Consulta de búsqueda con soporte de colección de dominio y filtros."""
    query: str
    collection_name: Optional[str] = None
    metadata_filter: Optional[Dict[str, Any]] = None
    limit: int = 4
