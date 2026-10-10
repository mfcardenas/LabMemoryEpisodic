# Prompt para Codex: evolución de sc-on-device-ai-memory a Memory Episodic Project

Actúa como responsable de arquitectura e implementación de Memory Episodic Project. Trabaja sobre el repositorio existente, inspecciona su código y construye la primera entrega funcional. Tu trabajo incluye implementar, ejecutar comprobaciones, documentar y dejar cambios revisables. Un plan sin código no satisface este encargo.

## 1. Repositorio y material de referencia

Repositorio base está en la carpeta sc-on-device-ai-memory

No trabajes sobre él, solo revisa su estructura y reutiliza el contenido que necesites. Muevelo al proyecto principal en la carpeta LabMemoryEpisodic.

Material complementario, adjunto en la carpeta sc-on-device-ai-memory: `helper.py`, `Lesson3.ipynb`, `Lesson4.ipynb`, `Lesson5.ipynb`, `Appendix (1).ipynb`, `Leeson All.txt` y `Plan_Integracion_Memory_Episodic_Project.md`. Los notebooks explican patrones de recuperación; el código ejecutable prevalece sobre comentarios y outputs guardados. Los requisitos de este prompt prevalecen sobre las restricciones de hardware de los documentos anteriores.

## 2. Objetivo del sistema

Construir una memoria episódica multimodal para conservar, indexar y consultar fotografías, vídeos, audio, transcripciones y anotaciones de varias fuentes. El sistema debe recuperar elementos concretos, reconstruir intervalos, enriquecer episodios y contrastar información entre escenarios y cámaras.

Los casos de uso incluyen revisar un conjunto fotográfico, encontrar un detalle inicialmente no etiquetado, localizar apariciones de una entidad y relacionar acontecimientos de cámaras diferentes. Mantén el dominio general: no lo limites a inventario doméstico, robótica ni investigación de un sector concreto.

Plataforma inicial: PC. CPU debe bastar para importar episodios preparados, persistir y consultar. GPU es una capacidad opcional del perfil. Jetson, Raspberry Pi y otros dispositivos orientan la separación de runtimes y presupuestos; su despliegue no forma parte de esta entrega.

Los agentes explotarán la memoria preparada mediante herramientas de consulta y verificación. Ingesta, percepción, embeddings, ASR, OCR y consolidación son servicios de procesamiento. No uses un agente para sustituir un pipeline determinista ni introduzcas un framework agéntico en la primera fase.

## 3. Estrategia de evolución y compatibilidad

Evoluciona este repositorio mediante un monolito modular. Conserva su separación entre lógica y dispositivo: `robot/device` accede a `robot/brain`; la lógica no depende de cámara, HTTP, HTML o micrófono.

Añade inicialmente un paquete `episodic/` junto a `robot/`. Mantén el comando y la demo actuales del robot. Evita una reescritura de todos los módulos y la creación de una aplicación independiente que copie el repositorio sin integrarlo.

La transición sigue este orden:

1. Añadir el dominio episódico y sus servicios, usando adaptadores sobre componentes existentes.
2. Extraer utilidades compartidas cuando dos consumidores las necesiten y las pruebas permitan hacerlo.
3. Incorporar el detector y la captura como productores de observaciones del nuevo dominio.
4. Migrar datos y consumidores antiguos mediante operaciones explícitas y verificables.

Mientras convivan los modos, utiliza directorios separados: el modo legado conserva sus datos originales; la aplicación episódica usa su propia memoria canónica. Dentro de la nueva aplicación habrá una fuente de verdad y sus índices derivados. No mantengas dos memorias canónicas sincronizadas informalmente.

Reutilizar implica llamar, adaptar o extraer código útil, conservando atribución. No cuenta como reutilización dejar imports sin uso, copiar módulos enteros con sus problemas o envolver `Robot.ask` y presentarlo como recuperación episódica.

## 4. Mapa de reutilización

Verifica este mapa contra los archivos presentes y documenta cambios necesarios.

