# Mapa auditado de reutilización y adaptación

Fecha de auditoría: 2026-10-04. Actualizado para Fase 6: 2026-10-06. Este documento diferencia reutilización directa, adaptación de comportamiento, referencia conceptual y trabajo aplazado.

## Límites actuales

Existen tres rutas independientes:

- el RAG legado de la raíz (`app.py`, `agent/`, `api/`, `pipeline/`, `storage/`, `static/`);
- la aplicación Memory Episodic (`episodic/`);
- el robot, todavía ubicado únicamente en `sc-on-device-ai-memory/sc-on-device-ai-memory/`.

No hay imports desde `episodic/` hacia el RAG legado ni hacia `robot/`. Por ello, la reutilización actual del robot/helper es una adaptación localizada de patrones y llamadas, no una extracción compartida de código.

## Referencia robot y material docente

| Origen y símbolo | Estado actual | Destino | Evidencia/decisión |
|---|---|---|---|
| `robot/brain/models.py::_text_model`, `embed_text`, `embed_query` | Adaptación de comportamiento | `episodic/adapters/models/fastembed.py::FastEmbedEpisodeTextEncoder` | Conserva FastEmbed, Nomic, `.embed`, `.query_embed`, caché, threads y carga diferida. No importa el módulo original. |
| `robot/brain/models.py::_clip_vision`, `embed_crop` | Adaptación parcial | `FastEmbedImageEncoder` | Conserva modelo/caché/carga diferida. La ruta actual recibe paths RGB manejados por FastEmbed; la conversión BGR→RGB se incorporará al adaptador de crops en Fase 2. |
| `helper.py::_clip_text`, `embed_query_clip` | Adaptación de comportamiento | `FastEmbedAlignedTextEncoder` | Incorpora la torre textual CLIP ausente en el robot. |
| `robot/brain/memory.py::Memory` | Adaptación de API y operación | `episodic/adapters/qdrant_edge/index.py` | Reutiliza EdgeShard, vectores nombrados, filtros, lock y flush; añade UUIDv5, payload episódico y generaciones. No comparte clase ni taxonomía `taught/seen/ignored`. |
| `robot/brain/detect.py::Detector`, `Track`, `padded_crop`, `crop_quality` | Backend semántico aplazado | Posterior a Fase 4 | `ImageProcessor` y `VideoProcessor` ya producen evidencia técnica verificable. YOLO/BOT-SORT no se importa porque faltan OpenCV, Torch, Ultralytics y pesos; no se inventan detecciones. |
| `robot/brain/core.py::Robot.process_frame` | Referencia pendiente | Fase 2 | Futuro productor del servicio de ingesta. Debe corregir el único write por `sighted` y conservar first/last seen. |
| `robot/brain/core.py::Robot.ask` | Excluido del núcleo | Ninguno | No satisface contratos temporales ni verificación canónica episódica. |
| `robot/device/live.py` | Aplazado | Fase 2/6 | Reutilizar workers, cierre y watchdog cuando el robot esté integrado. |
| `robot/device/mic.py::wav_rms` | Adaptación de comportamiento | `episodic/processors/audio.py` | El cálculo RMS se adaptó por intervalo WAV sin importar configuración de micrófono ni modificar el original. Captura, recorte y silencio siguen fuera de alcance. |
| `robot/device/runtime.py` | Pendiente de integración | Modo robot | Preservar su comando; no promover timestamps de presentación a tiempo canónico. |
| `robot/device/server.py` | No usado por episodic | Modo robot | Se mantendrá para su demo. FastAPI es la decisión explícita para episodic. |
| `robot/device/page.html` | No reutilizado | Modo robot | La SPA episódica fue implementada por separado. Solo se extraerán componentes si aparece un consumidor compartido. |
| `robot/config.py`, `robot/app.py` | Pendiente | Próximo PC-1 | Incorporar con atribución y mantener `python -m robot.app`. |
| `testdata/verify_scores.py` | No usado por episodic | Regresión robot | Sus umbrales no se trasladan a Nomic/CLIP episódico. |
| `helper.py::fresh_start`, `new_shard` | Excluido | Ninguno | Comportamiento destructivo incompatible con apertura y rebuild seguros. |
| Whisper en helper/robot | Aplazado | Backend ASR opcional | Se conserva el patrón de carga diferida/lote. `onnx_asr` no está instalado y el `AudioProcessor` de Fase 4 emite mediciones, no transcripciones ficticias. |
| Segmentación episódica del robot | No reutilizable como compositor | `DeterministicEpisodeComposer` | El robot trabaja con ciclos/frames y no distingue `ProcessingWindow`, `MicroEpisode` y `Episode`. Fase 5 reutiliza el dominio y SQLite de EpiGraph, pero implementa reglas explícitas sin agentes ni LLM. |
| Identidad/tracks del robot | Referencia conceptual, no resolución persistente | `LogicalGraphService` | Los tracks locales no equivalen a `Entity`. Fase 6 conserva entidades separadas, representa candidatos como hipótesis y solo crea equivalencia tras validación. |

## RAG legado de la raíz

| Componente | Uso actual en episodic | Decisión |
|---|---:|---|
| `pipeline/chunker.py::TextChunker` | Algoritmo compartido | Delega en `episodic/application/text_fragments.py`; conserva su API y EpiGraph añade offsets exactos. |
| `domain/`, `embeddings/`, `pipeline/indexer.py`, `storage/` | Ninguno | Preservar como RAG documental separado. |
| `agent/DomainRAGAgent` | Ninguno | Mantener en legado; adaptar en Fase 5 sobre una herramienta episódica, no sobre Qdrant directo. |
| `DomainRetrieverTool.get_tool_schema()` | Ninguno | Patrón candidato para exponer `QueryRun` verificable en Fase 5. |
| `QdrantHybridStore.hybrid_search` | Ninguno | RRF dense/sparse sirve como referencia, pero no conserva los metadatos multimodales exigidos. |
| `api/server.py`, `static/` | Ninguno | Mantener puerto 8000 y datos independientes. |
| FastAPI/Uvicorn/Pydantic | Dependencia compartida | Infraestructura elegida para la API episódica. |

## Implementación episódica propia

| Componente | Estado | Observación |
|---|---|---|
| Dominio, SQLite, media store y servicios | Activo | Implementación específica del nuevo dominio, no código trasladado del robot. |
| Qdrant Edge | Activo y validado | Adaptación más directa de los patrones de `Memory`; requiere atribución explícita al integrar la licencia. |
| Nomic y pareja CLIP | Activos | Nomic/vision siguen el robot; CLIP text sigue el helper. Revisión/checksum aún no está resuelta. |
| FastAPI y SPA | Activos | Implementación nueva; no reutiliza `StreamHandler` ni `page.html`. |
| `ManifestSchema`, `Clock`, retries/import root | Declarados pero no conectados | Deben integrarse antes de aceptar la Fase 1. |

## Regla para próximos cambios

Cada incorporación desde la referencia debe registrar: archivo y símbolo de origen, licencia, cambio realizado, test de regresión y consumidor real. Copiar un módulo sin consumidor o dejar un import sin uso no contará como reutilización.
