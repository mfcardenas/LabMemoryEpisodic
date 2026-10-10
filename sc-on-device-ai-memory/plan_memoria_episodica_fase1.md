# Plan actualizado y punto de control — Memory Episodic Project (Fase 1)

> **Punto de control vigente (2026-10-07): PC-EG1.** Queda bloqueado el avance
> hacia agentes o fases nuevas. La prioridad es validar desde el frontend el
> recorrido `Asset -> ProcessingWindow -> Observation/Event -> MicroEpisode ->
> Episode -> Qdrant -> recuperación con evidencia`. El frontend Atlas se aísla
> como legado y deja de representar el producto. Véase
> `docs/pc-eg1-consolidacion-epigraph.md`.

> **Revisión**: 2026-10-04  
> **Raíz de producto**: `C:\Users\sqs.erasmus\Documents\GitHub\LabMemoryEpisodic`  
> **Entorno comprobado**: Conda `LabMemoryEpic01`, Python 3.13.16  
> **Referencia de solo lectura**: `sc-on-device-ai-memory/sc-on-device-ai-memory/`  
> **Documento rector**: `Prompt_Codex_Memory_Episodic_Project.md`  
> **Estado global**: Fase 1 parcial; base episódica funcional, integración y garantías de aceptación incompletas.

---

## 1. Decisión de arquitectura

El producto se desarrollará como un **monolito modular con tres modos preservados**, pero una sola memoria canónica episódica:

```text
LabMemoryEpisodic/
├── robot/                 modo robot preservado; productor futuro de observaciones
├── agent/, api/, ...      RAG multidominio legado; memoria separada y regresión propia
└── episodic/              producto Memory Episodic; SQLite canónico + Qdrant derivado
```

Reglas:

1. `episodic/` no dependerá de cámara, micrófono, OpenCV, Torch, Ultralytics ni Ollama.
2. SQLite será la única fuente de verdad de la aplicación episódica. Qdrant Edge será una proyección reconstruible.
3. El RAG legado y el robot mantendrán directorios de datos separados; no habrá sincronización informal.
4. Los agentes no forman parte del pipeline de Fase 1. En una fase posterior consumirán `QueryRun` y herramientas de consulta/verificación.
5. Reutilizar significa importar, extraer o adaptar símbolos concretos con prueba y atribución. La similitud conceptual no se marcará como reutilización directa.
6. FastAPI se conserva para la interfaz episódica porque ya forma parte del proyecto principal y permite compartir `bootstrap` y servicios con la CLI. Esta es una decisión consciente, no una reutilización de `robot/device/server.py`.

---

## 2. Auditoría: qué existe realmente

### 2.1 Tres rutas de ejecución independientes

| Ruta | Entrada | Memoria | Agente | Estado |
|---|---|---|---|---|
| RAG legado de la raíz | `python app.py` | Qdrant HTTP, colecciones de documentos | `DomainRAGAgent` + Ollama | Preservado, aislado de `episodic/` |
| Memory Episodic | `python -m episodic.app` | SQLite + medios + Qdrant Edge/HTTP | Ninguno | Ruta activa de Fase 1 |
| Robot de referencia | `python -m robot.app` dentro de la referencia | Qdrant Edge del robot | Ninguno | Aún no incorporado a la raíz del producto |

Comprobación de dependencias: `episodic/`, sus tests y scripts no importan `agent`, `api`, `domain`, `embeddings`, `pipeline`, `storage` ni `robot`. Por tanto, hoy hay convivencia física, no integración.

### 2.2 Código usado por la aplicación episódica

