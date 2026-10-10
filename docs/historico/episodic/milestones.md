# Hitos comprobables — Fase 1

Todos los comandos se ejecutan desde:

```text
C:\Users\sqs.erasmus\Documents\GitHub\LabMemoryEpisodic
```

con este intérprete:

```powershell
$PY = 'C:\Users\sqs.erasmus\.conda\envs\LabMemoryEpic01\python.exe'
```

## Estado resumido

| Hito | Resultado verificable | Estado |
|---|---|---|
| H0. Entorno y baseline | Suite episódica disponible; faltan integración robot, Git raíz y lock `uv` | 🟡 En curso |
| H1. Ingesta durable | Idempotencia básica probada; faltan schema conectado, rutas confinadas y huérfanos | 🟡 En curso |
| H2. Consistencia canónica | Revalidación y rebuild funcionan; borrado/outbox durable incompleto | 🟡 En curso |
| H3. Recuperación textual real | Nomic y Edge reales; pendientes aislamiento de revisión, outbox y corpus independiente | 🟡 En curso |
| H4. Recuperación visual/multimodal | CLIP real, RRF, separación de evidencias y métricas | Pendiente |
| H5. API y UI segura | Contratos HTTP, límites, medios por ID y recorrido web | 🟡 En curso |
| H6. Candidata de entrega | Matriz completa de 14 criterios y demo reproducible | Pendiente |

## H0 — Entorno y baseline

```powershell
& $PY -m pip check
& $PY -m episodic.app --help
& $PY -m pytest -q
```

Resultado esperado actual:

```text
No broken requirements found.
42 passed, 2 warnings
```

Los warnings proceden de la deprecación del `TestClient` de Starlette y de los índices de payload sin efecto en Qdrant in-memory; no representan fallos funcionales.

## H1 — Ingesta durable e idempotente

Comprobación automática:

```powershell
& $PY -m pytest tests/unit/test_idempotency.py tests/unit/test_sqlite_repo.py tests/integration/test_e2e_flow.py -q
```

Evidencia ya observada en el recorrido CLI:

- Primera importación: 8 fuentes, 1 sesión, 10 episodios, 10 evidencias, 15 observaciones, 7 entidades y 20 representaciones.
- Segunda importación: cero registros nuevos y 10 duplicados omitidos.
- Después de reabrir: 10 episodios y 20 puntos indexados.

## H2 — Consistencia canónica y temporal

```powershell
& $PY -m pytest tests/unit/test_verdict.py tests/unit/test_last_observation.py tests/unit/test_temporal_index.py tests/integration/test_rebuild_safety.py tests/integration/test_qdrant_timeline.py -q
```

Este hito comprueba que:

1. Un punto vectorial obsoleto no hace reaparecer un episodio invisible en SQLite.
2. `last_observation` usa tiempo de captura, no tiempo de ingesta.
3. Un episodio oculto no participa en la última observación.
4. La presencia de tiempos desconocidos devuelve `latest_known` con evidencia insuficiente, no una certeza falsa.
5. Un fallo inyectado durante la reconstrucción conserva el índice anterior.
6. Repetir `rebuild-index` no añade filas duplicadas al outbox compatible.
7. La consulta temporal Atlas q3 devuelve exactamente los episodios 6 y 7 con semántica `[inicio, fin)`.
8. Cada chunk Qdrant registra su línea temporal y su procedencia.
9. El filtro de intervalo se aplica en Qdrant antes del top-k.
10. La reconstrucción usa staging, verifica el recuento exacto y cambia el alias; un fallo elimina staging y conserva la generación activa.

Las colecciones nuevas usan alias desde su creación. Una colección legacy con nombre físico directo requiere una migración única durante su primer rebuild.

## H3 — Recuperación textual real

Estado de entrada:

- Qdrant Edge es el backend predeterminado y ya está validado con modelos reales.
- HTTP permanece como perfil opcional; si se usa, todavía debe alinearse cliente 1.19.1 con servidor 1.16.2.
- Los tres modelos ya están presentes en la caché durable local: Nomic, CLIP text y CLIP vision.

Smoke de encoders reales, sin modificar SQLite ni Qdrant:

```powershell
& $PY -m scripts.verify_real_encoders
```

Resultado esperado: dimensiones `768/768/512/512`; CLIP con norma L2 próxima a 1. Nomic se registra correctamente como vector raw no normalizado y Qdrant aplica cosine al indexarlo.

Evaluación Nomic aislada sobre un corpus Atlas ya importado e indexado:

```powershell
& $PY -m scripts.evaluate_atlas_text `
  --vector-backend edge `
  --data <directorio-datos> `
  --qdrant-collection <coleccion-aislada> `
  --limit 5
```

