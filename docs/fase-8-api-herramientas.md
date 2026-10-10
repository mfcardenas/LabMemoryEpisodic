# Fase 8 — API de herramientas para consultar la memoria

## 1. Resultado

EpiGraph dispone de once herramientas deterministas, tipadas y de solo
lectura. Pueden ejecutarse desde Python o HTTP sin agente y sin LLM. Todas
consumen SQLite mediante los repositorios y servicios de aplicación; cuando
necesitan búsqueda semántica reutilizan `HybridRetrievalService` en lugar de
consultar Qdrant directamente.

No se generan informes, no se construye un orquestador y ninguna herramienta
modifica observaciones, episodios, relaciones o hipótesis.

## 2. Herramientas

| Herramienta | Resultado principal |
|---|---|
| `search_observations` | Observaciones filtradas o semánticamente relacionadas |
| `search_episodes` | Episodios rehidratados con observaciones y evidencias |
| `get_episode` | Detalle canónico completo de un episodio |
| `get_entity_timeline` | Observaciones cronológicas de una identidad validada |
| `get_last_confirmed_observation` | Última observación con estado `validated` |
| `expand_relations` | Recorrido lógico acotado entre nodos |
| `get_supporting_evidence` | Evidencias de observación, episodio, relación, hipótesis, entidad o activo |
| `find_contradictions` | Relaciones `CONTRADICTS`, extremos y evidencias |
| `compare_entities` | Comparación sin crear decisiones de identidad |
| `search_documents` | Fragmentos documentales con página o localizador |
| `get_environment_topology` | Ambientes, fuentes, entidades, episodios y vínculos espaciales |

## 3. Contratos tipados

Las entradas son modelos Pydantic estrictos (`extra="forbid"`). El catálogo
publica su JSON Schema, por lo que un parámetro desconocido produce HTTP 422.

Todas las herramientas devuelven `MemoryToolOutput`, un sobre estable con:

- `result_ids`;
- observaciones, episodios, entidades y relaciones tipadas;
- hipótesis en una colección separada;
- evidencias con `asset_id`, tipo y localizador exacto;
- documentos, topología o comparaciones cuando corresponda;
- `confidence` por registro y confianza agregada cuando existe;
- metadatos con `read_only=true`.

Los hechos tienen `record_kind="fact"`, las hipótesis
`record_kind="hypothesis"` y las evidencias `record_kind="evidence"`. Una
relación conserva `validation_state` e `is_validated`; una relación propuesta
no se presenta como validada.

## 4. Reutilización y decisiones

- `search_episodes` delega en la recuperación híbrida de Fase 7.
- `search_observations` reutiliza sus scores semánticos para observaciones
  pertenecientes a episodios y mantiene una coincidencia léxica determinista
  para observaciones aún no compuestas.
- La resolución de alias usa exclusivamente `SAME_ENTITY_AS` validado.
- La línea temporal y la última observación reutilizan `LogicalGraphService`.
- Las contradicciones y evidencias provienen de las relaciones persistidas.
- `compare_entities` solo informa `validated_same`, `hypothesized_same` o
  `undetermined`; nunca crea una relación o hipótesis.
- `expand_relations` limita la profundidad a cinco saltos.
- Se añadió `list_hypotheses` al puerto y al repositorio SQLite. Es una
  consulta de solo lectura y no requiere migración.

## 5. Ejecución exacta

Desde `LabMemoryEpisodic`:

```powershell
conda activate LabMemoryEpic01
python scripts/run_phase8_demo.py --data .runtime/fase8-demo
```

El script construye el caso de talleres, añade un registro documental en la
página 2, reconstruye la proyección y ejecuta las once herramientas.

Para conservar el corpus y probar la API desde el navegador:

```powershell
epigraph --data .runtime/fase8-demo --fake-encoders serve
```

Abra `http://127.0.0.1:8100/` y pulse **Probar API de herramientas**, o abra
directamente `http://127.0.0.1:8100/docs`.

El catálogo tipado está disponible en:

```text
GET /api/tools
```

Ejemplo PowerShell sin LLM:

```powershell
$body = @{ entity_id = "drill_north_track" } | ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8100/api/tools/get_last_confirmed_observation `
  -ContentType "application/json" `
  -Body $body
```

Ejemplo de consulta documental:

```powershell
$body = @{ query = "numero de serie"; limit = 5 } | ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8100/api/tools/search_documents `
  -ContentType "application/json" `
  -Body $body
```

Para usar Nomic real en un directorio separado:

```powershell
python scripts/run_phase8_demo.py --data .runtime/fase8-real --real-encoders
epigraph --data .runtime/fase8-real serve
```

## 6. Pruebas

Pruebas específicas:

```powershell
python -m pytest -q tests/integration/test_phase8_memory_tools.py --basetemp .runtime/pytest-phase8-target
```

Se prueban de manera independiente las once herramientas, el catálogo de
esquemas, rechazo de campos no declarados, contrato HTTP y ausencia de
mutaciones sobre las observaciones primarias.

Suite completa:

```powershell
python -m pytest -q --basetemp .runtime/pytest-phase8
```

## 7. Caso verificable

El corpus de demostración conserva:

- observaciones del taladro en los talleres norte y sur;
- una observación contradictoria desde una segunda cámara;
- `SAME_ENTITY_AS` validado;
- una hipótesis `POSSIBLY_SAME_ENTITY_AS` almacenada separadamente;
- movimiento inferido con evidencia;
- registro documental localizado en la página 2.

La demostración debe informar `tool_count=11`, `all_read_only=true` y una
salida estructurada para cada herramienta.

## 8. Limitaciones

- La relevancia de observaciones no asociadas a episodios usa coincidencia
  léxica; todavía no tienen una representación vectorial propia.
- La salida HTTP no se persiste como una nueva entidad de dominio.
- La topología es una vista lógica calculada desde SQLite, no un motor de
  grafos adicional.
- No existe todavía planificación de herramientas, memoria de conversación,
  síntesis, informe ni sistema multiagente.

## 9. Cambio propuesto para la siguiente fase

Sin implementarlo aquí: definir un `QueryOrchestrator` determinista que pueda
seleccionar y encadenar estas herramientas según una intención tipada. La
introducción de agentes deberá ser una decisión de una fase posterior y sus
salidas tendrán que conservar los identificadores y evidencias de este
contrato.