| Área | Archivos activos | Uso actual |
|---|---|---|
| Dominio | `episodic/domain/*` | Entidades, tiempos, procedencia, intenciones y veredictos |
| Aplicación | `ingest.py`, `indexing.py`, `retrieval.py`, `lifecycle.py`, `temporal_index.py` | Ingesta, outbox básico, búsqueda, tiempo, anotaciones, borrado y rebuild |
| Persistencia | `adapters/sqlite/*`, `adapters/media/store.py` | Fuente canónica, migración v1 y blobs por hash |
| Modelos | `adapters/models/fastembed.py` | Nomic, CLIP vision y CLIP text reales con carga diferida |
| Vector | `adapters/qdrant_edge/index.py` | Backend predeterminado, filtros temporales y generaciones |
| Vector opcional | `adapters/qdrant/index.py` | Perfil HTTP y backend local para pruebas |
| Interfaces | `interfaces/cli`, `interfaces/http`, `interfaces/web` | CLI, FastAPI y SPA sobre los mismos servicios |
| Evaluación | `episodic/evaluation.py`, `scripts/evaluate_atlas_text.py` | Métricas textuales reales |
| Datos | `data/atlas/manifest.json`, `media/`, `queries.json` | Fixture sintético de desarrollo |
| Pruebas | `tests/unit`, `tests/integration` | 41 pruebas aprobadas en el entorno actual |

### 2.3 Código presente pero todavía no conectado o no acreditado

| Componente | Situación real | Decisión |
|---|---|---|
| `episodic/manifest_schema.py` | Existe, pero `IngestService` no lo invoca | Conectarlo antes de aceptar la ingesta |
| `episodic/ports/clock.py` | Puerto declarado sin implementación ni uso | Inyectarlo en servicios temporales o retirarlo justificadamente |
| `outbox_retry_limit` | Configurado pero nunca consumido | Implementar intentos, error, lease y recuperación |
| `import_root` | Configurado pero no aplicado | Usarlo para confinar importación HTTP |
| `EvidenceState.PENDING_*` | Declarado, pero el ciclo de borrado no lo usa | Integrarlo con operaciones durables de índice/medios |
| `data/atlas/events/*.txt` | No tiene consumidores | Integrarlo explícitamente o eliminarlo del corpus de producto |
| `scripts/verify_real_encoders.py` | Solo comprueba carga/dimensiones | Mantener como smoke, no como evaluación visual |
| Búsqueda CLIP real | Encoder y camino técnico existen | Falta evaluación imagen→imagen y texto→imagen |
| UI de imagen | No existe upload/selección segura | Implementar mediante upload o `evidence_id`, nunca ruta arbitraria |
| `GET /api/jobs/{id}` | Requerido, no implementado | Añadir al cerrar el outbox durable |
| Agente RAG raíz | Solo lo usa `api/server.py` del modo legado | Preservar; adaptar como herramienta episódica recién en Fase 5 |

---

## 3. Reutilización comprobada y reutilización pendiente

### 3.1 Desde el robot y el helper de referencia

| Origen | Estado honesto | Destino/decisión |
|---|---|---|
| `robot/brain/models.py` | **Adaptación de comportamiento**, sin import directo | Carga diferida, Nomic, CLIP vision, caché y threads reaparecen en `episodic/adapters/models/fastembed.py` |
| `helper.py` (`_clip_text`, `embed_query_clip`) | **Adaptación de comportamiento**, sin import directo | Torre CLIP text implementada como `FastEmbedAlignedTextEncoder` |
| `robot/brain/memory.py` | **Adaptación de API/patrón**, no extracción compartida | EdgeShard, vectores nombrados, locks y flush se adaptaron al índice episódico |
| `robot/brain/detect.py` | **No usado aún** | Reutilización prevista en Fase 2 mediante `VisualPerception`; no copiar todavía al core |
| `robot/brain/core.py` | **Solo referencia** | `Robot.process_frame` será productor futuro; `Robot.ask` no sustituirá recuperación episódica |
| `robot/device/live.py` | **No usado aún** | Referencia para workers, cierre y watchdog en Fase 2 |
| `robot/device/mic.py` | **No usado aún** | Adaptador de audio/ASR en Fase 3 |
| `robot/device/runtime.py` | **No usado aún** | Incorporar con el modo robot; no usar sus tiempos relativos como cronología canónica |
| `robot/device/server.py` | **No usado por episodic** | Mantener para la demo robot; FastAPI continúa como servidor episódico |
| `robot/device/page.html` | **No reutilizado** | Mantener en el modo robot; extraer componentes solo si aparece una necesidad compartida |
| `robot/config.py`, `robot/app.py` | **Pendiente de integración** | Copiar con atribución para preservar comando y demo del robot |
| `testdata/verify_scores.py` | **No usado** | Conservar como regresión robot; no trasladar sus umbrales a episodic |
| Funciones destructivas del helper | **Excluidas** | `fresh_start` y `new_shard` no entran en el producto |