El informe JSON separa `recall_at_k`, `precision_at_k`, exactitud del veredicto y latencia por consulta. Declara explícitamente `profile=nomic_text_only` y el backend ejecutado. El corpus incluye casos positivos, negativos y ambiguos.

Baseline Edge observado con `k=5`: macro recall `0.9333`, macro precision `0.4400` y exactitud de veredicto `1.0000`, idéntico antes y después del rebuild. Véase [evaluation-text-edge.md](evaluation-text-edge.md). El perfil HTTP histórico está en [evaluation-text-http.md](evaluation-text-http.md).

Próximos puntos de control de H3:

1. Acreditar cambio de revisión/modelo sin mezclar espacios.
2. Probar filtro de fuente y recuperación del outbox tras reinicio con Edge real.
3. Validar la política explícita de resolución única sobre un corpus independiente.

Pruebas de salida previstas:

- Indexación y consulta con Nomic real.
- Filtros de fuente y tiempo aplicados antes del top-k o diagnosticados mediante sobreconsulta.
- Cambio de modelo/revisión sin mezclar espacios.
- Fallo de indexación recuperable mediante outbox.
- Recall@k textual y latencia por etapa registrados sobre `data/atlas/queries.json`.

El hito solo se acepta con modelos y backend reales. Los encoders falsos siguen reservados a comportamiento determinista.

## H4 — Recuperación visual y multimodal

- CLIP vision y CLIP text reales.
- Imagen→imagen y texto→imagen.
- RRF conserva score/rank original y no suma scores incompatibles.
- Texto e imagen de episodios distintos no se presentan como evidencia conjunta.
- Métricas por modalidad y falsos enlaces registradas.

## H5 — API y UI segura

- ✅ API en loopback y UI sobre los mismos servicios que la CLI.
- ✅ Intervalos ISO UTC validados de extremo a extremo; el filtro puede aplicarse a búsquedas semánticas y por intervalo.
- ✅ La UI expone `require_unique_resolution`, muestra los intervalos canónicos y permite filtrar la línea temporal por fuente.
- ✅ Salud y reconstrucción publican backend, colección, generación activa, conteos y verificación.
- ✅ Contrato web cubierto por `tests/integration/test_http_front_contract.py`; suite actual: `42 passed`.
- ✅ `GET /api/project-status` muestra qué está activo, aislado o solo presente como referencia; no etiqueta el RAG ni el robot como integrados.
- ✅ `GET /api/indexing/summary` y la pestaña **Verificación** exponen la outbox y cinco comprobaciones ejecutables contra la API real.
- ✅ La timeline HTTP completa el reloj desde la fuente canónica cuando el intervalo no lo repite dentro de `TimeSpec`.
- 🟡 Evidencia servida por ID; quedan por endurecer MIME/tamaño y confinamiento de rutas de consulta visual.
- 🟡 Paginación HTTP, ocultación de medios borrados y smoke test en navegador pendientes de aceptación.

Comprobación manual controlada:

```powershell
conda activate LabMemoryEpic01
python -m episodic.app serve --host 127.0.0.1 --port 8100
```

Abrir `http://127.0.0.1:8100/` y seguir este recorrido:

1. Entrar en **Verificación**. Debe mostrar memoria episódica `ACTIVE`, RAG legado `ISOLATED` y robot `REFERENCE ONLY` mientras PC-1 no haya integrado este último en la raíz.
2. Si no hay datos, ir a **Gestión & Corpus**, pulsar **Importar Manifiesto Atlas** y después **Procesar Outbox Qdrant**.
3. Volver a **Verificación** y pulsar **Ejecutar comprobaciones**. Con Atlas importado, evidencia disponible y la outbox drenada, los cinco controles deben quedar en `CORRECTO` y el checkpoint en `VALIDADO`.
4. Abrir **Línea Temporal** y confirmar que los episodios muestran intervalos UTC y reloj de captura.
5. En **Búsqueda Multimodal**, ejecutar una búsqueda sin intervalo y después activar **Filtrar por tiempo** con `2026-01-01 18:00:00`–`18:45:00` UTC; abrir un resultado y comprobar la evidencia.
6. Opcionalmente, en **Gestión & Corpus**, reconstruir el índice y comprobar que cambia la generación activa y queda marcada como verificada. Esta última operación sí es mutante y solicita confirmación.

La consola de verificación es observabilidad funcional, no sustituye las pruebas automatizadas ni marca como superada la integración de `robot/`.

## H6 — Candidata de entrega

- Catorce criterios de aceptación trazados a pruebas.
- Recorrido `import → index → search → evidence → enrich → restart → query → rebuild` documentado.
- Diferencia clara entre tests con doubles y validación real.
- Resultados, bloqueos, versiones, hardware y limitaciones registrados.
