# EpiGraph — memoria episódica multimodal

EpiGraph conserva lo que ocurre en un entorno a partir de capturas reales
(vídeo, audio, imágenes y documentos) y permite consultarlo después con
evidencia verificable: cada respuesta vuelve al archivo original y al
instante exacto en que ocurrió.

> **Estado (2026-10-12).** Cada captura (vídeo, audio y documentos subidos
> juntos, o dejados por otro proceso en una carpeta vigilada) forma un
> episodio. El vídeo se divide en **eventos con su Δt**,
> fotogramas clave y una descripción detallada generada por un modelo de
> visión local (Ollama), y cada evento muestra **lo que se dijo** durante su
> intervalo (transcripción con Whisper en un servicio aparte). Pendiente:
> resúmenes jerárquicos, agentes de consulta con citas y escala a millones de
> eventos. Hoja de ruta vigente:
> [`docs/plan-agentes-consulta.md`](docs/plan-agentes-consulta.md) (diseño
> original en [`docs/plan-mejora-uso-real.md`](docs/plan-mejora-uso-real.md)).

## Cómo funciona

```text
Captura (vídeo + audio + documentos subidos juntos)
  └─ Episodio: lo que ocurrió en ese entorno durante ese intervalo
       ├─ Eventos con Δt y descripción detallada     (objetivo, fases C–E)
       ├─ Transcripción alineada por tiempo          (fase D)
       ├─ Citas documentales                         (PDF/TXT/MD hoy)
       └─ Evidencias: originales, fotogramas, intervalos
```

- **SQLite** guarda el dominio y es la única verdad.
- **El almacén de medios** conserva cada original por su hash; nunca se modifica.
- **Qdrant** (servidor, por HTTP) guarda la proyección de embeddings en la
  colección `EPIGRAPH_QDRANT_COLLECTION`; puede borrarse y reconstruirse desde
  SQLite con `epigraph rebuild-index`. Qdrant Edge (ficheros locales) queda
  solo para demos y pruebas sin servidor: `--vector-backend edge`.
- Todo lo generado por un modelo queda marcado como tal, con su versión y
  sus evidencias; las hipótesis nunca se presentan como hechos.

## Puesta en marcha

Requisitos: Python 3.13 y el entorno Conda `LabMemoryEpic01`.

```powershell
conda activate LabMemoryEpic01
python -m pip install -e ".[dev,video]"
ollama pull qwen3.5:4b          # modelo de visión por defecto
epigraph --data .runtime/demo --vector-backend edge --fake-encoders serve
```

El vídeo se describe con `qwen3.5:4b` en Ollama (unos 5 s por tramo en una
RTX 5080). Para comparar modelos con vídeos propios sin tocar la memoria:
`python scripts/benchmark_vision.py VIDEO.mp4 --models qwen3.5:4b gemma3:12b`.
Sin Ollama o sin PyAV la captura sigue funcionando, pero el vídeo no tendrá
eventos y la etapa lo indica.

Para el uso diario, `start.bat` (Windows) o `./start.sh` levantan Qdrant y la
transcripción con Docker y arrancan la API con el front en el puerto de
`EPIGRAPH_PORT`; los argumentos se pasan a `epigraph serve`. El servidor
procesa lo que haya en la carpeta vigilada de `watch.toml`: las cargas se
ponen ahí a mano (o las deja la herramienta de captura).

Para repetir pruebas de carga desde cero, con EpiGraph parado:
`reset.bat` (o `./reset.sh`; `--yes` para no confirmar) borra SQLite, los
medios, el índice local y la colección de EpiGraph en Qdrant
(`epigraph reset-data`). No toca la carpeta vigilada ni `_procesados`, ni
otras colecciones del servidor: para reprocesar una carga, vuelva a ponerla
en la carpeta vigilada.

Los vectores se guardan en el servidor Qdrant del `docker-compose.yml`
(volumen persistente, arranque automático; ver `docs/qdrant.md`):

```powershell
docker compose up -d qdrant
```

El habla se transcribe en un **servicio aparte** con la API de OpenAI
(`/v1/audio/transcriptions`). El repositorio trae Speaches (faster-whisper)
en Docker con GPU:

```powershell
docker compose up -d speech
epigraph speech-check --download --file data_samples/phase4_input/conversacion_mecanico_15s_exact.mp3
```

