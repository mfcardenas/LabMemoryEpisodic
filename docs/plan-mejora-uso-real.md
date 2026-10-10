# Plan de mejora — de la consola técnica a una memoria episódica utilizable

Fecha: 2026-10-10. Sustituye a PC‑EG1 como guía de trabajo. La integración de
`sc-on-device-ai-memory` queda fuera de alcance hasta nuevo aviso.

## 1. Qué se espera de la aplicación

1. El usuario sube **una captura**: uno o varios archivos tomados juntos
   (vídeo y, si existen, audio, notas o documentos).
2. Cada captura produce **un episodio**. El episodio es la unidad mínima de
   memoria: lo que ocurrió en ese entorno durante ese intervalo.
3. Dentro del episodio, el vídeo se divide en **eventos con su Δt**
   (`inicio`–`fin` relativos al vídeo y absolutos cuando se conoce la hora
   de captura).
4. Cada evento guarda en SQLite una **descripción muy detallada**: qué se
   ve, quién actúa, sobre qué objetos, qué cambia respecto al evento anterior,
   qué texto es visible y qué se dice en el audio durante ese intervalo.
5. El audio y los textos **complementan** los eventos: la transcripción se
   alinea por tiempo y los documentos aportan contexto citable.
6. Después se puede **consultar**: "¿cuándo se aflojó el tornillo?",
   "¿qué herramienta usó?", "¿qué dijo sobre la pieza agarrotada?". La
   respuesta vuelve al evento, al segundo del vídeo y a la evidencia.

## 2. Diagnóstico: por qué hoy no se entiende

| Síntoma en el front | Causa en el código |
|---|---|
| El vídeo produce "Intervalo de vídeo disponible entre X y Y segundos". | `VideoProcessor` (`episodic/processors/video.py`) no decodifica el vídeo: solo lee la duración del contenedor y emite una observación por ventana fija. |
| No aparecen eventos. | `Event` solo se crea en el fixture `examples/pink_pipette.py:267`. El flujo real no tiene ningún productor de eventos. |
| El audio aporta "Segmento de audio de 15 s, RMS…". | `AudioProcessor` solo calcula RMS sobre WAV; `transcription` siempre es `None`. MP3 no da métricas. |
| La imagen dice "Imagen 1080x1920 en modo RGB". | `ImageProcessor` solo lee metadatos con Pillow. |
| Vídeo, audio y documento subidos a la vez no forman un episodio coherente. | La subida es de un archivo por petición. `DeterministicEpisodeComposer.compose(environment_id)` recompone **todas** las observaciones del ambiente cada vez, agrupando por huecos temporales; sin `captured_at` común, cada archivo cae en un episodio distinto y los episodios antiguos pueden cambiar al subir uno nuevo. |
| Mucha jerga técnica (ventanas, microepisodios, hashes, reglas). | El front se diseñó para auditar el pipeline, no para usar la memoria. |
| La subida "se cuelga" en archivos grandes. | `POST /api/workflow/ingest-and-process` es síncrono y termina con un `rebuild_index` completo. |

Lo que **sí** está bien y se conserva: SQLite como verdad, almacén por
SHA‑256, Qdrant Edge reconstruible, Nomic + CLIP, veredictos, tiempos UTC,
revisiones inmutables y las 11 herramientas de consulta.

### Capacidades de la máquina (verificadas el 2026-10-10)

- GPU NVIDIA RTX 5080 Laptop, 16 GB.
- Ollama operativo con modelos de visión `qwen3.5:4b`, `gemma3:12b`,
  `qwen3.5:35b`, `gemma3:latest`, y de texto `gpt-oss:20b`.
- **`gpt-oss` no tiene visión**: no puede mirar fotogramas. Se usará para
  fusionar y redactar; la visión la aporta un modelo VLM.
- No hay ffmpeg, OpenCV, PyAV ni Whisper instalados en `LabMemoryEpic01`.
- Material de prueba ideal ya presente en `data_samples/phase4_input/`:
  vídeo vertical 1080×1920 del mecánico, audio de 15 s de conversación y el
  manual de desmontaje (PDF y DOCX).

