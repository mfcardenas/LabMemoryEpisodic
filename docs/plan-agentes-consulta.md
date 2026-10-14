# Plan: de la búsqueda de pasajes a una red de agentes de consulta

Fecha: 2026-10-14 · Rama: `fase-d-audio` · Guía anterior: `docs/plan-mejora-uso-real.md`
(fases A–D hechas; E, F, G pendientes). Este plan sustituye a las fases E y G de
aquella guía y añade la escala y la red de agentes. Este fichero es la guía
versionada; `docs/plan-mejora-uso-real.md` ya remite aquí para E y G. Al cerrar
cada fase, marcarla en §4 con «✅ hecha (fecha)» y anotar el resultado del banco
en `docs/evaluacion.md`.

## 1. Contexto

El usuario probó Consultas y la recuperación le pareció pobre: coincidencia de
palabras y similitud semántica simple, sin componer respuestas. Quiere que el
sistema explote su capacidad de guardar millones de eventos detallados y que
una **red de agentes de consulta** componga respuestas inteligentes con citas.
Decisiones tomadas con el usuario (2026-10-14):

1. **Base de recuperación primero, agente después** (medir antes de razonar).
2. **Incluir memoria jerárquica**: resúmenes por episodio / día / entorno,
   generados por LLM, con procedencia `model_generated`, revisables.
3. **Modelo de razonamiento: `gpt-oss:20b`** en Ollama local (13 GB; RTX 5080
   16 GB). VLM actual `qwen3.5:4b` (3,4 GB): no caben los dos a la vez con
   holgura; Ollama los intercambiará. Aceptable mientras consulta y captura no
   sean simultáneas; ver riesgos.

Restricciones vigentes (memoria del proyecto, no negociables):
- SQLite es la verdad; Qdrant y FTS son proyecciones reconstruibles.
- Vectores en el contenedor `epigraph-qdrant` (puerto 6333), nunca Edge; en ese
  Qdrant hay colecciones de otros proyectos: **solo tocar `episodic_memory*`**.
- Todo proceso largo debe verse en vivo en Vigilancia (`episodic.application.progress.note`).
- Las cargas de prueba las pone el usuario a mano en `D:/LabMemoryEpisodic_data/jobs`;
  `reset`/`start` jamás mueven ficheros.
- Texto generado por modelo: siempre etiquetado con modelo, versión de prompt y
  evidencia; nunca rellena huecos como si fuera observación.
- Robot `sc-on-device-ai-memory`: fuera de alcance.

## 2. Diagnóstico verificado (por qué la consulta es pobre hoy)

Comprobado leyendo código y la BD real (`episodic-data/episodic.db`: 9 episodios,
16 eventos, 53 pasajes, 6 consultas registradas).

**Ningún LLM interviene en la consulta.** Solo hay: Nomic/Qdrant, BM25/FTS5,
cross-encoder y reglas. `gpt-oss` no se usa en ninguna parte del runtime.
El único Ollama es el VLM de ingesta (`adapters/models/ollama_vision.py`).

**La unidad indexada es la observación, no el evento** (`domain/passages.py:17-43`):
- Los `Event` (`domain/processing.py:112-152`: título, resumen, Δt, atributos,
  habla alineada en `attributes["speech"]`, keyframes) **no se indexan nunca**.
- `passage_text()` lee listas `people/tools/entities` que el VLM no produce;
  el VLM produce `actors` y `visible_text` (`ollama_vision.py:17-63`) ⇒ actores y
  texto visible **no son buscables**. El habla alineada tampoco.
- Entran como pasajes tipos ruido: `document_page_status`, `video_interval`,
  `image_frame_metadata`, `mock_observation`.

**Dos buscadores en paralelo con criterios distintos** (`web/app.js:1236-1245`):
el front lanza `/api/passage-search` y `/api/hybrid-search` a la vez. El segundo
(`application/retrieval.py`, umbral `TEXT_THRESHOLD=0.65`) marcó
`insufficient_evidence` las preguntas reales del usuario («¿Cuándo se usó una
tarjeta de crédito?» → 0,66/0,64) aunque el pasaje «tarjeta de crédito azul»
existe. Las preguntas puntúan bajo frente a descripciones; de ahí el parche
`PASSAGE_AGREEMENT_RANK`.

**Vía léxica sin filtros**: `search_passages` (`sqlite/repository.py:1824-1839`)
devuelve el top 60 global; los filtros se aplican después de hidratar ⇒ con
filtro de ambiente/fecha la vía léxica se vacía. `passage_index` tiene
`episode_id`/`observation_id` UNINDEXED (`migrations.py:509-516`) ⇒ borrados y
reemplazos por episodio son scans completos.