Servicio, modelo, idioma y vocabulario se cambian con `EPIGRAPH_SPEECH_*`
en `.env` (ver `.env.example`): cualquier servidor compatible sirve
(whisper.cpp, vLLM, OpenAI…). `EPIGRAPH_SPEECH_BACKEND=none` la desactiva.
Si el servicio no responde, la captura se procesa igual sin transcripción.

Abra <http://127.0.0.1:8100/> y vaya a **Ingesta**: seleccione a la vez el
vídeo, el audio y los documentos de una misma grabación. Se crea una
**captura**, se procesa en segundo plano (el progreso se ve por etapas) y al
terminar ofrece abrir su episodio. Con `--fake-encoders` no
se descargan modelos (útil para probar el flujo); omítalo para una búsqueda
semántica real con Nomic y CLIP. El catálogo de la API está en
<http://127.0.0.1:8100/docs>.

Material de prueba: `data_samples/phase4_input/` contiene un vídeo, un audio
y un manual de taller que forman una misma captura.

### Proyectos: espacios de datos aislados

Un proyecto agrupa sus ambientes y evidencias sin mezclarlos con los de otros.
Cada uno tiene su base de datos SQLite, sus medios, su índice, su colección de
Qdrant (`epigraph_p_<id>`) y, si quiere, sus carpetas vigiladas. Un mismo
ambiente (`A001`) puede existir en dos proyectos a la vez.

- **Principal** son las capturas de siempre: usa `EPIGRAPH_DATA_DIR`,
  `EPIGRAPH_QDRANT_COLLECTION` y `EPIGRAPH_WATCH_CONFIG`, y no se mueve nada.
  No se puede pausar ni borrar.
- **Crear**: pantalla **Proyectos** (07) o `POST /api/projects`. Nombre,
  descripción, tipo (prueba o real) y, opcionalmente, carpetas a vigilar. Los
  datos van a `epigraph-projects/<id>/` (`EPIGRAPH_PROJECTS_DIR`). Las carpetas
  se leen por manifiesto (`capture.json`, con el ambiente de cada captura) y los
  originales no se mueven. Una carpeta, ni sus subcarpetas, puede pertenecer a
  dos proyectos.
- **Usar**: el selector **Proyecto** de la barra lateral. La consola de cada
  proyecto vive en `/p/<id>/` y su API en `/p/<id>/api/...` (catálogo en
  `/p/<id>/docs`); `/api/...` sin prefijo sigue siendo el principal.
- **Activo o en pausa**: todos se consultan; solo los activos vigilan sus
  carpetas y procesan su cola. Todas las colas comparten un único turno de
  trabajo pesado: un proyecto procesa mientras los demás esperan («En espera»
  en Vigilancia), así no se reparten la VRAM ni Ollama.
- **Borrar**: escribiendo su identificador para confirmar. Se borran su
  directorio y su colección de Qdrant; las carpetas vigiladas y sus originales
  no se tocan.
- **CLI**: `python -m episodic projects` los lista; `--project <id>` hace que
  cualquier comando actúe sobre ese proyecto.

### Entrada automática: carpetas vigiladas

La herramienta que graba los entornos es externa y entrega cada captura en
una subcarpeta con un **manifiesto** (`capture.json`,
`epigraph.capture/v1`) que declara el **entorno**, el **instante** (epoch
en segundos, UTC) y los **dispositivos** con sus ficheros y, si se quiere,
el instante en que empezó cada uno. EpiGraph no deduce nada de los nombres.
Contrato completo para el equipo de la herramienta:
[`docs/contrato-captura.md`](docs/contrato-captura.md).

- La captura se llama `<entorno>_<epoch>` (p. ej.
  `env_context_01_1791786600`): es el `external_id` del episodio en SQLite
  y en Qdrant, y no puede repetirse.
- Cada dispositivo es una fuente `<entorno>.<dispositivo>` con su carril en
  la línea temporal; todo lo extraído de un fichero lleva hora absoluta
  desde el inicio de su dispositivo, de modo que cámaras y micrófonos se
  alinean y las capturas se ordenan por su instante real.
- Una captura puede traer solo vídeo, solo audio o todo junto. Se registra
  cuando el manifiesto existe y todos sus ficheros están completos; si
  alguno no llega a tiempo, o el manifiesto incumple el contrato, queda
  fallida con el motivo y se reintenta al reescribir el manifiesto.
- Cada fichero queda anotado en SQLite: reiniciar no reprocesa ni pierde
  nada. Los originales pueden quedarse o moverse a `_procesados`.

Configuración: copie `watch.example.toml` a `watch.toml` (fuera de git) y
ponga `EPIGRAPH_WATCH_CONFIG=watch.toml` en `.env`. Para carpetas sin
herramienta quedan los modos `folder`, `file` y `time_gap`.

