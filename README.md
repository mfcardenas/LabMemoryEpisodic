# EpiGraph — memoria episódica multimodal

EpiGraph se ejecuta actualmente como un monolito modular local para PC. El
producto activo vive en `episodic/`; el RAG documental de la raíz y el robot
de `sc-on-device-ai-memory/` se conservan como código legado y referencia,
pero no forman parte del arranque de EpiGraph.

## Punto de control activo — PC-EG1

El desarrollo de agentes y nuevas fases está detenido hasta validar un
recorrido vertical desde archivos reales. El frontend principal permite ahora
registrar un activo, conservarlo por hash, procesarlo, inspeccionar la
jerarquía `Observation -> MicroEpisode -> Episode`, comprobar sus evidencias,
visualizar intervalos por fuente y verificar su proyección en Qdrant.

```powershell
epigraph --data .runtime/pceg1 --fake-encoders serve
```

Abra `http://127.0.0.1:8100/` y comience por **Ingesta**. La especificación y
los criterios de aceptación están en `docs/pc-eg1-consolidacion-epigraph.md`.

## Arranque reproducible — Fase 1

Requisito: Python 3.13. Desde la raíz `LabMemoryEpisodic`:

```powershell
conda activate LabMemoryEpic01
python -m pip install -e ".[dev]"
```

Las versiones directas verificadas están fijadas en `pyproject.toml` y
`requirements.txt`; la gestión y activación del entorno Conda queda a cargo
del usuario.

El comando único del producto es `epigraph`. Para arrancar API y frontend con
el backend local existente:

```powershell
epigraph serve
```

Abra `http://127.0.0.1:8100/`. El mismo comando puede comprobarse sin cargar
modelos reales ni depender de servicios externos:

```powershell
epigraph --data .runtime/fase1 --fake-encoders serve
```

Pruebas y ejemplo mínimo:

```powershell
python -m pytest -q
python examples/minimal_boot.py
```

La configuración usa únicamente variables con prefijo `EPIGRAPH_`; consulte
`.env.example`. Las rutas relativas quedan ancladas a la raíz del proyecto.
Puede activar un fichero de log con, por ejemplo,
`EPIGRAPH_LOG_FILE=episodic-data/logs/epigraph.log`.

Los notebooks de `sc-on-device-ai-memory/` se mantienen como ejemplos. Tras
la instalación editable pueden importar directamente `episodic.config`,
`episodic.bootstrap` y los demás módulos de producción, sin copiar su lógica.

La evidencia y los límites de esta fase están documentados en
`docs/fase-1-base-ejecutable.md`.

## Modelo de dominio — Fase 2

SQLite se migra automáticamente a la versión 2 al abrir el repositorio. El
fixture reproducible de la pipeta rosada crea dos ambientes, tres fuentes,
tres activos originales y observaciones trazables:

```powershell
python scripts/load_pink_pipette_example.py --data .runtime/fase2-pink-pipette
epigraph --data .runtime/fase2-pink-pipette --fake-encoders serve
```

En `http://127.0.0.1:8100/` puede comprobarse el episodio compuesto y abrir su
observación enlazada. Las entidades internas, referencias, relaciones e
hipótesis se verifican mediante la suite de persistencia, sin añadir una UI
diagnóstica fuera del alcance de la fase.

El modelo, la migración, los comandos y las limitaciones están documentados en
`docs/fase-2-modelo-dominio.md`.

## Ingesta de fuentes y ventanas — Fase 3

Registre un archivo original sin extraer ni describir su contenido:

```powershell
epigraph ingest video.mp4 --environment laboratorio_01 --source camera_A01
epigraph ingest data_samples/phase3_structured.json --environment laboratorio_01 --source registro_manual_01
```

El comando devuelve el `Asset ID`, SHA-256, MIME, tamaño, duración cuando
puede obtenerse localmente y la ruta de la copia inmutable gestionada. Para audio o
vídeo, genere ventanas técnicas con:

```powershell
epigraph window <asset_id> --duration 60 --overlap 5
```

En contenedores cuya duración no sea legible sin herramientas externas puede
usarse `--total-duration <segundos>`. Esta fase no extrae texto, no ejecuta
modelos y no crea episodios. El detalle verificable está en
`docs/fase-3-ingestion-ventanas.md`.

## Observaciones normalizadas — Fase 4

Un Asset registrado puede transformarse en observaciones verificables sin
crear episodios:

```powershell
epigraph observe <asset_id>
```

La selección del procesador es automática por modalidad. Para comprobar el
contrato sin modelos puede usarse el procesador determinista:

```powershell
epigraph observe <asset_id> --processor mock-processor
```

La demostración conjunta espera un vídeo, un audio y un documento en
`data_samples/phase4_input/`:

```powershell
python scripts/run_phase4_demo.py --environment laboratorio_01
```

El comando imprime cada `Observation` junto con sus `EvidenceReference`. Para
PDF se usa `pypdf`; audio e imagen funcionan con capacidades locales y el
procesador de vídeo conserva intervalos sin afirmar detecciones inexistentes.
Consulte `docs/fase-4-observaciones-normalizadas.md`.

## Construcción determinista de episodios — Fase 5

Las observaciones persistidas pueden agruparse sin agentes ni LLM mediante
reglas configurables:

```powershell
epigraph compose `
  --data .runtime/fase4-demo `
  --environment laboratorio_01 `
  --rules data_samples/phase5_rules.json
```

La demostración autocontenida incluye una acción entre ventanas, dos acciones
en una ventana, cámaras sincronizadas, un intervalo sin evidencia y una
contradicción:

```powershell
python scripts/run_phase5_demo.py --data .runtime/fase5-demo
```

Cada `MicroEpisode` conserva sus observaciones, evidencias y las reglas que
produjeron la agrupación. La reconstrucción es idempotente y no depende de la
numeración de `ProcessingWindow`. Consulte
`docs/fase-5-construccion-determinista-episodios.md`.

## Entidades y grafo lógico — Fase 6

La resolución de entidades y las relaciones se almacenan en SQLite. Una
coincidencia posible permanece como `Hypothesis`; solo una decisión validada
crea `SAME_ENTITY_AS`. Las observaciones originales no se fusionan ni se
reescriben.

El escenario de herramientas entre dos talleres puede ejecutarse con:

```powershell
python scripts/run_phase6_demo.py --data .runtime/fase6-demo
```

La salida demuestra observaciones por identidad resuelta, relaciones de un
episodio, recorrido anterior/posterior, solapamientos, huecos de observación,
contradicciones, evidencias y última observación confirmada. Consulte
`docs/fase-6-entidades-grafo-logico.md`.

## Recuperación híbrida — Fase 7

La búsqueda combina filtros canónicos, embeddings existentes, tiempo y
expansión por relaciones sin generar respuestas con LLM. Cada resultado
devuelve episodio, observaciones y referencias de evidencia, y muestra por
separado sus puntuaciones estructural, semántica, temporal y relacional.

Ejecute el escenario medido de los dos talleres:

```powershell
python scripts/run_phase7_demo.py --data .runtime/fase7-demo --k 5
epigraph --data .runtime/fase7-demo --fake-encoders serve
```

En `http://127.0.0.1:8100/`, la vista **Búsqueda** expone los nueve filtros y
la expansión `BEFORE`, `AFTER` o `CONTRADICTS`. Consulte el contrato, métricas
y limitaciones en [docs/fase-7-recuperacion-hibrida.md](docs/fase-7-recuperacion-hibrida.md).

## API de herramientas — Fase 8

Las once herramientas deterministas consultan observaciones, episodios,
entidades, documentos, evidencias, contradicciones y topología sin utilizar
un LLM y sin modificar la memoria primaria:

```powershell
python scripts/run_phase8_demo.py --data .runtime/fase8-demo
epigraph --data .runtime/fase8-demo --fake-encoders serve
```

El catálogo y los formularios de ejecución están en
`http://127.0.0.1:8100/docs`; el frontend incluye un acceso directo. Los
contratos, ejemplos y limitaciones se documentan en
[docs/fase-8-api-herramientas.md](docs/fase-8-api-herramientas.md).

## RAG documental legado

La documentación que sigue corresponde a la aplicación RAG anterior. Su
entrada `python app.py` se conserva, pero no es el comando de EpiGraph.

Arquitectura de recuperación aumentada por generación (RAG) híbrida con soporte de múltiples dominios mediante colecciones dinámicas en Qdrant, enriquecimiento con diccionarios de metadatos personalizados y modelos locales en Ollama (`gpt-oss`, `qwen`).

---

## 🏗️ Arquitectura y Patrones de Diseño

```
                                      +--------------------+
                                      |   Ficheros / Docs  |
                                      +---------+----------+
                                                |
                                                v
                                      +--------------------+
                                      |    TextChunker     |  (Chunking semántico)
                                      +---------+----------+
                                                |
                        +-----------------------+-----------------------+
                        |                                               |
                        v                                               v
              +-------------------+                           +-------------------+
              | OllamaDenseEmbed  |                           | FastSparseEmbed   |
              | (nomic / qwen)    |                           | (Qdrant / BM25)   |
              +---------+---------+                           +---------+---------+
                        |                                               |
                        +-----------------------+-----------------------+
                                                |
                                                v
                                      +--------------------+
                                      |   DomainIndexer    |  (+ Metadatos Custom)
                                      +---------+----------+
                                                |
                                                v
                               +----------------------------------+
                               |     QdrantHybridStore            |
                               | (Colección: Dominio A / B / C)   |
                               |  - Named Vector: 'dense'         |
                               |  - Named Vector: 'sparse'        |
                               |  - Payload: Custom Metadata      |
                               +-----------------+----------------+
                                                 |
                                     (Prefetch + RRF Fusion)
                                                 |
                                                 v
                               +----------------------------------+
                               |     DomainRetrieverTool          |
                               +-----------------+----------------+
                                                 |
                                                 v
                               +----------------------------------+
                               |       DomainRAGAgent             |
                               |   (Ollama: gpt-oss / qwen)       |
                               +----------------------------------+
```

### Patrones Aplicados:
1. **Strategy Pattern** (`embeddings/base.py`):
   - `DenseEmbedder`: Abstracción para modelos densos (Ollama, HuggingFace, OpenAI).
   - `SparseEmbedder`: Abstracción para modelos dispersos (FastEmbed BM25/Splade).
2. **Repository Pattern** (`storage/qdrant_store.py`):
   - Encapsula la gestión de colecciones y el ciclo de vida de Qdrant.
   - Búsqueda híbrida con `prefetch` y fusión por **RRF (Reciprocal Rank Fusion)** nativa de Qdrant.
   - Filtrado dinámico sobre cualquier clave del diccionario de metadatos.
3. **Pipeline Pattern** (`pipeline/`):
   - `TextChunker`: Segmentación contextual con solapamiento.
   - `DomainIndexer`: Ingesta de texto/ficheros inyectando metadatos personalizados en el payload de Qdrant.
4. **Adapter / Tool Pattern** (`agent/retrieval_tool.py`):
   - Expone métodos de consulta y un schema estándar `get_tool_schema()` listo para function calling de agentes (LangChain, CrewAI, AutoGen).

---

## 📁 Estructura del Proyecto

