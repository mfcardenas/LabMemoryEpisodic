# EpiGraph — Fase 0: auditoría técnica del repositorio

Fecha de auditoría: 2026-10-04  
Directorio auditado: `C:\Users\sqs.erasmus\Documents\GitHub\LabMemoryEpisodic`  
Entorno utilizado: `C:\Users\sqs.erasmus\.conda\envs\LabMemoryEpic01\python.exe`  
Alcance: inspección, pruebas y ejecución controlada. No se ha modificado código, configuración ni dependencias.

Estado: **CERRADA Y ACEPTADA** el 2026-10-04.

## 1. Resumen ejecutivo

El directorio contiene **tres aplicaciones independientes** y material docente de referencia:

1. Un RAG documental legado en la raíz (`app.py`, `api/`, `agent/`, `pipeline/`, `embeddings/`, `storage/`, `static/`). Usa Qdrant HTTP y Ollama.
2. Una aplicación de memoria episódica en `episodic/`. Usa SQLite como fuente canónica, un almacén local de medios y Qdrant Edge o Qdrant HTTP como proyección vectorial.
3. El robot original dentro de `sc-on-device-ai-memory/sc-on-device-ai-memory/robot/`. Conserva cámara, detector, tracker, micrófono, ASR, memoria Edge y su interfaz propia, pero **no está integrado en la raíz del producto**.
4. Los notebooks `Lesson3`, `Lesson4`, `Lesson5`, `Appendix` y `helper.py`, que contienen patrones reutilizables de Qdrant Edge, Nomic, CLIP, Whisper y presentación docente.

No hay imports entre `episodic/` y el RAG o el robot. La aplicación episódica actual es funcional como archivo de episodios ya preparados, pero todavía **no implementa el modelo EpiGraph completo**. Faltan `Environment`, `Asset`, `ProcessingWindow`, `Event`, `MicroEpisode`, `Relation`, `Hypothesis` y `EvidenceReference` como conceptos persistentes.

La incompatibilidad principal es conceptual: el flujo actual importa directamente un `Episode` y crea dentro de él sus evidencias y observaciones. EpiGraph debe conservar primero el activo original, procesarlo mediante ventanas, producir observaciones y eventos y solo después componer microepisodios y episodios. En el modelo actual, `Observation.episode_id` hace que una observación dependa de un episodio previamente creado.

Conclusión de Fase 0:

- Existe una base técnica reutilizable sólida: tiempos, SQLite, almacén de medios, outbox, Qdrant Edge, encoders, filtros temporales, CLI y API.
- El robot aporta productores futuros valiosos, pero sus clases principales están acopladas a su taxonomía `taught/seen/ignored`, a hardware y a una interacción concreta.
- El RAG aporta un `RetrievalTool`, RRF denso/disperso y un agente consumidor, pero no debe convertirse en memoria primaria.
- No procede ampliar todavía la UI ni añadir agentes. El siguiente cambio debe ser una migración aditiva del modelo canónico, comenzando por procedencia primaria.

## 2. Inventario del repositorio

### 2.1 Directorios y archivos principales

| Ruta | Contenido | Situación |
|---|---|---|
| `app.py`, `config.py` | Entrada y configuración del RAG legado | Ejecutable independiente, puerto 8000 |
| `agent/` | Cliente Ollama, herramienta de recuperación y agente RAG | Solo RAG legado |
| `api/` | API FastAPI del RAG | Inicializa embeddings, almacenamiento y agente globales |
| `domain/` | Modelos `Document`, `Chunk`, `SearchResult`, `RetrievalQuery` | Dominio documental, no episódico |
| `embeddings/` | Nomic mediante Ollama y BM25 mediante FastEmbed | Reutilización posible por contratos, no directamente como dominio |
| `pipeline/` | Chunking e indexación de texto | Solo texto plano |
| `storage/` | Qdrant HTTP con vectores dense/sparse y RRF | RAG documental |
| `static/` | SPA del RAG | Interfaz separada |
| `episodic/domain/` | Tiempos, tipos y entidades episódicas actuales | Núcleo desacoplado de infraestructura |
| `episodic/application/` | Ingesta, indexación, recuperación y ciclo de vida | Aplicación episódica activa |
| `episodic/ports/` | Repositorio, almacén, encoders, reloj e índice | Buena base para evolución modular |
| `episodic/adapters/sqlite/` | Migraciones y repositorio canónico | Fuente de verdad actual |
| `episodic/adapters/media/` | Almacén de blobs con hash | Reutilizable para `Asset` |
| `episodic/adapters/qdrant_edge/` | Qdrant Edge, generaciones y alias | Proyección local activa |
| `episodic/adapters/qdrant/` | Backend Qdrant HTTP alternativo | Perfil opcional |
| `episodic/adapters/models/` | Encoders FastEmbed y dobles deterministas | Nomic y pareja CLIP |
| `episodic/interfaces/` | CLI, FastAPI y SPA episódica | Ejecutable independiente, puerto 8100 |
| `data/atlas/` | Manifiesto, consultas y diez imágenes sintéticas | Fixture de desarrollo, no corpus visual real |
| `episodic-data/` | SQLite, medios e índice Edge actuales | 10 episodios y 20 representaciones |
| `scripts/` | Generación Atlas, evaluación textual y smoke de modelos | Herramientas de desarrollo |
| `tests/` | 42 pruebas episódicas | Suite raíz configurada en `pytest.ini` |
| `docs/episodic/` | Arquitectura, baseline, evaluaciones e hitos anteriores | Describe la implementación previa a EpiGraph |
| `sc-on-device-ai-memory/helper.py` | Utilidades docentes de modelos, Edge, cloud y visualización | No importable en el entorno por falta de Matplotlib |
| `sc-on-device-ai-memory/*.ipynb` | Lessons 3–5 y Appendix | Material docente parcialmente autocontenido |
| `sc-on-device-ai-memory/sc-on-device-ai-memory/robot/` | Aplicación robot completa | Repositorio anidado de referencia, aún no integrado |
| `sc-on-device-ai-memory/sc-on-device-ai-memory/testdata/` | Imágenes y calibración del robot | Datos con créditos registrados |
| `sc-on-device-ai-memory/sc-on-device-ai-memory/deploy/` | Servicio y preparación headless | Específico de appliance/robot |
| `sc-on-device-ai-memory/sc-on-device-ai-memory/hardware/` | Modelos 3D y documentación física | Fuera del runtime EpiGraph PC |

