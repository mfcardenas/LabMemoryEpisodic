import os
import json
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from config import settings
from embeddings import OllamaDenseEmbedder, FastSparseEmbedder
from storage import QdrantHybridStore
from pipeline import DomainIndexer
from agent import DomainRetrieverTool, DomainRAGAgent, OllamaLLMClient

app = FastAPI(
    title="LabMemoryEpisodic RAG API",
    description="API para RAG Híbrido Multidominio con Qdrant y Ollama",
    version="1.0.0",
)

# Inicialización de dependencias (Singleton / Service Layer)
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
retriever_tool = DomainRetrieverTool(
    store=store,
    dense_embedder=dense_embedder,
    sparse_embedder=sparse_embedder,
)


# Modelos de Petición
class CreateCollectionRequest(BaseModel):
    collection_name: str = Field(..., description="Nombre del nuevo dominio/colección")


class IndexTextRequest(BaseModel):
    collection_name: str
    content: str
    source_name: Optional[str] = "manual_input"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    collection_name: str
    query: str
    metadata_filter: Optional[Dict[str, Any]] = None
    limit: int = 4


class AgentQueryRequest(BaseModel):
    collection_name: str
    question: str
    llm_model: Optional[str] = None
    metadata_filter: Optional[Dict[str, Any]] = None
    limit: int = 4


# Rutas API
@app.get("/api/status")
def get_status():
    """Comprueba el estado de conexión con Qdrant y Ollama."""
    qdrant_ok = False
    collections = []
    try:
        collections = store.list_collections()
        qdrant_ok = True
    except Exception:
        pass

    ollama_models = []
    try:
        import requests
        r = requests.get(f"{settings.ollama_url}/api/tags", timeout=3)
        if r.status_code == 200:
            ollama_models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass

    return {
        "status": "online",
        "qdrant": {
            "connected": qdrant_ok,
            "host": f"{settings.qdrant_host}:{settings.qdrant_port}",
            "collections_count": len(collections),
        },
        "ollama": {
            "connected": len(ollama_models) > 0,
            "url": settings.ollama_url,
            "active_llm": settings.ollama_llm_model,
            "active_embed": settings.ollama_embed_model,
            "available_models": ollama_models,
        },
    }


@app.get("/api/collections")
def get_collections():
    """Devuelve la lista de colecciones / dominios en Qdrant con su conteo de chunks."""
    try:
        cols_stats = store.get_collections_with_stats()
        return {"collections": cols_stats}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/collections/{collection_name}/points")
def get_collection_points(collection_name: str, limit: int = 15):
    """Inspecciona los chunks reales guardados en una colección en Qdrant."""
    try:
        points = store.get_collection_points(collection_name=collection_name, limit=limit)
        return {
            "collection": collection_name,
            "count": len(points),
            "points": points,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/collections")
def create_collection(req: CreateCollectionRequest):
    """Crea una nueva colección de dominio con soporte para vectores duales."""
    name = req.collection_name.strip().replace(" ", "_")
    if not name:
        raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")
    try:
        store.ensure_collection(name)
        print(f"[COLLECTION CREATED] '{name}' inicializada en Qdrant")
        return {"message": f"Colección '{name}' inicializada con éxito", "collection_name": name}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/index/text")
def index_text_content(req: IndexTextRequest):
    """Indexa texto directamente con metadatos personalizados."""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="El contenido no puede estar vacío")
    try:
        print(f"[INDEX TEXT] Colección: '{req.collection_name}', Tamaño texto: {len(req.content)} chars")
        chunks_count = indexer.index_text(
            content=req.content,
            collection_name=req.collection_name,
            custom_metadata=req.metadata,
            source_id=req.source_name,
        )
        total_in_col = store.client.count(collection_name=req.collection_name, exact=True).count
        print(f"[INDEX TEXT SUCCESS] '{req.collection_name}' +{chunks_count} chunks (Total ahora: {total_in_col})")
        return {
            "status": "success",
            "chunks_indexed": chunks_count,
            "total_in_collection": total_in_col,
            "collection": req.collection_name,
            "metadata": req.metadata,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/index/file")
async def index_uploaded_file(
    collection_name: str = Form(...),
    metadata_json: str = Form("{}"),
    file: UploadFile = File(...),
):
    """Indexa un fichero subido directamente en memoria con su diccionario de metadatos."""
    try:
        custom_metadata = json.loads(metadata_json) if metadata_json else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Formato JSON de metadatos inválido")

    try:
        content_bytes = await file.read()
        if not content_bytes:
            raise HTTPException(status_code=400, detail="El archivo subido está vacío")

        # Decodificación resiliente
        content = None
        for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
            try:
                content = content_bytes.decode(enc)
                break
            except Exception:
                continue

        if content is None:
            content = content_bytes.decode("utf-8", errors="replace")

        if not content.strip():
            raise HTTPException(status_code=400, detail="El archivo no contiene texto legible")

        file_metadata = {
            "file_name": file.filename,
            "file_extension": os.path.splitext(file.filename)[1].lower() if file.filename else "",
            **custom_metadata,
        }

        print(f"[INDEX FILE] Colección: '{collection_name}', Archivo: '{file.filename}', Tamaño: {len(content)} caracteres")

        chunks_count = indexer.index_text(
            content=content,
            collection_name=collection_name,
            custom_metadata=file_metadata,
            source_id=file.filename,
        )

        total_in_col = store.client.count(collection_name=collection_name, exact=True).count
        print(f"[INDEX FILE SUCCESS] '{collection_name}' +{chunks_count} chunks (Total ahora en Qdrant: {total_in_col})")

        return {
            "status": "success",
            "file_name": file.filename,
            "chunks_indexed": chunks_count,
            "total_in_collection": total_in_col,
            "collection": collection_name,
            "metadata": custom_metadata,
        }
    except Exception as exc:
        print(f"[INDEX ERROR] {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/search")
def search_hybrid(req: SearchRequest):
    """Ejecuta búsqueda híbrida (Dense + BM25 con RRF) sobre la colección elegida."""
    try:
        results = retriever_tool.search(
            query=req.query,
            collection_name=req.collection_name,
            metadata_filter=req.metadata_filter,
            limit=req.limit,
        )
        return {
            "query": req.query,
            "collection": req.collection_name,
            "results_count": len(results),
            "results": [
                {
                    "chunk_id": r.chunk_id,
                    "score": round(r.score, 4),
                    "text": r.text,
                    "metadata": r.metadata,
                }
                for r in results
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/agent/ask")
def ask_agent(req: AgentQueryRequest):
    """Ejecuta consulta completa con el Agente RAG y síntesis en Ollama."""
    try:
        model = req.llm_model or settings.ollama_llm_model
        agent = DomainRAGAgent(
            retriever_tool=retriever_tool,
            llm_client=OllamaLLMClient(model_name=model),
        )
        response = agent.ask(
            question=req.question,
            domain_collection=req.collection_name,
            metadata_filter=req.metadata_filter,
            limit=req.limit,
        )
        return response
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# Servir interfaz web estática
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def serve_index():
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Interfaz frontend no encontrada en /static"}
