# EpiGraph — Fase 2: modelo de dominio de memoria episódica

Fecha de cierre técnico: 2026-10-04  
Estado: **IMPLEMENTADA Y VERIFICADA; pendiente de aceptación del usuario**

## 1. Resumen de lo implementado

Se incorporó el modelo canónico y su persistencia SQLite inicial para las
trece entidades requeridas:

| Capa | Entidades |
|---|---|
| Procedencia | `Environment`, `Source`, `Asset`, `EvidenceReference` |
| Procesamiento | `ProcessingRun`, `ProcessingWindow`, `Observation`, `Event` |
| Identidad | `Entity` |
| Composición | `MicroEpisode`, `Episode` |
| Interpretación | `Relation`, `Hypothesis` |

Los IDs nuevos son UUID globales. El fixture usa UUIDv5 deterministas para
mantener IDs estables entre ejecuciones. Todos los timestamps nuevos exigen
zona horaria; los intervalos canónicos usan `start_time` y `end_time`.

`Observation`, `Event` y `Episode` son clases y tablas distintas.
`ProcessingWindow` no hereda de `Episode`. `Relation` e `Hypothesis` tienen
tipos, tablas y ciclos de vida separados.

La migración SQLite v2:

- crea las tablas nuevas;
- añade ambiente y metadatos de producción a fuentes;
- añade tiempo, ambientes y microepisodios a episodios;
- extiende entidades persistentes;
- reconstruye `observations` conservando los registros v1 y haciendo
  `episode_id` opcional;
- crea un ambiente explícito de compatibilidad para fuentes previas;
- aplica esquema y registro de versión en una sola transacción.

## 2. Archivos creados o modificados

### Creados

- `episodic/domain/model.py`
- `episodic/domain/provenance.py`
- `episodic/domain/processing.py`
- `episodic/domain/episodes.py`
- `episodic/domain/relations.py`
- `episodic/examples/__init__.py`
- `episodic/examples/pink_pipette.py`
- `scripts/load_pink_pipette_example.py`
- `tests/unit/test_phase2_domain.py`
- `tests/integration/test_phase2_persistence.py`
- `docs/fase-2-modelo-dominio.md`

### Modificados

- `episodic/domain/entities.py`
- `episodic/domain/types.py`
- `episodic/domain/__init__.py`
- `episodic/ports/repository.py`
- `episodic/adapters/sqlite/migrations.py`
- `episodic/adapters/sqlite/repository.py`
- `episodic/application/ingest.py`
- `README.md`

## 3. Componentes existentes reutilizados

- SQLite continúa como fuente canónica; no se añadió otra base de datos.
- `SqliteEpisodicRepository` sigue siendo el repositorio único.
- `TimeSpec`, `Interval` y el flujo de episodios v1 se conservan para
  compatibilidad; los campos canónicos nuevos son `start_time`/`end_time`.
- `Source`, `Observation`, `Entity` y `Episode` existentes se adaptaron en
  lugar de duplicarlos.
- El bootstrap, la API y el frontend anteriores siguen funcionando.
- La ingesta Atlas ahora genera correctamente UUID cuando el manifiesto no
  proporciona uno.

## 4. Decisiones técnicas tomadas

1. La migración es aditiva salvo la reconstrucción controlada de
   `observations`, necesaria para retirar el `episode_id NOT NULL` legado.
2. Un `Observation` puede existir antes de componerse en un episodio.
3. `Asset` conserva una URI original y no puede sobrescribirse con contenido
   o procedencia diferentes para el mismo ID.
4. `EvidenceReference` localiza archivo completo, intervalo audiovisual,
   fotograma, región, página o fragmento textual mediante campos validados.
5. Las relaciones usan referencias tipadas y el repositorio rechaza origen,
   destino o evidencias inexistentes antes de escribir.
6. Una hipótesis nunca se guarda en `relations`, aunque tenga evidencias o
   confianza alta.
7. La serialización añade `kind`; la deserialización rechaza reconstruir una
   entidad con un tipo distinto. Esto prueba explícitamente que una ventana no
   se convierte en episodio.
8. No se creó un grafo: las relaciones están en SQLite y pueden migrarse o
   proyectarse más adelante sin alterar los hechos primarios.

## 5. Comandos exactos para ejecutar

Con el entorno gestionado y activado por el usuario, desde la raíz:

```powershell
python -m pytest -q
```

Crear los datos reproducibles de la pipeta rosada:

```powershell
python scripts/load_pink_pipette_example.py --data .runtime/fase2-pink-pipette
```

Comprobar el episodio y su observación desde el frontend existente:

```powershell
epigraph --data .runtime/fase2-pink-pipette --fake-encoders serve
```

Abrir `http://127.0.0.1:8100/`, entrar en **Explorador de Episodios** y abrir
**Presencia de la pipeta rosada en el laboratorio**.

La apertura de cualquier SQLite v1 mediante `SqliteEpisodicRepository` aplica
automáticamente la migración v2. No es necesario ejecutar un comando de
migración separado.

## 6. Pruebas ejecutadas y resultados

Resultados antes del cierre documental:

| Comprobación | Resultado |
|---|---|
| Suite completa | `63 passed`, 2 warnings, 4.51 s |
| Pruebas específicas Fase 2 | `17 passed` |
| Migración v1 → v2 con observación previa | Aprobada; versiones `{1, 2}` |
| Round-trip de las 13 entidades | Aprobado |
| Seis tipos de `EvidenceReference` | Aprobados |
| Relación con destino inexistente | Rechazada, sin escritura parcial |
| Hipótesis frente a relación validada | Tablas y estados separados |
| Fixture ejecutado dos veces | Correcto e idempotente |
| Frontend sobre fixture | Salud `ok`, 3 fuentes, 1 entidad, 1 episodio |
| Detalle desde API/frontend | 1 observación enlazada; HTTP `200` |

Los warnings ya conocidos son la deprecación de `TestClient` y el aviso de
índices de payload al usar Qdrant local en una prueba no relacionada con este
modelo.

## 7. Limitaciones pendientes

- El frontend existente muestra el episodio compuesto y su observación, pero
  todavía no presenta ambientes, activos, ventanas, relaciones o hipótesis.
  Añadir esas vistas sería una fase de interfaz, no de dominio/persistencia.
- Las referencias genéricas de `Relation` se validan en el repositorio porque
  SQLite no puede expresar una clave foránea polimórfica nativa.
- Las listas de referencias se guardan inicialmente como JSON y se validan al
  escribir. Una futura necesidad de consultas masivas podría justificar
  tablas puente, sin cambiar el contrato del dominio.
- `Interval` permanece como compatibilidad del flujo v1; no debe usarse en
  entidades nuevas en lugar de `start_time`/`end_time`.
- Los URI del fixture son referencias sintéticas; no contienen medios reales.
- No se implementaron procesadores multimodales, agentes, grafo ni indexación
  de las nuevas entidades.

## 8. Cambios propuestos para la siguiente fase — no implementados

No se inicia ninguna fase posterior. El siguiente incremento deberá ser
definido y autorizado por el usuario. Una continuación coherente sería la
ingesta primaria de activos reales y la creación determinista de
`ProcessingRun`/`ProcessingWindow`, manteniendo las evidencias exactas y sin
componer episodios automáticamente hasta que se solicite.