Elementos citados en documentación previa pero ausentes del material recibido:

- `Leeson All.txt`.
- `Plan_Integracion_Memory_Episodic_Project.md`.
- `Appendix (1).ipynb`; el archivo disponible se llama `Appendix.ipynb`.
- `.env.example` del robot, aunque `docs/getting-started.md` indica copiarlo.
- Pesos `yoloe-11l-seg-pf.pt`; `ultralytics` intentaría resolverlos cuando corresponda, pero el código desactiva `YOLO_AUTOINSTALL`.
- `ro_shared_data/` y `my_photos/` requeridos por los notebooks.

### 2.2 Lenguajes y formatos

| Tecnología | Uso |
|---|---|
| Python | Dominio, servicios, adaptadores, robot, API, CLI, scripts y tests |
| JavaScript | SPA del RAG y SPA episódica |
| HTML/CSS | Interfaces RAG, episódica y robot |
| Jupyter Notebook | Curso y demostraciones de memoria local/cloud |
| JSON | Manifiesto Atlas, consultas y créditos |
| Markdown | Documentación y datos documentales de ejemplo |
| Shell/systemd | Despliegue headless del robot |
| STL/3MF | Hardware físico del robot; fuera del software EpiGraph |

### 2.3 Dependencias declaradas y entorno observado

La raíz solo tiene `requirements.txt`; no tiene `pyproject.toml` ni `uv.lock`. El repositorio anidado del robot sí contiene `pyproject.toml` y `uv.lock` y declara Python `>=3.12`.

Entorno observado:

- Python `3.13.16` mediante el intérprete de `LabMemoryEpic01`.
- `uv 0.8.6` disponible.
- El comando `conda` no está en el `PATH` de esta sesión, aunque el intérprete del entorno funciona por ruta absoluta.
- `pip check`: `No broken requirements found`.
- La raíz no es un repositorio Git. El robot anidado contiene `.git`, pero Git rechaza su lectura por `dubious ownership` bajo el usuario sandbox.

Dependencias principales instaladas:

| Paquete | Versión observada |
|---|---:|
| FastAPI | 0.142.2 |
| Uvicorn | 0.54.0 |
| Pydantic | 2.13.5 |
| pydantic-settings | 2.15.0 |
| qdrant-edge-py | 0.7.2 |
| qdrant-client | 1.19.1 |
| fastembed | 0.8.0 |
| onnxruntime | 1.27.0 |
| numpy | 2.5.1 |
| Pillow | 12.3.0 |
| pytest | 9.1.1 |
| python-multipart | 0.0.32 |
| requests | 2.34.2 |
| python-dotenv | 1.2.4 |

Dependencias del robot o notebooks no instaladas en `LabMemoryEpic01`:

- `cryptography`
- `opencv-python` / módulo `cv2`
- `onnx-asr`
- `sounddevice`
- `ultralytics`
- `torch`
- `torchvision`
- `matplotlib`
- Jupyter, JupyterLab, Notebook e IPython kernel

No se instalaron ni cambiaron dependencias durante esta fase.

## 3. Puntos de entrada y comandos actuales

### 3.1 RAG documental legado

```powershell
& 'C:\Users\sqs.erasmus\.conda\envs\LabMemoryEpic01\python.exe' app.py
```

- Sirve FastAPI en `0.0.0.0:8000`.
- Requiere Qdrant HTTP en `localhost:6333` y Ollama en `localhost:11434`.
- Endpoints principales: colecciones, indexación de texto/archivo, búsqueda híbrida y `/api/agent/ask`.
- La subida de archivos solo decodifica bytes como texto; no extrae PDF, Office, OCR ni estructura documental.

### 3.2 Aplicación episódica

```powershell
& 'C:\Users\sqs.erasmus\.conda\envs\LabMemoryEpic01\python.exe' -m episodic.app --help
& 'C:\Users\sqs.erasmus\.conda\envs\LabMemoryEpic01\python.exe' -m episodic.app serve --host 127.0.0.1 --port 8100
```

Subcomandos presentes:

- `init`
- `import`
- `index`
- `search`
- `timeline`
- `last-observation`
- `rebuild-index`
- `serve`

El backend predeterminado es Qdrant Edge. Se puede seleccionar Qdrant HTTP.

### 3.3 Robot de referencia

Desde `sc-on-device-ai-memory/sc-on-device-ai-memory/`:

```powershell
uv run python -m robot.app
uv run python -m robot.app --source testdata/
```

- Modo live: cámara, micrófono, navegador local y Qdrant Edge, puerto 8765.
- Modo replay: directorio de imágenes o archivo de vídeo.
- El intérprete `LabMemoryEpic01` no puede ejecutar ni `--help`, porque `robot/app.py` importa `cv2` al cargar el módulo.
- No se ejecutó `uv sync` porque la fase prohíbe cambiar dependencias y habría creado un entorno nuevo.

### 3.4 Notebooks

No son ejecutables en el entorno actual:

- No hay Jupyter/IPykernel.
- `helper.py` falla al importar por ausencia de `matplotlib`.
- Faltan `ro_shared_data/` y `my_photos/`.
- `Lesson5.ipynb` llama funciones que no existen en el `helper.py` entregado.

## 4. Auditoría de notebooks y `helper.py`

### 4.1 `Lesson3.ipynb` — Building the Memory

Contenido relevante:

- Crea un shard Qdrant Edge con vectores nombrados.
- Usa Nomic para texto y CLIP para imagen.
- Inserta notas como puntos y consulta memoria vacía/no vacía.
- Añade filtros por payload.
- Implementa texto→imagen mediante la torre textual CLIP.
- Enseña eliminación de puntos y latencia de búsqueda.

Decisión:

- Reutilizar los patrones de vectores nombrados, filtros y consulta multimodal.
- Aislar `fresh_start`, porque elimina recursivamente el shard.
- No reutilizar el esquema de payload como dominio EpiGraph.

### 4.2 `Lesson4.ipynb` — Building the Voice Assistant

Contenido relevante:

- Carga memorias de un día desde JSON.
- Transcribe notas de voz con Whisper local.
- Persiste texto, voz e imágenes en carriles distintos.
- Recupera texto con Nomic e imágenes con CLIP.
- Presenta resultados por modalidad sin mezclar scores.
- Añade manualmente una memoria y vuelve a consultarla.