| Componente actual                              | Reutilización esperada                                                              | Adaptación                                                                                                                  |
| ---------------------------------------------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `robot/brain/models.py`                        | Nomic, CLIP visual, ASR, carga diferida, caché durable y control de threads         | Añadir torre textual CLIP, configuración de idioma y procedencia de modelos; separar responsabilidades mediante adaptadores |
| `robot/brain/memory.py`                        | API Qdrant Edge, vectores nombrados, filtros, bloqueo, apertura y cierre            | Extraer operaciones genéricas; sustituir la taxonomía como dominio canónico; paginar lecturas                               |
| `robot/brain/detect.py`                        | YOLOE, BoT-SORT, estabilidad, `padded_crop`, `crop_quality` y perfiles CPU/CUDA/MPS | Adaptador opcional para vídeo; retener clase/confianza, tiempo y fuente; distinguir track de entidad                        |
| `robot/brain/core.py`                          | Secuencia de percepción, reconocimiento y corrección mediante ejemplares            | Referencia de coordinación; sus métodos no serán el núcleo episódico                                                        |
| `robot/device/live.py`                         | Separación de captura, detección, voz y presentación; cierre y watchdog             | Referencia para workers; evitar importar cámara y micrófono desde servicios de archivos                                     |
| `robot/device/mic.py`                          | Validación de silencio y recorte de audio                                           | Adaptador de audio, cuando entre ASR                                                                                        |
| `robot/device/runtime.py`                      | Rutas temporales portables y utilidades de presentación                             | Reutilizar cuando encajen; añadir tiempos con zona horaria explícita                                                        |
| `robot/device/server.py`                       | Servidor local, respuestas JSON, streaming y certificados                           | Reutilizar infraestructura desacoplada; las rutas nuevas llaman a servicios episódicos                                      |
| `robot/device/page.html`                       | Tarjetas, miniaturas, paginación y componentes de interacción                       | Incorporar vista de episodios, búsqueda escrita, filtros y evidencia; mantener el modo legado                               |
| `robot/config.py` y `robot/app.py`             | Validación, precedencia de configuración y CLI                                      | Configuración episódica independiente del hardware; nuevo punto de entrada                                                  |
| `testdata/verify_scores.py` y tests existentes | Patrón de calibración y pruebas de regresión                                        | Ampliar con consultas episódicas y negativos; no trasladar resultados de hardware                                           |
| Helper del curso                               | CLIP texto a imagen, consultas por modalidades y liberación de Whisper tras un lote | Extraer comportamiento útil; excluir HTML/Jupyter y funciones destructivas de preparación                                   |

Mantén las licencias y atribuciones. La licencia del repositorio no determina automáticamente la de los archivos docentes, paquetes, modelos o pesos. Registra el origen de los componentes efectivamente incorporados.

## 5. Hallazgos que condicionan la implementación

Verifica y trata estos problemas al incorporar el componente afectado:

1. `Robot.process_frame` escribe una vez por track mediante `sighted`. Esto no actualiza la última aparición durante una presencia continua. Una observación nueva debe conservar `first_seen`, `last_seen` y referencias a las evidencias seleccionadas.
2. Un track local no es una entidad persistente. Una etiqueta repetida tampoco es una identidad. No dedupliques objetos físicos por label.
3. El robot reconoce contra ejemplares `taught`. Acumular puntos `seen` no implica aprendizaje automático del reconocimiento.
4. `Robot.ask` y la recuperación textual antigua no resuelven todas las restricciones temporales ni rechazan siempre candidatos irrelevantes. Implementa la ruta nueva con contratos propios.
5. Lecturas mediante `scroll(limit=10000)` o `cloud_points(limit=1000)` sin cursor omiten registros. No calcules última aparición ni exportaciones sobre una primera página incompleta.
6. Borrar vectores no elimina archivos. Gestiona conjuntamente referencias, visibilidad, índices y medios administrados.
7. La reproducción antigua acumula vídeo en RAM y asigna tiempos artificiales. La nueva ingesta lee incrementalmente y conserva tiempos del medio.
8. El helper sí incorpora `Qdrant/clip-ViT-B-32-text` junto a la torre visual. El robot no la incorpora. Añádela al adaptador visual de la aplicación episódica.
9. `memory_inbox` atenúa resultados bajo umbral, pero no los rechaza. La aceptación se decide en el servicio, no en CSS.
10. Lesson5 llama seis funciones ausentes del helper recibido y de sus propias celdas: `show_example_object`, `store_day`, `memory_receipt`, `save_shard`, `freshness_ranking` y `recent_text_search`. No importes estas funciones suponiendo que existen. No atribuyas una fórmula de recencia a ese código.
11. `new_shard` llama a `fresh_start`, borra el destino y, si tiene contenido, cierra shards ajenos expuestos en `__main__`. Excluye esas utilidades del producto.
12. Nomic y CLIP tienen espacios y distribuciones distintos. No sumes sus scores brutos ni los interpretes como probabilidades.
13. El ASR del robot fija inglés. El idioma debe ser configurable y español debe formar parte de la evaluación.
14. La etiqueta de ubicación del dispositivo no demuestra una posición del objeto. Conserva su origen declarado.

