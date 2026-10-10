# PC-EG1 — Consolidación del flujo vertical de EpiGraph

Fecha: 2026-10-07. Este punto de control bloquea el desarrollo de agentes y de
nuevas fases hasta validar la memoria primaria con recursos reales.

## Criterio rector

EpiGraph tiene una sola memoria canónica y un recorrido observable:

```text
archivo original
  -> Asset
  -> ProcessingWindow
  -> Observation / Event
  -> MicroEpisode
  -> Episode
  -> representación Qdrant
  -> recuperación con evidencia
```

SQLite conserva el dominio. El almacén local conserva los archivos por hash.
Qdrant es una proyección reconstruible y nunca sustituye los registros
canónicos.

## Cambios de consolidación

1. La ingesta compartida copia los archivos al almacén local inmutable.
2. La API `POST /api/workflow/ingest-and-process` ejecuta el recorrido desde
   un archivo hasta la activación de la generación vectorial.
3. Las APIs `/api/memory/*` exponen activos, localizadores de evidencia,
   microepisodios, eventos, episodios y estado de indexación.
4. El frontend Atlas deja de ser la experiencia principal. La consola actual
   comienza en el activo original y permite inspeccionar la jerarquía.
5. `indexable_text` no se presenta como narración ni como hecho.
6. La línea temporal diferencia ventanas técnicas, observaciones, eventos,
   microepisodios y episodios.
7. Cuando no existen eventos semánticos, la interfaz lo declara expresamente.
8. Las hipótesis se muestran separadas de la estructura factual.

## Ejecución

```powershell
conda activate LabMemoryEpic01
cd C:\Users\sqs.erasmus\Documents\GitHub\LabMemoryEpisodic
epigraph --data .runtime\pceg1 --fake-encoders serve
```

Abra `http://127.0.0.1:8100/`, entre en **Ingesta**, seleccione un recurso y
use **Conservar y procesar**. Los encoders falsos solo permiten verificar el
flujo sin descargar modelos; para una recuperación semántica real, omita
`--fake-encoders`.

## Criterios de aceptación

- El archivo permanece disponible aunque desaparezca la copia de entrada.
- El hash mostrado en el front corresponde al archivo conservado.
- Las observaciones enlazan `asset_id`, ventana cuando procede y referencias
  exactas de evidencia.
- El episodio muestra sus microepisodios y las reglas de agrupación.
- Las ausencias de eventos no se rellenan con texto generado.
- La línea temporal representa intervalos y carriles por fuente.
- Qdrant queda sincronizado al terminar el recorrido.
- Una consulta semántica vuelve a registros SQLite y evidencias.

## Límites que siguen abiertos

- Los procesadores visual, de vídeo y de audio actuales extraen información
  técnica; todavía no detectan acciones u objetos semánticos reales.
- No existe todavía un derivador determinista de `Event` para el flujo normal.
- La reconstrucción Qdrant tras una ingesta es completa y segura, pero puede
  optimizarse posteriormente mediante una outbox incremental coherente.
- Los agentes continúan fuera del runtime.

No se debe iniciar la siguiente fase hasta probar este punto de control desde
el navegador con vídeo, audio y documento aportados por el usuario.