Decisión:

- Reutilizar separación por modalidades, ASR por lotes y liberación del modelo tras transcribir.
- Adaptar los registros de voz a `Asset` + `EvidenceReference` + `Observation`.
- No usar el JSON diario como fuente canónica.

### 4.3 `Lesson5.ipynb` — Learning Through Memory

Contenido relevante:

- Enseña un objeto mediante varias imágenes.
- Consulta el vecino visual más próximo.
- Calibra un umbral con ejemplos positivos retenidos y negativos.
- Introduce una memoria de asistente y un ranking de frescura.
- Advierte que no debe recrearse el shard para reparar una ejecución.

Problemas comprobados:

El notebook invoca seis funciones ausentes en `helper.py`:

- `show_example_object`
- `store_day`
- `memory_receipt`
- `save_shard`
- `freshness_ranking`
- `recent_text_search`

Los outputs guardados no contienen errores, lo que indica que fueron generados con otra versión del helper o en otro entorno. No deben considerarse evidencia de ejecución reproducible.

Decisión:

- Reutilizar el patrón de calibración y separación entre vecino, score y decisión.
- No reconstruir ni atribuir las funciones ausentes al material recibido.
- No adoptar el ranking de frescura hasta definir intención, fórmula, reloj y evaluación.

### 4.4 `Appendix.ipynb` — Hybrid On-Device and Cloud Memory

Contenido relevante:

- Construye memoria local.
- Copia puntos a Qdrant Cloud.
- Descarga snapshots completos o parciales.
- Consulta memoria local y cloud.

Decisión:

- Aislar para fases futuras de sincronización.
- No introducir Qdrant Cloud ni sincronización distribuida en las primeras fases de EpiGraph.
- Conservar como referencia del formato de puntos y snapshots.

### 4.5 `helper.py`

Capacidades encontradas:

- Nomic document/query embeddings, 768 dimensiones.
- CLIP vision y CLIP text alineados, 512 dimensiones.
- Carga diferida mediante `lru_cache`.
- Carga de imágenes locales o HTTP.
- Creación y consulta de Qdrant Edge.
- Almacenamiento de notas, voz transcrita y fotografías.
- Recuperación en carriles separados.
- Calibración visual con positivos y negativos.
- Whisper local y liberación posterior del modelo.
- Utilidades cloud y snapshots.
- HTML embebido para notebooks.

Riesgos y acoplamientos:

- Importa Matplotlib en el nivel superior; esto impide usar incluso funciones no gráficas si Matplotlib falta.
- `fresh_start` cierra shards descubiertos en `__main__` y elimina recursivamente el directorio indicado.
- `new_shard` siempre llama `fresh_start`.
- Usa rutas docentes fijas (`ro_shared_data`, `my_photos`).
- Mezcla encoders, almacenamiento, red, subida de archivos, render HTML y utilidades destructivas en un único módulo.
- `cloud_points(limit=1000)` no pagina.
- La tabla de latencias está precalculada en Apple M5 Pro y no representa este PC.

Decisión: no importar `helper.py` como dependencia productiva. Extraer únicamente comportamientos concretos detrás de puertos existentes, con pruebas y atribución.

## 5. Arquitectura original del robot

Regla de separación original:

```text
robot/device  →  robot/brain
robot/brain   ↛  robot/device
```

Esta separación es valiosa y debe conservarse. Sin embargo, `robot/brain` sigue representando un dominio de reconocimiento de objetos del robot, no un dominio EpiGraph genérico.

### 5.1 Componentes relativamente genéricos

| Componente | Parte reutilizable | Adaptación necesaria |
|---|---|---|
| `brain/models.py` | Carga diferida, caché durable, threads, Nomic, CLIP vision y Whisper | Añadir CLIP text; separar ASR/embeddings; idioma configurable; registrar versión |
| `brain/detect.py` | YOLOE, BoT-SORT, estabilidad, `padded_crop`, `crop_quality` | Convertir a productor de observaciones; conservar frame/tiempo/región; parametrizar tamaño |
| `brain/memory.py` | Operación Edge, vectores nombrados, locks, flush, filtros | No reutilizar taxonomía `taught/seen/ignored`; paginar; SQLite debe ser canónico |
| `device/mic.py` | Captura WAV, silencio, RMS y recorte | Desacoplar `MIC_DEVICE`; producir `Asset` de audio |
| `device/runtime.py` | Rutas temporales y presentación portátil de fechas | No usar sus strings como tiempo canónico |
| `device/server.py` | Servidor local, JSON, bytes y certificado | Separar rutas de `LiveApp`; usar solo si existe un consumidor real |
| `robot/config.py` | Validación simple y precedencia `.env` | Dividir configuración de productor y configuración central |

### 5.2 Componentes acoplados al robot

| Componente | Acoplamiento |
|---|---|
| `brain/core.py::Robot` | Coordina detector, atención visual, enseñanza por voz, etiquetas, thumbs y memoria `taught/seen/ignored` |
| `brain/memory.py::Memory` | Usa label como identidad, ubicación global del robot y payload específico del demo |
| `device/live.py::LiveApp` | Cámara única, teclado/touch, hilos, stream JPEG, voz y acciones del robot en una clase |
| `device/page.html` | Interacción TEACH/ASK/MEMORY/IGNORE y endpoints concretos de `LiveApp` |
| `device/draw.py` | Render de boxes y estados visuales del robot |
| `brain/labels.py` | Extrae nombres desde frases de enseñanza del demo |
| `robot/app.py` | Arranque de hardware, reset destructivo, cámara y replay con tiempos artificiales |

Problemas que afectan a una futura adaptación:

- `Track.sighted` genera una única escritura por track; una presencia continua no actualiza `last_seen` canónico.
- El track local y el label se usan como aproximaciones de identidad.
- El replay de vídeo acumula todos los frames en RAM antes de procesarlos.
- El replay incrementa `now` artificialmente en tres segundos por frame.
- La ubicación configurada es una declaración del dispositivo, no prueba de posición de una entidad.
- Varias lecturas usan `scroll(limit=10000)` sin paginación.
- Borrar puntos no gestiona conjuntamente los archivos `thumb` y `scene`.
- ASR fija `LANGUAGE = "en"`.
- El filtro de área máximo está calibrado para objetos de mano y puede descartar personas u objetos grandes.
- `--reset` elimina recursivamente el shard seleccionado.

