"""
Configuración centralizada y tipada del sistema RAG Híbrido.
Permite sobreescritura mediante variables de entorno o archivo .env.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Configuración de Qdrant
    qdrant_host: str = Field(default="localhost", description="Host de Qdrant")
    qdrant_port: int = Field(default=6333, description="Puerto HTTP de Qdrant")
    qdrant_grpc_port: int = Field(default=6334, description="Puerto gRPC de Qdrant")
    default_collection: str = Field(default="default_domain", description="Colección por defecto")

    # Configuración de Ollama (LLM y Dense Embeddings)
    ollama_url: str = Field(default="http://localhost:11434", description="URL del servicio Ollama")
    ollama_llm_model: str = Field(default="gpt-oss:latest", description="Modelo LLM para síntesis y agente")
    ollama_embed_model: str = Field(default="nomic-embed-text:latest", description="Modelo para embeddings densos")
    dense_vector_size: int = Field(default=768, description="Dimensión del vector denso")

    # Configuración de Sparse Embeddings (BM25 nativo rápido)
    sparse_model_name: str = Field(default="Qdrant/bm25", description="Modelo sparse para FastEmbed")

    # Parámetros de Ingesta y Recuperación
    chunk_size: int = Field(default=600, description="Tamaño de cada fragmento de texto")
    chunk_overlap: int = Field(default=100, description="Solapamiento entre fragmentos")
    search_limit: int = Field(default=4, description="Resultados top a recuperar")


# Instancia singleton accesible
settings = AppSettings()