```text
LabMemoryEpisodic/
├── config.py                 # Configuración tipada (Pydantic BaseSettings)
├── domain/
│   ├── __init__.py
│   └── models.py             # Modelos de dominio: Document, Chunk, SearchResult
├── embeddings/
│   ├── __init__.py
│   ├── base.py               # Protocolos abstractos DenseEmbedder y SparseEmbedder
│   ├── ollama_dense.py       # Implementación para Ollama (/api/embed)
│   └── fast_sparse.py        # Implementación BM25 ultrarrápida con FastEmbed
├── storage/
│   ├── __init__.py
│   └── qdrant_store.py       # Repositorio Qdrant Híbrido multidominio
├── pipeline/
│   ├── __init__.py
│   ├── chunker.py            # Chunker con solapamiento configurable
│   └── indexer.py            # Indexador de ficheros con metadatos custom
├── agent/
│   ├── __init__.py
│   ├── ollama_client.py      # Cliente HTTP para LLM en Ollama (gpt-oss/qwen)
│   ├── retrieval_tool.py     # Tool estándar de recuperación para agentes
│   └── rag_agent.py          # Agente que orquesta búsqueda y síntesis
├── data_samples/             # Archivos de prueba para diferentes dominios
├── example.py                # Demostración ejecutable de extremo a extremo
└── requirements.txt          # Dependencias mínimas
```

---

## 🚀 Uso Rápido

### 1. Iniciar Servicios
- **Qdrant**: `docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant`
- **Ollama**: En ejecución en `http://localhost:11434` con los modelos deseados (ej. `gpt-oss`, `nomic-embed-text` o `qwen`).

### 2. Indexar Ficheros con Metadatos Personalizados

```python
from embeddings import OllamaDenseEmbedder, FastSparseEmbedder
from storage import QdrantHybridStore
from pipeline import DomainIndexer

# Inicializar
store = QdrantHybridStore()
indexer = DomainIndexer(
    store=store,
    dense_embedder=OllamaDenseEmbedder(),
    sparse_embedder=FastSparseEmbedder(),
)

# Indexar fichero en un dominio específico con metadatos custom
custom_metadata = {
    "author": "Agente-Alpha",
    "experiment_id": "EXP-101",
    "sensitivity": "confidencial",
    "version": 1.2
}

indexer.index_file(
    file_path="mi_archivo.txt",
    collection_name="dominio_memoria_episodica",
    custom_metadata=custom_metadata,
)
```

### 3. Recuperar y Consultar con el Agente

```python
from agent import DomainRetrieverTool, DomainRAGAgent, OllamaLLMClient

retriever = DomainRetrieverTool(store, dense_embedder, sparse_embedder)
agent = DomainRAGAgent(
    retriever_tool=retriever,
    llm_client=OllamaLLMClient(model_name="gpt-oss:latest"),
)

# Preguntar a un dominio específico con filtrado de metadatos opcional
respuesta = agent.ask(
    question="¿Cuál fue el resultado del experimento?",
    domain_collection="dominio_memoria_episodica",
    metadata_filter={"experiment_id": "EXP-101"},
)

print(respuesta["answer"])
print("Fuentes utilizadas:", respuesta["sources"])
```

---

## 🌐 Despliegue de la Aplicación Web (Frontend SPA + API REST)

Para levantar el servidor web y acceder a la interfaz gráfica interactiva:

```bash
python app.py
```

- **Dashboard Web Interactivo**: [http://localhost:8000](http://localhost:8000)
- **Documentación Swagger / OpenAPI**: [http://localhost:8000/docs](http://localhost:8000/docs)

### Funcionalidades del Dashboard:
1. **Agente & Consulta**: Alterna entre *Modo Agente* (síntesis con `gpt-oss`/Qwen) y *Solo Búsqueda Híbrida RRF*, con filtrado opcional por metadatos.
2. **Indexador & Metadatos**: Permite subir ficheros (`.txt`, `.md`, `.json`, etc.) o pegar texto directo, agregando dinámicamente cualquier clave-valor de metadatos al payload de Qdrant.
3. **Dominios (Colecciones)**: Crea y administra colecciones independientes en Qdrant para aislar memorias o bases de conocimiento.