`epigraph serve` vigila mientras está en marcha (`--no-watch` lo evita);
`epigraph watch` vigila sin web y `epigraph watch --once` procesa lo
pendiente y termina. Solo un proceso vigila a la vez. La sección
**Vigilancia** del front muestra las carpetas, los ficheros leídos, los
trabajos con sus etapas y el registro de ejecución en vivo.

### Consultas: búsqueda por momentos

La unidad de búsqueda es el **evento**: cada tramo de vídeo descrito se
indexa como **pasaje** con su título, resumen, descripción, escena, acciones,
actores, objetos, texto visible y lo que se dijo mientras tanto, además de
su hora absoluta y su posición en el vídeo. Las frases transcritas que no
caen dentro de ningún evento y los fragmentos de documento son pasajes
aparte. SQLite guarda los pasajes en la tabla `passages` (proyección
reconstruible, como Qdrant). Una consulta los busca por dos vías, ambas con
los mismos filtros de ambiente, fuente, modalidad y tiempo aplicados antes
del corte, y las fusiona con RRF:

- **significado** (Nomic en Qdrant): paráfrasis y sinónimos;
- **palabras** (BM25 en SQLite FTS5): códigos, nombres y frases exactas
  entre comillas; plural y género se casan por prefijo.

Antes de buscar, la pregunta se **entiende** sin modelos
(`episodic/application/query_understanding.py`): «ayer por la tarde», «el
lunes», «el 12 de octubre después de las 16:30» o «hace tres días» se
convierten en un intervalo absoluto (zona horaria `EPIGRAPH_QUERY_TIMEZONE`,
por defecto la del sistema) que filtra las dos vías y desaparece del texto
buscado; se detecta la intención (cuándo, quién, dónde, cuánto, documento,
antes/después) y, si la pregunta es de antes/después, cada evento encontrado
llega con sus eventos **anterior y siguiente** (`neighbors` en la API). El
plan aplicado se devuelve en `plan` y el front lo muestra («Entendí: …»).

Un cross-encoder multilingüe (`EPIGRAPH_RERANK_MODEL`) reordena los mejores y
descarta los que no llegan a `EPIGRAPH_RERANK_MIN_SCORE`, salvo que ambas vías
los pongan entre sus `EPIGRAPH_PASSAGE_AGREEMENT_RANK` primeros: una pregunta
(«¿cuándo se usó una tarjeta?») puntúa bajo en el reordenador aunque el pasaje
la responda. El resultado son momentos agrupados por episodio, con el motivo
por el que aparecen y un enlace que abre el episodio en ese instante
(`POST /api/passage-search`); los descartados se muestran aparte, plegados.

Todo se ajusta en `.env` (ver `.env.example`). El formulario muestra los
umbrales vigentes y permite cambiarlos en cada consulta
(`GET /api/passage-search/settings`).

### Resúmenes jerárquicos: episodio, día y entorno

Al terminar una captura, el modelo de texto (`EPIGRAPH_TEXT_MODEL`, por
defecto `gpt-oss:latest` en Ollama; `EPIGRAPH_TEXT_BACKEND=none` lo
desactiva) redacta el **resumen del episodio** solo a partir de sus eventos,
del habla y de los documentos adjuntos: título, resumen, narrativa
cronológica con citas `[ev N · mm:ss]`, momentos clave y lo que no consta.
Las citas se **verifican** contra los eventos reales (las inválidas se
eliminan y quedan anotadas) y en la ficha del episodio cada cita abre el
vídeo en ese segundo. Después, un trabajo aparte («Resumen del día y del
entorno», visible en Vigilancia) redacta el **resumen del día** del entorno a
partir de los resúmenes de sus episodios, y el **del entorno** a partir de sus
últimos días (`EPIGRAPH_DIGEST_ENVIRONMENT_DAYS`). Es texto generado:
cada resumen guarda modelo, versión del prompt y huella de la entrada;
regenerar («Regenerar resumen» o `POST /api/summaries/regenerate`) crea
`revisión + 1` y conserva la anterior (`GET /api/summaries?scope=&key=`).
Si el modelo falla, la captura termina igual y el fallo queda en su etapa.
El resumen vigente de cada episodio se indexa como pasaje `episode_summary`,
pero la búsqueda por momentos **no lo usa salvo que se pida**
(`include_summaries`): la prosa generada atrae al reordenador y desplaza al
momento exacto; los resúmenes están pensados para que el agente navegue la
memoria de arriba abajo (entorno → día → episodio → evento).

