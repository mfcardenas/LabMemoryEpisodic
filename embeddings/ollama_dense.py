from typing import List, Optional
import requests
from embeddings.base import DenseEmbedder
from config import settings


class OllamaDenseEmbedder(DenseEmbedder):
    """Generador de embeddings densos utilizando el endpoint nativo de Ollama."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        vector_size: Optional[int] = None,
        timeout: int = 60,
    ):
        self.base_url = (base_url or settings.ollama_url).rstrip("/")
        self.model_name = model_name or settings.ollama_embed_model
        self.timeout = timeout
        self._dimension = vector_size or settings.dense_vector_size

    @property
    def dimension(self) -> int:
        if self._dimension <= 0:
            # Autodetección de la dimensión en caso de no especificarse
            sample = self.embed_query("init_dimension_probe")
            self._dimension = len(sample)
        return self._dimension

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        url = f"{self.base_url}/api/embed"
        payload = {"model": self.model_name, "input": texts}

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings", [])
            if not self._dimension and embeddings:
                self._dimension = len(embeddings[0])
            return embeddings
        except Exception as exc:
            raise RuntimeError(
                f"Error al generar embeddings en Ollama ({self.model_name}): {exc}"
            ) from exc

    def embed_query(self, text: str) -> List[float]:
        res = self.embed_documents([text])
        if not res:
            raise ValueError("No se pudo obtener embedding para la consulta.")
        return res[0]