## 6. Localización de capacidades solicitadas

| Capacidad | Código localizado | Evaluación |
|---|---|---|
| Captura/carga de vídeo | `robot/device/live.py`, `robot/app.py::replay` | Live lee incrementalmente; replay carga vídeo completo en RAM y fabrica tiempos |
| Captura/carga de audio | `robot/device/mic.py`, `robot/device/live.py`, `robot/brain/models.py`, `helper.py` | Captura y ASR existen; acoplados a interacción robot o notebook |
| Procesamiento de imágenes | `robot/brain/detect.py`, `robot/brain/core.py`, `helper.py`, `episodic/adapters/models/fastembed.py` | Detector/tracker solo robot; embeddings de imagen ya adaptados en episodic |
| Extracción de texto | `api/server.py`, `pipeline/indexer.py`, Whisper en robot/helper | Solo decodificación de texto plano y ASR; no hay PDF, Office, OCR ni extracción estructurada |
| Embeddings | `embeddings/`, `robot/brain/models.py`, `helper.py`, `episodic/adapters/models/` | Tres implementaciones; la episódica es la mejor base por puertos y aislamiento |
| Almacenamiento | `storage/qdrant_store.py`, `robot/brain/memory.py`, `episodic/adapters/sqlite`, `media`, `qdrant*` | Episodic es la única con fuente canónica separada del índice |
| Recuperación | `agent/retrieval_tool.py`, `robot/brain/core.py::ask`, `episodic/application/retrieval.py` | Retrieval episódico tiene intenciones y revalidación; RAG aporta contrato de tool; robot es específico |
| Agentes | `agent/rag_agent.py`, `agent/ollama_client.py` | Solo RAG legado; no participa en memoria episódica y debe permanecer aislado |
| Interfaz | `static/`, `episodic/interfaces/web/`, `robot/device/page.html` | Tres interfaces independientes; ninguna representa aún el grafo conceptual EpiGraph |
| Configuración | `config.py`, `episodic/config.py`, `robot/config.py`, CLI | Tres configuraciones separadas; la episódica no usa todavía todos sus campos |

## 7. Matriz de componentes y decisión

