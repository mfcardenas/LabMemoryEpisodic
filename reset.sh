#!/usr/bin/env bash
# Borra los datos de EpiGraph para repetir cargas: SQLite, medios, índice
# local y la colección de EpiGraph en Qdrant. No toca la carpeta vigilada
# ni _procesados: las cargas se ponen a mano. EpiGraph debe estar parado.
#   ./reset.sh          pide confirmación
#   ./reset.sh --yes    sin preguntar
set -euo pipefail
cd "$(dirname "$0")"
source scripts/launcher_env.sh
exec "$PYTHON" -m episodic reset-data "$@"