Corrige en esta entrega los problemas presentes en la ruta episódica implementada. No amplíes la fase inicial para reparar todos los comportamientos del robot legado. Documenta los pendientes de ese modo y sus límites.

## 6. Arquitectura objetivo

Usa Python y el flujo `uv` del repositorio. Mantén una versión de Python compatible con el código existente y valida la resolución real de dependencias. Reutiliza las versiones fijadas cuando funcionen; justifica cada cambio y actualiza el lock. No inventes compatibilidad ni versiones instalables.

El núcleo debe importar sin cargar modelos ni requerir OpenCV, Torch, Ultralytics, micrófono o GPU. Separa dependencias de percepción y dispositivo mediante extras o grupos compatibles con el proyecto. Si mueves dependencias antiguas, actualiza la instalación documentada del modo robot y verifica su resolución.

Organización propuesta, ajustable a la inspección con equivalencias documentadas:

```text
episodic/
  domain/          entidades, tiempos, relaciones y tipos de resultado
  application/     ingesta, indexación, recuperación y ciclo de vida
  ports/           contratos de almacenamiento, encoders y medios
  adapters/
    sqlite/        repositorio, migraciones y outbox
    qdrant/        índice vectorial derivado
    models/        puentes a encoders y ASR existentes
    media/         archivos, imágenes y posteriormente vídeo/cámara
  interfaces/
    cli/           comandos
    http/          rutas y serialización
    web/           interfaz local
  bootstrap.py     composición e inyección de dependencias
  app.py           entrada de la aplicación
```

Los puertos pueden ser `typing.Protocol` y los tipos de dominio dataclasses. Evita contenedores de inyección, repositorios genéricos universales y jerarquías de clases sin necesidad.

El dominio no importa Qdrant, FastEmbed, OpenCV ni HTTP. Los servicios reciben puertos. Los adaptadores conocen sus librerías. `bootstrap` selecciona implementaciones y configuración. Los controladores validan y delegan, sin contener ranking ni reglas temporales.

Puertos iniciales: `EpisodicRepository`, `EvidenceStore`, `VectorIndex`, `EpisodeTextEncoder`, `ImageEncoder`, `AlignedTextEncoder` y `Clock`. En fases posteriores incorpora `MediaReader`, `VisualPerception`, `SpeechTranscriber` y enriquecedores concretos. No crees implementaciones vacías para aparentar fases terminadas.

La API inicial puede conservar `ThreadingHTTPServer` y utilidades JSON del repositorio. Extrae dependencias de `LiveApp` y usa rutas exactas. No incorpores un framework web por inercia. Si un requisito concreto justifica otro, documenta el beneficio antes de añadirlo. Mantén la UI local sencilla y reutiliza componentes existentes cuando tengan sentido; no conviertas la fase inicial en una reescritura frontend.

## 7. Modelo de dominio y tiempos

Implementa en fase 1 las entidades necesarias para episodios preparados. Separa al menos:

| Entidad          | Responsabilidad                                                      |
| ---------------- | -------------------------------------------------------------------- |
| `Source`         | Fuente, tipo, ámbito y metadatos del reloj                           |
| `CaptureSession` | Contexto de ingesta y agrupación de capturas                         |
| `Evidence`       | Medio verificable, hash, fuente, tiempos, URI interna y estado       |
| `Observation`    | Dato observado, declarado o derivado con procedencia                 |
| `Episode`        | Unidad temporal/contextual con observaciones y evidencias vinculadas |
| `Annotation`     | Enriquecimiento versionado; autor/modelo y evidencias de respaldo    |
| `Representation` | Embedding, espacio, modelo, revisión y registro representado         |
| `QueryRun`       | Consulta, filtros, candidatos, decisión y referencias devueltas      |

Implementa en fase 1 una identidad `Entity` mínima y asociaciones declaradas entre entidad y observación para las consultas temporales. Conserva autor y procedencia de esas asociaciones. Prepara la extensión a ejemplares, `EntityLink` candidato y `EpisodeRelation`, sin implementar reconocimiento automático entre cámaras en esta fase.

Usa IDs globales de dominio, por ejemplo UUID como cadenas. Comprueba los tipos de ID admitidos por la versión de Qdrant Edge y crea un mapping persistente cuando difieran; no presupongas soporte de UUID ni uses hashes truncados sin resolver colisiones.

Representa por separado tiempo de captura, tiempo relativo del medio y tiempo de ingesta. Conserva inicio/fin conocidos, reloj/fuente, desfase e incertidumbre. Usa UTC para instantes absolutos y unidades explícitas para tiempos relativos. No compares tiempos relativos de vídeos distintos sin una alineación conocida.

Si una fotografía carece de fecha confiable, conserva tiempo desconocido. El orden de carga y `mtime` no establecen cronología. Si un episodio agrupa varias fuentes, registra su vínculo declarado o inferido y la versión que lo sustenta.

Una afirmación humana, una detección visual y una descripción generada tienen orígenes diferentes. Guarda `provenance_kind`, autor o herramienta, versión y evidencias. No conviertas una hipótesis en observación directa ni uses un score de similitud como probabilidad de identidad.

Un enriquecimiento añade una revisión. No reemplaza el original ni cambia retrospectivamente su procedencia. La narrativa final será un producto de consulta, no la única memoria almacenada.

## 8. Persistencia y consistencia

SQLite es la fuente de verdad inicial. Usa migraciones versionadas, claves foráneas, índices de fuente/tiempo y transacciones. Los originales y derivados se guardan en un almacén administrado con hashes y referencias. Qdrant Edge mantiene una proyección reconstruible de representaciones.

Separa los directorios de SQLite, medios, índices y caché de modelos. No intentes cargar como shard un directorio que contiene archivos ajenos. Abrir una memoria existente nunca debe ejecutar un reset.

Diseña la ingesta idempotente con una clave explícita de fuente, sesión y captura externa o una clave proporcionada. Dos evidencias con bytes iguales en fuentes diferentes siguen siendo registros diferentes; se admite compartir el blob físico. No dedupliques sucesos por hash de imagen.

Guarda de forma atómica el medio en el almacén, después persiste sus referencias y trabajos de outbox en una transacción. Define recuperación y limpieza de blobs huérfanos si un paso falla. SQLite y el índice no comparten una transacción: usa outbox durable, trabajos idempotentes, reintentos acotados y estados visibles de indexación.

No mantengas locks durante inferencia. Serializa acceso al shard donde lo exija su API y gestiona conexiones SQLite según los threads empleados. La fase inicial admite un worker local y cola durable, sin broker externo.

Una consulta revalida cada candidato contra el registro canónico, sus revisiones y su estado. Un punto vectorial pendiente de borrado no vuelve a aparecer como evidencia vigente. La reconstrucción produce un índice nuevo verificable y preserva el anterior ante un fallo.

La eliminación cambia primero la visibilidad canónica, genera operaciones de índice y limpia blobs cuando no tengan referencias vigentes. No borres el archivo externo que el usuario importó. Expón fallos pendientes y evita afirmar eliminación física completa antes de terminarla.