**Sin comprensión de la pregunta**: no se interpretan fechas («ayer», «la
semana pasada»), ni intención (cuándo / qué / quién / antes-después), ni se
expande la consulta. No existe vecindad temporal (evento anterior/siguiente).

**Herramientas Fase 8** (`tools/memory.py`, 11 herramientas, contratos
Pydantic `extra="forbid"`, sobre `MemoryToolOutput` en `tools/contracts.py:262`)
son la base correcta para agentes pero: `search_observations` y
`search_documents` cargan **todas** las observaciones y puntúan en Python;
`get_environment_topology`/`compare_entities` cargan todo; ninguna usa
`PassageSearchService`; el grafo de entidades está **vacío** (la captura nunca
crea entidades ni relaciones; `Event.entity_ids` siempre `[]`).

**Sin jerarquía**: no existe resumen/título/narrativa de episodio (título =
nombre de carpeta; `indexable_text` = concatenación de descripciones).
El `DeterministicEpisodeComposer` (fase 5) sigue en el flujo pero sus reglas
leen claves que el VLM no escribe (`scene_change_score` vs
`scene_change_distance`): solo corta por hueco de 5 s.

**Escala**: `IndexMaintenance.status()` recorre todos los episodios y el front
lo llama en **cada búsqueda**; `health` hace `list_episodes(limit=10000)`;
`list_episodes` filtra intervalo en Python tras `LIMIT` (`repository.py:700-720`);
upserts a Qdrant de uno en uno, `ENCODER_BATCH_SIZE=1`; el payload del
episodio lleva listas sin acotar de ids; `events` solo tiene `payload_json`
(sin `episode_id`, `asset_id`, offsets, título en columnas); no hay
`list_events`; hidratación N+1 (`get_observation` ×120 por consulta).

**Modelos disponibles** (`ollama list`): gpt-oss:20b, gpt-oss:latest, qwen3.5:4b,
qwen3.5:35b (23 GB, no cabe), gemma3:12b, qwen3:8b, nomic-embed-text.
Contenedores en marcha: `epigraph-qdrant` (v1.16.2), `epigraph-speech` (:8010).

## 3. Arquitectura objetivo (resumen)

```
Pregunta ─► Comprensión (determinista + LLM opcional): intención, tiempo, filtros, subpreguntas
        ─► Agente orquestador (gpt-oss:20b, tool calling Ollama /api/chat)
              │  bucle: planificar → llamar herramientas → verificar → responder (máx. N pasos)
              ├─ search_moments        (pasajes/eventos: Nomic + BM25 + rerank, con filtros)
              ├─ browse_memory         (jerarquía: entorno → día → episodio → evento)
              ├─ get_event / get_event_neighbors (antes / después, mismo episodio)
              ├─ get_episode_summary / get_day_digest
              ├─ herramientas Fase 8 (entidades, relaciones, evidencia, contradicciones)
              └─ (A3) especialistas: Localizador, Narrador, Verificador, en paralelo
        ─► Respuesta en español con citas [episodio · evento · mm:ss] + abstención explícita
        ─► Auditoría: answer_runs (pasos, herramientas, tokens, latencia) visible en el front
Datos: eventos como unidad indexada · resúmenes jerárquicos como pasajes de primer nivel
```

## 4. Fases

Cada fase termina con tests verdes (`~/.conda/envs/LabMemoryEpic01/python.exe -m pytest -q`,
hoy 231 pasan), README actualizado y un commit. Estimaciones para una persona
con asistencia.

### Fase 0 — Cierre y banco de evaluación (1–2 días) · ✅ hecha (2026-10-14)

Hecho: commit `d08cace`; `data/eval/preguntas.jsonl` (29 preguntas, 4 de
abstención) escrito a partir de las capturas reales; `scripts/evaluate_questions.py`
(en proceso o `--url`); línea base en `docs/evaluacion.md` (momento@5 88 %,
abstención 50 %; fallan texto visible, fecha y distractores, como se preveía);
`scripts/synthesize_corpus.py` (300 eventos de humo en `episodic-data-synth`
+ colección `episodic_memory_synth`, 28 ms por consulta con vectores hash).

Objetivo: confirmar el trabajo pendiente y poder **medir** cada mejora.

1. Commit del trabajo sin confirmar (búsqueda por pasajes + reindexado
   automático; ya con 231 tests verdes). No tocar el submódulo
   `sc-on-device-ai-memory` (está `-dirty`; dejarlo fuera del commit).
