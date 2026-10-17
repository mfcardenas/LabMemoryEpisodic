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

## Escala (Fase S)

`scripts/scale_test.py` sobre corpus sintético (`scripts/synthesize_corpus.py`).
Latencias p50 / p95 en ms de la búsqueda por momentos (sin y con
reordenador) y de `search_moments`; «hash» mide solo la ruta de datos
(vectores de hash, reordenador falso), «real» usa Nomic y jina-v2 en CPU.

| Fecha | Commit | Perfil | Eventos | Pasajes | búsqueda | +reordenador | search_moments | construcción s |
|---|---|---|---|---|---|---|---|---|
| 2026-10-15 | `439f5e0` | hash | 200 000 | 300 281 | 176 / 444 | 172 / 439 | 159 / 440 | 1016 |
| 2026-10-15 | S opt. | hash | 200 000 | 300 281 | 125 / 423 | 133 / 429 | 147 / 430 | — |

Lecciones a 200 k eventos (300 k pasajes, 309 k puntos): el estado del índice
recorría todos los episodios en Python (41 s por comprobación, y el front la
pedía al abrir Consultas) → en SQL, 93 ms; `browse_memory(environments)`
cargaba todos los episodios de cada ambiente (2 s) → una consulta agregada,
57 ms; la vía léxica con OR de prefijos puntuaba cientos de miles de filas
(170–335 ms por consulta) → primero AND, OR solo si no llena candidatos
(p50 125 ms; el p95 sigue en el OR de términos frecuentes). Qdrant con
vectores hash responde en 10–40 ms con filtro; la hidratación en lote, 5 ms.
Construcción: 197 eventos/s con escritura por lotes (antes 60/s).

**Encoders en la GPU** (2026-10-15, `eda4a42`): el perfil real de 50 k iba a
3,6 pasajes/s en CPU (≈5 h); con `onnxruntime-gpu` (extra `[gpu]`) Nomic
rinde 120 textos/s en la RTX 5080 (×15) y el reordenador deja de dominar la
consulta: el banco real pasa de p50 958 ms a **58 ms** (p95 81 ms) con el
mismo 96 %. Sin embargo, la carga sostenida (el perfil real de la prueba de
escala) reinicia el portátil a los pocos minutos, así que la GPU pasa a ser
opt-in (`EPIGRAPH_ENCODER_DEVICE=cuda`; por defecto `cpu`) y ese perfil
queda sin medir en este equipo.

## Resultados

### 2026-10-17 · Fase A3: primeras pruebas reales de la red de agentes

Sin banco compuesto todavía (se prepara después), dos preguntas a mano contra
una copia de los datos (9 episodios) con gpt-oss:latest y encoders en CPU:

| Pregunta | Modo | Resultado | Agentes | Tiempo |
|---|---|---|---|---|
| ¿En qué capturas aparece una persona usando una pantalla o un dispositivo y qué hace en cada una? | auto → red | 8 episodios cubiertos (el del taller, por debajo del umbral), 8 confirmados, cifra correcta, 10 frases verificadas | 11 | 89 s |
| ¿Quién dijo que algo estaba agarrotado y qué pensaba hacer? | auto → agente único | cita correcta, confianza alta | 1 | 12,7 s |

Iteraciones: con el plan heurístico y el foco igual a la pregunta entera, la
cobertura perdía A501 (tablet y tarjeta) frente a A004; el narrador contaba
«10 episodios» (contaba líneas) y el verificador, con solo la descripción del
evento como evidencia, retiraba todas las frases. Corregido con foco extraído,
suelo de relevancia, cifras calculadas para el narrador, evidencia completa
para el verificador y salvaguarda de «no concluyente».

| Fecha | Commit | Modo | momento@1 | momento@5 | episodio@5 | abstención | falsas abst. | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-14 | `d08cace` | passages | 88 % | 88 % | 88 % | 50 % | 0 | 934 | 1008 |
| 2026-10-14 | R1 | passages | 92 % | 92 % | 92 % | 50 % | 0 | 903 | 1068 |
| 2026-10-14 | R2 | passages | 96 % | 96 % | 96 % | 50 % | 0 | 958 | 1076 |
| 2026-10-14 | H | passages | 96 % | 96 % | 96 % | 50 % | 0 | 935 | 1547 |
| 2026-10-15 | `eda4a42` GPU | passages | 96 % | 96 % | 96 % | 50 % | 0 | 58 | 81 |
| 2026-10-14 | `f4ad509` | ask | 48 % | 48 % | 48 % | 100 % | 13 | 4732 | 16482 |
| 2026-10-14 | `a168c37` | ask | 72 % | 72 % | 72 % | 100 % | 7 | 4598 | 11976 |
| 2026-10-15 | A2 final | ask | 88 % | 88 % | 88 % | 100 % | 3 | 3241 | 5034 |

