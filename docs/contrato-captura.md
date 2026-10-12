# Contrato de entrega de capturas · `epigraph.capture/v1`

La herramienta que graba los entornos no forma parte de EpiGraph. Este
documento es lo único que necesita conocer para entregar capturas: dónde
dejarlas y qué declarar. EpiGraph no deduce nada de los nombres de carpetas
ni de ficheros: entorno, instante y dispositivos los declara la herramienta.

## 1. Entrega

Cada captura es **una subcarpeta** dentro de la carpeta vigilada. El nombre de
la subcarpeta es libre (no se interpreta), pero no debe reutilizarse mientras
la anterior siga allí.

```text
D:\LabMemoryEpisodic_data\jobs\              ← carpeta vigilada
  cualquier_nombre_01\
    cam_norte.mp4
    cam_sur.mp4
    micro.wav
    notas.txt
    capture.json                              ← manifiesto, SIEMPRE el último
```

Reglas:

1. **El manifiesto se escribe al final**, cuando todos los ficheros están
   completos. Escríbalo como fichero temporal y renómbrelo a `capture.json`
   (el renombrado es atómico): así nunca se lee a medias.
2. Si los ficheros se copian por red y el manifiesto llega antes, EpiGraph
   espera a los ficheros declarados hasta `missing_files_timeout_seconds`
   (600 s por defecto) y, si no llegan, marca la captura como fallida
   indicando cuáles faltan.
3. Un fichero se considera completo cuando su tamaño y fecha no cambian
   durante `settle_seconds` y se puede abrir.
4. Los ficheros de la carpeta que el manifiesto no declara se ignoran (y se
   indica en el registro).

## 2. Manifiesto

```json
{
  "schema": "epigraph.capture/v1",
  "environment_id": "env_context_01",
  "captured_at": 1791786600,
  "title": "Desmontaje de la pieza 7",
  "devices": [
    {"id": "cam_norte", "kind": "camera",     "files": ["cam_norte.mp4"]},
    {"id": "cam_sur",   "kind": "camera",     "files": ["cam_sur.mp4"], "started_at": 1791786601.4},
    {"id": "micro_1",   "kind": "microphone", "files": ["micro.wav"],    "started_at": 1791786598.0},
    {"id": "operario",  "kind": "document",   "files": ["notas.txt"]}
  ]
}
```

| Campo | Obligatorio | Significado |
|---|---|---|
| `schema` | sí | Siempre `"epigraph.capture/v1"`. |
| `environment_id` | sí | Entorno capturado. Letras, dígitos, `_`, `.`, `-` (máx. 128). |
| `captured_at` | sí | Instante de la captura: **epoch en segundos, UTC** (entero o con decimales). |
| `title` | no | Título legible; por defecto, el nombre de la captura. |
| `devices` | sí | Al menos un dispositivo. |
| `devices[].id` | sí | Identificador del dispositivo dentro del entorno (mismo formato que `environment_id`). |
| `devices[].kind` | sí | `camera`, `microphone`, `photo_set`, `document`, `manual` o `sensor`. |
| `devices[].files` | sí | Ficheros de ese dispositivo, relativos a la subcarpeta (se admiten subcarpetas, no `..` ni rutas absolutas). Cada fichero en un solo dispositivo. |
| `devices[].started_at` | no | Epoch en segundos en que **ese dispositivo** empezó a grabar. Por defecto, `captured_at`. |

Tipos de fichero que acepta cada clase de dispositivo:

| `kind` | Ficheros |
|---|---|
| `camera` | vídeo, imagen |
| `microphone` | audio, vídeo |
| `photo_set` | imagen |
| `document`, `manual` | PDF, texto (TXT, MD, CSV, JSON) |
| `sensor` | cualquiera admitido |

Una clave desconocida, un epoch en milisegundos o fuera de rango, un tipo de
fichero que no corresponde a su dispositivo, o un fichero declarado dos
veces invalidan la captura con un mensaje explícito. Si la herramienta
**reescribe el manifiesto** corregido, EpiGraph lo reintenta sola.

## 3. Qué hace EpiGraph con ello

- **Nombre de la captura**: `<environment_id>_<captured_at entero>`, p. ej.
  `env_context_01_1791786600`. Es el `external_id` del episodio en SQLite y
  en el payload de Qdrant, y su identificador interno se deriva de él.
  Una segunda captura con el mismo nombre (mismo entorno y segundo) se
  rechaza: no se duplica.
- **Fuentes**: cada dispositivo es una fuente `<environment_id>.<id>`
  (`env_context_01.cam_norte`) con su propio carril en la línea temporal.
- **Tiempo**: cada fichero se sitúa en `started_at` de su dispositivo; todo
  lo que se extrae de él (eventos del vídeo, frases transcritas, fragmentos)
  lleva hora absoluta = `started_at` + desplazamiento dentro del fichero.
  Así se alinean cámaras y micrófonos entre sí, y las capturas de un mismo
  entorno se ordenan por su instante real aunque lleguen desordenadas.
- **Trazabilidad**: la captura guarda el manifiesto íntegro y la ruta de
  cada fichero de origen; el original se custodia por SHA-256.
- **Después**: según la configuración, los originales se quedan o se mueven
  a `_procesados`.

## 4. Configuración en EpiGraph

`watch.toml` (ver `watch.example.toml`) con `grouping = "manifest"`, que es
el valor por defecto; `EPIGRAPH_WATCH_CONFIG=watch.toml` en `.env`. El estado
de cada captura, cada fichero leído y el registro de ejecución se ven en la
sección **Vigilancia** del front.
