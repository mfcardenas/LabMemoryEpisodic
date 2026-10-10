# Arquitectura del Sistema de Memoria Episódica (Fase 1)

## 0. Límite de integración actual

La raíz contiene actualmente dos aplicaciones ejecutables independientes y una referencia todavía no integrada:

- el RAG documental legado (`python app.py`, puerto 8000), con Qdrant HTTP, Ollama y `DomainRAGAgent`;
- Memory Episodic (`python -m episodic.app`, puerto 8100), con SQLite canónico y Qdrant Edge derivado;
- el modo robot, aún dentro de `sc-on-device-ai-memory/sc-on-device-ai-memory/` y pendiente de incorporarse como paquete `robot/` junto a `episodic/`.

No existen imports desde `episodic/` hacia el RAG legado o el robot. El agente legado no participa en ingesta, indexación, consolidación ni recuperación episódica. Los agentes serán consumidores futuros de herramientas basadas en `QueryRun`, después de cerrar la Fase 1.

FastAPI se mantiene deliberadamente para la API episódica porque ya es infraestructura del proyecto principal. `robot/device/server.py` seguirá atendiendo la demo robot cuando sea integrada; no se fuerza una abstracción común entre servidores con responsabilidades distintas.

## 1. Visión General y Principios de Diseño

El sistema de memoria episódica implementa una arquitectura modular desacoplada basada en el patrón de **Puertos y Adaptadores (Arquitectura Hexagonal)**.

```
+------------------------------------------------------------------------+
|                      Interfaces de Usuario / Red                       |
|   CLI (python -m episodic.app)   |   FastAPI REST API   |   Web UI SPA |
+-----------------------------------+----------------------+-------------+
                                     |
                                     v
+------------------------------------------------------------------------+
|                         Servicios de Aplicación                        |
|   IngestService  |  IndexingWorker  |  RetrievalService  |  Lifecycle  |
+-----------------------------------+------------------------------------+
                                     |
                                     v
+------------------------------------------------------------------------+
|                          Puertos (Protocols)                           |
|  EpisodicRepository  |  EvidenceStore  |  VectorIndex  |  Encoders    |
+-----------------------------------+------------------------------------+
                                     |
           +-------------------------+-------------------------+
           |                         |                         |
           v                         v                         v
+-----------------------+ +-----------------------+ +--------------------+
|    Adaptador SQLite   | |     Almacén Medios    | |  Adaptador Qdrant  |
|  (Fuente de Verdad)   | |  (SHA-256 + Atomic)   | |  (Proy. Reconstru) |
+-----------------------+ +-----------------------+ +--------------------+
```

### Principios Fundamentales:

1. **SQLite como Fuente Canónica de Verdad**:
   - Todo estado canónico, relaciones foráneas, auditoría e historial reside en SQLite.
   - Modo WAL (`journal_mode = WAL`) y claves foráneas obligatorias (`foreign_keys = ON`).
   - Paginación basada en cursor compuesto `(created_at, id)` para evitar barridos arbitrarios de memoria (`scroll limit=10000`).

2. **Qdrant como Proyección Derivada Reconstruible**:
   - Qdrant no es la fuente de verdad. Si la colección se corrompe o se borra, puede reconstruirse íntegramente desde SQLite con `rebuild-index`.
   - Vectores nombrados independientes: `"text"` (768d, Nomic) e `"image"` (512d, CLIP).
   - Filtros pre-topk para visibilidad, fuentes y solapamiento temporal.
   - Cada chunk guarda `timeline_start/end`, milisegundos UTC, tipo temporal, incertidumbre y procedencia. Una captura puntual conserva `start == end` y usa un ancho técnico de 1 ms únicamente para el filtro.
   - El nombre configurado es un alias lógico. `rebuild-index` construye una generación staging, verifica el recuento exacto y cambia el alias antes de retirar la generación anterior.
   - Qdrant Edge es el backend predeterminado y guarda generaciones bajo `data/index/qdrant-edge/`. El perfil HTTP sigue disponible mediante `--vector-backend http`.
   - Edge usa un fichero `ACTIVE` reemplazado atómicamente y un único proceso propietario del shard; su WAL impide aperturas concurrentes desde procesos distintos.

3. **Patrón Outbox para Indexación Asíncrona**:
   - La ingesta persiste las entidades y encola registros en la tabla `representations` con estado `pending`.
   - `IndexingWorker` procesa los lotes, genera los vectores y actualiza a `indexed` registrando el `qdrant_point_id`.
   - Si la indexación falla o se reinicia el proceso, ninguna evidencia ni episodio se pierde.

4. **Aislamiento Estricto de Espacios Vectoriales**:
   - Nomic text y CLIP tienen distribuciones y escalas matemáticas distintas.
   - **Regla de oro**: Nunca sumar scores brutos de encoders diferentes.
   - La búsqueda multimodal combina los rankings mediante **Reciprocal Rank Fusion (RRF)**:
     $$Score_{RRF} = \sum_{m \in \{text, image\}} \frac{1}{k + rank_m}$$

5. **Modelo Temporal Riguroso**:
   - `TimeSpec` fuerza instantes en UTC explícito.
   - Intervalos semiabiertos $[inicio, fin)$ donde una foto es un intervalo puntual ($start == end$).
   - Tiempos desconocidos se representan explícitamente (`is_unknown=True`), sin fabricar timestamps artificiales.
   - Las evidencias sin reloj propio heredan como contexto filtrable el intervalo de su único episodio y registran `timeline_origin=episode_interval`; también conservan `episode_timelines` para trazabilidad.

6. **Sistema de Veredictos (Verdict System)**:
   - Evita la trampa del booleano binario sí/no.
   - Categorías: `SUPPORTED`, `CANDIDATE`, `AMBIGUOUS`, `INSUFFICIENT_EVIDENCE`, `NO_MATCH`.
   - Cada consulta genera un registro `QueryRun` auditable con explicaciones y referencias.
   - `AMBIGUOUS` no se infiere solo por cercanía de scores: la consulta debe declarar `require_unique_resolution` cuando necesite resolver una única correspondencia.

7. **Anotaciones Inmutables Versionadas**:
   - Una corrección o enriquecimiento no sobreescribe la observación original.
   - Genera una nueva revisión (`revision_number = n + 1`) con enlace a `supersedes_id`, preservando la procedencia del autor.

8. **Almacén de Medios y Purga por Referencias**:
   - Archivos guardados bajo su hash SHA-256 con escritura atómica.
   - Un medio eliminado solo se borra físicamente del disco si ninguna otra evidencia activa mantiene referencias al mismo blob.

---

## 2. Esquema de Entidades

| Entidad | Propósito | Clave / Idempotencia |
|---|---|---|
| **Source** | Fuente de captura (cámara, sensor, micrófono) | `id` |
| **CaptureSession** | Sesión temporal de ingesta | `id` |
| **Evidence** | Medio verificable (imagen, audio, texto) | `(source_id, session_id, external_capture_id)` |
| **Episode** | Suceso contextual delimitado en tiempo/espacio | `external_id` (e.g. `ATLAS-EP-001`) |
| **Observation** | Dato observado, inferido o declarado con procedencia | `id`, vinculado a `episode_id` |
| **Annotation** | Enriquecimiento humano o supervisado versionado | `(episode_id, revision_number)` |
| **Entity** | Identidad persistente (persona, objeto de interés) | `id` (etiqueta repetida $\neq$ misma entidad) |
| **Representation** | Outbox de embedding asociado a modelo y revisión | `(target_type, target_id, space)` |
| **QueryRun** | Registro de auditoría y decisión de consulta | `id` |
