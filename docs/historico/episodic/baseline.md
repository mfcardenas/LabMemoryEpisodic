# Baseline de desarrollo

## Entorno

- Fecha: 2026-10-04.
- Raíz del producto: `C:\Users\sqs.erasmus\Documents\GitHub\LabMemoryEpisodic`.
- Entorno Conda: `LabMemoryEpic01`.
- Intérprete: Python 3.13.16, 64 bits.
- Ejecutable: `C:\Users\sqs.erasmus\.conda\envs\LabMemoryEpic01\python.exe`.
- La raíz del producto no contiene actualmente un marcador `.git`.

## Dependencias verificadas

`python -m pip check` finaliza sin requisitos rotos. Versiones relevantes:

| Paquete | Versión |
|---|---:|
| pydantic | 2.13.5 |
| pydantic-settings | 2.15.0 |
| fastapi | 0.142.2 |
| uvicorn | 0.54.0 |
| qdrant-edge-py | 0.7.2 |
| qdrant-client | 1.19.1 |
| fastembed | 0.8.0 |
| onnxruntime | 1.27.0 |
| numpy | 2.5.1 |
| Pillow | 12.3.0 |
| pytest | 9.1.1 |

## Pruebas iniciales

Desde la raíz del producto:

```powershell
C:\Users\sqs.erasmus\.conda\envs\LabMemoryEpic01\python.exe -m pytest tests -q
```

Resultado inicial: **14 passed**. Tras incorporar temporalidad, reconstrucción, Edge y el contrato frontend/API: **41 passed**, con dos warnings no funcionales (deprecación de `TestClient` y payload indexes sin efecto en Qdrant local/in-memory).

Prueba core ejecutada por separado en el repositorio de referencia:

```powershell
C:\Users\sqs.erasmus\.conda\envs\LabMemoryEpic01\python.exe -m pytest tests\test_runtime.py -q
```

Resultado: **4 passed**.

La colección completa de tests de la referencia se detiene durante colección: `test_live.py` requiere OpenCV y `test_certificate.py` requiere `cryptography`, ambos ausentes en `LabMemoryEpic01`. Este es un bloqueo explícito del perfil robot, no un éxito ni un fallo de la aplicación episódica.

Los imports del RAG legado (`config`, `domain`, `embeddings`, `storage`, `pipeline`, `agent`) completan correctamente. No se ha acreditado todavía su recorrido E2E porque depende de servicios externos Qdrant HTTP y Ollama.

## CLI y servicios locales

- `python -m episodic.app --help` funciona y expone los ocho subcomandos previstos.
- Se completó un recorrido en un directorio temporal y una colección Qdrant exclusiva:
  - `init` creó SQLite y los directorios administrados;
  - la primera importación creó 8 fuentes, 1 sesión, 10 episodios, 10 evidencias, 15 observaciones, 7 entidades, 1 anotación y 20 trabajos de representación;
  - la segunda importación creó cero registros y reportó 10 episodios duplicados omitidos;
  - los recuentos canónicos permanecieron estables después de la segunda importación;
  - `index` procesó 20 representaciones y Qdrant conservó 20 puntos;
  - `search`, `timeline` y `rebuild-index` funcionaron después de reabrir el proceso;
  - la cronología devolvió los 10 episodios Atlas en orden.
- Se detectó Qdrant HTTP en `127.0.0.1:6333`, versión 1.16.2.
- El cliente instalado es 1.19.1 y emite una advertencia de compatibilidad por la diferencia de versiones. Debe alinearse cliente o servidor antes de acreditar la integración real.
- La CLI permite seleccionar una colección Qdrant explícita mediante `--qdrant-collection`, evitando contaminar la colección de desarrollo durante pruebas controladas.
- La consulta con encoders falsos devolvió candidatos deterministas por debajo de los umbrales reales. El resultado global `NO_MATCH` es conservador y no se interpreta como una medida de calidad semántica.
- `rebuild-index` construye una generación staging, verifica el recuento exacto y cambia el alias lógico. Conserva la generación activa si falla un encoder o un upsert y elimina el staging fallido.

## Validación real controlada

- Los modelos cacheados cargan sin red: Nomic produce vectores de 768 dimensiones y CLIP text/vision de 512 dimensiones.
- Una importación Atlas aislada produjo 20 representaciones y 20 puntos tanto en Qdrant Edge como en el perfil HTTP.
- La consulta temporal q3, con semántica `[inicio, fin)`, recuperó exclusivamente `ATLAS-EP-006` y `ATLAS-EP-007`.
- Los chunks de texto e imagen registran línea temporal, estado desconocido, procedencia y episodios vinculados; los filtros temporales se ejecutan antes del top-k en Qdrant.
- Una reconstrucción real cambió de generación con 20 puntos verificados y conservó idénticas las métricas textuales.
- La evaluación textual se ejecuta de forma aislada con Nomic, sin fusión CLIP, mediante `python -m scripts.evaluate_atlas_text`.
- Qdrant Edge 0.7.2 está validado con modelos reales, filtro temporal, persistencia, reapertura y rebuild de generación.
- Las métricas Edge están en [evaluation-text-edge.md](evaluation-text-edge.md); HTTP queda documentado en [evaluation-text-http.md](evaluation-text-http.md).

## Política de ejecución

- La suite por defecto queda restringida a `tests/` mediante `pytest.ini`.
- Los tests del repositorio de referencia se ejecutan desde su propia raíz y de forma explícita.
- Las demostraciones controladas usan un directorio de datos temporal y una colección Qdrant exclusiva.
- Los encoders falsos solo acreditan comportamiento determinista; no acreditan calidad semántica.