| Componente actual | Archivo o módulo | Responsabilidad | Dependencias | Acoplamiento al robot | Estado funcional | Decisión: reutilizar, adaptar, aislar o descartar | Justificación |
|---|---|---|---|---|---|---|---|
| Entrada RAG | `app.py` | Arrancar FastAPI RAG | Uvicorn, configuración raíz | Ninguno | Funciona; servicios externos disponibles | Aislar | Es una aplicación distinta y escucha en `0.0.0.0` |
| Configuración RAG | `config.py` | Qdrant HTTP, Ollama, chunking | Pydantic Settings | Ninguno | Funciona | Adaptar | Puede aportar precedencia de configuración, no el modelo completo |
| Modelos documentales | `domain/models.py` | Documento, chunk y resultado | Pydantic | Ninguno | Funciona | Aislar | No representa procedencia episódica ni activos |
| Chunker textual | `pipeline/chunker.py` | Dividir texto con solapamiento | Dominio/config RAG | Ninguno | Funciona | Adaptar | Útil para fragmentos documentales; debe producir `EvidenceReference` exacta |
| Indexador documental | `pipeline/indexer.py` | Chunk, embeddings y upsert | Ollama/FastEmbed/Qdrant HTTP | Ninguno | Funciona | Adaptar | Patrón válido, pero escribe directo al índice sin memoria canónica |
| Embedding denso RAG | `embeddings/ollama_dense.py` | Nomic mediante Ollama | Requests, Ollama | Ninguno | Servicio disponible | Aislar | EpiGraph ya tiene Nomic local; conservar como perfil opcional si se justifica |
| Embedding sparse RAG | `embeddings/fast_sparse.py` | BM25 FastEmbed | FastEmbed | Ninguno | Funciona | Adaptar | Candidato a recuperación documental, no a memoria primaria |
| Store híbrido RAG | `storage/qdrant_store.py` | Dense+sparse y RRF | qdrant-client, Qdrant HTTP | Ninguno | Funciona | Adaptar | Reutilizar patrón RRF; no usar Qdrant como fuente canónica |
| RetrievalTool legado | `agent/retrieval_tool.py` | Buscar y formatear contexto | Store/embeddings RAG | Ninguno | Funciona | Adaptar | Buen contrato futuro; debe consumir consultas EpiGraph verificables |
| Agente RAG | `agent/rag_agent.py` | Recuperar y sintetizar con LLM | RetrievalTool, Ollama | Ninguno | Endpoint respondió 200 | Aislar | Los agentes solo deben operar después de construir la memoria |
| API/UI RAG | `api/server.py`, `static/` | Ingesta textual, búsqueda y agente | FastAPI, Qdrant, Ollama | Ninguno | Funciona; sin tests propios | Aislar | Mantener compatibilidad; no mezclar con el dominio canónico |
| Tiempos episódicos | `episodic/domain/times.py` | UTC, tiempos relativos, incertidumbre e intervalos | Biblioteca estándar | Ninguno | Probado | Reutilizar | Base adecuada para activos, ventanas y referencias |
| Tipos/entidades episódicas | `episodic/domain/types.py`, `entities.py` | Modelo canónico actual | Dataclasses | Ninguno | Probado | Adaptar | Source/Observation/Entity son base; faltan capas EpiGraph |
| Esquema de manifiesto | `episodic/manifest_schema.py` | Validación Pydantic | Pydantic | Ninguno | Declarado pero no usado | Adaptar | Debe conectarse y evolucionar al nuevo modelo |
| Ingesta episódica | `episodic/application/ingest.py` | Importar episodios y encolar embeddings | Repository, media store | Ninguno | Funciona e idempotente en Atlas | Adaptar | Actualmente importa Episode antes de construir memoria primaria |
| Repositorio SQLite | `episodic/adapters/sqlite/` | Fuente canónica y migraciones | sqlite3 | Ninguno | Funciona, WAL/FK probados | Reutilizar | Es la base natural del grafo canónico mediante migraciones aditivas |
| Almacén de medios | `episodic/adapters/media/store.py` | Blob con hash y escritura atómica | Filesystem | Ninguno | Funciona | Reutilizar | Debe convertirse en persistencia de `Asset`, sin borrar originales externos |
| Outbox/worker | `episodic/application/indexing.py` | Proyectar representaciones | Encoders, repository, index | Ninguno | Funciona en camino feliz | Adaptar | Faltan lease, intentos, error persistido y recuperación explícita |
| Qdrant Edge episódico | `episodic/adapters/qdrant_edge/` | Índice local, generaciones y rebuild | qdrant-edge | Ninguno | Funciona y persiste | Reutilizar | Proyección reconstruible correcta; ampliar targets/payloads |
| Qdrant HTTP episódico | `episodic/adapters/qdrant/` | Backend alternativo | qdrant-client/server | Ninguno | Tests locales; servidor disponible | Aislar | Mantener opcional, no hacerlo requisito de PC |
| Encoders episódicos | `episodic/adapters/models/fastembed.py` | Nomic, CLIP text y vision | FastEmbed/ONNX | Ninguno | Smoke real correcto | Reutilizar | Ya implementan puertos y espacios separados |
| Recuperación episódica | `episodic/application/retrieval.py` | Intenciones, RRF, revalidación y veredictos | Repository/index/encoders | Ninguno | Funciona parcialmente | Adaptar | Debe recuperar niveles EpiGraph y referencias exactas, no solo Episode |
| Índice temporal | `episodic/application/temporal_index.py` | Payload y filtros temporales | Dominio temporal | Ninguno | Probado | Reutilizar | Adecuado para Asset/Window/Event/MicroEpisode/Episode |
| Ciclo de vida | `episodic/application/lifecycle.py` | Anotar, ocultar y rebuild | Repository/index/media | Ninguno | Parcial | Adaptar | Debe gestionar relaciones, referencias y activos compartidos |
| CLI episódica | `episodic/interfaces/cli/` | Operación local | Bootstrap | Ninguno | Funciona | Reutilizar | Mantener equivalencia con HTTP en cada fase |
| API/UI episódica | `episodic/interfaces/http`, `web` | Consulta, detalle, gestión y diagnóstico | FastAPI/JS | Ninguno | Funciona como prototipo | Adaptar | Debe representar procedencia y composición EpiGraph |
| Corpus Atlas | `data/atlas/`, `scripts/build_atlas_dataset.py` | Fixture de diez episodios | Pillow/JSON | Ninguno | Flujo funcional | Adaptar | Convertir a manifiesto por capas; imágenes son tarjetas sintéticas |
| Tests episódicos | `tests/` | Dominio, SQLite, Edge, API y comportamiento | pytest | Ninguno | 42 aprobados | Reutilizar | Baseline de regresión antes de migrar |
| Modelos del robot | `robot/brain/models.py` | Nomic, CLIP vision y Whisper | FastEmbed, ONNX ASR | Medio | Bloqueado por dependencias | Adaptar | Reusar carga/caché/threads; la aplicación episódica ya absorbió parte |
| Memoria del robot | `robot/brain/memory.py` | Taught/seen/ignored en Edge | qdrant-edge | Alto | No ejecutada en este entorno | Adaptar | Extraer operación, no taxonomía, identidad por label ni límite 10000 |
| Detector/tracker | `robot/brain/detect.py` | YOLOE, BoT-SORT, crops y tracks | OpenCV, Torch, Ultralytics | Medio | Bloqueado por dependencias/pesos | Adaptar | Futuro productor de observaciones, no memoria canónica |
| Coordinador Robot | `robot/brain/core.py` | Detectar, reconocer, enseñar y recordar | Detector, modelos, Memory | Alto | Bloqueado | Aislar | Referencia de secuencia; no es el núcleo EpiGraph |
| Captura live | `robot/device/live.py` | Cámara, hilos, voz, render y servidor | OpenCV, mic, brain | Alto | Bloqueado por OpenCV | Adaptar | Extraer productor de cámara/workers en una fase posterior |
| Audio robot | `robot/device/mic.py` | WAV, silencio y trimming | sounddevice/wave | Medio | No ejecutado | Adaptar | Puede producir activos de audio verificables |
| UI/servidor robot | `robot/device/server.py`, `page.html`, `draw.py` | Stream y controles del demo | HTTP, OpenCV, LiveApp | Alto | Tests parciales bloqueados | Aislar | Preservar demo; no convertir en UI EpiGraph |
| Entrada/config robot | `robot/app.py`, `robot/config.py` | CLI, replay, hardware y `.env` | OpenCV/dotenv | Alto | `--help` bloqueado por OpenCV | Adaptar | Separar replay/captura de bootstrap de hardware |
| Helper docente | `helper.py` | Modelos, Edge, cloud y render notebook | Matplotlib, FastEmbed, Edge | Bajo | No importa por Matplotlib | Adaptar | Extraer funciones específicas; no usar módulo monolítico |
| Lesson3 | `Lesson3.ipynb` | Construcción de memoria local multimodal | helper/Qdrant Edge/datos ausentes | Ninguno | No reproducible ahora | Adaptar | Patrones de filtros y CLIP text→image |
| Lesson4 | `Lesson4.ipynb` | Memoria diaria y voz | helper/ASR/datos ausentes | Ninguno | No reproducible ahora | Adaptar | Separación por modalidad y ASR por lotes |
| Lesson5 | `Lesson5.ipynb` | Enseñanza, reconocimiento y frescura | helper/datos/funciones ausentes | Ninguno | Incompleto | Aislar | Solo calibración y conceptos verificables |
| Appendix | `Appendix.ipynb` | Sincronización local/cloud | helper/Qdrant Cloud | Ninguno | No reproducible ahora | Aislar | Infraestructura distribuida fuera de alcance |
| Utilidades destructivas | `helper.py::fresh_start`, `new_shard`; `robot.app --reset` | Recrear almacenamiento | Filesystem/Edge | Variable | Funcionamiento intencional | Descartar | Incompatibles con apertura segura de memoria canónica |

## 8. Mapa entre código actual y capacidades objetivo EpiGraph

`Environment` se incluye aunque no aparecía en la lista final del encargo, porque forma parte del modelo conceptual mínimo del prompt maestro.