2. **Conjunto de evaluación con capturas reales**: `data/eval/preguntas.jsonl`,
   una línea por pregunta: `{id, pregunta, tipo (cuando|que|quien|donde|antes_despues|
   documento|abstencion), episodio_esperado (external_id), ventana_esperada
   [inicio_s, fin_s] | null, evidencia_esperada (texto libre), notas}`.
   20–30 preguntas escritas con el usuario sobre sus capturas en
   `D:/LabMemoryEpisodic_data/_procesados` (env_context_A001…A501); incluir 3–4
   sin respuesta posible (medir abstención).
3. `scripts/evaluate_questions.py`: ejecuta contra el servidor vivo
   (`/api/passage-search` hoy; `/api/ask` desde A2) y escribe
   `data/eval/resultados-<fecha>.json` con: acierto@1/@5 de momento (evento
   correcto si solapa ±5 s con la ventana esperada), acierto de episodio,
   abstenciones correctas/incorrectas, latencia p50/p95, y desde A2 nº de
   pasos, llamadas a herramientas y tokens. Reutilizar `episodic/evaluation.py`
   (Recall@k, MRR ya implementados) y el patrón de `scripts/evaluate_atlas_text.py`.
4. **Línea base** anotada en `docs/evaluacion.md` (tabla por fecha/commit).
5. `scripts/synthesize_corpus.py` (para Fase S): genera N episodios/eventos
   sintéticos realistas (texto variado en español, horas distribuidas en meses,
   M ambientes) directamente en SQLite + outbox, sin vídeo. Parámetro `--events 1000000`.

Aceptación: línea base publicada; evaluación reproducible en un comando.

### Fase R1 — El evento como unidad de recuperación (3–4 días) · ✅ hecha (2026-10-14)

Hecho: migración v10 (`events` con `episode_id`, `asset_id`, orden, offsets,
título, `revision`; tabla `passages` + FTS5 de contenido externo con
disparadores); `domain/passages.py` con `Passage`, `event_passage_text`,
`episode_passages` (evento = pasaje; habla adherida y observación de origen
no se duplican); `search_passages` con filtros antes del límite; hidratación
en lote desde `passages`; `list_events`, `get_events`, `get_event_neighbors`,
`link_events_to_episode`, `delete_events_for_asset`; indexación por lotes;
`INDEX_FORMAT_VERSION=2`; front muestra el evento con miniatura y ya no llama
a `hybrid-search` ni a `index/status` por consulta. Banco: 88 % → 92 %
(`docs/evaluacion.md`). No hecho (pasa a S): colección separada, índices de
payload nuevos.

Objetivo: que lo buscable sea lo que el usuario ve (eventos con título, resumen,
descripción, actores, objetos, texto visible y habla), con filtros reales en
las dos vías.

Cambios:
- **Modelo**: migración v10 en `sqlite/migrations.py`: columnas en `events`
  (`episode_id`, `asset_id`, `sequence_number`, `start_offset_seconds`,
  `end_offset_seconds`, `title`, `revision` default 1) rellenadas desde
  `payload_json` en la propia migración; índices `(episode_id, sequence_number)`,
  `(asset_id, start_offset_seconds)`. Añadir `list_events(episode_id)`,
  `get_event_neighbors(event_id, before=k, after=k)`, `count_events()` en
  `ports/repository.py` y `sqlite/repository.py` (seguir el estilo de
  `list_observations_for_episode`). Fijar `episode_id` del evento en
  `episode_composition.py:_persist_reconstruction` al enlazar la captura.
- **Pasajes de evento**: en `domain/passages.py` añadir `event_passage_text(event)`
  (título · resumen · descripción · escena · acciones · actores · objetos con
  atributos · texto visible · habla alineada · cambios respecto al anterior) y
  un `PassageSource` que admita Observation **o** Event. Pasajes:
  `video_event` → **el Event** (no la observación), `speech_segment` solo si no
  quedó adherido a un evento, `document_text_fragment`, `text_fragment`,
  `episode_summary`/`day_digest` (Fase H). Lista blanca explícita en lugar de
  `NON_PASSAGE_TYPES`. Subir `INDEX_FORMAT_VERSION` a 2 (`index_maintenance.py:40`)
  ⇒ reindexado automático al arrancar.
- **FTS con filtros**: recrear `passage_index` como FTS5 **external content** o
  tabla `passages(id, kind, target_id, episode_id, environment_id, source_id,
  modality, start_ms, end_ms, title, text)` + FTS5 sobre ella con triggers.
  `search_passages(match, limit, filters)` hace JOIN y aplica ambiente/fuente/
  modalidad/intervalo **antes** del `LIMIT`; índices en `episode_id`,
  `(environment_id, start_ms)`.
