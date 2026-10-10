from abc import ABC, abstractmethod
from typing import List
from qdrant_client.models import SparseVector


class DenseEmbedder(ABC):
    """Estrategia abstracta para generación de vectores densos (semántica)."""

    @abstractmethod
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Genera embeddings densos para una lista de textos."""
        pass

    @abstractmethod
    def embed_query(self, text: str) -> List[float]:
        """Genera embedding denso para una única consulta."""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensión del vector denso."""
        pass


class SparseEmbedder(ABC):
    """Estrategia abstracta para generación de vectores dispersos (léxica / BM25)."""

    @abstractmethod
    def embed_documents(self, texts: List[str]) -> List[SparseVector]:
        """Genera representaciones sparse para una lista de textos."""
        pass

    @abstractmethod
    def embed_query(self, text: str) -> SparseVector:
        """Genera representación sparse para una consulta."""
        pass