### 3.2 Desde el RAG legado de la raíz

| Componente | Uso por `episodic/` | Tratamiento |
|---|---:|---|
| `domain/`, `pipeline/`, `embeddings/`, `storage/` | Ninguno | Preservar como aplicación separada; no convertir Qdrant en fuente canónica episódica |
| `agent/DomainRAGAgent` | Ninguno | Posponer; más adelante consumirá una herramienta episódica verificable |
| `api/server.py` | Ninguno | Mantener puerto 8000; la API episódica usa puerto 8100 |
| `static/` | Ninguno | Mantener interfaz RAG; no afirmar que la SPA episódica fue extraída de ella |
| FastAPI, Uvicorn y Pydantic | Dependencias compartidas | Reutilización de infraestructura del proyecto, con contratos episódicos independientes |
| Patrón RRF de `storage/qdrant_store.py` | Referencia conceptual | No reutilizable tal cual: fusiona denso/sparse documental, no Nomic/CLIP con evidencia episódica |
| `DomainRetrieverTool.get_tool_schema()` | No usado | Candidato para Fase 5 después de estabilizar `QueryRun` |

---

## 4. Brechas respecto al prompt rector

### 4.1 Bloqueantes de integración

1. La raíz `LabMemoryEpisodic` no tiene `.git`; el único repositorio está dentro de la referencia y Git lo rechaza por propiedad dudosa en el sandbox.
2. `robot/` no existe junto a `episodic/` en la raíz, por lo que no se conserva aún el modo robot dentro del producto.
3. La raíz no tiene `pyproject.toml` ni `uv.lock`; solo `requirements.txt`, aunque el prompt exige flujo `uv` y grupos de dependencias.
4. No existe una prueba de regresión conjunta para RAG legado, robot y aplicación episódica.

### 4.2 Bloqueantes funcionales de Fase 1

1. **Manifiesto y rutas**: el esquema Pydantic no se usa; una ruta se resuelve pero no se comprueba que permanezca bajo `base_dir`; un archivo ausente se sustituye silenciosamente por bytes sintéticos.
2. **HTTP visual inseguro**: `/api/search` acepta `image_query_path`, una ruta elegida por el cliente. Debe aceptar upload limitado o un `evidence_id` autorizado.
3. **Outbox incompleto**: no hay `running`, intentos, `last_error`, reprogramación ni recuperación tras caída. `FAILED` queda terminal y `outbox_retry_limit` no actúa.
4. **Borrado no durable**: se oculta en SQLite y se borra Qdrant sin una operación durable; los estados `pending_delete` no participan.
5. **Modelo/revisión**: ingesta y rebuild codifican `v1`, `v1.5`, 512 y 768 en lugar de registrar `encoder.info`; no hay checksum/revisión resuelta.
6. **Fusión multimodal**: RRF no conserva por resultado todos los ranks/scores de cada modalidad ni distingue de forma completa match directo y contexto expandido.
7. **Identidad ambigua**: `last_observation` elige la primera entidad cuando varias comparten label, en vez de devolver ambigüedad.
8. **Filtros parciales**: búsquedas SQLite usan solo la primera fuente de una lista; el texto opcional de `interval_search` no se aplica.
9. **Corpus Atlas**: usa una sola sesión asociada a C01 para evidencias de otras fuentes; no incluye claramente un episodio de tiempo desconocido; las imágenes son tarjetas sintéticas con texto pero varias observaciones se declaran como detección visual real.
10. **Evaluación visual ausente**: CLIP real carga, pero no hay Recall@k, falsos enlaces, abstenciones ni latencia visual/multimodal aceptadas.
11. **API/UI incompleta**: falta paginación navegable, jobs, upload de imagen, procedencia detallada y smoke de navegador.
12. **Seguridad local parcial**: CORS está abierto, MIME se decide por extensión y no hay límites globales de request/upload.

