# Fase 7 — recuperación híbrida sobre la memoria

## 1. Resultado

La recuperación opera sobre la memoria canónica construida en las fases
anteriores y combina cuatro niveles deterministas:

1. filtros estructurados revalidados en SQLite;
2. similitud semántica con el encoder Nomic y la proyección Qdrant existentes;
3. solapamiento temporal y última observación confirmada;
4. expansión por las relaciones lógicas persistidas en Fase 6.

No se genera una respuesta en lenguaje natural y no intervienen agentes o
LLM. Un resultado no es un fragmento: contiene el `Episode`, sus
`Observation`, sus `EvidenceReference` y las `Relation` que justifican una
expansión.

## 2. Contrato de recuperación

`HybridSearchFilters` implementa semántica AND entre los grupos solicitados:

- ambiente;
- fuente;
- modalidad;
- intervalo temporal;
- entidad, incluyendo alias unidos por `SAME_ENTITY_AS` validado;
- tipo de observación;
- tipo de episodio, almacenado en `Episode.metadata.episode_type`;
- confianza mínima;
- estado de validación.

`RetrievalScores` mantiene cuatro valores independientes: `structured`,
`semantic`, `temporal` y `relational`. `None` significa que ese nivel no
participó. No se calcula una suma opaca. El orden directo conserva el ranking
semántico si existe; sin consulta semántica usa el orden canónico reproducible
de SQLite. Los resultados expandidos se incorporan después de los directos.

Cada `RelationExpansion` expone la relación completa, sus evidencias y
`is_validated`. Una coincidencia semántica solo añade
`semantic_similarity`; nunca se convierte en una relación validada.

## 3. Indexación reutilizada

Se conserva la arquitectura de outbox, el puerto `VectorIndex`, Qdrant Edge
predeterminado, el perfil HTTP opcional y los encoders FastEmbed existentes.
El texto derivado de un episodio incorpora ahora:

- título y `indexable_text`;
- descripciones de sus observaciones;
- atributos estructurados serializados de manera estable.

El payload Qdrant conserva los identificadores de episodio, observación y
evidencia, junto con ambiente, fuente, modalidad, entidad, tipos, confianza,
validación y línea temporal. SQLite continúa siendo la fuente de verdad y
revalida todos los filtros después del top-k.

Después de actualizar desde una fase anterior debe reconstruirse la
proyección para obtener el texto y payload enriquecidos.

## 4. Verificación desde el frontend

El frontend existente usa `POST /api/hybrid-search` para búsqueda semántica,
intervalo y última observación. Permite seleccionar los nueve filtros,
activar expansión relacional y limitar tipos de relación. Cada tarjeta muestra
por separado:

- las cuatro puntuaciones;
- episodio y observaciones canónicas;
- referencias de evidencia;
- relaciones y su estado de validación;
- si el resultado fue directo o expandido.

El endpoint legado `POST /api/search` permanece disponible para conservar
compatibilidad y la vista de línea temporal.

## 5. Ejecución exacta

Desde `LabMemoryEpisodic`, con el entorno activado por el usuario:

```powershell
conda activate LabMemoryEpic01
python scripts/run_phase7_demo.py --data .runtime/fase7-demo --k 5
```

La demostración usa encoders deterministas para aislar la lógica y deja
SQLite y Qdrant Edge preparados para inspección visual:

```powershell
epigraph --data .runtime/fase7-demo --fake-encoders serve
```

Abra `http://127.0.0.1:8100/`, entre en **Búsqueda** y pruebe, por ejemplo:

- entidad `drill_north_track` y validación `validated`;
- ambiente `workshop_south`;
- modalidad `video` y tipo `tool_presence`;
- expansión con `BEFORE,AFTER` o `CONTRADICTS`.

Para comprobar el encoder Nomic real use otro directorio, evitando mezclar
representaciones de perfiles distintos:

```powershell
python scripts/run_phase7_demo.py --data .runtime/fase7-real --real-encoders --k 5
epigraph --data .runtime/fase7-real serve
```

## 6. Consultas y métricas

El script ejecuta las cinco consultas requeridas:

1. dónde se observó el taladro azul;
2. cuál fue su última observación confirmada;
3. qué ocurrió antes y después del inventario;
4. si apareció en otro ambiente;
5. si existen observaciones contradictorias.

En la ejecución controlada del 6 de octubre de 2026 con perfil
`fake_deterministic` y `k=5` se observó:

| Métrica | Resultado |
|---|---:|
| Recall@5 macro | 1.0 |
| MRR | 1.0 |
| Latencia media | 3.181 ms |
| Resultados sin evidencia | 0 |
| Exactitud del último evento | 1.0 |

La latencia depende del equipo, backend, estado de caché y encoder. El perfil
determinista valida integración y ranking filtrado, no calidad lingüística; la
calidad semántica real debe medirse con `--real-encoders` y un corpus mayor.

También se ejecutó el mismo protocolo con `real_nomic`: `Recall@5=1.0`,
`MRR=1.0`, cero resultados sin evidencia, exactitud del último evento `1.0` y
latencia media observada `109.574 ms`, incluyendo la primera codificación en
frío. Para la pregunta «Dónde se observó el taladro azul», Nomic asignó
`0.7752` al episodio norte y `0.7729` al episodio sur; ambos fueron
rehidratados con su evidencia.

## 7. Pruebas

Pruebas específicas:

```powershell
python -m pytest -q tests/integration/test_phase7_hybrid_retrieval.py tests/unit/test_evaluation.py --basetemp .runtime/pytest-phase7-target
```

Suite completa:

```powershell
python -m pytest -q --basetemp .runtime/pytest-phase7
```

Los tests verifican los nueve filtros, el texto indexado, la rehidratación de
evidencias, puntuaciones separadas, identidad validada entre ambientes,
anterior/posterior, contradicciones, última observación y contrato HTTP del
frontend.

## 8. Decisiones y limitaciones

- No se introdujo otra base de datos ni un motor de grafos.
- El RAG documental legado continúa aislado porque devuelve chunks sin
  revalidarlos contra la memoria canónica.
- Las listas alternativas dentro de un filtro usan OR; grupos distintos usan
  AND.
- La expansión actual recorre una relación desde episodios, observaciones o
  entidades del resultado directo. No realiza búsqueda ilimitada por
  múltiples saltos.
- `episode_type` sigue siendo metadato compatible; convertirlo en atributo de
  primer nivel exigiría una decisión de modelo de dominio posterior.
- Las evidencias de Fases 2–7 se presentan como `EvidenceReference`. Los
  thumbnails del modelo `Evidence` legado no sustituyen esas referencias.
- No hay generación de respuesta, planificación de consulta ni agentes en
  esta fase.

## 9. Cambio propuesto para la siguiente fase

Sin implementarlo aquí: definir herramientas de consulta estables sobre
`HybridRetrievalService` y, solo después, un orquestador que seleccione esas
herramientas. Cualquier agente deberá consumir resultados rehidratados y
citables, nunca Qdrant directamente.