### 2026-10-14 · Fase A2: el agente (modo `ask`)

En modo `ask` lo evaluado es la **cita principal de la respuesta** (no la
lista de candidatos): es más exigente que `passages`, porque el agente debe
leer, decidir y citar. gpt-oss:latest, `think=low`, 2–8 vueltas por pregunta,
4–6 k tokens de media, 3–12 s por pregunta en la RTX 5080.

- Primera pasada (48 %): abstención perfecta (4/4, incluidos los distractores
  que la búsqueda sola no sabe rechazar), 0 citas inventadas, pero 13 falsas
  abstenciones. Causas: el agente convertía la pregunta en frases cortas que
  el umbral del reordenador descartaba; citaba en formatos distintos a
  `[mN]` o volcaba su razonamiento en inglés; dos preguntas murieron por un
  500 de Ollama («error parsing tool call»).
- Correcciones: `search_moments(strict=false)` para el agente (sin umbral:
  él lee y decide), prompt (pregunta tal cual, segunda búsqueda con palabras
  clave antes de abstenerse, cita siempre `[mN]`), normalización de citas y
  limpieza del razonamiento filtrado, `think=<nivel>` para separar el
  razonamiento, reintento ante llamadas mal formadas. Resultado: 72 %.
- Tercera pasada (72 %): el agente recibía solo el fragmento de 24 palabras
  del índice léxico (`snippet`) en lugar del texto del evento; ahora ve el
  texto completo acotado.
- Cuarta pasada (**0 %**, instructiva): al dar al modelo el esquema completo
  de las herramientas rellenaba todos los opcionales (`per_episode=0`,
  `kinds=["speech_segment"]`, `start_time` sin `end_time`) y, con un campo
  `environment_id`, se inventaba el id («env1») y vaciaba todas las
  búsquedas. Decisión: el modelo ve **esquemas reducidos** (solo lo que debe
  decidir; `search_moments` solo `query`), el orquestador fija tamaños y
  umbrales, y un id de ambiente inexistente devuelve un error guiado.
- Quinta pasada (72 %): con 2 momentos por episodio los fragmentos de
  documento desplazaban al evento con el habla; y el presupuesto de 6 000
  caracteres por resultado recortaba cada texto a 300 y cortaba justo la
  tabla «Alimentación 12 V y 2 A» o la segunda frase del técnico.
- Sexta pasada (**88 %**): 3 momentos por episodio, 12 000 caracteres por
  resultado, contexto 32k. Quedan q09 y q23 (abstiene teniendo el momento) y
  q22 (citó «[ m1]» con espacio; ya se acepta). Frente al 96 % de `passages`,
  el agente pierde 8 puntos en recuperación pero gana la abstención con
  distractores (100 % frente a 50 %) y responde en lenguaje natural con citas
  verificadas, 0 inventadas, en 3 s de mediana.

### 2026-10-14 · Fase H: resúmenes jerárquicos

gpt-oss:latest redactó los 9 resúmenes de episodio (11–58 s cada uno,
600–3700 tokens de entrada, 0 citas inválidas), 9 de día y 7 de entorno.
Con los resúmenes como pasajes **dentro** de la búsqueda por momentos el
banco bajó a 92 % y la abstención a 25 %: la prosa generada atrae al
reordenador (q29 «¿llovía en el parque?» devolvía el resumen de otro
episodio) y desplaza al evento exacto en la vía léxica (q02 pasó de
lexical #3 a #4 y perdió el acuerdo entre vías). Decisión: los resúmenes se
indexan pero la búsqueda por momentos no los usa salvo `include_summaries`;
son para que el agente navegue de arriba abajo. Con eso, 96 % de nuevo.
Observación para A2: el evento correcto de q02 puntúa −1,83 en el
reordenador (umbral −1,5): el umbral absoluto y el acuerdo top 3 son frágiles;
la verificación del agente debe sustituirlos como filtro de pertinencia.

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