## 9. Encoders y recuperación multimodal

Baseline inicial:

1. `nomic-ai/nomic-embed-text-v1.5`, 768 dimensiones, para texto. Reutiliza las llamadas de documentos y consultas del repositorio, respetando `.embed` y `.query_embed`.
2. `Qdrant/clip-ViT-B-32-vision` y `Qdrant/clip-ViT-B-32-text`, 512 dimensiones, como pareja alineada para imagen a imagen y texto a imagen.

Registra identificador, revisión resuelta o checksum del artefacto cuando esté disponible, versión de runtime, dimensión, normalización y versión del preprocesado. No declares una revisión inmutable usando únicamente el nombre del modelo.

Conserva la ruta BGR a RGB cuando adaptes `embed_crop`, y utiliza un preprocesado coherente para imágenes completas y regiones. Las representaciones globales conservan contexto; los crops complementan esa evidencia. No destruyas el original aplicando máscaras o recortes.

No mezcles representaciones de encoders diferentes aunque tengan la misma dimensión. Cambiar un modelo crea otro espacio/versionado y requiere reindexación evaluada.

La recuperación debe:

1. Recibir intención explícita, query textual o imagen, fuentes, intervalo, entidad opcional, ámbito y límites.
2. Aplicar filtros antes del top-k cuando el backend lo permita. Si parte de los filtros se resuelve en SQLite, usa IDs elegibles o sobreconsulta iterativa con límites y diagnóstico; no filtres una página pequeña y declares que no existen coincidencias.
3. Recuperar candidatos textuales y visuales en sus espacios correspondientes. Voz transcrita es texto con procedencia de audio, no una tercera métrica vectorial.
4. Combinar rankings mediante RRF como baseline y conservar scores/ranks originales. Agrupar por episodio y evitar premiar un episodio únicamente por tener más crops duplicados. Parametriza pesos y límites por modalidad.
5. Expandir las referencias del episodio para aportar contexto. Distinguir evidencia encontrada directamente de evidencia añadida por pertenecer al mismo episodio.
6. Separar relevancia, aceptación y correspondencia. Dos primeros resultados de modalidades distintas no constituyen evidencia conjunta por aparecer en la misma pantalla.
7. Devolver `candidate`, `supported`, `ambiguous`, `insufficient_evidence` o `no_match`, con motivos y referencias. `supported` se limita a la consulta realmente resuelta; un episodio relevante no respalda cualquier pregunta sobre él.

Los umbrales son configurables por tarea y encoder. No adoptes 0,6, 0,23, 0,80 o 0,90 como valores universales. Implementa política de aceptación y evaluación con positivos y negativos. Hasta validar el punto de operación, presenta resultados semánticos como candidatos y deja constancia de la configuración experimental.

No necesitas un LLM para interpretar todas las preguntas inicialmente. La API y la UI deben permitir seleccionar la intención y suministrar filtros explícitos. Cualquier parser textual inicial será limitado y probado, especialmente en español. No inventes un intérprete general basado en expresiones regulares.

## 10. Recuperación temporal

Implementa rutas distintas:

| Intención          | Regla                                                                |
| ------------------ | -------------------------------------------------------------------- |
| `semantic_search`  | Relevancia; intervalo opcional; sin recencia impuesta                |
| `interval_search`  | Selección por solapamiento temporal válido y fuente                  |
| `last_observation` | Entidad resuelta, observaciones válidas y orden temporal descendente |
| `timeline`         | Orden y relaciones con tiempos conocidos e incertidumbre visible     |

Define intervalos y semántica de solapamiento de forma consistente, por ejemplo `[inicio, fin)`. Trata fotografías como capturas puntuales con una regla explícita. Cuando exista incertidumbre que impida ordenar dos acontecimientos, devuelve ambigüedad.

Para última aparición, no uses un pequeño bonus de recencia ni ordenes una página arbitraria de vecinos. Si la identidad no está resuelta o existe una observación de tiempo desconocido que impide asegurar la última, devuelve el alcance de la conclusión, por ejemplo `latest_known`, y el motivo de insuficiencia.