### 4.3 Deuda técnica que no bloquea el siguiente punto de control

- El almacén lee archivos completos en memoria; es aceptable para fotos pequeñas, no para vídeo futuro.
- Relaciones episodio/evidencia/observación se duplican en JSON y tablas, y los tiempos viven en JSON sin índices temporales SQLite.
- `FilesystemEvidenceStore.resolve_path` usa comparación textual de prefijo; debe usar `Path.relative_to` para evitar colisiones de prefijo.
- El frontend episódico fue reescrito; se conservará, pero no se contará como reutilización de `robot/device/page.html`.
- Los umbrales actuales son experimentales sobre un corpus sintético pequeño; no son universales.

---

## 5. Plan de trabajo actualizado

El orden se corrige para no seguir construyendo UI sobre contratos aún inestables.

### Etapa A — Integración real del repositorio y baseline de tres modos

**Estado**: siguiente etapa obligatoria.

**Trabajo**:

1. Crear o confirmar control de versiones en la raíz y preservar el estado actual antes de mover código.
2. Incorporar desde la referencia, con licencia y atribución, `robot/`, sus tests y `testdata` necesarios; no copiar notebooks, pesos, cachés ni hardware al runtime.
3. Crear `pyproject.toml` con grupos `core`, `models`, `rag`, `robot` y `test`; generar `uv.lock`. Mantener instrucciones Conda como perfil de desarrollo existente.
4. Mantener tres entradas explícitas:
   - `python app.py` — RAG legado;
   - `python -m episodic.app` — Memory Episodic;
   - `python -m robot.app` — robot.
5. Actualizar arquitectura, mapa de reutilización y README con límites de cada modo y sus directorios de datos.
6. Ejecutar baseline separado de los tres modos. Los tests robot que requieran extras deben ejecutarse en el grupo correspondiente, no ocultarse.

**Gate A**:

- `episodic.domain` importa sin extras robot.
- Suite episódica: al menos las 41 pruebas actuales.
- Referencia integrada: `test_runtime.py` conserva sus 4 pruebas; `test_live.py` y certificados tienen resultado explícito con sus extras.
- El RAG legado importa y su arranque no se mezcla con SQLite episódico.
- Existe lock reproducible y atribución de los archivos incorporados.

**No avanzar** si el modo robot deja de ser ejecutable o si la raíz sigue sin una estrategia de versionado recuperable.

### Etapa B — Fuente canónica, manifiesto y outbox recuperable

**Trabajo**:

1. Conectar `ManifestSchema` y validar enums, UTC, referencias cruzadas y errores por campo.
2. Confinar rutas con `relative_to`; rechazar archivos ausentes y separar generación de fixtures de la ingesta de producto.
3. Crear migración v2: jobs/outbox con intentos, error, lease, timestamps y unicidad por target/espacio/modelo/revisión.
4. Usar `Clock` inyectable en ingesta, jobs y consultas auditadas.
5. Registrar representaciones desde `encoder.info`, incluyendo runtime, normalización y revisión resuelta.
6. Implementar recuperación de trabajos `running` abandonados y reintentos acotados.
7. Convertir borrado vectorial y limpieza de medios en operaciones durables, revalidando siempre SQLite.
8. Añadir `GET /api/jobs/{id}` y estado operativo agregado.

**Gate B**:

- Criterios 1, 2, 3, 4, 11 y 12 cubiertos con inyección de fallos.
- Ninguna ruta externa se lee desde importación HTTP.
- Reabrir después de una caída procesa exactamente una vez o de forma idempotente.

### Etapa C — Cierre textual, temporal e identidad

**Trabajo**:

1. Resolver labels duplicados como ambiguos; un label nunca crea identidad.
2. Aplicar listas completas de fuentes y semántica temporal consistente en SQLite y Qdrant.
3. Definir si `interval_search + text` hace intersección semántica o rechaza el texto; probar la decisión.
4. Versionar índices por modelo/revisión y evitar que un cambio mezcle representaciones.
5. Separar `candidate` de `supported` hasta calibrar cada intención con positivos y negativos.
6. Completar pruebas reales de Nomic/Edge para filtro de fuente, resolución única, outbox recuperado y rebuild.