| Capacidad objetivo | Código actual relacionado | Cobertura | Brecha y decisión |
|---|---|---|---|
| `Environment` | `Source.scope`, `Episode.location`, `Memory.where` | Informal | Crear entidad canónica; no derivar posición de una etiqueta de dispositivo |
| `Source` | `episodic.domain.Source`; fuentes Atlas; cámara/mic del robot | Parcial alta | Reutilizar y enlazar con uno o varios ambientes |
| `Asset` | `Evidence`, `FilesystemEvidenceStore`, archivos `thumb/scene`, documentos RAG | Parcial | Separar original inmutable de su uso como evidencia y de derivados |
| `ProcessingWindow` | `CaptureSession`, `Track`, frames de replay, intervalos de Episode | No equivalente | Crear entidad propia; DeltaTime/configuración no será unidad semántica |
| `Observation` | `episodic.domain.Observation`, puntos `seen`, chunks documentales | Parcial | Eliminar dependencia obligatoria de Episode; enlazar Source/Asset/Window/EvidenceReference |
| `Entity` | `episodic.domain.Entity`, labels del robot | Parcial | Reutilizar Entity; nunca convertir track o label en identidad persistente |
| `Event` | Textos `data/atlas/events/`, observaciones narrativas | Ausente | Crear entidad para acción/cambio; un fichero de texto no es un Event canónico |
| `MicroEpisode` | Ninguno | Ausente | Crear composición coherente de observaciones/eventos con regla y versión |
| `Episode` | `episodic.domain.Episode` | Parcial | Migrar de entrada primaria a agregado de microepisodios; conservar compatibilidad Atlas |
| `Relation` | `Observation.entity_id`, listas de fuentes/evidencias | Muy débil | Crear relación tipada con procedencia, confianza, método, versión y estado |
| `Hypothesis` | Anotaciones libres y veredictos de consulta | Ausente | Crear tipo separado; nunca persistirla como hecho u observación directa |
| `EvidenceReference` | `evidence_ids`, `source_path`, `thumb`, `scene` | Parcial débil | Crear referencia exacta a asset + intervalo/frame/región/página/fragmento |
| `RetrievalTool` | `DomainRetrieverTool`, `RetrievalService` | Dos implementaciones | Adaptar RetrievalService como fuente verificable y envolverlo con contrato de tool |
| `QueryOrchestrator` | `DomainRAGAgent`, selector de intención de `RetrievalService` | Parcial y separado | Primero orquestador determinista; planificador agente solo después de construir la memoria |

## 9. Pruebas y ejecuciones realizadas

### 9.1 Suite episódica

Comando equivalente:

```powershell
& 'C:\Users\sqs.erasmus\.conda\envs\LabMemoryEpic01\python.exe' -m pytest -q
```

Resultado:

```text
42 passed, 2 warnings
```

Warnings:

1. Deprecación de `TestClient` de Starlette respecto a `httpx`.
2. Los índices de payload no tienen efecto en Qdrant local/in-memory usado por una prueba.

Una primera ejecución con `--basetemp C:\tmp\...` produjo 17 errores `PermissionError` porque esa ruta no era escribible para pytest bajo esta sesión. Al repetir con un directorio temporal validado dentro del workspace, pasaron las 42 pruebas. No es un fallo de aplicación.

### 9.2 Pruebas del robot

```powershell
& $PY -m unittest tests.test_runtime -v
```

Resultado: 4 pruebas aprobadas.

Descubrimiento completo:

```text
4 pruebas aprobadas
2 módulos no recolectados:
  - test_certificate: falta cryptography
  - test_live: falta cv2/opencv-python
```

`python -m robot.app --help` también falla antes de construir el parser porque `robot/app.py` importa `cv2` en el nivel superior.

### 9.3 Flujo episódico controlado

Se ejecutó en un directorio temporal posteriormente eliminado:

```text
init
→ import Atlas
→ import Atlas por segunda vez
→ index con encoders deterministas
→ interval_search
→ timeline
```

Resultado:

- Primera importación: 8 fuentes, 1 sesión, 10 episodios, 10 evidencias, 15 observaciones, 1 anotación, 7 entidades y 20 representaciones.
- Segunda importación: 0 altas, 10 episodios duplicados omitidos.
- Indexación: 20 representaciones y 20 puntos.
- Intervalo `[2026-01-01T18:23:35Z, 2026-01-01T18:25:10Z)`: exactamente `ATLAS-EP-007` y `ATLAS-EP-006`.
- Timeline CLI: operativa.
- Los datos temporales se eliminaron al terminar.

### 9.4 Encoders reales

```powershell
& $PY -m scripts.verify_real_encoders
```

Resultado:

| Encoder | Dimensión | Norma L2 | Tiempo observado |
|---|---:|---:|---:|
| Nomic documento | 768 | 19.993645 | 1198.112 ms |
| Nomic consulta | 768 | 21.366204 | 17.289 ms |
| CLIP texto | 512 | 1.0 | 556.794 ms |
| CLIP visión | 512 | 1.0 | 635.354 ms |

El script confirma conectividad real a los modelos, no calidad general de recuperación.

### 9.5 RAG legado

Durante la auditoría:

- Qdrant HTTP `localhost:6333`: disponible.
- Ollama `localhost:11434`: disponible.
- Importación de `api.server`: correcta.
- `GET /api/status`: HTTP 200, Qdrant y Ollama conectados.
- `GET /api/collections`: HTTP 200, cinco colecciones disponibles.
- Búsqueda híbrida en `dominio_memoria_episodica`: HTTP 200, tres resultados.
- `/api/agent/ask`: HTTP 200. La impresión posterior de la respuesta falló en la consola de auditoría por codificación CP1252 de un carácter Unicode; el endpoint ya había respondido correctamente. No se considera prueba suficiente de calidad del agente.

El RAG no tiene suite de tests propia.

### 9.6 Datos persistentes observados en modo solo lectura

`episodic-data/episodic.db` contiene:

| Tabla | Filas |
|---|---:|
| sources | 8 |
| capture_sessions | 1 |
| evidence | 10 |
| episodes | 10 |
| observations | 15 |
| annotations | 1 |
| entities | 7 |
| representations | 20 |
| query_runs | 37 |

El directorio `episodic-data/` contiene 88 archivos y ocupa aproximadamente 145 MB. `data/atlas/` contiene 22 archivos y aproximadamente 127 KB.

## 10. Errores, dependencias ausentes y supuestos ocultos

