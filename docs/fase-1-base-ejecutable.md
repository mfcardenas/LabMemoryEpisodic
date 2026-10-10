# EpiGraph — Fase 1: base ejecutable y estructura del proyecto

Fecha de cierre técnico: 2026-10-04  
Entorno verificado: `LabMemoryEpic01`, Python 3.13.16  
Estado: **IMPLEMENTADA Y VERIFICADA; pendiente de aceptación del usuario**

## 1. Resumen de lo implementado

La aplicación episódica existente quedó instalable y ejecutable desde PC como
un único monolito modular. El comando de producto es `epigraph`; compone los
mismos servicios ya usados por CLI, API y frontend.

Se incorporaron únicamente elementos de infraestructura de Fase 1:

- configuración central `EpisodicConfig` con variables `EPIGRAPH_*`;
- resolución estable de rutas relativas contra la raíz del proyecto;
- definición instalable y versiones directas fijadas en `pyproject.toml` y
  `requirements.txt`;
- entrada de consola `epigraph`;
- logging común a consola y, opcionalmente, a fichero;
- inclusión del frontend existente como recurso del paquete;
- ejemplo mínimo fuera de notebooks;
- pruebas de configuración, logging y arranque API/frontend.

No se añadieron base vectorial, grafo, agentes, segmentación episódica,
modelos, interfaz ni procesamiento en tiempo real. Los backends, encoders y
funciones previas conservan su comportamiento.

## 2. Archivos creados o modificados

### Creados

- `pyproject.toml`
- `.env.example`
- `episodic/logging_config.py`
- `episodic/interfaces/web/__init__.py`
- `examples/minimal_boot.py`
- `tests/smoke/__init__.py`
- `tests/smoke/test_startup.py`
- `tests/unit/test_runtime_config.py`
- `docs/fase-1-base-ejecutable.md`

### Modificados

- `episodic/config.py`
- `episodic/bootstrap.py`
- `episodic/interfaces/cli/commands.py`
- `episodic/interfaces/http/server.py`
- `requirements.txt`
- `README.md`

No se modificaron notebooks, datos canónicos, migraciones, modelos de dominio,
algoritmos de recuperación ni interfaces visuales.

## 3. Componentes existentes reutilizados

| Componente | Reutilización en Fase 1 |
|---|---|
| `episodic/bootstrap.py` | Sigue siendo la composición única de dependencias |
| `episodic/interfaces/cli` | Se conserva la CLI y se publica como `epigraph` |
| `episodic/interfaces/http` | Se reutiliza la misma factoría FastAPI |
| `episodic/interfaces/web` | Se sirve sin rediseños como frontend de verificación |
| SQLite y media store | Se inicializan mediante el bootstrap existente |
| Qdrant Edge / adaptador en memoria | Edge en ejecución; memoria solo en smoke/example |
| Encoders fake | Permiten arranque offline controlado, sin crear modelos nuevos |
| Suite de 42 pruebas | Baseline de regresión conservado íntegramente |
| Notebooks y `helper.py` | Conservados como ejemplos; no son núcleo productivo |

El RAG documental raíz y el robot de referencia permanecen aislados. No se
presentan como componentes integrados de EpiGraph.

## 4. Decisiones técnicas tomadas

1. Se mantiene `episodic/` como paquete principal; no se crea una aplicación
   paralela ni se reorganiza el dominio.
2. Las rutas configurables relativas se anclan a `PROJECT_ROOT`, evitando que
   cambien al ejecutar desde otro directorio.
3. CLI, API, frontend, scripts y tests comparten `EpisodicConfig` y el mismo
   bootstrap.
4. Se usa el prefijo único `EPIGRAPH_` para evitar colisiones con la
   configuración del RAG y del robot.
5. `logging.basicConfig` sale de la CLI y queda encapsulado en un módulo común.
6. El fichero de log es opcional; si se activa, su ruta pasa por el mismo
   resolvedor central.
7. Las dependencias directas quedan fijadas a las versiones comprobadas en el
   entorno. No se conserva `uv.lock`, por decisión expresa del usuario; la
   gestión y activación de Conda queda fuera del proyecto.
