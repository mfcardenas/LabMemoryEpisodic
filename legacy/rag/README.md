# RAG documental legado

Aplicación anterior a EpiGraph (Qdrant HTTP + Ollama + `DomainRAGAgent`).
No forma parte del producto ni de `pip install -e .`; se conserva como
referencia. EpiGraph (`episodic/`) no importa nada de esta carpeta.

Ejecución, desde esta carpeta para que resuelvan los imports de primer nivel:

```powershell
cd legacy/rag
python app.py   # http://localhost:8000
```

Requiere Qdrant HTTP en `localhost:6333` y Ollama en `localhost:11434`.
