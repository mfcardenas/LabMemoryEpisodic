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