- **Qdrant**: payload del pasaje añade `event_id`, `kind`, `sequence_number`;
  índices de payload en `observation_id`/`target_id`; upsert por lotes
  (`VectorIndex.upsert` ya acepta lista: agrupar de 64 en 64 en
  `indexing.py:_index_passages`); `ENCODER_BATCH_SIZE` por defecto 8.
- **Hidratación**: `get_observations(ids)`/`get_events(ids)` en lote (una
  consulta `IN`) en lugar de N+1.
- **Front**: `renderPassageSearch` muestra el evento (título, mm:ss, miniatura
  del primer keyframe vía `/api/media/...` existente) y deja de llamar a
  `/api/hybrid-search` y a `/api/index/status` en cada búsqueda (status: al
  cargar la vista y tras reindexar). La sección «Episodios completos» pasa a
  Auditoría o se elimina.
- Corregir `list_episodes` para filtrar intervalo en SQL (`idx_episodes_time`).

Tests: `tests/unit/test_passage_query.py` (texto de evento con actores/texto
visible/habla), `tests/integration/test_passage_search.py` (filtros en vía
léxica; evento devuelto con `event_id` y offsets; tipos ruido excluidos),
migración v10 sobre una BD v9 con eventos reales (`test_phase2_persistence.py`
como patrón). `test_capture_workflow.py:69` espera 5 etapas: no cambia aquí.

Aceptación: acierto@5 de momento en el banco ≥ línea base + mejora clara
(objetivo ≥ 0,8 en preguntas con respuesta); una búsqueda con filtro de
ambiente devuelve candidatos léxicos.

### Fase R2 — Comprensión de la consulta y vecindad temporal (2–3 días) · ✅ hecha (2026-10-14)

Hecho: `application/query_understanding.py` (`parse_query` → `QueryPlan`:
intervalo absoluto con zona horaria `EPIGRAPH_QUERY_TIMEZONE`, intención,
ambientes/fuentes nombrados, texto limpio; 37 casos de prueba);
`PassageSearchService.search(..., neighbors, now)` aplica el plan (el
intervalo explícito manda), con intervalo no descarta por umbral, y devuelve
vecinos (`get_event_neighbors`) para antes/después o si se piden;
`plan` y `neighbors` en la API y en el front («Entendí: …», tira
antes/este momento/después). Banco: 92 % → 96 %. **No hecho**: la
reformulación por LLM (`QUERY_REWRITE=llm`) necesita el `TextComposer` de la
Fase H; se hace allí.

Objetivo: que «¿qué pasó después de aplicar el lubricante el lunes por la
tarde?» se convierta en filtros y subconsultas correctas, sin LLM cuando no
hace falta.

- `application/query_understanding.py`: `parse_query(text, now, tz) -> QueryPlan`
  determinista: expresiones temporales en español (hoy, ayer, la semana
  pasada, el 12 de octubre, por la tarde, entre X e Y) → `interval`;
  intención (`cuando|que|quien|donde|antes_despues|cuanto|documento`); frases
  entrecomilladas; términos de ambiente/fuente si coinciden con nombres
  conocidos (`list_environments`, `list_sources`). Sin dependencias nuevas
  (regex + `datetime`); si se quisiera `dateparser`, fijar versión en
  `pyproject.toml`.
- Reformulación opcional por LLM (`QUERY_REWRITE=none|llm`): gpt-oss genera
  2–3 paráfrasis declarativas («persona usa tarjeta de crédito» en vez de
  «¿cuándo se usó…?») y sinónimos; se buscan todas y se fusionan con RRF.
  Esto ataca directamente el problema de que las preguntas puntúan bajo.
- Vecindad: `get_event_neighbors` expuesta en `PassageSearchService` y como
  herramienta; para intención `antes_despues` la respuesta incluye los k
  eventos adyacentes del mismo episodio.
- `PassageSearchRun` registra el `QueryPlan` aplicado (se muestra en el front:
  «Entendí: intervalo…, ambiente…»).

Tests: unitarios de `parse_query` (tabla de 30 expresiones), integración de
vecindad. Aceptación: banco con preguntas temporales y antes/después ≥ 0,8.

### Fase H — Memoria jerárquica (4–5 días) · ✅ hecha (2026-10-14)

