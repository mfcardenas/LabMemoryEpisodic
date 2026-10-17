# Qdrant para EpiGraph

EpiGraph guarda la proyección vectorial en un servidor Qdrant
(`EPIGRAPH_VECTOR_BACKEND=http`, colección `EPIGRAPH_QDRANT_COLLECTION`,
`episodic_memory` por defecto). SQLite sigue siendo la verdad: la colección
se puede borrar y reconstruir con `epigraph rebuild-index`.

## Arranque

```powershell
docker compose up -d qdrant
```

- Datos en el volumen con nombre `labmemoryepisodic_qdrant-storage`
  (instantáneas en `labmemoryepisodic_qdrant-snapshots`). Borrar o recrear el
  contenedor no los pierde; `docker compose down -v` sí.
- `restart: unless-stopped`: vuelve solo tras reiniciar la máquina.
- Dashboard: <http://127.0.0.1:6333/dashboard>.
- Puertos, imagen y nombre del contenedor se cambian en `.env`
  (`QDRANT_HOST_PORT`, `QDRANT_IMAGE`, …). Si cambia el puerto, cambie
  también `EPIGRAPH_QDRANT_PORT`.

Si EpiGraph no llega a Qdrant, no arranca: indica el host, el puerto y la
colección que intentaba usar.

## Traer los datos de un contenedor Qdrant anterior

Para pasar a este servicio las colecciones de un contenedor existente (por
ejemplo uno lanzado con `docker run` sin volumen), con la misma versión de
Qdrant en ambos:

```powershell
docker stop <contenedor_anterior>                  # libera el puerto y deja los datos quietos
docker cp <contenedor_anterior>:/qdrant/storage/collections "$env:TEMP\qdrant-collections"
docker compose create qdrant                       # crea contenedor y volúmenes, sin arrancar
docker compose stop qdrant                         # parado mientras se copia (y EpiGraph parado)
foreach ($c in Get-ChildItem "$env:TEMP\qdrant-collections" -Directory) {
  docker cp $c.FullName epigraph-qdrant:/qdrant/storage/collections/
}
docker compose up -d --wait qdrant
curl.exe http://127.0.0.1:6333/collections         # las nuevas más las que ya hubiera
```

- Se copian solo las carpetas de las colecciones, no `storage` entera: así no
  se pisan `aliases/` ni `raft_state.json` del contenedor nuevo (por ejemplo,
  el alias `episodic_memory` de EpiGraph si ya ha cargado algo). Los alias que
  tuviera el contenedor anterior hay que volver a crearlos.
- Se pasa por una carpeta temporal: en Windows PowerShell una tubería
  (`docker cp … - | docker cp - …`) trata el tar como texto y lo corrompe.

El contenedor anterior queda parado, no borrado: es la copia de seguridad
hasta comprobar que todo está. Después, `docker rm <contenedor_anterior>`.

## Cambiar de versión

La imagen va fijada (`qdrant/qdrant:v1.16.2`). Qdrant solo garantiza abrir
los datos de la versión menor anterior, así que se sube de una en una
(1.16 → 1.17 → …), arrancando con cada versión hasta que esté en verde.
Antes, una instantánea por colección desde el dashboard o
`POST /collections/<nombre>/snapshots`. El cliente de Python avisa si su
versión se aleja más de una menor de la del servidor.