### Línea temporal: pulso, pistas e inspector

La pestaña **Línea temporal** está pensada para millones de eventos: el
servidor solo devuelve lo que cabe en pantalla.

- **Pulso**: mapa de calor de eventos por ambiente y por minuto, hora o día
  (según el rango), con el volumen total encima. Arrastrar elige el intervalo
  que se muestra en las pistas.
- **Pistas**: un grupo plegable por ambiente y, dentro, una pista por fuente:
  vídeo con la tira de fotogramas clave de cada evento, habla transcrita
  alineada en el tiempo y documentos por página. Rueda para acercar sobre el
  cursor, arrastrar para desplazar, minimapa del día y cabezal con la hora.
  Si el intervalo tiene más eventos de los que caben, se muestran agrupados por
  cubo y un clic en la barra acerca el zoom.
- **Inspector y relato**: el detalle del evento (fotogramas, acciones, actores,
  objetos, texto visible, habla), anterior y siguiente en la misma fuente y
  «Abrir en el episodio» en ese segundo. El modo Relato lista lo visible como
  tarjetas por instante.

- **Desde Consultas**: cada resultado de la búsqueda y cada cita de una
  respuesta del agente tienen «Ver en la línea», y el resumen de la búsqueda y
  la evidencia citada, «Ver en la línea temporal». La línea se abre con esos
  momentos marcados (bandas, banderas numeradas en la regla, marcas en el
  pulso y el minimapa, clip resaltado) y una barra para ir de uno a otro, ver
  todos o quitar las marcas.

API: `GET /api/timeline/span`, `GET /api/timeline/density` (`start`, `end`,
`bucket=auto|minute|hour|day`, `environment_id`, `by_source`) y
`GET /api/timeline/items` (`start`, `end`, `environment_id`, `limit`), más
`?max_side=` en `/api/memory/events/{id}/keyframes/{i}` para miniaturas. Los
cubos se cuentan en la hora local de `EPIGRAPH_QUERY_TIMEZONE`, con los
cambios de horario. La línea temporal clásica sigue en el código, oculta:
`LEGACY_TIMELINE = true` en `app.js` la recupera.

### Estilo visual: editorial o panel

La consola tiene dos estilos. **Editorial** es el de siempre y sale por
defecto. **Panel** es un tema de grises fríos y primario azul, con superficies
planas y Nunito Sans, en claro u oscuro. Se cambia en la barra lateral
(«Estilo») y el navegador lo recuerda. También vale `?estilo=panel-claro`,
`?estilo=panel-oscuro` o `?estilo=editorial` en la URL. Todo el tema vive en
`episodic/interfaces/web/tema-panel.css` y solo actúa con
`<html data-skin="panel">`: quitar ese fichero deja la consola como estaba.

### Preguntar: el agente de consulta

La pestaña **Preguntar** de **Consultas** (`POST /api/ask`) envía la pregunta a un agente
(`EPIGRAPH_AGENT_MODEL`, por defecto el mismo gpt-oss de los resúmenes) que
solo puede usar las herramientas de memoria: busca momentos, navega
ambientes → días → episodios → eventos, lee eventos vecinos y documentos, y
responde en español. Cada momento que recupera recibe una marca corta
(`m1`, `m2`…) y el agente **cita con esas marcas**; antes de devolver la
respuesta, cada marca se resuelve contra lo recuperado de verdad: las
inventadas se eliminan y quedan anotadas, y la confianza se calcula de eso
(`alta`: todo citado y verificado; `media`: alguna cita inválida; `baja`:
afirma sin citar; `sin_evidencia`: «No consta en la memoria»). En el front
cada cita abre el episodio en ese segundo. El bucle está acotado
(`EPIGRAPH_AGENT_MAX_STEPS`, `_MAX_TOOL_CALLS`, `_MAX_SECONDS`); cada paso
se registra en Vigilancia (componente `agente`) y la respuesta completa
—pasos, argumentos, tokens, latencia— queda en `answer_runs`
(`GET /api/answers`). `scripts/evaluate_questions.py --mode ask` evalúa las
citas del agente con el mismo banco de preguntas.

### Red de agentes: preguntas que cruzan episodios

El agente único responde bien a preguntas de un momento («¿quién dijo que
estaba agarrotado?»). Las que cruzan episodios («¿en qué capturas aparece una
tarjeta y qué se hace con ella en cada una?», «¿cuántas veces…?», «¿qué tienen
en común…?») las responde una red de agentes:

1. **Planificador**: decide la estrategia (agente único, mapa por episodio o
   subpreguntas) y el foco que hay que buscar.
2. **Cobertura** (`find_episodes`, sin LLM): los episodios candidatos, cada uno
   con su mejor momento. El reordenador ve como mucho dos pasajes de cada
   episodio, así llegan a puntuarse episodios distintos, y los que no alcanzan
   el umbral de relevancia (`EPIGRAPH_RERANK_MIN_SCORE`) se descartan.
3. **Localizadores**: uno por episodio (o por subpregunta), en paralelo, con la
   búsqueda acotada a su episodio. Confirman si el foco aparece y responden la
   pregunta dentro de él, con citas. Las marcas `mN` son comunes a toda la red.
4. **Narrador**: redacta la respuesta solo con esos hallazgos, con las cifras
   que calcula la red (episodios revisados, donde aparece, sin revisar).
5. **Verificador**: contrasta cada frase con la evidencia de sus citas y retira
   las que no se sostienen; si rechaza demasiadas, no borra nada y lo avisa.

En Consultas → Preguntar se elige el modo: **Automático** (la red solo si la
pregunta tiene señales de varios episodios; las demás no pagan la red), **Un
agente** o **Red de agentes**. La respuesta muestra el plan, cada episodio
revisado con sus hallazgos, la tabla de agentes y la traza etiquetada por
agente.

| Variable | Por defecto | Para qué |
|---|---|---|
| `EPIGRAPH_AGENT_MODE` | `auto` | `auto`, `simple` o `red` |
| `EPIGRAPH_AGENT_MAP_EPISODES` | `8` | episodios que se revisan como mucho |
| `EPIGRAPH_AGENT_WORKERS` | `2` | localizadores a la vez (con `OLLAMA_NUM_PARALLEL` > 1 se nota más) |
| `EPIGRAPH_AGENT_NETWORK_MAX_SECONDS` | `300` | presupuesto total de la red |
| `EPIGRAPH_AGENT_VERIFY` | `true` | verificador por LLM |

`scripts/evaluate_questions.py --mode ask --agent-mode red|auto|simple`
evalúa cada modo.

**Consola de agentes.** Al lanzar una pregunta se abre una ventana flotante
tipo log: arriba, una ficha por agente (coordinador, planificador, cada
localizador, narrador, verificador) con su estado, vueltas, herramientas y
tiempo; debajo, cada acción en cuanto ocurre (plan, cobertura, herramientas,
hallazgos, narración, verificación), con filtros por tipo y por agente. Se
minimiza a una píldora que sigue visible en otras pantallas mientras trabaja;
el selector «Ejecuciones recientes» y el botón «Consola de agentes» de cada
respuesta reabren la traza de cualquier pregunta anterior.

### Herramientas para agentes

`GET /api/tools` publica el catálogo (entrada y salida como esquema JSON) y
`POST /api/tools/{nombre}` ejecuta cada herramienta; todas son de solo
lectura. A las once deterministas de la Fase 8 se suman seis pensadas para
un agente, compactas (`max_chars`) y paginadas:

| Herramienta | Qué devuelve |
|---|---|
| `search_moments` | momentos citables (evento, habla, documento) con plan de la pregunta, posición en el vídeo y vecinos |
| `get_event` / `get_event_neighbors` | un evento completo (acciones, actores, objetos, texto visible, habla) y los de alrededor |
| `get_episode_summary` | el resumen generado del episodio (marcado `generated`) y, si se pide, sus revisiones |
| `browse_memory` | navegación ambientes → días → episodios → eventos, con los resúmenes de cada nivel |
| `get_document_passages` | fragmentos de los documentos de un episodio |

La pestaña **Herramientas** del front genera los formularios desde ese
catálogo. Las salidas separan `fact` (observado), `generated` (texto de
modelo) y `evidence`.

### Encoders en la GPU

Nomic, CLIP y el reordenador corren en ONNX Runtime, **en CPU por defecto**.
Con el extra `[gpu]` pueden usar CUDA, pero solo si se pide:
`EPIGRAPH_ENCODER_DEVICE=cuda` (o `auto`, que la detecta). En la RTX 5080,
Nomic pasa de ~8 a ~120 textos/s, lo que acelera cada captura y la
reindexación. Aviso: en el portátil del laboratorio la carga sostenida en la
GPU (perfil «real» de la prueba de escala, reindexaciones largas) reinicia el
equipo a los pocos minutos; por eso el valor por defecto es `cpu` y las
pasadas largas se hacen en CPU o en otro equipo. La rueda de `onnxruntime-gpu` para CUDA 12 no está en PyPI
(la de PyPI es para CUDA 13, cuyas librerías `nvidia-*-cu13` no tienen rueda
para Windows):

