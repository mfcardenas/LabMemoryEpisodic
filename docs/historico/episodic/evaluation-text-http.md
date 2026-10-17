# Baseline textual real — perfil Qdrant HTTP

Fecha de ejecución: 2026-10-04.

Este documento conserva la medición del perfil HTTP opcional. La ejecución de aceptación sobre Edge está en [evaluation-text-edge.md](evaluation-text-edge.md).

## Perfil ejecutado

- Corpus: Atlas, 10 episodios, 20 representaciones indexadas.
- Consultas: 6 consultas semánticas; positivas, negativa y ambigua.
- Recuperador: Nomic text-only, sin fusión ni ranking CLIP.
- Modelo: `nomic-ai/nomic-embed-text-v1.5`.
- Snapshot local: `e9b6763023c676ca8431644204f50c2b100d9aab`.
- Dimensión: 768; distancia cosine en Qdrant.
- Backend exploratorio: Qdrant HTTP server 1.16.2.
- Cliente: `qdrant-client` 1.19.1.
- Sistema: Windows 11 Enterprise 64 bits, versión 10.0.26300.
- CPU: Intel Core Ultra 9 275HX, 24 núcleos lógicos informados.
- Python: 3.13.16, entorno Conda `LabMemoryEpic01`.

## Resultado observado con k=5

| Métrica | Resultado |
|---|---:|
| Macro recall@5 | 0.9333 |
| Macro precision@5 | 0.4400 |
| Exactitud de veredicto | 1.0000 (6/6) |
| Primera consulta, con calentamiento | 873.709 ms |
| Media de las cinco consultas posteriores | 40.336 ms |
| Media total | 179.232 ms |

La consulta negativa produjo `NO_MATCH` y todos sus candidatos diagnósticos quedaron como `INSUFFICIENT_EVIDENCE`. El caso q9 declara `require_unique_resolution=true` y produjo `AMBIGUOUS`. Las consultas que buscan múltiples apariciones ya no se marcan ambiguas únicamente por tener scores cercanos.

La precisión se calcula sobre el ranking bruto mostrado. Incluye candidatos diagnósticos aunque individualmente no superen el umbral, para no inflar la métrica ocultando resultados débiles.

## Reconstrucción y temporalidad reales

- La colección lógica apuntó inicialmente a una generación física de 20 puntos.
- `rebuild-index` creó una colección staging, generó y verificó exactamente 20 puntos y cambió el alias lógico.
- La generación anterior se eliminó solamente después del cambio; no quedaron colecciones staging huérfanas.
- El ranking y las métricas fueron idénticos antes y después de la reconstrucción.
- El chunk real `ATLAS-EP-006` conservó `2026-01-01T18:23:18Z`–`18:25:06Z` en el payload tras el cambio.
- Una búsqueda semántica restringida a q3 fue filtrada en Qdrant y devolvió solo `ATLAS-EP-006` y `ATLAS-EP-007`.

## Reproducción

Con un directorio Atlas importado/indexado y una colección exclusiva:

```powershell
$PY = 'C:\Users\sqs.erasmus\.conda\envs\LabMemoryEpic01\python.exe'
& $PY -m scripts.evaluate_atlas_text `
  --data <directorio-datos> `
  --qdrant-collection <coleccion-aislada> `
  --limit 5
```

El JSON de salida incluye ranking, score, veredicto, recall, precision y duración por consulta.

## Bloqueos antes de aceptar H3

1. Alinear las versiones del cliente y servidor HTTP mientras este perfil siga soportado.
2. Validar `require_unique_resolution` sobre un conjunto independiente; Atlas verifica el contrato, pero no debe ser el único corpus de calibración.
3. Añadir pruebas reales de filtro por fuente, aislamiento de revisión/modelo y recuperación del outbox tras fallo.
