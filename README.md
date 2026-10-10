# EpiGraph — memoria episódica multimodal

EpiGraph conserva lo que ocurre en un entorno a partir de capturas reales
(vídeo, audio, imágenes y documentos) y permite consultarlo después con
evidencia verificable: cada respuesta vuelve al archivo original y al
instante exacto en que ocurrió.

> **Estado (2026-10-10).** La base técnica está operativa: custodia de
> originales por SHA‑256, SQLite como fuente de verdad, Qdrant Edge como
> índice reconstruible, búsqueda híbrida y herramientas de consulta. Lo que
> **todavía no hace** es entender el contenido: el vídeo solo produce
> intervalos técnicos y el audio no se transcribe. La hoja de ruta para que
> cada vídeo genere eventos con descripciones detalladas está en
> [`docs/plan-mejora-uso-real.md`](docs/plan-mejora-uso-real.md).

## Cómo funciona

```text
Captura (vídeo + audio + documentos subidos juntos)
  └─ Episodio: lo que ocurrió en ese entorno durante ese intervalo
       ├─ Eventos con Δt y descripción detallada     (objetivo, fases C–E)
       ├─ Transcripción alineada por tiempo          (objetivo, fase D)
       ├─ Citas documentales                         (PDF/TXT/MD hoy)
       └─ Evidencias: originales, fotogramas, intervalos
```

- **SQLite** guarda el dominio y es la única verdad.
- **El almacén de medios** conserva cada original por su hash; nunca se modifica.
- **Qdrant Edge** es una proyección de embeddings que puede borrarse y
  reconstruirse desde SQLite.
- Todo lo generado por un modelo queda marcado como tal, con su versión y
  sus evidencias; las hipótesis nunca se presentan como hechos.

## Puesta en marcha

Requisitos: Python 3.13 y el entorno Conda `LabMemoryEpic01`.

```powershell
conda activate LabMemoryEpic01
python -m pip install -e ".[dev]"
epigraph --data .runtime/demo --fake-encoders serve
```

Abra <http://127.0.0.1:8100/> y vaya a **Ingesta**: seleccione a la vez el
vídeo, el audio y los documentos de una misma grabación. Se crea una
**captura**, se procesa en segundo plano (el progreso se ve por etapas) y al
terminar ofrece abrir su episodio. Con `--fake-encoders` no
se descargan modelos (útil para probar el flujo); omítalo para una búsqueda
semántica real con Nomic y CLIP. El catálogo de la API está en
<http://127.0.0.1:8100/docs>.

Material de prueba: `data_samples/phase4_input/` contiene un vídeo, un audio
y un manual de taller que forman una misma captura.

## Comandos principales

| Comando | Para qué |
|---|---|
| `epigraph serve` | API y front en `127.0.0.1:8100`. |
| `epigraph capture FILES… --environment ENV [--title T] [--captured-at ISO]` | Sube los archivos tomados juntos como una captura y crea su episodio. |
| `epigraph ingest FILE --environment ENV --source SRC` | Registra un original en custodia. |
| `epigraph observe ASSET_ID` | Extrae observaciones del activo. |
| `epigraph compose --environment ENV` | Agrupa observaciones en episodios. |
| `epigraph search --text "…"` | Búsqueda; admite `--source`, `--start`, `--end`, `--intent`. |
| `epigraph last-observation --entity ID` | Última observación confirmada de una entidad. |
| `epigraph rebuild-index` | Reconstruye Qdrant desde SQLite sin perder el índice activo si falla. |

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