8. Los notebooks no se editaron: la instalación editable permite importar el
   paquete de producción directamente desde ellos.

## 5. Comandos exactos para ejecutar

El usuario activa y gestiona el entorno Conda. Desde la raíz del proyecto:

```powershell
python -m pip install -e ".[dev]"
```

Arranque normal con el backend Edge y los datos configurados:

```powershell
epigraph serve
```

Arranque de comprobación sin cargar modelos reales ni requerir servicios
externos:

```powershell
epigraph --data .runtime/fase1 --fake-encoders serve
```

Frontend: `http://127.0.0.1:8100/`  
Salud de API: `http://127.0.0.1:8100/api/health`

Pruebas:

```powershell
python -m pytest -q
```

Ejemplo mínimo reproducible:

```powershell
python examples/minimal_boot.py
```

La alternativa compatible sin instalar la entrada de consola sigue presente:

```powershell
python -m episodic.app --help
```

## 6. Pruebas ejecutadas y resultados

| Comprobación | Resultado |
|---|---|
| Baseline anterior a cambios | `42 passed`, 2 warnings |
| Suite final | `46 passed`, 2 warnings, 4.31 s |
| Compilación de `episodic`, `examples`, `tests` | Correcta |
| `pip check` | `No broken requirements found` |
| Instalación `pip install -e ".[dev]"` | Correcta; paquete `epigraph-memory 0.1.0` |
| `epigraph --help` desde fuera de la raíz | Correcto |
| `python examples/minimal_boot.py` desde fuera de la raíz | Correcto; SQLite vacío y cierre limpio |
| Servidor real con Edge + fake encoders | Arranque correcto |
| `GET /api/health` | `200`, estado `ok`, backend `edge` |
| `GET /` | `200`, HTML del frontend existente |
| `GET /static/app.js` | `200` |

Los dos warnings son conocidos: una deprecación de `TestClient` y el aviso de
que los índices de payload no tienen efecto en Qdrant local usado por una
prueba. Ninguno produjo fallos funcionales.

El primer intento de smoke HTTP usó `C:\tmp` y fue rechazado por los permisos
del sandbox. Se repitió dentro del workspace y pasó; el runtime temporal fue
eliminado y el puerto quedó cerrado.

## 7. Limitaciones pendientes

- Sin `uv.lock`, las dependencias transitivas no están congeladas; las
  dependencias directas sí están fijadas en los dos manifiestos.
- La configuración lee variables de entorno, pero no carga automáticamente un
  fichero `.env`; `.env.example` documenta nombres y valores.
- El RAG legado y el robot mantienen sus propias entradas y configuraciones.
- Los notebooks necesitan que el usuario instale Jupyter si desea ejecutarlos;
  no es una dependencia del runtime EpiGraph.
- El arranque con encoders reales puede requerir modelos ya presentes en caché
  o acceso para descargarlos. El arranque con `--fake-encoders` es offline.
- No se ha cambiado ninguna limitación funcional inventariada en Fase 0.

## 8. Cambios propuestos para la siguiente fase — no implementados

No se propone aquí una implementación concreta porque la siguiente fase debe
ser autorizada y definida por el usuario. De acuerdo con la auditoría, el
próximo incremento debería abordar el primer bloque aditivo de procedencia
canónica (`Environment`, `Asset`, `EvidenceReference`) y su migración, con un
criterio verificable desde API/frontend cuando exista información real que
mostrar.

Esta sección no autoriza el comienzo de esa fase.

## 9. Criterios de aceptación

| Criterio | Evidencia | Estado |
|---|---|---|
| Instalación con un comando | `python -m pip install -e ".[dev]"` ejecutado | Cumplido |
| Arranque con un comando | `epigraph serve` / smoke real equivalente | Cumplido |
| Pruebas con un comando | `python -m pytest -q` → 46 aprobadas | Cumplido |
| Notebooks importan módulos | Paquete editable importable fuera de la raíz | Cumplido |
| No romper flujo existente | Baseline 42 → suite final 46 | Cumplido |
| Ejemplo mínimo reproducible | `examples/minimal_boot.py` ejecutado | Cumplido |