Hecho: puerto `ports/text_composer.py`; `adapters/models/ollama_text.py`
(`/api/chat`, JSON por esquema, `num_ctx`, `Reasoning:` para gpt-oss) y
`fake_text.py`; `domain/summaries.py` + migración v11 (`summaries` con
revisiones); `application/summaries.py` (`SummaryService`: entradas por
nivel, verificación determinista de citas, trabajos `summarize` y `digest`);
etapa «Resumen» en la captura (6 etapas; el fallo del modelo no tumba la
captura) y encolado del digesto diario/entorno; resumen vigente indexado
como pasaje `episode_summary`; `GET /api/summaries`, `POST
/api/summaries/regenerate`; ficha del episodio con resumen, citas que
abren el vídeo, momentos clave, «no consta» y «Regenerar». **Ojo**: la
etiqueta `gpt-oss:20b` instalada falla en Ollama 0.35 (GGUF antiguo); se usa
`gpt-oss:latest` (misma familia). Con datos reales: 9 episodios resumidos
(11–58 s, 0 citas inválidas), 9 días, 7 entornos. Los resúmenes **no entran**
en la búsqueda por momentos salvo `include_summaries` (bajaban el banco a
92 %: la prosa atrae al reordenador); son para `browse_memory` (A1).
**No hecho**: reformulación de consultas por LLM (`QUERY_REWRITE`), queda
para A2 donde el agente ya reformula.

Objetivo: resúmenes revisables por episodio, día y entorno, indexados como
pasajes de primer nivel, para que el agente navegue de arriba abajo sin leer
millones de eventos.

- **Puerto** `ports/text_composer.py`: `TextComposer.compose(task, context) ->
  Composition{text, structured, model_id, prompt_version, input_hash}`.
  Adaptador `adapters/models/ollama_text.py` (`OllamaTextComposer`, `/api/chat`,
  `format` JSON schema, `options.temperature` 0.2, `num_ctx` 16384,
  reutilizar el patrón de sesión inyectable de `ollama_vision.py` y sus tests
  con `FakeSession`) y `adapters/models/fake_text.py` (`FakeTextComposer`
  determinista, registra `.calls`). Config: `EPIGRAPH_TEXT_BACKEND=ollama|fake|none`,
  `EPIGRAPH_TEXT_MODEL=gpt-oss:20b`, `EPIGRAPH_TEXT_NUM_CTX`,
  `EPIGRAPH_TEXT_REASONING=low|medium|high` (gpt-oss lo lee del system prompt
  «Reasoning: …»). Factoría `build_text_composer` en `bootstrap.py` como
  `build_speech_transcriber`; `conftest.py` añade `EPIGRAPH_TEXT_BACKEND=fake`.
- **Tabla** `summaries` (migración v11): `id, scope (episode|day|environment),
  scope_key (episode_id | env+YYYY-MM-DD | env), revision, title, summary,
  narrative, highlights_json (eventos citados con ids y mm:ss), open_questions_json,
  model_id, prompt_version, input_hash, created_at, is_current`. Regenerar crea
  `revision+1` y marca la anterior como no vigente; nunca se borra.
- **Etapa «resumen»** en `CaptureProcessingService._run` (`captures.py:39-45`,
  `CAPTURE_STAGES` pasa a 6 ⇒ actualizar `test_capture_workflow.py:69`):
  entrada = eventos ordenados con título/resumen/habla + fragmentos de
  documento más afines (búsqueda Nomic restringida a los documentos de la
  captura); salida = título legible del episodio, resumen, narrativa cronológica
  con citas `[ev N · mm:ss]`, hechos clave. Si el backend es `none`, la etapa se
  omite con nota en Vigilancia. Progreso con `progress.note` por evento.
- **Digestos** día/entorno: trabajo `digest` en `JobRunner` (handler nuevo en
  `bootstrap._create_job_runner`), encolado al terminar una captura (día del
  entorno afectado) y disponible manualmente (`POST /api/summaries/rebuild`).
  Entrada = resúmenes de episodio del día; el de entorno = últimos N días.
- **Indexación**: cada `summary` vigente es un pasaje de tipo
  `episode_summary|day_digest|environment_digest` (texto = título + resumen +
  hechos) con `target_type="summary"` en Qdrant; filtro `kind` en búsqueda.
- **Front**: ficha de episodio muestra título/resumen/narrativa con etiqueta
  «Generado por gpt-oss:20b · rev N» y botón «Regenerar»; selector de
  revisiones. `GET /api/summaries?scope=&key=`, `POST /api/summaries/regenerate`.
- Ajustar el título del evento en Vigilancia/línea temporal al título generado
  (hoy el título del episodio es el nombre de carpeta).

Tests: unidad del composer con `FakeSession`; integración del flujo con
`FakeTextComposer` (revisión 1, regenerar → 2, pasaje indexado, cita a evento
válida). Aceptación: episodio legible sin abrir el vídeo (criterio 4 del plan
anterior); `browse` por día devuelve el digesto.

### Fase A1 — Capa de herramientas para agentes (2–3 días)

Objetivo: un catálogo compacto, paginado y barato en tokens, sin scans.