## 3. Modelo conceptual nuevo

```text
Captura (subida conjunta)                  → CaptureSession existente
 └─ Episodio  (1 por captura)              → resumen + narrativa + contexto
     ├─ Activos: vídeo, audio, documentos  → originales por SHA-256
     ├─ Eventos (ordenados, con Δt)        → descripción detallada fusionada
     │    ├─ Fotogramas clave              → evidencia derivada (miniaturas)
     │    ├─ Observaciones visuales        → salida cruda del VLM (JSON)
     │    ├─ Segmentos de transcripción    → salida cruda de Whisper
     │    └─ Citas documentales            → fragmentos con página
     └─ Hipótesis                          → separadas, como hoy
```

Decisiones:

- **Episodio = captura.** Deja de recomponerse a nivel de ambiente. Se
  puede fusionar o dividir explícitamente más adelante, nunca como efecto
  secundario de otra subida.
- **El evento es el centro de la consulta.** Una observación es la salida
  cruda de un procesador para una modalidad; un evento es lo que pasó en un
  intervalo, fusionando modalidades.
- **`MicroEpisode` sale de la experiencia de usuario.** Se mantiene interno
  mientras haya tests que dependan de él y se retira cuando los eventos lo
  sustituyan.
- **La narrativa ya no está prohibida, pero sí etiquetada.** Toda descripción
  generada lleva `provenance_kind=model_generated`, modelo, versión,
  hash del prompt y evidencias de soporte. Regenerar con otro modelo crea una
  nueva revisión, sin borrar la anterior. El original y la salida cruda del
  VLM se conservan siempre.

### Contrato de un evento en BD (ampliación de `events`)

```json
{
  "id": "…",
  "episode_id": "…",
  "asset_id": "…",
  "sequence": 7,
  "start_offset_s": 41.0,
  "end_offset_s": 53.5,
  "start_time": "2026-10-10T09:14:41Z",
  "end_time": "2026-10-10T09:14:53Z",
  "title": "Aplica lubricante penetrante sobre la tuerca",
  "summary": "Una línea para listados y resultados de búsqueda.",
  "detailed_description": "Varios párrafos: escena, acciones en orden, manos/herramientas, estado de la pieza, cambios respecto al evento anterior, incertidumbres.",
  "actions": ["rociar", "esperar"],
  "actors": [{"label": "mecánico", "description": "guantes negros, mono azul"}],
  "objects": [{"label": "spray penetrante", "attributes": ["lata roja"]}],
  "visible_text": ["WD-40"],
  "speech": [{"start_offset_s": 44.2, "end_offset_s": 47.9, "text": "…"}],
  "document_refs": [{"asset_id": "…", "page": 3, "quote": "…"}],
  "keyframe_evidence_ids": ["…", "…"],
  "uncertainty": "No se distingue si la tuerca es M10 o M12.",
  "generation": {
    "vision_model": "qwen3.5:4b",
    "fusion_model": "gpt-oss:20b",
    "prompt_version": "event-v1",
    "revision": 1
  }
}
```

Los campos estructurados permiten filtrar ("eventos con *llave dinamométrica*")
y el texto detallado alimenta la búsqueda semántica.

## 4. Pipeline objetivo

```text
1. Subida conjunta      → assets + capture_session + job asíncrono
2. Hora de captura      → formulario > metadatos MP4 (creation_time) > desconocida
3. Decodificación       → PyAV (incluye ffmpeg en la wheel; no requiere ffmpeg del sistema)
4. Muestreo             → 1 fps (vídeos < 5 min ⇒ ≤ 300 fotogramas)
5. Segmentación         → cambio de escena (diferencia de histograma/SSIM)
                          + duración mínima 2 s y máxima 15 s por evento
6. Fotogramas clave     → 2–4 por evento, guardados como evidencia derivada
7. Descripción visual   → VLM Ollama, salida JSON con esquema (`format`)
                          con el evento anterior como contexto
8. Audio                → pista del vídeo + audios sueltos → faster-whisper
                          (es, timestamps por palabra) → segmentos
9. Documentos           → procesadores actuales (PDF/TXT/MD) + DOCX nuevo
10. Fusión por evento   → gpt-oss: visual + habla en [t0, t1] + citas
                          relevantes ⇒ descripción detallada final
11. Resumen de episodio → gpt-oss sobre la secuencia de eventos
12. Indexación          → Nomic sobre eventos y transcripción; CLIP sobre
                          fotogramas clave; outbox incremental, sin rebuild total
```

