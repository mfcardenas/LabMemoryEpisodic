# EpiGraph — Fase 3: ingestión de fuentes y ventanas temporales

Fecha de cierre técnico: 2026-10-04  
Estado: **IMPLEMENTADA Y VERIFICADA; pendiente de aceptación del usuario**

## 1. Resumen de lo implementado

Se implementó el flujo `Environment -> Source -> Asset -> ProcessingWindow`.
La CLI registra vídeo, audio, imagen, texto/PDF y texto estructurado JSON. La
ingesta calcula SHA-256 por bloques, detecta MIME por firma y extensión,
conserva la ruta absoluta del original, registra tamaño y obtiene duración de
WAV y MP4 cuando el contenedor la expone.

La repetición de un mismo archivo en la misma fuente recupera el Asset ya
existente. El mismo hash no puede registrarse silenciosamente bajo otra fuente
o ambiente.

Las ventanas solo se generan para audio y vídeo. Conservan offsets relativos,
`start_time`/`end_time` con zona horaria, DeltaTime y solapamiento. Si el Asset
no tiene fecha de captura se usa `created_at` como eje técnico y se registra
`timeline_basis=asset_created_at_technical_axis`; no se presenta como tiempo
observado. La política y los IDs de ejecución/ventana son deterministas.

No se crean observaciones, eventos, microepisodios ni episodios. Tampoco se
extrae, transcribe o describe contenido.

## 2. Archivos creados o modificados

### Creados

- `episodic/adapters/files/__init__.py`
- `episodic/adapters/files/metadata.py`
- `episodic/application/asset_ingestion.py`
- `episodic/application/windowing.py`
- `data_samples/phase3_structured.json`
- `tests/integration/test_phase3_ingestion.py`
- `docs/fase-3-ingestion-ventanas.md`

### Modificados

- `episodic/domain/provenance.py`
- `episodic/domain/processing.py`
- `episodic/ports/repository.py`
- `episodic/adapters/sqlite/migrations.py`
- `episodic/adapters/sqlite/repository.py`
- `episodic/bootstrap.py`
- `episodic/interfaces/cli/commands.py`
- `tests/integration/test_phase2_persistence.py`
- `README.md`

## 3. Componentes existentes reutilizados

- `Environment`, `Source`, `Asset`, `ProcessingRun` y `ProcessingWindow` de
  Fase 2; no se creó un modelo paralelo.
- `SqliteEpisodicRepository` como única fuente canónica y su sistema de
  migraciones.
- El bootstrap compartido y el comando `epigraph` de Fase 1.
- `Pillow`, ya declarada, para leer dimensiones/formato de imágenes.
- Biblioteca estándar para SHA-256, MIME, WAV y metadatos MP4.

No se reutilizó el chunker textual legado porque convertir documentos en
texto no pertenece a esta fase. No se usaron agentes, Qdrant ni procesadores
multimodales.

## 4. Decisiones técnicas tomadas

1. El original no se copia ni transforma: `Asset.uri` y
   `metadata.original_path` apuntan al archivo resuelto durante la ingesta.
2. `content_hash` usa el formato `sha256:<hex>` y actúa como clave de
   deduplicación de aplicación.
3. MIME se determina primero por firma conocida y después por extensión.
4. La duración WAV se lee con `wave`; MP4 se obtiene del átomo `mvhd` sin
   decodificar vídeo ni incorporar FFmpeg.
5. `delta_time_seconds` existe solo en `ProcessingWindow`. La última ventana
   puede tener menor intervalo efectivo, pero conserva el DeltaTime solicitado
   como parámetro de procesamiento.
6. Una política repetida sobre el mismo activo es idempotente mediante UUIDv5.
7. La migración SQLite v3 es aditiva y conserva payloads de Fase 2.
8. La verificación de esta fase se ofrece por CLI. No se modificó el frontend
   porque la especificación solicita una interfaz mínima de comandos y todavía
   no existe contenido semántico que visualizar como memoria.
9. Los comandos de Fase 3 usan una composición ligera del bootstrap: abren
   SQLite, pero no inicializan modelos, Qdrant ni el almacén de blobs.

## 5. Comandos exactos para ejecutar

Desde `LabMemoryEpisodic`, con el entorno ya instalado:

```powershell
conda activate LabMemoryEpic01
epigraph ingest video.mp4 --environment laboratorio_01 --source camera_A01
epigraph window <asset_id> --duration 60 --overlap 5
```

Ejemplo incluido:

```powershell
epigraph ingest data_samples/phase3_structured.json --environment laboratorio_01 --source registro_manual_01
```

Para aislar los datos de una comprobación:

```powershell
epigraph --data .runtime/fase3 ingest video.mp4 --environment laboratorio_01 --source camera_A01
epigraph --data .runtime/fase3 window <asset_id> --duration 60 --overlap 5
```

Si el contenedor audiovisual no expone duración compatible:

```powershell
epigraph window <asset_id> --duration 60 --overlap 5 --total-duration 300
```

Pruebas:

```powershell
python -m pytest -q
```

## 6. Pruebas ejecutadas y resultados

- Compilación del paquete con Python 3.13.16: correcta.
- Suite completa del proyecto en `LabMemoryEpic01` con Python 3.13.16:
  `68 passed, 2 warnings`.
- CLI real: ingesta JSON, deduplicación, ingesta MP4 y generación de tres
  ventanas verificadas correctamente.

La suite de Fase 3 cubre:

- vídeo MP4, audio WAV, PNG, TXT, PDF y JSON estructurado;
- hash, MIME, tamaño, duración y preservación de la ruta original;
- detección de duplicados;
- ventanas con solapamiento y línea temporal;
- idempotencia de la política de ventanas;
- rechazo de ventanas para activos no audiovisuales;
- ausencia de episodios.

## 7. Limitaciones pendientes

- Sin FFmpeg/ffprobe no se calcula duración de todos los contenedores. Esos
  activos se registran y `window` exige `--total-duration`.
- La ruta se conserva por referencia: mover o borrar el archivo externo puede
  invalidarla, aunque el hash registrado permite detectar cambios futuros.
- La deduplicación se aplica en el servicio local; no se ha añadido una
  restricción distribuida porque la arquitectura sigue siendo monolítica.
- La UI web actual muestra memoria semántica, no un explorador técnico de
  activos o ventanas.

## 8. Cambios propuestos para la siguiente fase (no implementados)

- Definir procesadores por modalidad que produzcan `Observation` y
  `EvidenceReference` exactas desde estas ventanas y activos.
- Exponer activos, ventanas y evidencias en el frontend solo cuando formen
  parte del flujo verificable solicitado por esa fase.
- Evaluar ffprobe como adaptador opcional si la siguiente fase exige cobertura
  amplia de formatos audiovisuales.

No se ha implementado ninguno de estos puntos.