- Añadir a `MemoryToolbox` (`tools/memory.py`, contratos en `tools/contracts.py`):
  `search_moments` (envuelve `PassageSearchService` + `QueryPlan`; devuelve
  `MomentRecord{event_id, episode_id, external_id, title, snippet ≤ 300 car.,
  start_offset, end_offset, start_time, kind, scores}`), `get_event`,
  `get_event_neighbors`, `get_episode_summary`, `browse_memory(scope, key,
  page)` (entornos → días con nº de episodios → episodios con título → eventos),
  `list_environments`, `get_document_passages(episode_id, query)`.
- Reescribir `search_observations`/`search_documents` sobre `search_moments`
  (eliminar `list_observations()` completo); paginar
  `get_environment_topology` y `compare_entities`; límite de tamaño de salida
  (`max_chars`) en todas.
- `MemoryToolOutput` gana `moments: list[MomentRecord]` y `summaries`.
  `describe()` ya expone `input_schema`: se usará tal cual para Ollama `tools`.
- El front de Herramientas pasa a generarse desde `GET /api/tools` (quitar
  `TOOL_DEFS` fijo en `app.js:1048-1190`).
- Seguridad: siguen siendo `read_only=True`; validación `extra="forbid"`.

Tests: `test_phase8_memory_tools.py` ampliado; prueba de que ninguna
herramienta llama a `list_observations()` sin límite (contar consultas con un
repositorio espía). Aceptación: cada herramienta < 200 ms con 50 k eventos
sintéticos (Fase 0.5).

### Fase A2 — Agente orquestador y respuesta con citas (4–6 días)

Objetivo: `POST /api/ask` devuelve una respuesta en español con citas
verificables y abstención honesta; todo auditado y visible.

- **Puerto** `ports/reasoning.py`: `ReasoningModel.chat(messages, tools,
  options) -> Turn{content, tool_calls[], usage}`; adaptador
  `adapters/models/ollama_chat.py` (`/api/chat`, `tools=[{type:function,
  function:{name, description, parameters}}]`, `stream=False`, `think` según
  config; gpt-oss devuelve `message.tool_calls[].function.{name, arguments}`)
  y `FakeReasoningModel` (guion de turnos para tests).
- `application/agents/orchestrator.py`: bucle `plan → tool → observe → …`
  con presupuesto (`EPIGRAPH_AGENT_MAX_STEPS=8`, `MAX_TOOL_CALLS=12`,
  `MAX_SECONDS=90`); system prompt en español con reglas: usar
  `browse_memory`/`search_moments` antes de afirmar; **cada afirmación con
  cita** `[ep:<external_id> ev:<n> mm:ss]`; si no hay evidencia suficiente,
  decir «No consta en la memoria» y qué se buscó; nunca inventar horas.
  Reutilizar `QueryPlan` (R2) como primer paso determinista (filtros
  precalculados que el agente puede relajar).
- **Verificación determinista de citas**: antes de devolver, resolver cada
  cita contra SQLite (`get_event`); citas inválidas se eliminan y la respuesta
  se marca `verified=false` para esa frase. Respuesta estructurada:
  `{answer_md, citations[{event_id, episode_id, external_id, offset_s, text}],
  confidence (alta|media|baja|sin_evidencia), steps[], usage, took_ms}`.
- **Auditoría**: tabla `answer_runs` (migración v12): pregunta, plan, pasos
  (herramienta, argumentos, nº resultados, ms), modelo, tokens, respuesta,
  citas, veredicto. `GET /api/answers/{id}`.
- **HTTP**: `POST /api/ask` (síncrono) y `GET /api/ask/stream` (SSE con cada
  paso, reutilizando el mecanismo de `activity_log`/progreso para que
  Vigilancia también lo muestre).
- **Front**: pestaña **Preguntar** en `index.html` junto a Consultas: caja de
  pregunta, respuesta en Markdown con citas clicables (abren el episodio en
  el segundo: reutilizar `openEpisode`/`applyEpisodeFocus` de `app.js:1191`),
  panel plegable «Cómo se ha respondido» (pasos y herramientas), chip de
  confianza. Historial de la sesión (localStorage).
- **VRAM**: `EPIGRAPH_TEXT_KEEP_ALIVE` y `OLLAMA_KEEP_ALIVE`; documentar que
  con VLM + gpt-oss Ollama descarga uno u otro (latencia de ~10 s al alternar).
  Opción barata: `EPIGRAPH_AGENT_MODEL` distinto de `EPIGRAPH_TEXT_MODEL`.

