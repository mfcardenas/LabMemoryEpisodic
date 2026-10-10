"""
Punto de entrada principal para desplegar el servidor web y la API de RAG Híbrido.
Ejecuta: python app.py
"""

import sys
import uvicorn
from config import settings

if __name__ == "__main__":
    # Asegurar codificación utf-8
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("\n" + "=" * 70)
    print("🚀 DESPLEGANDO SERVIDOR RAG HÍBRIDO (QDRANT + OLLAMA)")
    print("=" * 70)
    print(f" -> Interfaz Web:  http://localhost:8000")
    print(f" -> Documentación: http://localhost:8000/docs")
    print(f" -> Qdrant Host:   {settings.qdrant_host}:{settings.qdrant_port}")
    print(f" -> Ollama URL:    {settings.ollama_url}")
    print(f" -> Modelo LLM:    {settings.ollama_llm_model}")
    print(f" -> Embeddings:    {settings.ollama_embed_model} + {settings.sparse_model_name}")
    print("=" * 70 + "\n")

    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