Orden de magnitud a validar en la Fase 2: para un vídeo de 3 min, unos 20–40
eventos. Con `qwen3.5:4b` en la 5080 se estiman unos segundos por evento, es
decir, pocos minutos por captura. Por eso el proceso pasa a ser asíncrono y
con progreso visible.

## 5. Fases

Cada fase termina con algo que se puede **usar** desde el navegador.

### Fase A — Saneamiento (1–2 días) · ✅ hecha (2026-10-10)

- Sacar del repositorio `episodic-data/` (BD e índice versionados por error)
  y revisar `.gitignore`.
- Mover el RAG legado de la raíz (`app.py`, `agent/`, `api/`, `pipeline/`,
  `storage/`, `domain/`, `embeddings/`, `static/`, `example.py`) a `legacy/`
  o retirarlo. `TextChunker` se usa desde `episodic/application/text_fragments.py`:
  trasladarlo antes.
- Corregir los bugs del front: botón de reconstrucción bloqueado por
  `event.currentTarget` tras `await`; episodio fuera de la primera página
  que se sustituye por otro; cargas dobles y carreras al navegar.
- Seguridad: retirar `image_query_path` de la API (aceptar imagen subida o
  ID de evidencia), confinar las rutas de manifiestos HTTP y rechazar
  referencias inexistentes en lugar de crear blobs sintéticos.
- Reescribir el README alrededor del uso; archivar `milestones.md` y PC‑EG1.

Salida: suite verde, repo limpio, README de cinco minutos.

### Fase B — La captura como episodio (2–3 días) · ✅ hecha (2026-10-10)

Implementado: tabla `captures` y `jobs` (migración v6), `CaptureService`,
`CaptureProcessingService`, `JobRunner` con conexión SQLite propia y
reanudación al arrancar, `DeterministicEpisodeComposer.compose_capture`,
`LifecycleService.index_episodes`, `POST/GET /api/captures`,
`GET /api/jobs/{id}`, `epigraph capture` y el front de subida múltiple.
Pendiente menor: límite de tamaño de subida y limpieza de activos si falla
el registro de una captura a mitad.

- Subida múltiple: un formulario, varios archivos, una `CaptureSession`.
- Episodio creado por captura. Retirar la recomposición global por ambiente
  del flujo normal; conservarla como comando explícito.
- Hora de captura desde metadatos MP4 cuando el usuario no la indique.
- Jobs asíncronos con estados por etapa: `GET /api/jobs/{id}` (previsto
  desde el prompt original y nunca implementado). Worker local en hilo, cola
  durable en SQLite.
- Indexación incremental vía outbox en lugar de `rebuild_index` en cada
  subida.

Salida: subir vídeo + audio + PDF juntos crea **un** episodio con los tres
activos, y la subida muestra el progreso sin bloquear la página.

### Fase C — Del vídeo a eventos (4–6 días)

- Dependencia `av` (PyAV) en un extra `[video]`.
- `VideoDecoder` con timestamps reales del decoder (PTS) y muestreo a 1 fps.
- `SceneSegmenter` determinista y configurable, con tests sobre vídeos
  sintéticos.
- Fotogramas clave persistidos como evidencia derivada (JPEG) y enlazados.
- Puerto `VisionDescriber` + adaptador `OllamaVisionDescriber` con
  esquema JSON, reintentos y registro del prompt.
- Productor de `Event` y migración de la tabla `events` con los campos de §3.
- Banco de prueba: comparar `qwen3.5:4b` y `gemma3:12b` sobre el vídeo del
  mecánico y elegir un modelo por defecto con criterio documentado.