Pospón el ranking suave de recencia hasta una evaluación por intención. No reconstruyas la función ausente de Lesson5 atribuyéndola al curso. Si la implementas después, documenta fórmula, reloj de referencia, datos sin timestamp y candidatos sobre los que actúa.

## 11. Vídeo, percepción y enriquecimiento: siguientes fases

En fase 2, añade lectura incremental de vídeo con timestamps del decoder, fotogramas/contexto y clips referenciables. El modo webcam conserva tiempo monotónico de sesión y tiempo absoluto cuando esté disponible. No atribuyas PTS exactos a un decoder que no los expone; documenta el método y sus límites.

Reutiliza detector, tracker, estabilidad y calidad de recorte. Retén coordenadas, máscara o referencia, clase cuando exista, confianza, fuente, sesión y track local. El filtro de área máximo del robot fue pensado para objetos de mano; debe configurarse por caso y no excluir personas o elementos grandes por defecto.

La selección conserva el tiempo del mejor crop, no el tiempo de un frame posterior usado para mostrarlo. Actualiza intervalos de presencia de forma acotada y evita una escritura vectorial por frame. El frame completo y el crop deben quedar ligados a su evidencia real.

Empieza con ventanas/episodios preparados y después compara segmentación automática contra anotaciones. Evalúa pérdida de acontecimientos por muestreo. No presentes ventanas fijas como detección de acciones.

En fases siguientes incorpora ASR alineado, OCR y descripciones estructuradas de escenas como anotaciones versionadas. Estos procesadores son opcionales, configurables y evaluables. Los sonidos ambientales requieren otro componente, no están cubiertos por Whisper.

Los cruces entre fuentes proponen relaciones y asociaciones de entidad con evidencia y estado de validación. Similitud visual no prueba identidad y simultaneidad no prueba causalidad. Conserva reloj, desfase y tolerancia de cada fuente.

## 12. Interfaz y API de la primera entrega

Añade un punto de entrada `python -m episodic.app` con subcomandos equivalentes a:

```text
init --data DIR
import --data DIR --manifest FILE
index --data DIR
search --data DIR --text TEXT --intent semantic_search
search --data DIR --image FILE --intent semantic_search
timeline --data DIR --source SOURCE_ID
last-observation --data DIR --entity ENTITY_ID
rebuild-index --data DIR
serve --data DIR --host 127.0.0.1
```

Mantén filtros de fuente/intervalo, límites y salida JSON disponibles. Los comandos y sus opciones deben coincidir con la documentación y estar probados. `init` es idempotente y no borra una memoria existente. No añadas reset automático a ningún flujo.

La UI debe permitir buscar por texto o imagen, elegir intención, filtrar fuente/tiempo y navegar episodios. Cada resultado muestra intervalo o tiempo desconocido, procedencia, evidencias, anotaciones, estado de aceptación y detalles de correspondencia. Ofrece búsqueda escrita desde el primer día; no exijas voz o cámara.

API propuesta, ajustable conservando los casos de uso:

```text
GET    /api/health
GET    /api/sources
POST   /api/import
POST   /api/search
GET    /api/episodes
GET    /api/episodes/{id}
GET    /api/episodes/{id}/annotations
POST   /api/episodes/{id}/annotations
GET    /api/timeline
GET    /api/entities/{id}/last-observation
GET    /api/evidence/{id}
DELETE /api/episodes/{id}
GET    /api/jobs/{id}
```

CLI y HTTP llaman a los mismos servicios. Sirve evidencias por ID y comprueba su estado canónico; no aceptes rutas arbitrarias del sistema. La importación HTTP lee desde una raíz de entrada configurada o archivos recibidos, sin permitir escapes de ese ámbito. La búsqueda visual HTTP recibe una imagen o un ID de evidencia autorizado, no una ruta elegida por el cliente. Limita tamaño de requests, valida intervalos, MIME y parámetros, y pagina colecciones. Usa métodos de escritura para mutaciones.

