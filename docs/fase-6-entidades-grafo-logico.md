# EpiGraph — Fase 6: resolución de entidades y grafo lógico

Fecha del punto de control: 2026-10-06  
Estado: **COMPLETA Y VERIFICADA**

## 1. Resumen de lo implementado

Se implementó un grafo lógico sobre SQLite utilizando las entidades,
observaciones, episodios, relaciones, hipótesis y evidencias existentes.
No se incorporó una base de grafos.

El vocabulario controlado contiene `OBSERVED_IN`, `BEFORE`, `AFTER`,
`OVERLAPS`, `LOCATED_IN`, `POSSIBLY_SAME_ENTITY_AS`, `SAME_ENTITY_AS`,
`MOVED_TO`, `SUPPORTS`, `CONTRADICTS`, `DERIVED_FROM` y `REVIEWED_BY`.

`POSSIBLY_SAME_ENTITY_AS` se persiste exclusivamente como `Hypothesis`.
`SAME_ENTITY_AS` solo admite estado `validated`. La resolución crea una vista
transitiva de alias confirmados, pero conserva cada `Entity` y cada
`Observation` original sin reescribirlas.

## 2. Archivos creados o modificados

### Creados

- `episodic/application/logical_graph.py`
- `episodic/examples/phase6_maintenance.py`
- `scripts/run_phase6_demo.py`
- `tests/integration/test_phase6_logical_graph.py`
- `docs/fase-6-entidades-grafo-logico.md`

### Modificados

- `episodic/domain/types.py`
- `episodic/domain/entities.py`
- `episodic/domain/relations.py`
- `episodic/ports/repository.py`
- `episodic/adapters/sqlite/migrations.py`
- `episodic/adapters/sqlite/repository.py`
- `episodic/bootstrap.py`
- `tests/integration/test_phase2_persistence.py`
- `README.md`
- `docs/episodic/reuse-map.md`

No se modificaron dependencias.

## 3. Componentes existentes reutilizados

- `Entity`, `Observation`, `Episode`, `Relation`, `Hypothesis` y
  `EvidenceReference` continúan siendo los modelos canónicos.
- Las tablas `entities`, `relations` e `hypotheses` existentes almacenan el
  grafo. SQLite conserva la fuente de verdad.
- El bootstrap ligero expone `LogicalGraphService` sin arrancar Qdrant,
  modelos ni agentes.
- La paginación existente de observaciones por entidad se reutiliza para
  recorrer una identidad validada y sus alias.

No se reutilizó el agente RAG ni código del robot: la resolución primaria es
determinista y no requiere agentes.

## 4. Decisiones técnicas

1. La migración SQLite v5 añade `Observation.validation_state`, necesario para
   distinguir observaciones propuestas, validadas y rechazadas.
2. Las relaciones usan tipos en mayúsculas y referencias a nodos existentes.
3. Una hipótesis de identidad puede ser abierta, soportada o rechazada, pero
   nunca aparece como una relación validada.
4. `SAME_ENTITY_AS` crea una equivalencia consultable y transitiva; no fusiona
   filas ni cambia el `entity_id` de observaciones previas.
5. `MOVED_TO` exige `transition_mode=observed|inferred`. Las transiciones
   inferidas requieren evidencia.
6. Toda relación con `derivation=inferred` y todo `DERIVED_FROM` exige método y
   referencias de evidencia.
7. Una contradicción enlaza los registros incompatibles y conserva ambas
   observaciones.
8. Los huecos se representan en una relación temporal mediante
   `metadata.observation_gap`, con inicio, fin, duración y motivo.
9. El recorrido `BEFORE`/`AFTER` es transitivo, evita ciclos y admite
   `max_depth`; `OVERLAPS` se devuelve como contexto directo.

## 5. Consultas y comandos exactos

Las seis consultas están disponibles en `LogicalGraphService`:

- `observations_for_entity(entity_id)`
- `relations_for_episode(episode_id)`
- `traverse_episodes(episode_id, max_depth=None)`
- `contradictions(node_type=None, node_id=None)`
- `evidence_for_relation(relation_id)`
- `last_confirmed_observation(entity_id)`

Demostración reproducible:

```powershell
conda activate LabMemoryEpic01
cd C:\Users\sqs.erasmus\Documents\GitHub\LabMemoryEpisodic

python scripts/run_phase6_demo.py --data .runtime/fase6-demo
```

Pruebas:

```powershell
python -m pytest -q
```

La base de demostración queda en `.runtime/fase6-demo/episodic.db`.

## 6. Pruebas ejecutadas y resultados

- Pruebas específicas de Fase 6: `5 passed`.
- Suite completa en Python 3.13.16: `85 passed, 2 warnings`.
- Demostración real del flujo: `exit code 0`.
- Persistencia demostrada: 2 ambientes, 4 entidades, 4 episodios, 4
  observaciones, 5 evidencias, 11 relaciones y 1 hipótesis de identidad.
- Los 12 tipos están representados: 11 como relaciones y
  `POSSIBLY_SAME_ENTITY_AS` como hipótesis.
- La última observación confirmada del taladro se resuelve en el taller sur y
  devuelve también su contradicción y evidencia.
- Se verificó que la resolución no modifica las observaciones originales.
- Se verificaron invariantes, recorrido temporal transitivo y hueco de 1.200 s.

Las dos advertencias preexistentes corresponden a Starlette y a los payload
indexes sin efecto en Qdrant local.

## 7. Limitaciones pendientes

- La propuesta de candidatos de identidad recibe entidades y evidencias ya
  seleccionadas; no ejecuta reconocimiento visual ni similitud automática.
- Las relaciones del escenario se construyen de forma explícita. Todavía no
  existe un constructor general que derive todas las relaciones de Fase 5.
- El recorrido se ejecuta sobre consultas SQLite y estructuras en memoria,
  apropiadas para la ejecución local actual; no se ha evaluado a gran escala.
- No se indexa el grafo en Qdrant.
- No se añadió una vista frontend ni endpoints HTTP nuevos en esta fase.

## 8. Cambios propuestos para la siguiente fase, sin implementar

- Exponer el grafo lógico y sus evidencias en una vista de revisión humana.
- Derivar relaciones temporales desde episodios de Fase 5 mediante reglas
  versionadas.
- Añadir un flujo de aceptación/rechazo de hipótesis de identidad.
- Medir el rendimiento de SQLite antes de considerar otra tecnología.

Ninguno de estos cambios se ha implementado.