Tests: orquestador con `FakeReasoningModel` (guion: busca → vecinos →
responde con cita válida; guion con cita inválida → se elimina; guion sin
evidencia → abstención); contrato HTTP; prueba de presupuesto (se corta en
MAX_STEPS). Evaluación con el banco: acierto de momento de la **respuesta**
(cita principal), abstenciones, latencia p95, tokens.
Aceptación: ≥ 0,8 acierto@1 en preguntas con respuesta y 0 respuestas sin
cita; abstención correcta en las preguntas trampa.

### Fase A3 — Red de agentes (3–5 días, tras medir A2)

Objetivo: solo si A2 muestra límites (preguntas compuestas, varios episodios,
comparaciones). Misma capa de herramientas y mismo puerto de razonamiento.

- `application/agents/roles.py`: **Planificador** (descompone en subpreguntas
  con filtros; gpt-oss, `reasoning=high`), **Localizadores** (uno por
  subpregunta, en paralelo con `ThreadPoolExecutor`; `reasoning=low`; devuelven
  momentos con citas, nunca prosa), **Narrador** (compone la respuesta final
  solo a partir de los momentos entregados), **Verificador** (determinista +
  LLM `low`: comprueba que cada frase está sostenida por una cita; si no,
  pide otra ronda o marca baja confianza). Presupuesto global compartido.
- Memoria de trabajo compartida (`Scratchpad`: hechos con cita, preguntas
  abiertas) serializada en `answer_runs.steps`.
- Modo por pregunta: `simple` (A2) o `compuesta` (A3), decidido por el
  Planificador; el front muestra el árbol de subpreguntas.
- Evaluar A2 vs A3 en el banco (añadir 10 preguntas compuestas: «¿en qué
  capturas aparece la tarjeta y qué se hizo después en cada una?»).

Aceptación: mejora medible en preguntas compuestas sin empeorar las simples
ni superar 2× la latencia.

### Fase S — Escala a millones de eventos (3–4 días, intercalable)

Objetivo: p95 de `search_moments` < 1,5 s y de `/api/ask` < 30 s con 1 M de
eventos sintéticos (Fase 0.5) en el portátil.

- SQLite: columnas e índices de R1; `count_events`/`count_passages` cacheados
  (no por consulta); `health` usa `SELECT count(*)`; paginar `list_episodes`
  por cursor; `IndexMaintenance.status()` en SQL (`representations` LEFT JOIN
  episodios) y no por búsqueda; `activity_log` ya tiene poda.
- Qdrant: colección separada para pasajes/eventos (`episodic_memory_passages`,
  generación con alias como ahora, `adapters/qdrant/index.py:58-92`) y otra
  para episodios/evidencia; `on_disk_payload=True`, vectores con cuantización
  escalar `int8` (`quantization_config`) y HNSW `m=16, ef_construct=128`;
  índice de payload `environment_ids` como **tenant** (`is_tenant=True`) y
  `timeline_start_ms` como `is_principal`; eliminar listas sin acotar del
  payload de episodio.
- Ingesta: outbox por lotes (`OUTBOX_BATCH_SIZE` ya existe; usarlo también en
  `_index_passages`), `embed_documents` por lotes de 32 con
  `ENCODER_THREADS` ajustado; reindexado con progreso por lotes en Vigilancia.
- Reranker: solo sobre top 20 (ya) y con `rerank_max_chars` 600; medir CPU.
- Jerarquía (Fase H) como poda: el agente filtra por entorno/día antes de
  buscar momentos; `browse_memory` paginado por cursor.
- Prueba de carga automatizada: `scripts/load_test_search.py` (50 consultas
  del banco contra el corpus sintético, informa p50/p95) y resultados en
  `docs/evaluacion.md`.

### Fase F' — Front mínimo necesario (incluido en R1/H/A2)

No se abre una fase de front separada: cada fase lleva su parte. Pendiente
de la antigua Fase F que **sí** conviene hacer al cerrar A2: mover
`hybrid-search`, hashes, microepisodios y reglas a una pestaña **Auditoría**;
eliminar `compatibility_mode` y `buildFallbackTimeline` de `app.js`.

## 5. Orden y calendario orientativo