El servicio se inicia en loopback. La exposición a red debe tener una configuración explícita y control de acceso antes de habilitarla. Reutiliza el certificado del repositorio únicamente si incorporas esa modalidad y verificas vigencia y nombres. No añadas infraestructura de autenticación empresarial a la fase local.

## 13. Corpus y manifiesto de importación

Define un manifiesto JSON versionado y su validación. Debe representar fuentes, sesiones, IDs externos, episodios, intervalos conocidos/desconocidos, evidencias con rutas relativas, observaciones y anotaciones. Resuelve las rutas relativas respecto al manifiesto, conserva Unicode y rechaza referencias inválidas. Conserva un ejemplo legible y un error por campo cuando falle.

Crea diez episodios simulados de una secuencia común registrada por tres cámaras o fuentes, con textos ricos y consultas de referencia. Incluye:

1. Una entidad con varias apariciones en orden conocido.
2. Un objeto visualmente similar perteneciente a otra entidad.
3. Dos acontecimientos simultáneos y un caso con orden incierto.
4. Una captura de tiempo desconocido.
5. Un detalle contextual presente en una imagen, sin etiqueta inicial.
6. Una declaración humana diferente de una observación visual.
7. Un episodio enriquecido en una segunda revisión.
8. Una consulta sin evidencia y otra con correspondencia ambigua.

Los medios de prueba deben existir y corresponder a sus referencias. Marca las descripciones simuladas como anotaciones de escenario. Si generas imágenes de fixtures, identifica su carácter sintético y úsalo para comprobar el pipeline; no afirmes precisión semántica del encoder a partir de gráficos simples. No declares visibles detalles que el medio de prueba no contiene.

Separa corpus de desarrollo, calibración y prueba por sesión o escenario. Diez episodios sirven para comprobar comportamiento y demostración; no constituyen validación general del sistema.

## 14. Alcance que debes ejecutar ahora

Implementa y termina la fase 1 en este encargo. Deja las otras fases en el roadmap, con dependencias y criterios de salida; no generes scaffolding masivo para ellas.

Fase 1: episodios preparados en PC, importación idempotente, SQLite y medios, outbox e indexación, Nomic y pareja CLIP, consultas textual/visual con filtros, recuperación temporal sobre vínculos declarados, anotaciones versionadas, CLI, API local, UI y evaluación de comportamiento.

Entrega conexiones reales a los encoders y Qdrant Edge. Los fakes están reservados a pruebas deterministas. Si un modelo o wheel no está disponible en el entorno, implementa el adaptador, ejecuta las comprobaciones posibles y reporta la limitación concreta. No sustituyas silenciosamente el backend ni declares terminada la validación real.

Fases posteriores:

| Fase | Incremento                                         | Criterio de salida                                                         |
| ---- | -------------------------------------------------- | -------------------------------------------------------------------------- |
| 2    | Vídeo/cámara y detector/tracker reutilizados       | Episodios con tiempos del medio y evidencias verificables; consumo acotado |
| 3    | ASR, OCR y descripción estructurada                | Enriquecimientos versionados y calidad evaluada                            |
| 4    | Cruce de fuentes e identidades candidatas          | Relación, reloj y ambigüedad explícitos; falsas asociaciones medidas       |
| 5    | Herramientas para agentes de explotación           | Respuestas y comparaciones reconstruibles desde evidencia                  |
| 6    | Perfiles de dispositivos y sincronización opcional | Mismo contrato con calidad, recursos y recuperación medidos                |

Una vez la fase 1 funcione, documenta dónde se conectará `Robot.process_frame` o el adaptador de `Detector.process` como productor del dominio. No realices doble escritura directa a SQLite y al shard sin pasar por el servicio de ingesta y su outbox.

## 15. Pruebas y criterios de aceptación

Primero ejecuta los tests existentes y registra el baseline. La referencia auditada tenía cinco pruebas auxiliares aprobadas y dos de LiveApp bloqueadas por ausencia de OpenCV; comprueba el estado de tu entorno, no heredes ese resultado.

Añade pruebas de comportamiento en directorios temporales y una prueba de integración del backend real cuando esté disponible. Prioriza:

1. Importar el manifiesto dos veces no duplica episodios ni evidencias.
2. Abrir y cerrar conserva datos y no afecta a otra instancia ni borra archivos.
3. Un fallo entre persistencia e indexación deja un trabajo recuperable; el reintento no duplica representaciones.
4. Cambiar encoder/revisión no mezcla espacios incompatibles.
5. Buscar por texto o imagen devuelve episodios con referencias existentes; filtros de fuente y tiempo se respetan aunque los candidatos globales estén dominados por otra fuente.
6. Los vacíos y resultados débiles no provocan `[0]`, ni se transforman en hechos respaldados.
7. La última observación devuelve la más reciente del conjunto válido completo; no depende del tamaño de una primera página. Tiempo desconocido y reloj incierto conservan sus límites.
8. Un texto y una imagen de episodios diferentes no aparecen como prueba conjunta del mismo hecho.
9. Dos entidades con la misma etiqueta siguen separadas; una similitud no crea un enlace confirmado.
10. Una anotación nueva mantiene accesible la revisión anterior y su procedencia.
11. Un episodio eliminado desaparece de búsqueda y la API de medios, incluso si su borrado vectorial está pendiente. Un blob compartido permanece mientras haya referencias vigentes.
12. La reconstrucción recupera el índice desde registros/evidencias y conserva el estado anterior si falla.
13. Dominio, CLI de ayuda y pruebas básicas funcionan sin cámara ni extras del robot.
14. Los comandos y el recorrido UI documentados son ejecutables.

Usa doubles para forzar ranking, errores y tiempos; distingue esas pruebas de una ejecución real de modelos. No midas calidad de recuperación con embeddings ficticios. Separa pruebas rápidas, integración local y descarga de modelos.

Para evaluación real registra Recall@k por intención y modalidad, falsos enlaces, abstenciones, última aparición correcta y latencia por etapa. Documenta hardware, versiones y tamaño del corpus. No reutilices curvas de latencia guardadas en el helper como mediciones actuales.

No declares la fase aceptada únicamente porque pasan tests unitarios. Demuestra el recorrido importar, indexar, buscar, revisar evidencia, enriquecer, reiniciar y volver a consultar, indicando qué pasos usaron modelos reales.

## 16. Documentación y forma de trabajar

Antes de editar, realiza una inspección breve y escribe `docs/episodic/architecture.md` con el diseño concreto y `docs/episodic/reuse-map.md` con componentes reutilizados, modificados y aplazados. Después continúa la implementación sin detenerte a pedir aprobación de decisiones rutinarias dentro de este alcance.

Conserva el estilo del repositorio y evita cambios ajenos al objetivo. Extrae código compartido en pasos pequeños y comprobables. Mantén los tests del modo antiguo; no los alteres para ocultar regresiones.

Incluye migraciones, configuración de ejemplo, comandos de instalación PC y robot, caché persistente de modelos, preparación para ejecución sin red una vez descargados, y tratamiento de trabajos fallidos. No incluyas modelos, caches ni datos personales en Git.

El estado final debe incluir:

1. Código funcional de la fase 1 y cambios revisables en el repositorio.
2. Mapa de reutilización con archivos/símbolos realmente utilizados y motivos para sustituirlos.
3. Arquitectura, modelo de datos, manifiesto y decisiones de compatibilidad.
4. Corpus simulado y consultas esperadas, con límites de evaluación.
5. Guía de ejecución y demostración reproducible de principio a fin.
6. Resultado de pruebas existentes, nuevas e integración real, diferenciando bloqueos.
7. Roadmap de fases 2–6 y puntos exactos de conexión con el robot.

Trabaja en una rama o checkout que preserve el trabajo existente. No publiques, despliegues ni envíes cambios remotos por este encargo. Si faltan credenciales o permisos, no inventes resultados; continúa con el trabajo local posible.

Empieza ahora por inspeccionar el repositorio y construir la fase 1. Cuando termines, informa qué funciona, qué componentes has reutilizado, cómo ejecutarlo y qué validación queda pendiente.