Salida: el vídeo del mecánico produce una lista de eventos con Δt,
miniaturas y descripción visual detallada en BD.

### Fase D — Audio y documentos (2–4 días)

- Extra `[audio]` con `faster-whisper`; modelo `large-v3-turbo` en CUDA,
  idioma `es` configurable.
- **Riesgo a verificar el primer día**: compatibilidad de CTranslate2 y
  cuDNN con Blackwell (RTX 50xx). Alternativa: Whisper en CPU con modelo
  `small`/`medium`, que para audios de pocos minutos es viable.
- Extracción de la pista de audio del vídeo con PyAV.
- Alineación de los segmentos de habla con los eventos por solapamiento
  temporal; audio suelto alineado mediante la hora de captura común.
- Procesador DOCX (`python-docx`); hoy el front no admite `.docx`.

Salida: cada evento muestra lo que se dijo durante su intervalo.

### Fase E — Fusión y descripción rica (2–3 días)

- Puerto `TextComposer` + adaptador `OllamaTextComposer` (`gpt-oss:20b`).
- Por evento: descripción visual + habla + citas documentales relevantes
  (búsqueda Nomic restringida a los documentos de la captura) ⇒
  `detailed_description` y campos estructurados.
- Por episodio: título, resumen y narrativa a partir de los eventos.
- Revisiones: regenerar crea `revision + 1`; la UI permite comparar.
- Tests con un `FakeComposer` determinista; evaluación manual con el modelo
  real.

Salida: un episodio legible por una persona sin abrir el vídeo.

### Fase F — Front orientado al uso (4–5 días)

- **Ficha de episodio**: reproductor de vídeo arriba; debajo, una tira
  temporal de eventos (clic ⇒ salta al segundo); a la derecha, lista de
  eventos con título, Δt, miniatura y descripción expandible; transcripción
  sincronizada; documentos adjuntos; resumen del episodio arriba.
- **Subida**: arrastrar varios archivos, ver el progreso por etapa y abrir
  el episodio al terminar.
- **Búsqueda**: resultados a nivel de evento con miniatura, fragmento,
  episodio y "ver en el vídeo (mm:ss)". Filtros por ambiente, fecha y
  modalidad.
- Mover ventanas técnicas, hashes, reglas y microepisodios a una pestaña
  **Auditoría**.
- Eliminar las rutas de compatibilidad (`compatibility_mode`,
  `buildFallbackTimeline`) y generar el formulario de herramientas desde
  `GET /api/tools`.

Salida: recorrido completo en el navegador sin jerga técnica.

### Fase G — Consultar la memoria (3–4 días)

- Indexar eventos (texto) y fotogramas clave (CLIP) como representaciones
  de primer nivel; resultados agrupados por evento y episodio.
- Pregunta en lenguaje natural ⇒ `gpt-oss` usa las herramientas de la
  Fase 8 ya existentes ⇒ respuesta con citas a eventos y segundos del
  vídeo. Las herramientas siguen siendo de solo lectura.
- Conjunto de evaluación con tus propias capturas: 10–20 preguntas con
  evento esperado; registrar acierto@k y latencia.

Salida: preguntar "¿qué hizo después de aplicar el lubricante?" y recibir
la respuesta con enlace al minuto exacto.

## 6. Criterio de aceptación global

Con `data_samples/phase4_input/` subido como una única captura:

1. Se crea un episodio con tres activos.
2. El vídeo tiene eventos con Δt y miniaturas.
3. Cada evento tiene una descripción detallada, con habla alineada si la hay.
4. El episodio tiene un resumen comprensible.
5. Una consulta en español devuelve el evento correcto y abre el vídeo en
   ese segundo.
6. Reiniciar la aplicación conserva todo; reconstruir Qdrant no pierde nada.

## 7. Decisiones pendientes

- Modelo VLM por defecto (se decide con el banco de la Fase C).
- Tamaño de Whisper según el resultado de la prueba de compatibilidad en
  Blackwell.
- Si en el futuro se admiten vídeos largos, añadir filtrado de tramos sin
  actividad antes de describir (fuera de alcance mientras sean < 5 min).