### 10.1 Modelo y persistencia

1. `Evidence` combina lo que EpiGraph separa como `Asset` y `EvidenceReference`.
2. `Observation` requiere `episode_id`, por lo que no puede preceder a la composición episódica.
3. `Episode` se importa directamente desde el manifiesto.
4. `CaptureSession` no equivale a `ProcessingWindow`.
5. `location` y `scope` son strings; no existe `Environment`.
6. No hay tablas para Event, MicroEpisode, Relation, Hypothesis o EvidenceReference.
7. La relación observación-entidad es un único `entity_id`, sin procedencia de asociación.
8. `indexable_text` vive dentro del episodio canónico sin un registro explícito de representación derivada, método o revisión.
9. `model_revision` usa valores hardcoded `v1` y `v1.5`; no son revisiones resueltas de artefacto.
10. Las dimensiones de representación se hardcodean también durante ingesta y rebuild.

### 10.2 Ingesta y seguridad

1. `ManifestSchema` está definido pero `IngestService` no lo utiliza.
2. Las referencias cruzadas del manifiesto no se validan de forma completa.
3. Una ruta de medio se resuelve con `(base_dir / rel_file).resolve()` sin comprobar que permanezca bajo la raíz autorizada.
4. Si el fichero no existe, la ingesta crea contenido sintético y continúa; esto oculta referencias inválidas.
5. Copiar el blob sucede dentro del flujo transaccional SQLite, pero un rollback no elimina necesariamente el blob huérfano.
6. `import_root` existe en configuración pero no se usa.
7. La API importa Atlas por defecto desde una ruta relativa al directorio de ejecución.
8. El endpoint de búsqueda acepta `image_query_path` enviado por el cliente; expone rutas arbitrarias del host.
9. CORS permite `*` junto con credenciales.
10. No hay límites consistentes de tamaño/MIME para todos los medios.

### 10.3 Indexación y ciclo de vida

1. `outbox_retry_limit` está declarado pero no se usa.
2. La outbox no guarda número de intentos, lease, último error o próxima ejecución.
3. `Clock` está declarado pero no se inyecta.
4. El rebuild seguro funciona, pero el modelo de targets solo contempla episodio/evidencia actuales.
5. El borrado canónico y la revalidación existen parcialmente; falta una operación durable completa para grafo y referencias.

### 10.4 Recuperación

1. La recuperación episódica retorna principalmente episodios; no puede consultar las capas EpiGraph ausentes.
2. La resolución por label no basta para identidad persistente.
3. El RAG y el agente consultan Qdrant directamente, sin revalidar contra una memoria episódica canónica.
4. `DomainRAGAgent.ask` realiza la recuperación dos veces: una para `raw_results` y otra dentro de `retrieve_formatted_context`.
5. El corpus visual Atlas son tarjetas sintéticas generadas con texto y colores; sirve para plumbing, no para validar precisión semántica CLIP.
6. El RAG de documentos no mantiene activos originales ni referencias exactas a offsets de fragmento.

### 10.5 Robot y notebooks

1. Dependencias y pesos necesarios no están instalados en el entorno actual.
2. La entrada del robot importa OpenCV incluso para `--help`.
3. Faltan `.env.example`, pesos y `edge-data` en la referencia recibida.
4. `helper.py` no importa sin Matplotlib.
5. Lesson5 y helper no son de la misma versión funcional.
6. Los outputs guardados de notebooks no demuestran reproducibilidad actual.
7. Las rutas y datos docentes no están incluidos.
8. Hay operaciones destructivas diseñadas para clase/demo que no pueden pasar a producto.

### 10.6 Repositorio y reproducibilidad

1. La raíz no es Git y no tiene estrategia recuperable de cambios.
2. La raíz carece de `pyproject.toml` y `uv.lock`.
3. El robot anidado sí tiene lock, pero está separado y Git no permite inspeccionarlo bajo el usuario actual por ownership.
4. Existen tres configuraciones y tres interfaces sin composición común.
5. Los datos del RAG HTTP, Qdrant Edge episódico y robot están conceptualmente separados; no deben sincronizarse informalmente.

## 11. Arquitectura objetivo mínima

La arquitectura objetivo debe seguir siendo un **monolito modular** en el proceso principal. No se justifican microservicios, broker ni nuevas bases de datos.

```text
Fuentes / archivos / robot futuro
                │
                ▼
┌──────────────────────────────────────────────────────────┐
│ Ingesta primaria                                         │
│ Environment → Source → Asset → EvidenceReference         │
└───────────────────────────┬──────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────┐
│ Procesamiento determinista                               │
│ ProcessingWindow → Observation → Entity / Event          │
└───────────────────────────┬──────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────┐
│ Composición episódica                                    │
│ MicroEpisode → Episode → Relation                        │
│                         └→ Hypothesis (separada)          │
└───────────────────────────┬──────────────────────────────┘
                            │
            ┌───────────────┴────────────────┐
            ▼                                ▼
     SQLite canónico                  Media/Asset store
            │
            ▼ outbox
     Qdrant reconstruible
            │
            ▼
 RetrievalTool → QueryOrchestrator determinista
            │
            └→ agentes consumidores en una fase posterior
```

### 11.1 Organización propuesta sobre el código existente

No se debe crear una aplicación paralela ni renombrar todo el paquete de inmediato. La evolución mínima puede realizarse dentro de `episodic/`:

```text
episodic/
  domain/
    provenance.py       Environment, Source, Asset, EvidenceReference
    processing.py       ProcessingWindow, Observation, Event
    identity.py         Entity
    episodes.py         MicroEpisode, Episode
    relations.py        Relation, Hypothesis
    representations.py  Representation, QueryRun
    times.py            TimeSpec, Interval reutilizados
  application/
    ingest_assets.py
    process_windows.py
    compose_episodes.py
    manage_relations.py
    indexing.py
    retrieval.py
    lifecycle.py
  ports/
    repository.py
    asset_store.py
    processors.py
    encoders.py
    vector_index.py
    retrieval_tool.py
  adapters/
    sqlite/
    media/
    qdrant_edge/
    qdrant/
    models/
    perception/         solo cuando se incorpore detector/vídeo
    documents/          extracción de documentos cuando se solicite
  interfaces/
    cli/
    http/
    web/
```