**Gate C**:

- Criterios 5 textual, 6, 7 y 9 aceptados.
- Métricas textuales reproducibles sobre conjunto de prueba separado.

### Etapa D — Recuperación visual y multimodal completa

**Trabajo**:

1. Añadir búsqueda por upload limitado o `evidence_id`; retirar rutas HTTP arbitrarias.
2. Mantener Nomic, CLIP vision y CLIP text como espacios versionados independientes.
3. Conservar ranks/scores originales por carril y fusionar con RRF parametrizado.
4. Distinguir evidencia directa, contexto expandido y procedencia de cada match.
5. Evitar premio por múltiples crops del mismo episodio.
6. Corregir Atlas: sesiones por fuente, caso temporal desconocido, procedencia `scenario_fixture` y particiones desarrollo/calibración/prueba.
7. Añadir fotografías de prueba adecuadas o un corpus visual con licencia; las tarjetas sintéticas solo validarán plumbing.
8. Medir imagen→imagen y texto→imagen con CLIP real.

**Gate D**:

- Criterios 5 visual y 8 aceptados.
- Recall@k por modalidad, falsos enlaces, abstenciones y latencia documentados.

### Etapa E — Resiliencia y ciclo de vida

**Trabajo**:

- Inyectar fallos después de copiar medio, commit SQLite, embedding, upsert, delete y rebuild.
- Recuperar blobs huérfanos y jobs abandonados sin borrar originales externos.
- Verificar acceso SQLite por thread y propiedad exclusiva del shard Edge.
- Probar reinicio offline con modelos ya cacheados.

**Gate E**: criterios 3, 11 y 12 aprobados de extremo a extremo, no solo con doubles.

### Etapa F — API/UI de aceptación

**Trabajo**:

- Completar paginación, jobs, upload visual, MIME/tamaño, CORS local y errores de contrato.
- Mostrar procedencia, intervalo/incertidumbre, evidencia directa/contextual, modelo y revisión.
- Mantener la UI sin reglas de ranking, aceptación o tiempo; solo representa decisiones del servicio.
- Añadir smoke test de navegador del recorrido principal.

**Gate F**: criterios 13 y 14 aceptados; API y CLI equivalentes sobre el mismo bootstrap.

### Etapa G — Candidata de Fase 1

Ejecutar y documentar:

```text
init → import dos veces → index → search texto → search imagen
→ revisar evidencia → anotar → borrar/controlar visibilidad
→ cerrar/reabrir → consultar → rebuild → repetir métricas
```

Separar claramente resultados fake, SQLite local, Qdrant Edge real, modelos reales y navegador. La Fase 1 solo se cierra cuando los catorce criterios tienen evidencia reproducible.

---

## 6. Matriz honesta de aceptación actual

| # | Criterio | Estado 2026-10-04 | Próximo gate |
|---:|---|---|---|
| 1 | Importación idempotente | 🟡 Probada con Atlas; validación de manifiesto incompleta | B |
| 2 | Persistencia y aislamiento | 🟡 SQLite/Edge probados; integración de tres modos pendiente | A/B |
| 3 | Outbox recuperable | 🔴 Sin reintentos/lease/recuperación | B/E |
| 4 | Espacios y revisiones separados | 🟡 Espacios separados; revisión hardcoded | B/C |
| 5 | Texto/imagen con refs y filtros | 🟡 Texto real; visual y algunos filtros pendientes | C/D |
| 6 | Vacíos/débiles no son hechos | 🟡 Tests deterministas; política real limitada | C |
| 7 | Última observación completa | 🟡 Paginada; resolución por label duplicado incorrecta | C |
| 8 | Modalidades distintas no se fusionan como prueba | 🔴 Sin prueba suficiente ni detalle por carril | D |
| 9 | Label no equivale a identidad | 🟡 Dominio separado; búsqueda por label elige la primera | C |
| 10 | Anotaciones versionadas | 🟡 Revisiones existen; falta recorrido completo y modelo/revisión | B/G |
| 11 | Borrado coherente | 🟡 Revalidación canónica existe; operación durable incompleta | B/E |
| 12 | Rebuild seguro | 🟢 Staging/generaciones y preservación ante fallo probados | E/G |
| 13 | Core sin extras robot | 🟢 Importación y tests episódicos independientes | A/G |
| 14 | CLI y UI ejecutables | 🟡 Flujo textual funciona; imagen/seguridad/browser pendientes | F/G |

