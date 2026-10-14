# Evaluación de la consulta

Banco: `data/eval/preguntas.jsonl` (29 preguntas sobre las capturas reales
`env_context_A001…A501` del 2026-10-12; 25 con respuesta y 4 de abstención).
Se ejecuta con `scripts/evaluate_questions.py` (en proceso con los modelos
reales y el Qdrant de `.env`, o `--url` contra el servidor). Cada ejecución
deja el detalle en `data/eval/resultados-<fecha>-<modo>.json`.

Métricas:

- **momento@k**: el episodio esperado aparece entre los k primeros resultados
  y, si la pregunta fija una ventana, el pasaje solapa con ella (±5 s). Los
  pasajes de documento no tienen posición: basta el episodio.
- **episodio@5**: todos los episodios esperados están entre los cinco primeros.
- **abstención**: en las preguntas sin respuesta, no se devuelve ningún momento
  (modo `passages`) o el agente responde «no consta» (modo `ask`).
- **falsas abst.**: preguntas con respuesta que no devolvieron nada.
- latencia p50/p95 en ms de la consulta completa (codificación + búsqueda +
  reordenado).

Plan de fases y objetivos: `plan-agentes-consulta.md`.

## Resultados

| Fecha | Commit | Modo | momento@1 | momento@5 | episodio@5 | abstención | falsas abst. | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-14 | `d08cace` | passages | 88 % | 88 % | 88 % | 50 % | 0 | 934 | 1008 |
| 2026-10-14 | R1 | passages | 92 % | 92 % | 92 % | 50 % | 0 | 903 | 1068 |
| 2026-10-14 | R2 | passages | 96 % | 96 % | 96 % | 50 % | 0 | 958 | 1076 |

### 2026-10-14 · Fase R2: comprensión de la consulta y vecindad

`parse_query` convierte las expresiones temporales en intervalo absoluto
(zona horaria del sistema; `EPIGRAPH_QUERY_TIMEZONE` para fijarla) y detecta
la intención; las preguntas de antes/después traen los eventos vecinos.
Acierta ya q25 (fecha y hora). Dos lecciones del banco:

- «¿Quién se comprometió a terminar la presentación **antes de las 6**?» se
  tomó como filtro horario y vació la búsqueda → una hora sin día no es
  filtro (sigue buscándose como texto).
- Con intervalo, «¿qué capturas se hicieron…?» no describe ningún pasaje y el
  reordenador descartaba los únicos candidatos → con intervalo el reordenador
  ordena pero no descarta.

Pendientes: q05 (Mobil), q27/q28 (abstención con distractor → A2).

### 2026-10-14 · Fase R1: el evento como pasaje

Índice reconstruido con formato v2 (40 pasajes: 16 eventos, frases sueltas y
fragmentos de documento). Aciertan ya q15 (texto visible), q13/q14/q17
(habla adherida al evento). Lección: con el texto del evento completo el
reordenador descartaba los 20 candidatos de las preguntas sobre el habla
(solo lee 800 caracteres y el habla iba al final); se reordenó el texto
(título, resumen, habla, texto visible, objetos, acciones, actores y después
la descripción larga) y pasaron de 76 % a 92 %. Pendientes: q05 (Mobil: la vía
léxica lo encuentra, el reordenador lo descarta), q25 (fecha → R2), q27/q28
(abstención con distractor → A2).

### 2026-10-14 · línea base (Fase 0)

Búsqueda por pasajes (Nomic + BM25 + reordenador jina-v2, umbral −1,5,
acuerdo top 3). Corpus pequeño (9 episodios, 16 eventos, 53 pasajes), así que
el acierto es alto; lo relevante son los fallos, que coinciden con el
diagnóstico del plan:

| Pregunta | Fallo | Fase que lo cubre |
|---|---|---|
| q05 Mobil, q15 «Marketing: From Trade to Tech» | el texto visible (`visible_text`) no se indexa | R1 |
| q25 capturas del 12/10 después de las 14:30 | no se interpretan fechas ni horas | R2 |
| q27 coche rojo, q28 factura del taller | el distractor (taller, coche plateado) pasa el umbral; sin verificación de que el pasaje responda | A2 (verificación de citas y abstención) |

Por tipo (momento@5): antes/después 100 %, documento 100 %, qué 100 %,
quién 100 %, cuándo 75 %, dónde 50 %.