```bat
pip install -e .[gpu] --extra-index-url https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/onnxruntime-cuda-12/pypi/simple/
```

Sustituye al `onnxruntime` de CPU (mismo módulo) y trae cuDNN y cuBLAS por
pip; no hace falta instalar CUDA en el sistema. En GPU conviene
`EPIGRAPH_ENCODER_BATCH_SIZE=16`.

### Prueba de escala

`scripts\scale_test.bat` (o `scripts/scale_test.sh`) ejecuta sin intervención
los dos perfiles de la Fase S sobre un corpus sintético propio
(`episodic-data-scale-<perfil>`, colección `episodic_memory_scale_<perfil>`;
nunca toca las capturas reales): **hash** (200 000 eventos con vectores de
hash: mide SQLite, FTS y Qdrant) y **real** (50 000 eventos con Nomic y el
reordenador; horas en CPU). Cada perfil genera, indexa y mide la búsqueda por
momentos (con y sin reordenador, con intervalo), `search_moments`,
`browse_memory`, `get_event` y vecinos, y deja `data/eval/escala-*.json` más
una fila para `docs/evaluacion.md`. `python scripts/scale_test.py --help`
muestra las opciones (`--events`, `--skip-build` para medir sin regenerar).

**El índice se mantiene al día solo.** Cada captura se indexa al procesarse.
SQLite guarda además la huella con la que se indexó (modelos, prefijo de
documento, versión del formato); al arrancar (`serve` o `watch`), si no
coincide con `.env`, faltan pasajes o Qdrant está vacío, se encola un trabajo
**Reindexado** que se ve en Vigilancia con su motivo y su progreso. Se
reconstruye en una generación aparte: la búsqueda sigue funcionando hasta que
se activa la nueva. «Reindexar» en Vigilancia (`POST /api/index/reindex`) lo
fuerza; `GET /api/index/status` dice si el índice está al día y por qué no.

## Comandos principales

| Comando | Para qué |
|---|---|
| `epigraph serve [--no-watch]` | API y front en `127.0.0.1:8100`; vigila las carpetas de `EPIGRAPH_WATCH_CONFIG`. |
| `epigraph watch [--config F] [--once]` | Vigila las carpetas sin servidor web; `--once` procesa lo pendiente y termina. |
| `epigraph capture FILES… --environment ENV [--title T] [--captured-at ISO] [--audio-offset S]` | Sube los archivos tomados juntos como una captura y crea su episodio. |
| `epigraph speech-check [--download] [--file F]` | Comprueba el servicio de transcripción y su modelo. |
| `epigraph ingest FILE --environment ENV --source SRC` | Registra un original en custodia. |
| `epigraph observe ASSET_ID` | Extrae observaciones del activo. |
| `epigraph compose --environment ENV` | Agrupa observaciones en episodios. |
| `epigraph search --text "…"` | Búsqueda; admite `--source`, `--start`, `--end`, `--intent`. |
| `epigraph last-observation --entity ID` | Última observación confirmada de una entidad. |
| `epigraph rebuild-index` | Reconstruye Qdrant desde SQLite sin perder el índice activo si falla (el servidor ya lo hace solo cuando hace falta). |
| `epigraph reset-data [--yes]` | Borra SQLite, medios y la colección de EpiGraph en Qdrant para repetir cargas; no toca la carpeta vigilada. |

`epigraph --help` muestra el resto. La configuración usa variables
`EPIGRAPH_*` (ver `.env.example`); las rutas relativas se anclan a la raíz
del proyecto.

## Pruebas

```powershell
python -m pytest -q
```

## Estructura

```text
episodic/          producto: dominio, servicios, adaptadores, CLI, HTTP y front
  interfaces/web/  front local (HTML + JS sin framework)
tests/             unitarios, integración y smoke
scripts/           demos reproducibles y evaluación
data/atlas/        corpus simulado de 10 episodios y consultas de referencia
data_samples/      material de prueba real
docs/              plan vigente; docs/historico/ guarda las fases 0–8
legacy/rag/        RAG documental anterior, aislado (ver su README)
```

`sc-on-device-ai-memory/` contiene material de referencia (robot y cursos)
cuya integración está aplazada.