---

## 7. Punto de control acordado

### PC-0 — Auditoría y realineación

**Estado**: alcanzado con este documento.

Evidencia disponible:

- Suite episódica: `42 passed`, 2 warnings no funcionales.
- Tests core de la referencia: `4 passed`.
- Suite completa de referencia: bloqueada en colección por ausencia de `cryptography` y `cv2` en `LabMemoryEpic01`.
- Imports del RAG legado: correctos; no se ha acreditado un E2E porque depende de Qdrant HTTP y Ollama externos.
- Qdrant Edge 0.7.2 y Nomic reales: validados para recuperación textual, persistencia, filtro temporal y rebuild.
- No hay imports desde `episodic/` hacia el RAG raíz ni hacia `robot/`.

### Próximo incremento autorizado: PC-1 — Integración y reutilización verificable

**Estado**: en curso.

Mientras no se supere Gate A se trabajará **solo en la Etapa A**. No se añadirán agentes, vídeo, ASR ni funciones de producto ajenas a este gate. A petición del usuario, sí se mantiene una superficie de observabilidad en el frontend para que cada incremento pueda validarse desde el navegador contra la API real.

Entregables de PC-1:

1. estrategia Git recuperable para la raíz;
2. `robot/` integrado con licencia/atribución y comando preservado;
3. `pyproject.toml` y `uv.lock` con grupos separados;
4. baseline reproducible de RAG, robot y episodic;
5. arquitectura y mapa de reutilización corregidos;
6. cero dependencias robot al importar `episodic.domain`.
7. estado de integración y outbox observable desde API y navegador, sin presentar código meramente presente como integrado.

#### PC-1.1 — Superficie de verificación desde el frontend

**Estado**: implementado; smoke manual pendiente de confirmación del usuario.

- `GET /api/project-status` diferencia memoria episódica activa, RAG legado aislado y robot solo disponible en referencia.
- `GET /api/indexing/summary` publica los estados `pending`, `indexed`, `failed` y `stale` de la outbox canónica.
- La pestaña **Verificación** ejecuta cinco controles reales: runtime, outbox, línea temporal, intervalo Atlas y acceso a evidencia por ID.
- Si falta el corpus, el frontend muestra `SIN DATOS`/`NO APLICA`; no convierte la ausencia de evidencia en éxito.
- La suite que protege este contrato queda en `42 passed`; la aceptación visual se realiza siguiendo `docs/episodic/milestones.md`.

Este punto de control evita seguir acumulando código aislado y establece una base única sobre la que cerrar después la memoria canónica y la multimodalidad.

---

## 8. Roadmap posterior a Fase 1

| Fase | Incremento | Reutilización prevista |
|---:|---|---|
| 2 | Vídeo/cámara y detector/tracker | `robot/brain/detect.py`, coordinación de `Robot.process_frame`, workers de `live.py` |
| 3 | ASR, OCR y descripción estructurada | `robot/brain/models.py`, `robot/device/mic.py`, liberación de Whisper del helper |
| 4 | Cruce de fuentes e identidades candidatas | Contratos temporales y de evidencia de Fase 1 |
| 5 | Herramientas para agentes | Adaptar `DomainRetrieverTool` al contrato `QueryRun`, sin acceso directo a Qdrant |
| 6 | Perfiles de dispositivo y sincronización | Configuración, runtime y servidor local del robot donde corresponda |

El productor robot futuro invocará el servicio de ingesta y su outbox. No escribirá directamente en SQLite y Qdrant en paralelo.