| Semana | Fases | Entregable |
|---|---|---|
| 1 | 0 → R1 | banco + línea base; eventos buscables con filtros; commit |
| 2 | R2 → H (inicio) | comprensión temporal, vecinos; composer y resumen de episodio |
| 3 | H (digestos) → A1 | jerarquía navegable; catálogo de herramientas paginado |
| 4 | A2 | `/api/ask` + pestaña Preguntar + auditoría; evaluación |
| 5 | S (+ F') | 1 M eventos sintéticos, p95 medidos; Auditoría en el front |
| 6 | A3 | red de agentes solo si A2 lo justifica |

Fuera de este plan (siguen pendientes): procesador DOCX (Fase D), CLIP sobre
fotogramas clave (búsqueda por imagen), grabación continua, robot.

## 6. Riesgos y decisiones

- **VRAM**: gpt-oss:20b (13 GB) + qwen3.5:4b (3,4 GB) > 16 GB ⇒ Ollama alterna
  modelos; capturas largas y preguntas simultáneas se ralentizan. Mitigación:
  cola única (el `JobRunner` ya es monohilo) y `keep_alive` corto del VLM;
  alternativa `qwen3:8b` para Localizadores (A3).
- **Contexto de Ollama**: por defecto 4096 tokens; el agente necesita
  `num_ctx` 16 k–32 k ⇒ más VRAM (KV cache). Medir en A2.
- **Alucinación**: se controla con verificación determinista de citas y con
  prohibir prosa sin cita; el Narrador solo ve momentos recuperados.
- **Reindexados**: R1 y H cambian el formato ⇒ `INDEX_FORMAT_VERSION` y el
  reindexado automático ya existente (`index_maintenance.py`) lo cubren; con
  1 M eventos un reindexado completo tarda horas ⇒ en Fase S hacerlo por
  generación con progreso (ya) y por lotes.
- **Eventos antiguos**: `EventDerivationService` solo hace upsert; al cambiar
  `model_version` quedan eventos huérfanos de versiones previas. R1 debe
  borrar los de otras versiones del mismo asset al derivar.
- **Grafo de entidades vacío**: no se aborda aquí; cuando H funcione, un paso
  posterior puede extraer entidades (actores/objetos recurrentes) desde los
  resúmenes con procedencia `model_generated`.

## 7. Verificación global

1. `~/.conda/envs/LabMemoryEpic01/python.exe -m pytest -q` verde en cada fase.
2. `start.bat` → Vigilancia muestra las nuevas etapas (resumen, digest,
   reindexado) en vivo; `reset.bat --yes` sigue sin tocar la carpeta vigilada.
3. `scripts/evaluate_questions.py` tras cada fase; tabla en `docs/evaluacion.md`
   (fecha, commit, acierto@1/@5, abstención, p50/p95, tokens).
4. Criterio final (del plan anterior, criterio 5 ampliado): preguntar en
   español «¿qué hizo después de aplicar el lubricante?» y recibir respuesta
   con cita que abre el vídeo en el segundo correcto; con 1 M eventos
   sintéticos, p95 < 1,5 s en momentos y < 30 s en respuesta.

## 8. Notas para el modelo que continúe (ahorro de tiempo y tokens)

- Entorno: Conda `LabMemoryEpic01` (`~/.conda/envs/LabMemoryEpic01/python.exe`),
  Python 3.13, Windows; tests `-p no:cacheprovider` evita ruido. `.env` local:
  `EPIGRAPH_VECTOR_BACKEND=http`, `EPIGRAPH_WATCH_CONFIG=watch.toml`.
  Datos: `episodic-data/` (SQLite, media, index) y `D:/LabMemoryEpisodic_data/jobs`.
- Convenciones: código y comentarios en español, nombres de tests en inglés
  descriptivo; arquitectura puertos/adaptadores (`ports/`, `adapters/`,
  `application/`, `domain/`, `tools/`, `interfaces/{cli,http,web}`); fakes en
  `adapters/models/fake*.py`, `adapters/speech/fake.py`; selección por
  `EPIGRAPH_<X>_BACKEND` validada en `config.__post_init__`; factorías en
  `bootstrap.py`; `tests/conftest.py` fija backends fake con `setdefault`.
- Patrón de integración: `EpisodicConfig(project_root=tmp_path, data_dir="runtime",
  vision_backend="fake")` + `create_app_container(config, use_fake_encoders=True,
  use_memory_qdrant=True)` + `TestClient(create_api_app(container=container))`;
  medios reales en `data_samples/phase4_input/`.
- Migraciones: lista ordenada en `migrations.py:519-529`; cada una en
  `BEGIN IMMEDIATE`; v9 está definida antes que v8 en el fichero (no importa).
- Ficheros con LF (`cli/commands.py`, `test_http_front_contract.py`,
  `test_phase2_persistence.py`): git avisa de CRLF; ignorar.
- Ollama: `http://127.0.0.1:11434/api/chat`; herramientas vía campo `tools`;
  gpt-oss acepta `think` y nivel de razonamiento en el system prompt.
- Qdrant dashboard `http://localhost:6333/dashboard`; colecciones ajenas:
  `entorno_de_laura`, `dominio_*`, `episodic_ambient_01` — no tocar.
- No gastar tokens re-explorando: el diagnóstico de §2 ya cita las líneas
  exactas; empezar cada fase leyendo solo los ficheros nombrados en ella.
