# EpiGraph — Fase 5: construcción determinista de microepisodios

Fecha del punto de control: 2026-10-06  
Estado: **COMPLETA Y VERIFICADA**

## 1. Resumen de lo implementado

Se incorporó la composición determinista:

```text
Observation -> MicroEpisode -> Episode
```

`DeterministicEpisodeComposer` ordena por tiempo absoluto e ID estable, nunca
por el número de ventana. Las reglas contemplan proximidad temporal, fuentes
iguales o sincronizadas, continuidad de entidad, compatibilidad de acción y
estado, cambio significativo de escena, entrada/salida, inicio/final de una
interacción, evento acústico relevante y contradicción explícita.

Cada decisión queda almacenada en `metadata.grouping_decisions`, junto con las
reglas que abrieron o cerraron cada microepisodio. La agregación a `Episode`
registra sus propias `aggregation_decisions`.

Los IDs son UUIDv5 derivados de las observaciones, la configuración y el
ámbito. Repetir el proceso da la misma estructura. Cambiar reglas reconstruye
la proyección y retira exclusivamente los resultados obsoletos del compositor.

## 2. Archivos creados o modificados

### Creados

- `episodic/application/episode_composition.py`
- `episodic/examples/phase5_scenarios.py`
- `scripts/run_phase5_demo.py`
- `data_samples/phase5_observations.json`
- `data_samples/phase5_rules.json`
- `tests/integration/test_phase5_episode_composition.py`
- `docs/fase-5-construccion-determinista-episodios.md`

### Modificados

- `episodic/ports/repository.py`
- `episodic/adapters/sqlite/repository.py`
- `episodic/bootstrap.py`
- `episodic/interfaces/cli/commands.py`
- `README.md`
- `docs/episodic/reuse-map.md`

No fue necesaria una migración: los modelos y tablas de Fase 2 ya soportaban
la información requerida.

## 3. Componentes existentes reutilizados

- `Observation`, `MicroEpisode` y `Episode` son los modelos canónicos ya
  existentes; no se crearon equivalentes paralelos.
- SQLite sigue siendo la fuente de verdad.
- `EvidenceReference` se propaga sin copiar ni sustituir la evidencia.
- El contenedor ligero y la CLI se ampliaron; no se inicializan Qdrant ni
  modelos para componer episodios.
- `ProcessingWindow` solo se conserva como trazabilidad de la observación. Su
  `sequence_number` no participa en reglas, ordenación ni IDs.

No se reutilizó ningún agente: la arquitectura original no contiene un
compositor determinista adecuado y un agente sería contrario al requisito.

## 4. Decisiones técnicas

1. Las reglas se representan mediante `EpisodeRuleConfig`, serializable desde
   JSON y validada antes de procesar.
2. Una regla de frontera crea un microepisodio nuevo; las reglas de continuidad
   quedan registradas observación por observación.
3. Una acción puede atravesar ventanas porque se evalúan tiempos, fuente,
   entidad y acción, no el ID o número de ventana.
4. Acciones incompatibles se separan aunque compartan ventana.
5. Las fuentes distintas solo continúan juntas cuando se declaran
   sincronizadas.
6. Una contradicción no se convierte en hecho ni hipótesis: permanece como
   observación independiente, marcada y agregada al episodio que le da contexto.
7. Una observación sin evidencia no se descarta y produce un microepisodio con
   `evidence_reference_ids=[]`.
8. La reconstrucción es transaccional y solo elimina episodios con el productor
   `phase5-deterministic-composer` dentro del ambiente reconstruido.
9. No se añadió frontend: esta fase estabiliza primero la estructura canónica y
   expone una comprobación reproducible por CLI.

## 5. Comandos exactos para ejecutar

Demostración autocontenida con los cinco casos obligatorios:

```powershell
conda activate LabMemoryEpic01
cd C:\Users\sqs.erasmus\Documents\GitHub\LabMemoryEpisodic

python scripts/run_phase5_demo.py --data .runtime/fase5-demo
```

Reconstruir posteriormente desde las observaciones ya persistidas:

```powershell
epigraph compose `
  --data .runtime/fase5-demo `
  --environment laboratorio_fase5 `
  --rules data_samples/phase5_rules.json
```

Usar reglas predeterminadas sobre otro ambiente:

```powershell
epigraph compose --data <directorio> --environment <environment_id>
```

Pruebas:

```powershell
python -m pytest -q
```

## 6. Pruebas ejecutadas y resultados

- Pruebas específicas de Fase 5: `5 passed`.
- Suite completa en Python 3.13.16: `80 passed, 2 warnings`.
- Demostración: `exit code 0`, 10 observaciones, 8 microepisodios y 1 episodio.
- Acción a través de dos ventanas: un solo microepisodio con dos ventanas.
- Dos acciones en una ventana: dos microepisodios distintos.
- Dos cámaras sincronizadas: un microepisodio común.
- Intervalo sin evidencia: conservado con lista de evidencias vacía.
- Contradicción: frontera trazada y marca propagada al episodio.
- Numeraciones de ventana completamente diferentes producen los mismos IDs y
  las mismas agrupaciones.
- Repetición idempotente y reconstrucción con otra configuración verificadas.

Las advertencias preexistentes corresponden a una deprecación de Starlette y
a los payload indexes sin efecto en Qdrant local.

## 7. Limitaciones pendientes

- Las reglas consumen atributos normalizados (`action`, `state`,
  `entity_transition`, `interaction_phase`, `scene_change_score` y
  `acoustic_relevance`). Los procesadores actuales aún no generan todos ellos.
- La sincronización de fuentes se declara en configuración; esta fase no estima
  desfases de reloj.
- La contradicción debe venir declarada en la observación. Su detección
  automática pertenece a una fase posterior.
- Los episodios construidos no se indexan automáticamente en Qdrant.
- Todavía no existe una vista frontend para inspeccionar microepisodios y sus
  reglas.

## 8. Cambios propuestos para la siguiente fase, sin implementar

- Definir una vista de revisión de `Episode -> MicroEpisode -> Observation ->
  EvidenceReference` en el frontend.
- Decidir si la siguiente fase crea eventos normalizados o indexa los episodios
  deterministas para recuperación.
- Incorporar calibración de relojes antes de inferir sincronización de fuentes.

Ninguno de estos cambios se ha implementado.
