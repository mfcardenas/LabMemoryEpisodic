# EpiGraph — Fase 4: extracción de observaciones normalizadas

Fecha del punto de control: 2026-10-06  
Estado: **COMPLETA Y VERIFICADA CON RECURSOS REALES DEL USUARIO**

## 1. Resumen de lo implementado

Se incorporó el contrato común:

```python
process(asset_or_window) -> list[Observation]
```

Los procesadores disponibles son `TextProcessor`, `DocumentProcessor`,
`AudioProcessor`, `ImageProcessor`, `VideoProcessor` y `MockProcessor`.

Cada observación persiste ID, fuente, ambiente, Asset, ventana cuando existe,
tiempos completos, modalidad, tipo, atributos, descripción, confianza,
referencias de evidencia, productor, versión y ejecución. `observation_id`
es un alias del ID canónico y se incluye en la serialización externa.

Cada procesador crea un `ProcessingRun`. Los IDs de run, evidencia y
observación son UUIDv5 deterministas. Un error marca la ejecución como
`failed`; no elimina ni altera el Asset original.

No se crean episodios, microepisodios, eventos, entidades, relaciones o
hipótesis. No se usan agentes ni se realizan afirmaciones causales.

## 2. Archivos creados o modificados

### Creados

- `episodic/ports/processors.py`
- `episodic/processors/__init__.py`
- `episodic/processors/base.py`
- `episodic/processors/text.py`
- `episodic/processors/document.py`
- `episodic/processors/audio.py`
- `episodic/processors/image.py`
- `episodic/processors/video.py`
- `episodic/processors/mock.py`
- `episodic/application/text_fragments.py`
- `episodic/application/observation_extraction.py`
- `scripts/run_phase4_demo.py`
- `data_samples/phase4_input/README.md`
- `tests/integration/test_phase4_processors.py`
- `docs/fase-4-observaciones-normalizadas.md`

### Modificados

- `episodic/domain/entities.py`
- `episodic/adapters/files/metadata.py`
- `episodic/ports/repository.py`
- `episodic/adapters/sqlite/migrations.py`
- `episodic/adapters/sqlite/repository.py`
- `episodic/application/asset_ingestion.py`
- `episodic/bootstrap.py`
- `episodic/interfaces/cli/commands.py`
- `pipeline/chunker.py`
- `pyproject.toml`
- `requirements.txt`
- `tests/integration/test_phase2_persistence.py`
- `tests/integration/test_phase3_ingestion.py`
- `docs/episodic/reuse-map.md`
- `README.md`

## 3. Componentes existentes reutilizados

- El algoritmo de `pipeline/chunker.py::TextChunker` se extrajo a una rutina
  compartida con offsets; el RAG legado conserva su API.
- El cálculo RMS de `robot/device/mic.py::wav_rms` se adaptó para intervalos
  WAV, sin importar configuración de hardware ni editar el audio.
- El patrón de carga diferida del Whisper de `helper.py`/`robot/brain/models.py`
  queda preservado como decisión, pero ASR no se activa porque `onnx_asr` no
  forma parte del entorno actual.
- `Asset`, `ProcessingWindow`, `ProcessingRun`, `Observation` y
  `EvidenceReference` se ampliaron; no existe un modelo paralelo.
- SQLite sigue siendo la fuente canónica y el bootstrap ligero evita arrancar
  Qdrant y modelos durante extracción.
- Pillow se reutiliza para inspección de imagen.

El detector YOLO/BOT-SORT no se importó: OpenCV, Torch, Ultralytics y los
pesos no están disponibles. Los procesadores visuales básicos conservan
fotograma/intervalo y publican listas de detecciones vacías, sin simular
resultados.

## 4. Decisiones técnicas

1. SQLite v4 añade `description`, `modality`, `observation_type` y
   `attributes_json` a observaciones, preservando registros anteriores.
2. Texto y JSON se fragmentan con posiciones de caracteres exactas.
3. PDF se extrae por página con `pypdf` cargado en diferido. Una página sin
   texto genera solo un estado técnico, no una descripción inventada.
4. Audio WAV calcula RMS por ventana y siempre conserva el intervalo exacto.
5. Imagen conserva `frame_number=0` y solo afirma dimensiones, modo y formato.
6. Vídeo produce una observación técnica por ventana audiovisual. Un backend
   detector podrá enriquecerla en otra fase, pero no se anticipa aquí.
7. Una excepción se convierte en fallo localizado del procesador; el
   orquestador continúa con otros objetivos.
8. No se añadió UI nueva. La demostración controlada imprime el objeto completo
   y su evidencia; una vista web tendrá sentido cuando se autorice el recorrido
   de revisión de observaciones.
9. La duración se inspecciona localmente para WAV, MP3, MP4 clásico y MP4
   fragmentado. Si un Asset existente tenía metadatos derivados incompletos,
   una nueva ingesta por el mismo hash los actualiza sin cambiar su identidad.
10. La demo tolera consolas Windows con `cp1252`: los símbolos Unicode no
    representables se escapan, sin interrumpir el procesamiento.

## 5. Comandos exactos

Actualizar dependencias en el entorno gestionado por el usuario:

```powershell
conda activate LabMemoryEpic01
python -m pip install -e ".[dev]"
```

Procesar un Asset existente:

```powershell
epigraph observe <asset_id>
epigraph observe <asset_id> --processor mock-processor
```

Demostración con los recursos del usuario:

```powershell
python scripts/run_phase4_demo.py `
  --input-dir data_samples/phase4_input `
  --data .runtime/fase4-demo `
  --environment laboratorio_01
```

Pruebas:

```powershell
python -m pytest -q
```

## 6. Pruebas ejecutadas

- Suite completa en Python 3.13.16: `75 passed, 2 warnings`.
- Regresión de duración MP3, MP4 fragmentado y refresco de metadatos de un
  Asset duplicado: `7 passed` en el conjunto de Fase 3.
- Demostración sintética: vídeo, audio y JSON en un ambiente; salida con
  observaciones y evidencia correcta; `exit code 0`.
- Demostración real del usuario: MP4 de `5,433 s`, MP3 de `15,020 s` y PDF de
  dos páginas; `exit code 0`. Resultado persistido: 3 Assets, 2 ventanas, 9
  observaciones (1 vídeo, 1 audio y 7 documento), 9 evidencias, 0 episodios y
  0 entidades. Todas las ejecuciones quedaron en estado `completed`.
- Se verificaron las cinco modalidades, offsets de texto, página PDF,
  intervalos audiovisuales, frame de imagen, RMS por ventana, idempotencia del
  MockProcessor, fallo localizado y ausencia de episodios/entidades.

Las dos advertencias ya existentes son una deprecación de Starlette y la
limitación de payload indexes en Qdrant local.

## 7. Limitaciones pendientes

- `AudioProcessor` mide segmentos; no transcribe voz ni clasifica sonidos.
- `ImageProcessor` y `VideoProcessor` no detectan objetos sin un backend visual
  explícito.
- Un PDF escaneado sin capa textual produce estado `no_text`; OCR no está
  implementado.
- DOCX todavía no está entre los tipos admitidos; para esta demostración se
  procesó el PDF equivalente conservando el DOCX original sin alterarlo.

## 8. Siguiente fase propuesta (no implementada)

- Incorporar backends opcionales y evaluables de ASR, OCR y detección visual.
- Permitir revisión humana de observaciones y evidencias desde el frontend.
- Definir composición de observaciones en eventos y microepisodios.

Ninguno de estos cambios se ha implementado.
