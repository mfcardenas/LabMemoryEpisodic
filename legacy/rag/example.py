"""
Demostración Integral de RAG Híbrido con Qdrant, FastEmbed (BM25) y Ollama (gpt-oss).
Permite gestionar múltiples colecciones por dominio e indexar con metadatos personalizados.
"""

import json
import sys

# Asegurar encoding UTF-8 en la salida de consola (evitar errores cp1252 en Windows)
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import settings
from embeddings import OllamaDenseEmbedder, FastSparseEmbedder
from storage import QdrantHybridStore
from pipeline import DomainIndexer
from agent import DomainRetrieverTool, DomainRAGAgent, OllamaLLMClient


def main():
    print("=" * 70)
    print("INICIALIZANDO RAG HÍBRIDO MULTIDOMINIO (QDRANT + OLLAMA)")
    print("=" * 70)

    # 1. Instanciación mediante patrones Strategy e Inyección de Dependencias
    print("\n[1/5] Inicializando componentes...")
    dense_embedder = OllamaDenseEmbedder(
        base_url=settings.ollama_url,
        model_name=settings.ollama_embed_model,
        vector_size=settings.dense_vector_size,
    )
    sparse_embedder = FastSparseEmbedder(model_name=settings.sparse_model_name)
    store = QdrantHybridStore(dense_size=dense_embedder.dimension)
    indexer = DomainIndexer(
        store=store,
        dense_embedder=dense_embedder,
        sparse_embedder=sparse_embedder,
    )

    # 2. Definición de dos colecciones de dominio distintas
    DOMAIN_EPISODIC = "dominio_memoria_episodica"
    DOMAIN_SYSTEMS = "dominio_sistemas_infraestructura"

    print(f"\n[2/5] Indexando documentos con metadatos personalizados...")

    # Documento 1: Memoria Episódica
    metadata_episodic = {
        "author": "Agente-Alpha",
        "experiment_id": "EXP-101",
        "sensitivity": "confidencial",
        "checkpoint": "CKPT-ALPHA-9981",
        "tags": ["memoria", "rrf", "estres"],
    }
    chunks_episodic = indexer.index_file(
        file_path="data_samples/episodic_memory.txt",
        collection_name=DOMAIN_EPISODIC,
        custom_metadata=metadata_episodic,
    )
    print(f" -> Colección '{DOMAIN_EPISODIC}': {chunks_episodic} fragmentos indexados.")
    print(f"    Metadatos asociados: {json.dumps(metadata_episodic, ensure_ascii=False)}")

    # Documento 2: Especificaciones de Sistemas
    metadata_systems = {
        "author": "DevOps-Team",
        "project": "Infra-Core",
        "environment": "production",
        "version": "2.4.0",
        "tags": ["docker", "hnsw", "puertos"],
    }
    chunks_systems = indexer.index_file(
        file_path="data_samples/tech_specs.md",
        collection_name=DOMAIN_SYSTEMS,
        custom_metadata=metadata_systems,
    )
    print(f" -> Colección '{DOMAIN_SYSTEMS}': {chunks_systems} fragmentos indexados.")
    print(f"    Metadatos asociados: {json.dumps(metadata_systems, ensure_ascii=False)}")

    # Listar colecciones activas en Qdrant
    print(f"\nColecciones en Qdrant actualmente: {store.list_collections()}")

    # 3. Prueba de recuperación híbrida directa con filtro de metadatos
    print("\n[3/5] Probando Búsqueda Híbrida (Dense + BM25 con RRF) con filtrado...")
    retriever_tool = DomainRetrieverTool(
        store=store,
        dense_embedder=dense_embedder,
        sparse_embedder=sparse_embedder,
    )

    query_episodic = "¿Cuál fue el resultado del protocolo RRF y el checkpoint generado?"
    results = retriever_tool.search(
        query=query_episodic,
        collection_name=DOMAIN_EPISODIC,
        metadata_filter={"experiment_id": "EXP-101"},
        limit=2,
    )

    print(f"\nConsulta: '{query_episodic}'")
    for r in results:
        print(f" * Score RRF: {r.score:.4f} | ID: {r.chunk_id}")
        print(f"   Metadatos: {r.metadata}")
        print(f"   Texto: {r.text[:140]}...")

    # 4. Agente Inteligente resolviendo una consulta en el dominio de Sistemas
    print(f"\n[4/5] Invocando al Agente con modelo LLM '{settings.ollama_llm_model}'...")
    agent = DomainRAGAgent(
        retriever_tool=retriever_tool,
        llm_client=OllamaLLMClient(model_name=settings.ollama_llm_model),
    )

    query_agent = "¿Qué modelos procesa Ollama y cuáles son las recomendaciones para HNSW en producción?"
    print(f"\nPregunta dirigida al dominio '{DOMAIN_SYSTEMS}': '{query_agent}'")
    
    response = agent.ask(
        question=query_agent,
        domain_collection=DOMAIN_SYSTEMS,
        limit=2,
    )

    print("\n" + "=" * 70)
    print("RESPUESTA SINTETIZADA POR EL AGENTE:")
    print("=" * 70)
    print(response["answer"])

    print("\nFUENTES Y METADATOS UTILIZADOS POR EL AGENTE:")
    for src in response["sources"]:
        print(f" - Doc Source: {src['metadata'].get('file_name')} | Autor: {src['metadata'].get('author')} | Score: {src['score_rrf']}")

    print("\n" + "=" * 70)
    print("FLUJO COMPLETADO CON ÉXITO")
    print("=" * 70)


if __name__ == "__main__":
    main()