Los nombres son una guía de límites, no una solicitud para crear scaffolding vacío. Cada módulo solo debe aparecer cuando su fase lo implemente y tenga consumidor y pruebas.

### 11.2 Reglas de dependencia

1. Dominio no importa FastAPI, Qdrant, OpenCV, Torch, FastEmbed ni filesystem.
2. SQLite conserva hechos canónicos y estados; Qdrant solo representaciones derivadas.
3. Un procesador recibe `Asset`/`ProcessingWindow` y produce observaciones o eventos con referencias exactas.
4. Un compositor recibe observaciones/eventos y propone o crea microepisodios mediante reglas versionadas.
5. Una hipótesis nunca se guarda como observación confirmada.
6. El robot invoca casos de uso de ingesta; no escribe directamente en SQLite y Qdrant.
7. Los agentes solo consumen `RetrievalTool`/`QueryOrchestrator` y no ejecutan percepción primaria.
8. Los ambientes filtran o agrupan, pero no crean silos de almacenamiento.

## 12. Plan mínimo de migración

Este plan es una propuesta. No se ha implementado ninguna etapa.

### Paso 1 — Congelar contratos y datos actuales

- Conservar las 42 pruebas como baseline.
- Documentar el manifiesto Atlas actual como `legacy prepared episode v1`.
- Añadir en la siguiente fase pruebas de migración antes de cambiar tablas.
- No borrar ni reescribir `episodic-data`.

Gate: aplicación actual abre en modo solo lectura y los conteos no cambian.

### Paso 2 — Procedencia primaria aditiva

- Añadir `Environment`, `Asset` y `EvidenceReference` mediante nuevas migraciones.
- Mantener `Source` y enlazarlo a ambientes sin convertir el ambiente en silo.
- Migrar cada `Evidence` actual a un `Asset` administrado y una referencia completa compatible.
- Mantener IDs existentes mediante tabla de correspondencia o relación explícita.

Gate: importar y consultar un activo preserva hash, fuente, tiempo y ruta original sin crear Episode.

### Paso 3 — Ventanas y resultados de procesamiento

- Añadir `ProcessingWindow` separado de `CaptureSession`.
- Hacer que Observation enlace ventana, activo, procesador y evidencias; permitir que exista sin Episode.
- Añadir Event como acción/cambio distinto de una descripción libre.
- Registrar método, versión, confianza y procedencia.

Gate: una ventana produce observaciones/eventos trazables y no aparece como episodio.

### Paso 4 — Composición episódica

- Añadir `MicroEpisode` y relaciones de pertenencia.
- Convertir Episode en agregado de microepisodios.
- Importar los episodios Atlas antiguos como episodios declarados compatibles, con procedencia explícita, no como detección automática.
- Separar narrativa/indexable text como representación derivada versionada.

Gate: cada Episode se reconstruye hasta sus microepisodios, eventos, observaciones y activos.

### Paso 5 — Relaciones e hipótesis

- Añadir Relation tipada con origen, confianza, método, versión y estado.
- Añadir Hypothesis separada de observaciones/eventos confirmados.
- Definir validación, rechazo y supersesión sin sobrescribir historial.

Gate: una hipótesis no aparece en consultas de hechos salvo que el usuario solicite hipótesis.

### Paso 6 — Reorientar indexación y recuperación

- Mantener outbox y adaptadores Qdrant existentes.
- Versionar representaciones por target y modelo real.
- Permitir recuperación de activos, observaciones, eventos, microepisodios y episodios.
- Adaptar `RetrievalService` a `RetrievalTool` con referencias verificables.
- Mantener `DomainRAGAgent` aislado hasta que el contrato anterior sea estable.

Gate: todo resultado indexado se revalida y puede navegarse hasta el Asset original.

### Paso 7 — Productores e interfaz por incrementos

- Adaptar documentos primero como productor determinista si esa es la fase autorizada.
- Incorporar vídeo/detector y audio/ASR únicamente en sus fases.
- Modificar la UI solo para representar entidades realmente disponibles.
- Mover diagnóstico y administración a un nivel secundario.

Gate: cada fase termina con CLI, API, UI mínima, pruebas y recorrido reproducible sobre la misma composición de servicios.

## 13. Estado de cierre de Fase 0

La auditoría solicitada queda completada sin implementar funcionalidades, generar agentes ni cambiar dependencias.

Resultado de decisión:

- **Reutilizar**: núcleo temporal, SQLite, media store, outbox, Qdrant Edge, encoders, filtros, CLI y pruebas episódicas.
- **Adaptar**: dominio episódico, ingesta, recuperación, modelo Atlas, utilidades de modelos/ASR/detector y RetrievalTool.
- **Aislar**: RAG y agente legado, UI robot, Qdrant Cloud, despliegue hardware y notebooks no reproducibles.
- **Descartar del producto**: resets destructivos y supuestos que convierten ventana, label, track, ubicación del dispositivo o texto derivado en hechos canónicos.

La siguiente fase no debe comenzar sin autorización expresa y debe limitarse al primer paso de dominio que se acuerde.

## 14. Acuerdo de verificación para las fases siguientes

La Fase 0 se cierra con aceptación expresa del usuario. A partir de este punto se aplicarán estas reglas:

1. No se iniciará una fase sin que el usuario la autorice expresamente.
2. Cada fase tendrá un criterio de salida observable y reproducible.
3. Cuando tenga sentido funcional, el incremento se expondrá en el frontend para que el usuario pueda verificarlo directamente.
4. La UI utilizará los mismos servicios de aplicación que la CLI y la API; no duplicará reglas de dominio, composición, aceptación o tiempo.
5. La visualización distinguirá datos originales, derivados, relaciones e hipótesis según las entidades disponibles en esa fase.
6. No se mostrarán controles o pantallas para capacidades todavía no implementadas.
7. Si una fase es exclusivamente interna —por ejemplo, una migración o una garantía transaccional— se explicará por qué una UI no añade una comprobación fiable y se proporcionará una prueba equivalente por API o CLI.
8. Las herramientas de diagnóstico permanecerán separadas del recorrido principal del producto.
9. Una fase que incluya frontend no se cerrará únicamente con tests: debe existir un recorrido manual documentado desde el navegador.
10. La incorporación de frontend no amplía por sí misma el alcance funcional de la fase.

Este acuerdo no autoriza el comienzo de la siguiente fase; solo fija cómo se validarán los incrementos posteriores.
