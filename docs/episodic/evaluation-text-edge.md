# Baseline textual real — Qdrant Edge

Fecha de ejecución: 2026-10-04.

## Perfil ejecutado

- Backend: `qdrant-edge-py` 0.7.2, shard embebido persistente.
- Corpus: Atlas, 10 episodios y 20 representaciones.
- Recuperador: Nomic text-only, sin fusión CLIP.
- Modelo: `nomic-ai/nomic-embed-text-v1.5`, snapshot `e9b6763023c676ca8431644204f50c2b100d9aab`.
- Dimensión: 768, distancia cosine.
- Python 3.13.16 sobre Windows 11 Enterprise 64 bits.
- CPU: Intel Core Ultra 9 275HX.

## Resultado real con k=5

| Métrica | Antes de rebuild | Después de rebuild |
|---|---:|---:|
| Macro recall@5 | 0.9333 | 0.9333 |
| Macro precision@5 | 0.4400 | 0.4400 |
| Exactitud de veredicto | 1.0000 | 1.0000 |
| Latencia media, incluida carga inicial | 184.346 ms | 180.499 ms |
| Puntos | 20 | 20 |

Los rankings se conservaron después de reconstruir. La consulta negativa produjo `NO_MATCH`; q9, con `require_unique_resolution=true`, produjo `AMBIGUOUS`.

## Temporalidad y reconstrucción

- Los chunks contienen límites ISO UTC, milisegundos, clase temporal, incertidumbre y procedencia.
- `ATLAS-EP-006` conservó `18:23:18Z`–`18:25:06Z` antes y después del rebuild.
- La búsqueda semántica restringida a q3 aplicó el filtro dentro de Edge y devolvió solo `ATLAS-EP-006` y `ATLAS-EP-007`.
- `rebuild-index` creó una generación staging, verificó exactamente 20 puntos, reemplazó atómicamente el fichero `ACTIVE` y retiró la generación anterior.
- Después del cambio quedó una sola generación y el shard reabrió con 20 puntos.

## Compatibilidad observada

- Edge admite IDs enteros y UUID completos; rechaza strings arbitrarios. El adaptador usa UUIDv5 determinista y conserva `raw_point_id`.
- En Edge 0.7.2, `MatchValue` booleano se convierte a `0/1` y no consulta correctamente el índice `Bool`. El payload conserva los booleanos canónicos y añade campos keyword privados para el filtrado Edge.
- El shard mantiene bloqueo exclusivo del WAL. CLI y API no deben abrir el mismo directorio simultáneamente desde procesos distintos; el adaptador devuelve un error explícito en ese caso.
- Los upserts Edge eliminan primero el UUID existente porque esta versión no reemplaza completamente todos los campos de un punto mediante upsert.

## Reproducción

```powershell
$PY = 'C:\Users\sqs.erasmus\.conda\envs\LabMemoryEpic01\python.exe'

& $PY -m episodic.app --vector-backend edge --data <dir> init
& $PY -m episodic.app --vector-backend edge --data <dir> import --manifest data/atlas/manifest.json
& $PY -m episodic.app --vector-backend edge --data <dir> index
& $PY -m scripts.evaluate_atlas_text --vector-backend edge --data <dir> --limit 5
& $PY -m episodic.app --vector-backend edge --data <dir> rebuild-index
```

## Pendiente para cerrar H3

1. Aislar de forma probada cambios de modelo, revisión, dimensión y preprocesado.
2. Probar recuperación real del outbox después de reiniciar tras un fallo.
3. Acreditar el filtro por fuente con Edge real.
4. Validar la política de resolución única en un corpus independiente de Atlas.
